export type ImapTrashFolderMode =
  | "automatic"
  | "needs_mapping"
  | "configured";

export type ImapTrashFolderOption = {
  providerFolder: string;
};

export type ImapTrashFolderState =
  | {
      mode: "automatic" | "configured";
      currentFolder: string;
    }
  | {
      mode: "needs_mapping";
      currentFolder: null;
    };

export type ImapTrashFolderInventorySuccess = {
  ok: true;
  mailboxId: string;
  trash: ImapTrashFolderState;
  folders: ImapTrashFolderOption[];
};

export type ImapFolderMappingFailure = {
  ok: false;
  error: {
    code: string;
    message: string;
  };
};

export type ImapTrashFolderInventoryResponse =
  | ImapTrashFolderInventorySuccess
  | ImapFolderMappingFailure;

export type SaveImapTrashFolderMappingRequest = {
  mailboxId: string;
  role: "trash";
  selectedFolder: string;
};

const MAX_MAILBOX_ID_BYTES = 512;
const MAX_PROVIDER_FOLDER_BYTES = 16_384;
const MAX_PUBLIC_ERROR_TEXT_BYTES = 1_024;
const MAX_FOLDER_COUNT = 4_096;
const MAX_RESPONSE_BYTES = 4 * 1_024 * 1_024;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;
const encoder = new TextEncoder();

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: string[]) {
  const actualKeys = Object.keys(value).sort();
  const expectedKeys = [...keys].sort();
  return (
    actualKeys.length === expectedKeys.length &&
    actualKeys.every((key, index) => key === expectedKeys[index])
  );
}

function hasUnpairedSurrogate(value: string) {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      const nextCodeUnit = value.charCodeAt(index + 1);
      if (nextCodeUnit < 0xdc00 || nextCodeUnit > 0xdfff) {
        return true;
      }
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      return true;
    }
  }

  return false;
}

function isBoundedExactString(value: unknown, maximumBytes: number) {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value === value.trim() &&
    !CONTROL_CHARACTERS.test(value) &&
    !hasUnpairedSurrogate(value) &&
    encoder.encode(value).byteLength <= maximumBytes
  );
}

function isMailboxId(value: unknown): value is string {
  return isBoundedExactString(value, MAX_MAILBOX_ID_BYTES);
}

function isProviderFolder(value: unknown): value is string {
  return isBoundedExactString(value, MAX_PROVIDER_FOLDER_BYTES);
}

function isTrashTargetFolder(value: unknown): value is string {
  return isProviderFolder(value) && value.toLowerCase() !== "inbox";
}

function isPublicErrorText(value: unknown) {
  return isBoundedExactString(value, MAX_PUBLIC_ERROR_TEXT_BYTES);
}

function failure(code: string, message: string): ImapFolderMappingFailure {
  return {
    ok: false,
    error: { code, message },
  };
}

function parsePublicFailure(value: unknown): ImapFolderMappingFailure | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["ok", "error"]) ||
    value.ok !== false ||
    !isRecord(value.error) ||
    !hasExactKeys(value.error, ["code", "message"]) ||
    !isPublicErrorText(value.error.code) ||
    !isPublicErrorText(value.error.message)
  ) {
    return null;
  }

  return value as ImapFolderMappingFailure;
}

function parseTrashState(
  value: unknown,
): ImapTrashFolderInventorySuccess["trash"] | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["mode", "currentFolder"]) ||
    (value.mode !== "automatic" &&
      value.mode !== "needs_mapping" &&
      value.mode !== "configured")
  ) {
    return null;
  }

  if (value.mode === "needs_mapping") {
    return value.currentFolder === null
      ? { mode: value.mode, currentFolder: null }
      : null;
  }

  return isTrashTargetFolder(value.currentFolder)
    ? { mode: value.mode, currentFolder: value.currentFolder }
    : null;
}

function parseFolderOptions(value: unknown): ImapTrashFolderOption[] | null {
  if (!Array.isArray(value) || value.length > MAX_FOLDER_COUNT) {
    return null;
  }

  const seenFolders = new Set<string>();
  const folders: ImapTrashFolderOption[] = [];

  for (const entry of value) {
    if (
      !isRecord(entry) ||
      !hasExactKeys(entry, ["providerFolder"]) ||
      !isTrashTargetFolder(entry.providerFolder) ||
      seenFolders.has(entry.providerFolder)
    ) {
      return null;
    }

    seenFolders.add(entry.providerFolder);
    folders.push({ providerFolder: entry.providerFolder });
  }

  return folders;
}

function parseInventorySuccess(
  value: unknown,
  mailboxId: string,
): ImapTrashFolderInventorySuccess | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["ok", "mailboxId", "trash", "folders"]) ||
    value.ok !== true ||
    value.mailboxId !== mailboxId
  ) {
    return null;
  }

  const trash = parseTrashState(value.trash);
  const folders = parseFolderOptions(value.folders);
  if (!trash || !folders) {
    return null;
  }

  if (
    trash.mode === "configured" &&
    !folders.some((entry) => entry.providerFolder === trash.currentFolder)
  ) {
    return null;
  }

  return {
    ok: true,
    mailboxId,
    trash,
    folders,
  };
}

async function readBoundedJson(response: Response): Promise<unknown> {
  const rawPayload = await response.text();
  if (
    !rawPayload.trim() ||
    encoder.encode(rawPayload).byteLength > MAX_RESPONSE_BYTES
  ) {
    return null;
  }

  try {
    return JSON.parse(rawPayload) as unknown;
  } catch {
    return null;
  }
}

async function postFolderMappingRequest({
  endpoint,
  requestBody,
  mailboxId,
  invalidResponseCode,
  invalidResponseMessage,
  networkErrorCode,
  networkErrorMessage,
}: {
  endpoint: string;
  requestBody: Record<string, string>;
  mailboxId: string;
  invalidResponseCode: string;
  invalidResponseMessage: string;
  networkErrorCode: string;
  networkErrorMessage: string;
}): Promise<ImapTrashFolderInventoryResponse> {
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(requestBody),
    });
    const payload = await readBoundedJson(response);
    const publicFailure = parsePublicFailure(payload);

    if (response.status !== 200) {
      return (
        publicFailure ?? failure(invalidResponseCode, invalidResponseMessage)
      );
    }

    return (
      parseInventorySuccess(payload, mailboxId) ??
      publicFailure ??
      failure(invalidResponseCode, invalidResponseMessage)
    );
  } catch {
    return failure(networkErrorCode, networkErrorMessage);
  }
}

export async function listImapTrashFolders({
  mailboxId,
}: {
  mailboxId: string;
}): Promise<ImapTrashFolderInventoryResponse> {
  if (!isMailboxId(mailboxId)) {
    return failure(
      "invalid_imap_folder_list_request",
      "A valid connected IMAP mailbox is required.",
    );
  }

  return postFolderMappingRequest({
    endpoint: "/api/inboxes/list-imap-folders",
    requestBody: { mailboxId },
    mailboxId,
    invalidResponseCode: "imap_folder_list_response_invalid",
    invalidResponseMessage:
      "The IMAP folder list could not be verified safely.",
    networkErrorCode: "imap_folder_list_failed",
    networkErrorMessage: "Could not load IMAP folders safely.",
  });
}

export async function saveImapTrashFolderMapping(
  request: SaveImapTrashFolderMappingRequest,
): Promise<ImapTrashFolderInventoryResponse> {
  if (
    !isRecord(request) ||
    !hasExactKeys(request as unknown as Record<string, unknown>, [
      "mailboxId",
      "role",
      "selectedFolder",
    ]) ||
    !isMailboxId(request.mailboxId) ||
    request.role !== "trash" ||
    !isTrashTargetFolder(request.selectedFolder)
  ) {
    return failure(
      "invalid_imap_folder_mapping_request",
      "Choose one current provider folder for this IMAP mailbox.",
    );
  }

  const response = await postFolderMappingRequest({
    endpoint: "/api/inboxes/save-imap-folder-mapping",
    requestBody: {
      mailboxId: request.mailboxId,
      role: request.role,
      selectedFolder: request.selectedFolder,
    },
    mailboxId: request.mailboxId,
    invalidResponseCode: "imap_folder_mapping_response_invalid",
    invalidResponseMessage:
      "The saved IMAP folder mapping could not be verified safely.",
    networkErrorCode: "imap_folder_mapping_failed",
    networkErrorMessage: "Could not save the IMAP folder mapping safely.",
  });

  if (response.ok && response.trash.mode === "needs_mapping") {
    return failure(
      "imap_folder_mapping_response_invalid",
      "The saved IMAP folder mapping could not be verified safely.",
    );
  }

  return response;
}
