import {
  PRIORITY_SEMANTIC_REASON_CODES,
  PRIORITY_SEMANTIC_REASON_CODES_BY_STATE,
  PRIORITY_SEMANTIC_STATES,
  SEMANTIC_SCHEMA_VERSION,
  resolvePrioritySemanticEffectiveState,
  type PrioritySemanticAssessment,
  type PrioritySemanticIdentity,
  type PrioritySemanticIncomingLocator,
  type PrioritySemanticReasonCode,
  type PrioritySemanticState,
} from "./prioritySemanticState";

export const PRIORITY_SEMANTIC_NEW_INBOUND_BOUNDARY_VERSION = 1;
export const PRIORITY_SEMANTIC_NEW_INBOUND_MAX_GMAIL_IDS = 512;
export const PRIORITY_SEMANTIC_NEW_INBOUND_MAX_HYDRATION_RECORDS = 64;
export const PRIORITY_SEMANTIC_NEW_INBOUND_PROMOTION_CONFIDENCE = 0.9;

const NEW_INBOUND_STORAGE_PREFIX =
  "cuevion.priority-semantic.new-inbound-boundary.v1";
const MAX_IDENTIFIER_LENGTH = 2_048;
const MAX_MAILBOX_ID_LENGTH = 512;
const MAX_CONNECTION_SCOPE_LENGTH = 2_048;
const MAX_PROVIDER_FOLDER_LENGTH = 1_024;
const MAX_RETRY_AFTER_SECONDS = 24 * 60 * 60;
const MAX_IMAP_INTEGER = 4_294_967_295;

export type PrioritySemanticNewInboundMode = "off" | "shadow";

export type PrioritySemanticNewInboundStorage = {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
};

export type PrioritySemanticNewInboundSnapshotMessage = {
  providerMessageId?: string | null;
  providerFolder?: string | null;
  uidValidity?: string | null;
  imapUid?: string | null;
};

export type PrioritySemanticNewInboundPendingCandidate = {
  mailboxId: string;
  incomingLocator: PrioritySemanticIncomingLocator;
};

export type PrioritySemanticNewInboundAssessmentRequest = {
  mailboxId: string;
  trigger: "new_inbound";
  incomingLocator: PrioritySemanticIncomingLocator;
};

export type PrioritySemanticNewInboundHydrationRequest = {
  operation: "hydrate_new_inbound";
  mailboxId: string;
};

export type PrioritySemanticNewInboundDismissalIdentity = Pick<
  PrioritySemanticIdentity,
  "conversationId" | "latestTurnId" | "semanticVersion"
>;

export type PrioritySemanticNewInboundDismissalRequest = {
  operation: "dismiss_new_inbound";
  mailboxId: string;
  identity: PrioritySemanticNewInboundDismissalIdentity;
};

export type PrioritySemanticNewInboundAssessmentSuccess = {
  ok: true;
  status: "assessed" | "cached";
  semanticTrigger: "new_inbound";
  newInboundMode: "shadow";
  priorityEffect: "observe_only";
  assessment: PrioritySemanticAssessment;
  effectiveSemanticState: PrioritySemanticState;
  identity: PrioritySemanticIdentity;
  assessedAt: string;
};

export type PrioritySemanticNewInboundAssessmentFallback = {
  ok: true;
  status: "pending" | "deferred";
  semanticTrigger: "new_inbound";
  newInboundMode: PrioritySemanticNewInboundMode;
  priorityEffect: "observe_only";
  identity: PrioritySemanticIdentity;
  retryAfterSeconds: number;
};

export type PrioritySemanticNewInboundAssessmentError = {
  ok: false;
  error: {
    code: string;
    message: string;
  };
};

export type PrioritySemanticNewInboundAssessmentResponse =
  | PrioritySemanticNewInboundAssessmentSuccess
  | PrioritySemanticNewInboundAssessmentFallback
  | PrioritySemanticNewInboundAssessmentError;

export type PrioritySemanticNewInboundHydrationRecord = {
  assessment: PrioritySemanticAssessment;
  effectiveSemanticState: PrioritySemanticState;
  priorityEffect: "observe_only";
  identity: PrioritySemanticIdentity;
  assessedAt: string;
};

export type PrioritySemanticNewInboundHydrationSuccess = {
  ok: true;
  status: "hydrated";
  semanticTrigger: "new_inbound";
  newInboundMode: PrioritySemanticNewInboundMode;
  priorityEffect: "observe_only";
  records: PrioritySemanticNewInboundHydrationRecord[];
};

export type PrioritySemanticNewInboundHydrationResponse =
  | PrioritySemanticNewInboundHydrationSuccess
  | PrioritySemanticNewInboundAssessmentError;

export type PrioritySemanticNewInboundDismissalSuccess = {
  ok: true;
  status: "dismissed";
  semanticTrigger: "new_inbound";
  newInboundMode: "shadow";
  priorityEffect: "observe_only";
  identity: PrioritySemanticIdentity;
};

export type PrioritySemanticNewInboundDismissalResponse =
  | PrioritySemanticNewInboundDismissalSuccess
  | PrioritySemanticNewInboundAssessmentError;

export type PrioritySemanticNewInboundLiveObservationIdentity = {
  mailboxId: string;
  conversationId: string;
  latestTurnId: string;
  semanticVersion: typeof SEMANTIC_SCHEMA_VERSION;
  isExactCurrentAuthoritativeInboxRow: boolean;
  isLowOrFiltered: boolean;
  isSpamTrashOrArchiveOnly: boolean;
  isNoise: boolean;
  isOrganizerExcluded: boolean;
};

type GmailBoundaryRecord = {
  version: typeof PRIORITY_SEMANTIC_NEW_INBOUND_BOUNDARY_VERSION;
  mailboxId: string;
  provider: "google";
  connectionScopeToken: string;
  providerMessageIds: string[];
};

type ImapBoundaryRecord = {
  version: typeof PRIORITY_SEMANTIC_NEW_INBOUND_BOUNDARY_VERSION;
  mailboxId: string;
  provider: "custom_imap";
  connectionScopeToken: string;
  uidValidity: string;
  highWaterUid: string;
};

type BoundaryRecord = GmailBoundaryRecord | ImapBoundaryRecord;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  requiredKeys: readonly string[],
) {
  const candidateKeys = Object.keys(value).sort();
  const expectedKeys = [...requiredKeys].sort();
  return (
    candidateKeys.length === expectedKeys.length &&
    candidateKeys.every((key, index) => key === expectedKeys[index])
  );
}

function normalizeBoundedIdentifier(value: unknown, maxLength: number) {
  if (typeof value !== "string") {
    return "";
  }
  const normalized = value.trim();
  return normalized &&
    normalized === value &&
    normalized.length <= maxLength &&
    !/[\u0000-\u001f\u007f]/.test(normalized)
    ? normalized
    : "";
}

function normalizeMailboxId(value: unknown) {
  return normalizeBoundedIdentifier(value, MAX_MAILBOX_ID_LENGTH);
}

function normalizeProviderMessageId(value: unknown) {
  return normalizeBoundedIdentifier(value, 512);
}

function normalizeProviderFolder(value: unknown) {
  return normalizeBoundedIdentifier(value, MAX_PROVIDER_FOLDER_LENGTH);
}

function normalizeImapInteger(value: unknown, allowZero = false) {
  const normalized = normalizeBoundedIdentifier(value, 10);
  const pattern = allowZero ? /^(?:0|[1-9]\d{0,9})$/ : /^[1-9]\d{0,9}$/;
  if (!pattern.test(normalized)) {
    return "";
  }
  const numericValue = Number(normalized);
  return Number.isSafeInteger(numericValue) && numericValue <= MAX_IMAP_INTEGER
    ? normalized
    : "";
}

function uniqueBoundedIdentifiers(values: readonly unknown[], limit: number) {
  const unique = new Set<string>();
  for (const value of values) {
    const identifier = normalizeProviderMessageId(value);
    if (identifier) {
      unique.add(identifier);
    }
    if (unique.size >= limit) {
      break;
    }
  }
  return [...unique];
}

function buildConnectionScopeToken(connectionScope: unknown) {
  const normalized = normalizeBoundedIdentifier(
    connectionScope,
    MAX_CONNECTION_SCOPE_LENGTH,
  );
  if (!normalized) {
    return "";
  }

  // This is a non-secret continuity token, not authentication or semantic
  // authority. Hashing avoids persisting the provider account key itself.
  const bytes = new TextEncoder().encode(normalized);
  let hash = 0xcbf29ce484222325n;
  for (const byte of bytes) {
    hash ^= BigInt(byte);
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return `fnv1a64:${hash.toString(16).padStart(16, "0")}`;
}

function parseBoundaryRecord(value: unknown): BoundaryRecord | null {
  if (!isPlainObject(value)) {
    return null;
  }
  const mailboxId = normalizeMailboxId(value.mailboxId);
  const connectionScopeToken = normalizeBoundedIdentifier(
    value.connectionScopeToken,
    64,
  );
  if (
    value.version !== PRIORITY_SEMANTIC_NEW_INBOUND_BOUNDARY_VERSION ||
    !mailboxId ||
    !connectionScopeToken
  ) {
    return null;
  }

  if (value.provider === "google") {
    if (
      !hasExactKeys(value, [
        "version",
        "mailboxId",
        "provider",
        "connectionScopeToken",
        "providerMessageIds",
      ]) ||
      !Array.isArray(value.providerMessageIds) ||
      value.providerMessageIds.length >
        PRIORITY_SEMANTIC_NEW_INBOUND_MAX_GMAIL_IDS
    ) {
      return null;
    }
    const providerMessageIds = uniqueBoundedIdentifiers(
      value.providerMessageIds,
      PRIORITY_SEMANTIC_NEW_INBOUND_MAX_GMAIL_IDS,
    );
    if (providerMessageIds.length !== value.providerMessageIds.length) {
      return null;
    }
    return {
      version: PRIORITY_SEMANTIC_NEW_INBOUND_BOUNDARY_VERSION,
      mailboxId,
      provider: "google",
      connectionScopeToken,
      providerMessageIds,
    };
  }

  if (
    value.provider !== "custom_imap" ||
    !hasExactKeys(value, [
      "version",
      "mailboxId",
      "provider",
      "connectionScopeToken",
      "uidValidity",
      "highWaterUid",
    ])
  ) {
    return null;
  }
  const uidValidity = normalizeImapInteger(value.uidValidity);
  const highWaterUid = normalizeImapInteger(value.highWaterUid, true);
  return uidValidity && highWaterUid
    ? {
        version: PRIORITY_SEMANTIC_NEW_INBOUND_BOUNDARY_VERSION,
        mailboxId,
        provider: "custom_imap",
        connectionScopeToken,
        uidValidity,
        highWaterUid,
      }
    : null;
}

function persistBoundaryRecord(
  storage: PrioritySemanticNewInboundStorage,
  storageKey: string,
  record: BoundaryRecord,
) {
  try {
    storage.setItem(storageKey, JSON.stringify(record));
    return true;
  } catch {
    return false;
  }
}

function readBoundaryRecord(
  storage: PrioritySemanticNewInboundStorage,
  storageKey: string,
) {
  try {
    const raw = storage.getItem(storageKey);
    if (raw === null) {
      return { available: true, record: null } as const;
    }
    try {
      return { available: true, record: parseBoundaryRecord(JSON.parse(raw)) } as const;
    } catch {
      return { available: true, record: null } as const;
    }
  } catch {
    return { available: false, record: null } as const;
  }
}

export function normalizePrioritySemanticNewInboundMode(
  value: unknown,
): PrioritySemanticNewInboundMode {
  return value === "shadow" ? "shadow" : "off";
}

export function buildPrioritySemanticNewInboundStorageKey(
  workspaceScope: string,
  mailboxOrderKey: string,
) {
  return `${NEW_INBOUND_STORAGE_PREFIX}:${encodeURIComponent(
    workspaceScope.trim(),
  )}:${encodeURIComponent(mailboxOrderKey.trim())}`;
}

function buildMailboxBoundaryStorageKey(
  baseStorageKey: string,
  mailboxId: string,
) {
  return `${baseStorageKey}:mailbox:${encodeURIComponent(mailboxId)}`;
}

export function buildPrioritySemanticNewInboundLocatorKey(
  locator: PrioritySemanticIncomingLocator,
) {
  return locator.provider === "google"
    ? JSON.stringify(["google", locator.providerMessageId])
    : JSON.stringify([
        "custom_imap",
        locator.providerFolder,
        locator.uidValidity,
        locator.imapUid,
      ]);
}

function observeGmailSnapshot(input: {
  storage: PrioritySemanticNewInboundStorage;
  storageKey: string;
  mailboxId: string;
  connectionScopeToken: string;
  mode: PrioritySemanticNewInboundMode;
  messages: readonly PrioritySemanticNewInboundSnapshotMessage[];
  previous: BoundaryRecord | null;
}) {
  const currentProviderMessageIds = uniqueBoundedIdentifiers(
    input.messages.map((message) => message.providerMessageId),
    PRIORITY_SEMANTIC_NEW_INBOUND_MAX_GMAIL_IDS,
  );
  const continuousRecord =
    input.previous?.provider === "google" &&
    input.previous.mailboxId === input.mailboxId &&
    input.previous.connectionScopeToken === input.connectionScopeToken
      ? input.previous
      : null;
  const previousProviderMessageIds = continuousRecord
    ? continuousRecord.providerMessageIds
    : [];
  const previousIds = new Set(previousProviderMessageIds);
  const mergedIds = uniqueBoundedIdentifiers(
    [...currentProviderMessageIds, ...previousProviderMessageIds],
    PRIORITY_SEMANTIC_NEW_INBOUND_MAX_GMAIL_IDS,
  );
  const persisted = persistBoundaryRecord(input.storage, input.storageKey, {
    version: PRIORITY_SEMANTIC_NEW_INBOUND_BOUNDARY_VERSION,
    mailboxId: input.mailboxId,
    provider: "google",
    connectionScopeToken: input.connectionScopeToken,
    providerMessageIds: mergedIds,
  });
  if (!persisted || !continuousRecord || input.mode !== "shadow") {
    return [];
  }

  return currentProviderMessageIds
    .filter((providerMessageId) => !previousIds.has(providerMessageId))
    .map((providerMessageId) => ({
      mailboxId: input.mailboxId,
      incomingLocator: { provider: "google" as const, providerMessageId },
    }));
}

function parseAuthoritativeInboxUidSet(value: unknown) {
  if (!Array.isArray(value)) {
    return null;
  }
  const normalized = value.map((uid) => normalizeImapInteger(uid));
  if (normalized.some((uid) => !uid)) {
    return null;
  }
  return [...new Set(normalized)];
}

function observeImapSnapshot(input: {
  storage: PrioritySemanticNewInboundStorage;
  storageKey: string;
  mailboxId: string;
  connectionScopeToken: string;
  mode: PrioritySemanticNewInboundMode;
  uidValidity: unknown;
  inboxUidSet: unknown;
  messages: readonly PrioritySemanticNewInboundSnapshotMessage[];
  previous: BoundaryRecord | null;
}) {
  const uidValidity = normalizeImapInteger(input.uidValidity);
  const inboxUids = parseAuthoritativeInboxUidSet(input.inboxUidSet);
  if (!uidValidity || !inboxUids) {
    return [];
  }
  const highWaterUid = inboxUids.reduce(
    (highest, uid) => Math.max(highest, Number(uid)),
    0,
  );
  const continuousRecord =
    input.previous?.provider === "custom_imap" &&
    input.previous.mailboxId === input.mailboxId &&
    input.previous.connectionScopeToken === input.connectionScopeToken &&
    input.previous.uidValidity === uidValidity
      ? input.previous
      : null;
  const previousHighWaterUid = continuousRecord
    ? Number(continuousRecord.highWaterUid)
    : highWaterUid;
  const nextHighWaterUid = Math.max(previousHighWaterUid, highWaterUid);
  const persisted = persistBoundaryRecord(input.storage, input.storageKey, {
    version: PRIORITY_SEMANTIC_NEW_INBOUND_BOUNDARY_VERSION,
    mailboxId: input.mailboxId,
    provider: "custom_imap",
    connectionScopeToken: input.connectionScopeToken,
    uidValidity,
    highWaterUid: String(nextHighWaterUid),
  });
  if (!persisted || !continuousRecord || input.mode !== "shadow") {
    return [];
  }

  const currentUidSet = new Set(inboxUids);
  const candidateUids = new Set(
    inboxUids.filter((uid) => Number(uid) > previousHighWaterUid),
  );
  const observedMessageUids = new Set(
    input.messages.flatMap((message) => {
      const providerFolder = normalizeProviderFolder(message.providerFolder);
      const messageUidValidity = normalizeImapInteger(message.uidValidity);
      const imapUid = normalizeImapInteger(message.imapUid);
      return providerFolder === "INBOX" &&
        messageUidValidity === uidValidity &&
        imapUid &&
        currentUidSet.has(imapUid) &&
        candidateUids.has(imapUid)
        ? [imapUid]
        : [];
    }),
  );
  return [...observedMessageUids]
    .sort((left, right) => Number(left) - Number(right))
    .map((imapUid) => ({
      mailboxId: input.mailboxId,
      incomingLocator: {
        provider: "custom_imap" as const,
        providerFolder: "INBOX",
        uidValidity,
        imapUid,
      },
    }));
}

/**
 * Advances the provider-identity boundary before returning any candidate.
 * The stored record is continuity metadata only and is never semantic
 * authority. Missing/corrupt/reconnected boundaries seed without backfill.
 */
export function observePrioritySemanticNewInboundSnapshot(input: {
  storage: PrioritySemanticNewInboundStorage;
  storageKey: string;
  mailboxId: string;
  connectionScope: string;
  provider: "google" | "custom_imap";
  mode: PrioritySemanticNewInboundMode | unknown;
  uidValidity?: string | null;
  inboxUidSet?: readonly string[] | null;
  messages: readonly PrioritySemanticNewInboundSnapshotMessage[];
}): PrioritySemanticNewInboundPendingCandidate[] {
  const mailboxId = normalizeMailboxId(input.mailboxId);
  const connectionScopeToken = buildConnectionScopeToken(
    input.connectionScope,
  );
  if (!mailboxId || !connectionScopeToken) {
    return [];
  }
  const mailboxStorageKey = buildMailboxBoundaryStorageKey(
    input.storageKey,
    mailboxId,
  );
  const stored = readBoundaryRecord(input.storage, mailboxStorageKey);
  if (!stored.available) {
    return [];
  }
  const mode = normalizePrioritySemanticNewInboundMode(input.mode);
  return input.provider === "google"
    ? observeGmailSnapshot({
        storage: input.storage,
        storageKey: mailboxStorageKey,
        mailboxId,
        connectionScopeToken,
        mode,
        messages: input.messages,
        previous: stored.record,
      })
    : observeImapSnapshot({
        storage: input.storage,
        storageKey: mailboxStorageKey,
        mailboxId,
        connectionScopeToken,
        mode,
        uidValidity: input.uidValidity,
        inboxUidSet: input.inboxUidSet,
        messages: input.messages,
        previous: stored.record,
      });
}

export function isPrioritySemanticNewInboundEligible(input: {
  isAuthoritativeInbox: boolean;
  isExternal: boolean;
  isLowOrFiltered: boolean;
  isSpamTrashOrArchiveOnly: boolean;
  isNoise: boolean;
  isOrganizerExcluded: boolean;
  hasActiveOpenLoop: boolean;
  hasDeterministicPriority: boolean;
  isDuplicateOrOwnMessage: boolean;
}) {
  return (
    input.isAuthoritativeInbox &&
    input.isExternal &&
    !input.isLowOrFiltered &&
    !input.isSpamTrashOrArchiveOnly &&
    !input.isNoise &&
    !input.isOrganizerExcluded &&
    !input.hasActiveOpenLoop &&
    !input.hasDeterministicPriority &&
    !input.isDuplicateOrOwnMessage
  );
}

/** Future active-promotion policy only; shadow analysis never calls this. */
export function meetsPrioritySemanticNewInboundPromotionThreshold(
  assessment: Pick<PrioritySemanticAssessment, "state" | "confidence">,
) {
  return (
    assessment.state === "needs_user_action" &&
    Number.isFinite(assessment.confidence) &&
    assessment.confidence >=
      PRIORITY_SEMANTIC_NEW_INBOUND_PROMOTION_CONFIDENCE &&
    assessment.confidence <= 1
  );
}

export function buildPrioritySemanticNewInboundWireRequest(
  value: unknown,
): PrioritySemanticNewInboundAssessmentRequest | null {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, ["mailboxId", "trigger", "incomingLocator"]) ||
    value.trigger !== "new_inbound"
  ) {
    return null;
  }
  const mailboxId = normalizeMailboxId(value.mailboxId);
  if (!mailboxId || !isPlainObject(value.incomingLocator)) {
    return null;
  }
  const locator = value.incomingLocator;
  if (
    locator.provider === "google" &&
    hasExactKeys(locator, ["provider", "providerMessageId"])
  ) {
    const providerMessageId = normalizeProviderMessageId(
      locator.providerMessageId,
    );
    return providerMessageId
      ? {
          mailboxId,
          trigger: "new_inbound",
          incomingLocator: { provider: "google", providerMessageId },
        }
      : null;
  }
  if (
    locator.provider !== "custom_imap" ||
    !hasExactKeys(locator, [
      "provider",
      "providerFolder",
      "uidValidity",
      "imapUid",
    ])
  ) {
    return null;
  }
  const providerFolder = normalizeProviderFolder(locator.providerFolder);
  const uidValidity = normalizeImapInteger(locator.uidValidity);
  const imapUid = normalizeImapInteger(locator.imapUid);
  return providerFolder === "INBOX" && uidValidity && imapUid
    ? {
        mailboxId,
        trigger: "new_inbound",
        incomingLocator: {
          provider: "custom_imap",
          providerFolder: "INBOX",
          uidValidity,
          imapUid,
        },
      }
    : null;
}

export function buildPrioritySemanticNewInboundHydrationWireRequest(
  value: unknown,
): PrioritySemanticNewInboundHydrationRequest | null {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, ["operation", "mailboxId"]) ||
    value.operation !== "hydrate_new_inbound"
  ) {
    return null;
  }
  const mailboxId = normalizeMailboxId(value.mailboxId);
  return mailboxId
    ? { operation: "hydrate_new_inbound", mailboxId }
    : null;
}

export function buildPrioritySemanticNewInboundDismissalWireRequest(
  value: unknown,
): PrioritySemanticNewInboundDismissalRequest | null {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, ["operation", "mailboxId", "identity"]) ||
    value.operation !== "dismiss_new_inbound" ||
    !isPlainObject(value.identity) ||
    !hasExactKeys(value.identity, [
      "conversationId",
      "latestTurnId",
      "semanticVersion",
    ])
  ) {
    return null;
  }
  const mailboxId = normalizeBoundedIdentifier(value.mailboxId, 256);
  const conversationId = normalizeBoundedIdentifier(
    value.identity.conversationId,
    1_024,
  );
  const latestTurnId = normalizeBoundedIdentifier(
    value.identity.latestTurnId,
    512,
  );
  if (
    !mailboxId ||
    !conversationId ||
    !latestTurnId ||
    value.identity.semanticVersion !== SEMANTIC_SCHEMA_VERSION
  ) {
    return null;
  }
  return {
    operation: "dismiss_new_inbound",
    mailboxId,
    identity: {
      conversationId,
      latestTurnId,
      semanticVersion: SEMANTIC_SCHEMA_VERSION,
    },
  };
}

export function buildPrioritySemanticNewInboundIdentityKey(
  identity: PrioritySemanticIdentity,
) {
  return JSON.stringify([
    identity.mailboxId,
    identity.conversationId,
    identity.latestTurnId,
    identity.semanticVersion,
  ]);
}

export function rememberPrioritySemanticNewInboundDismissalFence(
  fencesByMailbox: Map<string, Set<string>>,
  mailboxId: string,
  fenceKey: string,
) {
  const mailboxFences = fencesByMailbox.get(mailboxId) ?? new Set<string>();
  fencesByMailbox.set(mailboxId, mailboxFences);
  mailboxFences.add(fenceKey);
  while (
    mailboxFences.size >
    PRIORITY_SEMANTIC_NEW_INBOUND_MAX_HYDRATION_RECORDS
  ) {
    const oldestFenceKey = mailboxFences.values().next().value;
    if (typeof oldestFenceKey !== "string") {
      break;
    }
    mailboxFences.delete(oldestFenceKey);
  }
  return mailboxFences;
}

export function isPrioritySemanticNewInboundDismissalTurnCurrent(input: {
  dismissedIdentity: Pick<
    PrioritySemanticIdentity,
    "mailboxId" | "conversationId" | "latestTurnId"
  >;
  currentIdentity:
    | Pick<
        PrioritySemanticIdentity,
        "mailboxId" | "conversationId" | "latestTurnId"
      >
    | null;
}) {
  return Boolean(
    input.currentIdentity &&
      input.currentIdentity.mailboxId === input.dismissedIdentity.mailboxId &&
      input.currentIdentity.conversationId ===
        input.dismissedIdentity.conversationId &&
      input.currentIdentity.latestTurnId ===
        input.dismissedIdentity.latestTurnId,
  );
}

function isPrioritySemanticState(value: unknown): value is PrioritySemanticState {
  return PRIORITY_SEMANTIC_STATES.some((state) => state === value);
}

function isPrioritySemanticReasonCode(
  value: unknown,
): value is PrioritySemanticReasonCode {
  return PRIORITY_SEMANTIC_REASON_CODES.some((reasonCode) => reasonCode === value);
}

function parseIdentity(value: unknown): PrioritySemanticIdentity | null {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "mailboxId",
      "conversationId",
      "latestTurnId",
      "semanticVersion",
    ])
  ) {
    return null;
  }
  const mailboxId = normalizeMailboxId(value.mailboxId);
  const conversationId = normalizeBoundedIdentifier(value.conversationId, 4_096);
  const latestTurnId = normalizeBoundedIdentifier(
    value.latestTurnId,
    MAX_IDENTIFIER_LENGTH,
  );
  return mailboxId &&
    conversationId &&
    latestTurnId &&
    value.semanticVersion === SEMANTIC_SCHEMA_VERSION
    ? {
        mailboxId,
        conversationId,
        latestTurnId,
        semanticVersion: SEMANTIC_SCHEMA_VERSION,
      }
    : null;
}

function parseError(
  value: unknown,
): PrioritySemanticNewInboundAssessmentError | null {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, ["ok", "error"]) ||
    value.ok !== false ||
    !isPlainObject(value.error) ||
    !hasExactKeys(value.error, ["code", "message"])
  ) {
    return null;
  }
  const code = normalizeBoundedIdentifier(value.error.code, 128);
  const message = normalizeBoundedIdentifier(value.error.message, 2_048);
  return code && message ? { ok: false, error: { code, message } } : null;
}

function normalizeIsoTimestamp(value: unknown) {
  const timestamp = normalizeBoundedIdentifier(value, 64);
  const timestampMs = timestamp ? new Date(timestamp).getTime() : Number.NaN;
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(
    timestamp,
  ) && Number.isFinite(timestampMs) && timestampMs > 0
    ? timestamp
    : "";
}

function parseHydrationRecord(
  value: unknown,
): PrioritySemanticNewInboundHydrationRecord | null {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "assessment",
      "effectiveSemanticState",
      "priorityEffect",
      "identity",
      "assessedAt",
    ]) ||
    value.priorityEffect !== "observe_only" ||
    !isPlainObject(value.assessment) ||
    !hasExactKeys(value.assessment, ["state", "confidence", "reasonCode"])
  ) {
    return null;
  }
  const state = value.assessment.state;
  const confidence = value.assessment.confidence;
  const reasonCode = value.assessment.reasonCode;
  const effectiveSemanticState = value.effectiveSemanticState;
  const identity = parseIdentity(value.identity);
  const assessedAt = normalizeIsoTimestamp(value.assessedAt);
  if (
    !isPrioritySemanticState(state) ||
    typeof confidence !== "number" ||
    !Number.isFinite(confidence) ||
    confidence < 0 ||
    confidence > 1 ||
    !isPrioritySemanticReasonCode(reasonCode) ||
    !PRIORITY_SEMANTIC_REASON_CODES_BY_STATE[state].includes(reasonCode) ||
    !isPrioritySemanticState(effectiveSemanticState) ||
    effectiveSemanticState !==
      resolvePrioritySemanticEffectiveState({ state, confidence, reasonCode }) ||
    !identity ||
    !assessedAt
  ) {
    return null;
  }
  return {
    assessment: { state, confidence, reasonCode },
    effectiveSemanticState,
    priorityEffect: "observe_only",
    identity,
    assessedAt,
  };
}

export function parsePrioritySemanticNewInboundHydrationResponse(
  value: unknown,
): PrioritySemanticNewInboundHydrationResponse | null {
  const parsedError = parseError(value);
  if (parsedError) {
    return parsedError;
  }
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "ok",
      "status",
      "semanticTrigger",
      "newInboundMode",
      "priorityEffect",
      "records",
    ]) ||
    value.ok !== true ||
    value.status !== "hydrated" ||
    value.semanticTrigger !== "new_inbound" ||
    value.priorityEffect !== "observe_only" ||
    (value.newInboundMode !== "off" && value.newInboundMode !== "shadow") ||
    !Array.isArray(value.records) ||
    value.records.length >
      PRIORITY_SEMANTIC_NEW_INBOUND_MAX_HYDRATION_RECORDS ||
    (value.newInboundMode === "off" && value.records.length !== 0)
  ) {
    return null;
  }
  const records = value.records.map(parseHydrationRecord);
  if (records.some((record) => record === null)) {
    return null;
  }
  const conversationKeys = new Set<string>();
  for (const record of records as PrioritySemanticNewInboundHydrationRecord[]) {
    const conversationKey = JSON.stringify([
      record.identity.mailboxId,
      record.identity.conversationId,
    ]);
    if (conversationKeys.has(conversationKey)) {
      return null;
    }
    conversationKeys.add(conversationKey);
  }
  return {
    ok: true,
    status: "hydrated",
    semanticTrigger: "new_inbound",
    newInboundMode: value.newInboundMode,
    priorityEffect: "observe_only",
    records: records as PrioritySemanticNewInboundHydrationRecord[],
  };
}

export function parsePrioritySemanticNewInboundDismissalResponse(
  value: unknown,
): PrioritySemanticNewInboundDismissalResponse | null {
  const parsedError = parseError(value);
  if (parsedError) {
    return parsedError;
  }
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "ok",
      "status",
      "semanticTrigger",
      "newInboundMode",
      "priorityEffect",
      "identity",
    ]) ||
    value.ok !== true ||
    value.status !== "dismissed" ||
    value.semanticTrigger !== "new_inbound" ||
    value.newInboundMode !== "shadow" ||
    value.priorityEffect !== "observe_only"
  ) {
    return null;
  }
  const identity = parseIdentity(value.identity);
  return identity
    ? {
        ok: true,
        status: "dismissed",
        semanticTrigger: "new_inbound",
        newInboundMode: "shadow",
        priorityEffect: "observe_only",
        identity,
      }
    : null;
}

export function isPrioritySemanticNewInboundHydratedObservationCurrent(input: {
  record: PrioritySemanticNewInboundHydrationRecord;
  liveIdentity: PrioritySemanticNewInboundLiveObservationIdentity;
}) {
  const { identity } = input.record;
  const liveIdentity = input.liveIdentity;
  return (
    input.record.priorityEffect === "observe_only" &&
    identity.semanticVersion === SEMANTIC_SCHEMA_VERSION &&
    liveIdentity.semanticVersion === SEMANTIC_SCHEMA_VERSION &&
    identity.mailboxId === liveIdentity.mailboxId &&
    identity.conversationId === liveIdentity.conversationId &&
    identity.latestTurnId === liveIdentity.latestTurnId &&
    liveIdentity.isExactCurrentAuthoritativeInboxRow &&
    !liveIdentity.isLowOrFiltered &&
    !liveIdentity.isSpamTrashOrArchiveOnly &&
    !liveIdentity.isNoise &&
    !liveIdentity.isOrganizerExcluded
  );
}

export function parsePrioritySemanticNewInboundResponse(
  value: unknown,
): PrioritySemanticNewInboundAssessmentResponse | null {
  const parsedError = parseError(value);
  if (parsedError) {
    return parsedError;
  }
  if (
    !isPlainObject(value) ||
    value.ok !== true ||
    value.semanticTrigger !== "new_inbound" ||
    value.priorityEffect !== "observe_only" ||
    (value.newInboundMode !== "off" && value.newInboundMode !== "shadow")
  ) {
    return null;
  }
  const identity = parseIdentity(value.identity);
  if (!identity) {
    return null;
  }

  if (value.status === "pending" || value.status === "deferred") {
    if (
      !hasExactKeys(value, [
        "ok",
        "status",
        "semanticTrigger",
        "newInboundMode",
        "priorityEffect",
        "identity",
        "retryAfterSeconds",
      ]) ||
      (value.newInboundMode === "off" && value.status !== "deferred") ||
      typeof value.retryAfterSeconds !== "number" ||
      !Number.isInteger(value.retryAfterSeconds) ||
      value.retryAfterSeconds < 0 ||
      value.retryAfterSeconds > MAX_RETRY_AFTER_SECONDS
    ) {
      return null;
    }
    return {
      ok: true,
      status: value.status,
      semanticTrigger: "new_inbound",
      newInboundMode: value.newInboundMode,
      priorityEffect: "observe_only",
      identity,
      retryAfterSeconds: value.retryAfterSeconds,
    };
  }

  if (
    (value.status !== "assessed" && value.status !== "cached") ||
    value.newInboundMode !== "shadow" ||
    !hasExactKeys(value, [
      "ok",
      "status",
      "semanticTrigger",
      "newInboundMode",
      "priorityEffect",
      "assessment",
      "effectiveSemanticState",
      "identity",
      "assessedAt",
    ]) ||
    !isPlainObject(value.assessment) ||
    !hasExactKeys(value.assessment, ["state", "confidence", "reasonCode"])
  ) {
    return null;
  }
  const state = value.assessment.state;
  const confidence = value.assessment.confidence;
  const reasonCode = value.assessment.reasonCode;
  const effectiveSemanticState = value.effectiveSemanticState;
  const assessedAt = normalizeIsoTimestamp(value.assessedAt);
  if (
    !isPrioritySemanticState(state) ||
    typeof confidence !== "number" ||
    !Number.isFinite(confidence) ||
    confidence < 0 ||
    confidence > 1 ||
    !isPrioritySemanticReasonCode(reasonCode) ||
    !PRIORITY_SEMANTIC_REASON_CODES_BY_STATE[state].includes(reasonCode) ||
    !isPrioritySemanticState(effectiveSemanticState) ||
    effectiveSemanticState !==
      resolvePrioritySemanticEffectiveState({ state, confidence, reasonCode }) ||
    !assessedAt
  ) {
    return null;
  }
  return {
    ok: true,
    status: value.status,
    semanticTrigger: "new_inbound",
    newInboundMode: "shadow",
    priorityEffect: "observe_only",
    assessment: { state, confidence, reasonCode },
    effectiveSemanticState,
    identity,
    assessedAt,
  };
}
