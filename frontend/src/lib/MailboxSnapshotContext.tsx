import React, { createContext, useContext, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { readLiveInboxSnapshots, saveLiveInboxSnapshot, type TrustedLiveInboxSnapshotContexts, type LiveInboxSnapshot } from "./liveInboxSnapshots";
import {
  getMailboxSnapshotSession, sameMailboxSnapshotOwner, subscribeMailboxSnapshotSession,
  readMailboxFolderSnapshot, saveMailboxFolderSnapshot,
  clearMailboxSnapshotSession,
  type MailboxSnapshotOwner, type MailboxSnapshotSession,
} from "./mailboxSnapshotSession";

export const MailboxSnapshotContext = createContext<MailboxSnapshotSession | null>(null);

export function useMailboxSnapshots() {
  const session = useContext(MailboxSnapshotContext);
  // Each callback captures its original, revocable session. Looking up the
  // current account at write time would let an old request write as a new owner.
  return useMemo(() => ({
    readLiveInboxSnapshots: (contexts?: TrustedLiveInboxSnapshotContexts) =>
      readLiveInboxSnapshots(contexts, session),
    saveLiveInboxSnapshot: (snapshot: LiveInboxSnapshot) =>
      saveLiveInboxSnapshot(snapshot, session),
    readFolderSnapshot: (key: string) => readMailboxFolderSnapshot(key, session),
    saveFolderSnapshot: (key: string, value: string) => saveMailboxFolderSnapshot(key, value, session),
    clear: () => clearMailboxSnapshotSession(session),
    isActive: () => session?.isActive() === true,
  }), [session]);
}

// A new epoch remounts the entire private subtree, releasing mailbox bodies,
// selected messages, compose state and Microsoft data held in React state/refs.
export function MailboxSnapshotBoundary({ owner, children, fallback }: {
  owner: MailboxSnapshotOwner | null;
  children: ReactNode;
  fallback: ReactNode;
}) {
  const session = useSyncExternalStore(
    subscribeMailboxSnapshotSession, getMailboxSnapshotSession, () => null,
  );
  if (owner && (!session?.isActive() || !sameMailboxSnapshotOwner(session.owner, owner))) {
    return <>{fallback}</>;
  }
  const cacheSession = owner ? session : null;
  return (
    <MailboxSnapshotContext.Provider key={cacheSession?.epoch ?? "no-mailbox-session"} value={cacheSession}>
      {children}
    </MailboxSnapshotContext.Provider>
  );
}
