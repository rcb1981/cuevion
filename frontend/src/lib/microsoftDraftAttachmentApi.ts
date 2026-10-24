import { MICROSOFT_DRAFT_ATTACHMENTS_RELEASED, validateMicrosoftDraftFiles, type MicrosoftNewDraftFile } from "./microsoftDraftAttachmentPolicy";
import { trustedMicrosoftAttachment, type MicrosoftAttachmentMetadata } from "./microsoftAttachmentReadApi";

export type MicrosoftAttachmentOutcome = "confirmed_performed" | "confirmed_not_performed" | "uncertain";
type Context = { mailboxId: string; providerMessageId: string };
type Mutation = Context & { requestId: string; snapshotHash: string } & (
  { action: "add"; files: MicrosoftNewDraftFile[] } | { action: "remove"; attachmentId: string }
);
export type MicrosoftDraftAttachmentReceipt = {
  ok: true; mailboxId: string; providerMessageId: string; requestId: string;
  outcome: MicrosoftAttachmentOutcome; count: number; completed: number; blocked: boolean;
};
export type MicrosoftDraftAttachmentFailure = {
  ok: false; outcome: "uncertain"; blocked: true; error: { code: string; message: string };
};
export type MicrosoftDraftAttachmentSnapshot = {
  ok: true; mailboxId: string; providerMessageId: string; snapshotHash: string; complete: true;
  attachments: (MicrosoftAttachmentMetadata & { removable: boolean })[];
};
const ID = /^[^\x00-\x20\x7f-\x9f?#]{1,512}$/;
const validId = (s: unknown): s is string => typeof s === "string" && ID.test(s) && s !== "." && !s.includes("..") && new TextEncoder().encode(s).byteLength <= 512;
const validContext = (r: Context) => /^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$/.test(r.mailboxId) && validId(r.providerMessageId);
const validOperation = (id: unknown): id is string => typeof id === "string" && /^[A-Za-z0-9_-]{43}$/.test(id);

function failure(code = "microsoft_attachment_outcome_uncertain"): MicrosoftDraftAttachmentFailure {
  const messages: Record<string, string> = {
    microsoft_attachment_disabled: "Microsoft draft attachment changes are not enabled.",
    microsoft_attachment_selection_invalid: "Select at most 10 files, 2 MiB each and 8 MiB in total.",
    microsoft_attachment_draft_blocked: "An earlier attachment operation needs verification. Do not repeat it.",
    microsoft_attachment_snapshot_conflict: "The draft changed. Reload it before choosing a new operation.",
    microsoft_attachment_attachment_protected: "This attachment cannot safely be removed. Inline images are protected.",
    microsoft_attachment_ledger_full: "Attachment safety storage is full. No new operation can start.",
    microsoft_reconnect_required: "Reconnect the Microsoft mailbox before continuing.",
    microsoft_unauthorized: "Sign in again before continuing.",
    microsoft_attachment_outcome_uncertain: "The attachment outcome could not be confirmed. Check its status; do not repeat the operation.",
  };
  const safe = Object.prototype.hasOwnProperty.call(messages, code) ? code : "microsoft_attachment_outcome_uncertain";
  return { ok: false, outcome: "uncertain", blocked: true, error: { code: safe, message: messages[safe] } };
}

export function newMicrosoftAttachmentRequestId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function post(body: object): Promise<Record<string, unknown>> {
  try {
    const response = await fetch("/api/inboxes/mutate-microsoft-draft-attachments", {
      method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (response.status === 401 && typeof window !== "undefined") {
      try { window.dispatchEvent(new Event("cuevion:auth-session-recheck")); } catch { /* Keep the operation blocked. */ }
    }
    const data: unknown = await response.json();
    if (!data || typeof data !== "object" || Array.isArray(data)) return failure();
    const r = data as Record<string, unknown>;
    if (!response.ok || r.ok !== true) {
      const error = r.error as { code?: unknown } | undefined;
      return failure(error && typeof error.code === "string" ? error.code : undefined);
    }
    return r;
  } catch { return failure(); } // No retries or automatic follow-up mutations.
}

export async function loadMicrosoftDraftAttachments(r: Context): Promise<MicrosoftDraftAttachmentSnapshot | MicrosoftDraftAttachmentFailure> {
  if (!MICROSOFT_DRAFT_ATTACHMENTS_RELEASED) return failure("microsoft_attachment_disabled");
  if (!validContext(r)) return failure();
  const data = await post({ action: "load", ...r });
  if (data.ok !== true) return data as MicrosoftDraftAttachmentFailure;
  if (Object.keys(data).sort().join(",") !== "attachments,complete,mailboxId,ok,providerMessageId,snapshotHash" ||
      data.mailboxId !== r.mailboxId || data.providerMessageId !== r.providerMessageId || data.complete !== true ||
      typeof data.snapshotHash !== "string" || !/^[a-f0-9]{64}$/.test(data.snapshotHash) ||
      !Array.isArray(data.attachments) || data.attachments.length > 100 ||
      !data.attachments.every((a: Record<string, unknown>) => {
        if (!a || typeof a !== "object" || Array.isArray(a) || typeof a.removable !== "boolean") return false;
        const { removable: _removable, ...metadata } = a;
        return trustedMicrosoftAttachment(metadata) && (!a.removable || (a.kind === "file" && a.isInline === false && !a.contentId));
      }) || new Set(data.attachments.map(a => a.id)).size !== data.attachments.length) return failure();
  return data as unknown as MicrosoftDraftAttachmentSnapshot;
}

function receipt(data: Record<string, unknown>, r: Context & { requestId: string }, expectedCount?: number): MicrosoftDraftAttachmentReceipt | MicrosoftDraftAttachmentFailure {
  if (data.ok !== true) return data as MicrosoftDraftAttachmentFailure;
  if (Object.keys(data).sort().join(",") !== "blocked,completed,count,mailboxId,ok,outcome,providerMessageId,requestId" ||
      data.mailboxId !== r.mailboxId || data.providerMessageId !== r.providerMessageId || data.requestId !== r.requestId ||
      !["confirmed_performed", "confirmed_not_performed", "uncertain"].includes(String(data.outcome)) ||
      !Number.isInteger(data.count) || Number(data.count) < 1 || Number(data.count) > 10 ||
      (expectedCount !== undefined && data.count !== expectedCount) ||
      !Number.isInteger(data.completed) || Number(data.completed) < 0 || Number(data.completed) > Number(data.count) ||
      data.blocked !== (data.outcome === "uncertain") ||
      (data.outcome === "confirmed_performed" && data.completed !== data.count) ||
      (data.outcome === "confirmed_not_performed" && data.completed !== 0)) return failure();
  return data as unknown as MicrosoftDraftAttachmentReceipt;
}

export async function mutateMicrosoftDraftAttachments(r: Mutation): Promise<MicrosoftDraftAttachmentReceipt | MicrosoftDraftAttachmentFailure> {
  if (!MICROSOFT_DRAFT_ATTACHMENTS_RELEASED) return failure("microsoft_attachment_disabled");
  if (!validContext(r) || !validOperation(r.requestId) || !/^[a-f0-9]{64}$/.test(r.snapshotHash)) return failure();
  if (r.action === "add" ? !validateMicrosoftDraftFiles(r.files) : r.action !== "remove" || !validId(r.attachmentId)) {
    return failure("microsoft_attachment_selection_invalid");
  }
  // Construct an explicit payload; browser snapshots, tokens and URLs are never sent.
  const body = { mailboxId: r.mailboxId, providerMessageId: r.providerMessageId, requestId: r.requestId, snapshotHash: r.snapshotHash,
    ...(r.action === "add" ? { action: "add", files: r.files } : { action: "remove", attachmentId: r.attachmentId }) };
  return receipt(await post(body), r, r.action === "add" ? r.files.length : 1);
}

export async function inspectMicrosoftDraftAttachmentRequest(r: Context & { requestId: string }): Promise<MicrosoftDraftAttachmentReceipt | MicrosoftDraftAttachmentFailure> {
  if (!MICROSOFT_DRAFT_ATTACHMENTS_RELEASED) return failure("microsoft_attachment_disabled");
  if (!validContext(r) || !validOperation(r.requestId)) return failure();
  return receipt(await post({ action: "status", mailboxId: r.mailboxId, providerMessageId: r.providerMessageId, requestId: r.requestId }), r);
}

export type MicrosoftDraftAttachmentStatus = {
  ok: true; mailboxId: string; providerMessageId: string; requests: MicrosoftDraftAttachmentReceipt[];
};

/** Discover durable operations without storing browser request IDs or resuming writes. */
export async function inspectMicrosoftDraftAttachmentStatus(r: Context): Promise<MicrosoftDraftAttachmentStatus | MicrosoftDraftAttachmentFailure> {
  if (!MICROSOFT_DRAFT_ATTACHMENTS_RELEASED) return failure("microsoft_attachment_disabled");
  if (!validContext(r)) return failure();
  const data = await post({ action: "draft_status", ...r });
  if (data.ok !== true) return data as MicrosoftDraftAttachmentFailure;
  if (Object.keys(data).sort().join(",") !== "mailboxId,ok,providerMessageId,requests" ||
      data.mailboxId !== r.mailboxId || data.providerMessageId !== r.providerMessageId ||
      !Array.isArray(data.requests) || data.requests.length > 32) return failure();
  const requests: MicrosoftDraftAttachmentReceipt[] = [];
  for (const value of data.requests) {
    if (!value || typeof value !== "object" || Array.isArray(value) || !validOperation(value.requestId) ||
        Object.keys(value).sort().join(",") !== "blocked,completed,count,outcome,providerMessageId,requestId") return failure();
    const parsed = receipt({ ...value, ok: true, mailboxId: r.mailboxId }, { ...r, requestId: value.requestId });
    if (!parsed.ok) return failure();
    requests.push(parsed);
  }
  if (new Set(requests.map(x => x.requestId)).size !== requests.length) return failure();
  return { ok: true, ...r, requests };
}
