import { fetchGmailDrafts, type GmailDraftSummary, type GmailDraftDetail } from "./gmailDraftsApi";
export type GmailDraftsState = {
  drafts: GmailDraftSummary[]; scope: string | null; nextPageToken: string | null;
  loading: boolean; loaded: boolean; error: string | null;
  selectedId: string | null; detail: GmailDraftDetail | null; detailLoading: boolean; detailError: string | null;
};
const EMPTY: GmailDraftsState = { drafts: [], scope: null, nextPageToken: null, loading: false, loaded: false,
  error: null, selectedId: null, detail: null, detailLoading: false, detailError: null };
export function createGmailDraftsController(mailboxId: string, isActive: () => boolean, fetchPage = fetchGmailDrafts) {
  let state = EMPTY; let listRevision = 0; let detailRevision = 0;
  let listAbort: AbortController | null = null; let detailAbort: AbortController | null = null;
  let consumed = new Set<string>();
  const listeners = new Set<() => void>();
  const publish = (next: GmailDraftsState) => { state = next; listeners.forEach(listener => listener()); };
  const cancelDetail = () => { detailRevision++; detailAbort?.abort(); detailAbort = null; };
  const clear = () => { listRevision++; listAbort?.abort(); listAbort = null; cancelDetail(); consumed = new Set(); publish(EMPTY); };
  const failure = (code: string, message: string, detail = false) => {
    if (["scope_changed", "unauthorized", "permission_denied"].includes(code)) { clear(); publish({ ...EMPTY, error: message }); }
    else publish({ ...state, ...(detail ? { detail: null, detailLoading: false, detailError: message } : { loading: false, error: message }) });
  };
  const select = async (draftId: string) => {
    if (!isActive() || !state.scope || (!state.drafts.some(d => d.draftId === draftId) && state.selectedId !== draftId)) return;
    cancelDetail(); const revision = detailRevision; const scope = state.scope;
    const abort = new AbortController(); detailAbort = abort;
    publish({ ...state, selectedId: draftId, detail: null, detailLoading: true, detailError: null });
    try {
      const result = await fetchPage({ mailboxId, scope, draftId }, abort.signal);
      if (!isActive() || abort.signal.aborted || revision !== detailRevision || state.scope !== scope || state.selectedId !== draftId) return;
      if (!result.ok) { failure(result.code, result.message, true); return; }
      if (!("draft" in result) || result.scope !== scope || result.draft.draftId !== draftId) { failure("scope_changed", "Refresh Drafts to continue."); return; }
      publish({ ...state, detail: result.draft, detailLoading: false,
        drafts: state.drafts.map(d => d.draftId === draftId ? Object.fromEntries(Object.keys(d).map(k => [k, result.draft[k as keyof GmailDraftDetail]])) as GmailDraftSummary : d) });
    } catch { if (isActive() && revision === detailRevision) failure("unavailable", "The draft preview could not be loaded.", true); }
  };
  const read = async (more = false) => {
    if (!isActive() || more && (state.loading || !state.nextPageToken || !state.scope)) return;
    listRevision++; listAbort?.abort(); const revision = listRevision;
    const abort = new AbortController(); listAbort = abort;
    const pageToken = more ? state.nextPageToken! : undefined;
    const scope = more ? state.scope! : undefined;
    if (!more) cancelDetail();
    publish({ ...state, loading: true, error: null, ...(!more ? { detailLoading: false } : {}) });
    try {
      const result = await fetchPage({ mailboxId, ...(scope ? { scope } : {}), ...(pageToken ? { pageToken } : {}) }, abort.signal);
      if (!isActive() || revision !== listRevision || abort.signal.aborted) return;
      if (!result.ok) { failure(result.code, result.message); return; }
      if (!("drafts" in result) || scope && scope !== result.scope) { failure("scope_changed", "Refresh Drafts to continue."); return; }
      const tokens = more ? new Set(consumed) : new Set<string>(); if (pageToken) tokens.add(pageToken);
      if (result.nextPageToken && tokens.has(result.nextPageToken)) { failure("pagination", "Drafts pagination expired. Refresh Drafts."); return; }
      const drafts = new Map((more ? state.drafts : []).map(d => [d.draftId, d]));
      result.drafts.forEach(d => drafts.set(d.draftId, d)); consumed = tokens;
      const sameScope = state.scope === result.scope;
      const selectedId = sameScope && state.selectedId && (drafts.has(state.selectedId) || result.nextPageToken)
        ? state.selectedId : null;
      if (!selectedId) cancelDetail();
      publish({ ...state, drafts: [...drafts.values()], scope: result.scope, nextPageToken: result.nextPageToken,
        loaded: true, loading: false, selectedId, detail: selectedId ? state.detail : null,
        detailLoading: selectedId ? state.detailLoading : false, detailError: null });
      if (!more && selectedId) void select(selectedId);
    } catch { if (isActive() && revision === listRevision) failure("unavailable", "Gmail drafts could not be loaded. Refresh Drafts."); }
  };
  return { getSnapshot: () => isActive() ? state : EMPTY,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    refresh: () => read(), loadMore: () => read(true), select, clear };
}
