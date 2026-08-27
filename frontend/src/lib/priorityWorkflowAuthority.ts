import {
  buildPriorityWorkflowRecordKey,
  type PriorityWorkflowAuthorityClient,
  type PriorityWorkflowAuthorityError,
  type PriorityWorkflowCleared,
  type PriorityWorkflowIdentity,
  type PriorityWorkflowManualPriority,
  type PriorityWorkflowRecord,
  type PriorityWorkflowWaiting,
} from "./priorityWorkflowAuthorityApi";

export type PriorityWorkflowMailboxSource = {
  id: string;
  provider: string | null;
  connected: boolean;
  connectionStatus: string;
};

export type PriorityWorkflowMessageSource = {
  serverMailboxId?: string | null;
  providerMessageId?: string | null;
  providerFolder?: string | null;
  uidValidity?: string | null;
  imapUid?: string | null;
  threadIdentityContext?: {
    mailboxId?: string | null;
    provider?: string | null;
    folder?: string | null;
    uidValidity?: string | null;
  } | null;
};

export type PriorityWorkflowTarget = {
  mailboxId: string;
  identity: PriorityWorkflowIdentity;
  recordKey: string;
};

export type PriorityWorkflowTargetResolution =
  | { status: "canonical"; target: PriorityWorkflowTarget }
  | { status: "local_only"; reason: "non_authoritative_workspace" }
  | {
      status: "invalid";
      reason:
        | "mailbox_missing"
        | "mailbox_not_ready"
        | "mailbox_mismatch"
        | "provider_unsupported"
        | "provider_mismatch"
        | "identity_missing_or_unsafe";
    };

const IMAP_MAX_NUMBER = "4294967295";

function validIdentifier(value: unknown, maximum = 1_024): value is string {
  return (
    typeof value === "string" &&
    value === value.trim() &&
    value.length >= 1 &&
    value.length <= maximum &&
    !/[\u0000-\u001f\u007f]/.test(value)
  );
}

function validImapNumber(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[1-9]\d*$/.test(value) &&
    (value.length < IMAP_MAX_NUMBER.length ||
      (value.length === IMAP_MAX_NUMBER.length && value <= IMAP_MAX_NUMBER))
  );
}

function validImapFolder(value: unknown): value is string {
  if (!validIdentifier(value, 16_384)) {
    return false;
  }
  try {
    return new TextEncoder().encode(value).length <= 16_384;
  } catch {
    return false;
  }
}

function hasConflictingValue(
  expected: string,
  candidate: string | null | undefined,
) {
  return typeof candidate === "string" && candidate !== expected;
}

export function resolvePriorityWorkflowTarget(input: {
  serverAuthorityEnabled: boolean;
  mailbox: PriorityWorkflowMailboxSource | null | undefined;
  message: PriorityWorkflowMessageSource;
}): PriorityWorkflowTargetResolution {
  if (!input.serverAuthorityEnabled) {
    return { status: "local_only", reason: "non_authoritative_workspace" };
  }
  const mailbox = input.mailbox;
  if (!mailbox || !validIdentifier(mailbox.id, 256)) {
    return { status: "invalid", reason: "mailbox_missing" };
  }
  if (!mailbox.connected || mailbox.connectionStatus !== "connected") {
    return { status: "invalid", reason: "mailbox_not_ready" };
  }
  const message = input.message;
  if (
    message.serverMailboxId !== mailbox.id ||
    hasConflictingValue(
      mailbox.id,
      message.threadIdentityContext?.mailboxId,
    )
  ) {
    return { status: "invalid", reason: "mailbox_mismatch" };
  }
  if (mailbox.provider !== "google" && mailbox.provider !== "custom_imap") {
    return { status: "invalid", reason: "provider_unsupported" };
  }
  if (
    hasConflictingValue(
      mailbox.provider,
      message.threadIdentityContext?.provider,
    )
  ) {
    return { status: "invalid", reason: "provider_mismatch" };
  }

  let identity: PriorityWorkflowIdentity;
  if (mailbox.provider === "google") {
    if (
      !validIdentifier(message.providerMessageId, 256) ||
      Boolean(message.imapUid)
    ) {
      return { status: "invalid", reason: "identity_missing_or_unsafe" };
    }
    identity = {
      provider: "google",
      providerMessageId: message.providerMessageId,
    };
  } else {
    const providerFolder = message.providerFolder;
    const uidValidity = message.uidValidity;
    const imapUid = message.imapUid;
    if (
      !validImapFolder(providerFolder) ||
      !validImapNumber(uidValidity) ||
      !validImapNumber(imapUid) ||
      hasConflictingValue(
        providerFolder,
        message.threadIdentityContext?.folder,
      ) ||
      hasConflictingValue(
        uidValidity,
        message.threadIdentityContext?.uidValidity,
      )
    ) {
      return { status: "invalid", reason: "identity_missing_or_unsafe" };
    }
    identity = {
      provider: "custom_imap",
      providerFolder,
      uidValidity,
      imapUid,
    };
  }
  return {
    status: "canonical",
    target: {
      mailboxId: mailbox.id,
      identity,
      recordKey: buildPriorityWorkflowRecordKey(mailbox.id, identity),
    },
  };
}

type WorkflowClient = Pick<
  PriorityWorkflowAuthorityClient,
  "setManualPriority" | "setCleared" | "setWaiting" | "read"
>;

export type PriorityWorkflowOperation =
  | { operation: "set_manual_priority"; value: PriorityWorkflowManualPriority }
  | { operation: "set_cleared"; value: PriorityWorkflowCleared }
  | { operation: "set_waiting"; value: PriorityWorkflowWaiting };

export type PriorityWorkflowWriteOutcome =
  | {
      status: "applied" | "reconciled";
      record: PriorityWorkflowRecord;
      generation: number;
    }
  | { status: "stale" | "superseded"; generation: number }
  | {
      status: "failed";
      generation: number;
      error: PriorityWorkflowAuthorityError;
    };

function recordHasRequestedValue(
  record: PriorityWorkflowRecord,
  operation: PriorityWorkflowOperation,
) {
  if (operation.operation === "set_manual_priority") {
    return record.manualPriority === operation.value;
  }
  if (operation.operation === "set_cleared") {
    return record.cleared === operation.value;
  }
  return record.waiting === operation.value;
}

function sameTarget(record: PriorityWorkflowRecord, target: PriorityWorkflowTarget) {
  return (
    buildPriorityWorkflowRecordKey(record.mailboxId, record.identity) ===
    target.recordKey
  );
}

export class PriorityWorkflowWriteCoordinator {
  readonly #client: WorkflowClient;
  #activeScopeKey: string;
  #scopeGeneration = 0;
  readonly #recordGenerations = new Map<string, number>();
  readonly #acceptedVersions = new Map<string, number>();
  readonly #recordTails = new Map<string, Promise<void>>();
  readonly #pendingWrites = new Map<string, Promise<PriorityWorkflowWriteOutcome>>();
  readonly #pendingActions = new Map<string, Promise<unknown>>();

  constructor(client: WorkflowClient, initialScopeKey: string) {
    this.#client = client;
    this.#activeScopeKey = initialScopeKey;
  }

  activateScope(scopeKey: string) {
    if (scopeKey !== this.#activeScopeKey) {
      this.#activeScopeKey = scopeKey;
      this.#scopeGeneration += 1;
    }
  }

  runAction<T>(scopeKey: string, actionKey: string, task: () => Promise<T>) {
    const key = JSON.stringify([scopeKey, actionKey]);
    const pending = this.#pendingActions.get(key) as Promise<T> | undefined;
    if (pending) {
      return pending;
    }
    const promise = Promise.resolve()
      .then(task)
      .finally(() => {
        if (this.#pendingActions.get(key) === promise) {
          this.#pendingActions.delete(key);
        }
      });
    this.#pendingActions.set(key, promise);
    return promise;
  }

  write(input: {
    scopeKey: string;
    target: PriorityWorkflowTarget;
    operation: PriorityWorkflowOperation;
    commit: (record: PriorityWorkflowRecord) => void;
  }): Promise<PriorityWorkflowWriteOutcome> {
    const operationKey = JSON.stringify([
      input.scopeKey,
      input.target.recordKey,
      input.operation.operation,
      input.operation.value,
    ]);
    const pending = this.#pendingWrites.get(operationKey);
    if (pending) {
      return pending;
    }
    const scopedRecordKey = JSON.stringify([
      input.scopeKey,
      input.target.recordKey,
    ]);
    const generation = (this.#recordGenerations.get(scopedRecordKey) ?? 0) + 1;
    this.#recordGenerations.set(scopedRecordKey, generation);
    const scopeGeneration = this.#scopeGeneration;
    const priorTail = this.#recordTails.get(scopedRecordKey) ?? Promise.resolve();
    const promise = priorTail
      .catch(() => undefined)
      .then(() => this.#performWrite({ ...input, generation, scopeGeneration }))
      .finally(() => {
        if (this.#pendingWrites.get(operationKey) === promise) {
          this.#pendingWrites.delete(operationKey);
        }
      });
    this.#pendingWrites.set(operationKey, promise);
    const tail = promise.then(
      () => undefined,
      () => undefined,
    );
    this.#recordTails.set(scopedRecordKey, tail);
    void tail.finally(() => {
      if (this.#recordTails.get(scopedRecordKey) === tail) {
        this.#recordTails.delete(scopedRecordKey);
      }
    });
    return promise;
  }

  async #performWrite(input: {
    scopeKey: string;
    target: PriorityWorkflowTarget;
    operation: PriorityWorkflowOperation;
    commit: (record: PriorityWorkflowRecord) => void;
    generation: number;
    scopeGeneration: number;
  }): Promise<PriorityWorkflowWriteOutcome> {
    if (
      this.#activeScopeKey !== input.scopeKey ||
      this.#scopeGeneration !== input.scopeGeneration
    ) {
      return { status: "superseded", generation: input.generation };
    }
    const request = {
      mailboxId: input.target.mailboxId,
      identity: input.target.identity,
      value: input.operation.value,
    };
    const response =
      input.operation.operation === "set_manual_priority"
        ? await this.#client.setManualPriority(
            request as Parameters<WorkflowClient["setManualPriority"]>[0],
          )
        : input.operation.operation === "set_cleared"
          ? await this.#client.setCleared(
              request as Parameters<WorkflowClient["setCleared"]>[0],
            )
          : await this.#client.setWaiting(
              request as Parameters<WorkflowClient["setWaiting"]>[0],
            );
    let record: PriorityWorkflowRecord | null = null;
    let status: "applied" | "reconciled" = "applied";
    if (response.ok) {
      record = response.value;
    } else if (response.error.ambiguous) {
      const reconciliation = await this.#client.read({
        mailboxId: input.target.mailboxId,
        identities: [input.target.identity],
      });
      const candidate = reconciliation.ok ? reconciliation.value[0] : null;
      if (
        candidate &&
        candidate.version > 0 &&
        sameTarget(candidate, input.target) &&
        recordHasRequestedValue(candidate, input.operation)
      ) {
        record = candidate;
        status = "reconciled";
      } else {
        return {
          status: "failed",
          generation: input.generation,
          error: response.error,
        };
      }
    } else {
      return {
        status: "failed",
        generation: input.generation,
        error: response.error,
      };
    }

    if (!sameTarget(record, input.target) || !recordHasRequestedValue(record, input.operation)) {
      return {
        status: "failed",
        generation: input.generation,
        error: {
          code: "workflow_response_mismatch",
          message: "The saved change could not be confirmed safely.",
          ambiguous: true,
        },
      };
    }
    if (
      this.#activeScopeKey !== input.scopeKey ||
      this.#scopeGeneration !== input.scopeGeneration
    ) {
      return { status: "superseded", generation: input.generation };
    }
    const scopedRecordKey = JSON.stringify([
      input.scopeKey,
      input.target.recordKey,
    ]);
    const acceptedVersion = this.#acceptedVersions.get(scopedRecordKey) ?? 0;
    if (record.version <= acceptedVersion) {
      return { status: "stale", generation: input.generation };
    }
    this.#acceptedVersions.set(scopedRecordKey, record.version);
    input.commit(record);
    return { status, record, generation: input.generation };
  }
}
