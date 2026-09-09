import { useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { createWorkspaceNotificationStore } from "./workspaceNotificationStore";
import { notificationScopeKey, type NotificationScope } from "./notificationsApi";

export function useWorkspaceNotifications(scope: NotificationScope | null) {
  const key = notificationScopeKey(scope);
  const store = useMemo(() => createWorkspaceNotificationStore(scope), [key]);
  const active = useRef(store);
  if (active.current !== store) { active.current.cancel(); active.current = store; }
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const lease = useRef(0);
  useEffect(() => {
    const id = ++lease.current;
    void store.start();
    // StrictMode effect replay keeps the same startup request. Actual unmounts
    // cancel before another task can publish; scope changes cancel synchronously.
    return () => { queueMicrotask(() => { if (lease.current === id) store.cancel(); }); };
  }, [store]);
  return { store, state };
}
