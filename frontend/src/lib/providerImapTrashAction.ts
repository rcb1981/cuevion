const MAX_PROVIDER_IDENTIFIER_LENGTH = 256;
const MAX_PROVIDER_FOLDER_LENGTH = 16_384;
const MAX_IMAP_UID = "4294967295";
const MAX_RESPONSE_BYTES = 64 * 1024;
const MAX_TRASH_RESPONSE_BYTES = 10 * 1024 * 1024;
const MAX_TRASH_MESSAGES = 100;
const MAX_IMAP_UID_SET_SIZE = 100_000;

const SAFE_MUTATION_FAILURE_MESSAGE =
  "Could not move this message to Trash safely.";
const SAFE_MUTATION_UNCERTAIN_MESSAGE =
  "Trash may have completed; mailbox state must be refreshed.";
const SAFE_FETCH_FAILURE_MESSAGE =
  "Could not refresh this Trash folder safely.";

const CAPABILITY_ERROR_CODES = new Set([
  "trash_folder_unavailable",
  "trash_folder_ambiguous",
  "trash_move_unsupported",
  "trash_uidplus_unsupported",
]);

const PUBLIC_MUTATION_ERROR_CODES = new Set([
  ...CAPABILITY_ERROR_CODES,
  "invalid_trash_request",
  "invalid_mailbox_id",
  "invalid_source_folder",
  "invalid_imap_uid",
  "invalid_uid_validity",
  "mailbox_configuration_malformed",
  "mailbox_not_found",
  "managed_inbox_not_found",
  "reconnect_required",
  "source_folder_unavailable",
  "trash_source_invalid",
  "trash_source_unconfirmed",
  "trash_message_not_found",
  "trash_move_failed",
  "imap_trash_failed",
  "invalid_credentials",
  "imap_connection_failed",
  "mailbox_configuration_unavailable",
  "mailbox_secret_store_unavailable",
  "mailbox_secret_malformed",
  "uid_validity_changed",
  "uid_validity_unavailable",
  "unauthorized",
  "unsupported_provider",
]);

const PUBLIC_FETCH_ERROR_CODES = new Set([
  ...CAPABILITY_ERROR_CODES,
  "invalid_mailbox_id",
  "mailbox_configuration_malformed",
  "mailbox_not_found",
  "managed_inbox_not_found",
  "invalid_credentials",
  "imap_connection_failed",
  "mailbox_configuration_unavailable",
  "mailbox_secret_store_unavailable",
  "mailbox_secret_malformed",
  "reconnect_required",
  "trash_fetch_failed",
  "trash_snapshot_failed",
  "trash_snapshot_invalid",
  "uid_validity_unavailable",
  "unauthorized",
  "unsupported_provider",
]);

const FORBIDDEN_RESPONSE_KEYS = new Set([
  "authorization",
  "connection",
  "cookie",
  "fingerprint",
  "headers",
  "host",
  "identities",
  "identity",
  "mailboxconfig",
  "mailboxemail",
  "owneremail",
  "password",
  "payload",
  "port",
  "providererror",
  "providerpayload",
  "raw",
  "rawproviderresponse",
  "rawresponse",
  "session",
  "ssl",
  "userid",
  "username",
]);

const FORBIDDEN_RESPONSE_KEY_FRAGMENTS = [
  "credential",
  "password",
  "secret",
  "token",
];

const PREVIEW_FIELDS = new Set([
  "id",
  "sender",
  "subject",
  "snippet",
  "from",
  "to",
  "cc",
  "timestamp",
  "createdAt",
  "body",
  "bodyHtml",
  "attachments",
  "unread",
  "flagged",
  "category",
  "categorySource",
  "categoryConfidence",
  "signal",
  "ui_signal",
  "internalClassification",
  "final_visibility",
  "action",
  "v7_final_priority",
  "classifierVersion",
]);

const NULLABLE_PREVIEW_FIELDS = new Set([
  "category",
  "categorySource",
  "categoryConfidence",
  "signal",
  "ui_signal",
  "internalClassification",
  "final_visibility",
  "action",
  "v7_final_priority",
  "classifierVersion",
]);

const ATTACHMENT_FIELDS = new Set([
  "id",
  "name",
  "mimeType",
  "size",
  "contentId",
  "disposition",
  "inlineSrc",
]);

const IMAP_TRASH_MESSAGE_FIELDS = new Set([
  ...PREVIEW_FIELDS,
  "serverMailboxId",
  "providerFolder",
  "imapUid",
  "uidValidity",
  "threadId",
  "rfcMessageId",
]);

export type ProviderImapTrashMutationRequest = {
  mailboxId: string;
  action: "trash";
  sourceFolder: "INBOX";
  imapUid: string;
  uidValidity: string;
};

export type ProviderImapTrashSourceIdentity = {
  mailboxId: string;
  sourceFolder: "INBOX";
  sourceImapUid: string;
  sourceUidValidity: string;
};

export type ProviderImapTrashTargetIdentity = {
  targetFolder: string;
  targetImapUid: string;
  targetUidValidity: string;
};

export type ProviderImapTrashConfirmedMutation =
  ProviderImapTrashSourceIdentity &
  ProviderImapTrashTargetIdentity & {
    ok: true;
    status: "ok";
    action: "trash";
    provider: "custom_imap";
    confirmation: "source_removed_target_bound";
  };

export type ProviderImapTrashMutationUnconfirmed =
  ProviderImapTrashSourceIdentity & {
    ok: false;
    status: "mutation_unconfirmed";
    action: "trash";
    provider: "custom_imap";
    error: {
      code: "trash_mutation_unconfirmed";
      message: string;
    };
  };

export type ProviderImapTrashMutationFailure = {
  ok: false;
  error: {
    code: string;
    message: string;
  };
};

export type ProviderImapTrashMutationResponse =
  | ProviderImapTrashConfirmedMutation
  | ProviderImapTrashMutationUnconfirmed
  | ProviderImapTrashMutationFailure;

export type ProviderImapTrashMutation = (
  request: ProviderImapTrashMutationRequest,
) => Promise<unknown>;

export type ProviderImapTrashBlockReason =
  | "not_live_mailbox"
  | "invalid_selection"
  | "invalid_source_folder"
  | "unsupported_provider"
  | "mailbox_not_connected"
  | "invalid_mailbox_id"
  | "source_message_not_unique"
  | "source_mailbox_mismatch"
  | "mixed_provider_identity"
  | "invalid_imap_uid"
  | "invalid_uid_validity"
  | "unsafe_source_message"
  | "already_pending";

export type ProviderImapTrashCandidate = {
  provider: unknown;
  mailboxId: unknown;
  sourceFolder: unknown;
  imapUid: unknown;
  uidValidity: unknown;
};

export type ProviderImapTrashMutationTarget = {
  ok: true;
  inFlightKey: string;
  sourceIdentity: ProviderImapTrashSourceIdentity;
  request: ProviderImapTrashMutationRequest;
};

export type ProviderImapTrashBlockedTarget = {
  ok: false;
  classification: "blocked";
  reason: Exclude<ProviderImapTrashBlockReason, "already_pending">;
};

export type ProviderImapTrashTargetResult =
  | ProviderImapTrashMutationTarget
  | ProviderImapTrashBlockedTarget;

export type ExactCustomImapTrashSourceMessage = {
  id: string;
  serverMailboxId?: string | null;
  providerFolder?: string | null;
  imapUid?: string | null;
  uidValidity?: string | null;
  providerMessageId?: unknown;
  providerThreadId?: unknown;
  labelIds?: unknown;
  [key: string]: unknown;
};

export type CustomImapTrashManagedMailbox = {
  id?: unknown;
  provider?: unknown;
  connected?: unknown;
  connectionStatus?: unknown;
  [key: string]: unknown;
};

export type ResolvedCustomImapTrashMutationTarget<
  Message extends ExactCustomImapTrashSourceMessage,
> = {
  sourceMessage: Message;
  candidate: ProviderImapTrashCandidate;
  target: ProviderImapTrashMutationTarget;
};

export type ProviderImapTrashAttachmentSnapshot = {
  id: string;
  name: string;
  mimeType?: string;
  size?: number;
  contentId?: string;
  disposition?: string;
  inlineSrc?: string;
};

export type ProviderImapTrashMessageSnapshot = {
  id: string;
  serverMailboxId: string;
  providerFolder: string;
  imapUid: string;
  uidValidity: string;
  threadId: string;
  rfcMessageId?: string;
  sender: string;
  subject: string;
  snippet: string;
  from: string;
  to: string;
  cc?: string;
  timestamp: string;
  createdAt: string;
  body: string[];
  bodyHtml?: string;
  attachments?: ProviderImapTrashAttachmentSnapshot[];
  unread?: boolean;
  flagged?: boolean;
  category?: string | null;
  categorySource?: string | null;
  categoryConfidence?: string | null;
  signal?: string | null;
  ui_signal?: string | null;
  internalClassification?: string | null;
  final_visibility?: string | null;
  action?: string | null;
  v7_final_priority?: string | null;
  classifierVersion?: string | null;
};

export type ProviderImapTrashSnapshot = {
  serverMailboxId: string;
  providerFolder: string;
  uidValidity: string;
  imapUidSet: string[];
  messages: ProviderImapTrashMessageSnapshot[];
};

export type ProviderImapTrashFetchSuccess = {
  ok: true;
  status: "ok";
  provider: "custom_imap";
  mailboxId: string;
  folder: ProviderImapTrashSnapshot;
};

export type ProviderImapTrashFetchFailure = {
  ok: false;
  error: {
    code: string;
    message: string;
  };
};

export type ProviderImapTrashFetchResult =
  | ProviderImapTrashFetchSuccess
  | ProviderImapTrashFetchFailure;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[] = [],
) {
  const allowed = new Set([...required, ...optional]);
  return (
    required.every((key) => Object.prototype.hasOwnProperty.call(value, key)) &&
    Object.keys(value).every((key) => allowed.has(key))
  );
}

function hasOnlyKeys(
  value: Record<string, unknown>,
  allowed: ReadonlySet<string>,
) {
  return Object.keys(value).every((key) => allowed.has(key));
}

function compactKey(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function containsForbiddenResponseField(
  value: unknown,
  visited = new Set<object>(),
): boolean {
  if (!value || typeof value !== "object") return false;
  if (visited.has(value)) return false;
  visited.add(value);
  if (Array.isArray(value)) {
    return value.some((item) => containsForbiddenResponseField(item, visited));
  }
  return Object.entries(value).some(([key, item]) => {
    const compact = compactKey(key);
    return (
      FORBIDDEN_RESPONSE_KEYS.has(compact) ||
      FORBIDDEN_RESPONSE_KEY_FRAGMENTS.some((fragment) =>
        compact.includes(fragment),
      ) ||
      containsForbiddenResponseField(item, visited)
    );
  });
}

function isExactIdentifier(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= MAX_PROVIDER_IDENTIFIER_LENGTH &&
    value === value.trim() &&
    !Array.from(value).some((character) => {
      const point = character.codePointAt(0) ?? 0;
      return point < 32 || point === 127;
    })
  );
}

function isProviderFolder(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value.length < 1 ||
    value !== value.trim()
  ) {
    return false;
  }
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
      continue;
    }
    if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  if (
    Array.from(value).some((character) => {
      const point = character.codePointAt(0) ?? 0;
      return point < 32 || (point >= 127 && point <= 159);
    })
  ) {
    return false;
  }
  return new TextEncoder().encode(value).byteLength <= MAX_PROVIDER_FOLDER_LENGTH;
}

function isConcreteTrashFolder(value: unknown): value is string {
  return isProviderFolder(value) && value.toUpperCase() !== "INBOX";
}

function isCanonicalUidValidity(value: unknown): value is string {
  return typeof value === "string" && /^[1-9][0-9]{0,19}$/.test(value);
}

function isCanonicalImapUid(value: unknown): value is string {
  if (typeof value !== "string" || !/^[1-9][0-9]*$/.test(value)) {
    return false;
  }
  return (
    value.length < MAX_IMAP_UID.length ||
    (value.length === MAX_IMAP_UID.length && value <= MAX_IMAP_UID)
  );
}

function safeErrorMessage(value: unknown) {
  return (
    typeof value === "string" &&
    value.trim().length >= 1 &&
    value.length <= 2_048 &&
    !value.includes("\0")
  );
}

function mutationFailure(
  code = "trash_mutation_failed",
): ProviderImapTrashMutationFailure {
  return {
    ok: false,
    error: { code, message: SAFE_MUTATION_FAILURE_MESSAGE },
  };
}

function mutationUnconfirmed(
  request: ProviderImapTrashMutationRequest,
): ProviderImapTrashMutationUnconfirmed {
  return {
    ok: false,
    status: "mutation_unconfirmed",
    action: "trash",
    provider: "custom_imap",
    mailboxId: request.mailboxId,
    sourceFolder: "INBOX",
    sourceImapUid: request.imapUid,
    sourceUidValidity: request.uidValidity,
    error: {
      code: "trash_mutation_unconfirmed",
      message: SAFE_MUTATION_UNCERTAIN_MESSAGE,
    },
  };
}

function fetchFailure(
  code = "trash_fetch_failed",
): ProviderImapTrashFetchFailure {
  return {
    ok: false,
    error: { code, message: SAFE_FETCH_FAILURE_MESSAGE },
  };
}

export function buildProviderImapTrashInFlightKey(identity: {
  mailboxId: string;
  sourceUidValidity: string;
  sourceImapUid: string;
}) {
  return JSON.stringify([
    "trash",
    "custom_imap",
    identity.mailboxId,
    identity.sourceUidValidity,
    identity.sourceImapUid,
  ]);
}

export function hasPendingProviderImapTrashForMailbox(
  pendingKeys: ReadonlySet<string>,
  mailboxId: string,
) {
  return [...pendingKeys].some((key) => {
    try {
      const parts = JSON.parse(key);
      return (
        Array.isArray(parts) &&
        parts.length === 5 &&
        parts[0] === "trash" &&
        parts[1] === "custom_imap" &&
        parts[2] === mailboxId
      );
    } catch {
      return false;
    }
  });
}

export type MailboxRefreshTailSequencer<Result> = {
  run: (
    mailboxId: string,
    options: {
      queueAfterActive: boolean;
      perform: () => Promise<Result>;
    },
  ) => Promise<Result | "skipped">;
  reset: (mailboxId: string) => void;
};

export function createMailboxRefreshTailSequencer<
  Result,
>(): MailboxRefreshTailSequencer<Result> {
  const tails = new Map<string, Promise<Result | "skipped">>();
  const generations = new Map<string, number>();
  const readGeneration = (mailboxId: string) =>
    generations.get(mailboxId) ?? 0;

  return {
    run: (mailboxId, { queueAfterActive, perform }) => {
      const generation = readGeneration(mailboxId);
      const previousTail = tails.get(mailboxId);
      if (previousTail && !queueAfterActive) {
        return Promise.resolve("skipped");
      }

      const startRefresh = (): Promise<Result | "skipped"> =>
        readGeneration(mailboxId) === generation
          ? Promise.resolve().then(perform)
          : Promise.resolve("skipped" as const);
      const refreshPromise: Promise<Result | "skipped"> = previousTail
        ? previousTail.then<Result | "skipped", Result | "skipped">(
            startRefresh,
            startRefresh,
          )
        : startRefresh();
      let trackedPromise: Promise<Result | "skipped">;
      trackedPromise = refreshPromise.finally(() => {
        if (tails.get(mailboxId) === trackedPromise) {
          tails.delete(mailboxId);
        }
      });
      tails.set(mailboxId, trackedPromise);
      return trackedPromise;
    },
    reset: (mailboxId) => {
      generations.set(mailboxId, readGeneration(mailboxId) + 1);
      tails.delete(mailboxId);
    },
  };
}

export function buildProviderImapTrashMutationTarget(
  candidate: ProviderImapTrashCandidate,
): ProviderImapTrashTargetResult {
  if (candidate.provider !== "custom_imap") {
    return { ok: false, classification: "blocked", reason: "unsupported_provider" };
  }
  if (!isExactIdentifier(candidate.mailboxId)) {
    return { ok: false, classification: "blocked", reason: "invalid_mailbox_id" };
  }
  if (candidate.sourceFolder !== "INBOX") {
    return { ok: false, classification: "blocked", reason: "invalid_source_folder" };
  }
  if (!isCanonicalImapUid(candidate.imapUid)) {
    return { ok: false, classification: "blocked", reason: "invalid_imap_uid" };
  }
  if (!isCanonicalUidValidity(candidate.uidValidity)) {
    return { ok: false, classification: "blocked", reason: "invalid_uid_validity" };
  }

  const request: ProviderImapTrashMutationRequest = {
    mailboxId: candidate.mailboxId,
    action: "trash",
    sourceFolder: "INBOX",
    imapUid: candidate.imapUid,
    uidValidity: candidate.uidValidity,
  };
  const sourceIdentity: ProviderImapTrashSourceIdentity = {
    mailboxId: request.mailboxId,
    sourceFolder: "INBOX",
    sourceImapUid: request.imapUid,
    sourceUidValidity: request.uidValidity,
  };
  return {
    ok: true,
    request,
    sourceIdentity,
    inFlightKey: buildProviderImapTrashInFlightKey(sourceIdentity),
  };
}

export function resolveExactCustomImapTrashMutationTarget<
  Message extends ExactCustomImapTrashSourceMessage,
>({
  isLiveMailbox,
  selectedMessageIds,
  sourceFolder,
  sourceManagedMailbox,
  sourceMessages,
}: {
  isLiveMailbox: boolean;
  selectedMessageIds: readonly string[];
  sourceFolder: unknown;
  sourceManagedMailbox: CustomImapTrashManagedMailbox | null | undefined;
  sourceMessages: readonly Message[];
}): ResolvedCustomImapTrashMutationTarget<Message> | null {
  if (!isLiveMailbox) return null;
  if (selectedMessageIds.length !== 1) return null;
  if (sourceFolder !== "Inbox") return null;
  if (
    !sourceManagedMailbox ||
    sourceManagedMailbox.provider !== "custom_imap" ||
    sourceManagedMailbox.connected !== true ||
    sourceManagedMailbox.connectionStatus !== "connected" ||
    !isExactIdentifier(sourceManagedMailbox.id)
  ) {
    return null;
  }

  const matches = sourceMessages.filter(
    (message) => message.id === selectedMessageIds[0],
  );
  if (matches.length !== 1) return null;
  const sourceMessage = matches[0];
  if (containsForbiddenResponseField(sourceMessage)) return null;
  if (sourceMessage.serverMailboxId !== sourceManagedMailbox.id) return null;
  if (sourceMessage.providerFolder !== "INBOX") return null;
  if (
    sourceMessage.providerMessageId !== undefined &&
    sourceMessage.providerMessageId !== null
  ) {
    return null;
  }
  if (
    sourceMessage.providerThreadId !== undefined &&
    sourceMessage.providerThreadId !== null
  ) {
    return null;
  }
  if (sourceMessage.labelIds !== undefined && sourceMessage.labelIds !== null) {
    return null;
  }

  const candidate: ProviderImapTrashCandidate = {
    provider: "custom_imap",
    mailboxId: sourceMessage.serverMailboxId,
    sourceFolder: sourceMessage.providerFolder,
    imapUid: sourceMessage.imapUid,
    uidValidity: sourceMessage.uidValidity,
  };
  const target = buildProviderImapTrashMutationTarget(candidate);
  return target.ok ? { sourceMessage, candidate, target } : null;
}

function requestIsValid(
  value: unknown,
): value is ProviderImapTrashMutationRequest {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "mailboxId",
      "action",
      "sourceFolder",
      "imapUid",
      "uidValidity",
    ]) &&
    isExactIdentifier(value.mailboxId) &&
    value.action === "trash" &&
    value.sourceFolder === "INBOX" &&
    isCanonicalImapUid(value.imapUid) &&
    isCanonicalUidValidity(value.uidValidity)
  );
}

function responseMatchesSource(
  value: Record<string, unknown>,
  request?: ProviderImapTrashMutationRequest,
) {
  return (
    value.sourceFolder === "INBOX" &&
    isExactIdentifier(value.mailboxId) &&
    isCanonicalImapUid(value.sourceImapUid) &&
    isCanonicalUidValidity(value.sourceUidValidity) &&
    (
      !request ||
      (
        value.mailboxId === request.mailboxId &&
        value.sourceImapUid === request.imapUid &&
        value.sourceUidValidity === request.uidValidity
      )
    )
  );
}

export function isProviderImapTrashMutationSuccessResponse(
  value: unknown,
  request?: ProviderImapTrashMutationRequest,
): value is ProviderImapTrashConfirmedMutation {
  return (
    !containsForbiddenResponseField(value) &&
    isRecord(value) &&
    hasExactKeys(value, [
      "ok",
      "status",
      "action",
      "provider",
      "mailboxId",
      "sourceFolder",
      "sourceImapUid",
      "sourceUidValidity",
      "targetFolder",
      "targetImapUid",
      "targetUidValidity",
      "confirmation",
    ]) &&
    value.ok === true &&
    value.status === "ok" &&
    value.action === "trash" &&
    value.provider === "custom_imap" &&
    value.confirmation === "source_removed_target_bound" &&
    responseMatchesSource(value, request) &&
    isConcreteTrashFolder(value.targetFolder) &&
    isCanonicalImapUid(value.targetImapUid) &&
    isCanonicalUidValidity(value.targetUidValidity)
  );
}

export function sanitizeProviderImapTrashMutationUnconfirmedResponse(
  value: unknown,
  request: ProviderImapTrashMutationRequest,
): ProviderImapTrashMutationUnconfirmed | null {
  if (
    containsForbiddenResponseField(value) ||
    !isRecord(value) ||
    !hasExactKeys(value, [
      "ok",
      "status",
      "action",
      "provider",
      "mailboxId",
      "sourceFolder",
      "sourceImapUid",
      "sourceUidValidity",
      "error",
    ]) ||
    value.ok !== false ||
    value.status !== "mutation_unconfirmed" ||
    value.action !== "trash" ||
    value.provider !== "custom_imap" ||
    !responseMatchesSource(value, request) ||
    !isRecord(value.error) ||
    !hasExactKeys(value.error, ["code", "message"]) ||
    value.error.code !== "trash_mutation_unconfirmed" ||
    !safeErrorMessage(value.error.message)
  ) {
    return null;
  }
  return mutationUnconfirmed(request);
}

function sanitizeMutationFailure(
  value: unknown,
): ProviderImapTrashMutationFailure | null {
  if (
    containsForbiddenResponseField(value) ||
    !isRecord(value) ||
    !hasExactKeys(value, ["ok", "error"]) ||
    value.ok !== false ||
    !isRecord(value.error) ||
    !hasExactKeys(value.error, ["code", "message"]) ||
    typeof value.error.code !== "string" ||
    !PUBLIC_MUTATION_ERROR_CODES.has(value.error.code) ||
    !safeErrorMessage(value.error.message)
  ) {
    return null;
  }
  return mutationFailure(value.error.code);
}

function sanitizeConfirmedMutation(
  value: unknown,
  request: ProviderImapTrashMutationRequest,
): ProviderImapTrashConfirmedMutation | null {
  if (!isProviderImapTrashMutationSuccessResponse(value, request)) return null;
  return {
    ok: true,
    status: "ok",
    action: "trash",
    provider: "custom_imap",
    mailboxId: value.mailboxId,
    sourceFolder: "INBOX",
    sourceImapUid: value.sourceImapUid,
    sourceUidValidity: value.sourceUidValidity,
    targetFolder: value.targetFolder,
    targetImapUid: value.targetImapUid,
    targetUidValidity: value.targetUidValidity,
    confirmation: "source_removed_target_bound",
  };
}

function isAttachment(
  value: unknown,
): value is ProviderImapTrashAttachmentSnapshot {
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, ATTACHMENT_FIELDS) ||
    typeof value.id !== "string" ||
    typeof value.name !== "string"
  ) {
    return false;
  }
  for (const key of ["mimeType", "contentId", "disposition", "inlineSrc"]) {
    if (value[key] !== undefined && typeof value[key] !== "string") {
      return false;
    }
  }
  return (
    value.size === undefined ||
    (
      typeof value.size === "number" &&
      Number.isFinite(value.size) &&
      value.size >= 0
    )
  );
}

function previewFieldsAreValid(value: Record<string, unknown>) {
  for (const key of [
    "id",
    "sender",
    "subject",
    "snippet",
    "from",
    "to",
    "timestamp",
    "createdAt",
  ]) {
    if (typeof value[key] !== "string") return false;
  }
  if (
    !Array.isArray(value.body) ||
    value.body.some((part) => typeof part !== "string")
  ) {
    return false;
  }
  for (const [key, item] of Object.entries(value)) {
    if (!PREVIEW_FIELDS.has(key)) continue;
    if (key === "body") continue;
    if (key === "attachments") {
      if (
        !Array.isArray(item) ||
        item.length > 1_000 ||
        item.some((attachment) => !isAttachment(attachment))
      ) {
        return false;
      }
      continue;
    }
    if (key === "unread" || key === "flagged") {
      if (typeof item !== "boolean") return false;
      continue;
    }
    if (
      typeof item !== "string" &&
      !(item === null && NULLABLE_PREVIEW_FIELDS.has(key))
    ) {
      return false;
    }
  }
  return true;
}

function trashMessageIsValid(
  value: unknown,
  mailboxId: string,
  providerFolder: string,
  uidValidity: string,
  knownUids: ReadonlySet<string>,
): value is ProviderImapTrashMessageSnapshot {
  return (
    !containsForbiddenResponseField(value) &&
    isRecord(value) &&
    hasOnlyKeys(value, IMAP_TRASH_MESSAGE_FIELDS) &&
    previewFieldsAreValid(value) &&
    value.serverMailboxId === mailboxId &&
    value.providerFolder === providerFolder &&
    value.uidValidity === uidValidity &&
    isCanonicalImapUid(value.imapUid) &&
    knownUids.has(value.imapUid) &&
    isExactIdentifier(value.threadId) &&
    (
      value.rfcMessageId === undefined ||
      isExactIdentifier(value.rfcMessageId)
    )
  );
}

export function validateProviderImapTrashSnapshot(
  value: unknown,
  mailboxId: string,
): value is ProviderImapTrashSnapshot {
  if (
    containsForbiddenResponseField(value) ||
    !isExactIdentifier(mailboxId) ||
    !isRecord(value) ||
    !hasExactKeys(value, [
      "serverMailboxId",
      "providerFolder",
      "uidValidity",
      "imapUidSet",
      "messages",
    ]) ||
    value.serverMailboxId !== mailboxId ||
    !isConcreteTrashFolder(value.providerFolder) ||
    !isCanonicalUidValidity(value.uidValidity) ||
    !Array.isArray(value.imapUidSet) ||
    !Array.isArray(value.messages) ||
    value.messages.length > MAX_TRASH_MESSAGES
  ) {
    return false;
  }

  const uidSet = value.imapUidSet;
  if (
    uidSet.length > MAX_IMAP_UID_SET_SIZE ||
    uidSet.some((uid) => !isCanonicalImapUid(uid)) ||
    new Set(uidSet).size !== uidSet.length ||
    !uidSet.every(
      (uid, index) =>
        index === 0 || BigInt(uidSet[index - 1]) < BigInt(uid),
    )
  ) {
    return false;
  }

  const expectedMessageUids = uidSet
    .slice(Math.max(0, uidSet.length - MAX_TRASH_MESSAGES))
    .reverse();
  if (value.messages.length !== expectedMessageUids.length) return false;

  const knownUids = new Set(uidSet);
  return value.messages.every(
    (message, index) =>
      trashMessageIsValid(
        message,
        mailboxId,
        value.providerFolder as string,
        value.uidValidity as string,
        knownUids,
      ) && message.imapUid === expectedMessageUids[index],
  );
}

export function isProviderImapTrashFetchSuccessResponse(
  value: unknown,
  mailboxId: string,
): value is ProviderImapTrashFetchSuccess {
  return (
    !containsForbiddenResponseField(value) &&
    isRecord(value) &&
    hasExactKeys(value, [
      "ok",
      "status",
      "provider",
      "mailboxId",
      "folder",
    ]) &&
    value.ok === true &&
    value.status === "ok" &&
    value.provider === "custom_imap" &&
    value.mailboxId === mailboxId &&
    validateProviderImapTrashSnapshot(value.folder, mailboxId)
  );
}

function sanitizeFetchFailure(value: unknown) {
  if (
    containsForbiddenResponseField(value) ||
    !isRecord(value) ||
    !hasExactKeys(value, ["ok", "error"]) ||
    value.ok !== false ||
    !isRecord(value.error) ||
    !hasExactKeys(value.error, ["code", "message"]) ||
    typeof value.error.code !== "string" ||
    !PUBLIC_FETCH_ERROR_CODES.has(value.error.code) ||
    !safeErrorMessage(value.error.message)
  ) {
    return null;
  }
  return fetchFailure(value.error.code);
}

async function readBoundedJson(response: Response, maximumBytes: number) {
  const contentLength = response.headers.get("content-length");
  if (
    contentLength !== null &&
    (!/^\d+$/.test(contentLength) ||
      BigInt(contentLength) > BigInt(maximumBytes))
  ) {
    return null;
  }

  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    reader = response.body?.getReader();
  } catch {
    return null;
  }
  if (!reader) return null;
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const textParts: string[] = [];
  let receivedBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      receivedBytes += value.byteLength;
      if (receivedBytes > maximumBytes) {
        await reader.cancel().catch(() => undefined);
        return null;
      }
      textParts.push(decoder.decode(value, { stream: true }));
    }
    textParts.push(decoder.decode());
  } catch {
    await reader.cancel().catch(() => undefined);
    return null;
  }
  const text = textParts.join("");
  if (text.length === 0) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

export async function mutateProviderImapTrashMessage(
  request: ProviderImapTrashMutationRequest,
): Promise<ProviderImapTrashMutationResponse> {
  if (!requestIsValid(request)) return mutationFailure("invalid_trash_request");

  let response: Response;
  try {
    response = await fetch("/api/inboxes/message-action", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  } catch {
    return mutationUnconfirmed(request);
  }

  const payload = await readBoundedJson(response, MAX_RESPONSE_BYTES);
  const confirmed = sanitizeConfirmedMutation(payload, request);
  if (response.ok && confirmed) return confirmed;
  const unconfirmed = sanitizeProviderImapTrashMutationUnconfirmedResponse(
    payload,
    request,
  );
  if (unconfirmed) return unconfirmed;
  const failure = sanitizeMutationFailure(payload);
  if (!response.ok && failure) return failure;

  if (
    !response.ok &&
    response.status >= 400 &&
    response.status < 500 &&
    response.status !== 408 &&
    response.status !== 429
  ) {
    return mutationFailure();
  }
  return mutationUnconfirmed(request);
}

export async function fetchProviderImapTrash({
  mailboxId,
}: {
  mailboxId: string;
}): Promise<ProviderImapTrashFetchResult> {
  if (!isExactIdentifier(mailboxId)) return fetchFailure("invalid_mailbox_id");

  let response: Response;
  try {
    response = await fetch("/api/inboxes/fetch-trash", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mailboxId }),
    });
  } catch {
    return fetchFailure();
  }

  const payload = await readBoundedJson(response, MAX_TRASH_RESPONSE_BYTES);
  if (response.ok && isProviderImapTrashFetchSuccessResponse(payload, mailboxId)) {
    return {
      ok: true,
      status: "ok",
      provider: "custom_imap",
      mailboxId,
      folder: payload.folder,
    };
  }
  return sanitizeFetchFailure(payload) ?? fetchFailure(
    response.ok ? "trash_snapshot_invalid" : "trash_fetch_failed",
  );
}

type ImapTrashStateMessage = {
  serverMailboxId?: string | null;
  providerFolder?: string | null;
  imapUid?: string | null;
  uidValidity?: string | null;
};

export function applyConfirmedImapTrashSourceRemoval<
  Message extends ImapTrashStateMessage,
  Collections extends { Inbox: Message[] },
>(
  current: Collections,
  response: ProviderImapTrashConfirmedMutation,
): { applied: boolean; state: Collections } {
  if (!isProviderImapTrashMutationSuccessResponse(response)) {
    return { applied: false, state: current };
  }

  let matches = 0;
  const Inbox = current.Inbox.filter((message) => {
    const match =
      message.serverMailboxId === response.mailboxId &&
      message.providerFolder === "INBOX" &&
      message.imapUid === response.sourceImapUid &&
      message.uidValidity === response.sourceUidValidity;
    if (match) matches += 1;
    return !match;
  });
  if (matches !== 1) return { applied: false, state: current };
  return { applied: true, state: { ...current, Inbox } };
}

export function replaceCustomImapTrashFolderReadback<
  Message,
  Collections extends { Trash: Message[] },
>(
  current: Collections,
  snapshot: ProviderImapTrashSnapshot,
): { applied: boolean; state: Collections } {
  if (
    !isRecord(snapshot) ||
    !isExactIdentifier(snapshot.serverMailboxId) ||
    !validateProviderImapTrashSnapshot(snapshot, snapshot.serverMailboxId)
  ) {
    return { applied: false, state: current };
  }
  return {
    applied: true,
    state: {
      ...current,
      Trash: [...snapshot.messages] as Message[],
    },
  };
}

export type ProviderImapTrashClassification =
  | "success"
  | "uncertain"
  | "capability_unavailable"
  | "ordinary_failure"
  | "blocked";

export type ProviderImapTrashReadOnlyRefreshRequest = {
  mailboxId: string;
  cause: "confirmed_success" | "mutation_unconfirmed";
  sourceIdentity: ProviderImapTrashSourceIdentity;
  confirmedTarget?: ProviderImapTrashTargetIdentity;
};

export type ProviderImapTrashCoordinatorResult =
  | {
      classification: "success";
      inFlightKey: string;
      request: ProviderImapTrashMutationRequest;
      response: ProviderImapTrashConfirmedMutation;
      sourceRemovalApplied: boolean;
      refreshAttempted: true;
      refreshed: boolean;
    }
  | {
      classification: "uncertain";
      inFlightKey: string;
      request: ProviderImapTrashMutationRequest;
      response: ProviderImapTrashMutationUnconfirmed;
      sourceRemovalApplied: false;
      refreshAttempted: true;
      refreshed: boolean;
    }
  | {
      classification: "capability_unavailable" | "ordinary_failure";
      inFlightKey: string;
      request: ProviderImapTrashMutationRequest;
      response: ProviderImapTrashMutationFailure;
      sourceRemovalApplied: false;
      refreshAttempted: false;
      refreshed: false;
    }
  | {
      classification: "blocked";
      reason: ProviderImapTrashBlockReason;
      inFlightKey?: string;
      request?: ProviderImapTrashMutationRequest;
      sourceRemovalApplied: false;
      refreshAttempted: false;
      refreshed: false;
    };

export type ProviderImapTrashCoordinator = {
  trash(
    target: ProviderImapTrashMutationTarget | ProviderImapTrashCandidate,
  ): Promise<ProviderImapTrashCoordinatorResult>;
};

export function classifyProviderImapTrashMutationResponse(
  value: unknown,
  request: ProviderImapTrashMutationRequest,
):
  | {
      classification: "success";
      response: ProviderImapTrashConfirmedMutation;
    }
  | {
      classification: "uncertain";
      response: ProviderImapTrashMutationUnconfirmed;
    }
  | {
      classification: "capability_unavailable";
      response: ProviderImapTrashMutationFailure;
    }
  | {
      classification: "ordinary_failure";
      response: ProviderImapTrashMutationFailure;
    } {
  const confirmed = sanitizeConfirmedMutation(value, request);
  if (confirmed) return { classification: "success", response: confirmed };
  const unconfirmed = sanitizeProviderImapTrashMutationUnconfirmedResponse(
    value,
    request,
  );
  if (unconfirmed) {
    return { classification: "uncertain", response: unconfirmed };
  }
  const failure = sanitizeMutationFailure(value);
  if (failure) {
    if (CAPABILITY_ERROR_CODES.has(failure.error.code)) {
      return { classification: "capability_unavailable", response: failure };
    }
    return { classification: "ordinary_failure", response: failure };
  }
  return {
    classification: "uncertain",
    response: mutationUnconfirmed(request),
  };
}

function normalizeCoordinatorTarget(
  value: ProviderImapTrashMutationTarget | ProviderImapTrashCandidate,
): ProviderImapTrashTargetResult {
  const record: Record<string, unknown> | null = isRecord(value)
    ? (value as Record<string, unknown>)
    : null;
  if (
    record &&
    record.ok === true &&
    isRecord(record.request) &&
    requestIsValid(record.request)
  ) {
    const rebuilt = buildProviderImapTrashMutationTarget({
      provider: "custom_imap",
      mailboxId: record.request.mailboxId,
      sourceFolder: record.request.sourceFolder,
      imapUid: record.request.imapUid,
      uidValidity: record.request.uidValidity,
    });
    if (
      rebuilt.ok &&
      record.inFlightKey === rebuilt.inFlightKey &&
      isRecord(record.sourceIdentity) &&
      record.sourceIdentity.mailboxId === rebuilt.sourceIdentity.mailboxId &&
      record.sourceIdentity.sourceFolder === "INBOX" &&
      record.sourceIdentity.sourceImapUid ===
        rebuilt.sourceIdentity.sourceImapUid &&
      record.sourceIdentity.sourceUidValidity ===
        rebuilt.sourceIdentity.sourceUidValidity
    ) {
      return rebuilt;
    }
    return {
      ok: false,
      classification: "blocked",
      reason: "unsafe_source_message",
    };
  }
  return buildProviderImapTrashMutationTarget(
    value as ProviderImapTrashCandidate,
  );
}

export function createProviderImapTrashCoordinator({
  mutate = mutateProviderImapTrashMessage,
  pendingKeys = new Set<string>(),
  onPendingKeysChange,
  applyConfirmedSourceRemoval,
  refreshProviderTrashReadOnly,
}: {
  mutate?: ProviderImapTrashMutation;
  pendingKeys?: Set<string>;
  onPendingKeysChange?: (pendingKeys: ReadonlySet<string>) => void;
  applyConfirmedSourceRemoval: (
    response: ProviderImapTrashConfirmedMutation,
  ) => boolean;
  refreshProviderTrashReadOnly: (
    request: ProviderImapTrashReadOnlyRefreshRequest,
  ) => boolean | void | Promise<boolean | void>;
}): ProviderImapTrashCoordinator {
  const notifyPendingKeysChange = () => {
    try {
      onPendingKeysChange?.(new Set(pendingKeys));
    } catch {
      // Local observers cannot alter mutation safety or pending ownership.
    }
  };

  return {
    async trash(input) {
      const target = normalizeCoordinatorTarget(input);
      if (target.ok === false) {
        return {
          classification: "blocked",
          reason: target.reason,
          sourceRemovalApplied: false,
          refreshAttempted: false,
          refreshed: false,
        };
      }
      if (pendingKeys.has(target.inFlightKey)) {
        return {
          classification: "blocked",
          reason: "already_pending",
          inFlightKey: target.inFlightKey,
          request: target.request,
          sourceRemovalApplied: false,
          refreshAttempted: false,
          refreshed: false,
        };
      }

      let released = false;
      const release = () => {
        if (released) return;
        released = true;
        pendingKeys.delete(target.inFlightKey);
        notifyPendingKeysChange();
      };
      pendingKeys.add(target.inFlightKey);
      notifyPendingKeysChange();

      try {
        let rawResponse: unknown;
        try {
          rawResponse = await mutate(target.request);
        } catch {
          rawResponse = mutationUnconfirmed(target.request);
        }
        const mutation = classifyProviderImapTrashMutationResponse(
          rawResponse,
          target.request,
        );

        if (mutation.classification === "capability_unavailable") {
          release();
          return {
            classification: "capability_unavailable",
            inFlightKey: target.inFlightKey,
            request: target.request,
            response: mutation.response,
            sourceRemovalApplied: false,
            refreshAttempted: false,
            refreshed: false,
          };
        }
        if (mutation.classification === "ordinary_failure") {
          release();
          return {
            classification: "ordinary_failure",
            inFlightKey: target.inFlightKey,
            request: target.request,
            response: mutation.response,
            sourceRemovalApplied: false,
            refreshAttempted: false,
            refreshed: false,
          };
        }

        let sourceRemovalApplied = false;
        if (mutation.classification === "success") {
          try {
            sourceRemovalApplied =
              applyConfirmedSourceRemoval(mutation.response) === true;
          } catch {
            sourceRemovalApplied = false;
          }
        }

        // A confirmed source callback finishes before its key is released. An
        // uncertain mutation keeps ownership until reconciliation settles so a
        // second MOVE cannot race the read-only source/Trash refresh.
        if (mutation.classification === "success") release();
        let refreshed = false;
        try {
          const refreshResult = await refreshProviderTrashReadOnly({
            mailboxId: target.request.mailboxId,
            cause:
              mutation.classification === "success"
                ? "confirmed_success"
                : "mutation_unconfirmed",
            sourceIdentity: target.sourceIdentity,
            ...(mutation.classification === "success"
              ? {
                  confirmedTarget: {
                    targetFolder: mutation.response.targetFolder,
                    targetImapUid: mutation.response.targetImapUid,
                    targetUidValidity: mutation.response.targetUidValidity,
                  },
                }
              : {}),
          });
          refreshed = refreshResult !== false;
        } catch {
          refreshed = false;
        }
        if (mutation.classification === "uncertain") release();

        if (mutation.classification === "success") {
          return {
            classification: "success",
            inFlightKey: target.inFlightKey,
            request: target.request,
            response: mutation.response,
            sourceRemovalApplied,
            refreshAttempted: true,
            refreshed,
          };
        }
        return {
          classification: "uncertain",
          inFlightKey: target.inFlightKey,
          request: target.request,
          response: mutation.response,
          sourceRemovalApplied: false,
          refreshAttempted: true,
          refreshed,
        };
      } finally {
        release();
      }
    },
  };
}
