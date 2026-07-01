import type { LiveInboxMessageSnapshot } from "./inboxConnectionApi";

const LIVE_INBOX_SNAPSHOTS_STORAGE_KEY = "cuevion-live-inbox-snapshots";
const LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION = 5;
export const MUSIC_CLASSIFIER_VERSION = "2026-07-01-universal-music-subject-v3";

export type LiveInboxSnapshot = {
  schemaVersion?: number;
  classifierVersion?: string;
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

function hasMessageBodyPayload(message: LiveInboxMessageSnapshot) {
  const hasBodyHtml = typeof message.bodyHtml === "string" && message.bodyHtml.trim().length > 0;
  const hasTextBody =
    Array.isArray(message.body) &&
    message.body.some((line) => typeof line === "string" && line.trim().length > 0);
  const hasAttachments = Array.isArray(message.attachments) && message.attachments.length > 0;

  return hasBodyHtml || hasTextBody || hasAttachments;
}

function normalizeSnapshot(
  inboxId: string,
  snapshot: LiveInboxSnapshot,
): LiveInboxSnapshot | null {
  if (snapshot.classifierVersion !== MUSIC_CLASSIFIER_VERSION) {
    return null;
  }

  const messages = Array.isArray(snapshot.messages)
    ? snapshot.messages.filter(
        (message) =>
          message &&
          typeof message === "object" &&
          typeof message.id === "string" &&
          message.id.length > 0 &&
          hasMessageBodyPayload(message),
      )
    : [];

  if (!snapshot.inboxId && !inboxId) {
    return null;
  }

  return {
    ...snapshot,
    schemaVersion: LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
    classifierVersion: MUSIC_CLASSIFIER_VERSION,
    inboxId: snapshot.inboxId || inboxId,
    email: String(snapshot.email ?? "").trim().toLowerCase(),
    fetchedAt: String(snapshot.fetchedAt ?? new Date().toISOString()),
    messages: messages.map((message) => ({
      ...message,
      classifierVersion: MUSIC_CLASSIFIER_VERSION,
    })),
    uidValidity:
      typeof snapshot.uidValidity === "string" || snapshot.uidValidity === null
        ? snapshot.uidValidity
        : undefined,
  };
}

function normalizeSnapshotStore(store: LiveInboxSnapshotStore): LiveInboxSnapshotStore {
  return Object.fromEntries(
    Object.entries(store)
      .map(([inboxId, snapshot]): [string, LiveInboxSnapshot | null] => [
        inboxId,
        normalizeSnapshot(inboxId, snapshot as LiveInboxSnapshot),
      ])
      .filter(
        (entry): entry is [string, LiveInboxSnapshot] =>
          Boolean(entry[1]?.inboxId) &&
          Boolean(entry[1]?.messages.length) &&
          isSnapshotUiSignalComplete(entry[1] as LiveInboxSnapshot),
      ),
  ) as LiveInboxSnapshotStore;
}

function writeSnapshotStore(store: LiveInboxSnapshotStore) {
  window.localStorage.setItem(
    LIVE_INBOX_SNAPSHOTS_STORAGE_KEY,
    JSON.stringify(store),
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
    const sourceSnapshots = parsed && typeof parsed === "object" ? parsed : {};
    const nextSnapshots = normalizeSnapshotStore(sourceSnapshots as LiveInboxSnapshotStore);
    const nextStoredValue = JSON.stringify(nextSnapshots);

    if (
      Object.keys(nextSnapshots).length !== Object.keys(sourceSnapshots).length ||
      nextStoredValue !== storedValue
    ) {
      try {
        window.localStorage.setItem(LIVE_INBOX_SNAPSHOTS_STORAGE_KEY, nextStoredValue);
      } catch {
        // Snapshot cleanup is best-effort; callers can use the sanitized in-memory copy.
      }
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
  const nextSnapshot = normalizeSnapshot(snapshot.inboxId, {
    ...snapshot,
    schemaVersion: LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
    classifierVersion: MUSIC_CLASSIFIER_VERSION,
    messages: snapshot.messages.map((message) => ({
      ...message,
      classifierVersion: message.classifierVersion ?? MUSIC_CLASSIFIER_VERSION,
    })),
  });

  if (!nextSnapshot) {
    return;
  }

  const nextSnapshots = {
    ...currentSnapshots,
    [nextSnapshot.inboxId]: nextSnapshot,
  };

  try {
    writeSnapshotStore(nextSnapshots);
  } catch {
    // Persistence is best-effort. Never strip message bodies to satisfy quota.
  }
}
