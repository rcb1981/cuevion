/** Source-only release gate. No environment/browser setting can enable sending. */
export const MICROSOFT_DRAFT_SEND_RELEASED = false;
export type MicrosoftDraftSendReceipt = {
  attemptId: string; providerMessageId: string;
  outcome: "confirmed_performed" | "performed_content_mismatch" | "confirmed_not_started" | "uncertain";
  providerAccepted: boolean; sentCopyConfirmed: boolean; deliveryConfirmed: false; reason: string | null;
};
type Context = { mailboxId: string };
type Failure = { ok: false; outcome: "uncertain"; error: { code: string; message: string } };
const object = (x: unknown): x is Record<string, unknown> => Boolean(x) && typeof x === "object" && !Array.isArray(x);
const keys = (v: object, want: string) => Object.keys(v).sort().join(",") === want;
const operation = (v: unknown): v is string => typeof v === "string" && /^[A-Za-z0-9_-]{43}$/.test(v);
const message = (v: unknown): v is string => typeof v === "string" && /^[^\x00-\x20\x7f-\x9f?#]{1,512}$/.test(v) && v !== "." && !v.includes("..") && new TextEncoder().encode(v).length <= 512;
const context = (v: Context) => /^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$/.test(v.mailboxId);
const messages: Record<string, string> = {
  microsoft_compose_body_unverifiable: "The original message body cannot be verified as plain text. HTML projections, unconfirmed preferences and unsupported MIME remain protected; saving or sending is blocked.",
  microsoft_draft_send_disabled: "Sending existing Microsoft drafts is not enabled.",
  microsoft_draft_send_sender_unsupported: "Only the verified own Microsoft mailbox may send. Review From, Sender and Reply-To.",
  microsoft_draft_send_unverifiable: "The complete draft content or attachment bytes cannot be verified. Sending was blocked.",
  microsoft_draft_send_invalid: "Save valid recipients, a subject and message before sending.",
  microsoft_draft_send_conflict: "The saved draft changed. Reopen and review it before a new send request.",
  microsoft_draft_send_blocked: "An earlier send, draft or attachment operation requires verification. Do not send again.",
  microsoft_draft_send_busy: "Another mailbox action is in progress. Check status before continuing.",
  microsoft_draft_send_unavailable: "The send result could not be verified. Check status; do not send again.",
  microsoft_send_permission_required: "Reconnect Microsoft with Mail.Send authorization before sending.",
  microsoft_write_permission_required: "Reconnect Microsoft to allow draft access.",
  microsoft_reconnect_required: "Reconnect this Microsoft mailbox before continuing.",
  microsoft_unauthorized: "Sign in again before continuing.",
};
const reasons = new Set(["microsoft_compose_body_unverifiable", "microsoft_draft_send_sender_unsupported", "microsoft_draft_send_unverifiable", "microsoft_draft_send_invalid", "microsoft_draft_send_conflict", "microsoft_draft_send_blocked", "microsoft_reconnect_required", "microsoft_send_permission_required", "microsoft_write_permission_required", "microsoft_unauthorized", "microsoft_draft_send_unavailable"]);
export function microsoftDraftSendReason(code: string | null): string | null {
  return code && Object.prototype.hasOwnProperty.call(messages, code) ? messages[code] : null;
}
function failure(code = "microsoft_draft_send_unavailable"): Failure {
  const safe = Object.prototype.hasOwnProperty.call(messages, code) ? code : "microsoft_draft_send_unavailable";
  return { ok: false, outcome: "uncertain", error: { code: safe, message: messages[safe] } };
}
function receipt(v: unknown): v is MicrosoftDraftSendReceipt {
  return object(v) && keys(v, "attemptId,deliveryConfirmed,outcome,providerAccepted,providerMessageId,reason,sentCopyConfirmed") &&
    operation(v.attemptId) && message(v.providerMessageId) && ["confirmed_performed", "performed_content_mismatch", "confirmed_not_started", "uncertain"].includes(String(v.outcome)) &&
    typeof v.providerAccepted === "boolean" && v.deliveryConfirmed === false && typeof v.sentCopyConfirmed === "boolean" && (!v.sentCopyConfirmed || v.providerAccepted === true) &&
    (!["confirmed_performed", "performed_content_mismatch"].includes(String(v.outcome)) || v.sentCopyConfirmed === true) && (v.outcome !== "confirmed_not_started" || v.providerAccepted === false) &&
    (v.reason === null || v.outcome === "confirmed_not_started" && typeof v.reason === "string" && reasons.has(v.reason));
}
async function post(body: object): Promise<Record<string, unknown>> {
  try {
    const r = await fetch("/api/inboxes/send-microsoft-draft", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 401 && typeof window !== "undefined") {
      try { window.dispatchEvent(new Event("cuevion:auth-session-recheck")); } catch { /* Preserve uncertainty. */ }
    }
    const data: unknown = await r.json();
    if (!object(data)) return failure();
    if (!r.ok || data.ok !== true) return failure(object(data.error) && typeof data.error.code === "string" ? data.error.code : undefined);
    return data;
  } catch { return failure(); } // Never retry send or invent a new attempt ID.
}
export async function inspectMicrosoftDraftSends(r: Context): Promise<Failure | { ok: true; attempts: MicrosoftDraftSendReceipt[] }> {
  if (!context(r)) return failure("microsoft_draft_send_invalid");
  const data = await post({ action: "status", mailboxId: r.mailboxId });
  if (data.ok !== true) return data as Failure;
  if (!keys(data, "attempts,mailboxId,ok") || data.mailboxId !== r.mailboxId || !Array.isArray(data.attempts) || data.attempts.length > 32 ||
      !data.attempts.every(receipt) || new Set(data.attempts.map(x => x.attemptId)).size !== data.attempts.length) return failure();
  return { ok: true, attempts: data.attempts };
}
export async function sendMicrosoftExistingDraft(r: Context & { attemptId: string; providerMessageId: string; etag: string; snapshotHash: string }): Promise<Failure | { ok: true; receipt: MicrosoftDraftSendReceipt }> {
  if (!MICROSOFT_DRAFT_SEND_RELEASED) return failure("microsoft_draft_send_disabled");
  if (!context(r) || !operation(r.attemptId) || !message(r.providerMessageId) || !/^(?:W\/)?"[\x21\x23-\x7e]{1,256}"$/.test(r.etag) || !/^[a-f0-9]{64}$/.test(r.snapshotHash)) return failure("microsoft_draft_send_invalid");
  const data = await post({ action: "send", mailboxId: r.mailboxId, attemptId: r.attemptId, providerMessageId: r.providerMessageId, etag: r.etag, snapshotHash: r.snapshotHash });
  if (data.ok !== true) return data as Failure;
  if (!keys(data, "mailboxId,ok,receipt") || data.mailboxId !== r.mailboxId || !receipt(data.receipt) || data.receipt.attemptId !== r.attemptId || data.receipt.providerMessageId !== r.providerMessageId) return failure();
  return { ok: true, receipt: data.receipt };
}
export async function checkMicrosoftDraftSend(r: Context & { attemptId: string }): Promise<Failure | { ok: true; receipt: MicrosoftDraftSendReceipt }> {
  if (!context(r) || !operation(r.attemptId)) return failure("microsoft_draft_send_invalid");
  const data = await post({ action: "check", mailboxId: r.mailboxId, attemptId: r.attemptId });
  if (data.ok !== true) return data as Failure;
  if (!keys(data, "mailboxId,ok,receipt") || data.mailboxId !== r.mailboxId || !receipt(data.receipt) || data.receipt.attemptId !== r.attemptId) return failure();
  return { ok: true, receipt: data.receipt };
}


export type MicrosoftSendHistoryEntry = { receipt: MicrosoftDraftSendReceipt; sequence: number; createdAt: string | null };
export type MicrosoftSendHistoryPage = { ok: true; entries: MicrosoftSendHistoryEntry[]; nextCursor: number | null; currentDraft: MicrosoftSendHistoryEntry | null };
const sequence = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;
function historyEntry(value: unknown): value is MicrosoftSendHistoryEntry {
  return object(value) && keys(value, "createdAt,receipt,sequence") && receipt(value.receipt) && sequence(value.sequence) &&
    (value.createdAt === null || typeof value.createdAt === "string" && value.createdAt.length <= 64 &&
      /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value.createdAt) && Number.isFinite(Date.parse(value.createdAt)));
}
export async function listMicrosoftDraftSendHistory(r: Context & { before?: number | null; providerMessageId: string | null }): Promise<Failure | MicrosoftSendHistoryPage> {
  if (!context(r) || r.before != null && !sequence(r.before) || r.providerMessageId !== null && !message(r.providerMessageId)) return failure("microsoft_draft_send_invalid");
  const data = await post({ action: "history", mailboxId: r.mailboxId, before: r.before ?? null, providerMessageId: r.providerMessageId });
  if (data.ok !== true) return data as Failure;
  if (!keys(data, "currentDraft,entries,mailboxId,nextCursor,ok") || data.mailboxId !== r.mailboxId || !Array.isArray(data.entries) ||
      data.entries.length > 32 || !data.entries.every(historyEntry) || new Set(data.entries.map(x => x.receipt.attemptId)).size !== data.entries.length ||
      data.entries.some((x, i, all) => i > 0 && all[i - 1].sequence - x.sequence !== 1) ||
      data.entries.length > 0 && r.before != null && data.entries[0].sequence !== r.before - 1 ||
      data.nextCursor !== null && (!sequence(data.nextCursor) || data.nextCursor <= 1 || !data.entries.length || data.nextCursor !== data.entries[data.entries.length - 1]?.sequence) ||
      data.currentDraft !== null && (!historyEntry(data.currentDraft) || data.currentDraft.receipt.providerMessageId !== r.providerMessageId)) return failure();
  const last = data.entries[data.entries.length - 1]?.sequence;
  const expectedNext = last !== undefined && last > 1 ? last : null;
  if (data.nextCursor !== expectedNext || expectedNext !== null && data.entries.length !== 32 ||
      data.entries.length === 0 && r.before != null && r.before !== 1) return failure();
  return { ok: true, entries: data.entries, nextCursor: data.nextCursor as number | null, currentDraft: data.currentDraft as MicrosoftSendHistoryEntry | null };
}
