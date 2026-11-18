export type GmailDraftHeader = { name: string; value: string };
export type GmailDraftSummary = {
  draftId: string; messageId: string; threadId: string;
  from: string; to: string; cc: string; bcc: string; subject: string;
  internalDate: string | null; headers: GmailDraftHeader[];
};
export type GmailDraftPart = {
  partId: string; parentPartId: string | null; mimeType: string; filename: string;
  size: number; headers: GmailDraftHeader[]; attachmentId: string | null;
};
export type GmailDraftDetail = GmailDraftSummary & {
  previewStatus: "available" | "unavailable";
  unavailableReason: "too_large" | "malformed" | "unsupported_mime" | "body_unavailable" | null;
  bodyText: string | null; bodyHtml: string | null; inventoryComplete: boolean;
  mimeParts: GmailDraftPart[];
  attachments: Array<GmailDraftPart & { availability: "metadata_only" }>;
};
export type GmailDraftsRequest = { mailboxId: string; scope?: string; pageToken?: string; draftId?: string };
export type GmailDraftsResult =
  | { ok: true; scope: string; drafts: GmailDraftSummary[]; nextPageToken: string | null }
  | { ok: true; scope: string; draft: GmailDraftDetail }
  | { ok: false; code: string; message: string };
const MAX_BYTES = 10 * 1024 * 1024;
const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown, max = 65536): value is string => typeof value === "string" && new TextEncoder().encode(value).length <= max;
const id = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_-]{1,256}$/.test(value);
const scopeId = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const token = (value: unknown): value is string => typeof value === "string" && /^[\x21-\x7e]{1,4096}$/.test(value);
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).length === keys.length && keys.every(key => Object.prototype.hasOwnProperty.call(value, key));
const summaryKeys = ["draftId", "messageId", "threadId", "from", "to", "cc", "bcc", "subject", "internalDate", "headers"];
const partKeys = ["partId", "parentPartId", "mimeType", "filename", "size", "headers", "attachmentId"];
const detailKeys = ["previewStatus", "unavailableReason", "bodyText", "bodyHtml", "inventoryComplete", "mimeParts", "attachments"];
function headers(value: unknown): boolean {
  return Array.isArray(value) && value.length <= 200 && value.every(h => isRecord(h) && exact(h, ["name", "value"]) && text(h.name, 128) && text(h.value, 16384));
}
function summary(value: Record<string, unknown>): boolean {
  return [value.draftId, value.messageId, value.threadId].every(id) &&
    [value.from, value.to, value.cc, value.bcc, value.subject].every(v => text(v)) && headers(value.headers) &&
    (value.internalDate === null || typeof value.internalDate === "string" && /^[0-9]{1,16}$/.test(value.internalDate));
}
function part(value: unknown, attachment = false): boolean {
  return isRecord(value) && exact(value, [...partKeys, ...(attachment ? ["availability"] : [])]) &&
    text(value.partId, 128) && (value.parentPartId === null || text(value.parentPartId, 128)) &&
    text(value.mimeType, 256) && text(value.filename, 4096) && headers(value.headers) &&
    Number.isSafeInteger(value.size) && Number(value.size) >= 0 && Number(value.size) <= 2 ** 31 &&
    (value.attachmentId === null || token(value.attachmentId)) && (!attachment || value.availability === "metadata_only");
}
export function validGmailDraftDetail(value: unknown): value is GmailDraftDetail {
  if (!isRecord(value) || !exact(value, [...summaryKeys, ...detailKeys]) || !summary(value) ||
      !["available", "unavailable"].includes(String(value.previewStatus)) || typeof value.inventoryComplete !== "boolean" ||
      !Array.isArray(value.mimeParts) || value.mimeParts.length > 100 || !value.mimeParts.every(p => part(p)) ||
      !Array.isArray(value.attachments) || value.attachments.length > 100 || !value.attachments.every(p => part(p, true)) ||
      ![value.bodyText, value.bodyHtml].every(v => v === null || text(v, 8 * 1024 * 1024))) return false;
  return value.previewStatus === "available" ? value.unavailableReason === null && value.inventoryComplete === true :
    ["too_large", "malformed", "unsupported_mime", "body_unavailable"].includes(String(value.unavailableReason)) && value.bodyText === null && value.bodyHtml === null;
}
async function boundedText(response: Response): Promise<string> {
  if (Number(response.headers.get("content-length")) > MAX_BYTES) throw Error("size");
  const reader = response.body?.getReader();
  if (!reader) { const raw = await response.text(); if (!text(raw, MAX_BYTES)) throw Error("size"); return raw; }
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) { const chunk = await reader.read(); if (chunk.done) break; size += chunk.value.length;
      if (size > MAX_BYTES) { await reader.cancel(); throw Error("size"); } chunks.push(chunk.value); }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}
export async function fetchGmailDrafts(request: GmailDraftsRequest, signal?: AbortSignal): Promise<GmailDraftsResult> {
  const failure = (code = "gmail_drafts_unavailable", message = "Gmail drafts could not be loaded. Refresh Drafts to try again."): GmailDraftsResult => ({ ok: false, code, message });
  if (!text(request.mailboxId, 256) || !request.mailboxId.trim() ||
      request.scope !== undefined && !scopeId(request.scope) ||
      request.pageToken !== undefined && (!token(request.pageToken) || !request.scope) ||
      request.draftId !== undefined && (!id(request.draftId) || !request.scope || request.pageToken !== undefined)) return failure("invalid_request");
  try {
    const response = await fetch("/api/inboxes/fetch-gmail-drafts", {
      method: "POST", credentials: "include", cache: "no-store", signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
    });
    if (response.status === 401) {
      try { if (typeof window !== "undefined") window.dispatchEvent(new Event("cuevion:auth-session-recheck")); } catch { /* Preserve safe failure. */ }
      return failure("unauthorized", "Sign in or check the Gmail connection to load drafts.");
    }
    if (response.status === 409) return failure("scope_changed", "The Gmail connection changed. Refresh Drafts to continue.");
    if (response.status === 404) return failure("not_found", "This draft is no longer available. Refresh Drafts.");
    if (response.status === 403) return failure("permission_denied", "Gmail did not permit draft reading with this connection.");
    if (response.status !== 200) return failure();
    const value: unknown = JSON.parse(await boundedText(response));
    if (!isRecord(value) || value.ok !== true || value.mailboxId !== request.mailboxId || !scopeId(value.scope) ||
        request.scope !== undefined && value.scope !== request.scope) return failure("scope_changed");
    if (request.draftId !== undefined) {
      if (!exact(value, ["ok", "mailboxId", "scope", "draft"]) || !validGmailDraftDetail(value.draft) || value.draft.draftId !== request.draftId) return failure();
      return { ok: true, scope: value.scope, draft: value.draft };
    }
    if (!exact(value, ["ok", "mailboxId", "scope", "drafts", "nextPageToken"]) || !Array.isArray(value.drafts) || value.drafts.length > 20 ||
        !value.drafts.every(d => isRecord(d) && exact(d, summaryKeys) && summary(d)) ||
        !(value.nextPageToken === null || token(value.nextPageToken)) || value.nextPageToken === request.pageToken) return failure();
    return { ok: true, scope: value.scope, drafts: value.drafts as GmailDraftSummary[], nextPageToken: value.nextPageToken as string | null };
  } catch { return failure(); }
}
