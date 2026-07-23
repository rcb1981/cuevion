import type {
  InboxConnection,
  InboxId,
  OnboardingState,
  ProviderId,
} from "../types/onboarding";
import type { InboxConnectionAttemptResult } from "./inboxConnectionApi";

export const CUSTOM_IMAP_ONBOARDING_POST_TIMEOUT_MS = 30_000;
export const CUSTOM_IMAP_ONBOARDING_READBACK_TIMEOUT_MS = 15_000;

export type CustomImapOnboardingAttemptPhase =
  | "posting"
  | "reconciling"
  | "reconciliation_required"
  | "checking";

export type CustomImapOnboardingAttemptGuard = Readonly<{
  attemptToken: symbol;
  onboardingInboxId: InboxId;
  phase: CustomImapOnboardingAttemptPhase;
  recovery: "check" | "reload";
  snapshot: CustomImapOnboardingAttemptSnapshot;
}>;

export type CustomImapOnboardingAttemptSnapshot = Readonly<{
  attemptToken: symbol;
  onboardingInboxId: InboxId;
  provider: "custom_imap";
  normalizedEmail: string;
  normalizedHost: string;
  port: string;
  normalizedUsername: string;
  ssl: true;
  passwordRevision: number;
  selectedInboxes: readonly InboxId[];
  selectedPositionIdentity: string;
  fingerprint: string;
}>;

export type CustomImapOnboardingReconciliationResult =
  | {
      status: "matched";
      connection: InboxConnection;
      serverMailboxId: string;
    }
  | { status: "absent" }
  | { status: "conflict" }
  | { status: "required" };

export type CustomImapOnboardingCurrentContext = Readonly<{
  mounted: boolean;
  onboardingInboxId: InboxId | null;
  provider: ProviderId | null;
  selectedPositionIdentity: string | null;
  fingerprint: string | null;
  passwordRevision: number;
}>;

type TimerHandle = ReturnType<typeof setTimeout>;

type AttemptRuntime = {
  readonly snapshot: CustomImapOnboardingAttemptSnapshot;
  expectedPasswordRevision: number;
  phase: CustomImapOnboardingAttemptPhase;
  recovery: "check" | "reload";
  controller: AbortController | null;
  timeoutId: TimerHandle | null;
};

type BoundedResult<T> =
  | { status: "settled"; value: T }
  | { status: "failed" }
  | { status: "timed_out" };

export type CustomImapOnboardingAttemptCoordinator = {
  start: (
    snapshot: CustomImapOnboardingAttemptSnapshot,
    password: string,
  ) => boolean;
  adoptReconciliationGuard: (
    guard: CustomImapOnboardingAttemptGuard,
  ) => boolean;
  retryReconciliation: () => boolean;
  dispose: () => void;
  getGuard: () => CustomImapOnboardingAttemptGuard | null;
};

type CustomImapOnboardingAttemptCoordinatorOptions = {
  getCurrentContext: (
    snapshot: CustomImapOnboardingAttemptSnapshot,
  ) => CustomImapOnboardingCurrentContext;
  post: (
    snapshot: CustomImapOnboardingAttemptSnapshot,
    password: string,
    signal: AbortSignal,
  ) => Promise<InboxConnectionAttemptResult>;
  reconcile: (
    snapshot: CustomImapOnboardingAttemptSnapshot,
    signal: AbortSignal,
  ) => Promise<CustomImapOnboardingReconciliationResult>;
  consumePassword: (
    snapshot: CustomImapOnboardingAttemptSnapshot,
    expectedRevision: number,
  ) => number | null;
  applyMatched: (
    snapshot: CustomImapOnboardingAttemptSnapshot,
    result: Extract<
      CustomImapOnboardingReconciliationResult,
      { status: "matched" }
    >,
  ) => void;
  applyAbsent: (
    snapshot: CustomImapOnboardingAttemptSnapshot,
    postResult: InboxConnectionAttemptResult | null,
  ) => void;
  applyReconciliationRequired: (
    snapshot: CustomImapOnboardingAttemptSnapshot,
  ) => void;
  onGuardChange: (
    guard: CustomImapOnboardingAttemptGuard | null,
  ) => void;
  postTimeoutMs?: number;
  readbackTimeoutMs?: number;
  setTimer?: (handler: () => void, timeoutMs: number) => TimerHandle;
  clearTimer?: (timeoutId: TimerHandle) => void;
};

function normalizeAttemptValue(value: string) {
  return value.trim().toLowerCase();
}

function normalizeUsername(value: string) {
  return value.trim();
}

export function createCustomImapSelectedPositionIdentity(
  selectedInboxes: readonly InboxId[],
  onboardingInboxId: InboxId,
) {
  return JSON.stringify({
    selectedInboxes,
    selectedIndex: selectedInboxes.indexOf(onboardingInboxId),
  });
}

export function createCustomImapOnboardingFingerprint({
  onboardingInboxId,
  selectedInboxes,
  connection,
}: {
  onboardingInboxId: InboxId;
  selectedInboxes: readonly InboxId[];
  connection: InboxConnection;
}) {
  const selectedPositionIdentity =
    createCustomImapSelectedPositionIdentity(
      selectedInboxes,
      onboardingInboxId,
    );
  return JSON.stringify({
    onboardingInboxId,
    selectedPositionIdentity,
    provider: connection.provider,
    normalizedEmail: normalizeAttemptValue(connection.email),
    normalizedHost: normalizeAttemptValue(connection.customImap.host),
    port: connection.customImap.port.trim(),
    normalizedUsername: normalizeUsername(
      connection.customImap.username,
    ),
    ssl: connection.customImap.ssl,
  });
}

export function createCustomImapOnboardingAttemptSnapshot({
  onboardingInboxId,
  selectedInboxes,
  connection,
  passwordRevision,
  attemptToken = Symbol("custom-imap-onboarding-attempt"),
}: {
  onboardingInboxId: InboxId;
  selectedInboxes: readonly InboxId[];
  connection: InboxConnection;
  passwordRevision: number;
  attemptToken?: symbol;
}): CustomImapOnboardingAttemptSnapshot {
  const selectedSnapshot = Object.freeze([...selectedInboxes]);
  return Object.freeze({
    attemptToken,
    onboardingInboxId,
    provider: "custom_imap",
    normalizedEmail: normalizeAttemptValue(connection.email),
    normalizedHost: normalizeAttemptValue(connection.customImap.host),
    port: connection.customImap.port.trim(),
    normalizedUsername: normalizeUsername(
      connection.customImap.username,
    ),
    ssl: true,
    passwordRevision,
    selectedInboxes: selectedSnapshot,
    selectedPositionIdentity:
      createCustomImapSelectedPositionIdentity(
        selectedSnapshot,
        onboardingInboxId,
      ),
    fingerprint: createCustomImapOnboardingFingerprint({
      onboardingInboxId,
      selectedInboxes: selectedSnapshot,
      connection,
    }),
  });
}

export function isCustomImapOnboardingAttemptCurrent(
  snapshot: CustomImapOnboardingAttemptSnapshot,
  expectedPasswordRevision: number,
  context: CustomImapOnboardingCurrentContext,
) {
  return (
    context.mounted &&
    context.onboardingInboxId === snapshot.onboardingInboxId &&
    context.provider === snapshot.provider &&
    context.selectedPositionIdentity ===
      snapshot.selectedPositionIdentity &&
    context.fingerprint === snapshot.fingerprint &&
    context.passwordRevision === expectedPasswordRevision
  );
}

export function doesCustomImapOnboardingSnapshotMatchState(
  snapshot: CustomImapOnboardingAttemptSnapshot,
  state: OnboardingState,
) {
  const connection = state.inboxConnections[snapshot.onboardingInboxId];
  return Boolean(
    connection &&
      state.selectedInboxes.length === snapshot.selectedInboxes.length &&
      state.selectedInboxes.every(
        (inboxId, index) => inboxId === snapshot.selectedInboxes[index],
      ) &&
      createCustomImapSelectedPositionIdentity(
        state.selectedInboxes,
        snapshot.onboardingInboxId,
      ) === snapshot.selectedPositionIdentity &&
      createCustomImapOnboardingFingerprint({
        onboardingInboxId: snapshot.onboardingInboxId,
        selectedInboxes: state.selectedInboxes,
        connection,
      }) === snapshot.fingerprint,
  );
}

export function isCustomImapOnboardingInteractionLocked(
  guard: CustomImapOnboardingAttemptGuard | null,
) {
  return guard !== null;
}

export function createCustomImapOnboardingAttemptCoordinator(
  options: CustomImapOnboardingAttemptCoordinatorOptions,
): CustomImapOnboardingAttemptCoordinator {
  const postTimeoutMs =
    options.postTimeoutMs ?? CUSTOM_IMAP_ONBOARDING_POST_TIMEOUT_MS;
  const readbackTimeoutMs =
    options.readbackTimeoutMs ??
    CUSTOM_IMAP_ONBOARDING_READBACK_TIMEOUT_MS;
  const setTimer =
    options.setTimer ??
    ((handler, timeoutMs) => setTimeout(handler, timeoutMs));
  const clearTimer =
    options.clearTimer ??
    ((timeoutId) => clearTimeout(timeoutId));

  let disposed = false;
  let current: AttemptRuntime | null = null;

  const isOwned = (runtime: AttemptRuntime) =>
    !disposed && current === runtime;

  const isCurrent = (runtime: AttemptRuntime) =>
    isOwned(runtime) &&
    isCustomImapOnboardingAttemptCurrent(
      runtime.snapshot,
      runtime.expectedPasswordRevision,
      options.getCurrentContext(runtime.snapshot),
    );

  const emitOwnedGuard = (runtime: AttemptRuntime) => {
    if (
      !isOwned(runtime) ||
      !options.getCurrentContext(runtime.snapshot).mounted
    ) {
      return false;
    }
    options.onGuardChange({
      attemptToken: runtime.snapshot.attemptToken,
      onboardingInboxId: runtime.snapshot.onboardingInboxId,
      phase: runtime.phase,
      recovery: runtime.recovery,
      snapshot: runtime.snapshot,
    });
    return true;
  };

  const emitGuard = (runtime: AttemptRuntime) =>
    isCurrent(runtime) && emitOwnedGuard(runtime);

  const clearRuntimeOperation = (runtime: AttemptRuntime) => {
    if (runtime.timeoutId !== null) {
      clearTimer(runtime.timeoutId);
      runtime.timeoutId = null;
    }
    runtime.controller = null;
  };

  const runBounded = async <T>(
    runtime: AttemptRuntime,
    timeoutMs: number,
    operation: (signal: AbortSignal) => Promise<T>,
  ): Promise<BoundedResult<T>> => {
    const controller = new AbortController();
    runtime.controller = controller;

    let settleTimeout!: (result: BoundedResult<T>) => void;
    const timeout = new Promise<BoundedResult<T>>((resolve) => {
      settleTimeout = resolve;
    });
    runtime.timeoutId = setTimer(() => {
      controller.abort();
      settleTimeout({ status: "timed_out" });
    }, timeoutMs);

    let pendingOperation: Promise<T>;
    try {
      pendingOperation = operation(controller.signal);
    } catch (error) {
      pendingOperation = Promise.reject(error);
    }
    const operationResult = Promise.resolve(pendingOperation)
      .then<BoundedResult<T>>((value) => ({
        status: "settled",
        value,
      }))
      .catch<BoundedResult<T>>(() => ({ status: "failed" }));

    const result = await Promise.race([operationResult, timeout]);
    clearRuntimeOperation(runtime);
    return result;
  };

  const retireOwnedRuntime = (runtime: AttemptRuntime) => {
    if (
      !isOwned(runtime) ||
      !options.getCurrentContext(runtime.snapshot).mounted
    ) {
      return;
    }
    current = null;
    options.onGuardChange(null);
  };

  const finish = (runtime: AttemptRuntime) => {
    if (isCurrent(runtime)) {
      retireOwnedRuntime(runtime);
    }
  };

  const applyReconciliationResult = (
    runtime: AttemptRuntime,
    result: CustomImapOnboardingReconciliationResult,
    postResult: InboxConnectionAttemptResult | null,
  ) => {
    if (
      !isOwned(runtime) ||
      !options.getCurrentContext(runtime.snapshot).mounted
    ) {
      return;
    }

    if (!isCurrent(runtime)) {
      if (result.status === "absent") {
        retireOwnedRuntime(runtime);
        return;
      }
      runtime.phase = "reconciliation_required";
      runtime.recovery = "reload";
      emitOwnedGuard(runtime);
      return;
    }

    if (result.status === "matched") {
      options.applyMatched(runtime.snapshot, result);
      finish(runtime);
      return;
    }

    if (result.status === "absent") {
      options.applyAbsent(runtime.snapshot, postResult);
      finish(runtime);
      return;
    }

    runtime.phase = "reconciliation_required";
    runtime.recovery = "check";
    options.applyReconciliationRequired(runtime.snapshot);
    emitGuard(runtime);
  };

  const reconcile = async (
    runtime: AttemptRuntime,
    postResult: InboxConnectionAttemptResult | null,
    phase: Extract<CustomImapOnboardingAttemptPhase, "reconciling" | "checking">,
  ) => {
    if (isCurrent(runtime)) {
      runtime.phase = phase;
      emitGuard(runtime);
    } else if (
      isOwned(runtime) &&
      options.getCurrentContext(runtime.snapshot).mounted
    ) {
      runtime.phase = phase;
      emitOwnedGuard(runtime);
    }

    const readback = await runBounded(
      runtime,
      readbackTimeoutMs,
      (signal) => options.reconcile(runtime.snapshot, signal),
    );
    if (
      !isOwned(runtime) ||
      !options.getCurrentContext(runtime.snapshot).mounted
    ) {
      return;
    }

    applyReconciliationResult(
      runtime,
      readback.status === "settled"
        ? readback.value
        : { status: "required" },
      postResult,
    );
  };

  const executePost = async (
    runtime: AttemptRuntime,
    password: string,
  ) => {
    const postPromise = runBounded(
      runtime,
      postTimeoutMs,
      (signal) => options.post(runtime.snapshot, password, signal),
    );
    password = "";
    if (isCurrent(runtime)) {
      const nextRevision = options.consumePassword(
        runtime.snapshot,
        runtime.expectedPasswordRevision,
      );
      if (nextRevision !== null) {
        runtime.expectedPasswordRevision = nextRevision;
      }
    }
    const post = await postPromise;
    const postResult = post.status === "settled" ? post.value : null;

    if (disposed || current !== runtime) {
      return;
    }
    await reconcile(runtime, postResult, "reconciling");
  };

  return {
    start(snapshot, password) {
      if (
        disposed ||
        current !== null ||
        !password ||
        !isCustomImapOnboardingAttemptCurrent(
          snapshot,
          snapshot.passwordRevision,
          options.getCurrentContext(snapshot),
        )
      ) {
        return false;
      }

      const runtime: AttemptRuntime = {
        snapshot,
        expectedPasswordRevision: snapshot.passwordRevision,
        phase: "posting",
        recovery: "check",
        controller: null,
        timeoutId: null,
      };
      current = runtime;
      emitGuard(runtime);
      void executePost(runtime, password);
      return true;
    },

    adoptReconciliationGuard(guard) {
      if (
        disposed ||
        current !== null ||
        guard.attemptToken !== guard.snapshot.attemptToken ||
        guard.onboardingInboxId !==
          guard.snapshot.onboardingInboxId
      ) {
        return false;
      }
      const context = options.getCurrentContext(guard.snapshot);
      const runtime: AttemptRuntime = {
        snapshot: guard.snapshot,
        expectedPasswordRevision: context.passwordRevision,
        phase: "reconciliation_required",
        recovery: guard.recovery,
        controller: null,
        timeoutId: null,
      };
      current = runtime;
      if (isCurrent(runtime) && emitGuard(runtime)) {
        return true;
      }
      runtime.recovery = "reload";
      if (!emitOwnedGuard(runtime)) {
        current = null;
        return false;
      }
      return true;
    },

    retryReconciliation() {
      const runtime = current;
      if (
        !runtime ||
        runtime.phase !== "reconciliation_required" ||
        runtime.recovery !== "check" ||
        !isOwned(runtime) ||
        !options.getCurrentContext(runtime.snapshot).mounted
      ) {
        return false;
      }
      void reconcile(runtime, null, "checking");
      return true;
    },

    dispose() {
      if (disposed) {
        return;
      }
      disposed = true;
      if (current?.controller) {
        current.controller.abort();
      }
      if (current?.timeoutId !== null && current?.timeoutId !== undefined) {
        clearTimer(current.timeoutId);
        current.timeoutId = null;
      }
      if (current) {
        current.controller = null;
      }
      current = null;
    },

    getGuard() {
      if (!current) {
        return null;
      }
      return {
        attemptToken: current.snapshot.attemptToken,
        onboardingInboxId: current.snapshot.onboardingInboxId,
        phase: current.phase,
        recovery: current.recovery,
        snapshot: current.snapshot,
      };
    },
  };
}
