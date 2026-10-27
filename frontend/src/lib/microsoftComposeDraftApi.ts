import { canonicalMicrosoftComposeHtml } from "./microsoftComposeHtml";
import { normalizeMicrosoftRecipientList } from "./microsoftComposeRequest";
import { newMicrosoftAttachmentRequestId } from "./microsoftDraftAttachmentApi";

/** Independent source gate. No browser/env setting enables Compose drafts. */
export const MICROSOFT_COMPOSE_DRAFTS_RELEASED = false;
export const MICROSOFT_RESPONSE_DRAFTS_RELEASED = false;
export type MicrosoftResponseMode = "reply" | "reply_all" | "forward";
export type MicrosoftResponseContext = { mode: MicrosoftResponseMode; quotePreview: string };
export const createMicrosoftComposeDraftRequestId = newMicrosoftAttachmentRequestId;
export type MicrosoftComposeDraftEdit = { subject: string; body: string; bodyType?: "html"; to: string[]; cc: string[]; bcc: string[] };
export type MicrosoftComposeDraftReceipt = {
  requestId: string; action: "create" | "update"; providerMessageId: string | null;
  outcome: "confirmed_performed" | "confirmed_not_performed" | "uncertain"; blocked: boolean;
};
export type MicrosoftComposeDraftRecord = { providerMessageId: string; etag: string; snapshotHash: string; edit: MicrosoftComposeDraftEdit; response?: MicrosoftResponseContext };
type Failure = { ok: false; blocked: true; error: { code: string; message: string } };
type Context = { mailboxId: string; acceptance?: boolean };
const id = (s: unknown): s is string => typeof s === "string" && /^[^\x00-\x20\x7f-\x9f?#]{1,512}$/.test(s) && s !== "." && !s.includes("..") && new TextEncoder().encode(s).length <= 512;
const operation = (s: unknown): s is string => typeof s === "string" && /^[A-Za-z0-9_-]{43}$/.test(s);
const etag = (s: unknown): s is string => typeof s === "string" && /^(?:W\/)?"[\x21\x23-\x7e]{1,256}"$/.test(s);
const context = (r: Context) => /^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$/.test(r.mailboxId);
const keys = (v: object, expected: string) => Object.keys(v).sort().join(",") === expected;
const object = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const messages: Record<string, string> = {
  microsoft_acceptance_blocked: "This draft test is not authorized for the current owner and Microsoft account.",
  microsoft_acceptance_stopped: "Microsoft draft testing is stopped or expired. Check status; do not repeat an uncertain action.",
  microsoft_acceptance_unavailable: "The draft test authorization cannot be verified. Writes remain blocked.",
  microsoft_acceptance_limit: "The authorized draft test operation limit has been reached.",
  microsoft_compose_html_unsupported: "This HTML formatting cannot be safely edited. The original draft is preserved; do not retry an uncertain save.",
  microsoft_response_busy: "Another mailbox operation is in progress. Check status before continuing.",
  microsoft_response_blocked: "An earlier draft or attachment operation needs verification. Do not repeat it.",
  microsoft_response_disabled: "Microsoft reply and forward drafts are not enabled.",
  microsoft_response_invalid: "Check the selected message and response details.",
  microsoft_response_unsupported: "This message cannot be safely used for a reply or forward draft.",
  microsoft_response_conflict: "The original message or protected quote changed. Reopen it before continuing.",
  microsoft_response_uncertain: "The response draft may have been saved. Check Drafts and status; do not repeat the operation.",
  microsoft_response_store_unavailable: "Secure response draft tracking is unavailable. No retry is allowed.",
  microsoft_compose_body_unconfirmed: "Microsoft may have saved this draft, but its original body cannot be verified as plain text. Do not save or send again. Review Drafts and check status.",
  microsoft_compose_body_unverifiable: "The original message body cannot be verified as plain text. HTML projections, unconfirmed preferences and unsupported MIME remain protected; saving or sending is blocked.",
  microsoft_compose_disabled: "Microsoft draft composition is not enabled.",
  microsoft_compose_invalid: "Check the recipients, subject and message. Use at most 20 distinct recipients and 32000 message characters.",
  microsoft_compose_conflict: "This draft changed in Outlook. Reopen it before saving. No overwrite was attempted.",
  microsoft_compose_unsupported: "Only plain-text drafts can be edited here. HTML and CID content remain protected.",
  microsoft_compose_busy: "Another mailbox operation is in progress. Check status before continuing.",
  microsoft_compose_blocked: "An attachment operation needs verification. Do not repeat it.",
  microsoft_compose_operation_blocked: "An earlier draft operation is uncertain. Do not create or save it again.",
  microsoft_compose_operation_conflict: "This operation identifier cannot be reused for another draft or message.",
  microsoft_compose_store_unavailable: "Secure draft tracking could not be verified. Check status before continuing.",
  microsoft_compose_store_invalid: "Secure draft tracking could not be verified. Check status before continuing.",
  microsoft_compose_ledger_full: "Secure draft tracking is full. New operations are blocked.",
  microsoft_reconnect_required: "Reconnect this Microsoft mailbox before continuing.",
  microsoft_write_permission_required: "Reconnect Microsoft to allow draft saving.",
  microsoft_unauthorized: "Sign in again before continuing.",
  microsoft_mailbox_not_found: "The selected Microsoft mailbox could not be verified.",
  microsoft_compose_uncertain: "The draft result is uncertain. Check status and Microsoft Drafts before repeating any action.",
};
function failure(code = "microsoft_compose_uncertain"): Failure {
  const safe = Object.prototype.hasOwnProperty.call(messages, code) ? code : "microsoft_compose_uncertain";
  return { ok: false, blocked: true, error: { code: safe, message: messages[safe] } };
}

export function validMicrosoftComposeDraftEdit(value: unknown): value is MicrosoftComposeDraftEdit {
  if (!object(value) || !(keys(value, "bcc,body,cc,subject,to") || keys(value, "bcc,body,bodyType,cc,subject,to") && value.bodyType === "html") || typeof value.subject !== "string" ||
      value.subject.length > 250 || /[\x00-\x1f\x7f]/.test(value.subject) || typeof value.body !== "string" ||
      value.body.length > 32000 || value.body.includes("\0") || new TextEncoder().encode(value.body).length > 128000) return false;
  if (value.bodyType === "html" && canonicalMicrosoftComposeHtml(value.body) === null) return false;
  const all: string[] = [];
  for (const field of [value.to, value.cc, value.bcc]) {
    if (!Array.isArray(field) || field.some(s => typeof s !== "string" || s.length > 320)) return false;
    const parsed = normalizeMicrosoftRecipientList(field.join(","));
    if (!parsed || JSON.stringify(parsed) !== JSON.stringify(field)) return false;
    all.push(...field);
  }
  return all.length <= 20 && new Set(all).size === all.length;
}
export function prepareMicrosoftComposeDraftEdit(subject: string, body: string, to: string, cc: string, bcc: string, bodyType?: "html"): MicrosoftComposeDraftEdit | null {
  if (bodyType) {
    const safe = canonicalMicrosoftComposeHtml(body);
    if (safe === null) return null;
    body = safe;
  }
  const candidate = { subject, body, ...(bodyType ? { bodyType } : {}), to: normalizeMicrosoftRecipientList(to), cc: normalizeMicrosoftRecipientList(cc), bcc: normalizeMicrosoftRecipientList(bcc) };
  return validMicrosoftComposeDraftEdit(candidate) ? candidate : null;
}
function validReceipt(v: unknown): v is MicrosoftComposeDraftReceipt {
  return object(v) && keys(v, "action,blocked,outcome,providerMessageId,requestId") && operation(v.requestId) &&
    ["create", "update"].includes(String(v.action)) &&
    ["confirmed_performed", "confirmed_not_performed", "uncertain"].includes(String(v.outcome)) &&
    v.blocked === (v.outcome === "uncertain") && (id(v.providerMessageId) ||
      v.providerMessageId === null && v.action === "create" && v.outcome !== "confirmed_performed");
}
async function post(body: object): Promise<Record<string, unknown>> {
  try {
    const { acceptance: _acceptance, ...payload } = body as { acceptance?: boolean };
    const response = await fetch("/api/inboxes/microsoft-compose-draft", { method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (response.status === 401 && typeof window !== "undefined") {
      try { window.dispatchEvent(new Event("cuevion:auth-session-recheck")); } catch { /* fail closed */ }
    }
    const data: unknown = await response.json();
    if (!object(data)) return failure();
    if (!response.ok || data.ok !== true) return failure(object(data.error) && typeof data.error.code === "string" ? data.error.code : undefined);
    return data;
  } catch { return failure(); } // No retries, redirects or automatic mutations.
}
export async function inspectMicrosoftComposeDrafts(r: Context & { before?: number; providerMessageId?: string; requestId?: string }): Promise<Failure | { ok: true; operations: MicrosoftComposeDraftReceipt[]; nextCursor?: number | null }> {
  if (!MICROSOFT_COMPOSE_DRAFTS_RELEASED && !r.acceptance) return failure("microsoft_compose_disabled");
  if (!context(r) || r.before !== undefined && (!Number.isSafeInteger(r.before) || r.before < 1) ||
      r.providerMessageId !== undefined && !id(r.providerMessageId) || r.requestId !== undefined && !operation(r.requestId)) return failure("microsoft_compose_invalid");
  const data = await post({ action: "status", ...r });
  if (data.ok !== true) return data as Failure;
  const paged = keys(data, "mailboxId,nextCursor,ok,operations");
  if (!(paged || keys(data, "mailboxId,ok,operations")) || data.mailboxId !== r.mailboxId || !Array.isArray(data.operations) ||
      data.operations.length > (paged ? 97 : 32) || !data.operations.every(validReceipt) || new Set(data.operations.map(v => v.requestId)).size !== data.operations.length ||
      paged && data.nextCursor !== null && (!Number.isSafeInteger(data.nextCursor) || (data.nextCursor as number) < 1 || r.before !== undefined && (data.nextCursor as number) >= r.before)) return failure();
  return { ok: true, operations: data.operations, ...(paged ? { nextCursor: data.nextCursor as number | null } : {}) };
}
export async function loadMicrosoftComposeDraft(r: Context & { providerMessageId: string }): Promise<Failure | { ok: true; draft: MicrosoftComposeDraftRecord }> {
  if (!MICROSOFT_COMPOSE_DRAFTS_RELEASED && !r.acceptance) return failure("microsoft_compose_disabled");
  if (!context(r) || !id(r.providerMessageId)) return failure("microsoft_compose_invalid");
  const data = await post({ action: "load", mailboxId: r.mailboxId, providerMessageId: r.providerMessageId });
  if (data.ok !== true) return data as Failure;
  if (!(keys(data, "edit,etag,mailboxId,ok,providerMessageId,snapshotHash") ||
        keys(data, "edit,etag,mailboxId,ok,providerMessageId,response,snapshotHash") && validResponse(data.response)) || data.mailboxId !== r.mailboxId ||
      data.providerMessageId !== r.providerMessageId || !etag(data.etag) || !validMicrosoftComposeDraftEdit(data.edit) ||
      typeof data.snapshotHash !== "string" || !/^[a-f0-9]{64}$/.test(data.snapshotHash)) return failure();
  return { ok: true, draft: { providerMessageId: r.providerMessageId, etag: data.etag, snapshotHash: data.snapshotHash, edit: data.edit, ...(validResponse(data.response) ? { response: data.response } : {}) } };
}
export async function saveMicrosoftComposeDraft(r: Context & { requestId: string; edit: MicrosoftComposeDraftEdit } & (
  { action: "create" } | { action: "update"; providerMessageId: string; etag: string }
)): Promise<Failure | { ok: true; receipt: MicrosoftComposeDraftReceipt }> {
  if (!MICROSOFT_COMPOSE_DRAFTS_RELEASED && !r.acceptance) return failure("microsoft_compose_disabled");
  if (!context(r) || !operation(r.requestId) || !validMicrosoftComposeDraftEdit(r.edit) ||
      r.action !== "create" && (r.action !== "update" || !id(r.providerMessageId) || !etag(r.etag))) return failure("microsoft_compose_invalid");
  const data = await post({ mailboxId: r.mailboxId, requestId: r.requestId, action: r.action, edit: r.edit,
    ...(r.action === "update" ? { providerMessageId: r.providerMessageId, etag: r.etag } : {}) });
  if (data.ok !== true) return data as Failure;
  if (!keys(data, "mailboxId,ok,receipt") || data.mailboxId !== r.mailboxId || !validReceipt(data.receipt) ||
      data.receipt.requestId !== r.requestId || data.receipt.action !== r.action ||
      r.action === "update" && data.receipt.providerMessageId !== r.providerMessageId) return failure();
  return { ok: true, receipt: data.receipt };
}

function validResponse(v: unknown): v is MicrosoftResponseContext {
  return object(v) && keys(v, "mode,quotePreview") && ["reply", "reply_all", "forward"].includes(String(v.mode)) &&
    typeof v.quotePreview === "string" && v.quotePreview.length <= 6000;
}
export async function prepareMicrosoftResponseDraft(r: Context & { mode: MicrosoftResponseMode; sourceProviderMessageId: string }): Promise<Failure | {
  ok: true; sourceHash: string; edit: MicrosoftComposeDraftEdit; response: MicrosoftResponseContext;
}> {
  if ((!MICROSOFT_COMPOSE_DRAFTS_RELEASED || !MICROSOFT_RESPONSE_DRAFTS_RELEASED) && !r.acceptance) return failure("microsoft_response_disabled");
  if (!context(r) || !id(r.sourceProviderMessageId) || !["reply","reply_all","forward"].includes(r.mode)) return failure("microsoft_response_invalid");
  const data = await post({ action: "prepare_response", ...r });
  if (data.ok !== true) return data as Failure;
  if (!keys(data,"edit,mailboxId,ok,response,sourceHash") || data.mailboxId !== r.mailboxId ||
      typeof data.sourceHash !== "string" || !/^[a-f0-9]{64}$/.test(data.sourceHash) || !validMicrosoftComposeDraftEdit(data.edit) ||
      !validResponse(data.response) || data.response.mode !== r.mode || data.edit.body !== "") return failure();
  return { ok: true, sourceHash: data.sourceHash, edit: data.edit, response: data.response };
}
export async function createMicrosoftResponseDraft(r: Context & { mode: MicrosoftResponseMode; sourceProviderMessageId: string;
  sourceHash: string; requestId: string; edit: MicrosoftComposeDraftEdit
}): Promise<Failure | { ok: true; receipt: MicrosoftComposeDraftReceipt }> {
  if ((!MICROSOFT_COMPOSE_DRAFTS_RELEASED || !MICROSOFT_RESPONSE_DRAFTS_RELEASED) && !r.acceptance) return failure("microsoft_response_disabled");
  if (!context(r) || !id(r.sourceProviderMessageId) || !operation(r.requestId) || !validMicrosoftComposeDraftEdit(r.edit) ||
      !["reply","reply_all","forward"].includes(r.mode) || !/^[a-f0-9]{64}$/.test(r.sourceHash)) return failure("microsoft_response_invalid");
  const data = await post({ action: "create_response", ...r });
  if (data.ok !== true) return data as Failure;
  if (!keys(data,"mailboxId,ok,receipt") || data.mailboxId !== r.mailboxId || !validReceipt(data.receipt) ||
      data.receipt.requestId !== r.requestId || data.receipt.action !== "create") return failure();
  return { ok: true, receipt: data.receipt };
}

export type MicrosoftDraftAcceptance = { canInspect: boolean; canWrite: boolean; mailReadWrite: boolean; tokenExpired: boolean; reason: string };
export async function inspectMicrosoftDraftAcceptance(mailboxId: string): Promise<MicrosoftDraftAcceptance | null> {
  if (!context({mailboxId})) return null;
  const data = await post({action: "capabilities", mailboxId});
  if (data.ok !== true || data.mailboxId !== mailboxId || data.send !== false || data.attachments !== false || data.replyAll !== false ||
      [data.canInspect,data.canWrite,data.mailReadWrite,data.tokenExpired].some(x => typeof x !== "boolean") ||
      data.canWrite && (!data.canInspect || !data.mailReadWrite) || !["not_configured","blocked","ready","consent_required"].includes(String(data.reason))) return null;
  return {canInspect: data.canInspect as boolean, canWrite: data.canWrite as boolean, mailReadWrite: data.mailReadWrite as boolean,
    tokenExpired: data.tokenExpired as boolean, reason: data.reason as string};
}
