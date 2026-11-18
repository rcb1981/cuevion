import type { LiveInboxMessageSnapshot } from "./inboxConnectionApi";

export type ImapSentDiscovery = {
  mode: "ready" | "choose" | "unavailable";
  providerFolder: string | null;
  folders: Array<{ providerFolder: string; displayName: string; suggested: boolean }>;
};
export type ImapSentPage = {
  serverMailboxId: string; providerFolder: string; uidValidity: string; scope: string;
  messages: LiveInboxMessageSnapshot[]; scanLow: number; scanHigh: number; nextPageToken: string | null;
};
export type ImapSentResponse =
  | { ok: true; mailboxId: string; scope: string; discovery: ImapSentDiscovery; page: ImapSentPage | null }
  | { ok: false; error: { code: string; message: string } };
export type ImapSentChoice = { providerFolder: string; scope: string };
const record = (v: unknown): v is Record<string, any> => !!v && typeof v === "object" && !Array.isArray(v);
const exact = (v: Record<string, any>, keys: string[]) => Object.keys(v).length === keys.length && keys.every((k) => k in v);
const text = (v: unknown, max = 16384): v is string => typeof v === "string" && v.length <= max && !/[\u0000-\u001f\u007f-\u009f]/.test(v);
const folderName = (v: unknown): v is string => text(v) && v.length > 0 && v === v.trim();
const uid = (v: unknown): v is string => typeof v === "string" && /^[1-9][0-9]{0,9}$/.test(v) && Number(v) <= 4294967295;
const scopeId = (v: unknown): v is string => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const token = (v: unknown): v is string => typeof v === "string" && v.length <= 32768 && /^[A-Za-z0-9_-]+$/.test(v);
const failure = (code = "imap_sent_unavailable", message = "IMAP Sent could not be loaded. Try again."): ImapSentResponse => ({ ok: false, error: { code, message } });

export function parseImapSentResponse(v: unknown, mailboxId: string, previousToken?: string): ImapSentResponse {
  if (!record(v) || !exact(v, ["ok", "mailboxId", "scope", "discovery", "page"]) || v.ok !== true || v.mailboxId !== mailboxId || !scopeId(v.scope)) return failure();
  const d = v.discovery;
  if (!record(d) || !exact(d, ["mode", "providerFolder", "folders"]) || !["ready", "choose", "unavailable"].includes(d.mode) || !Array.isArray(d.folders) || d.folders.length > 256) return failure();
  const names = new Set<string>();
  for (const f of d.folders) {
    if (!record(f) || !exact(f, ["providerFolder", "displayName", "suggested"]) || !folderName(f.providerFolder) || !text(f.displayName) || typeof f.suggested !== "boolean" || names.has(f.providerFolder)) return failure();
    names.add(f.providerFolder);
  }
  if (d.mode !== "ready") {
    if (v.page !== null || d.providerFolder !== null || (d.mode === "choose") !== (names.size > 0)) return failure();
    return v as ImapSentResponse;
  }
  const p = v.page;
  if (record(p) && previousToken !== undefined && p.nextPageToken === previousToken) return failure("invalid_page_token", "Sent pagination expired. Refresh Sent.");
  if (!names.has(d.providerFolder) || !record(p) || !exact(p, ["serverMailboxId", "providerFolder", "uidValidity", "scope", "messages", "scanLow", "scanHigh", "nextPageToken"]) ||
      p.serverMailboxId !== mailboxId || p.providerFolder !== d.providerFolder || p.scope !== v.scope || !uid(p.uidValidity) ||
      !Number.isInteger(p.scanLow) || !Number.isInteger(p.scanHigh) || p.scanLow < 1 || p.scanHigh < 0 || p.scanHigh > 4294967295 || p.scanLow > p.scanHigh + 1 ||
      !(p.nextPageToken === null || token(p.nextPageToken)) || !Array.isArray(p.messages) || p.messages.length > 20) return failure();
  const ids = new Set<string>();
  const fields = new Set(["id", "serverMailboxId", "providerFolder", "uidValidity", "imapUid", "sender", "from", "to", "cc", "subject", "snippet", "body", "bodyHtml", "createdAt", "timestamp", "unread", "flagged", "attachments", "rfcMessageId", "threadId"]);
  for (const m of p.messages) {
    if (!record(m) || Object.keys(m).some((k) => !fields.has(k)) || m.serverMailboxId !== mailboxId || m.providerFolder !== p.providerFolder || m.uidValidity !== p.uidValidity || !uid(m.imapUid) || m.id !== m.imapUid || ids.has(m.imapUid) ||
        Number(m.imapUid) < p.scanLow || Number(m.imapUid) > p.scanHigh ||
        !["sender", "from", "to", "cc", "subject", "snippet", "createdAt", "timestamp", "threadId"].every((k) => text(m[k])) || !m.threadId.startsWith("imap:") ||
        !Number.isFinite(Date.parse(m.createdAt)) || !Number.isFinite(Date.parse(m.timestamp)) || typeof m.unread !== "boolean" || typeof m.flagged !== "boolean" ||
        !Array.isArray(m.body) || m.body.length > 100000 || !m.body.every((s: unknown) => typeof s === "string") ||
        (m.bodyHtml !== undefined && typeof m.bodyHtml !== "string") || (m.rfcMessageId !== undefined && !text(m.rfcMessageId, 4096)) ||
        !Array.isArray(m.attachments) || m.attachments.length > 256) return failure();
    for (const a of m.attachments) {
      if (!record(a) || Object.keys(a).some((k) => !["id", "name", "mimeType", "size", "contentId", "disposition", "inlineSrc"].includes(k)) ||
          !text(a.id, 512) || !text(a.name) || (a.size !== undefined && (!Number.isSafeInteger(a.size) || a.size < 0)) ||
          ["mimeType", "contentId", "disposition"].some((k) => a[k] !== undefined && !text(a[k])) ||
          (a.inlineSrc !== undefined && (typeof a.inlineSrc !== "string" || !/^data:image\/[a-z0-9.+-]+;base64,[A-Za-z0-9+/]*={0,2}$/i.test(a.inlineSrc)))) return failure();
    }
    ids.add(m.imapUid);
  }
  return v as ImapSentResponse;
}

export async function fetchImapSent(mailboxId: string, pageToken?: string, folderChoice?: ImapSentChoice, signal?: AbortSignal): Promise<ImapSentResponse> {
  if (!folderName(mailboxId) || (pageToken !== undefined && !token(pageToken)) || (folderChoice !== undefined && (!folderName(folderChoice.providerFolder) || !scopeId(folderChoice.scope)))) return failure("invalid_request", "Invalid Sent page parameters.");
  try {
    const response = await fetch("/api/inboxes/fetch-imap-sent", { method: "POST", credentials: "include", cache: "no-store", signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mailboxId, limit: 20, ...(pageToken ? { pageToken } : {}), ...(folderChoice ? { folderChoice } : {}) }) });
    if (response.status === 401) {
      try { window.dispatchEvent(new Event("cuevion:auth-session-recheck")); } catch { /* SSR or revoked session */ }
      return failure("reconnect_required", "Sign in or reconnect this mailbox to load Sent.");
    }
    if (response.status === 403 || response.status === 404) return failure("mailbox_connection_changed", "This mailbox is no longer available. Reconnect it to load Sent.");
    const reader = response.body?.getReader();
    if (!reader) return failure();
    let size = 0, raw = "";
    const decoder = new TextDecoder();
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 10 * 1024 * 1024) { await reader.cancel(); return failure(); }
      raw += decoder.decode(value, { stream: true });
    }
    raw += decoder.decode();
    const value: unknown = JSON.parse(raw);
    if (response.status !== 200) {
      if (response.status === 409) return failure(record(value) && record(value.error) && ["sent_folder_changed", "uid_validity_changed", "invalid_page_token", "mailbox_connection_changed"].includes(value.error.code) ? value.error.code : "mailbox_connection_changed", "Sent changed. Refresh Sent or choose its folder again.");
      return failure();
    }
    return parseImapSentResponse(value, mailboxId, pageToken);
  } catch { return failure(); }
}
