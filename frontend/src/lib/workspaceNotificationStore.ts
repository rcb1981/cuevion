import { createNotificationsApi, notificationScopeKey, validNotificationScope, type NotificationScope, type NotificationsApi, type ServerNotification } from "./notificationsApi";

export type NotificationStoreState = Readonly<{
  scope: NotificationScope | null; generation: number; unreadCount: number;
  byId: ReadonlyMap<string, ServerNotification>; orderedIds: readonly string[]; nextCursor: string | null;
  summaryLoading: boolean; summaryError: boolean; listLoading: boolean; listError: boolean;
  loadMoreLoading: boolean; loadMoreError: boolean; markReadIds: ReadonlySet<string>;
  firstPageLoaded: boolean; lastFirstPageRefreshAt: number | null;
}>;
const empty = (scope: NotificationScope | null, generation: number): NotificationStoreState => ({
  scope, generation, unreadCount: 0, byId: new Map(), orderedIds: [], nextCursor: null,
  summaryLoading: false, summaryError: false, listLoading: false, listError: false,
  loadMoreLoading: false, loadMoreError: false, markReadIds: new Set(), firstPageLoaded: false, lastFirstPageRefreshAt: null,
});
export function createWorkspaceNotificationStore(initialScope: NotificationScope | null, api: NotificationsApi = createNotificationsApi(), now = Date.now) {
  let state = empty(validNotificationScope(initialScope) ? initialScope : null, 0);
  let abort = new AbortController();
  let summaryStarted = false;
  let summaryPromise: Promise<void> | null = null;
  let firstPromise: Promise<void> | null = null;
  let morePromise: Promise<void> | null = null;
  let marks = new Map<string, Promise<boolean>>();
  let markTail = Promise.resolve();
  let consumedCursors = new Set<string>();
  let sequence = 0, publishedCountSequence = 0, readRevision = 0;
  const listeners = new Set<() => void>();
  const publish = (patch: Partial<NotificationStoreState>) => { state = { ...state, ...patch }; listeners.forEach(listener => listener()); };
  const current = (generation: number) => state.generation === generation && !abort.signal.aborted && !!state.scope;
  function summary(retry = false): Promise<void> {
    if (summaryPromise) return summaryPromise;
    if (!state.scope || abort.signal.aborted || (summaryStarted && !retry)) return Promise.resolve();
    summaryStarted = true;
    const generation = state.generation, requestSequence = ++sequence, revision = readRevision;
    publish({ summaryLoading: true, summaryError: false });
    summaryPromise = (async () => {
      const result = await api.summary(abort.signal);
      if (!current(generation)) return;
      if (result.status === "success") {
        const count = requestSequence >= publishedCountSequence && revision === readRevision;
        if (count) publishedCountSequence = requestSequence;
        publish({ summaryLoading: false, ...(count ? { unreadCount: result.value.unreadCount } : {}) });
      } else publish({ summaryLoading: false, summaryError: true });
    })().finally(() => { if (current(generation)) summaryPromise = null; });
    return summaryPromise;
  }
  function page(more: boolean): Promise<void> {
    if (firstPromise) return firstPromise;
    if (morePromise) return morePromise;
    if (!state.scope || abort.signal.aborted || (more && state.nextCursor === null)) return Promise.resolve();
    const generation = state.generation, scope = state.scope, cursor = more ? state.nextCursor : null;
    const requestSequence = ++sequence, revision = readRevision;
    publish(more ? { loadMoreLoading: true, loadMoreError: false } : { listLoading: true, listError: false });
    const promise = (async () => {
      const result = await api.list(scope.workspaceId, cursor, abort.signal);
      if (!current(generation)) return;
      const fail = () => publish(more ? { loadMoreLoading: false, loadMoreError: true } : { listLoading: false, listError: true });
      if (result.status !== "success") { fail(); return; }
      const { notifications, nextCursor, unreadCount } = result.value;
      if (more && (nextCursor !== null && (consumedCursors.has(nextCursor) || nextCursor === cursor || notifications.every(row => state.byId.has(row.notificationId))))) { fail(); return; }
      const byId = new Map(more ? state.byId : []);
      for (const row of notifications) {
        const previous = state.byId.get(row.notificationId);
        // A list dispatched before a successful read cannot resurrect unread state.
        byId.set(row.notificationId, revision !== readRevision && previous?.readAt !== null && previous?.readAt !== undefined ? previous : row);
      }
      if (!more) consumedCursors = new Set();
      if (cursor !== null) consumedCursors.add(cursor);
      const orderedIds = [...byId.values()].sort((a, b) => b.createdAt - a.createdAt || b.notificationId.localeCompare(a.notificationId)).map(row => row.notificationId);
      const count = requestSequence >= publishedCountSequence && revision === readRevision;
      if (count) publishedCountSequence = requestSequence;
      publish({ byId, orderedIds, nextCursor, ...(count ? { unreadCount } : {}), summaryError: false,
        listLoading: false, loadMoreLoading: false, loadMoreError: false, firstPageLoaded: true,
        ...(!more ? { lastFirstPageRefreshAt: now() } : {}),
      });
    })().finally(() => { if (current(generation)) { if (more) morePromise = null; else firstPromise = null; } });
    if (more) morePromise = promise; else firstPromise = promise;
    return promise;
  }
  function markRead(id: string, isCurrent: () => boolean = () => true, signal?: AbortSignal): Promise<boolean> {
    const existing = marks.get(id);
    if (existing) return existing;
    const row = state.byId.get(id), scope = state.scope, generation = state.generation;
    if (!scope || !row || !current(generation) || !isCurrent() || signal?.aborted) return Promise.resolve(false);
    if (row.readAt !== null) return Promise.resolve(true);
    publish({ markReadIds: new Set([...state.markReadIds, id]) });
    const promise = markTail.then(async () => {
      if (!current(generation) || !isCurrent() || signal?.aborted) return false;
      const controller = new AbortController();
      const cancel = () => controller.abort();
      const scopeSignal = abort.signal;
      scopeSignal.addEventListener("abort", cancel, { once: true });
      signal?.addEventListener("abort", cancel, { once: true });
      const requestSequence = ++sequence;
      try {
        const result = await api.markRead(scope.workspaceId, id, controller.signal);
        if (!current(generation) || controller.signal.aborted || result.status !== "success") return false;
        readRevision++;
        publishedCountSequence = requestSequence;
        const byId = new Map(state.byId).set(id, result.value.notification);
        publish({ byId, unreadCount: result.value.unreadCount });
        return true;
      } finally {
        scopeSignal.removeEventListener("abort", cancel);
        signal?.removeEventListener("abort", cancel);
      }
    }).finally(() => {
      if (current(generation)) {
        marks.delete(id);
        const ids = new Set(state.markReadIds); ids.delete(id); publish({ markReadIds: ids });
      }
    });
    marks.set(id, promise);
    markTail = promise.then(() => undefined, () => undefined);
    return promise;
  }
  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    start: () => summary(), retrySummary: () => summary(true),
    ensureFirstPage: (consumer: "dashboard" | "workbench") => {
      if (!state.firstPageLoaded || (consumer === "workbench" && state.lastFirstPageRefreshAt !== null && now() - state.lastFirstPageRefreshAt >= 60000)) return page(false);
      return Promise.resolve();
    },
    refresh: () => page(false), loadMore: () => page(true), markRead,
    cancel: () => abort.abort(),
    setScope: (scope: NotificationScope | null) => {
      if (notificationScopeKey(scope) === notificationScopeKey(state.scope)) return;
      abort.abort(); abort = new AbortController(); summaryStarted = false;
      summaryPromise = firstPromise = morePromise = null; marks = new Map(); markTail = Promise.resolve();
      consumedCursors = new Set(); sequence = publishedCountSequence = readRevision = 0;
      state = empty(validNotificationScope(scope) ? scope : null, state.generation + 1);
      listeners.forEach(listener => listener());
      void summary();
    },
  };
}
export type WorkspaceNotificationStore = ReturnType<typeof createWorkspaceNotificationStore>;
