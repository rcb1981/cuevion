import type { LiveInboxMessageSnapshot } from "./inboxConnectionApi";
import {
  applyLiveThreadIdentity,
  buildConservativeLiveCustomImapThreadId,
  normalizeThreadSubject,
  type LiveInboxProvider,
  type LiveThreadIdentityContext,
} from "./inboxEngine";

const LIVE_INBOX_SNAPSHOTS_STORAGE_KEY = "cuevion-live-inbox-snapshots";
const LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION = 5;
export const LIVE_INBOX_THREAD_IDENTITY_VERSION = 1;
export const MUSIC_CLASSIFIER_VERSION = "2026-07-01-universal-music-subject-v3";

export type LiveInboxSnapshot = {
  schemaVersion?: number;
  threadIdentityVersion?: number;
  classifierVersion?: string;
  provider?: LiveInboxProvider;
  inboxId: string;
  email: string;
  fetchedAt: string;
  messages: LiveInboxMessageSnapshot[];
  folder?: string;
  uidValidity?: string | null;
};

type LiveInboxSnapshotStore = Record<string, LiveInboxSnapshot>;
export type TrustedLiveInboxSnapshotContext = Pick<
  LiveThreadIdentityContext,
  "mailboxId" | "provider" | "folder"
>;
export type TrustedLiveInboxSnapshotContexts = Record<
  string,
  TrustedLiveInboxSnapshotContext
>;

export function removeGmailInboxProviderMessageFromSnapshot(
  snapshot: LiveInboxSnapshot | undefined,
  mailboxId: string,
  providerMessageId: string,
): LiveInboxSnapshot | undefined {
  if (
    !snapshot ||
    snapshot.provider !== "google" ||
    snapshot.folder?.trim().toUpperCase() !== "INBOX" ||
    snapshot.inboxId !== mailboxId
  ) {
    return snapshot;
  }

  const messages = snapshot.messages.filter(
    (message) =>
      message.serverMailboxId !== mailboxId ||
      message.providerMessageId !== providerMessageId,
  );

  return messages.length === snapshot.messages.length
    ? snapshot
    : {
        ...snapshot,
        messages,
      };
}

export function removeAndPersistGmailInboxProviderMessageFromSnapshot(
  snapshot: LiveInboxSnapshot | undefined,
  mailboxId: string,
  providerMessageId: string,
  persistSnapshot: (snapshot: LiveInboxSnapshot) => void,
): { snapshot: LiveInboxSnapshot | undefined; changed: boolean } {
  let nextSnapshot: LiveInboxSnapshot | undefined;

  try {
    nextSnapshot = removeGmailInboxProviderMessageFromSnapshot(
      snapshot,
      mailboxId,
      providerMessageId,
    );
  } catch {
    return { snapshot, changed: false };
  }

  if (!nextSnapshot || nextSnapshot === snapshot) {
    return { snapshot, changed: false };
  }

  try {
    persistSnapshot(nextSnapshot);
  } catch {
    // A provider-confirmed Archive must not fail on local snapshot persistence.
  }

  return { snapshot: nextSnapshot, changed: true };
}

export function buildLiveInboxSnapshotThreadIdentityContext(
  snapshot: LiveInboxSnapshot,
): LiveThreadIdentityContext | null {
  if (!snapshot.provider) {
    return null;
  }

  return {
    mailboxId: snapshot.inboxId,
    provider: snapshot.provider,
    folder: snapshot.folder?.trim() || "INBOX",
    uidValidity: snapshot.uidValidity ?? null,
  };
}

export function hydrateLiveInboxSnapshot(snapshot: LiveInboxSnapshot) {
  const context = buildLiveInboxSnapshotThreadIdentityContext(snapshot);

  return {
    context,
    messages: context
      ? snapshot.messages.map((message) => applyLiveThreadIdentity(message, context))
      : snapshot.messages,
  };
}

function migrateCustomImapMessageThreadIdentity(
  message: LiveInboxMessageSnapshot,
  context: LiveThreadIdentityContext,
): LiveInboxMessageSnapshot {
  const threadId = message.threadId?.trim();
  if (threadId && threadId !== normalizeThreadSubject(message.subject)) {
    return message;
  }

  return {
    ...message,
    threadId: buildConservativeLiveCustomImapThreadId(message, context),
  };
}

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
  trustedContext?: TrustedLiveInboxSnapshotContext,
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

  const normalizedInboxId = snapshot.inboxId || inboxId;
  const storedProvider =
    snapshot.provider === "google" || snapshot.provider === "custom_imap"
      ? snapshot.provider
      : undefined;
  if (
    storedProvider &&
    trustedContext?.provider &&
    storedProvider !== trustedContext.provider
  ) {
    return null;
  }
  const provider = storedProvider ?? trustedContext?.provider;
  const folder = String(snapshot.folder ?? trustedContext?.folder ?? "INBOX").trim() || "INBOX";
  const uidValidity =
    typeof snapshot.uidValidity === "string" || snapshot.uidValidity === null
      ? snapshot.uidValidity
      : undefined;
  const threadIdentityContext = provider
    ? {
        mailboxId: normalizedInboxId,
        provider,
        folder,
        uidValidity: uidValidity ?? null,
      }
    : null;

  return {
    ...snapshot,
    schemaVersion: LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
    threadIdentityVersion: provider
      ? LIVE_INBOX_THREAD_IDENTITY_VERSION
      : snapshot.threadIdentityVersion,
    classifierVersion: MUSIC_CLASSIFIER_VERSION,
    provider,
    inboxId: normalizedInboxId,
    email: String(snapshot.email ?? "").trim().toLowerCase(),
    fetchedAt: String(snapshot.fetchedAt ?? new Date().toISOString()),
    messages: messages.map((message) => {
      const normalizedMessage = {
        ...message,
        classifierVersion: MUSIC_CLASSIFIER_VERSION,
      };

      return threadIdentityContext?.provider === "custom_imap"
        ? migrateCustomImapMessageThreadIdentity(
            normalizedMessage,
            threadIdentityContext,
          )
        : normalizedMessage;
    }),
    folder,
    uidValidity,
  };
}

function normalizeSnapshotStore(
  store: LiveInboxSnapshotStore,
  trustedContexts: TrustedLiveInboxSnapshotContexts,
): LiveInboxSnapshotStore {
  return Object.fromEntries(
    Object.entries(store)
      .map(([inboxId, snapshot]): [string, LiveInboxSnapshot | null] => [
        inboxId,
        normalizeSnapshot(
          inboxId,
          snapshot as LiveInboxSnapshot,
          trustedContexts[inboxId],
        ),
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

export function readLiveInboxSnapshots(
  trustedContexts: TrustedLiveInboxSnapshotContexts = {},
): LiveInboxSnapshotStore {
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
    const nextSnapshots = normalizeSnapshotStore(
      sourceSnapshots as LiveInboxSnapshotStore,
      trustedContexts,
    );
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

  if (
    (snapshot.provider !== "google" && snapshot.provider !== "custom_imap") ||
    !snapshot.folder?.trim()
  ) {
    return;
  }

  const trustedContext = {
    mailboxId: snapshot.inboxId,
    provider: snapshot.provider,
    folder: snapshot.folder,
  };
  const trustedContexts = { [snapshot.inboxId]: trustedContext };
  const currentSnapshots = readLiveInboxSnapshots(trustedContexts);
  const nextSnapshot = normalizeSnapshot(snapshot.inboxId, {
    ...snapshot,
    schemaVersion: LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
    threadIdentityVersion: LIVE_INBOX_THREAD_IDENTITY_VERSION,
    classifierVersion: MUSIC_CLASSIFIER_VERSION,
    provider: snapshot.provider,
    folder: snapshot.folder,
    messages: snapshot.messages.map((message) => ({
      ...message,
      classifierVersion: message.classifierVersion ?? MUSIC_CLASSIFIER_VERSION,
    })),
  }, trustedContext);

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
