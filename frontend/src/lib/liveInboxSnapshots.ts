import type { LiveInboxMessageSnapshot } from "./inboxConnectionApi";

const LIVE_INBOX_SNAPSHOTS_STORAGE_KEY = "cuevion-live-inbox-snapshots";
const LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION = 4;
const MAX_SNAPSHOT_MESSAGES_PER_MAILBOX = 50;
const QUOTA_RETRY_MESSAGES_PER_MAILBOX = 25;

export type LiveInboxSnapshot = {
  schemaVersion?: number;
  inboxId: string;
  email: string;
  fetchedAt: string;
  messages: LiveInboxMessageSnapshot[];
  uidValidity?: string | null;
};

type LiveInboxSnapshotStore = Record<string, LiveInboxSnapshot>;

type SnapshotMessageInput = LiveInboxMessageSnapshot & {
  category?: string;
  categorySource?: string;
  categoryConfidence?: string;
  threadId?: string;
  v7_final_priority?: string;
};

function isSnapshotUiSignalComplete(snapshot: LiveInboxSnapshot) {
  return snapshot.messages.every(
    (message) => typeof message.ui_signal === "string" && message.ui_signal.length > 0,
  );
}

function resolveSnapshotMessageTime(value: LiveInboxMessageSnapshot) {
  const timestamp = Date.parse(value.createdAt || value.timestamp || "");
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function pruneSnapshotMessages(
  messages: LiveInboxMessageSnapshot[],
  limit = MAX_SNAPSHOT_MESSAGES_PER_MAILBOX,
) {
  return [...messages]
    .sort((first, second) => resolveSnapshotMessageTime(second) - resolveSnapshotMessageTime(first))
    .slice(0, limit);
}

function compactSnapshotMessage(message: SnapshotMessageInput): LiveInboxMessageSnapshot {
  return {
    id: String(message.id ?? ""),
    imapUid: typeof message.imapUid === "string" ? message.imapUid : undefined,
    threadId: typeof message.threadId === "string" ? message.threadId : undefined,
    sender: String(message.sender ?? ""),
    subject: String(message.subject ?? ""),
    snippet: String(message.snippet ?? ""),
    from: String(message.from ?? ""),
    to: String(message.to ?? ""),
    cc: typeof message.cc === "string" ? message.cc : undefined,
    timestamp: String(message.timestamp ?? message.createdAt ?? ""),
    createdAt: String(message.createdAt ?? message.timestamp ?? ""),
    body: [],
    unread: message.unread === true,
    category: typeof message.category === "string" ? message.category : undefined,
    categorySource:
      typeof message.categorySource === "string" ? message.categorySource : undefined,
    categoryConfidence:
      typeof message.categoryConfidence === "string" ? message.categoryConfidence : undefined,
    signal: typeof message.signal === "string" ? message.signal : undefined,
    ui_signal:
      typeof message.ui_signal === "string" && message.ui_signal
        ? message.ui_signal
        : "UNKNOWN",
    internalClassification:
      typeof message.internalClassification === "string"
        ? message.internalClassification
        : undefined,
    final_visibility:
      typeof message.final_visibility === "string" ? message.final_visibility : undefined,
    action: typeof message.action === "string" ? message.action : undefined,
    v7_final_priority:
      typeof message.v7_final_priority === "string" ? message.v7_final_priority : undefined,
  };
}

function compactLiveInboxSnapshot(
  snapshot: LiveInboxSnapshot,
  limit = MAX_SNAPSHOT_MESSAGES_PER_MAILBOX,
): LiveInboxSnapshot {
  const messages = Array.isArray(snapshot.messages)
    ? pruneSnapshotMessages(
        snapshot.messages
          .filter((message) => message && typeof message === "object")
          .map((message) => compactSnapshotMessage(message as SnapshotMessageInput))
          .filter((message) => message.id && message.subject),
        limit,
      )
    : [];

  return {
    schemaVersion: LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
    inboxId: String(snapshot.inboxId ?? ""),
    email: String(snapshot.email ?? "").trim().toLowerCase(),
    fetchedAt: String(snapshot.fetchedAt ?? new Date().toISOString()),
    messages,
    uidValidity:
      typeof snapshot.uidValidity === "string" || snapshot.uidValidity === null
        ? snapshot.uidValidity
        : undefined,
  };
}

function compactLiveInboxSnapshotStore(
  store: LiveInboxSnapshotStore,
  limit = MAX_SNAPSHOT_MESSAGES_PER_MAILBOX,
): LiveInboxSnapshotStore {
  return Object.fromEntries(
    Object.entries(store)
      .map(([inboxId, snapshot]): [string, LiveInboxSnapshot] => {
        const compactSnapshot = compactLiveInboxSnapshot(
          {
            ...(snapshot as LiveInboxSnapshot),
            inboxId: (snapshot as LiveInboxSnapshot).inboxId || inboxId,
          },
          limit,
        );

        return [inboxId, compactSnapshot];
      })
      .filter(([, snapshot]) => snapshot.inboxId && isSnapshotUiSignalComplete(snapshot)),
  ) as LiveInboxSnapshotStore;
}

function isQuotaError(error: unknown) {
  return (
    error instanceof DOMException &&
    (error.name === "QuotaExceededError" || error.name === "NS_ERROR_DOM_QUOTA_REACHED")
  );
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
    const nextSnapshots = compactLiveInboxSnapshotStore(
      sourceSnapshots as LiveInboxSnapshotStore,
    );
    const nextStoredValue = JSON.stringify(nextSnapshots);

    if (
      Object.keys(nextSnapshots).length !== Object.keys(sourceSnapshots).length ||
      nextStoredValue !== storedValue
    ) {
      try {
        window.localStorage.setItem(LIVE_INBOX_SNAPSHOTS_STORAGE_KEY, nextStoredValue);
      } catch {
        // Ignore cleanup failures; callers can still hydrate from the parsed cache.
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
  const nextSnapshots = compactLiveInboxSnapshotStore({
    ...currentSnapshots,
    [snapshot.inboxId]: snapshot,
  });

  try {
    writeSnapshotStore(nextSnapshots);
  } catch (error) {
    if (!isQuotaError(error)) {
      return;
    }

    try {
      writeSnapshotStore(
        compactLiveInboxSnapshotStore(nextSnapshots, QUOTA_RETRY_MESSAGES_PER_MAILBOX),
      );
    } catch {
      // Snapshot cache is best-effort; live React state must never depend on storage.
    }
  }
}
