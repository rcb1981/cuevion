import { useContext, useEffect, useLayoutEffect, useMemo, useSyncExternalStore } from "react";
import { MailboxSnapshotContext } from "./MailboxSnapshotContext";
import { createImapSentController } from "./imapSentState";

export function useImapSent(mailboxId: string, connectionKey: string | null, visible: boolean) {
  const session = useContext(MailboxSnapshotContext);
  const controller = useMemo(() => createImapSentController(mailboxId, () => Boolean(connectionKey && session?.isActive())), [mailboxId, connectionKey, session]);
  useLayoutEffect(() => {
    const clear = () => controller.reset();
    session?.signal.addEventListener("abort", clear);
    return () => { controller.cancel(); session?.signal.removeEventListener("abort", clear); };
  }, [controller, session]);
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  useEffect(() => {
    if (visible && connectionKey) void controller.refresh();
    return () => controller.cancel();
  }, [visible, connectionKey, controller]);
  return { ...state, refresh: controller.refresh, loadMore: controller.loadMore, choose: controller.choose };
}
