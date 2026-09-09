export const EXACT_MAILBOX_MESSAGE_ENDPOINT = "/api/inboxes/fetch-exact-message";
export const EXACT_MAILBOX_MESSAGE_ERROR = "The exact message could not be loaded.";

export type ExactMailboxSourceRef =
  | { readonly provider: "google"; readonly providerMessageId: string }
  | { readonly provider: "custom_imap"; readonly folder: "INBOX"; readonly uidValidity: string; readonly imapUid: string };

export type ExactMailboxMessageIdentity = {
  id: string;
  serverMailboxId?: string;
  providerFolder?: string;
  providerMessageId?: string;
  providerThreadId?: string;
  uidValidity?: string;
  imapUid?: string;
  threadIdentityContext?: { mailboxId: string; provider: string; folder: string; uidValidity: string | null };
};

export type ExactMailboxAttachment = {
  id: string;
  name: string;
  mimeType?: string;
  size?: number;
  contentId?: string;
  disposition?: string;
  inlineSrc?: string;
};

/** Existing canonical mail preview fields, ready for normalizeMailMessage with
 * time: timestamp and applyLiveThreadIdentity. No second rendering model.
 */
export type ExactMailboxMessage = ExactMailboxMessageIdentity & {
  serverMailboxId: string;
  providerFolder: string;
  sender: string;
  subject: string;
  snippet: string;
  from: string;
  to: string;
  cc: string;
  timestamp: string;
  createdAt: string;
  body: string[];
  bodyHtml?: string;
  attachments: ExactMailboxAttachment[];
  unread: boolean;
  flagged: boolean;
  labelIds?: string[];
  threadId?: string;
  rfcMessageId?: string;
};

export type ExactMailboxMessageResponse = {
  v: 1;
  mailboxId: string;
  sourceRef: ExactMailboxSourceRef;
  message: ExactMailboxMessage;
};

const ERROR_CODES = [
  "authentication_required", "forbidden", "mailbox_not_found", "provider_mismatch",
  "source_invalid", "source_changed", "message_not_found", "service_unavailable",
  "invalid_response", "rate_limited", "method_not_allowed", "payload_too_large",
  "unsupported_media_type",
] as const;
export type ExactMailboxMessageFailure = typeof ERROR_CODES[number] | "aborted";
export type ExactMailboxMessageResult =
  | { status: "success"; response: ExactMailboxMessageResponse }
  | { status: ExactMailboxMessageFailure };

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function closed(value: unknown, required: readonly string[], optional: readonly string[] = []): value is Record<string, unknown> {
  return record(value) && required.every((key) => Object.prototype.hasOwnProperty.call(value, key)) &&
    Object.keys(value).every((key) => required.includes(key) || optional.includes(key));
}
function positiveDecimal(value: unknown, maximum?: string): value is string {
  return typeof value === "string" && /^[1-9][0-9]{0,19}$/.test(value) &&
    (!maximum || value.length < maximum.length || (value.length === maximum.length && value <= maximum));
}
function providerIdentifier(value: unknown): value is string {
  return typeof value === "string" && value.length <= 512 && /^[\x20-\x7e]+$/.test(value) && value === value.trim();
}
function googleFolder(labels: readonly string[]): string {
  for (const [label, folder] of [
    ["TRASH", "Trash"], ["SPAM", "Spam"], ["DRAFT", "Drafts"],
    ["INBOX", "Inbox"], ["SENT", "Sent"],
  ]) {
    if (labels.includes(label)) return folder;
  }
  return "Archive";
}
const MAX_MESSAGE_BYTES = 6 * 1024 * 1024;
function textWithin(value: unknown, maximum = 65536, nonempty = false): value is string {
  return typeof value === "string" && (!nonempty || value.length > 0) &&
    value.length <= maximum && !/\p{Cs}/u.test(value) && new TextEncoder().encode(value).length <= maximum;
}
export function isExactMailboxId(value: unknown): value is string {
  return typeof value === "string" && /^[\x21-\x7e]{1,256}$/.test(value);
}
export function isExactMailboxSourceRef(value: unknown): value is ExactMailboxSourceRef {
  if (closed(value, ["provider", "providerMessageId"])) {
    return value.provider === "google" && providerIdentifier(value.providerMessageId);
  }
  return closed(value, ["provider", "folder", "uidValidity", "imapUid"]) &&
    value.provider === "custom_imap" && value.folder === "INBOX" &&
    positiveDecimal(value.uidValidity) && positiveDecimal(value.imapUid, "4294967295");
}
export function areExactMailboxSourcesEqual(left: unknown, right: unknown): boolean {
  if (!isExactMailboxSourceRef(left) || !isExactMailboxSourceRef(right) || left.provider !== right.provider) return false;
  return left.provider === "google" && right.provider === "google"
    ? left.providerMessageId === right.providerMessageId
    : left.provider === "custom_imap" && right.provider === "custom_imap" &&
      left.folder === right.folder && left.uidValidity === right.uidValidity && left.imapUid === right.imapUid;
}

/** Exact identity only. Subjects, sender, dates, thread IDs and renderer IDs are
 * never used to infer or substitute a source message.
 */
export function matchesExactMailboxMessageIdentity(
  message: ExactMailboxMessageIdentity,
  mailboxId: string,
  sourceRef: ExactMailboxSourceRef,
): boolean {
  if (!isExactMailboxId(mailboxId) || !isExactMailboxSourceRef(sourceRef) ||
    !message || message.serverMailboxId !== mailboxId || typeof message.id !== "string" || !message.id) return false;
  const context = message.threadIdentityContext;
  if (context && (context.mailboxId !== mailboxId || context.provider !== sourceRef.provider)) return false;
  if (sourceRef.provider === "google") {
    return message.providerMessageId === sourceRef.providerMessageId && message.imapUid === undefined;
  }
  return message.providerFolder === "INBOX" && message.providerMessageId === undefined &&
    message.providerThreadId === undefined && message.uidValidity === sourceRef.uidValidity &&
    message.imapUid === sourceRef.imapUid &&
    (!context || (context.folder === "INBOX" && context.uidValidity === sourceRef.uidValidity));
}

function parseAttachment(value: unknown): ExactMailboxAttachment | null {
  const optional = ["mimeType", "size", "contentId", "disposition", "inlineSrc"];
  if (!closed(value, ["id", "name"], optional) || !textWithin(value.id, 65536, true) || !textWithin(value.name, 65536, true)) return null;
  if (optional.some((key) => key !== "size" && key in value && !textWithin(value[key], key === "inlineSrc" ? MAX_MESSAGE_BYTES : 65536))) return null;
  if ("size" in value && (typeof value.size !== "number" || !Number.isSafeInteger(value.size) || value.size < 0 || value.size > MAX_MESSAGE_BYTES)) return null;
  return { ...value } as ExactMailboxAttachment;
}

export function parseExactMailboxMessageResponse(
  value: unknown,
  mailboxId: string,
  sourceRef: ExactMailboxSourceRef,
): ExactMailboxMessageResponse | null {
  if (!isExactMailboxId(mailboxId) || !isExactMailboxSourceRef(sourceRef) ||
    !closed(value, ["v", "mailboxId", "sourceRef", "message"]) || value.v !== 1 || value.mailboxId !== mailboxId ||
    !areExactMailboxSourcesEqual(value.sourceRef, sourceRef)) return null;
  const stringFields = ["id", "serverMailboxId", "providerFolder", "sender", "subject", "snippet", "from", "to", "cc", "timestamp", "createdAt"];
  const providerFields = sourceRef.provider === "google"
    ? ["providerMessageId", "labelIds"] : ["imapUid", "uidValidity", "threadId"];
  const optionalFields = ["bodyHtml", "rfcMessageId", ...(sourceRef.provider === "google" ? ["providerThreadId"] : [])];
  const message = value.message;
  if (!closed(message, [...stringFields, "body", "attachments", "unread", "flagged", ...providerFields], optionalFields) ||
    stringFields.some((key) => !textWithin(message[key])) || !message.id ||
    ("bodyHtml" in message && !textWithin(message.bodyHtml, MAX_MESSAGE_BYTES)) ||
    ("rfcMessageId" in message && !textWithin(message.rfcMessageId, 65536, true)) ||
    typeof message.unread !== "boolean" || typeof message.flagged !== "boolean" ||
    !Array.isArray(message.body) || message.body.length < 1 || message.body.length > 10000 ||
    !message.body.every((line) => textWithin(line, MAX_MESSAGE_BYTES)) ||
    !Array.isArray(message.attachments) || message.attachments.length > 100) return null;
  if (sourceRef.provider === "google") {
    if (!Array.isArray(message.labelIds) || message.labelIds.length > 1000 ||
      !message.labelIds.every(providerIdentifier) ||
      new Set(message.labelIds).size !== message.labelIds.length ||
      message.providerFolder !== googleFolder(message.labelIds) ||
      ("providerThreadId" in message && !providerIdentifier(message.providerThreadId))) return null;
  } else if (!textWithin(message.threadId, 512, true)) return null;
  if (!matchesExactMailboxMessageIdentity(message as ExactMailboxMessage, mailboxId, sourceRef)) return null;
  const attachments = message.attachments.map(parseAttachment);
  if (attachments.some((attachment) => attachment === null)) return null;
  return {
    v: 1, mailboxId, sourceRef: { ...sourceRef },
    message: {
      ...message, body: [...message.body], attachments,
      ...(sourceRef.provider === "google" ? { labelIds: [...message.labelIds as string[]] } : {}),
    } as ExactMailboxMessage,
  };
}

export async function fetchExactMailboxMessage(
  mailboxId: string,
  sourceRef: ExactMailboxSourceRef,
  options: { signal?: AbortSignal; fetchImplementation?: typeof fetch } = {},
): Promise<ExactMailboxMessageResult> {
  if (options.signal?.aborted) return { status: "aborted" };
  if (!isExactMailboxId(mailboxId) || !isExactMailboxSourceRef(sourceRef)) return { status: "source_invalid" };
  // Capture caller-owned inputs before any asynchronous boundary.
  const requestedSource = { ...sourceRef };
  try {
    const response = await (options.fetchImplementation ?? fetch)(EXACT_MAILBOX_MESSAGE_ENDPOINT, {
      method: "POST", credentials: "include", cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ v: 1, mailboxId, sourceRef: requestedSource }), signal: options.signal,
    });
    if (options.signal?.aborted) return { status: "aborted" };
    let payload: unknown;
    try { payload = await response.json(); } catch { return { status: options.signal?.aborted ? "aborted" : "invalid_response" }; }
    if (options.signal?.aborted) return { status: "aborted" };
    if (response.ok) {
      const parsed = parseExactMailboxMessageResponse(payload, mailboxId, requestedSource);
      return parsed ? { status: "success", response: parsed } : { status: "invalid_response" };
    }
    if (closed(payload, ["error"]) && closed(payload.error, ["code", "message"]) &&
      payload.error.message === EXACT_MAILBOX_MESSAGE_ERROR &&
      ERROR_CODES.includes(payload.error.code as typeof ERROR_CODES[number])) {
      return { status: payload.error.code as typeof ERROR_CODES[number] };
    }
    return { status: "invalid_response" };
  } catch {
    return { status: options.signal?.aborted ? "aborted" : "service_unavailable" };
  }
}
