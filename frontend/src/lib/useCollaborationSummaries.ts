import { useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { createCollaborationSummaryStore } from "./collaborationSummaryStore";
import type { CollaborationOwnerReadDto } from "./collaborationOwnerReadApi";
import { isActiveCollaborationSummary } from "./collaborationSummaryApi";

export function useCollaborationSummaries(workspaceId: string | null, userId: string | null) {
  const scopeKey = workspaceId && userId && /^wsp_[A-Za-z0-9_-]{22}$/.test(workspaceId) &&
    /^usr_[A-Za-z0-9_-]{22}$/.test(userId) ? JSON.stringify([workspaceId, userId]) : "";
  const store = useMemo(() => createCollaborationSummaryStore(scopeKey ? workspaceId : null), [scopeKey, workspaceId]);
  const currentStore = useRef(store);
  currentStore.current = store;
  const summaries = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const index = useMemo(() => new Map([...summaries].filter(([, summary]) => isActiveCollaborationSummary(summary))), [summaries]);
  useEffect(() => {
    void store.refresh();
    return store.cancel;
  }, [store]);
  const acceptMutation = useMemo(() => (collaboration: CollaborationOwnerReadDto) => {
    if (currentStore.current === store) void store.acceptMutation(collaboration);
  }, [store]);
  return { scopeKey, index, acceptMutation };
}
