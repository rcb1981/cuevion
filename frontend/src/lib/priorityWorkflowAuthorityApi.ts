export const PRIORITY_WORKFLOW_AUTHORITY_ENDPOINT =
  "/api/priority/workflow-authority";

export type PriorityWorkflowGmailIdentity = {
  provider: "google";
  providerMessageId: string;
};

export type PriorityWorkflowImapIdentity = {
  provider: "custom_imap";
  providerFolder: string;
  uidValidity: string;
  imapUid: string;
};

export type PriorityWorkflowIdentity =
  | PriorityWorkflowGmailIdentity
  | PriorityWorkflowImapIdentity;

export type PriorityWorkflowManualPriority =
  | "none"
  | "priority"
  | "removed";
export type PriorityWorkflowCleared = "active" | "cleared";
export type PriorityWorkflowWaiting =
  | "absent"
  | "waiting_on_other"
  | "returned_reply";

export type PriorityWorkflowRecord = {
  mailboxId: string;
  identity: PriorityWorkflowIdentity;
  manualPriority: PriorityWorkflowManualPriority;
  cleared: PriorityWorkflowCleared;
  waiting: PriorityWorkflowWaiting;
  version: number;
  updatedAt: number | null;
};

export type PriorityWorkflowAuthorityError = {
  code: string;
  message: string;
  ambiguous: boolean;
};

export type PriorityWorkflowAuthorityResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: PriorityWorkflowAuthorityError };

type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

type WorkflowWriteRequest<TValue extends string> = {
  mailboxId: string;
  identity: PriorityWorkflowIdentity;
  value: TValue;
};

const DEFAULT_TIMEOUT_MS = 8_000;
const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;
const IMAP_MAX_NUMBER = 4_294_967_295;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]) {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return (
    actual.length === expected.length &&
    actual.every((key, index) => key === expected[index])
  );
}

function isBoundedIdentifier(value: unknown, maximum = 1_024): value is string {
  return (
    typeof value === "string" &&
    value === value.trim() &&
    value.length >= 1 &&
    value.length <= maximum &&
    !/[\u0000-\u001f\u007f]/.test(value)
  );
}

function isImapNumber(value: unknown): value is string {
  if (typeof value !== "string" || !/^[1-9]\d*$/.test(value)) {
    return false;
  }
  return (
    value.length < String(IMAP_MAX_NUMBER).length ||
    (value.length === String(IMAP_MAX_NUMBER).length &&
      value <= String(IMAP_MAX_NUMBER))
  );
}

function isImapFolder(value: unknown): value is string {
  if (!isBoundedIdentifier(value, 16_384)) {
    return false;
  }
  try {
    return new TextEncoder().encode(value).length <= 16_384;
  } catch {
    return false;
  }
}

export function normalizePriorityWorkflowIdentity(
  value: unknown,
): PriorityWorkflowIdentity | null {
  if (!isPlainObject(value)) {
    return null;
  }
  if (
    hasExactKeys(value, ["provider", "providerMessageId"]) &&
    value.provider === "google" &&
    isBoundedIdentifier(value.providerMessageId, 256)
  ) {
    return {
      provider: "google",
      providerMessageId: value.providerMessageId,
    };
  }
  if (
    hasExactKeys(value, [
      "provider",
      "providerFolder",
      "uidValidity",
      "imapUid",
    ]) &&
    value.provider === "custom_imap" &&
    isImapFolder(value.providerFolder) &&
    isImapNumber(value.uidValidity) &&
    isImapNumber(value.imapUid)
  ) {
    return {
      provider: "custom_imap",
      providerFolder: value.providerFolder,
      uidValidity: value.uidValidity,
      imapUid: value.imapUid,
    };
  }
  return null;
}

export function buildPriorityWorkflowRecordKey(
  mailboxId: string,
  identity: PriorityWorkflowIdentity,
) {
  return identity.provider === "google"
    ? JSON.stringify([mailboxId, "google", identity.providerMessageId])
    : JSON.stringify([
        mailboxId,
        "custom_imap",
        identity.providerFolder,
        identity.uidValidity,
        identity.imapUid,
      ]);
}

function sameIdentity(
  left: PriorityWorkflowIdentity,
  right: PriorityWorkflowIdentity,
) {
  return (
    buildPriorityWorkflowRecordKey("mailbox", left) ===
    buildPriorityWorkflowRecordKey("mailbox", right)
  );
}

function parseRecord(
  value: unknown,
  options: { allowEmpty: boolean },
): PriorityWorkflowRecord | null {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, [
      "mailboxId",
      "identity",
      "manualPriority",
      "cleared",
      "waiting",
      "version",
      "updatedAt",
    ])
  ) {
    return null;
  }
  const identity = normalizePriorityWorkflowIdentity(value.identity);
  const version = value.version;
  const updatedAt = value.updatedAt;
  const validVersion =
    Number.isSafeInteger(version) &&
    typeof version === "number" &&
    version >= (options.allowEmpty ? 0 : 1) &&
    version <= MAX_SAFE_INTEGER;
  const validUpdatedAt =
    version === 0
      ? updatedAt === null
      : typeof updatedAt === "number" &&
        Number.isSafeInteger(updatedAt) &&
        updatedAt >= 0 &&
        updatedAt <= MAX_SAFE_INTEGER;
  if (
    !isBoundedIdentifier(value.mailboxId, 256) ||
    !identity ||
    !["none", "priority", "removed"].includes(
      value.manualPriority as string,
    ) ||
    !["active", "cleared"].includes(value.cleared as string) ||
    !["absent", "waiting_on_other", "returned_reply"].includes(
      value.waiting as string,
    ) ||
    !validVersion ||
    !validUpdatedAt ||
    (version === 0 &&
      (value.manualPriority !== "none" ||
        value.cleared !== "active" ||
        value.waiting !== "absent"))
  ) {
    return null;
  }
  return {
    mailboxId: value.mailboxId,
    identity,
    manualPriority: value.manualPriority as PriorityWorkflowManualPriority,
    cleared: value.cleared as PriorityWorkflowCleared,
    waiting: value.waiting as PriorityWorkflowWaiting,
    version,
    updatedAt: updatedAt as number | null,
  };
}

function responseMatchesRequest(
  record: PriorityWorkflowRecord,
  request: { mailboxId: string; identity: PriorityWorkflowIdentity },
) {
  return (
    record.mailboxId === request.mailboxId &&
    sameIdentity(record.identity, request.identity)
  );
}

function fixedErrorMessage(status: number) {
  if (status === 401) {
    return "Your session could not save this change. Sign in again and retry.";
  }
  if (status === 404) {
    return "This mailbox is no longer available. Refresh and retry.";
  }
  if (status === 409) {
    return "This mailbox is not ready to save the change. Refresh and retry.";
  }
  return "This change could not be saved across devices. Please try again.";
}

function failure(
  code: string,
  message: string,
  ambiguous = false,
): PriorityWorkflowAuthorityResult<never> {
  return { ok: false, error: { code, message, ambiguous } };
}

function parseServerErrorCode(value: unknown) {
  if (
    isPlainObject(value) &&
    hasExactKeys(value, ["ok", "error"]) &&
    value.ok === false &&
    isPlainObject(value.error) &&
    hasExactKeys(value.error, ["code", "message"]) &&
    typeof value.error.code === "string" &&
    /^[a-z0-9_]{1,80}$/.test(value.error.code)
  ) {
    return value.error.code;
  }
  return "workflow_request_failed";
}

function validRequestBase(value: {
  mailboxId: string;
  identity: PriorityWorkflowIdentity;
}) {
  return (
    isBoundedIdentifier(value.mailboxId, 256) &&
    normalizePriorityWorkflowIdentity(value.identity) !== null
  );
}

export class PriorityWorkflowAuthorityClient {
  readonly #fetchImpl: FetchLike;
  readonly #timeoutMs: number;

  constructor(options: { fetch?: FetchLike; timeoutMs?: number } = {}) {
    this.#fetchImpl = options.fetch ?? ((input, init) => fetch(input, init));
    this.#timeoutMs =
      typeof options.timeoutMs === "number" &&
      Number.isFinite(options.timeoutMs) &&
      options.timeoutMs > 0
        ? options.timeoutMs
        : DEFAULT_TIMEOUT_MS;
  }

  setManualPriority(
    request: WorkflowWriteRequest<PriorityWorkflowManualPriority>,
  ) {
    return this.#write("set_manual_priority", request);
  }

  setCleared(request: WorkflowWriteRequest<PriorityWorkflowCleared>) {
    return this.#write("set_cleared", request);
  }

  setWaiting(request: WorkflowWriteRequest<PriorityWorkflowWaiting>) {
    return this.#write("set_waiting", request);
  }

  async read(
    request: {
      mailboxId: string;
      identities: PriorityWorkflowIdentity[];
    },
  ): Promise<PriorityWorkflowAuthorityResult<PriorityWorkflowRecord[]>> {
    if (
      !isBoundedIdentifier(request.mailboxId, 256) ||
      !Array.isArray(request.identities) ||
      request.identities.length < 1 ||
      request.identities.length > 64 ||
      request.identities.some(
        (identity) => normalizePriorityWorkflowIdentity(identity) === null,
      ) ||
      new Set(
        request.identities.map((identity) =>
          buildPriorityWorkflowRecordKey(request.mailboxId, identity),
        ),
      ).size !== request.identities.length
    ) {
      return failure(
        "invalid_workflow_request",
        "This change cannot be reconciled safely.",
      );
    }
    const result = await this.#request({
      operation: "read",
      mailboxId: request.mailboxId,
      identities: request.identities,
    });
    if (!result.ok) {
      return result;
    }
    const payload = result.value;
    if (
      !isPlainObject(payload) ||
      !hasExactKeys(payload, ["ok", "status", "records"]) ||
      payload.ok !== true ||
      payload.status !== "hydrated" ||
      !Array.isArray(payload.records) ||
      payload.records.length !== request.identities.length
    ) {
      return failure(
        "invalid_workflow_response",
        "This change could not be reconciled safely.",
      );
    }
    const records = payload.records.map((record) =>
      parseRecord(record, { allowEmpty: true }),
    );
    if (
      records.some((record) => !record) ||
      records.some(
        (record, index) =>
          !record ||
          !responseMatchesRequest(record, {
            mailboxId: request.mailboxId,
            identity: request.identities[index],
          }),
      )
    ) {
      return failure(
        "invalid_workflow_response",
        "This change could not be reconciled safely.",
      );
    }
    return { ok: true, value: records as PriorityWorkflowRecord[] };
  }

  async #write<TValue extends string>(
    operation: "set_manual_priority" | "set_cleared" | "set_waiting",
    request: WorkflowWriteRequest<TValue>,
  ): Promise<PriorityWorkflowAuthorityResult<PriorityWorkflowRecord>> {
    const allowed =
      operation === "set_manual_priority"
        ? ["none", "priority", "removed"]
        : operation === "set_cleared"
          ? ["active", "cleared"]
          : ["absent", "waiting_on_other", "returned_reply"];
    if (!validRequestBase(request) || !allowed.includes(request.value)) {
      return failure(
        "invalid_workflow_request",
        "This change cannot be saved safely.",
      );
    }
    const result = await this.#request({
      operation,
      mailboxId: request.mailboxId,
      identity: request.identity,
      value: request.value,
    });
    if (!result.ok) {
      return result;
    }
    const payload = result.value;
    const record =
      isPlainObject(payload) &&
      hasExactKeys(payload, ["ok", "status", "record"]) &&
      payload.ok === true &&
      payload.status === "updated"
        ? parseRecord(payload.record, { allowEmpty: false })
        : null;
    if (!record || !responseMatchesRequest(record, request)) {
      return failure(
        "invalid_workflow_response",
        "The saved change could not be confirmed safely.",
        true,
      );
    }
    return { ok: true, value: record };
  }

  async #request(
    body: Record<string, unknown>,
  ): Promise<PriorityWorkflowAuthorityResult<unknown>> {
    const abortController = new AbortController();
    const timeoutId = globalThis.setTimeout(
      () => abortController.abort(),
      this.#timeoutMs,
    );
    try {
      const response = await this.#fetchImpl(
        PRIORITY_WORKFLOW_AUTHORITY_ENDPOINT,
        {
          method: "POST",
          credentials: "include",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          signal: abortController.signal,
          body: JSON.stringify(body),
        },
      );
      let text: string;
      try {
        text = await response.text();
      } catch {
        return failure(
          "workflow_response_unreadable",
          "The saved change could not be confirmed. Please retry.",
          true,
        );
      }
      let payload: unknown = null;
      if (text.trim()) {
        try {
          payload = JSON.parse(text) as unknown;
        } catch {
          payload = null;
        }
      }
      if (!response.ok) {
        return failure(
          parseServerErrorCode(payload),
          fixedErrorMessage(response.status),
        );
      }
      if (payload === null) {
        return failure(
          "invalid_workflow_response",
          "The saved change could not be confirmed safely.",
          true,
        );
      }
      return { ok: true, value: payload };
    } catch (error) {
      const timedOut =
        error instanceof DOMException && error.name === "AbortError";
      return failure(
        timedOut ? "workflow_timeout" : "workflow_network_error",
        timedOut
          ? "Saving this change timed out. Please retry."
          : "This change could not be saved across devices. Please retry.",
        true,
      );
    } finally {
      globalThis.clearTimeout(timeoutId);
    }
  }
}
