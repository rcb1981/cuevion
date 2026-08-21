import {
  resolveCanonicalConversationIdentity,
  resolveMessageDateMs,
  type RenderedConversationMessage,
} from "./inboxEngine";
import type {
  WaitingConversationState,
  WaitingOnOtherStore,
} from "./waitingOnOther";

export const SEMANTIC_SCHEMA_VERSION = "priority-semantic-state-v1";
export const PRIORITY_SEMANTIC_AUTHORED_TEXT_MAX_CODE_POINTS = 12_000;
export const PRIORITY_SEMANTIC_EVENT_REF_MAX_LENGTH = 8_192;
export const PRIORITY_SEMANTIC_EVENT_REF_MAX_AGE_MS =
  14 * 24 * 60 * 60 * 1000;
export const PRIORITY_SEMANTIC_EVENT_REF_STORE_MAX_RECORDS = 256;
export const PRIORITY_SEMANTIC_SHADOW_OBSERVATION_MAX_RECORDS = 256;
export const PRIORITY_SEMANTIC_REQUESTED_TRIGGER_MAX_KEYS = 512;
export const PRIORITY_SEMANTIC_PENDING_RETURNED_TRIGGER_MAX_RECORDS = 256;

export const PRIORITY_SEMANTIC_STATES = [
  "needs_user_action",
  "waiting_on_other",
  "resolved",
  "informational",
  "uncertain",
] as const;

export type PrioritySemanticState =
  (typeof PRIORITY_SEMANTIC_STATES)[number];

export const PRIORITY_SEMANTIC_REASON_CODES = [
  "explicit_request",
  "implicit_request",
  "mixed_acknowledgement_with_request",
  "user_owns_next_action",
  "external_owns_next_action",
  "user_handed_off_action",
  "awaiting_confirmation",
  "awaiting_approval",
  "completed_confirmation",
  "closing_acknowledgement",
  "informational_update",
  "ambiguous_context",
] as const;

export type PrioritySemanticReasonCode =
  (typeof PRIORITY_SEMANTIC_REASON_CODES)[number];

export const PRIORITY_SEMANTIC_REASON_CODES_BY_STATE: Readonly<
  Record<PrioritySemanticState, readonly PrioritySemanticReasonCode[]>
> = {
  needs_user_action: [
    "explicit_request",
    "implicit_request",
    "mixed_acknowledgement_with_request",
    "user_owns_next_action",
  ],
  waiting_on_other: [
    "external_owns_next_action",
    "user_handed_off_action",
    "awaiting_confirmation",
    "awaiting_approval",
  ],
  resolved: ["closing_acknowledgement", "completed_confirmation"],
  informational: ["informational_update"],
  uncertain: ["ambiguous_context"],
};

export const PRIORITY_SEMANTIC_CONFIDENCE_THRESHOLDS: Readonly<
  Record<Exclude<PrioritySemanticState, "uncertain">, number>
> = {
  needs_user_action: 0.8,
  waiting_on_other: 0.82,
  resolved: 0.97,
  informational: 0.93,
};

export type PrioritySemanticAssessment = {
  state: PrioritySemanticState;
  confidence: number;
  reasonCode: PrioritySemanticReasonCode;
};

export type PrioritySemanticIdentity = {
  mailboxId: string;
  conversationId: string;
  latestTurnId: string;
  semanticVersion: typeof SEMANTIC_SCHEMA_VERSION;
};

export type PrioritySemanticOutgoingAssessmentRequest = {
  mailboxId: string;
  trigger: "outgoing_reply";
  eventRef: string;
  authoredText: string;
};

export type PrioritySemanticIncomingLocator =
  | {
      provider: "google";
      providerMessageId: string;
    }
  | {
      provider: "custom_imap";
      providerFolder: string;
      uidValidity: string;
      imapUid: string;
    };

export type PrioritySemanticIncomingAssessmentRequest =
  | {
      mailboxId: string;
      trigger: "incoming_reply";
      activeEventRef: string;
      incomingLocator: Extract<
        PrioritySemanticIncomingLocator,
        { provider: "google" }
      >;
    }
  | {
      mailboxId: string;
      trigger: "incoming_reply";
      incomingLocator: Extract<
        PrioritySemanticIncomingLocator,
        { provider: "custom_imap" }
      >;
    };

export type PrioritySemanticAssessmentRequest =
  | PrioritySemanticOutgoingAssessmentRequest
  | PrioritySemanticIncomingAssessmentRequest;

export type PrioritySemanticAssessmentSuccess = {
  ok: true;
  status: "assessed" | "cached";
  assessment: PrioritySemanticAssessment;
  effectiveSemanticState: PrioritySemanticState;
  identity: PrioritySemanticIdentity;
  activeEventRef?: string;
  assessedAt: string;
};

export type PrioritySemanticAssessmentFallback = {
  ok: true;
  status: "pending" | "deferred";
  identity: PrioritySemanticIdentity;
  retryAfterSeconds: number;
};

export type PrioritySemanticAssessmentError = {
  ok: false;
  error: {
    code: string;
    message: string;
  };
};

export type PrioritySemanticAssessmentResponse =
  | PrioritySemanticAssessmentSuccess
  | PrioritySemanticAssessmentFallback
  | PrioritySemanticAssessmentError;

export type PrioritySemanticShadowObservation = {
  status: "assessed" | "cached" | "pending" | "deferred";
  state?: PrioritySemanticState;
  effectiveSemanticState?: PrioritySemanticState;
  confidence?: number;
  reasonCode?: PrioritySemanticReasonCode;
  assessedAt?: string;
  isShadow: true;
};

export type PrioritySemanticActiveEventRefRecord = {
  mailboxId: string;
  conversationId: string;
  activeEventRef: string;
  recordedAt: string;
};

export type PrioritySemanticActiveEventRefStore = Record<
  string,
  PrioritySemanticActiveEventRefRecord
>;

export type PrioritySemanticStorage = {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
};

export type PrioritySemanticReturnedReplyTrigger = {
  mailboxId: string;
  conversationId: string;
  latestTurnId: string;
  returnedMessageKey: string;
  incomingLocator: PrioritySemanticIncomingLocator;
};

export function addPrioritySemanticShadowObservation(
  observations: Record<string, PrioritySemanticShadowObservation>,
  observationKey: string,
  observation: PrioritySemanticShadowObservation,
) {
  const nextObservations = { ...observations };
  delete nextObservations[observationKey];
  nextObservations[observationKey] = observation;
  const nextEntries = Object.entries(nextObservations).slice(
    -PRIORITY_SEMANTIC_SHADOW_OBSERVATION_MAX_RECORDS,
  );
  return Object.fromEntries(nextEntries);
}

export function rememberPrioritySemanticRequestedTriggerKey(
  requestedKeys: Set<string>,
  triggerKey: string,
) {
  if (requestedKeys.has(triggerKey)) {
    return false;
  }

  requestedKeys.add(triggerKey);
  while (requestedKeys.size > PRIORITY_SEMANTIC_REQUESTED_TRIGGER_MAX_KEYS) {
    const oldestKey = requestedKeys.values().next().value as string | undefined;
    if (oldestKey === undefined) {
      break;
    }
    requestedKeys.delete(oldestKey);
  }
  return true;
}

export function rememberPrioritySemanticPendingReturnedReplyTrigger(
  pendingTriggers: Map<string, PrioritySemanticReturnedReplyTrigger>,
  pendingKey: string,
  trigger: PrioritySemanticReturnedReplyTrigger,
) {
  rememberPrioritySemanticPendingTrigger(
    pendingTriggers,
    pendingKey,
    trigger,
  );
}

export function rememberPrioritySemanticPendingTrigger<T>(
  pendingTriggers: Map<string, T>,
  pendingKey: string,
  trigger: T,
) {
  pendingTriggers.delete(pendingKey);
  pendingTriggers.set(pendingKey, trigger);
  while (
    pendingTriggers.size >
    PRIORITY_SEMANTIC_PENDING_RETURNED_TRIGGER_MAX_RECORDS
  ) {
    const oldestKey = pendingTriggers.keys().next().value as string | undefined;
    if (oldestKey === undefined) {
      break;
    }
    pendingTriggers.delete(oldestKey);
  }
}

type PrioritySemanticInboundMessage = RenderedConversationMessage & {
  providerFolder?: string | null;
  providerMessageId?: string | null;
  rfcMessageId?: string | null;
  imapUid?: string | null;
  uidValidity?: string | null;
};

type PrioritySemanticInboundEntry<T extends PrioritySemanticInboundMessage> = {
  mailboxId: string;
  message: T;
};

const SIMPLE_IDENTIFIER_MAX_LENGTH = 2_048;
const PROVIDER_FOLDER_MAX_LENGTH = 1_024;

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

function hasOnlyKeys(
  value: Record<string, unknown>,
  allowedKeys: readonly string[],
) {
  const allowedKeySet = new Set(allowedKeys);
  return Object.keys(value).every((key) => allowedKeySet.has(key));
}

function normalizeBoundedIdentifier(value: unknown, maxLength = SIMPLE_IDENTIFIER_MAX_LENGTH) {
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

function normalizeIsoTimestamp(value: unknown) {
  const timestamp = normalizeBoundedIdentifier(value, 64);
  const timestampMs = timestamp ? new Date(timestamp).getTime() : Number.NaN;

  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(
    timestamp,
  ) &&
    Number.isFinite(timestampMs) &&
    timestampMs > 0
    ? timestamp
    : "";
}

export function normalizePrioritySemanticEventRef(value: unknown) {
  if (typeof value !== "string") {
    return "";
  }

  return value.length > 0 &&
    value.length <= PRIORITY_SEMANTIC_EVENT_REF_MAX_LENGTH &&
    value === value.trim() &&
    /^[\x20-\x7e]+$/.test(value)
    ? value
    : "";
}

export function normalizePrioritySemanticLatestTurnId(value: unknown) {
  const normalized = normalizeBoundedIdentifier(value);
  if (!normalized) {
    return "";
  }

  return normalized.startsWith("<") && normalized.endsWith(">")
    ? normalized.slice(1, -1).trim()
    : normalized;
}

export function normalizePrioritySemanticAuthoredText(value: unknown) {
  if (typeof value !== "string") {
    return "";
  }

  const normalized = value
    .normalize("NFKC")
    .replace(/\r\n?/g, "\n")
    .trim();
  return Array.from(normalized).length <=
    PRIORITY_SEMANTIC_AUTHORED_TEXT_MAX_CODE_POINTS
    ? normalized
    : "";
}

function normalizeMailboxId(value: unknown) {
  return normalizeBoundedIdentifier(value, 512);
}

function normalizeConversationId(value: unknown) {
  return normalizeBoundedIdentifier(value, 4_096);
}

function normalizeProviderMessageId(value: unknown) {
  return normalizeBoundedIdentifier(value, 512);
}

function normalizeProviderFolder(value: unknown) {
  return normalizeBoundedIdentifier(value, PROVIDER_FOLDER_MAX_LENGTH);
}

function normalizeImapInteger(value: unknown) {
  const normalized = normalizeBoundedIdentifier(value, 32);
  if (!/^[1-9]\d{0,9}$/.test(normalized)) {
    return "";
  }

  const numericValue = Number(normalized);
  return Number.isSafeInteger(numericValue) && numericValue <= 4_294_967_295
    ? normalized
    : "";
}

function normalizePrioritySemanticIncomingLocator(
  value: unknown,
): PrioritySemanticIncomingLocator | null {
  if (!isPlainObject(value) || typeof value.provider !== "string") {
    return null;
  }

  if (value.provider === "google") {
    if (!hasExactKeys(value, ["provider", "providerMessageId"])) {
      return null;
    }
    const providerMessageId = normalizeProviderMessageId(value.providerMessageId);
    return providerMessageId
      ? { provider: "google", providerMessageId }
      : null;
  }

  if (value.provider === "custom_imap") {
    if (
      !hasExactKeys(value, [
        "provider",
        "providerFolder",
        "uidValidity",
        "imapUid",
      ])
    ) {
      return null;
    }
    const providerFolder = normalizeProviderFolder(value.providerFolder);
    const uidValidity = normalizeImapInteger(value.uidValidity);
    const imapUid = normalizeImapInteger(value.imapUid);
    return providerFolder && uidValidity && imapUid
      ? {
          provider: "custom_imap",
          providerFolder,
          uidValidity,
          imapUid,
        }
      : null;
  }

  return null;
}

/**
 * Selects the complete request body so browser-held workspace/conversation
 * authority and incoming message text can never leak into the wire payload.
 */
export function buildPrioritySemanticAssessmentWireRequest(
  value: unknown,
): PrioritySemanticAssessmentRequest | null {
  if (!isPlainObject(value)) {
    return null;
  }

  const mailboxId = normalizeMailboxId(value.mailboxId);
  if (!mailboxId) {
    return null;
  }

  if (value.trigger === "outgoing_reply") {
    if (
      !hasExactKeys(value, [
        "mailboxId",
        "trigger",
        "eventRef",
        "authoredText",
      ])
    ) {
      return null;
    }
    const eventRef = normalizePrioritySemanticEventRef(value.eventRef);
    const authoredText = normalizePrioritySemanticAuthoredText(value.authoredText);
    return eventRef && authoredText
      ? { mailboxId, trigger: "outgoing_reply", eventRef, authoredText }
      : null;
  }

  if (value.trigger === "incoming_reply") {
    const incomingLocator = normalizePrioritySemanticIncomingLocator(
      value.incomingLocator,
    );
    if (!incomingLocator) {
      return null;
    }
    if (incomingLocator.provider === "google") {
      if (
        !hasExactKeys(value, [
          "mailboxId",
          "trigger",
          "activeEventRef",
          "incomingLocator",
        ])
      ) {
        return null;
      }
      const activeEventRef = normalizePrioritySemanticEventRef(
        value.activeEventRef,
      );
      return activeEventRef
        ? {
            mailboxId,
            trigger: "incoming_reply",
            activeEventRef,
            incomingLocator,
          }
        : null;
    }
    return hasExactKeys(value, ["mailboxId", "trigger", "incomingLocator"])
      ? { mailboxId, trigger: "incoming_reply", incomingLocator }
      : null;
  }

  return null;
}

function isPrioritySemanticState(value: unknown): value is PrioritySemanticState {
  return PRIORITY_SEMANTIC_STATES.some((state) => state === value);
}

function isPrioritySemanticReasonCode(
  value: unknown,
): value is PrioritySemanticReasonCode {
  return PRIORITY_SEMANTIC_REASON_CODES.some((reasonCode) => reasonCode === value);
}

export function isPrioritySemanticStateReasonCodePair(
  state: PrioritySemanticState,
  reasonCode: PrioritySemanticReasonCode,
) {
  return PRIORITY_SEMANTIC_REASON_CODES_BY_STATE[state].includes(reasonCode);
}

function parsePrioritySemanticIdentity(
  value: unknown,
): PrioritySemanticIdentity | null {
  if (!isPlainObject(value)) {
    return null;
  }

  const mailboxId = normalizeMailboxId(value.mailboxId);
  const conversationId = normalizeConversationId(value.conversationId);
  const latestTurnId = normalizeBoundedIdentifier(value.latestTurnId);
  return hasExactKeys(value, [
    "mailboxId",
    "conversationId",
    "latestTurnId",
    "semanticVersion",
  ]) &&
    mailboxId &&
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

function parsePrioritySemanticError(
  value: unknown,
): PrioritySemanticAssessmentError | null {
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

export function parsePrioritySemanticAssessmentResponse(
  value: unknown,
): PrioritySemanticAssessmentResponse | null {
  const parsedError = parsePrioritySemanticError(value);
  if (parsedError) {
    return parsedError;
  }
  if (!isPlainObject(value) || value.ok !== true) {
    return null;
  }

  const identity = parsePrioritySemanticIdentity(value.identity);
  if (!identity) {
    return null;
  }

  if (value.status === "pending" || value.status === "deferred") {
    if (
      !hasExactKeys(value, [
        "ok",
        "status",
        "identity",
        "retryAfterSeconds",
      ])
    ) {
      return null;
    }
    const retryAfterSeconds = value.retryAfterSeconds;
    return typeof retryAfterSeconds === "number" &&
      Number.isInteger(retryAfterSeconds) &&
      retryAfterSeconds >= 0 &&
      retryAfterSeconds <= 24 * 60 * 60
      ? {
          ok: true,
          status: value.status,
          identity,
          retryAfterSeconds,
        }
      : null;
  }

  if (
    (value.status !== "assessed" && value.status !== "cached") ||
    !isPlainObject(value.assessment) ||
    !hasOnlyKeys(value, [
      "ok",
      "status",
      "assessment",
      "effectiveSemanticState",
      "identity",
      "activeEventRef",
      "assessedAt",
    ]) ||
    !hasExactKeys(value.assessment, ["state", "confidence", "reasonCode"])
  ) {
    return null;
  }

  const state = value.assessment.state;
  const confidence = value.assessment.confidence;
  const reasonCode = value.assessment.reasonCode;
  const assessedAt = normalizeIsoTimestamp(value.assessedAt);
  if (
    !isPrioritySemanticState(state) ||
    typeof confidence !== "number" ||
    !Number.isFinite(confidence) ||
    confidence < 0 ||
    confidence > 1 ||
    !isPrioritySemanticReasonCode(reasonCode) ||
    !isPrioritySemanticStateReasonCodePair(state, reasonCode) ||
    !assessedAt
  ) {
    return null;
  }

  const hasActiveEventRef = Object.prototype.hasOwnProperty.call(
    value,
    "activeEventRef",
  );
  const activeEventRef = normalizePrioritySemanticEventRef(value.activeEventRef);
  if (hasActiveEventRef && !activeEventRef) {
    return null;
  }
  if (!isPrioritySemanticState(value.effectiveSemanticState)) {
    return null;
  }

  return {
    ok: true,
    status: value.status,
    assessment: { state, confidence, reasonCode },
    effectiveSemanticState: value.effectiveSemanticState,
    identity,
    ...(activeEventRef ? { activeEventRef } : {}),
    assessedAt,
  };
}

export function resolvePrioritySemanticEffectiveState(
  assessment: PrioritySemanticAssessment,
): PrioritySemanticState {
  if (assessment.state === "uncertain") {
    return "uncertain";
  }

  return assessment.confidence >=
    PRIORITY_SEMANTIC_CONFIDENCE_THRESHOLDS[assessment.state]
    ? assessment.state
    : "uncertain";
}

export function projectPrioritySemanticShadowObservation(
  response: PrioritySemanticAssessmentResponse,
  expectedIdentity: Omit<PrioritySemanticIdentity, "semanticVersion">,
): PrioritySemanticShadowObservation | null {
  if (!response.ok) {
    return null;
  }
  if (
    response.identity.mailboxId !== expectedIdentity.mailboxId ||
    response.identity.conversationId !== expectedIdentity.conversationId ||
    response.identity.latestTurnId !==
      normalizePrioritySemanticLatestTurnId(expectedIdentity.latestTurnId)
  ) {
    return null;
  }

  if (response.status === "pending" || response.status === "deferred") {
    return { status: response.status, isShadow: true };
  }
  if (!("assessment" in response)) {
    return null;
  }

  const locallyProjectedState = resolvePrioritySemanticEffectiveState(
    response.assessment,
  );
  const effectiveSemanticState =
    response.effectiveSemanticState === undefined ||
    response.effectiveSemanticState === locallyProjectedState
      ? locallyProjectedState
      : "uncertain";

  return {
    status: response.status,
    state: response.assessment.state,
    effectiveSemanticState,
    confidence: response.assessment.confidence,
    reasonCode: response.assessment.reasonCode,
    assessedAt: response.assessedAt,
    isShadow: true,
  };
}

function buildPrioritySemanticActiveEventRefStoreKey(
  mailboxId: string,
  conversationId: string,
) {
  return `${encodeURIComponent(mailboxId)}::${encodeURIComponent(conversationId)}`;
}

export function buildPrioritySemanticActiveEventRefStorageKey(
  workspaceScope: string,
  orderedMailboxKey: string,
) {
  return [
    "cuevion-priority-semantic-shadow-event-refs",
    encodeURIComponent(workspaceScope),
    encodeURIComponent(orderedMailboxKey),
  ].join(":");
}

function normalizePrioritySemanticActiveEventRefRecord(
  value: unknown,
  nowMs: number,
): PrioritySemanticActiveEventRefRecord | null {
  if (!isPlainObject(value)) {
    return null;
  }

  const mailboxId = normalizeMailboxId(value.mailboxId);
  const conversationId = normalizeConversationId(value.conversationId);
  const activeEventRef = normalizePrioritySemanticEventRef(value.activeEventRef);
  const recordedAt = normalizeIsoTimestamp(value.recordedAt);
  const recordedAtMs = recordedAt ? new Date(recordedAt).getTime() : Number.NaN;
  return mailboxId &&
    conversationId &&
    activeEventRef &&
    Number.isFinite(recordedAtMs) &&
    recordedAtMs <= nowMs + 5 * 60 * 1000 &&
    nowMs - recordedAtMs <= PRIORITY_SEMANTIC_EVENT_REF_MAX_AGE_MS
    ? {
        mailboxId,
        conversationId,
        activeEventRef,
        recordedAt,
      }
    : null;
}

export function normalizePrioritySemanticActiveEventRefStore(
  value: unknown,
  nowMs = Date.now(),
): PrioritySemanticActiveEventRefStore {
  if (!isPlainObject(value)) {
    return {};
  }

  const records = Object.values(value)
    .map((candidate) =>
      normalizePrioritySemanticActiveEventRefRecord(candidate, nowMs),
    )
    .filter(
      (candidate): candidate is PrioritySemanticActiveEventRefRecord =>
        candidate !== null,
    )
    .sort(
      (left, right) =>
        new Date(right.recordedAt).getTime() -
        new Date(left.recordedAt).getTime(),
    )
    .slice(0, PRIORITY_SEMANTIC_EVENT_REF_STORE_MAX_RECORDS);

  return Object.fromEntries(
    records.map((record) => [
      buildPrioritySemanticActiveEventRefStoreKey(
        record.mailboxId,
        record.conversationId,
      ),
      record,
    ]),
  );
}

export function readPrioritySemanticActiveEventRefStore(
  storage: Pick<PrioritySemanticStorage, "getItem">,
  storageKey: string,
  nowMs = Date.now(),
) {
  try {
    const storedValue = storage.getItem(storageKey);
    return storedValue
      ? normalizePrioritySemanticActiveEventRefStore(
          JSON.parse(storedValue),
          nowMs,
        )
      : {};
  } catch {
    return {};
  }
}

export function persistPrioritySemanticActiveEventRefStore(
  storage: Pick<PrioritySemanticStorage, "setItem">,
  storageKey: string,
  store: PrioritySemanticActiveEventRefStore,
  nowMs = Date.now(),
) {
  const normalizedStore = normalizePrioritySemanticActiveEventRefStore(
    store,
    nowMs,
  );
  try {
    storage.setItem(storageKey, JSON.stringify(normalizedStore));
  } catch {
    // The normalized in-memory transport remains usable if browser storage is
    // disabled or full. This helper must never surface a post-send exception.
  }
  return normalizedStore;
}

export function recordPrioritySemanticActiveEventRef(
  store: PrioritySemanticActiveEventRefStore,
  record: PrioritySemanticActiveEventRefRecord,
  nowMs = Date.now(),
) {
  return normalizePrioritySemanticActiveEventRefStore(
    {
      ...store,
      [buildPrioritySemanticActiveEventRefStoreKey(
        record.mailboxId,
        record.conversationId,
      )]: record,
    },
    nowMs,
  );
}

export function resolvePrioritySemanticActiveEventRef(
  store: PrioritySemanticActiveEventRefStore,
  mailboxId: string,
  conversationId: string,
  nowMs = Date.now(),
) {
  const normalizedStore = normalizePrioritySemanticActiveEventRefStore(
    store,
    nowMs,
  );
  return (
    normalizedStore[
      buildPrioritySemanticActiveEventRefStoreKey(mailboxId, conversationId)
    ] ?? null
  );
}

function indexWaitingConversationState(store: WaitingOnOtherStore) {
  return new Map(
    Object.values(store).map((record) => [
      `${record.mailboxId}::${record.conversationKey}`,
      record,
    ]),
  );
}

function isNewReturnedReplyTransition(
  previous: WaitingConversationState | undefined,
  next: WaitingConversationState,
) {
  if (!previous || next.state !== "returned_reply") {
    return false;
  }
  if (
    previous.mailboxId !== next.mailboxId ||
    previous.conversationKey !== next.conversationKey ||
    previous.transitionedAt !== next.transitionedAt
  ) {
    return false;
  }
  if (previous.state === "waiting_on_other") {
    return true;
  }

  const previousReturnedAt = new Date(previous.returnedReplyAt).getTime();
  const nextReturnedAt = new Date(next.returnedReplyAt).getTime();
  return (
    Number.isFinite(previousReturnedAt) &&
    Number.isFinite(nextReturnedAt) &&
    nextReturnedAt > previousReturnedAt &&
    next.returnedMessageKey !== previous.returnedMessageKey
  );
}

function buildIncomingLocator(
  message: PrioritySemanticInboundMessage,
): { locator: PrioritySemanticIncomingLocator; latestTurnId: string } | null {
  const provider = message.threadIdentityContext?.provider;
  if (provider === "google") {
    const providerMessageId = normalizeProviderMessageId(
      message.providerMessageId,
    );
    return providerMessageId
      ? {
          locator: { provider: "google", providerMessageId },
          latestTurnId: providerMessageId,
        }
      : null;
  }

  if (provider === "custom_imap") {
    const providerFolder = normalizeProviderFolder(
      message.threadIdentityContext?.folder,
    );
    const attachedProviderFolder = message.providerFolder
      ? normalizeProviderFolder(message.providerFolder)
      : providerFolder;
    const uidValidity = normalizeImapInteger(
      message.threadIdentityContext?.uidValidity,
    );
    const attachedUidValidity = message.uidValidity
      ? normalizeImapInteger(message.uidValidity)
      : uidValidity;
    const imapUid = normalizeImapInteger(message.imapUid);
    const latestTurnId = normalizePrioritySemanticLatestTurnId(
      message.rfcMessageId,
    );
    return providerFolder &&
      attachedProviderFolder === providerFolder &&
      uidValidity &&
      attachedUidValidity === uidValidity &&
      imapUid &&
      latestTurnId
      ? {
          locator: {
            provider: "custom_imap",
            providerFolder,
            uidValidity,
            imapUid,
          },
          latestTurnId,
        }
      : null;
  }

  return null;
}

/**
 * Trigger B exists only for a new deterministic returned_reply transition.
 * The helper never infers externality, recency, or conversation authority;
 * reconcileWaitingOnOtherStore has already established those hard facts.
 */
export function findPrioritySemanticReturnedReplyTriggers<
  T extends PrioritySemanticInboundMessage,
>(
  previousStore: WaitingOnOtherStore,
  reconciledStore: WaitingOnOtherStore,
  externalInboundEntries: PrioritySemanticInboundEntry<T>[],
): PrioritySemanticReturnedReplyTrigger[] {
  const previousByConversation = indexWaitingConversationState(previousStore);
  const triggers: PrioritySemanticReturnedReplyTrigger[] = [];

  Object.values(reconciledStore).forEach((next) => {
    const conversationIndexKey = `${next.mailboxId}::${next.conversationKey}`;
    if (
      !isNewReturnedReplyTransition(
        previousByConversation.get(conversationIndexKey),
        next,
      ) ||
      next.state !== "returned_reply"
    ) {
      return;
    }

    const returnedReplyAtMs = new Date(next.returnedReplyAt).getTime();
    if (!Number.isFinite(returnedReplyAtMs)) {
      return;
    }

    const candidates = externalInboundEntries.flatMap((entry) => {
      if (
        entry.mailboxId !== next.mailboxId ||
        entry.message.threadIdentityContext?.mailboxId !== entry.mailboxId
      ) {
        return [];
      }
      const identity = resolveCanonicalConversationIdentity(
        entry.message,
        entry.mailboxId,
      );
      if (
        !identity.isAuthoritativeConversation ||
        identity.key !== next.conversationKey ||
        resolveMessageDateMs(entry.message) !== returnedReplyAtMs
      ) {
        return [];
      }
      const incoming = buildIncomingLocator(entry.message);
      return incoming ? [incoming] : [];
    });
    const uniqueCandidates = new Map(
      candidates.map((candidate) => [
        JSON.stringify(candidate),
        candidate,
      ]),
    );
    if (uniqueCandidates.size !== 1) {
      return;
    }

    const [{ locator, latestTurnId }] = [...uniqueCandidates.values()];
    triggers.push({
      mailboxId: next.mailboxId,
      conversationId: next.conversationKey,
      latestTurnId,
      returnedMessageKey: next.returnedMessageKey,
      incomingLocator: locator,
    });
  });

  return triggers.sort((left, right) =>
    [left.mailboxId, left.conversationId, left.returnedMessageKey]
      .join("::")
      .localeCompare(
        [right.mailboxId, right.conversationId, right.returnedMessageKey].join(
          "::",
        ),
      ),
  );
}
