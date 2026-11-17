import { fetchGmailSent, type GmailSentResponse, type LiveInboxMessageSnapshot } from "./inboxConnectionApi";

type SentIdentity = {
  id: string;
  providerMessageId?: string;
  serverMailboxId?: string;
  threadIdentityContext?: { mailboxId?: string; provider?: string };
};

// Pure display projection: never mutates or writes the shared mailbox store.
export function mergeGmailSentDisplay<T extends SentIdentity>(
  mailboxId: string, provider: readonly T[], local: readonly T[], observed: ReadonlySet<string>,
): T[] {
  const byId = new Map<string, T>();
  for (const message of provider) {
    if (message.serverMailboxId === mailboxId && message.providerMessageId) byId.set(message.providerMessageId, message);
  }
  const result = [...byId.values()];
  for (const message of local) {
    if ((message.serverMailboxId && message.serverMailboxId !== mailboxId) ||
        (message.threadIdentityContext?.mailboxId && message.threadIdentityContext.mailboxId !== mailboxId) ||
        (message.threadIdentityContext?.provider && message.threadIdentityContext.provider !== "google")) continue;
    if (message.providerMessageId) {
      if (byId.has(message.providerMessageId) || observed.has(message.providerMessageId)) continue;
      byId.set(message.providerMessageId, message);
    }
    // Identity-less historical copies are preserved, never guessed or overwritten.
    result.push(message);
  }
  return result;
}

export type GmailSentState = {
  messages: LiveInboxMessageSnapshot[];
  nextPageToken: string | null;
  observed: ReadonlySet<string>;
  loading: boolean;
  loaded: boolean;
  error: string | null;
};
type FetchPage = (mailboxId: string, token?: string, signal?: AbortSignal) => Promise<GmailSentResponse>;

export function createGmailSentController(
  mailboxId: string, isActive: () => boolean, fetchPage: FetchPage = fetchGmailSent,
) {
  let state: GmailSentState = { messages: [], nextPageToken: null, observed: new Set(), loading: false, loaded: false, error: null };
  let generation = 0;
  let abort: AbortController | null = null;
  const listeners = new Set<() => void>();
  let consumedTokens = new Set<string>();
  const publish = (next: GmailSentState) => { state = next; listeners.forEach((listener) => listener()); };
  const cancel = () => { generation += 1; abort?.abort(); abort = null; };
  const read = async (more = false) => {
    if (!isActive() || (more && (state.loading || !state.nextPageToken))) return;
    cancel();
    const requestGeneration = generation;
    const controller = new AbortController();
    abort = controller;
    const token = more ? state.nextPageToken! : undefined;
    publish({ ...state, loading: true, error: null });
    try {
      const response = await fetchPage(mailboxId, token, controller.signal);
      if (!isActive() || controller.signal.aborted || requestGeneration !== generation) return;
      if (!response.ok) {
        publish({ ...state, loading: false, error: response.error.message });
        return;
      }
      const tokens = more ? new Set(consumedTokens) : new Set<string>();
      if (token) tokens.add(token);
      if (response.page.nextPageToken && tokens.has(response.page.nextPageToken)) {
        publish({ ...state, loading: false, error: "Sent pagination expired. Refresh Sent to continue." });
        return;
      }
      const rows = new Map((more ? state.messages : []).map((message) => [message.providerMessageId, message]));
      const observed = new Set(state.observed);
      response.page.messages.forEach((message) => { rows.set(message.providerMessageId, message); observed.add(message.providerMessageId!); });
      consumedTokens = tokens;
      publish({ messages: [...rows.values()], nextPageToken: response.page.nextPageToken, observed, loading: false, loaded: true, error: null });
    } catch {
      if (isActive() && !controller.signal.aborted && requestGeneration === generation) {
        publish({ ...state, loading: false, error: "Sent could not be refreshed from Gmail. Try again." });
      }
    }
  };
  return { getSnapshot: () => state, subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    refresh: () => read(), loadMore: () => read(true), cancel };
}
