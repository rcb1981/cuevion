import assert from "node:assert/strict";
import "sucrase/register/tsx";
import { initialOnboardingState } from "./data/onboardingOptions";
import type {
  UserAccountConfig,
  UserAccountConfigReadResult,
  UserAccountConfigSaveResult,
} from "./lib/userConfigApi";

const { accountConfigOrchestration } = require("./App.tsx") as typeof import("./App");

const ACCOUNT_KEY = "member@example.com";
const ONBOARDING_SESSION_KEY = "label-inbox-ai-onboarding-state";
const ONBOARDING_DRAFT_KEY = "label-inbox-ai-onboarding-draft-state";
const APP_VIEW_KEY = "cuevion-app-view";
const MANAGED_INBOXES_KEY = "cuevion-managed-inboxes";
const MAILBOX_TITLES_KEY = "cuevion-mailbox-title-overrides";

class MemoryStorage {
  private readonly values: Map<string, string>;
  readonly reads: string[] = [];
  readonly mutations: Array<{ type: "set" | "remove"; key: string }> = [];

  constructor(initialValues: Record<string, string> = {}) {
    this.values = new Map(Object.entries(initialValues));
  }

  getItem(key: string) {
    this.reads.push(key);
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.mutations.push({ type: "set", key });
    this.values.set(key, value);
  }

  removeItem(key: string) {
    this.mutations.push({ type: "remove", key });
    this.values.delete(key);
  }

  snapshot() {
    return Object.fromEntries(this.values);
  }
}

function createLegacyStorage() {
  return new MemoryStorage({
    [ONBOARDING_SESSION_KEY]: JSON.stringify({
      completed: true,
      state: { primaryRole: "stale-local", selectedInboxes: ["legacy"] },
    }),
    [ONBOARDING_DRAFT_KEY]: JSON.stringify({ state: { primaryRole: "draft" } }),
    [APP_VIEW_KEY]: "workspace",
    [MANAGED_INBOXES_KEY]: JSON.stringify([
      { id: "legacy", email: "legacy@example.com" },
    ]),
    [MAILBOX_TITLES_KEY]: JSON.stringify({ legacy: "Legacy" }),
  });
}

const emptyCompletedState = {
  ...initialOnboardingState,
  selectedInboxes: [],
  internalRole: null,
};

const selectedCompletedState = {
  ...emptyCompletedState,
  selectedInboxes: ["server-inbox"],
  inboxConnections: {
    ...emptyCompletedState.inboxConnections,
    "server-inbox": {
      provider: "google",
      email: "server@example.com",
      connected: true,
      connectionMethod: "oauth",
      connectionStatus: "connected",
      connectionMessage: null,
      customImap: null,
      customSmtp: null,
    },
  },
};

function completedConfig(
  onboardingState: typeof initialOnboardingState = emptyCompletedState,
): UserAccountConfig {
  return {
    v: 1,
    onboardingSession: { completed: true, state: onboardingState },
    managedInboxes: [],
    mailboxTitleOverrides: {},
    primaryManagedInboxId: null,
    mailboxFocusPreferenceOverrides: {},
    inboxSignatures: {},
    smartFolders: [],
    uiPreferences: {},
    displayNameOverrides: { [ACCOUNT_KEY]: "Server Member" },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

async function flushAsyncWork() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function hydrateResult(
  result: UserAccountConfigReadResult,
  storage: MemoryStorage,
  resetOnboarding = false,
  clearResetQuery: () => void = () => undefined,
) {
  return accountConfigOrchestration
    .createHydrator(async () => result)
    .hydrate({
      accountStorageOwnerKey: ACCOUNT_KEY,
      storage,
      resetOnboarding,
      clearResetQuery,
    });
}

let passedTests = 0;

async function test(name: string, operation: () => void | Promise<void>) {
  try {
    await operation();
    passedTests += 1;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`${name}: ${message}`);
  }
}

async function run() {
  await test("session disposition preserves guest flow", () => {
    assert.equal(
      accountConfigOrchestration.resolveSessionDisposition({
        hasInviteRoute: false,
        sessionStatus: "authenticated",
        userType: "guest",
        accountStorageKey: "",
      }),
      "guest",
    );
    assert.equal(
      accountConfigOrchestration.resolveSessionDisposition({
        hasInviteRoute: false,
        sessionStatus: "authenticated",
        userType: "member",
        accountStorageKey: ACCOUNT_KEY,
      }),
      "member",
    );
  });

  await test("found replaces stale mirrors and builds the real echo payload", async () => {
    const storage = createLegacyStorage();
    const outcome = await hydrateResult(
      { status: "found", config: completedConfig() },
      storage,
    );
    assert.equal(outcome.status, "found");
    if (outcome.status !== "found") throw new Error("Expected found outcome");
    assert.equal(outcome.accountState.view, "workspace");
    assert.equal(outcome.accountState.onboardingState.internalRole, null);
    assert.equal(storage.getItem(APP_VIEW_KEY), "workspace");
    assert.equal(storage.getItem(ONBOARDING_DRAFT_KEY), null);
    assert.equal(storage.getItem(MANAGED_INBOXES_KEY), "[]");
    assert.deepEqual(outcome.expectedWorkspaceHydrationEcho?.managedInboxes, []);
    assert.deepEqual(outcome.expectedWorkspaceHydrationEcho?.uiPreferences, {
      themeMode: "Light",
      aiSuggestionsEnabled: true,
      inboxChangesEnabled: true,
      teamActivityEnabled: true,
    });
    assert.equal("v" in outcome.expectedWorkspaceHydrationEcho!, false);
    assert.equal("displayNameOverrides" in outcome.expectedWorkspaceHydrationEcho!, false);
  });

  await test("explicit empty managed list remains authoritative", async () => {
    const storage = createLegacyStorage();
    const outcome = await hydrateResult(
      { status: "found", config: completedConfig(selectedCompletedState) },
      storage,
    );
    assert.equal(outcome.status, "found");
    if (outcome.status !== "found") throw new Error("Expected found outcome");
    assert.equal(storage.getItem(MANAGED_INBOXES_KEY), "[]");
    assert.equal(outcome.expectedWorkspaceHydrationEcho?.managedInboxes?.length, 1);
    assert.equal(
      (outcome.expectedWorkspaceHydrationEcho?.managedInboxes?.[0] as { email?: string }).email,
      "server@example.com",
    );
  });

  await test("found incomplete neutralizes stale local completion", async () => {
    const storage = createLegacyStorage();
    const outcome = await hydrateResult(
      { status: "found", config: { onboardingSession: {}, managedInboxes: [] } },
      storage,
    );
    assert.equal(outcome.status, "found");
    if (outcome.status !== "found") throw new Error("Expected found outcome");
    assert.equal(outcome.accountState.view, "onboarding");
    assert.equal(outcome.accountState.persistedOnboardingSession, null);
    assert.equal(outcome.expectedWorkspaceHydrationEcho, null);
    assert.equal(storage.getItem(ONBOARDING_SESSION_KEY), null);
    assert.equal(storage.getItem(ONBOARDING_DRAFT_KEY), null);
    assert.equal(storage.getItem(APP_VIEW_KEY), "onboarding");
  });

  await test("missing is clean and read-write silent", async () => {
    const storage = createLegacyStorage();
    const before = storage.snapshot();
    const outcome = await hydrateResult(
      { status: "missing", config: null },
      storage,
    );
    assert.equal(outcome.status, "missing");
    if (outcome.status !== "missing") throw new Error("Expected missing outcome");
    assert.equal(outcome.clearResetQuery, false);
    assert.equal(outcome.didResetOnboarding, false);
    assert.deepEqual(storage.reads, []);
    assert.deepEqual(storage.mutations, []);
    assert.deepEqual(storage.snapshot(), before);
  });

  await test("missing reset clears only local onboarding keys", async () => {
    const storage = createLegacyStorage();
    let clearedResetQueries = 0;
    const managedInboxes = storage.getItem(MANAGED_INBOXES_KEY);
    const mailboxTitles = storage.getItem(MAILBOX_TITLES_KEY);
    storage.reads.length = 0;
    const outcome = await hydrateResult(
      { status: "missing", config: null },
      storage,
      true,
      () => {
        clearedResetQueries += 1;
      },
    );
    assert.equal(outcome.status, "missing");
    if (outcome.status !== "missing") throw new Error("Expected missing outcome");
    assert.equal(outcome.clearResetQuery, true);
    assert.equal(outcome.didResetOnboarding, false);
    assert.equal(clearedResetQueries, 1);
    assert.equal(storage.getItem(ONBOARDING_SESSION_KEY), null);
    assert.equal(storage.getItem(ONBOARDING_DRAFT_KEY), null);
    assert.equal(storage.getItem(APP_VIEW_KEY), "onboarding");
    assert.equal(storage.getItem(MANAGED_INBOXES_KEY), managedInboxes);
    assert.equal(storage.getItem(MAILBOX_TITLES_KEY), mailboxTitles);
  });

  await test("reset runs after found and preserves mailbox metadata", async () => {
    const storage = createLegacyStorage();
    let clearedResetQueries = 0;
    const outcome = await hydrateResult(
      {
        status: "found",
        config: {
          ...completedConfig(selectedCompletedState),
          managedInboxes: [{ id: "server", email: "server@example.com" }],
          mailboxTitleOverrides: { server: "Server title" },
        },
      },
      storage,
      true,
      () => {
        clearedResetQueries += 1;
      },
    );
    assert.equal(outcome.status, "found");
    if (outcome.status !== "found") throw new Error("Expected found outcome");
    assert.equal(outcome.didResetOnboarding, true);
    assert.equal(
      clearedResetQueries,
      0,
      "the reset query must remain until the server reset is durably saved",
    );
    assert.equal(outcome.accountState.view, "onboarding");
    assert.equal(outcome.expectedWorkspaceHydrationEcho, null);
    assert.equal(storage.getItem(ONBOARDING_SESSION_KEY), null);
    assert.equal(storage.getItem(APP_VIEW_KEY), "onboarding");
    assert.deepEqual(JSON.parse(storage.getItem(MANAGED_INBOXES_KEY)!), [
      { id: "server", email: "server@example.com" },
    ]);
    assert.deepEqual(JSON.parse(storage.getItem(MAILBOX_TITLES_KEY)!), {
      server: "Server title",
    });

    let saveAttempts = 0;
    const queue = accountConfigOrchestration.createSaveQueue({
      save: async (config) => {
        saveAttempts += 1;
        return saveAttempts === 1
          ? {
              status: "network_error" as const,
              error: { code: "network_error", message: "Offline" },
            }
          : { status: "found" as const, config };
      },
      onClean: () => {
        clearedResetQueries += 1;
      },
    });
    queue.reset(ACCOUNT_KEY);
    queue.markDirty(ACCOUNT_KEY);
    queue.enqueue({ accountKey: ACCOUNT_KEY, config: { onboardingSession: {} } });
    await flushAsyncWork();
    assert.equal(clearedResetQueries, 0);
    assert.equal(queue.isDirty(ACCOUNT_KEY), true);
    queue.enqueue({ accountKey: ACCOUNT_KEY, config: { onboardingSession: {} } });
    await flushAsyncWork();
    assert.equal(clearedResetQueries, 1);
  });

  await test("errors and unauthorized never touch storage", async () => {
    const results: UserAccountConfigReadResult[] = [
      { status: "unavailable", error: { code: "config_unavailable", message: "x" } },
      { status: "invalid", error: { code: "config_invalid", message: "x" } },
      {
        status: "authentication_unavailable",
        error: { code: "authentication_unavailable", message: "x" },
      },
      { status: "malformed_response", error: { code: "malformed_response", message: "x" } },
      { status: "network_error", error: { code: "network_error", message: "x" } },
      { status: "unauthorized", error: { code: "unauthorized", message: "x" } },
    ];
    for (const result of results) {
      const storage = createLegacyStorage();
      const before = storage.snapshot();
      const outcome = await hydrateResult(
        result,
        storage,
      );
      assert.equal(outcome.status, result.status === "unauthorized" ? "unauthorized" : "error");
      assert.deepEqual(storage.mutations, []);
      assert.deepEqual(storage.snapshot(), before);
    }
  });

  await test("storage exceptions fail closed", async () => {
    const storage = createLegacyStorage();
    storage.setItem = () => {
      throw new Error("quota exceeded");
    };
    const outcome = await hydrateResult(
      { status: "found", config: completedConfig() },
      storage,
    );
    assert.deepEqual(outcome, { status: "error", errorStatus: "unavailable" });
  });

  await test("hydration never dirties or enqueues the save coordinator", async () => {
    let saveCalls = 0;
    const queue = accountConfigOrchestration.createSaveQueue({
      save: async (config) => {
        saveCalls += 1;
        return { status: "found", config };
      },
    });
    const results: UserAccountConfigReadResult[] = [
      { status: "found", config: completedConfig() },
      { status: "missing", config: null },
      { status: "network_error", error: { code: "network_error", message: "x" } },
    ];

    for (const result of results) {
      queue.reset(ACCOUNT_KEY);
      await hydrateResult(result, createLegacyStorage());
      assert.equal(queue.isDirty(ACCOUNT_KEY), false);
      assert.equal(
        queue.enqueue({ accountKey: ACCOUNT_KEY, config: { email: "startup" } }),
        false,
      );
    }
    await flushAsyncWork();
    assert.equal(saveCalls, 0);
  });

  await test("retry cancels a stale GET before it can apply", async () => {
    const loads: Array<ReturnType<typeof deferred<UserAccountConfigReadResult>>> = [];
    const hydrator = accountConfigOrchestration.createHydrator(() => {
      const load = deferred<UserAccountConfigReadResult>();
      loads.push(load);
      return load.promise;
    });
    const storage = createLegacyStorage();
    let clearedResetQueries = 0;
    const options = {
      accountStorageOwnerKey: ACCOUNT_KEY,
      storage,
      resetOnboarding: false,
      clearResetQuery: () => {
        clearedResetQueries += 1;
      },
    };

    const staleHydration = hydrator.hydrate(options);
    const retryHydration = hydrator.hydrate(options);
    loads[1].resolve({
      status: "found",
      config: { ...completedConfig(), mailboxTitleOverrides: { winner: "retry" } },
    });
    const retryOutcome = await retryHydration;
    assert.equal(retryOutcome.status, "found");
    assert.deepEqual(JSON.parse(storage.getItem(MAILBOX_TITLES_KEY)!), {
      winner: "retry",
    });
    assert.equal(clearedResetQueries, 0);

    loads[0].resolve({
      status: "found",
      config: { ...completedConfig(), mailboxTitleOverrides: { stale: "first" } },
    });
    const staleOutcome = await staleHydration;
    assert.equal(staleOutcome.status, "cancelled");
    assert.deepEqual(JSON.parse(storage.getItem(MAILBOX_TITLES_KEY)!), {
      winner: "retry",
    });
    assert.equal(clearedResetQueries, 0);
  });

  await test("save coordinator serializes, coalesces and retains dirty failures", async () => {
    const pendingSaves: Array<ReturnType<typeof deferred<UserAccountConfigSaveResult>>> = [];
    const savedConfigs: UserAccountConfig[] = [];
    let activeSaves = 0;
    let maximumActiveSaves = 0;
    const queue = accountConfigOrchestration.createSaveQueue({
      save: async (config) => {
        savedConfigs.push(config);
        const pendingSave = deferred<UserAccountConfigSaveResult>();
        pendingSaves.push(pendingSave);
        activeSaves += 1;
        maximumActiveSaves = Math.max(maximumActiveSaves, activeSaves);
        try {
          return await pendingSave.promise;
        } finally {
          activeSaves -= 1;
        }
      },
    });
    queue.reset(ACCOUNT_KEY);
    assert.equal(queue.markDirty(ACCOUNT_KEY), 1);
    queue.enqueue({ accountKey: ACCOUNT_KEY, config: { email: "one" } });
    assert.equal(queue.markDirty(ACCOUNT_KEY), 2);
    queue.enqueue({ accountKey: ACCOUNT_KEY, config: { email: "two" } });
    assert.equal(queue.markDirty(ACCOUNT_KEY), 3);
    queue.enqueue({ accountKey: ACCOUNT_KEY, config: { email: "three" } });
    assert.equal(savedConfigs.length, 1);

    pendingSaves[0].resolve({ status: "found", config: savedConfigs[0] });
    await flushAsyncWork();
    assert.equal(savedConfigs.length, 2);
    assert.equal(savedConfigs[1].email, "three");
    assert.equal(queue.isDirty(ACCOUNT_KEY), true);
    pendingSaves[1].resolve({ status: "found", config: savedConfigs[1] });
    await flushAsyncWork();
    assert.equal(queue.isDirty(ACCOUNT_KEY), false);

    queue.markDirty(ACCOUNT_KEY);
    queue.enqueue({ accountKey: ACCOUNT_KEY, config: { email: "fails" } });
    pendingSaves[2].resolve({
      status: "network_error",
      error: { code: "network_error", message: "Offline" },
    });
    await flushAsyncWork();
    assert.equal(queue.isDirty(ACCOUNT_KEY), true);
    queue.enqueue({ accountKey: ACCOUNT_KEY, config: { email: "retry" } });
    pendingSaves[3].resolve({ status: "found", config: savedConfigs[3] });
    await flushAsyncWork();
    assert.equal(queue.isDirty(ACCOUNT_KEY), false);

    queue.markDirty(ACCOUNT_KEY);
    queue.enqueue({ accountKey: ACCOUNT_KEY, config: { email: "old-account" } });
    const switchStart = savedConfigs.length;
    queue.reset("second@example.com");
    queue.markDirty("second@example.com");
    queue.enqueue({ accountKey: "second@example.com", config: { email: "new-account" } });
    assert.equal(savedConfigs.length, switchStart);
    pendingSaves[4].resolve({ status: "found", config: savedConfigs[4] });
    await flushAsyncWork();
    assert.equal(savedConfigs.length, switchStart + 1);
    assert.equal(queue.isDirty("second@example.com"), true);
    pendingSaves[5].resolve({ status: "found", config: savedConfigs[5] });
    await flushAsyncWork();
    assert.equal(queue.isDirty("second@example.com"), false);
    assert.equal(maximumActiveSaves, 1);
  });

  console.log(`${passedTests} App startup orchestration tests passed`);
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
