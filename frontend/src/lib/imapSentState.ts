import { fetchImapSent, type ImapSentChoice, type ImapSentDiscovery, type ImapSentResponse } from "./imapSentApi";
import type { LiveInboxMessageSnapshot } from "./inboxConnectionApi";

type Identity = { id: string; serverMailboxId?: string; providerFolder?: string; uidValidity?: string; imapUid?: string; threadIdentityContext?: { mailboxId?: string; provider?: string } };
// Raw rows live inside one controller scope; display IDs additionally carry it.
export const imapSentIdentity = (m: Identity) => JSON.stringify([m.serverMailboxId, m.id.startsWith("imap-sent:") ? m.id : null, m.providerFolder, m.uidValidity, m.imapUid]);
export function mergeImapSentDisplay<T extends Identity>(mailboxId: string, provider: readonly T[], local: readonly T[]): T[] {
  const owned = (m: T) => (!m.serverMailboxId || m.serverMailboxId === mailboxId) &&
    (!m.threadIdentityContext?.mailboxId || m.threadIdentityContext.mailboxId === mailboxId) &&
    (!m.threadIdentityContext?.provider || m.threadIdentityContext.provider === "custom_imap");
  const rows = new Map<string, T>();
  provider.filter(owned).forEach((m) => { if (m.serverMailboxId === mailboxId && m.providerFolder && m.uidValidity && m.imapUid) rows.set(imapSentIdentity(m), m); });
  const providerIds = new Set([...rows.values()].map((m) => m.id));
  // Legacy SMTP records have no provable provider correlation. Preserve them all.
  return [...rows.values(), ...local.filter(owned).filter((m) => !m.id.startsWith("imap-sent:") || !providerIds.has(m.id))];
}
export type ImapSentState = { messages: LiveInboxMessageSnapshot[]; scope: string | null; folder: string | null; uidValidity: string | null;
  discovery: ImapSentDiscovery | null; nextPageToken: string | null; loading: boolean; loaded: boolean; error: string | null };
const empty = (): ImapSentState => ({ messages: [], scope: null, folder: null, uidValidity: null, discovery: null, nextPageToken: null, loading: false, loaded: false, error: null });
export function createImapSentController(mailboxId: string, isActive: () => boolean, fetchPage = fetchImapSent) {
  let state = empty(), generation = 0, abort: AbortController | null = null;
  let choice: ImapSentChoice | undefined;
  let consumed = new Set<string>();
  const listeners = new Set<() => void>();
  const publish = (next: ImapSentState) => { state = next; listeners.forEach((f) => f()); };
  const cancel = () => { generation++; abort?.abort(); abort = null; };
  const reset = () => { cancel(); choice = undefined; consumed = new Set(); publish(empty()); };
  const read = async (more = false) => {
    if (!isActive() || (more && (state.loading || !state.nextPageToken))) return;
    const token = more ? state.nextPageToken! : undefined;
    if (token && consumed.has(token)) { publish({ ...state, error: "Sent pagination expired. Refresh Sent.", nextPageToken: null }); return; }
    cancel(); const requestGeneration = generation;
    const controller = new AbortController(); abort = controller;
    publish({ ...state, loading: true, error: null });
    try {
      const result: ImapSentResponse = await fetchPage(mailboxId, token, choice, controller.signal);
      if (!isActive() || controller.signal.aborted || requestGeneration !== generation) return;
      if (!result.ok) {
        if (["reconnect_required", "mailbox_connection_changed", "uid_validity_changed", "sent_folder_changed", "invalid_page_token"].includes(result.error.code)) {
          choice = undefined; consumed.clear(); publish({ ...empty(), error: result.error.message });
        } else publish({ ...state, loading: false, error: result.error.message });
        return;
      }
      const p = result.page;
      if (p) choice = { providerFolder: p.providerFolder, scope: result.scope };
      const same = state.scope === result.scope && state.folder === p?.providerFolder && state.uidValidity === p?.uidValidity;
      if (!more || !same) consumed = new Set();
      if (token) consumed.add(token);
      if (p?.nextPageToken && consumed.has(p.nextPageToken)) { publish({ ...state, loading: false, nextPageToken: null, error: "Sent pagination expired. Refresh Sent." }); return; }
      // A refresh only proves absence in the scanned range, not in older loaded pages.
      const previous = same && p ? state.messages.filter((m) => Number(m.imapUid) < p.scanLow || Number(m.imapUid) > p.scanHigh) : [];
      const rows = new Map(previous.map((m) => [imapSentIdentity(m), m]));
      p?.messages.forEach((m) => rows.set(imapSentIdentity(m), m));
      publish({ messages: [...rows.values()].sort((a, b) => Date.parse(b.createdAt!) - Date.parse(a.createdAt!) || Number(b.imapUid) - Number(a.imapUid)),
        scope: result.scope, folder: p?.providerFolder ?? null, uidValidity: p?.uidValidity ?? null,
        discovery: result.discovery, nextPageToken: p?.nextPageToken ?? null, loading: false, loaded: true, error: result.preferenceSaved === false ? "Sent loaded, but your folder choice could not be saved. Try refreshing to save it." : null });
    } catch {
      if (isActive() && !controller.signal.aborted && requestGeneration === generation) publish({ ...state, loading: false, error: "IMAP Sent could not be loaded. Try again." });
    }
  };
  return { getSnapshot: () => state, subscribe: (f: () => void) => { listeners.add(f); return () => { listeners.delete(f); }; },
    refresh: () => read(), loadMore: () => read(true), cancel, reset,
    choose: (folder: string) => {
      if (!state.scope || !state.discovery?.folders.some((f) => f.providerFolder === folder)) return Promise.resolve();
      const selected = { providerFolder: folder, scope: state.scope };
      reset(); choice = selected; return read();
    } };
}
