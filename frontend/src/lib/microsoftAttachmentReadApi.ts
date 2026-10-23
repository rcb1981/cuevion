/** Microsoft-only bounded attachment reads; no mailbox mutations. */
// Keep credentialed transport and Auth0 session notifications consistent with
// the existing client, while leaving its protected shared source unchanged.
async function fetchProtectedApi(input: string, init: RequestInit): Promise<Response> {
  const response = await globalThis.fetch(input, init);
  if (response.status === 401) {
    try {
      if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
        window.dispatchEvent(new Event("cuevion:auth-session-recheck"));
      }
    } catch { /* Session notification must not replace the read result. */ }
  }
  return response;
}

function safeMicrosoftFetchError(payload: unknown): { code: string; message: string } {
  const messages: Record<string, string> = {
    invalid_request: "Invalid Microsoft attachment request.",
    microsoft_unauthorized: "Please sign in again.",
    microsoft_mailbox_not_found: "Microsoft mailbox not found.",
    microsoft_reconnect_required: "Reconnect the Microsoft mailbox to continue.",
    microsoft_graph_rate_limited: "Microsoft mailbox is temporarily rate limited.",
    microsoft_graph_invalid_response: "Microsoft mailbox returned invalid data.",
    microsoft_graph_unavailable: "Microsoft mailbox is temporarily unavailable.",
    microsoft_mailbox_unavailable: "Microsoft mailbox is temporarily unavailable.",
  };
  if (payload && typeof payload === "object" && "error" in payload &&
      payload.error && typeof payload.error === "object" && "code" in payload.error &&
      typeof payload.error.code === "string" && Object.prototype.hasOwnProperty.call(messages, payload.error.code)) {
    return { code: payload.error.code, message: messages[payload.error.code] };
  }
  return { code: "microsoft_attachment_read_failed", message: "Microsoft attachments could not be loaded." };
}

export type MicrosoftAttachmentMetadata = {
  id: string;
  name: string;
  size: number;
  contentType: string;
  isInline: boolean;
  kind: "file" | "item" | "reference" | "other";
  contentId?: string | null; // inert CID hint; never a fetch URL
};

export type MicrosoftAttachmentListResponse = {
  ok: boolean;
  attachments?: MicrosoftAttachmentMetadata[];
  hasMore?: boolean;
  error?: { code?: string; message?: string };
};

export function trustedMicrosoftAttachment(value: unknown): value is MicrosoftAttachmentMetadata {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const a = value as Record<string, unknown>;
  return (
    Object.keys(a).every((key) =>
      ["id", "name", "size", "contentType", "isInline", "kind", "contentId"].includes(key)
    ) &&
    typeof a.id === "string" &&
    /^[^\s?#]+$/.test(a.id) &&
    a.id.length <= 512 &&
    !a.id.includes("..") &&
    typeof a.name === "string" && a.name.length > 0 && a.name.length <= 256 &&
    typeof a.size === "number" && Number.isSafeInteger(a.size) &&
    a.size >= 0 && a.size <= 1024 ** 4 &&
    typeof a.contentType === "string" && a.contentType.length <= 128 &&
    typeof a.isInline === "boolean" &&
    ["file", "item", "reference", "other"].includes(String(a.kind)) &&
    (a.contentId === undefined || a.contentId === null ||
      (typeof a.contentId === "string" &&
        /^[a-zA-Z0-9_.@+%\-]{1,200}$/.test(a.contentId) &&
        a.kind === "file"))
  );
}

export async function fetchMicrosoftAttachments(request: {
  mailboxId: string;
  providerMessageId: string;
  folder: "Inbox" | "Sent" | "Archive" | "Drafts" | "Deleted" | "Junk";
}): Promise<MicrosoftAttachmentListResponse> {
  if (
    !/^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$/.test(request.mailboxId) ||
    !/^[^\s?#]{1,512}$/.test(request.providerMessageId) ||
    request.providerMessageId.includes("..") ||
    !["Inbox", "Sent", "Archive", "Drafts", "Deleted", "Junk"].includes(request.folder)
  ) {
    return { ok: false, error: { code: "invalid_request", message: "Invalid Microsoft request." } };
  }
  try {
    const response = await fetchProtectedApi("/api/inboxes/fetch-microsoft-attachments", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    const payload: unknown = await response.json();
    if (!response.ok) {
      return { ok: false, error: safeMicrosoftFetchError(payload) };
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("invalid_response");
    }
    const data = payload as Record<string, unknown>;
    if (
      data.ok !== true ||
      !Array.isArray(data.attachments) ||
      data.attachments.length > 100 ||
      !data.attachments.every(trustedMicrosoftAttachment) ||
      new Set(data.attachments.map((item: MicrosoftAttachmentMetadata) => item.id)).size
        !== data.attachments.length ||
      typeof data.hasMore !== "boolean" ||
      !Object.keys(data).every((key) => ["ok", "attachments", "hasMore"].includes(key))
    ) {
      throw new Error("invalid_response");
    }
    return {
      ok: true,
      attachments: data.attachments as MicrosoftAttachmentMetadata[],
      hasMore: data.hasMore,
    };
  } catch {
    return {
      ok: false,
      error: { code: "microsoft_graph_invalid_response", message: "Attachments could not be loaded." },
    };
  }
}

export type MicrosoftAttachmentFileResponse = {
  ok: boolean;
  attachmentId?: string;
  fileName?: string;
  size?: number;
  base64?: string;
  sizeVerifiedAsPng?: true;
  error?: { code?: string; message?: string };
};

/** File bytes remain ephemeral and bounded; only the exact mailbox owner can read. */
export async function downloadMicrosoftAttachmentFile(request: {
  mailboxId: string;
  providerMessageId: string;
  attachmentId: string;
  folder: "Inbox" | "Sent" | "Archive" | "Drafts" | "Deleted" | "Junk";
}): Promise<MicrosoftAttachmentFileResponse> {
  const validGraphId = (id: unknown) =>
    typeof id === "string" && id.length <= 512 &&
    /^[^\x00-\x20\x7f-\x9f?#]+$/.test(id) && id !== "." &&
    !id.includes("..") && new TextEncoder().encode(id).byteLength <= 512;
  if (
    !/^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$/.test(request.mailboxId) ||
    !validGraphId(request.providerMessageId) ||
    !validGraphId(request.attachmentId) ||
    (request.folder !== "Inbox" && request.folder !== "Sent" && request.folder !== "Archive" && request.folder !== "Drafts" && request.folder !== "Deleted")
  ) {
    return { ok: false, error: { code: "invalid_request", message: "Invalid Microsoft attachment request." } };
  }
  try {
    const response = await fetchProtectedApi("/api/inboxes/download-microsoft-attachment", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    const payload: unknown = await response.json();
    if (!response.ok) {
      if (response.status === 413) {
        return { ok: false, error: {
          code: "microsoft_attachment_too_large",
          message: "This file exceeds the 2 MB download limit.",
        } };
      }
      return { ok: false, error: safeMicrosoftFetchError(payload) };
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("invalid_attachment");
    }
    const data = payload as Record<string, unknown>;
    if (
      data.ok !== true ||
      !Object.keys(data).every((key) =>
        ["ok", "attachmentId", "fileName", "size", "base64", "sizeVerifiedAsPng"].includes(key)
      ) ||
      (data.sizeVerifiedAsPng !== undefined && data.sizeVerifiedAsPng !== true) ||
      (data.sizeVerifiedAsPng === true && (typeof data.base64 !== "string" || !data.base64.startsWith("iVBORw0KGgo"))) ||
      data.attachmentId !== request.attachmentId ||
      typeof data.fileName !== "string" ||
      !data.fileName || data.fileName.length > 128 ||
      /[\\/\x00-\x1f\x7f]/.test(data.fileName) ||
      data.fileName === "." || data.fileName === ".." ||
      typeof data.size !== "number" || !Number.isSafeInteger(data.size) ||
      data.size < 0 || data.size > 2 * 1024 * 1024 ||
      typeof data.base64 !== "string" ||
      data.base64.length !== 4 * Math.ceil(data.size / 3) ||
      !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(data.base64)
    ) {
      throw new Error("invalid_attachment");
    }
    return {
      ok: true,
      attachmentId: data.attachmentId as string,
      fileName: data.fileName,
      ...(data.sizeVerifiedAsPng === true ? { sizeVerifiedAsPng: true as const } : {}),
      size: data.size,
      base64: data.base64,
    };
  } catch {
    return { ok: false, error: {
      code: "microsoft_attachment_download_failed",
      message: "The attachment could not be downloaded.",
    } };
  }
}
