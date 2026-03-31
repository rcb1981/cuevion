import type { LiveInboxMessageSnapshot } from "./inboxConnectionApi";

const LIVE_INBOX_SNAPSHOTS_STORAGE_KEY = "cuevion-live-inbox-snapshots-v2";

export type LiveInboxSnapshot = {
  inboxId: string;
  email: string;
  fetchedAt: string;
  messages: LiveInboxMessageSnapshot[];
};

type LiveInboxSnapshotStore = Record<string, LiveInboxSnapshot>;

function isSnapshotUiSignalComplete(snapshot: LiveInboxSnapshot) {
  return snapshot.messages.every(
    (message) =>
      typeof message.ui_signal === "string" &&
      message.ui_signal.length > 0 &&
      typeof message.internalClassification === "string" &&
      message.internalClassification.length > 0 &&
      (typeof message.final_visibility === "string" ||
        typeof message.action === "string" ||
        typeof message.v7_final_priority === "string"),
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
      Object.entries(parsed).filter(([, snapshot]) => isSnapshotUiSignalComplete(snapshot)),
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
  currentSnapshots[snapshot.inboxId] = snapshot;
  window.localStorage.setItem(
    LIVE_INBOX_SNAPSHOTS_STORAGE_KEY,
    JSON.stringify(currentSnapshots),
  );
}
