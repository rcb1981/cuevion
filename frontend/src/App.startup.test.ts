import assert from "node:assert/strict";
import "sucrase/register/tsx.js";
import { initialOnboardingState } from "./data/onboardingOptions";
import {
  ONBOARDING_STEP_MAX,
  type OnboardingSessionV1,
  type OnboardingState,
} from "./types/onboarding";
import {
  setUserAccountConfigHydrationEchoExpectation,
  type UserAccountConfig,
  type UserAccountConfigReadResult,
  type UserAccountConfigSaveResult,
} from "./lib/userConfigApi";

const { accountConfigOrchestration } = require("./App.tsx") as typeof import("./App");
const { onboardingFlowProgression } = require(
  "./components/onboarding/OnboardingFlow.tsx"
) as typeof import("./components/onboarding/OnboardingFlow");

const ACCOUNT_KEY = "member@example.com";
const SECOND_ACCOUNT_KEY = "second@example.com";
const ONBOARDING_SESSION_KEY = "label-inbox-ai-onboarding-state";
const ONBOARDING_DRAFT_KEY = "label-inbox-ai-onboarding-draft-state";
const APP_VIEW_KEY = "cuevion-app-view";
const MANAGED_INBOXES_KEY = "cuevion-managed-inboxes";
const MAILBOX_TITLES_KEY = "cuevion-mailbox-title-overrides";
const GLOBAL_ONBOARDING_KEYS = new Set([
  ONBOARDING_SESSION_KEY,
  ONBOARDING_DRAFT_KEY,
  APP_VIEW_KEY,
]);

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

function assertGlobalOnboardingStorageUntouched(
  storage: MemoryStorage,
  before: Record<string, string>,
) {
  const after = storage.snapshot();
  for (const key of GLOBAL_ONBOARDING_KEYS) {
    assert.equal(after[key], before[key]);
  }
  assert.equal(
    storage.reads.some((key) => GLOBAL_ONBOARDING_KEYS.has(key)),
    false,
  );
  assert.equal(
    storage.mutations.some(({ key }) => GLOBAL_ONBOARDING_KEYS.has(key)),
    false,
  );
}

function createLegacyStorage() {
  return new MemoryStorage({
    [ONBOARDING_SESSION_KEY]: JSON.stringify({
      completed: true,
      state: {
        ...initialOnboardingState,
        primaryRole: "label_owner",
        selectedInboxes: ["main"],
        inboxConnections: {
          main: {
            provider: "google",
            email: "stale-local@example.com",
            customImap: { password: "stale-local-secret" },
          },
        },
      },
    }),
    [ONBOARDING_DRAFT_KEY]: JSON.stringify({
      schemaVersion: 1,
      completed: false,
      currentStep: 3,
      choices: { primaryRole: "producer" },
    }),
    [APP_VIEW_KEY]: "workspace",
    [MANAGED_INBOXES_KEY]: JSON.stringify([
      { id: "legacy", email: "legacy@example.com" },
    ]),
    [MAILBOX_TITLES_KEY]: JSON.stringify({ legacy: "Legacy" }),
  });
}

const cleanState = accountConfigOrchestration.hydrateChoices(
  accountConfigOrchestration.projectChoices(initialOnboardingState),
);
const selectedChoiceState = accountConfigOrchestration.hydrateChoices({
  ...accountConfigOrchestration.projectChoices(initialOnboardingState),
  primaryRole: "dj",
  secondaryRole: "producer",
  primaryInbox: "main",
  primaryInboxType: "work",
  inboxCount: "2",
  selectedInboxes: ["main"],
});
const selectedState: OnboardingState = {
  ...selectedChoiceState,
  inboxConnections: {
    ...selectedChoiceState.inboxConnections,
    main: {
      provider: "google",
      email: "private@example.com",
      connected: true,
      connectionMethod: "oauth",
      connectionStatus: "connected",
      connectionMessage: null,
      oauthAuthorizationUrl: "https://oauth.example.test/private",
      accessToken: "oauth-access-secret",
      customImap: {
        host: "imap.example.com",
        port: "993",
        ssl: true,
        username: "private@example.com",
        password: "imap-secret",
      },
      customSmtp: {
        host: "smtp.example.com",
        port: "587",
        security: "starttls",
        username: "private@example.com",
        password: "smtp-secret",
        useSameCredentials: false,
      },
    },
  },
};

function incompleteSession(
  currentStep = 1,
  state: OnboardingState = selectedState,
): OnboardingSessionV1 {
  return accountConfigOrchestration.createIncompleteSession(state, currentStep);
}

function completedV1Session(
  currentStep = ONBOARDING_STEP_MAX,
  state: OnboardingState = selectedState,
): OnboardingSessionV1 {
  const session = incompleteSession(currentStep, state);
  return { ...session, completed: true };
}

function completedLegacyConfig(
  state: OnboardingState = selectedState,
): UserAccountConfig {
  return {
    v: 1,
    onboardingSession: { completed: true, state },
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
  accountStorageOwnerKey = ACCOUNT_KEY,
) {
  return accountConfigOrchestration
    .createHydrator(async () => result)
    .hydrate({
      accountStorageOwnerKey,
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
  await test("member startup is clean before server hydration", () => {
    const storage = createLegacyStorage();
    const accountState = accountConfigOrchestration.createCleanStartupState();

    assert.equal(accountState.persistedOnboardingSession, null);
    assert.equal(accountState.onboardingStep, 0);
    assert.equal(accountState.view, "onboarding");
    assert.equal(accountState.userConfig, null);
    assert.deepEqual(storage.reads, []);
    assert.deepEqual(storage.mutations, []);
  });

  await test("session disposition preserves guest flow without config work", () => {
    const storage = new MemoryStorage();
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
    assert.deepEqual(storage.reads, []);
    assert.deepEqual(storage.mutations, []);
  });

  await test("guest and invite progress is identity-scoped and never falls back to member globals", async () => {
    const storage = createLegacyStorage();
    const before = storage.snapshot();
    const previousFetch = globalThis.fetch;
    let configFetches = 0;
    globalThis.fetch = (async () => {
      configFetches += 1;
      throw new Error("Guest-local hydration must not call user config");
    }) as typeof fetch;

    try {
      const memberOutcome = await hydrateResult(
        {
          status: "found",
          config: { onboardingSession: incompleteSession(2) },
        },
        storage,
      );
      assert.equal(memberOutcome.status, "found");
      if (memberOutcome.status !== "found") {
        throw new Error("Expected member hydration to succeed");
      }
      assert.equal(memberOutcome.accountState.onboardingStep, 2);
      assert.equal(memberOutcome.accountState.onboardingState.primaryRole, "dj");
      assertGlobalOnboardingStorageUntouched(storage, before);

      const guestAScope = accountConfigOrchestration.resolveLocalOnboardingIdentityScope({
        userType: "guest",
        userEmail: "guest-a@example.com",
        collaborationInvite: {
          mode: "invite",
          inviteToken: "secret-invite-token-a",
          messageId: "message-a",
          inviteeEmail: "guest-a@example.com",
        },
      });
      assert.notEqual(guestAScope, null);
      if (!guestAScope?.storageKeys) {
        throw new Error("Expected guest A to have safe scoped storage keys");
      }

      const firstGuestAHydration =
        accountConfigOrchestration.hydrateLocalOnboardingScope(
          guestAScope,
          storage,
        );
      assert.equal(firstGuestAHydration.onboardingStep, 0);
      assert.equal(firstGuestAHydration.onboardingState.primaryRole, cleanState.primaryRole);
      assert.equal(firstGuestAHydration.persistedOnboardingSession, null);

      const guestAState: OnboardingState = {
        ...cleanState,
        primaryRole: "producer",
      };
      accountConfigOrchestration.writeOnboardingSessionMirror(
        incompleteSession(2, guestAState),
        storage,
        guestAScope.storageKeys,
      );
      const restoredGuestA = accountConfigOrchestration.hydrateLocalOnboardingScope(
        guestAScope,
        storage,
      );
      assert.equal(restoredGuestA.onboardingStep, 2);
      assert.equal(restoredGuestA.onboardingState.primaryRole, "producer");

      const guestBScope = accountConfigOrchestration.resolveLocalOnboardingIdentityScope({
        userType: "guest",
        userEmail: "guest-b@example.com",
        collaborationInvite: {
          mode: "invite",
          inviteToken: "secret-invite-token-b",
          messageId: "message-b",
          inviteeEmail: "guest-b@example.com",
        },
      });
      assert.notEqual(guestBScope, null);
      if (!guestBScope) throw new Error("Expected guest B scope");
      assert.notEqual(guestBScope.hydrationKey, guestAScope.hydrationKey);
      const guestBHydration = accountConfigOrchestration.hydrateLocalOnboardingScope(
        guestBScope,
        storage,
      );
      assert.equal(guestBHydration.onboardingStep, 0);
      assert.equal(guestBHydration.onboardingState.primaryRole, cleanState.primaryRole);
      assert.equal(guestBHydration.persistedOnboardingSession, null);

      const guestAReturn = accountConfigOrchestration.hydrateLocalOnboardingScope(
        guestAScope,
        storage,
      );
      assert.equal(guestAReturn.onboardingStep, 2);
      assert.equal(guestAReturn.onboardingState.primaryRole, "producer");

      const emailGuestA =
        accountConfigOrchestration.resolveLocalOnboardingIdentityScope({
          userType: "guest",
          userEmail: "guest-a@example.com",
        });
      const emailGuestB =
        accountConfigOrchestration.resolveLocalOnboardingIdentityScope({
          userType: "guest",
          userEmail: "guest-b@example.com",
        });
      assert.notEqual(emailGuestA?.hydrationKey, emailGuestB?.hydrationKey);
      assert.notDeepEqual(emailGuestA?.storageKeys, emailGuestB?.storageKeys);

      const emailLessGuestA =
        accountConfigOrchestration.resolveLocalOnboardingIdentityScope({
          userType: "guest",
          ephemeralIdentity: "guest-session-a",
        });
      const emailLessGuestB =
        accountConfigOrchestration.resolveLocalOnboardingIdentityScope({
          userType: "guest",
          ephemeralIdentity: "guest-session-b",
        });
      assert.notEqual(
        emailLessGuestA?.hydrationKey,
        emailLessGuestB?.hydrationKey,
      );
      assert.equal(emailLessGuestA?.storageKeys, null);
      assert.equal(emailLessGuestB?.storageKeys, null);

      const tokenOnlyInvite =
        accountConfigOrchestration.resolveLocalOnboardingIdentityScope({
          userType: null,
          teamInviteToken: "team-invite-secret",
        });
      assert.notEqual(tokenOnlyInvite, null);
      assert.equal(tokenOnlyInvite?.storageKeys, null);
      assert.equal(
        accountConfigOrchestration.hydrateLocalOnboardingScope(
          tokenOnlyInvite!,
          storage,
        ).onboardingStep,
        0,
      );

      const serializedLocalStorage = JSON.stringify(storage.snapshot());
      assert.equal(serializedLocalStorage.includes("secret-invite-token-a"), false);
      assert.equal(serializedLocalStorage.includes("secret-invite-token-b"), false);
      assert.equal(serializedLocalStorage.includes("team-invite-secret"), false);
      assertGlobalOnboardingStorageUntouched(storage, before);
      assert.equal(configFetches, 0);
    } finally {
      globalThis.fetch = previousFetch;
    }
  });

  await test("found incomplete resumes partial safe choices and server step", async () => {
    const storage = createLegacyStorage();
    const before = storage.snapshot();
    const outcome = await hydrateResult(
      {
        status: "found",
        config: {
          onboardingSession: {
            schemaVersion: 1,
            completed: false,
            currentStep: 2,
            choices: {
              primaryRole: "dj",
              selectedInboxes: ["main"],
            },
          },
          managedInboxes: [],
        },
      },
      storage,
    );

    assert.equal(outcome.status, "found");
    if (outcome.status !== "found") throw new Error("Expected found outcome");
    assert.equal(outcome.accountState.view, "onboarding");
    assert.equal(outcome.accountState.onboardingStep, 2);
    assert.equal(outcome.accountState.onboardingState.primaryRole, "dj");
    assert.equal(
      outcome.accountState.onboardingState.internalRole,
      cleanState.internalRole,
    );
    assert.deepEqual(
      outcome.accountState.onboardingState.inboxConnections,
      cleanState.inboxConnections,
    );
    assert.equal(outcome.expectedWorkspaceHydrationEcho, null);
    assertGlobalOnboardingStorageUntouched(storage, before);
    const hydratedSession = outcome.accountState.persistedOnboardingSession;
    assert.equal(hydratedSession?.currentStep, 2);
    assert.equal(hydratedSession?.completed, false);
    assert.equal(
      JSON.stringify(hydratedSession).includes("stale-local-secret"),
      false,
    );
  });

  await test("the same server session hydrates identically in a second clean profile", async () => {
    const serverSession = incompleteSession(2);
    const firstStorage = new MemoryStorage();
    const secondStorage = new MemoryStorage();
    const first = await hydrateResult(
      { status: "found", config: { onboardingSession: serverSession } },
      firstStorage,
    );
    const second = await hydrateResult(
      { status: "found", config: { onboardingSession: serverSession } },
      secondStorage,
      false,
      () => undefined,
      SECOND_ACCOUNT_KEY,
    );

    assert.equal(first.status, "found");
    assert.equal(second.status, "found");
    if (first.status !== "found" || second.status !== "found") {
      throw new Error("Expected found outcomes");
    }
    assert.equal(first.accountState.onboardingStep, 2);
    assert.equal(second.accountState.onboardingStep, 2);
    assert.deepEqual(
      accountConfigOrchestration.projectChoices(first.accountState.onboardingState),
      accountConfigOrchestration.projectChoices(second.accountState.onboardingState),
    );
    assert.deepEqual(
      first.accountState.onboardingState.inboxConnections,
      cleanState.inboxConnections,
    );
    assert.deepEqual(
      second.accountState.onboardingState.inboxConnections,
      cleanState.inboxConnections,
    );
  });

  await test("found empty object is not-started without touching guest globals", async () => {
    const storage = createLegacyStorage();
    const before = storage.snapshot();
    const outcome = await hydrateResult(
      { status: "found", config: { onboardingSession: {}, managedInboxes: [] } },
      storage,
    );

    assert.equal(outcome.status, "found");
    if (outcome.status !== "found") throw new Error("Expected found outcome");
    assert.equal(outcome.accountState.onboardingStep, 0);
    assert.equal(outcome.accountState.onboardingState.primaryRole, cleanState.primaryRole);
    assert.equal(outcome.accountState.persistedOnboardingSession, null);
    assertGlobalOnboardingStorageUntouched(storage, before);
  });

  await test("missing is clean, storage-silent and never auto-saves", async () => {
    const storage = createLegacyStorage();
    const before = storage.snapshot();
    let saveCalls = 0;
    const queue = accountConfigOrchestration.createSaveQueue({
      save: async (config) => {
        saveCalls += 1;
        return { status: "found", config };
      },
    });
    queue.reset(ACCOUNT_KEY);
    const outcome = await hydrateResult(
      { status: "missing", config: null },
      storage,
      true,
    );

    assert.equal(outcome.status, "missing");
    if (outcome.status !== "missing") throw new Error("Expected missing outcome");
    assert.equal(outcome.accountState.onboardingStep, 0);
    assert.equal(outcome.accountState.persistedOnboardingSession, null);
    assert.deepEqual(storage.reads, []);
    assert.deepEqual(storage.mutations, []);
    assert.deepEqual(storage.snapshot(), before);
    assert.equal(queue.isDirty(ACCOUNT_KEY), false);
    assert.equal(
      queue.enqueue({ accountKey: ACCOUNT_KEY, config: { onboardingSession: {} } }),
      false,
    );
    await flushAsyncWork();
    assert.equal(saveCalls, 0);
  });

  await test("malformed sessions fail closed with zero progress", async () => {
    const malformedSessions = [
      null,
      { completed: false, currentStep: 1, choices: {} },
      { schemaVersion: 1, completed: false, currentStep: 4, choices: {} },
      {
        schemaVersion: 1,
        completed: false,
        currentStep: 1,
        choices: { primaryRole: "dj", inboxConnections: { secret: true } },
      },
      {
        schemaVersion: 1,
        completed: false,
        currentStep: 1,
        choices: { focusPreferences: { demos: "urgent" } },
      },
      {
        schemaVersion: 1,
        completed: false,
        currentStep: 1,
        choices: { focusPreferences: { demos: "high" } },
      },
      {
        schemaVersion: 1,
        completed: false,
        currentStep: 1,
        choices: { internalRole: "root" },
      },
      {
        schemaVersion: 1,
        completed: false,
        currentStep: 1,
        choices: { selectedInboxes: ["main", "main"] },
      },
      {
        schemaVersion: 1,
        completed: false,
        currentStep: 1,
        choices: {
          customInboxes: [{ id: "custom:owner@example.com", name: "Smuggled" }],
        },
      },
      {
        schemaVersion: 1,
        completed: false,
        currentStep: 1,
        choices: {
          customInboxes: [
            { id: "custom:safe", name: "One" },
            { id: "custom:safe", name: "Two" },
          ],
        },
      },
    ];

    for (const onboardingSession of malformedSessions) {
      const storage = createLegacyStorage();
      const before = storage.snapshot();
      const outcome = await hydrateResult(
        { status: "found", config: { onboardingSession } },
        storage,
      );
      assert.deepEqual(outcome, { status: "error", errorStatus: "unavailable" });
      assert.deepEqual(storage.mutations, []);
      assert.deepEqual(storage.snapshot(), before);
    }

    const missingSessionOutcome = await hydrateResult(
      { status: "found", config: { managedInboxes: [] } },
      new MemoryStorage(),
    );
    assert.deepEqual(missingSessionOutcome, {
      status: "error",
      errorStatus: "unavailable",
    });
  });

  await test("every backend internalRole hydrates and serializes through one validator", () => {
    const validInternalRoles = [
      "management",
      "label_manager",
      "label_ar_manager",
      "ar_manager",
      "product_manager",
      "artist_manager",
      "dj",
      "producer",
    ] as const;

    for (const internalRole of validInternalRoles) {
      const parsed = accountConfigOrchestration.parseOnboardingSession({
        completed: true,
        state: {
          ...initialOnboardingState,
          internalRole,
        },
      });
      assert.equal(parsed.status, "valid", internalRole);
      if (parsed.status !== "valid") {
        throw new Error(`Expected ${internalRole} to be valid`);
      }
      const hydrated = accountConfigOrchestration.hydrateChoices(
        parsed.session.choices,
      );
      assert.equal(hydrated.internalRole, internalRole);
      assert.equal(
        accountConfigOrchestration.projectChoices(hydrated).internalRole,
        internalRole,
      );
    }

    assert.equal(
      accountConfigOrchestration.parseOnboardingSession({
        schemaVersion: 1,
        completed: false,
        currentStep: 1,
        choices: { internalRole: "root" },
      }).status,
      "invalid",
    );
  });

  await test("legacy completed is read-compatible, safe and reset-resistant", async () => {
    const storage = createLegacyStorage();
    const before = storage.snapshot();
    let clearedResetQueries = 0;
    const outcome = await hydrateResult(
      { status: "found", config: completedLegacyConfig() },
      storage,
      true,
      () => {
        clearedResetQueries += 1;
      },
    );

    assert.equal(outcome.status, "found");
    if (outcome.status !== "found") throw new Error("Expected found outcome");
    assert.equal(outcome.accountState.view, "workspace");
    assert.equal(outcome.accountState.onboardingStep, ONBOARDING_STEP_MAX);
    assert.equal(outcome.didResetOnboarding, false);
    assert.equal(clearedResetQueries, 1);
    assert.equal("onboardingSession" in outcome.expectedWorkspaceHydrationEcho!, false);
    assert.deepEqual(
      outcome.accountState.onboardingState.inboxConnections,
      cleanState.inboxConnections,
    );
    assertGlobalOnboardingStorageUntouched(storage, before);
    const hydratedSession = outcome.accountState.persistedOnboardingSession;
    assert.equal(hydratedSession?.schemaVersion, 1);
    assert.equal(hydratedSession?.completed, true);
    assert.equal("state" in hydratedSession!, false);
    assert.equal("inboxConnections" in hydratedSession!.choices, false);
    assert.equal(hydratedSession?.choices.focusPreferences.demos, "medium");
    assert.equal(JSON.stringify(hydratedSession).includes("imap-secret"), false);
  });

  await test("v1 completed opens workspace but is omitted from later client payloads", async () => {
    const storage = new MemoryStorage({ [MANAGED_INBOXES_KEY]: "[]" });
    const completedSession = completedV1Session();
    const outcome = await hydrateResult(
      { status: "found", config: { onboardingSession: completedSession } },
      storage,
    );
    assert.equal(outcome.status, "found");
    if (outcome.status !== "found") throw new Error("Expected found outcome");
    assert.equal(outcome.accountState.view, "workspace");

    const config = accountConfigOrchestration.buildAuthoritativeConfig(
      ACCOUNT_KEY,
      completedSession,
      {},
      storage,
    );
    assert.equal(Object.prototype.hasOwnProperty.call(config, "onboardingSession"), false);
  });

  await test("incomplete reset writes no completed record and waits for save", async () => {
    const storage = createLegacyStorage();
    const before = storage.snapshot();
    let clearedResetQueries = 0;
    const outcome = await hydrateResult(
      {
        status: "found",
        config: { onboardingSession: incompleteSession(2), managedInboxes: [] },
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
    assert.equal(outcome.accountState.onboardingStep, 0);
    assert.equal(outcome.accountState.persistedOnboardingSession, null);
    assertGlobalOnboardingStorageUntouched(storage, before);
    assert.equal(clearedResetQueries, 0);
  });

  await test("safe payload is exact, canonical and ignores local onboarding authority", () => {
    const storage = new MemoryStorage({
      [ONBOARDING_SESSION_KEY]: JSON.stringify(completedV1Session()),
      [ONBOARDING_DRAFT_KEY]: JSON.stringify(incompleteSession(3)),
      [MANAGED_INBOXES_KEY]: "[]",
    });
    const session = incompleteSession(1);
    const config = accountConfigOrchestration.buildAuthoritativeConfig(
      ACCOUNT_KEY,
      session,
      {},
      storage,
    );
    const payload = config.onboardingSession as OnboardingSessionV1;

    assert.deepEqual(Object.keys(payload), [
      "schemaVersion",
      "completed",
      "currentStep",
      "choices",
    ]);
    assert.equal(payload.schemaVersion, 1);
    assert.equal(payload.completed, false);
    assert.equal(payload.currentStep, 1);
    assert.deepEqual(Object.keys(payload.choices), [
      "primaryRole",
      "internalRole",
      "secondaryRole",
      "primaryInbox",
      "primaryInboxType",
      "focusPreferences",
      "inboxCount",
      "selectedInboxes",
      "customInboxes",
    ]);
    const serializedSession = JSON.stringify(payload);
    assert.equal(serializedSession.includes("inboxConnections"), false);
    assert.equal(serializedSession.includes("provider"), false);
    assert.equal(serializedSession.includes("password"), false);
    assert.equal(serializedSession.includes("oauthAuthorizationUrl"), false);
    assert.equal(serializedSession.includes("accessToken"), false);
    assert.equal(serializedSession.includes("email"), false);
    assert.equal(serializedSession.includes("private@example.com"), false);
    assert.equal(storage.reads.includes(ONBOARDING_SESSION_KEY), false);
    assert.equal(storage.reads.includes(ONBOARDING_DRAFT_KEY), false);
  });

  await test("a clean member save never writes the global guest mirror", async () => {
    const storage = createLegacyStorage();
    const before = storage.snapshot();
    const pendingSave = deferred<UserAccountConfigSaveResult>();
    let cleanCalls = 0;
    const queue = accountConfigOrchestration.createSaveQueue({
      save: async () => pendingSave.promise,
      onClean: () => {
        cleanCalls += 1;
      },
    });
    const config = { onboardingSession: incompleteSession(2) };

    queue.reset(ACCOUNT_KEY);
    queue.markDirty(ACCOUNT_KEY);
    queue.enqueue({ accountKey: ACCOUNT_KEY, config });
    assertGlobalOnboardingStorageUntouched(storage, before);
    pendingSave.resolve({ status: "found", config });
    await flushAsyncWork();
    assert.equal(cleanCalls, 1);
    assertGlobalOnboardingStorageUntouched(storage, before);
  });

  await test("the production safe-choice handler produces one incomplete POST payload", async () => {
    const savedConfigs: UserAccountConfig[] = [];
    const storage = new MemoryStorage({ [MANAGED_INBOXES_KEY]: "[]" });
    let currentState = cleanState;
    let currentStep = 0;
    let persistedSession: OnboardingSessionV1 | null = null;
    const queue = accountConfigOrchestration.createSaveQueue({
      save: async (nextConfig) => {
        savedConfigs.push(nextConfig);
        return { status: "found", config: nextConfig };
      },
    });
    queue.reset(ACCOUNT_KEY);
    const handlers = accountConfigOrchestration.createOnboardingHandlers({
      getOnboardingState: () => currentState,
      getOnboardingStep: () => currentStep,
      commitAccountConfigMutation: (mutation) => {
        queue.markDirty(ACCOUNT_KEY);
        mutation();
        if (!persistedSession) throw new Error("Expected a safe session");
        queue.enqueue({
          accountKey: ACCOUNT_KEY,
          config: accountConfigOrchestration.buildAuthoritativeConfig(
            ACCOUNT_KEY,
            persistedSession,
            {},
            storage,
          ),
        });
      },
      setOnboardingState: (nextState) => {
        currentState = nextState;
      },
      setOnboardingStep: (nextStep) => {
        currentStep = nextStep;
      },
      setPersistedOnboardingSession: (session) => {
        persistedSession = session;
      },
      canOpenWorkspace: false,
      openWorkspace: () => undefined,
    });

    handlers.onSafeStateChange({ ...cleanState, primaryRole: "dj" });
    await flushAsyncWork();
    assert.equal(savedConfigs.length, 1);
    const posted = savedConfigs[0].onboardingSession as OnboardingSessionV1;
    assert.equal(posted.completed, false);
    assert.equal(posted.currentStep, 0);
    assert.equal(posted.choices.primaryRole, "dj");
    assert.equal("inboxConnections" in posted.choices, false);
  });

  await test("production step handlers persist forward and back safe steps", async () => {
    const savedSteps: number[] = [];
    const storage = new MemoryStorage({ [MANAGED_INBOXES_KEY]: "[]" });
    let currentState = selectedState;
    let currentStep = 0;
    let persistedSession: OnboardingSessionV1 | null = null;
    const queue = accountConfigOrchestration.createSaveQueue({
      save: async (config) => {
        savedSteps.push(
          (config.onboardingSession as OnboardingSessionV1).currentStep,
        );
        return { status: "found", config };
      },
    });
    queue.reset(ACCOUNT_KEY);
    const handlers = accountConfigOrchestration.createOnboardingHandlers({
      getOnboardingState: () => currentState,
      getOnboardingStep: () => currentStep,
      commitAccountConfigMutation: (mutation) => {
        queue.markDirty(ACCOUNT_KEY);
        mutation();
        if (!persistedSession) throw new Error("Expected a safe session");
        queue.enqueue({
          accountKey: ACCOUNT_KEY,
          config: accountConfigOrchestration.buildAuthoritativeConfig(
            ACCOUNT_KEY,
            persistedSession,
            {},
            storage,
          ),
        });
      },
      setOnboardingState: (nextState) => {
        currentState = nextState;
      },
      setOnboardingStep: (nextStep) => {
        currentStep = nextStep;
      },
      setPersistedOnboardingSession: (session) => {
        persistedSession = session;
      },
      canOpenWorkspace: false,
      openWorkspace: () => undefined,
    });

    const nextStep = onboardingFlowProgression.next(0);
    handlers.onStepChange(nextStep);
    await flushAsyncWork();

    const backStep = onboardingFlowProgression.back(nextStep);
    handlers.onStepChange(backStep);
    await flushAsyncWork();

    assert.deepEqual(savedSteps, [1, 0]);
    assert.equal(queue.isDirty(ACCOUNT_KEY), false);
  });

  await test("production handlers persist to a durable server and a clean browser GET resumes", async () => {
    const previousFetch = globalThis.fetch;
    let serverRecord: UserAccountConfig | null = null;
    let getCalls = 0;
    let postCalls = 0;
    let failNextGet = false;
    const cloneConfig = (config: UserAccountConfig): UserAccountConfig =>
      JSON.parse(JSON.stringify(config)) as UserAccountConfig;

    globalThis.fetch = (async (_input, init) => {
      const method = init?.method ?? "GET";
      if (method === "GET") {
        getCalls += 1;
        if (failNextGet) {
          failNextGet = false;
          throw new Error("offline");
        }
        return new Response(
          JSON.stringify(
            serverRecord
              ? {
                  ok: true,
                  configState: "found",
                  config: cloneConfig(serverRecord),
                }
              : { ok: true, configState: "missing", config: null },
          ),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      assert.equal(method, "POST");
      assert.equal(typeof init?.body, "string");
      const envelope = JSON.parse(String(init?.body)) as {
        config: UserAccountConfig;
      };
      postCalls += 1;
      serverRecord = cloneConfig(envelope.config);
      return new Response(
        JSON.stringify({ ok: true, config: cloneConfig(serverRecord) }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as typeof fetch;

    try {
      setUserAccountConfigHydrationEchoExpectation(ACCOUNT_KEY, null);
      const browserAStorage = new MemoryStorage({ [MANAGED_INBOXES_KEY]: "[]" });
      const browserABefore = browserAStorage.snapshot();
      const browserAHydration = await accountConfigOrchestration
        .createHydrator()
        .hydrate({
          accountStorageOwnerKey: ACCOUNT_KEY,
          storage: browserAStorage,
          resetOnboarding: false,
          clearResetQuery: () => undefined,
        });
      assert.equal(browserAHydration.status, "missing");
      assert.equal(getCalls, 1);
      assert.equal(postCalls, 0);

      const queue = accountConfigOrchestration.createSaveQueue();
      queue.reset(ACCOUNT_KEY);
      let currentState = cleanState;
      let currentStep = 0;
      let persistedSession: OnboardingSessionV1 | null = null;
      let enqueueScheduled = false;
      const handlers = accountConfigOrchestration.createOnboardingHandlers({
        getOnboardingState: () => currentState,
        getOnboardingStep: () => currentStep,
        commitAccountConfigMutation: (mutation) => {
          queue.markDirty(ACCOUNT_KEY);
          mutation();
          if (enqueueScheduled) return;
          enqueueScheduled = true;
          queueMicrotask(() => {
            enqueueScheduled = false;
            if (!persistedSession) return;
            queue.enqueue({
              accountKey: ACCOUNT_KEY,
              config: accountConfigOrchestration.buildAuthoritativeConfig(
                ACCOUNT_KEY,
                persistedSession,
                {},
                browserAStorage,
              ),
            });
          });
        },
        setOnboardingState: (nextState) => {
          currentState = nextState;
        },
        setOnboardingStep: (nextStep) => {
          currentStep = nextStep;
        },
        setPersistedOnboardingSession: (session) => {
          persistedSession = session;
        },
        canOpenWorkspace: false,
        openWorkspace: () => undefined,
      });

      handlers.onSafeStateChange(selectedState);
      handlers.onStepChange(onboardingFlowProgression.next(0));
      for (let attempt = 0; attempt < 30 && queue.isDirty(ACCOUNT_KEY); attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 0));
      }

      assert.equal(queue.isDirty(ACCOUNT_KEY), false);
      assert.equal(postCalls, 1);
      assert.notEqual(serverRecord, null);
      const serverSession = serverRecord!.onboardingSession as OnboardingSessionV1;
      assert.equal(serverSession.completed, false);
      assert.equal(serverSession.currentStep, 1);
      assert.equal(serverSession.choices.primaryRole, "dj");
      assert.equal("inboxConnections" in serverSession.choices, false);
      const serializedServerRecord = JSON.stringify(serverRecord);
      for (const forbiddenValue of [
        "password",
        "accessToken",
        "oauthAuthorizationUrl",
        "provider",
        "connected",
        "connectionMethod",
        "connectionStatus",
        "customImap",
        "customSmtp",
        '"email"',
        "private@example.com",
      ]) {
        assert.equal(serializedServerRecord.includes(forbiddenValue), false);
      }
      assertGlobalOnboardingStorageUntouched(browserAStorage, browserABefore);

      setUserAccountConfigHydrationEchoExpectation(ACCOUNT_KEY, null);
      const browserBStorage = new MemoryStorage();
      const browserBBefore = browserBStorage.snapshot();
      const browserBHydration = await accountConfigOrchestration
        .createHydrator()
        .hydrate({
          accountStorageOwnerKey: ACCOUNT_KEY,
          storage: browserBStorage,
          resetOnboarding: false,
          clearResetQuery: () => undefined,
        });
      assert.equal(browserBHydration.status, "found");
      if (browserBHydration.status !== "found") {
        throw new Error("Expected Browser B to read the durable server record");
      }
      assert.equal(browserBHydration.accountState.onboardingStep, 1);
      assert.equal(browserBHydration.accountState.onboardingState.primaryRole, "dj");
      assert.deepEqual(
        accountConfigOrchestration.projectChoices(
          browserBHydration.accountState.onboardingState,
        ),
        serverSession.choices,
      );
      assert.deepEqual(
        browserBHydration.accountState.onboardingState.inboxConnections,
        cleanState.inboxConnections,
      );
      assert.equal(getCalls, 2);
      assert.equal(postCalls, 1);
      assertGlobalOnboardingStorageUntouched(browserBStorage, browserBBefore);

      failNextGet = true;
      const retryableOutcome = await accountConfigOrchestration
        .createHydrator()
        .hydrate({
          accountStorageOwnerKey: ACCOUNT_KEY,
          storage: new MemoryStorage(),
          resetOnboarding: false,
          clearResetQuery: () => undefined,
        });
      assert.deepEqual(retryableOutcome, {
        status: "error",
        errorStatus: "network_error",
      });
      assert.equal(getCalls, 3);
      assert.equal(postCalls, 1);
    } finally {
      setUserAccountConfigHydrationEchoExpectation(null, null);
      globalThis.fetch = previousFetch;
    }
  });

  await test("save queue coalesces latest step and retries after failure", async () => {
    const pendingSaves: Array<ReturnType<typeof deferred<UserAccountConfigSaveResult>>> = [];
    const savedConfigs: UserAccountConfig[] = [];
    const storage = new MemoryStorage({ [MANAGED_INBOXES_KEY]: "[]" });
    let activeSaves = 0;
    let maximumActiveSaves = 0;
    let currentState = selectedState;
    let currentStep = 0;
    let persistedSession: OnboardingSessionV1 | null = null;
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
    const handlers = accountConfigOrchestration.createOnboardingHandlers({
      getOnboardingState: () => currentState,
      getOnboardingStep: () => currentStep,
      commitAccountConfigMutation: (mutation) => {
        queue.markDirty(ACCOUNT_KEY);
        mutation();
        if (!persistedSession) throw new Error("Expected a safe session");
        queue.enqueue({
          accountKey: ACCOUNT_KEY,
          config: accountConfigOrchestration.buildAuthoritativeConfig(
            ACCOUNT_KEY,
            persistedSession,
            {},
            storage,
          ),
        });
      },
      setOnboardingState: (nextState) => {
        currentState = nextState;
      },
      setOnboardingStep: (nextStep) => {
        currentStep = nextStep;
      },
      setPersistedOnboardingSession: (session) => {
        persistedSession = session;
      },
      canOpenWorkspace: false,
      openWorkspace: () => undefined,
    });

    handlers.onStepChange(0);
    handlers.onStepChange(1);
    handlers.onStepChange(3);
    assert.equal(savedConfigs.length, 1);

    pendingSaves[0].resolve({ status: "found", config: savedConfigs[0] });
    await flushAsyncWork();
    assert.equal(savedConfigs.length, 2);
    assert.equal(
      (savedConfigs[1].onboardingSession as OnboardingSessionV1).currentStep,
      3,
    );
    pendingSaves[1].resolve({
      status: "network_error",
      error: { code: "network_error", message: "Offline" },
    });
    await flushAsyncWork();
    assert.equal(queue.isDirty(ACCOUNT_KEY), true);

    handlers.onStepChange(3);
    pendingSaves[2].resolve({ status: "found", config: savedConfigs[2] });
    await flushAsyncWork();
    assert.equal(queue.isDirty(ACCOUNT_KEY), false);
    assert.equal(
      (savedConfigs[2].onboardingSession as OnboardingSessionV1).currentStep,
      3,
    );
    assert.equal(maximumActiveSaves, 1);
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
      { status: "found", config: { onboardingSession: incompleteSession(2) } },
      { status: "found", config: { onboardingSession: {} } },
      { status: "missing", config: null },
      { status: "network_error", error: { code: "network_error", message: "x" } },
    ];

    for (const result of results) {
      queue.reset(ACCOUNT_KEY);
      await hydrateResult(result, new MemoryStorage());
      assert.equal(queue.isDirty(ACCOUNT_KEY), false);
      assert.equal(
        queue.enqueue({ accountKey: ACCOUNT_KEY, config: { onboardingSession: {} } }),
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
    const options = {
      accountStorageOwnerKey: ACCOUNT_KEY,
      storage,
      resetOnboarding: false,
      clearResetQuery: () => undefined,
    };

    const staleHydration = hydrator.hydrate(options);
    const retryHydration = hydrator.hydrate(options);
    loads[1].resolve({
      status: "found",
      config: {
        onboardingSession: incompleteSession(2),
        mailboxTitleOverrides: { winner: "retry" },
      },
    });
    const retryOutcome = await retryHydration;
    assert.equal(retryOutcome.status, "found");
    assert.deepEqual(JSON.parse(storage.getItem(MAILBOX_TITLES_KEY)!), {
      winner: "retry",
    });

    loads[0].resolve({
      status: "found",
      config: {
        onboardingSession: incompleteSession(1),
        mailboxTitleOverrides: { stale: "first" },
      },
    });
    const staleOutcome = await staleHydration;
    assert.equal(staleOutcome.status, "cancelled");
    assert.deepEqual(JSON.parse(storage.getItem(MAILBOX_TITLES_KEY)!), {
      winner: "retry",
    });
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
      const outcome = await hydrateResult(result, storage);
      assert.equal(outcome.status, result.status === "unauthorized" ? "unauthorized" : "error");
      assert.deepEqual(storage.mutations, []);
      assert.deepEqual(storage.snapshot(), before);
    }
  });

  await test("client progress helpers can never create completed true", () => {
    const created = accountConfigOrchestration.createIncompleteSession(selectedState, 99);
    assert.equal(created.completed, false);
    assert.equal(created.currentStep, ONBOARDING_STEP_MAX);
    const config = accountConfigOrchestration.buildAuthoritativeConfig(
      ACCOUNT_KEY,
      created,
      {},
      new MemoryStorage(),
    );
    assert.equal((config.onboardingSession as OnboardingSessionV1).completed, false);
  });

  await test("production completion handler blocks members and opens for guests", () => {
    const storage = new MemoryStorage();
    const queue = accountConfigOrchestration.createSaveQueue();
    queue.reset(ACCOUNT_KEY);
    let openCalls = 0;
    let currentState = cleanState;
    let currentStep = 0;
    let persistedSession: OnboardingSessionV1 | null = null;
    const createHandlers = (canOpenWorkspace: boolean) =>
      accountConfigOrchestration.createOnboardingHandlers({
        getOnboardingState: () => currentState,
        getOnboardingStep: () => currentStep,
        commitAccountConfigMutation: (mutation) => {
          queue.markDirty(ACCOUNT_KEY);
          mutation();
        },
        setOnboardingState: (nextState) => {
          currentState = nextState;
        },
        setOnboardingStep: (nextStep) => {
          currentStep = nextStep;
        },
        setPersistedOnboardingSession: (session) => {
          persistedSession = session;
        },
        canOpenWorkspace,
        openWorkspace: () => {
          openCalls += 1;
        },
      });
    const memberHandlers = createHandlers(
      accountConfigOrchestration.canOpenWorkspaceWithoutServerCompletion("member"),
    );
    const guestHandlers = createHandlers(
      accountConfigOrchestration.canOpenWorkspaceWithoutServerCompletion("guest"),
    );
    const workspaceConfig = {} as Parameters<
      typeof memberHandlers.onOpenWorkspace
    >[0];

    memberHandlers.onOpenWorkspace(workspaceConfig);
    assert.equal(openCalls, 0);
    guestHandlers.onOpenWorkspace(workspaceConfig);
    assert.equal(openCalls, 1);
    assert.equal(persistedSession, null);
    assert.equal(queue.isDirty(ACCOUNT_KEY), false);
    assert.deepEqual(storage.reads, []);
    assert.deepEqual(storage.mutations, []);
  });

  console.log(`${passedTests} App startup orchestration tests passed`);
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
