import { useContext, useLayoutEffect, useSyncExternalStore } from "react";
import { MailboxSnapshotContext } from "./MailboxSnapshotContext";
import { getRecipientMemory } from "./recipientMemory";

export function useRecipientMemory() {
  // Context is established from /api/auth/session. Each writer retains its
  // original session, so a late send cannot record recipients as the next user.
  const session = useContext(MailboxSnapshotContext);
  const store = getRecipientMemory(session);
  useLayoutEffect(store.start, [store]);
  useSyncExternalStore(store.subscribe, store.getRecipients, () => null);
  return store;
}
