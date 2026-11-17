import { useContext, useEffect, useLayoutEffect, useMemo, useSyncExternalStore } from "react";
import { MailboxSnapshotContext } from "./MailboxSnapshotContext";
import { createGmailSentController } from "./gmailSentState";

export function useGmailSent(mailboxId: string, connectionKey: string | null, visible: boolean) {
  const session = useContext(MailboxSnapshotContext);
  const controller = useMemo(() => createGmailSentController(
    mailboxId, () => Boolean(connectionKey && session?.isActive()),
  ), [mailboxId, connectionKey, session]);
  useLayoutEffect(() => {
    const cancel = () => controller.cancel();
    session?.signal.addEventListener("abort", cancel);
    return () => { cancel(); session?.signal.removeEventListener("abort", cancel); };
  }, [controller, session]);
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  useEffect(() => {
    if (visible && connectionKey) void controller.refresh();
    return () => controller.cancel();
  }, [visible, connectionKey, controller]);
  return { ...state, refresh: controller.refresh, loadMore: controller.loadMore };
}
