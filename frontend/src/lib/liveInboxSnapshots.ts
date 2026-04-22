import type { LiveInboxMessageSnapshot } from "./inboxConnectionApi";

const LIVE_INBOX_SNAPSHOTS_STORAGE_KEY = "cuevion-live-inbox-snapshots";
const LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION = 2;

export type LiveInboxSnapshot = {
  schemaVersion?: number;
  inboxId: string;
  email: string;
  fetchedAt: string;
  messages: LiveInboxMessageSnapshot[];
  uidValidity?: string | null;
};

type LiveInboxSnapshotStore = Record<string, LiveInboxSnapshot>;

function isSnapshotUiSignalComplete(snapshot: LiveInboxSnapshot) {
  return snapshot.messages.every(
    (message) => typeof message.ui_signal === "string" && message.ui_signal.length > 0,
  );
}

function isCurrentLiveInboxSnapshot(snapshot: LiveInboxSnapshot) {
  return (
    snapshot.schemaVersion === LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION &&
    isSnapshotUiSignalComplete(snapshot)
  );
}

export function readLiveInboxSnapshots(): LiveInboxSnapshotStore {
  if (typeof window === "undefined") {
    return {};
  }

  const storedValue = window.localStorage.getItem(LIVE_INBOX_SNAPSHOTS_STORAGE_KEY);

  if (!storedValue) {
    return {};
  }

  try {
    const parsed = JSON.parse(storedValue) as LiveInboxSnapshotStore;
    const nextSnapshots = Object.fromEntries(
      Object.entries(parsed).filter(([, snapshot]) => isCurrentLiveInboxSnapshot(snapshot)),
    ) as LiveInboxSnapshotStore;

    if (Object.keys(nextSnapshots).length !== Object.keys(parsed).length) {
      window.localStorage.setItem(
        LIVE_INBOX_SNAPSHOTS_STORAGE_KEY,
        JSON.stringify(nextSnapshots),
      );
    }

    return nextSnapshots;
  } catch {
    return {};
  }
}

export function saveLiveInboxSnapshot(snapshot: LiveInboxSnapshot) {
  if (typeof window === "undefined") {
    return;
  }

  const currentSnapshots = readLiveInboxSnapshots();
  currentSnapshots[snapshot.inboxId] = {
    ...snapshot,
    schemaVersion: LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
  };
  window.localStorage.setItem(
    LIVE_INBOX_SNAPSHOTS_STORAGE_KEY,
    JSON.stringify(currentSnapshots),
  );
}
