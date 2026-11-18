import { useContext, useEffect, useLayoutEffect, useMemo, useSyncExternalStore } from "react";
import { MailboxSnapshotContext } from "./MailboxSnapshotContext";
import { createGmailDraftsController } from "./gmailDraftsState";

export function useGmailDrafts(mailboxId: string, connectionKey: string | null, visible: boolean) {
  const session = useContext(MailboxSnapshotContext);
  const controller = useMemo(() => createGmailDraftsController(mailboxId,
    () => !!connectionKey && session?.isActive() === true), [mailboxId, connectionKey, session]);
  useLayoutEffect(() => {
    session?.signal.addEventListener("abort", controller.clear);
    return () => { controller.clear(); session?.signal.removeEventListener("abort", controller.clear); };
  }, [controller, session]);
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  useEffect(() => {
    if (visible && connectionKey) void controller.refresh();
    return controller.clear;
  }, [controller, visible, connectionKey]);
  return { ...state, refresh: controller.refresh, loadMore: controller.loadMore, select: controller.select };
}
