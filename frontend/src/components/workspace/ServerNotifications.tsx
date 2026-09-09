import { useEffect } from "react";
import { notificationCopy, type ServerNotification } from "../../lib/notificationsApi";
import type { NotificationStoreState, WorkspaceNotificationStore } from "../../lib/workspaceNotificationStore";

export type ServerNotificationsProps = {
  store: WorkspaceNotificationStore; state: NotificationStoreState;
  onOpen: (row: ServerNotification) => void; preview?: boolean;
};
export function ServerNotifications({ store, state, onOpen, preview = false }: ServerNotificationsProps) {
  useEffect(() => { void store.ensureFirstPage(preview ? "dashboard" : "workbench"); }, [store, preview]);
  const ids = preview ? state.orderedIds.slice(0, 5) : state.orderedIds;
  return <div className="min-w-0 space-y-3" data-server-notifications={preview ? "dashboard" : "workbench"}>
    <div className="flex items-center justify-between gap-3">
      {preview ? <h2 className="text-xl font-semibold text-[var(--workspace-text)]">Notifications</h2> : <span className="text-sm text-[var(--workspace-text-soft)]">Your collaboration updates</span>}
      <button type="button" disabled={state.listLoading || state.loadMoreLoading} onClick={() => { void store.refresh(); }} className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2">Refresh</button>
    </div>
    {state.listLoading ? <p role="status">Loading notifications…</p> : null}
    {state.listError ? <div role="alert">Notifications could not be loaded. <button type="button" onClick={() => { void store.refresh(); }} className="underline">Retry</button></div> : null}
    {!state.listLoading && !state.listError && state.firstPageLoaded && ids.length === 0 ? <p>No notifications yet.</p> : null}
    <div className="divide-y divide-[var(--workspace-divider)]">
      {ids.map(id => {
        const row = state.byId.get(id)!;
        return <button key={id} type="button" onClick={() => onOpen(row)} className="flex w-full flex-wrap items-start justify-between gap-x-4 gap-y-2 rounded-xl px-3 py-4 text-left hover:bg-[var(--workspace-surface-hover)] focus-visible:outline focus-visible:outline-2" data-notification-id={id}>
          <span className="min-w-0 flex-1 basis-48 break-words text-[var(--workspace-text)]">{notificationCopy(row)}{row.readAt === null ? <span className="ml-2 inline-block rounded border border-current px-1.5 text-xs font-semibold">Unread</span> : null}</span>
          <time dateTime={new Date(row.createdAt).toISOString()} className="text-xs text-[var(--workspace-text-soft)]">{new Date(row.createdAt).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>
        </button>;
      })}
    </div>
    {!preview && state.nextCursor !== null ? <button type="button" disabled={state.loadMoreLoading || state.listLoading} onClick={() => { void store.loadMore(); }} className="rounded-lg border border-[var(--workspace-border)] px-4 py-2 focus-visible:outline focus-visible:outline-2">{state.loadMoreLoading ? "Loading more…" : "Load more"}</button> : null}
    {!preview && state.loadMoreError ? <p role="alert">More notifications could not be loaded. Use Load more to try again.</p> : null}
  </div>;
}
