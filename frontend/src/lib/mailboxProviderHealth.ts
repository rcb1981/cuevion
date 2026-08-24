export type MailboxHealthProvider = "google" | "custom_imap";

export type MailboxHealthStatus =
  | "checking"
  | "connected"
  | "temporary_issue"
  | "action_required";

export type MailboxHealthPendingRollback = Readonly<{
  existed: boolean;
  status: MailboxHealthStatus;
  updatedAt: string | null;
  errorCode: string | null;
  actionRequiredRecoveryAttempted: boolean;
}>;

export type MailboxHealthRecord = Readonly<{
  mailboxId: string;
  provider: MailboxHealthProvider;
  authorityKey: string;
  status: MailboxHealthStatus;
  latestOperationToken: number;
  operationPending: boolean;
  actionRequiredRecoveryAttempted: boolean;
  pendingRollback: MailboxHealthPendingRollback | null;
  updatedAt: string | null;
  lastSuccessAt: string | null;
  errorCode: string | null;
}>;

export type MailboxHealthStore = Readonly<
  Record<string, MailboxHealthRecord>
>;

export type MailboxHealthOperation = Readonly<{
  mailboxId: string;
  provider: MailboxHealthProvider;
  token: number;
  startedAt: string;
  actionRequiredRecovery: boolean;
}>;

export type MailboxHealthSeed = Readonly<{
  mailboxId: string;
  provider: MailboxHealthProvider;
  status?: MailboxHealthStatus;
  authorityKey?: string;
}>;

export type MailboxHealthBeginOptions = Readonly<{
  actionRequiredRecovery?: boolean;
}>;

export type MailboxHealthCompletion = Readonly<{
  ok: boolean;
  errorCode?: string | null;
  completedAt?: string;
  provesProviderUsable?: boolean;
}>;

export type MailboxHealthTone =
  | "neutral"
  | "success"
  | "warning"
  | "danger";

export type MailboxHealthPresentation = Readonly<{
  label: "CHECKING" | "CONNECTED" | "TEMPORARY ISSUE" | "ACTION REQUIRED";
  tone: MailboxHealthTone;
  description: string;
}>;

export type MailboxHealthOperationClock = Readonly<{
  begin: (
    mailboxId: string,
    provider: MailboxHealthProvider,
    options?: MailboxHealthBeginOptions,
  ) => MailboxHealthOperation;
}>;

const HEALTH_PRESENTATIONS: Readonly<
  Record<MailboxHealthStatus, MailboxHealthPresentation>
> = Object.freeze({
  checking: Object.freeze({
    label: "CHECKING",
    tone: "neutral",
    description: "Mailbox provider access is being checked.",
  }),
  connected: Object.freeze({
    label: "CONNECTED",
    tone: "success",
    description: "The latest mailbox provider operation succeeded.",
  }),
  temporary_issue: Object.freeze({
    label: "TEMPORARY ISSUE",
    tone: "warning",
    description: "The mailbox provider is temporarily unavailable.",
  }),
  action_required: Object.freeze({
    label: "ACTION REQUIRED",
    tone: "danger",
    description: "This mailbox needs attention in connection settings.",
  }),
});

// These codes provide definitive evidence that the Gmail authorization cannot
// be reused. Provider, token-store, rate-limit, and generic refresh failures are
// intentionally absent and therefore remain temporary by default.
const GMAIL_ACTION_REQUIRED_CODES = new Set([
  "reconnect_required",
  "gmail_connection_not_ready",
  "gmail_authorization_revoked",
  "gmail_invalid_grant",
  "gmail_refresh_invalid_grant",
  "invalid_grant",
  "gmail_reconnect_required",
  "gmail_token_invalid",
  "gmail_token_record_malformed",
  "gmail_refresh_token_invalid",
  "gmail_token_missing",
  "gmail_refresh_token_missing",
  "gmail_credential_missing",
  "gmail_credentials_missing",
  "gmail_owner_mismatch",
  "gmail_provider_mismatch",
  "gmail_credential_owner_mismatch",
  "gmail_provider_credential_mismatch",
]);

// IMAP authentication errors are not reliably distinguishable from provider
// outages at the current boundary. Only explicit reconnect/missing-secret or
// corrupt stored-configuration evidence is allowed to turn the UI red.
const IMAP_ACTION_REQUIRED_CODES = new Set([
  "reconnect_required",
  "imap_host_invalid",
  "imap_credentials_unavailable",
  "imap_credentials_missing",
  "imap_credential_missing",
  "imap_password_missing",
  "mailbox_credentials_missing",
  "mailbox_credential_missing",
  "mailbox_secret_missing",
  "mailbox_secret_malformed",
  "mailbox_credential_generation_missing",
  "mailbox_credential_generation_invalid",
  "mailbox_configuration_malformed",
]);

function assertExactMailboxId(mailboxId: string): void {
  if (!mailboxId || mailboxId.trim() !== mailboxId) {
    throw new Error("Mailbox health requires an exact mailbox ID.");
  }
}

function assertProvider(
  provider: MailboxHealthProvider,
): asserts provider is MailboxHealthProvider {
  if (provider !== "google" && provider !== "custom_imap") {
    throw new Error("Mailbox health requires a supported provider.");
  }
}

function freezeRecord(record: MailboxHealthRecord): MailboxHealthRecord {
  return Object.freeze(record);
}

function freezeRollback(
  rollback: MailboxHealthPendingRollback,
): MailboxHealthPendingRollback {
  return Object.freeze(rollback);
}

function freezeStore(
  store: Record<string, MailboxHealthRecord>,
): MailboxHealthStore {
  return Object.freeze(store);
}

function normalizedErrorCode(errorCode: string | null | undefined): string {
  return typeof errorCode === "string" ? errorCode.trim().toLowerCase() : "";
}

function defaultAuthorityKey(
  mailboxId: string,
  provider: MailboxHealthProvider,
): string {
  return `${provider}:${mailboxId}`;
}

function classifyFailure(
  provider: MailboxHealthProvider,
  errorCode: string | null | undefined,
): Exclude<MailboxHealthStatus, "checking" | "connected"> {
  const code = normalizedErrorCode(errorCode);
  const actionRequiredCodes =
    provider === "google"
      ? GMAIL_ACTION_REQUIRED_CODES
      : IMAP_ACTION_REQUIRED_CODES;

  return actionRequiredCodes.has(code)
    ? "action_required"
    : "temporary_issue";
}

function resolveTimestamp(value: string | undefined): string {
  const normalized = value?.trim();
  return normalized || new Date().toISOString();
}

export function createMailboxHealthOperationClock(): MailboxHealthOperationClock {
  let nextToken = 0;

  return Object.freeze({
    begin(
      mailboxId: string,
      provider: MailboxHealthProvider,
      options?: MailboxHealthBeginOptions,
    ): MailboxHealthOperation {
      assertExactMailboxId(mailboxId);
      assertProvider(provider);
      nextToken += 1;

      return Object.freeze({
        mailboxId,
        provider,
        token: nextToken,
        startedAt: new Date().toISOString(),
        actionRequiredRecovery:
          options?.actionRequiredRecovery === true,
      });
    },
  });
}

export function createInitialMailboxHealthStore(
  seeds: readonly MailboxHealthSeed[],
): MailboxHealthStore {
  const store: Record<string, MailboxHealthRecord> = {};

  seeds.forEach((seed) => {
    assertExactMailboxId(seed.mailboxId);
    assertProvider(seed.provider);
    if (store[seed.mailboxId]) {
      throw new Error(`Duplicate mailbox health seed: ${seed.mailboxId}`);
    }

    store[seed.mailboxId] = freezeRecord({
      mailboxId: seed.mailboxId,
      provider: seed.provider,
      authorityKey:
        seed.authorityKey ?? defaultAuthorityKey(seed.mailboxId, seed.provider),
      status: seed.status ?? "checking",
      latestOperationToken: 0,
      operationPending: false,
      actionRequiredRecoveryAttempted: false,
      pendingRollback: null,
      updatedAt: null,
      lastSuccessAt: null,
      errorCode: null,
    });
  });

  return freezeStore(store);
}

export function beginMailboxHealthOperation(
  store: MailboxHealthStore,
  operation: MailboxHealthOperation,
): MailboxHealthStore {
  assertExactMailboxId(operation.mailboxId);
  assertProvider(operation.provider);
  const storedCurrent = store[operation.mailboxId];
  const current =
    storedCurrent?.provider === operation.provider ? storedCurrent : undefined;

  if (current && operation.token <= current.latestOperationToken) {
    return store;
  }

  const pendingRollback = current?.operationPending
    ? current.pendingRollback
    : null;
  const rollback = freezeRollback(
    pendingRollback ??
      (current
        ? {
            existed: true,
            status: current.status,
            updatedAt: current.updatedAt,
            errorCode: current.errorCode,
            actionRequiredRecoveryAttempted:
              current.actionRequiredRecoveryAttempted,
          }
        : {
            existed: false,
            status: "checking",
            updatedAt: null,
            errorCode: null,
            actionRequiredRecoveryAttempted: false,
          }),
  );
  const actionRequiredRecoveryAttempted =
    (current?.actionRequiredRecoveryAttempted ?? false) ||
    operation.actionRequiredRecovery;
  const preserveActionRequired =
    rollback.existed &&
    rollback.status === "action_required" &&
    !actionRequiredRecoveryAttempted;

  return freezeStore({
    ...store,
    [operation.mailboxId]: freezeRecord({
      mailboxId: operation.mailboxId,
      provider: operation.provider,
      authorityKey:
        current?.authorityKey ??
        defaultAuthorityKey(operation.mailboxId, operation.provider),
      status: preserveActionRequired ? "action_required" : "checking",
      latestOperationToken: operation.token,
      operationPending: true,
      actionRequiredRecoveryAttempted,
      pendingRollback: rollback,
      updatedAt: preserveActionRequired
        ? rollback.updatedAt
        : operation.startedAt,
      lastSuccessAt: current?.lastSuccessAt ?? null,
      errorCode: preserveActionRequired ? rollback.errorCode : null,
    }),
  });
}

export function cancelMailboxHealthOperation(
  store: MailboxHealthStore,
  operation: MailboxHealthOperation,
): MailboxHealthStore {
  const current = store[operation.mailboxId];
  if (
    !current ||
    !current.operationPending ||
    current.provider !== operation.provider ||
    current.latestOperationToken !== operation.token
  ) {
    return store;
  }

  const rollback = current.pendingRollback;
  if (!rollback?.existed) {
    const next = { ...store };
    delete next[operation.mailboxId];
    return freezeStore(next);
  }

  return freezeStore({
    ...store,
    [operation.mailboxId]: freezeRecord({
      ...current,
      status: rollback.status,
      operationPending: false,
      actionRequiredRecoveryAttempted:
        rollback.actionRequiredRecoveryAttempted,
      pendingRollback: null,
      updatedAt: rollback.updatedAt,
      errorCode: rollback.errorCode,
    }),
  });
}

export function completeMailboxHealthOperation(
  store: MailboxHealthStore,
  operation: MailboxHealthOperation,
  completion: MailboxHealthCompletion,
): MailboxHealthStore {
  const current = store[operation.mailboxId];
  if (
    !current ||
    !current.operationPending ||
    current.provider !== operation.provider ||
    current.latestOperationToken !== operation.token
  ) {
    return store;
  }

  const completedAt = resolveTimestamp(completion.completedAt);
  const errorCode = normalizedErrorCode(completion.errorCode) || null;
  const classifiedStatus = completion.ok
    ? completion.provesProviderUsable === false
      ? "checking"
      : "connected"
    : classifyFailure(operation.provider, errorCode);
  const rollback = current.pendingRollback;
  const preservesUnrecoveredActionRequired =
    rollback?.existed === true &&
    rollback.status === "action_required" &&
    !current.actionRequiredRecoveryAttempted &&
    classifiedStatus !== "action_required";
  const status = preservesUnrecoveredActionRequired
    ? "action_required"
    : classifiedStatus;
  const definitiveActionRequired = classifiedStatus === "action_required";

  return freezeStore({
    ...store,
    [operation.mailboxId]: freezeRecord({
      ...current,
      status,
      operationPending: false,
      actionRequiredRecoveryAttempted: definitiveActionRequired
        ? false
        : current.actionRequiredRecoveryAttempted,
      pendingRollback: null,
      updatedAt: preservesUnrecoveredActionRequired
        ? rollback.updatedAt
        : completedAt,
      lastSuccessAt:
        status === "connected" && !preservesUnrecoveredActionRequired
          ? completedAt
          : current.lastSuccessAt,
      errorCode: preservesUnrecoveredActionRequired
        ? rollback.errorCode
        : completion.ok
          ? null
          : errorCode,
    }),
  });
}

export function reconcileMailboxHealthStore(
  store: MailboxHealthStore,
  seeds: readonly MailboxHealthSeed[],
): MailboxHealthStore {
  const seededStore = createInitialMailboxHealthStore(seeds);
  let changed = Object.keys(store).length !== Object.keys(seededStore).length;
  const next: Record<string, MailboxHealthRecord> = {};

  Object.entries(seededStore).forEach(([mailboxId, seededRecord]) => {
    const current = store[mailboxId];
    let nextRecord = current;

    if (
      !current ||
      current.provider !== seededRecord.provider ||
      current.authorityKey !== seededRecord.authorityKey
    ) {
      nextRecord = seededRecord;
    } else if (
      seededRecord.status === "action_required" &&
      current.status !== "action_required" &&
      !current.actionRequiredRecoveryAttempted
    ) {
      nextRecord = seededRecord;
    }

    next[mailboxId] = nextRecord;
    if (nextRecord !== current) {
      changed = true;
    }
  });

  return changed ? freezeStore(next) : store;
}

export function getMailboxHealthPresentation(
  status: MailboxHealthStatus,
): MailboxHealthPresentation {
  return HEALTH_PRESENTATIONS[status];
}
