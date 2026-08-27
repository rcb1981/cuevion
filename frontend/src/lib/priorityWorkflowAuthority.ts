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

const PRIORITY_WORKFLOW_MAX_BATCH_IDENTITIES = 64;

export type PriorityWorkflowAuthorityReadState =
  | { status: "not_ready" }
  | {
      status: "ready";
      record: PriorityWorkflowRecord;
      generationKey: string;
      source: "hydration" | "write";
    };

export type PriorityWorkflowFieldProjection = {
  manualPriority: PriorityWorkflowManualPriority;
  cleared: PriorityWorkflowCleared;
  waiting: PriorityWorkflowWaiting;
  ready: boolean;
  source: "server" | "legacy" | "not_ready";
};

export function projectPriorityWorkflowFields(input: {
  canonical: boolean;
  authority: PriorityWorkflowAuthorityReadState;
  legacy: Pick<
    PriorityWorkflowFieldProjection,
    "manualPriority" | "cleared" | "waiting"
  >;
}): PriorityWorkflowFieldProjection {
  if (!input.canonical) {
    return { ...input.legacy, ready: true, source: "legacy" };
  }
  if (input.authority.status !== "ready") {
    return {
      manualPriority: "none",
      cleared: "active",
      waiting: "absent",
      ready: false,
      source: "not_ready",
    };
  }
  return {
    manualPriority: input.authority.record.manualPriority,
    cleared: input.authority.record.cleared,
    waiting: input.authority.record.waiting,
    ready: true,
    source: "server",
  };
}

type PriorityWorkflowAcceptedRecord = Extract<
  PriorityWorkflowAuthorityReadState,
  { status: "ready" }
>;

export type PriorityWorkflowHydrationOutcome =
  | {
      status: "hydrated" | "partial";
      generationKey: string;
      acceptedRecordKeys: string[];
      failedBatchCount: number;
    }
  | {
      status: "failed";
      generationKey: string;
      acceptedRecordKeys: string[];
      failedBatchCount: number;
      error: PriorityWorkflowAuthorityError;
    }
  | { status: "empty" | "already_requested" | "stale"; generationKey: string };

type PriorityWorkflowHydrationPlan = {
  scopeKey: string;
  scopeGeneration: number;
  mailboxId: string;
  generationKey: string;
  requestGeneration: number;
  batches: PriorityWorkflowTarget[][];
};

function recordsHaveEqualAuthority(
  left: PriorityWorkflowRecord,
  right: PriorityWorkflowRecord,
) {
  return (
    left.mailboxId === right.mailboxId &&
    buildPriorityWorkflowRecordKey(left.mailboxId, left.identity) ===
      buildPriorityWorkflowRecordKey(right.mailboxId, right.identity) &&
    left.manualPriority === right.manualPriority &&
    left.cleared === right.cleared &&
    left.waiting === right.waiting &&
    left.version === right.version &&
    left.updatedAt === right.updatedAt
  );
}

/**
 * In-memory read authority for the currently accepted provider generations.
 * Browser persistence is deliberately absent: local workflow state is only a
 * mirror and never participates in record acceptance.
 */
export class PriorityWorkflowAuthorityStore {
  #activeScopeKey: string;
  #scopeGeneration = 0;
  #requestGeneration = 0;
  readonly #mailboxGenerations = new Map<
    string,
    { generationKey: string; requestGeneration: number }
  >();
  readonly #acceptedRecords = new Map<string, PriorityWorkflowAcceptedRecord>();
  readonly #acceptedVersions = new Map<string, number>();
  readonly #latestRecords = new Map<string, PriorityWorkflowRecord>();

  constructor(initialScopeKey: string) {
    this.#activeScopeKey = initialScopeKey;
  }

  activateScope(scopeKey: string) {
    if (scopeKey === this.#activeScopeKey) {
      return false;
    }
    this.#activeScopeKey = scopeKey;
    this.#scopeGeneration += 1;
    this.#mailboxGenerations.clear();
    this.#acceptedRecords.clear();
    this.#acceptedVersions.clear();
    this.#latestRecords.clear();
    return true;
  }

  read(target: PriorityWorkflowTarget): PriorityWorkflowAuthorityReadState {
    return this.#acceptedRecords.get(target.recordKey) ?? { status: "not_ready" };
  }

  acceptWrite(input: {
    scopeKey: string;
    target: PriorityWorkflowTarget;
    record: PriorityWorkflowRecord;
  }) {
    if (
      input.scopeKey !== this.#activeScopeKey ||
      input.record.version < 1 ||
      !sameTarget(input.record, input.target)
    ) {
      return false;
    }
    const generationKey =
      this.#mailboxGenerations.get(input.target.mailboxId)?.generationKey ??
      JSON.stringify(["write", this.#scopeGeneration, input.target.mailboxId]);
    return this.#acceptRecord(
      input.target,
      input.record,
      generationKey,
      "write",
    );
  }

  activateMailboxGeneration(input: {
    scopeKey: string;
    mailboxId: string;
    generationKey: string;
    targets: readonly PriorityWorkflowTarget[];
  }) {
    if (input.scopeKey !== this.#activeScopeKey) {
      return false;
    }
    const uniqueTargets = new Map<string, PriorityWorkflowTarget>();
    input.targets.forEach((target) => {
      if (target.mailboxId === input.mailboxId) {
        uniqueTargets.set(target.recordKey, target);
      }
    });
    return this.#activateMailboxGeneration(
      input.mailboxId,
      input.generationKey,
      uniqueTargets,
    );
  }

  beginMailboxHydration(input: {
    scopeKey: string;
    mailboxId: string;
    generationKey: string;
    targets: readonly PriorityWorkflowTarget[];
  }): PriorityWorkflowHydrationPlan | "empty" | "already_requested" | "stale" {
    if (input.scopeKey !== this.#activeScopeKey) {
      return "stale";
    }
    const uniqueTargets = new Map<string, PriorityWorkflowTarget>();
    input.targets.forEach((target) => {
      if (target.mailboxId === input.mailboxId) {
        uniqueTargets.set(target.recordKey, target);
      }
    });
    if (uniqueTargets.size === 0) {
      return "empty";
    }
    const current = this.#mailboxGenerations.get(input.mailboxId);
    if (
      current?.generationKey === input.generationKey &&
      current.requestGeneration > 0
    ) {
      return "already_requested";
    }

    if (current?.generationKey !== input.generationKey) {
      this.#activateMailboxGeneration(
        input.mailboxId,
        input.generationKey,
        uniqueTargets,
      );
    }

    this.#requestGeneration += 1;
    const requestGeneration = this.#requestGeneration;
    this.#mailboxGenerations.set(input.mailboxId, {
      generationKey: input.generationKey,
      requestGeneration,
    });
    const targets = [...uniqueTargets.values()];
    const batches: PriorityWorkflowTarget[][] = [];
    for (
      let index = 0;
      index < targets.length;
      index += PRIORITY_WORKFLOW_MAX_BATCH_IDENTITIES
    ) {
      batches.push(
        targets.slice(index, index + PRIORITY_WORKFLOW_MAX_BATCH_IDENTITIES),
      );
    }
    return {
      scopeKey: input.scopeKey,
      scopeGeneration: this.#scopeGeneration,
      mailboxId: input.mailboxId,
      generationKey: input.generationKey,
      requestGeneration,
      batches,
    };
  }

  acceptHydrationBatch(
    plan: PriorityWorkflowHydrationPlan,
    batch: readonly PriorityWorkflowTarget[],
    records: readonly PriorityWorkflowRecord[],
  ) {
    if (!this.#isCurrentPlan(plan) || batch.length !== records.length) {
      return [] as string[];
    }
    const acceptedRecordKeys: string[] = [];
    records.forEach((record, index) => {
      const target = batch[index];
      if (
        target &&
        target.mailboxId === plan.mailboxId &&
        this.#acceptRecord(
          target,
          record,
          plan.generationKey,
          "hydration",
        )
      ) {
        acceptedRecordKeys.push(target.recordKey);
      }
    });
    return acceptedRecordKeys;
  }

  isCurrentPlan(plan: PriorityWorkflowHydrationPlan) {
    return this.#isCurrentPlan(plan);
  }

  #isCurrentPlan(plan: PriorityWorkflowHydrationPlan) {
    const current = this.#mailboxGenerations.get(plan.mailboxId);
    return Boolean(
      plan.scopeKey === this.#activeScopeKey &&
        plan.scopeGeneration === this.#scopeGeneration &&
        current?.generationKey === plan.generationKey &&
        current.requestGeneration === plan.requestGeneration,
    );
  }

  #activateMailboxGeneration(
    mailboxId: string,
    generationKey: string,
    targets: ReadonlyMap<string, PriorityWorkflowTarget>,
  ) {
    const current = this.#mailboxGenerations.get(mailboxId);
    if (current?.generationKey === generationKey) {
      return false;
    }
    this.#mailboxGenerations.set(mailboxId, {
      generationKey,
      requestGeneration: 0,
    });
    for (const [recordKey, accepted] of this.#acceptedRecords) {
      if (accepted.record.mailboxId !== mailboxId) {
        continue;
      }
      if (accepted.source === "write" && targets.has(recordKey)) {
        this.#acceptedRecords.set(recordKey, {
          ...accepted,
          generationKey,
        });
      } else {
        this.#acceptedRecords.delete(recordKey);
      }
    }
    return true;
  }

  #acceptRecord(
    target: PriorityWorkflowTarget,
    record: PriorityWorkflowRecord,
    generationKey: string,
    source: "hydration" | "write",
  ) {
    if (!sameTarget(record, target)) {
      return false;
    }
    const acceptedVersion = this.#acceptedVersions.get(target.recordKey);
    const accepted = this.#acceptedRecords.get(target.recordKey);
    const latestRecord = this.#latestRecords.get(target.recordKey);
    if (
      acceptedVersion !== undefined &&
      (record.version < acceptedVersion ||
        (record.version === acceptedVersion &&
          (!latestRecord ||
            !recordsHaveEqualAuthority(record, latestRecord))))
    ) {
      return false;
    }
    if (acceptedVersion === undefined || record.version > acceptedVersion) {
      this.#acceptedVersions.set(target.recordKey, record.version);
      this.#latestRecords.set(target.recordKey, record);
    }
    this.#acceptedRecords.set(target.recordKey, {
      status: "ready",
      record,
      generationKey,
      source:
        record.version === acceptedVersion && accepted?.source === "write"
          ? "write"
          : source,
    });
    return true;
  }
}

export class PriorityWorkflowHydrationCoordinator {
  readonly #client: Pick<PriorityWorkflowAuthorityClient, "read">;
  readonly #store: PriorityWorkflowAuthorityStore;

  constructor(
    client: Pick<PriorityWorkflowAuthorityClient, "read">,
    store: PriorityWorkflowAuthorityStore,
  ) {
    this.#client = client;
    this.#store = store;
  }

  async hydrateMailbox(input: {
    scopeKey: string;
    mailboxId: string;
    generationKey: string;
    targets: readonly PriorityWorkflowTarget[];
  }): Promise<PriorityWorkflowHydrationOutcome> {
    const plan = this.#store.beginMailboxHydration(input);
    if (typeof plan === "string") {
      return { status: plan, generationKey: input.generationKey };
    }
    const results = await Promise.all(
      plan.batches.map(async (batch) => {
        try {
          return {
            batch,
            result: await this.#client.read({
              mailboxId: plan.mailboxId,
              identities: batch.map((target) => target.identity),
            }),
          };
        } catch {
          return {
            batch,
            result: {
              ok: false as const,
              error: {
                code: "workflow_read_failed",
                message: "Priority workflow authority is temporarily unavailable.",
                ambiguous: false,
              },
            },
          };
        }
      }),
    );
    if (!this.#store.isCurrentPlan(plan)) {
      return { status: "stale", generationKey: plan.generationKey };
    }
    const acceptedRecordKeys: string[] = [];
    const failures: PriorityWorkflowAuthorityError[] = [];
    results.forEach(({ batch, result }) => {
      if (!result.ok) {
        failures.push(result.error);
        return;
      }
      acceptedRecordKeys.push(
        ...this.#store.acceptHydrationBatch(plan, batch, result.value),
      );
    });
    if (failures.length === results.length) {
      return {
        status: "failed",
        generationKey: plan.generationKey,
        acceptedRecordKeys,
        failedBatchCount: failures.length,
        error: failures[0],
      };
    }
    return {
      status: failures.length > 0 ? "partial" : "hydrated",
      generationKey: plan.generationKey,
      acceptedRecordKeys,
      failedBatchCount: failures.length,
    };
  }
}

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
    commit: (record: PriorityWorkflowRecord) => boolean | void;
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
    commit: (record: PriorityWorkflowRecord) => boolean | void;
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
    if (input.commit(record) === false) {
      return { status: "stale", generation: input.generation };
    }
    return { status, record, generation: input.generation };
  }
}
