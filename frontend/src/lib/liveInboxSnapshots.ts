import type { LiveInboxMessageSnapshot } from "./inboxConnectionApi";

const LIVE_INBOX_SNAPSHOTS_STORAGE_KEY = "cuevion-live-inbox-snapshots";

export type LiveInboxSnapshot = {
  inboxId: string;
  email: string;
  fetchedAt: string;
  messages: LiveInboxMessageSnapshot[];
};

type LiveInboxSnapshotStore = Record<string, LiveInboxSnapshot>;

export function readLiveInboxSnapshots(): LiveInboxSnapshotStore {
  if (typeof window === "undefined") {
    return {};
  }

  const storedValue = window.localStorage.getItem(LIVE_INBOX_SNAPSHOTS_STORAGE_KEY);

  if (!storedValue) {
    return {};
  }

  try {
    return JSON.parse(storedValue) as LiveInboxSnapshotStore;
  } catch {
    return {};
  }
}

export function saveLiveInboxSnapshot(snapshot: LiveInboxSnapshot) {
  if (typeof window === "undefined") {
    return;
  }

  const currentSnapshots = readLiveInboxSnapshots();
  currentSnapshots[snapshot.inboxId] = snapshot;
  window.localStorage.setItem(
    LIVE_INBOX_SNAPSHOTS_STORAGE_KEY,
    JSON.stringify(currentSnapshots),
  );
}
