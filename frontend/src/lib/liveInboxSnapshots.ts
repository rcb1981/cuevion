import type { LiveInboxMessageSnapshot } from "./inboxConnectionApi";
import {
  applyLiveThreadIdentity,
  buildConservativeLiveCustomImapThreadId,
  normalizeThreadSubject,
  type LiveInboxProvider,
  type LiveThreadIdentityContext,
} from "./inboxEngine";
import { resolveMessageNoisePolicy } from "./messageNoiseGate";

const LIVE_INBOX_SNAPSHOTS_STORAGE_KEY = "cuevion-live-inbox-snapshots";
export const LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION = 6;
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
type LegacyLiveInboxMessageSnapshot = LiveInboxMessageSnapshot & {
  isAutoReply?: boolean;
  isShared?: boolean;
  collaboration?: unknown;
};
export type TrustedLiveInboxSnapshotContext = Pick<
  LiveThreadIdentityContext,
  "mailboxId" | "provider" | "folder"
>;
export type TrustedLiveInboxSnapshotContexts = Record<
  string,
  TrustedLiveInboxSnapshotContext
>;

function isLiveInboxProvider(value: unknown): value is LiveInboxProvider {
  return value === "google" || value === "custom_imap";
}

function isExactMailboxId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value === value.trim()
  );
}

function isExactLiveInboxFolder(value: unknown): value is "INBOX" {
  return value === "INBOX";
}

function isExactProviderFolder(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value === value.trim() &&
    !Array.from(value).some((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint < 32 || codePoint === 127;
    })
  );
}

function hasCustomImapProviderIdentityConflict(
  message: LiveInboxMessageSnapshot,
) {
  return (
    (message.providerMessageId !== undefined &&
      message.providerMessageId !== null) ||
    (message.providerThreadId !== undefined &&
      message.providerThreadId !== null) ||
    (message.labelIds !== undefined && message.labelIds !== null)
  );
}

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

const CANONICAL_IMAP_UID = /^[1-9][0-9]*$/;
const CANONICAL_UID_VALIDITY = /^[1-9][0-9]{0,19}$/;
const MAX_IMAP_UID = 4_294_967_295;

function isCanonicalImapUid(value: unknown): value is string {
  if (typeof value !== "string" || !CANONICAL_IMAP_UID.test(value)) {
    return false;
  }
  const maximum = String(MAX_IMAP_UID);
  return (
    value.length < maximum.length ||
    (value.length === maximum.length && value <= maximum)
  );
}

function isCanonicalUidValidity(value: unknown): value is string {
  return typeof value === "string" && CANONICAL_UID_VALIDITY.test(value);
}

export function removeCustomImapInboxMessageFromSnapshot(
  snapshot: LiveInboxSnapshot | undefined,
  mailboxId: string,
  uidValidity: string,
  imapUid: string,
): LiveInboxSnapshot | undefined {
  if (
    !snapshot ||
    snapshot.provider !== "custom_imap" ||
    snapshot.folder !== "INBOX" ||
    snapshot.inboxId !== mailboxId ||
    snapshot.uidValidity !== uidValidity ||
    !isCanonicalUidValidity(uidValidity) ||
    !isCanonicalImapUid(imapUid)
  ) {
    return snapshot;
  }

  const exactMatches = snapshot.messages.filter(
    (message) =>
      message.serverMailboxId === mailboxId &&
      message.providerFolder === "INBOX" &&
      message.uidValidity === uidValidity &&
      message.imapUid === imapUid,
  );
  if (exactMatches.length !== 1) {
    return snapshot;
  }

  const exactMatch = exactMatches[0];
  return {
    ...snapshot,
    messages: snapshot.messages.filter((message) => message !== exactMatch),
  };
}

export function removeAndPersistCustomImapInboxMessageFromSnapshot(
  snapshot: LiveInboxSnapshot | undefined,
  mailboxId: string,
  uidValidity: string,
  imapUid: string,
  persistSnapshot: (snapshot: LiveInboxSnapshot) => void,
): { snapshot: LiveInboxSnapshot | undefined; changed: boolean } {
  let nextSnapshot: LiveInboxSnapshot | undefined;

  try {
    nextSnapshot = removeCustomImapInboxMessageFromSnapshot(
      snapshot,
      mailboxId,
      uidValidity,
      imapUid,
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
    // Provider-confirmed state stays authoritative without local persistence.
  }

  return { snapshot: nextSnapshot, changed: true };
}

export function buildLiveInboxSnapshotThreadIdentityContext(
  snapshot: LiveInboxSnapshot,
): LiveThreadIdentityContext | null {
  if (
    !isLiveInboxProvider(snapshot.provider) ||
    !isExactMailboxId(snapshot.inboxId) ||
    !isExactLiveInboxFolder(snapshot.folder)
  ) {
    return null;
  }

  return {
    mailboxId: snapshot.inboxId,
    provider: snapshot.provider,
    folder: snapshot.folder,
    uidValidity: snapshot.uidValidity ?? null,
  };
}

function hasLiveInboxMessageScopeConflict(snapshot: LiveInboxSnapshot) {
  if (!Array.isArray(snapshot.messages)) {
    return true;
  }

  const expectedProviderFolder =
    snapshot.provider === "google"
      ? "Inbox"
      : snapshot.provider === "custom_imap"
        ? "INBOX"
        : null;

  return (
    expectedProviderFolder === null ||
    snapshot.messages.some(
      (message) =>
        !message ||
        typeof message !== "object" ||
        (message.serverMailboxId !== undefined &&
          message.serverMailboxId !== snapshot.inboxId) ||
        (message.providerFolder !== undefined &&
          message.providerFolder !== expectedProviderFolder) ||
        (snapshot.provider === "custom_imap" &&
          ((message.uidValidity !== undefined &&
            message.uidValidity !== snapshot.uidValidity) ||
            hasCustomImapProviderIdentityConflict(message))),
    )
  );
}

export function hydrateLiveInboxSnapshot(snapshot: LiveInboxSnapshot) {
  const context = buildLiveInboxSnapshotThreadIdentityContext(snapshot);

  if (!context || hasLiveInboxMessageScopeConflict(snapshot)) {
    return { context: null, messages: [] };
  }

  return {
    context,
    messages: snapshot.messages.map((message) =>
      applyLiveThreadIdentity(message, context),
    ),
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

export function upconvertLegacyPromoReminderSnapshotMessage(
  message: LiveInboxMessageSnapshot,
): LiveInboxMessageSnapshot {
  const legacyMessage = message as LegacyLiveInboxMessageSnapshot;
  if (
    legacyMessage.internalClassification !== "promo" ||
    legacyMessage.isAutoReply ||
    legacyMessage.isShared ||
    legacyMessage.collaboration ||
    legacyMessage.ui_signal === "REPLY" ||
    legacyMessage.signal === "Follow-up" ||
    /^(?:re|fw|fwd):/i.test(String(legacyMessage.subject ?? "").trim()) ||
    resolveMessageNoisePolicy(legacyMessage).blocksAutoPriority
  ) {
    return message;
  }

  const contentText = [
    legacyMessage.subject,
    legacyMessage.snippet,
    ...(legacyMessage.body ?? []),
  ]
    .join(" ")
    .toLowerCase();
  const subjectAndSnippetText = [
    legacyMessage.subject,
    legacyMessage.snippet,
  ]
    .join(" ")
    .toLowerCase();
  const bodyText = (legacyMessage.body ?? []).join(" ").toLowerCase();
  const identityText = [
    legacyMessage.sender,
    legacyMessage.from,
    legacyMessage.to,
  ]
    .join(" ")
    .toLowerCase();
  const hasReminderEvidence = /\bremind(?:er|ing)?\b/.test(
    String(legacyMessage.subject ?? "").toLowerCase(),
  );
  const hasPromoEvidence = /\bpromo(?:tional)?\b/.test(contentText);
  const hasMusicIdentityEvidence = [
    "records",
    "recordings",
    "record label",
    "music",
    "dj",
  ].some((keyword) => identityText.includes(keyword));
  const hasMusicPromoContentEvidence = [
    "promo invite",
    "promo invitation",
    "dj promo",
    "promo download",
    "promo release",
    "promo track",
    "promo remix",
    "listen and download",
    "for your sets",
    "promobox",
    "inflyte",
    "fatdrop",
  ].some((keyword) => contentText.includes(keyword));
  const hasUnsafeReminderContext =
    /\b(?:invoice|payment|payout|billing|amount due|past due|contract|agreement|legal|approval|approve|signature|security|password|verification|sign-in|login|meeting|calendar|appointment|subscription|renewal)\b/.test(
      subjectAndSnippetText,
    ) ||
    /\b(?:invoice|payment|payout|billing|amount due|past due|contract|agreement|legal|signature)\b/.test(
      bodyText,
    );

  if (
    !hasReminderEvidence ||
    !hasPromoEvidence ||
    hasUnsafeReminderContext ||
    (!hasMusicIdentityEvidence && !hasMusicPromoContentEvidence)
  ) {
    return message;
  }

  return {
    ...message,
    internalClassification: "promo_reminder",
  };
}

function normalizeSnapshot(
  inboxId: string,
  snapshot: LiveInboxSnapshot,
  trustedContext?: TrustedLiveInboxSnapshotContext,
): LiveInboxSnapshot | null {
  if (
    !snapshot ||
    typeof snapshot !== "object" ||
    Array.isArray(snapshot) ||
    snapshot.classifierVersion !== MUSIC_CLASSIFIER_VERSION ||
    !isExactMailboxId(inboxId) ||
    snapshot.inboxId !== inboxId
  ) {
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

  const storedProvider = isLiveInboxProvider(snapshot.provider)
    ? snapshot.provider
    : undefined;
  if (snapshot.provider !== undefined && !storedProvider) {
    return null;
  }

  if (
    trustedContext &&
    (
      trustedContext.mailboxId !== inboxId ||
      !isLiveInboxProvider(trustedContext.provider) ||
      !isExactProviderFolder(trustedContext.folder) ||
      (storedProvider !== undefined && storedProvider !== trustedContext.provider) ||
      (snapshot.folder !== undefined && snapshot.folder !== trustedContext.folder)
    )
  ) {
    return null;
  }

  const provider = trustedContext?.provider ?? storedProvider;
  const folder = trustedContext?.folder ?? snapshot.folder ?? "INBOX";
  const isSafelyBoundLegacyNonInbox = Boolean(
    trustedContext &&
    storedProvider === undefined &&
    snapshot.folder === undefined,
  );
  if (
    provider &&
    !isExactLiveInboxFolder(folder) &&
    !isSafelyBoundLegacyNonInbox
  ) {
    return null;
  }
  const uidValidity =
    typeof snapshot.uidValidity === "string" || snapshot.uidValidity === null
      ? snapshot.uidValidity
      : undefined;
  const expectedProviderFolder =
    provider === "google" ? "Inbox" : provider === "custom_imap" ? "INBOX" : null;
  if (
    expectedProviderFolder &&
    messages.some(
      (message) =>
        (message.serverMailboxId !== undefined &&
          message.serverMailboxId !== inboxId) ||
        (message.providerFolder !== undefined &&
          message.providerFolder !== expectedProviderFolder) ||
        (provider === "custom_imap" &&
          ((message.uidValidity !== undefined &&
            message.uidValidity !== uidValidity) ||
            hasCustomImapProviderIdentityConflict(message))),
    )
  ) {
    return null;
  }
  const threadIdentityContext = provider
    ? {
        mailboxId: inboxId,
        provider,
        folder,
        uidValidity: uidValidity ?? null,
      }
    : null;
  const shouldUpconvertLegacyPromoReminders =
    typeof snapshot.schemaVersion !== "number" ||
    snapshot.schemaVersion < LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION;

  return {
    ...snapshot,
    schemaVersion: LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
    threadIdentityVersion: provider
      ? LIVE_INBOX_THREAD_IDENTITY_VERSION
      : snapshot.threadIdentityVersion,
    classifierVersion: MUSIC_CLASSIFIER_VERSION,
    provider,
    inboxId,
    email: String(snapshot.email ?? "").trim().toLowerCase(),
    fetchedAt: String(snapshot.fetchedAt ?? new Date().toISOString()),
    messages: messages.map((message) => {
      const compatibleMessage = shouldUpconvertLegacyPromoReminders
        ? upconvertLegacyPromoReminderSnapshotMessage(message)
        : message;
      const normalizedMessage = {
        ...compatibleMessage,
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
    !isLiveInboxProvider(snapshot.provider) ||
    !isExactMailboxId(snapshot.inboxId) ||
    !isExactLiveInboxFolder(snapshot.folder)
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
