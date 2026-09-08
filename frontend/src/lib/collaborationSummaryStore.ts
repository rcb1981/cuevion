import {
  isActiveCollaborationSummary,
  listCollaborationSummaries,
  parseCollaborationSummary,
  parseCollaborationSummaryPage,
  type CollaborationSummary,
} from "./collaborationSummaryApi";
import {
  deriveCollaborationOwnerSourceLocator,
  type CollaborationOwnerSourceLocatorInput,
} from "./collaborationOwnerSourceLocator";
import type { CollaborationOwnerReadDto } from "./collaborationOwnerReadApi";

export const COLLABORATION_DISCOVERY_MAX = 1000;
// A page can contain no visible summaries. Bound requests as well as results.
export const COLLABORATION_DISCOVERY_MAX_PAGES = 20;
export type CollaborationSummaryIndex = ReadonlyMap<string, CollaborationSummary>;

export function collaborationSummaryKey(summary: Pick<CollaborationSummary, "workspaceId" | "mailboxId" | "sourceRef">) {
  const source = summary.sourceRef;
  return JSON.stringify([summary.workspaceId, summary.mailboxId, source.provider,
    ...(source.provider === "google" ? [source.providerMessageId]
      : [source.folder, source.uidValidity, source.imapUid])]);
}

export function indexCollaborationSummaries(workspaceId: string, values: readonly unknown[]): CollaborationSummaryIndex {
  const index = new Map<string, CollaborationSummary>();
  const ambiguous = new Set<string>();
  for (const value of values) {
    const summary = parseCollaborationSummary(value, workspaceId);
    if (!summary) continue;
    const key = collaborationSummaryKey(summary);
    const previous = index.get(key);
    if (previous && JSON.stringify(previous) !== JSON.stringify(summary)) ambiguous.add(key);
    index.set(key, summary);
  }
  for (const key of ambiguous) index.delete(key);
  return index;
}

export function lookupActiveCollaborationSummary(
  index: CollaborationSummaryIndex,
  workspaceId: string | null,
  input: CollaborationOwnerSourceLocatorInput,
): CollaborationSummary | null {
  if (!workspaceId || index.size === 0) return null;
  const locator = deriveCollaborationOwnerSourceLocator(input);
  if (!locator) return null;
  const sourceRef: CollaborationSummary["sourceRef"] = "providerMessageId" in locator.sourceRef
    ? { provider: "google", ...locator.sourceRef }
    : { provider: "custom_imap", ...locator.sourceRef };
  const summary = index.get(collaborationSummaryKey({ workspaceId, mailboxId: locator.mailboxId, sourceRef }));
  return summary && isActiveCollaborationSummary(summary) ? summary : null;
}

export type CollaborationOpenBinding = {
  scopeKey: string;
  selectionKey: string;
  summary: CollaborationSummary;
};

export function isCurrentCollaborationOpenBinding(
  captured: CollaborationOpenBinding,
  current: CollaborationOpenBinding | null,
): boolean {
  return Boolean(current && captured.scopeKey === current.scopeKey &&
    captured.selectionKey === current.selectionKey &&
    captured.summary.collaborationId === current.summary.collaborationId &&
    collaborationSummaryKey(captured.summary) === collaborationSummaryKey(current.summary) &&
    isActiveCollaborationSummary(current.summary));
}

type PageLoader = typeof listCollaborationSummaries;
export async function loadWorkspaceCollaborationSummaries(
  workspaceId: string,
  loadPage: PageLoader = listCollaborationSummaries,
  isCurrent: () => boolean = () => true,
): Promise<CollaborationSummary[] | null> {
  const summaries: CollaborationSummary[] = [];
  let cursor: string | null = null;
  try {
    for (let request = 0; request < COLLABORATION_DISCOVERY_MAX_PAGES; request += 1) {
      if (!isCurrent()) return null;
      const result = await loadPage(workspaceId, cursor);
      if (!isCurrent() || result.status !== "success") return null;
      const page = parseCollaborationSummaryPage(result.page, workspaceId, cursor);
      if (!page || summaries.length + page.summaries.length > COLLABORATION_DISCOVERY_MAX) return null;
      summaries.push(...page.summaries);
      if (page.nextCursor === null) return summaries;
      cursor = page.nextCursor;
    }
  } catch {
    // Keep ordinary mail usable on transport/parser failure.
  }
  return null;
}

// An instance belongs to one authenticated account/workspace lifetime. Nothing
// is persisted, and a previous instance cannot publish into a new identity.
export function createCollaborationSummaryStore(workspaceId: string | null, loadPage?: PageLoader) {
  let snapshot: CollaborationSummaryIndex = new Map();
  let generation = 0;
  let pending: Promise<void> | null = null;
  const listeners = new Set<() => void>();
  const publish = (values: readonly unknown[]) => {
    snapshot = indexCollaborationSummaries(workspaceId!, values);
    listeners.forEach(listener => listener());
  };
  const cancel = () => { generation += 1; pending = null; };
  const refresh = (): Promise<void> => {
    if (!workspaceId) return Promise.resolve();
    if (pending) return pending;
    const request = ++generation;
    const operation = loadWorkspaceCollaborationSummaries(workspaceId, loadPage, () => request === generation)
      .then(values => { if (request === generation && values !== null) publish(values); })
      .finally(() => { if (request === generation) pending = null; });
    pending = operation;
    return operation;
  };
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    refresh,
    cancel,
    acceptMutation: (collaboration: CollaborationOwnerReadDto): Promise<void> => {
      if (!workspaceId) return Promise.resolve();
      const existing = [...snapshot.values()].find(summary => summary.collaborationId === collaboration.collaborationId &&
        summary.mailboxId === collaboration.mailboxId);
      if (existing) {
        if (collaboration.updatedAt < existing.updatedAt) return Promise.resolve();
        const updated = parseCollaborationSummary({ ...existing, state: collaboration.state,
          updatedAt: collaboration.updatedAt, viewerAccess: collaboration.viewerAccess }, workspaceId);
        if (!updated) return Promise.resolve();
        cancel(); // An earlier list response must not undo a confirmed mutation.
        publish([...snapshot.values()].map(summary => summary === existing ? updated : summary));
        return Promise.resolve();
      }
      // Create DTOs omit workspace/sourceRef. Refresh once instead of inventing routing.
      cancel();
      return refresh();
    },
  };
}
