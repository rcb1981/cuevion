import { normalizeMicrosoftRecipientList } from "./microsoftComposeRequest";
import { newMicrosoftAttachmentRequestId } from "./microsoftDraftAttachmentApi";

/** Independent source gate. No browser/env setting enables Compose drafts. */
export const MICROSOFT_COMPOSE_DRAFTS_RELEASED = false;
export const createMicrosoftComposeDraftRequestId = newMicrosoftAttachmentRequestId;
export type MicrosoftComposeDraftEdit = { subject: string; body: string; to: string[]; cc: string[]; bcc: string[] };
export type MicrosoftComposeDraftReceipt = {
  requestId: string; action: "create" | "update"; providerMessageId: string | null;
  outcome: "confirmed_performed" | "confirmed_not_performed" | "uncertain"; blocked: boolean;
};
export type MicrosoftComposeDraftRecord = { providerMessageId: string; etag: string; snapshotHash: string; edit: MicrosoftComposeDraftEdit };
type Failure = { ok: false; blocked: true; error: { code: string; message: string } };
type Context = { mailboxId: string };
const id = (s: unknown): s is string => typeof s === "string" && /^[^\x00-\x20\x7f-\x9f?#]{1,512}$/.test(s) && s !== "." && !s.includes("..") && new TextEncoder().encode(s).length <= 512;
const operation = (s: unknown): s is string => typeof s === "string" && /^[A-Za-z0-9_-]{43}$/.test(s);
const etag = (s: unknown): s is string => typeof s === "string" && /^(?:W\/)?"[\x21\x23-\x7e]{1,256}"$/.test(s);
const context = (r: Context) => /^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$/.test(r.mailboxId);
const keys = (v: object, expected: string) => Object.keys(v).sort().join(",") === expected;
const object = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const messages: Record<string, string> = {
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
  if (!object(value) || !keys(value, "bcc,body,cc,subject,to") || typeof value.subject !== "string" ||
      value.subject.length > 250 || /[\x00-\x1f\x7f]/.test(value.subject) || typeof value.body !== "string" ||
      value.body.length > 32000 || value.body.includes("\0") || new TextEncoder().encode(value.body).length > 128000) return false;
  const all: string[] = [];
  for (const field of [value.to, value.cc, value.bcc]) {
    if (!Array.isArray(field) || field.some(s => typeof s !== "string" || s.length > 320)) return false;
    const parsed = normalizeMicrosoftRecipientList(field.join(","));
    if (!parsed || JSON.stringify(parsed) !== JSON.stringify(field)) return false;
    all.push(...field);
  }
  return all.length <= 20 && new Set(all).size === all.length;
}
export function prepareMicrosoftComposeDraftEdit(subject: string, body: string, to: string, cc: string, bcc: string): MicrosoftComposeDraftEdit | null {
  const candidate = { subject, body, to: normalizeMicrosoftRecipientList(to), cc: normalizeMicrosoftRecipientList(cc), bcc: normalizeMicrosoftRecipientList(bcc) };
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
    const response = await fetch("/api/inboxes/microsoft-compose-draft", { method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (response.status === 401 && typeof window !== "undefined") {
      try { window.dispatchEvent(new Event("cuevion:auth-session-recheck")); } catch { /* fail closed */ }
    }
    const data: unknown = await response.json();
    if (!object(data)) return failure();
    if (!response.ok || data.ok !== true) return failure(object(data.error) && typeof data.error.code === "string" ? data.error.code : undefined);
    return data;
  } catch { return failure(); } // No retries, redirects or automatic mutations.
}
export async function inspectMicrosoftComposeDrafts(r: Context): Promise<Failure | { ok: true; operations: MicrosoftComposeDraftReceipt[] }> {
  if (!MICROSOFT_COMPOSE_DRAFTS_RELEASED) return failure("microsoft_compose_disabled");
  if (!context(r)) return failure("microsoft_compose_invalid");
  const data = await post({ action: "status", mailboxId: r.mailboxId });
  if (data.ok !== true) return data as Failure;
  if (!keys(data, "mailboxId,ok,operations") || data.mailboxId !== r.mailboxId || !Array.isArray(data.operations) ||
      data.operations.length > 32 || !data.operations.every(validReceipt) || new Set(data.operations.map(v => v.requestId)).size !== data.operations.length) return failure();
  return { ok: true, operations: data.operations };
}
export async function loadMicrosoftComposeDraft(r: Context & { providerMessageId: string }): Promise<Failure | { ok: true; draft: MicrosoftComposeDraftRecord }> {
  if (!MICROSOFT_COMPOSE_DRAFTS_RELEASED) return failure("microsoft_compose_disabled");
  if (!context(r) || !id(r.providerMessageId)) return failure("microsoft_compose_invalid");
  const data = await post({ action: "load", mailboxId: r.mailboxId, providerMessageId: r.providerMessageId });
  if (data.ok !== true) return data as Failure;
  if (!keys(data, "edit,etag,mailboxId,ok,providerMessageId,snapshotHash") || data.mailboxId !== r.mailboxId ||
      data.providerMessageId !== r.providerMessageId || !etag(data.etag) || !validMicrosoftComposeDraftEdit(data.edit) ||
      typeof data.snapshotHash !== "string" || !/^[a-f0-9]{64}$/.test(data.snapshotHash)) return failure();
  return { ok: true, draft: { providerMessageId: r.providerMessageId, etag: data.etag, snapshotHash: data.snapshotHash, edit: data.edit } };
}
export async function saveMicrosoftComposeDraft(r: Context & { requestId: string; edit: MicrosoftComposeDraftEdit } & (
  { action: "create" } | { action: "update"; providerMessageId: string; etag: string }
)): Promise<Failure | { ok: true; receipt: MicrosoftComposeDraftReceipt }> {
  if (!MICROSOFT_COMPOSE_DRAFTS_RELEASED) return failure("microsoft_compose_disabled");
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
