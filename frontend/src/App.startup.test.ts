import assert from "node:assert/strict";
import "sucrase/register/tsx.js";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { initialOnboardingState } from "./data/onboardingOptions";
import {
  createCustomImapOnboardingAttemptCoordinator,
  createCustomImapOnboardingAttemptSnapshot,
  createCustomImapOnboardingFingerprint,
  createCustomImapSelectedPositionIdentity,
  type CustomImapOnboardingAttemptGuard,
  type CustomImapOnboardingAttemptSnapshot,
  type CustomImapOnboardingReconciliationResult,
} from "./lib/customImapOnboardingAttempt";
import {
  ONBOARDING_STEP_MAX,
  type InboxConnection,
  type OnboardingSessionV1,
  type OnboardingState,
} from "./types/onboarding";
import type { InboxConnectionAttemptResult } from "./lib/inboxConnectionApi";
import {
  loadUserAccountConfig,
  setUserAccountConfigHydrationEchoExpectation,
  type UserAccountConfig,
  type UserAccountConfigReadResult,
  type UserAccountConfigSaveResult,
} from "./lib/userConfigApi";

const { accountConfigOrchestration } = require("./App.tsx") as typeof import("./App");
const {
  CustomImapServerReloadRecovery,
  OnboardingFlow,
  applyAuthoritativeCustomImapConnection,
  areSelectedOnboardingInboxesFullyConnected,
  buildOnboardingInboxConnectionUpdate,
  invokeCustomImapServerReload,
  onboardingFlowProgression,
  shouldBlockOnboardingMutation,
} = require("./components/onboarding/OnboardingFlow.tsx") as typeof import(
  "./components/onboarding/OnboardingFlow"
);
const inboxConnectionApi = require(
  "./lib/inboxConnectionApi.ts"
) as typeof import("./lib/inboxConnectionApi");
const {
  StepConnectInboxes,
  buildCustomImapOnboardingConnectionOptions,
  buildOnboardingInboxConnectionOptions,
  buildSuccessfulOnboardingConnectionUpdate,
  getConnectionFeedback,
  isConnectionReady,
} = require(
  "./components/onboarding/StepConnectInboxes.tsx"
) as typeof import("./components/onboarding/StepConnectInboxes");
const { NavigationBar, invokeNavigationAction } = require(
  "./components/onboarding/NavigationBar.tsx"
) as typeof import("./components/onboarding/NavigationBar");

const ACCOUNT_KEY = "member@example.com";
const SECOND_ACCOUNT_KEY = "second@example.com";
const ONBOARDING_SESSION_KEY = "label-inbox-ai-onboarding-state";
const ONBOARDING_DRAFT_KEY = "label-inbox-ai-onboarding-draft-state";
const APP_VIEW_KEY = "cuevion-app-view";
const MANAGED_INBOXES_KEY = "cuevion-managed-inboxes";
const MAILBOX_TITLES_KEY = "cuevion-mailbox-title-overrides";
const OAUTH_CALLBACK_RESULT_KEY = "cuevion-oauth-callback-result";
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
const customImapSelectedState: OnboardingState = {
  ...selectedChoiceState,
  inboxCount: "2",
  selectedInboxes: ["main", "demo"],
  inboxConnections: {
    ...selectedChoiceState.inboxConnections,
    demo: {
      ...selectedChoiceState.inboxConnections.demo,
      provider: "custom_imap",
      email: "verified.imap@example.com",
      connected: false,
      connectionMethod: "imap",
      connectionStatus: "not_connected",
      imapConnectionStatus: "not_connected",
      smtpConnectionStatus: "not_configured",
      fullyConnected: false,
      connectionMessage: null,
      oauthAuthorizationUrl: null,
      customImap: {
        host: "imap.example.com",
        port: "993",
        ssl: true,
        username: "verified.imap@example.com",
        password: "must-remain-ephemeral",
      },
      customSmtp: {
        host: "smtp.example.com",
        port: "587",
        security: "starttls",
        username: "smtp-user@example.com",
        password: "must-remain-ephemeral-smtp",
        useSameCredentials: false,
      },
    },
  },
};
const customImapAttemptSnapshot =
  createCustomImapOnboardingAttemptSnapshot({
    onboardingInboxId: "demo",
    selectedInboxes: customImapSelectedState.selectedInboxes,
    connection: customImapSelectedState.inboxConnections.demo,
    passwordRevision: 1,
  });

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

type ManagedProjectionInput = Parameters<
  typeof accountConfigOrchestration.projectConnectedManagedInboxes
>[1][number];

function connectedGoogleManagedInbox(
  overrides: Partial<ManagedProjectionInput> = {},
): ManagedProjectionInput {
  return {
    id: "managed-google-1",
    onboardingInboxId: "main",
    title: "Verified Gmail",
    email: "verified.account@gmail.com",
    provider: "google",
    connected: true,
    connectionMethod: "oauth",
    connectionStatus: "connected",
    connectionMessage: "Connected through Google.",
    oauthAuthorizationUrl: null,
    ...overrides,
  };
}

function connectedCustomImapManagedInbox(
  overrides: Partial<ManagedProjectionInput> = {},
): ManagedProjectionInput {
  const hasFullSmtpCapability =
    overrides.smtpConnectionStatus === "connected" &&
    overrides.fullyConnected === true;
  return {
    id: "imap-server-1",
    onboardingInboxId: "demo",
    title: "Verified Custom IMAP",
    email: "verified.imap@example.com",
    provider: "custom_imap",
    connected: true,
    connectionMethod: "imap",
    connectionStatus: "connected",
    imapConnectionStatus: "connected",
    smtpConnectionStatus: "not_configured",
    imapPasswordSet: true,
    smtpPasswordSet: hasFullSmtpCapability,
    fullyConnected: false,
    connectionMessage: null,
    oauthAuthorizationUrl: null,
    customImap: {
      host: "imap.example.com",
      port: "993",
      ssl: true,
      username: "verified.imap@example.com",
    },
    customSmtp: { password: "" },
    ...overrides,
  };
}

const PRODUCTION_CUSTOM_INBOX_ID = "custom:inbox-2";
const PRODUCTION_SERVER_MAILBOX_ID = "imap-server-generated";

function createProductionIncomingOnlyState(): OnboardingState {
  return {
    ...cleanState,
    primaryInbox: PRODUCTION_CUSTOM_INBOX_ID,
    inboxCount: "2",
    selectedInboxes: ["main", PRODUCTION_CUSTOM_INBOX_ID],
    customInboxes: [
      {
        id: PRODUCTION_CUSTOM_INBOX_ID,
        name: "Promo inbox",
      },
    ],
    inboxConnections: {
      ...cleanState.inboxConnections,
      [PRODUCTION_CUSTOM_INBOX_ID]: {
        ...cleanState.inboxConnections.demo,
        provider: "custom_imap",
        email: "promo@example.com",
        connected: false,
        connectionMethod: "imap",
        connectionStatus: "not_connected",
        imapConnectionStatus: "not_connected",
        smtpConnectionStatus: "not_configured",
        fullyConnected: false,
        customImap: {
          host: "mail.example.com",
          port: "993",
          ssl: true,
          username: "promo@example.com",
          password: "",
        },
        customSmtp: {
          host: "smtp.example.com",
          port: "587",
          security: "starttls",
          username: "",
          password: "",
          useSameCredentials: true,
        },
      },
    },
  };
}

function productionIncomingOnlyManagedInbox(
  overrides: Partial<ManagedProjectionInput> = {},
): ManagedProjectionInput {
  return {
    id: PRODUCTION_SERVER_MAILBOX_ID,
    onboardingInboxId: PRODUCTION_CUSTOM_INBOX_ID,
    email: "promo@example.com",
    provider: "custom_imap",
    connected: true,
    connectionMethod: "imap",
    connectionStatus: "connected",
    customImap: {
      host: "mail.example.com",
      port: "993",
      ssl: true,
      username: "promo@example.com",
      password: "",
    },
    customSmtp: {
      password: "",
    },
    imapConnectionStatus: "connected",
    smtpConnectionStatus: "not_configured",
    imapPasswordSet: true,
    smtpPasswordSet: false,
    fullyConnected: false,
    ...overrides,
  };
}

function productionGmailWithServerMetadata(): ManagedProjectionInput {
  return connectedGoogleManagedInbox({
    id: "managed-google-production",
    onboardingInboxId: "main",
    email: "owner@gmail.com",
    connectionType: "gmail",
    oauthOwnerEmail: "owner@gmail.com",
  } as any);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

function createFakeTimers() {
  let nextId = 1;
  const pending = new Map<number, () => void>();
  return {
    setTimer(handler: () => void) {
      const id = nextId;
      nextId += 1;
      pending.set(id, handler);
      return id as ReturnType<typeof setTimeout>;
    },
    clearTimer(id: ReturnType<typeof setTimeout>) {
      pending.delete(id as unknown as number);
    },
    fireNext() {
      const entry = pending.entries().next().value as
        | [number, () => void]
        | undefined;
      if (!entry) {
        throw new Error("Expected a pending timer");
      }
      pending.delete(entry[0]);
      entry[1]();
    },
    pendingCount() {
      return pending.size;
    },
  };
}

async function flushAsyncWork() {
  for (let turn = 0; turn < 12; turn += 1) {
    await Promise.resolve();
  }
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

const successfulCustomImapPost: InboxConnectionAttemptResult = {
  ok: true,
  connected: false,
  connectionMethod: "imap",
  connectionStatus: "not_connected",
  connectionMessage: null,
};

const failedCustomImapPost: InboxConnectionAttemptResult = {
  ok: false,
  connected: false,
  connectionMethod: "imap",
  connectionStatus: "connection_failed",
  connectionMessage: "Could not connect to inbox.",
  error: {
    code: "connection_failed",
    message: "Could not connect to inbox.",
  },
};

function createCustomImapAttemptHarness() {
  const timers = createFakeTimers();
  let mounted = true;
  let selectedInboxes = [...customImapSelectedState.selectedInboxes];
  let connection: InboxConnection = {
    ...customImapSelectedState.inboxConnections.demo,
    customImap: {
      ...customImapSelectedState.inboxConnections.demo.customImap,
      password: "",
    },
    customSmtp: {
      ...customImapSelectedState.inboxConnections.demo.customSmtp,
      password: "",
    },
  };
  let password = "ephemeral-test-value";
  let passwordRevision = 1;
  let smtpPassword = "ephemeral-smtp-test-value";
  let smtpPasswordRevision = 1;
  let guard: CustomImapOnboardingAttemptGuard | null = null;
  let passwordClearCalls = 0;
  let smtpPasswordClearCalls = 0;
  let matchedCalls = 0;
  const matchedServerMailboxIds: string[] = [];
  let absentCalls = 0;
  let requiredCalls = 0;
  const postCalls: Array<{
    signal: AbortSignal;
    passwordWasPresent: boolean;
    smtpPasswordWasPresent: boolean;
    pending: ReturnType<
      typeof deferred<InboxConnectionAttemptResult>
    >;
  }> = [];
  const readbackCalls: Array<{
    signal: AbortSignal;
    pending: ReturnType<
      typeof deferred<CustomImapOnboardingReconciliationResult>
    >;
  }> = [];
  const guards: Array<CustomImapOnboardingAttemptGuard | null> = [];
  const snapshot = createCustomImapOnboardingAttemptSnapshot({
    onboardingInboxId: "demo",
    selectedInboxes,
    connection,
    passwordRevision,
    smtpPasswordRevision,
  });

  const rawCoordinator =
    createCustomImapOnboardingAttemptCoordinator({
      getCurrentContext: (attempt) => ({
        mounted,
        onboardingInboxId: selectedInboxes.includes(
          attempt.onboardingInboxId,
        )
          ? attempt.onboardingInboxId
          : null,
        provider: connection.provider,
        selectedPositionIdentity:
          createCustomImapSelectedPositionIdentity(
            selectedInboxes,
            attempt.onboardingInboxId,
          ),
        fingerprint: createCustomImapOnboardingFingerprint({
          onboardingInboxId: attempt.onboardingInboxId,
          selectedInboxes,
          connection,
        }),
        passwordRevision,
        smtpPasswordRevision,
      }),
      post: async (
        _attempt,
        postedPassword,
        signal,
        postedSmtpPassword,
      ) => {
        const pending =
          deferred<InboxConnectionAttemptResult>();
        postCalls.push({
          signal,
          passwordWasPresent: postedPassword.length > 0,
          smtpPasswordWasPresent: postedSmtpPassword.length > 0,
          pending,
        });
        return pending.promise;
      },
      reconcile: async (_attempt, signal) => {
        const pending =
          deferred<CustomImapOnboardingReconciliationResult>();
        readbackCalls.push({ signal, pending });
        return pending.promise;
      },
      consumePassword: (_attempt, expectedRevision) => {
        if (passwordRevision !== expectedRevision) {
          return null;
        }
        password = "";
        passwordRevision += 1;
        passwordClearCalls += 1;
        return passwordRevision;
      },
      consumeSmtpPassword: (_attempt, expectedRevision) => {
        if (smtpPasswordRevision !== expectedRevision) {
          return null;
        }
        smtpPassword = "";
        smtpPasswordRevision += 1;
        smtpPasswordClearCalls += 1;
        return smtpPasswordRevision;
      },
      applyMatched: (_attempt, result) => {
        matchedCalls += 1;
        matchedServerMailboxIds.push(result.serverMailboxId);
        connection = result.connection;
      },
      applyAbsent: () => {
        absentCalls += 1;
      },
      applyReconciliationRequired: () => {
        requiredCalls += 1;
      },
      onGuardChange: (nextGuard) => {
        guard = nextGuard;
        guards.push(nextGuard);
      },
      postTimeoutMs: 100,
      readbackTimeoutMs: 50,
      setTimer: timers.setTimer,
      clearTimer: timers.clearTimer,
    });
  const coordinator: typeof rawCoordinator = {
    ...rawCoordinator,
    start: (attempt, postedPassword, postedSmtpPassword = smtpPassword) =>
      rawCoordinator.start(
        attempt,
        postedPassword,
        postedSmtpPassword,
      ),
  };

  return {
    coordinator,
    snapshot,
    timers,
    postCalls,
    readbackCalls,
    guards,
    matchedServerMailboxIds,
    get guard() {
      return guard;
    },
    get password() {
      return password;
    },
    get passwordRevision() {
      return passwordRevision;
    },
    get smtpPassword() {
      return smtpPassword;
    },
    get smtpPasswordRevision() {
      return smtpPasswordRevision;
    },
    get passwordClearCalls() {
      return passwordClearCalls;
    },
    get smtpPasswordClearCalls() {
      return smtpPasswordClearCalls;
    },
    get matchedCalls() {
      return matchedCalls;
    },
    get absentCalls() {
      return absentCalls;
    },
    get requiredCalls() {
      return requiredCalls;
    },
    get connection() {
      return connection;
    },
    setMounted(value: boolean) {
      mounted = value;
    },
    replacePassword(value: string) {
      password = value;
      passwordRevision += 1;
    },
    replaceSmtpPassword(value: string) {
      smtpPassword = value;
      smtpPasswordRevision += 1;
    },
    replaceEmail(value: string) {
      connection = { ...connection, email: value };
    },
    replaceSelectedInboxes(value: OnboardingState["selectedInboxes"]) {
      selectedInboxes = [...value];
    },
  };
}

function matchedReadback(
  connection: InboxConnection,
): CustomImapOnboardingReconciliationResult {
  return {
    status: "matched",
    connection: {
      ...connection,
      connected: true,
      connectionMethod: "imap",
      connectionStatus: "connected",
      imapConnectionStatus: "connected",
      smtpConnectionStatus: "connected",
      fullyConnected: true,
      customImap: {
        ...connection.customImap,
        password: "",
      },
    },
    serverMailboxId: "imap-server-1",
  };
}

function assertMarkupControlDisabled(markup: string, control: string) {
  const escapedControl = control.replace(
    /[.*+?^${}()|[\]\\]/g,
    "\\$&",
  );
  const match = markup.match(
    new RegExp(
      `<(?:button|input)[^>]*data-attempt-control="${escapedControl}"[^>]*>`,
    ),
  );
  assert.notEqual(match, null, `Missing ${control} control`);
  assert.match(match![0], /\sdisabled(?:=""|\s|>)/);
}

function assertMarkupControlEnabled(markup: string, control: string) {
  const escapedControl = control.replace(
    /[.*+?^${}()|[\]\\]/g,
    "\\$&",
  );
  const match = markup.match(
    new RegExp(
      `<(?:button|input)[^>]*data-attempt-control="${escapedControl}"[^>]*>`,
    ),
  );
  assert.notEqual(match, null, `Missing ${control} control`);
  assert.doesNotMatch(
    match![0],
    /\sdisabled(?:=""|\s|>)/,
  );
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

  await test("custom IMAP onboarding keeps credentials ephemeral and requires readback", async () => {
    const connection = customImapSelectedState.inboxConnections.demo;
    const untrustedLocalSuccess = buildOnboardingInboxConnectionUpdate(
      {
        ...connection,
        serverMailboxId: "must-be-cleared",
      },
      {
        connected: true,
        connectionMethod: "imap",
        connectionStatus: "connected",
      },
    );
    assert.equal(untrustedLocalSuccess.connected, false);
    assert.equal(untrustedLocalSuccess.serverMailboxId, null);
    assert.equal(untrustedLocalSuccess.connectionStatus, "not_connected");
    assert.equal(untrustedLocalSuccess.customImap.password, "");
    assert.equal(untrustedLocalSuccess.customSmtp.password, "");

    const unchangedGoogleSuccess = buildOnboardingInboxConnectionUpdate(
      {
        ...selectedState.inboxConnections.main,
        serverMailboxId: "managed-google-1",
        customImap: {
          ...selectedState.inboxConnections.main.customImap,
          password: "",
        },
        customSmtp: {
          ...selectedState.inboxConnections.main.customSmtp,
          password: "",
        },
      },
      {
        connected: true,
        connectionMethod: "oauth",
        connectionStatus: "connected",
      },
    );
    assert.equal(unchangedGoogleSuccess.connected, true);
    assert.equal(
      unchangedGoogleSuccess.serverMailboxId,
      "managed-google-1",
    );
    assert.equal(unchangedGoogleSuccess.connectionStatus, "connected");

    const options = buildCustomImapOnboardingConnectionOptions({
      inboxId: "demo",
      connection,
      imapPassword: "one-use-password",
      smtpPassword: "one-use-smtp-password",
    });
    assert.deepEqual(options, {
      onboardingInboxId: "demo",
      serverMailboxId: null,
      email: "verified.imap@example.com",
      customImap: {
        host: "imap.example.com",
        port: "993",
        ssl: true,
        username: "verified.imap@example.com",
        password: "",
      },
      customSmtp: {
        host: "smtp.example.com",
        port: "587",
        security: "starttls",
        username: "smtp-user@example.com",
        password: "",
        useSameCredentials: false,
      },
      imapPassword: "one-use-password",
      smtpPassword: "one-use-smtp-password",
    });

    const safeSession = accountConfigOrchestration.createIncompleteSession(
      customImapSelectedState,
      2,
    );
    const serializedSession = JSON.stringify(safeSession);
    assert.equal(serializedSession.includes("one-use-password"), false);
    assert.equal(serializedSession.includes("one-use-smtp-password"), false);
    assert.equal(serializedSession.includes("must-remain-ephemeral"), false);
    assert.equal(
      serializedSession.includes("must-remain-ephemeral-smtp"),
      false,
    );
    assert.equal(serializedSession.includes("imap.example.com"), false);
    assert.equal(serializedSession.includes("verified.imap@example.com"), false);

    const serializedAttempt = JSON.stringify(customImapAttemptSnapshot);
    assert.equal(serializedAttempt.includes("one-use-password"), false);
    assert.equal(serializedAttempt.includes("must-remain-ephemeral"), false);
    assert.equal("password" in customImapAttemptSnapshot, false);
    assert.equal("smtpPassword" in customImapAttemptSnapshot, false);
    assert.equal(
      customImapAttemptSnapshot.fingerprint.includes(
        "must-remain-ephemeral",
      ),
      false,
    );

    const incomingOnlyState: OnboardingState = {
      ...customImapSelectedState,
      selectedInboxes: ["demo"],
      inboxConnections: {
        ...customImapSelectedState.inboxConnections,
        demo: {
          ...connection,
          serverMailboxId: "imap-server-1",
          connected: true,
          connectionStatus: "connected",
          imapConnectionStatus: "connected",
          smtpConnectionStatus: "not_configured",
          fullyConnected: false,
        },
      },
    };
    assert.equal(
      areSelectedOnboardingInboxesFullyConnected(incomingOnlyState),
      false,
    );
    assert.equal(
      areSelectedOnboardingInboxesFullyConnected({
        ...incomingOnlyState,
        inboxConnections: {
          ...incomingOnlyState.inboxConnections,
          demo: {
            ...incomingOnlyState.inboxConnections.demo,
            smtpConnectionStatus: "connected",
            fullyConnected: true,
          },
        },
      }),
      true,
    );
  });

  await test("custom IMAP attempts are globally serial and only readback can connect", async () => {
    const harness = createCustomImapAttemptHarness();
    const secondSnapshot =
      createCustomImapOnboardingAttemptSnapshot({
        onboardingInboxId: "main",
        selectedInboxes:
          customImapSelectedState.selectedInboxes,
        connection: {
          ...customImapSelectedState.inboxConnections.demo,
          email: "second@example.com",
        },
        passwordRevision: 1,
      });

    assert.equal(
      harness.coordinator.start(
        harness.snapshot,
        "ephemeral-test-value",
      ),
      true,
    );
    assert.equal(
      harness.coordinator.start(
        harness.snapshot,
        "duplicate-test-value",
      ),
      false,
    );
    assert.equal(
      harness.coordinator.start(
        secondSnapshot,
        "second-test-value",
      ),
      false,
    );
    assert.equal(
      harness.guard?.attemptToken,
      harness.snapshot.attemptToken,
    );
    assert.equal(harness.guard?.phase, "posting");
    await flushAsyncWork();
    assert.equal(harness.postCalls.length, 1);
    assert.equal(
      harness.postCalls[0].passwordWasPresent,
      true,
    );
    assert.equal(
      harness.postCalls[0].smtpPasswordWasPresent,
      true,
    );
    assert.equal(harness.password, "");
    assert.equal(harness.passwordRevision, 2);
    assert.equal(harness.passwordClearCalls, 1);
    assert.equal(harness.smtpPassword, "");
    assert.equal(harness.smtpPasswordRevision, 2);
    assert.equal(harness.smtpPasswordClearCalls, 1);
    assert.equal(harness.timers.pendingCount(), 1);

    harness.postCalls[0].pending.resolve(
      successfulCustomImapPost,
    );
    await flushAsyncWork();
    assert.equal(harness.readbackCalls.length, 1);
    assert.equal(harness.password, "");
    assert.equal(harness.passwordRevision, 2);
    assert.equal(harness.passwordClearCalls, 1);
    assert.equal(harness.matchedCalls, 0);

    harness.readbackCalls[0].pending.resolve(
      matchedReadback(harness.connection),
    );
    await flushAsyncWork();
    assert.equal(harness.matchedCalls, 1);
    assert.equal(harness.connection.connected, true);
    assert.deepEqual(harness.matchedServerMailboxIds, [
      "imap-server-1",
    ]);
    assert.equal(harness.guard, null);
    assert.equal(harness.timers.pendingCount(), 0);
    assert.equal(harness.postCalls.length, 1);
    assert.equal(harness.readbackCalls.length, 1);
  });

  await test("late custom IMAP success or failure cannot overwrite a newer draft", async () => {
    for (const postResult of [
      successfulCustomImapPost,
      failedCustomImapPost,
    ]) {
      const harness = createCustomImapAttemptHarness();
      assert.equal(
        harness.coordinator.start(
          harness.snapshot,
          "ephemeral-test-value",
        ),
        true,
      );
      await flushAsyncWork();
      harness.replacePassword("newer-local-value");
      harness.replaceEmail("newer@example.com");
      harness.postCalls[0].pending.resolve(postResult);
      await flushAsyncWork();
      assert.equal(harness.readbackCalls.length, 1);
      assert.equal(harness.password, "newer-local-value");
      assert.equal(harness.passwordRevision, 3);
      assert.equal(harness.passwordClearCalls, 1);

      harness.readbackCalls[0].pending.resolve(
        matchedReadback(harness.connection),
      );
      await flushAsyncWork();
      assert.equal(harness.matchedCalls, 0);
      assert.equal(harness.absentCalls, 0);
      assert.equal(harness.requiredCalls, 0);
      assert.equal(harness.connection.connected, false);
      assert.equal(
        harness.connection.email,
        "newer@example.com",
      );
      assert.equal(
        harness.guard?.phase,
        "reconciliation_required",
      );
      assert.equal(harness.guard?.recovery, "reload");
      assert.equal(
        harness.coordinator.retryReconciliation(),
        false,
      );
      assert.equal(
        harness.guards.some((guard) => guard === null),
        false,
      );
      assert.equal(harness.timers.pendingCount(), 0);
      harness.coordinator.dispose();
    }
  });

  await test("stale custom IMAP absence retires only the old guard without touching the newer draft", async () => {
    const harness = createCustomImapAttemptHarness();
    harness.coordinator.start(
      harness.snapshot,
      "ephemeral-test-value",
    );
    await flushAsyncWork();
    harness.replacePassword("newer-local-value");
    harness.replaceEmail("newer@example.com");
    harness.postCalls[0].pending.resolve(
      successfulCustomImapPost,
    );
    await flushAsyncWork();
    assert.equal(harness.readbackCalls.length, 1);
    harness.readbackCalls[0].pending.resolve({
      status: "absent",
    });
    await flushAsyncWork();
    assert.equal(harness.guard, null);
    assert.equal(harness.absentCalls, 0);
    assert.equal(harness.requiredCalls, 0);
    assert.equal(harness.matchedCalls, 0);
    assert.equal(harness.connection.connected, false);
    assert.equal(harness.connection.email, "newer@example.com");
    assert.equal(harness.password, "newer-local-value");
    assert.equal(harness.passwordRevision, 3);
    assert.equal(harness.postCalls.length, 1);
    assert.equal(harness.readbackCalls.length, 1);
  });

  await test("custom IMAP unmount aborts and clears its timer without state apply", async () => {
    const harness = createCustomImapAttemptHarness();
    assert.equal(
      harness.coordinator.start(
        harness.snapshot,
        "ephemeral-test-value",
      ),
      true,
    );
    await flushAsyncWork();
    assert.equal(harness.postCalls.length, 1);
    assert.equal(harness.postCalls[0].signal.aborted, false);
    assert.equal(harness.timers.pendingCount(), 1);

    harness.setMounted(false);
    harness.coordinator.dispose();
    assert.equal(harness.postCalls[0].signal.aborted, true);
    assert.equal(harness.timers.pendingCount(), 0);
    harness.postCalls[0].pending.resolve(
      successfulCustomImapPost,
    );
    await flushAsyncWork();
    assert.equal(harness.readbackCalls.length, 0);
    assert.equal(harness.password, "");
    assert.equal(harness.passwordClearCalls, 1);
    assert.equal(harness.matchedCalls, 0);
    assert.equal(harness.absentCalls, 0);
    assert.equal(harness.requiredCalls, 0);
    assert.equal(harness.connection.connected, false);
  });

  await test("custom IMAP remount adopts the credential-free guard for GET-only recovery", async () => {
    const firstMount = createCustomImapAttemptHarness();
    assert.equal(
      firstMount.coordinator.start(
        firstMount.snapshot,
        "ephemeral-test-value",
      ),
      true,
    );
    await flushAsyncWork();
    const preservedGuard = firstMount.guard;
    assert.notEqual(preservedGuard, null);
    assert.equal(
      preservedGuard?.snapshot,
      firstMount.snapshot,
    );
    firstMount.setMounted(false);
    firstMount.coordinator.dispose();
    assert.equal(firstMount.postCalls[0].signal.aborted, true);
    assert.equal(firstMount.timers.pendingCount(), 0);

    const secondMount = createCustomImapAttemptHarness();
    assert.equal(
      secondMount.coordinator.adoptReconciliationGuard(
        preservedGuard!,
      ),
      true,
    );
    assert.equal(
      secondMount.guard?.phase,
      "reconciliation_required",
    );
    assert.equal(secondMount.postCalls.length, 0);
    assert.equal(
      secondMount.coordinator.retryReconciliation(),
      true,
    );
    await flushAsyncWork();
    assert.equal(secondMount.postCalls.length, 0);
    assert.equal(secondMount.readbackCalls.length, 1);
    secondMount.readbackCalls[0].pending.resolve(
      matchedReadback(secondMount.connection),
    );
    await flushAsyncWork();
    assert.equal(secondMount.matchedCalls, 1);
    assert.equal(secondMount.connection.connected, true);
    assert.equal(secondMount.guard, null);
    assert.equal(secondMount.timers.pendingCount(), 0);
  });

  await test("custom IMAP remount quarantines a drifted guard behind server reload recovery", async () => {
    const firstMount = createCustomImapAttemptHarness();
    firstMount.coordinator.start(
      firstMount.snapshot,
      "ephemeral-test-value",
    );
    await flushAsyncWork();
    const preservedGuard = firstMount.guard;
    assert.notEqual(preservedGuard, null);
    firstMount.setMounted(false);
    firstMount.coordinator.dispose();

    const driftedMount = createCustomImapAttemptHarness();
    driftedMount.replaceEmail("newer@example.com");
    assert.equal(
      driftedMount.coordinator.adoptReconciliationGuard(
        preservedGuard!,
      ),
      true,
    );
    assert.equal(
      driftedMount.guard?.phase,
      "reconciliation_required",
    );
    assert.equal(driftedMount.guard?.recovery, "reload");
    assert.equal(
      driftedMount.coordinator.retryReconciliation(),
      false,
    );
    assert.equal(driftedMount.postCalls.length, 0);
    assert.equal(driftedMount.readbackCalls.length, 0);
    assert.equal(driftedMount.matchedCalls, 0);
    assert.equal(driftedMount.absentCalls, 0);
    assert.equal(driftedMount.requiredCalls, 0);
    assert.equal(driftedMount.connection.connected, false);
  });

  await test("server-reload quarantine remains sticky for the same token after an exact remount", async () => {
    const firstMount = createCustomImapAttemptHarness();
    firstMount.coordinator.start(
      firstMount.snapshot,
      "ephemeral-test-value",
    );
    await flushAsyncWork();
    firstMount.replaceEmail("newer@example.com");
    firstMount.postCalls[0].pending.resolve(
      successfulCustomImapPost,
    );
    await flushAsyncWork();
    firstMount.readbackCalls[0].pending.resolve(
      matchedReadback(firstMount.connection),
    );
    await flushAsyncWork();
    const quarantinedGuard = firstMount.guard;
    assert.equal(quarantinedGuard?.recovery, "reload");
    firstMount.setMounted(false);
    firstMount.coordinator.dispose();

    const exactRemount = createCustomImapAttemptHarness();
    assert.equal(
      exactRemount.coordinator.adoptReconciliationGuard(
        quarantinedGuard!,
      ),
      true,
    );
    assert.equal(exactRemount.guard?.recovery, "reload");
    assert.equal(
      exactRemount.coordinator.retryReconciliation(),
      false,
    );
    assert.equal(exactRemount.postCalls.length, 0);
    assert.equal(exactRemount.readbackCalls.length, 0);
    assert.equal(exactRemount.matchedCalls, 0);
  });

  await test("custom IMAP timeout reconciles server commit without a second POST", async () => {
    const harness = createCustomImapAttemptHarness();
    assert.equal(
      harness.coordinator.start(
        harness.snapshot,
        "ephemeral-test-value",
      ),
      true,
    );
    await flushAsyncWork();
    const postSignal = harness.postCalls[0].signal;
    harness.timers.fireNext();
    assert.equal(postSignal.aborted, true);
    await flushAsyncWork();
    assert.equal(harness.readbackCalls.length, 1);
    assert.notEqual(
      harness.readbackCalls[0].signal,
      postSignal,
    );
    assert.equal(
      harness.readbackCalls[0].signal.aborted,
      false,
    );
    assert.equal(harness.password, "");

    harness.readbackCalls[0].pending.resolve(
      matchedReadback(harness.connection),
    );
    await flushAsyncWork();
    assert.equal(harness.matchedCalls, 1);
    assert.equal(harness.connection.connected, true);
    assert.equal(harness.postCalls.length, 1);
    assert.equal(harness.readbackCalls.length, 1);
    assert.equal(harness.guard, null);
    assert.equal(harness.timers.pendingCount(), 0);
    harness.postCalls[0].pending.resolve(
      successfulCustomImapPost,
    );
  });

  await test("timeout without a server commit fails safely and unlocks", async () => {
    const harness = createCustomImapAttemptHarness();
    harness.coordinator.start(
      harness.snapshot,
      "ephemeral-test-value",
    );
    await flushAsyncWork();
    harness.timers.fireNext();
    await flushAsyncWork();
    harness.readbackCalls[0].pending.resolve({
      status: "absent",
    });
    await flushAsyncWork();
    assert.equal(harness.matchedCalls, 0);
    assert.equal(harness.absentCalls, 1);
    assert.equal(harness.connection.connected, false);
    assert.equal(harness.guard, null);
    assert.equal(harness.postCalls.length, 1);
    assert.equal(harness.timers.pendingCount(), 0);
    harness.postCalls[0].pending.resolve(
      failedCustomImapPost,
    );
  });

  await test("readback failure locks reconciliation-only retries", async () => {
    const harness = createCustomImapAttemptHarness();
    harness.coordinator.start(
      harness.snapshot,
      "ephemeral-test-value",
    );
    await flushAsyncWork();
    harness.postCalls[0].pending.resolve(failedCustomImapPost);
    await flushAsyncWork();
    assert.equal(harness.readbackCalls.length, 1);
    const firstReadbackSignal =
      harness.readbackCalls[0].signal;
    harness.timers.fireNext();
    assert.equal(firstReadbackSignal.aborted, true);
    await flushAsyncWork();
    assert.equal(harness.requiredCalls, 1);
    assert.equal(
      harness.guard?.phase,
      "reconciliation_required",
    );
    assert.equal(harness.password, "");
    assert.equal(harness.passwordClearCalls, 1);
    assert.equal(
      harness.coordinator.start(
        harness.snapshot,
        "must-not-post-again",
      ),
      false,
    );

    assert.equal(
      harness.coordinator.retryReconciliation(),
      true,
    );
    await flushAsyncWork();
    assert.equal(harness.readbackCalls.length, 2);
    assert.equal(harness.postCalls.length, 1);
    harness.readbackCalls[1].pending.reject(
      new Error("still unavailable"),
    );
    await flushAsyncWork();
    assert.equal(harness.requiredCalls, 2);
    assert.equal(
      harness.guard?.phase,
      "reconciliation_required",
    );

    assert.equal(
      harness.coordinator.retryReconciliation(),
      true,
    );
    await flushAsyncWork();
    assert.equal(harness.readbackCalls.length, 3);
    harness.readbackCalls[2].pending.resolve(
      matchedReadback(harness.connection),
    );
    await flushAsyncWork();
    assert.equal(harness.matchedCalls, 1);
    assert.equal(harness.connection.connected, true);
    assert.equal(harness.guard, null);
    assert.equal(harness.postCalls.length, 1);
    assert.equal(harness.timers.pendingCount(), 0);
  });

  await test("reconciliation-only absence unlocks without credentials or POST", async () => {
    const harness = createCustomImapAttemptHarness();
    harness.coordinator.start(
      harness.snapshot,
      "ephemeral-test-value",
    );
    await flushAsyncWork();
    harness.postCalls[0].pending.resolve(
      successfulCustomImapPost,
    );
    await flushAsyncWork();
    harness.readbackCalls[0].pending.reject(
      new Error("temporary failure"),
    );
    await flushAsyncWork();
    assert.equal(
      harness.guard?.phase,
      "reconciliation_required",
    );
    const postCountBeforeRetry = harness.postCalls.length;
    harness.coordinator.retryReconciliation();
    await flushAsyncWork();
    harness.readbackCalls[1].pending.resolve({
      status: "absent",
    });
    await flushAsyncWork();
    assert.equal(
      harness.postCalls.length,
      postCountBeforeRetry,
    );
    assert.equal(harness.absentCalls, 1);
    assert.equal(harness.matchedCalls, 0);
    assert.equal(harness.guard, null);
  });

  await test("attempt metadata and runtime never enter persistent browser storage", async () => {
    const localStorage = new MemoryStorage();
    const sessionStorage = new MemoryStorage();
    const harness = createCustomImapAttemptHarness();
    harness.coordinator.start(
      harness.snapshot,
      "ephemeral-test-value",
    );
    await flushAsyncWork();
    harness.postCalls[0].pending.resolve(
      successfulCustomImapPost,
    );
    await flushAsyncWork();
    harness.readbackCalls[0].pending.resolve({
      status: "absent",
    });
    await flushAsyncWork();
    accountConfigOrchestration.writeOnboardingSessionMirror(
      incompleteSession(2, {
        ...customImapSelectedState,
        inboxConnections: {
          ...customImapSelectedState.inboxConnections,
          demo: {
            ...customImapSelectedState.inboxConnections.demo,
            customImap: {
              ...customImapSelectedState.inboxConnections.demo
                .customImap,
              password: "must-never-persist",
            },
          },
        },
      }),
      localStorage,
      {
        state: "account-onboarding-state",
        draft: "account-onboarding-draft",
        view: "account-app-view",
      },
    );
    assert.equal(localStorage.mutations.length > 0, true);
    assert.deepEqual(sessionStorage.mutations, []);
    const serializedPublicRuntime = JSON.stringify({
      snapshot: harness.snapshot,
      guards: harness.guards,
      localStorage: localStorage.snapshot(),
      sessionStorage: sessionStorage.snapshot(),
    });
    for (const forbidden of [
      "ephemeral-test-value",
      "must-never-persist",
      '"password":',
      "AbortController",
      "signal",
      "secret",
    ]) {
      assert.equal(
        serializedPublicRuntime.includes(forbidden),
        false,
      );
    }
  });

  await test("startup scrubs legacy managed-inbox storage before guest, missing, or offline flows", async () => {
    const legacyManagedInboxes = JSON.stringify([
      {
        id: "legacy-custom",
        onboardingInboxId: "demo",
        title: "Legacy Custom",
        email: "legacy@example.com",
        provider: "custom_imap",
        connected: false,
        connectionMethod: "imap",
        connectionStatus: "not_connected",
        privateKey: "legacy-private-key",
        bearer: "legacy-bearer",
        messages: [{ body: "legacy-message-body" }],
        customImap: {
          host: "imap.example.com",
          port: "993",
          ssl: true,
          username: "legacy@example.com",
          password: "legacy-imap-password",
          apiKey: "legacy-nested-api-key",
        },
        customSmtp: {
          host: "smtp.example.com",
          port: "587",
          security: "starttls",
          username: "",
          password: "legacy-smtp-password",
          useSameCredentials: true,
          encryptedValue: "legacy-encrypted-value",
        },
      },
    ]);
    const assertScrubbed = (storage: MemoryStorage) => {
      const rewritten =
        storage.snapshot()[MANAGED_INBOXES_KEY] ?? "";
      const parsed = JSON.parse(rewritten) as Array<
        Record<string, any>
      >;
      assert.equal(parsed.length, 1);
      assert.equal(parsed[0].id, "legacy-custom");
      assert.equal(
        parsed[0].customSmtp.useSameCredentials,
        true,
      );
      assert.equal(parsed[0].customImap.password, "");
      assert.equal(parsed[0].customSmtp.password, "");
      for (const forbidden of [
        "legacy-private-key",
        "legacy-bearer",
        "legacy-message-body",
        "legacy-imap-password",
        "legacy-nested-api-key",
        "legacy-smtp-password",
        "legacy-encrypted-value",
        "privateKey",
        "bearer",
        "messages",
        "apiKey",
        "encryptedValue",
      ]) {
        assert.equal(rewritten.includes(forbidden), false);
      }
      assert.equal(
        storage.mutations.some(
          ({ type, key }) =>
            type === "set" && key === MANAGED_INBOXES_KEY,
        ),
        true,
      );
    };

    for (const flow of ["guest", "missing", "offline"] as const) {
      const storage = new MemoryStorage({
        [MANAGED_INBOXES_KEY]: legacyManagedInboxes,
      });
      accountConfigOrchestration.scrubManagedInboxBrowserStorage(
        storage,
      );
      if (flow === "guest") {
        const scope =
          accountConfigOrchestration.resolveLocalOnboardingIdentityScope({
            userType: "guest",
            userEmail: "guest@example.com",
            collaborationInvite: {
              mode: "invite",
              inviteToken: "guest-invite",
              inviteeEmail: "guest@example.com",
            },
          });
        assert.notEqual(scope, null);
        accountConfigOrchestration.hydrateLocalOnboardingScope(
          scope!,
          storage,
        );
      } else {
        await hydrateResult(
          flow === "missing"
            ? { status: "missing", config: null }
            : {
                status: "network_error",
                error: {
                  code: "network_error",
                  message: "offline",
                },
              },
          storage,
        );
      }
      assertScrubbed(storage);
    }
  });

  await test("active custom IMAP controls and navigation render disabled", () => {
    const lockedState: OnboardingState = {
      ...customImapSelectedState,
      inboxConnections: {
        ...customImapSelectedState.inboxConnections,
        main: {
          ...customImapSelectedState.inboxConnections.demo,
          email: "first@example.com",
          customImap: {
            ...customImapSelectedState.inboxConnections.demo
              .customImap,
            username: "first@example.com",
            password: "",
          },
        },
        demo: {
          ...customImapSelectedState.inboxConnections.demo,
          customImap: {
            ...customImapSelectedState.inboxConnections.demo
              .customImap,
            password: "",
          },
        },
      },
    };
    const guard: CustomImapOnboardingAttemptGuard = {
      attemptToken: customImapAttemptSnapshot.attemptToken,
      onboardingInboxId: "demo",
      phase: "posting",
      recovery: "check",
      snapshot: customImapAttemptSnapshot,
    };
    const markup = renderToStaticMarkup(
      createElement(StepConnectInboxes, {
        selectedInboxes: lockedState.selectedInboxes,
        customInboxes: lockedState.customInboxes,
        inboxConnections: lockedState.inboxConnections,
        onProviderChange: () => undefined,
        onEmailChange: () => undefined,
        onCustomImapChange: () => undefined,
        onCustomSmtpChange: () => undefined,
        onReuseCustomImap: () => undefined,
        onConnectInbox: () => undefined,
        onReloadAccountConfig: async () => ({
          status: "required",
        }),
        onApplyAuthoritativeCustomImapConnection: () =>
          undefined,
        customImapAttemptGuard: guard,
        onCustomImapAttemptGuardChange: () => undefined,
        canRemoveInbox: () => true,
        onRemoveInbox: () => undefined,
        onAddInbox: () => undefined,
      }),
    );
    for (const control of [
      "provider-main-custom_imap",
      "provider-demo-custom_imap",
      "connect-main",
      "connect-demo",
      "email-demo",
      "host-demo",
      "port-demo",
      "username-demo",
      "password-demo",
      "smtp-host-demo",
      "smtp-port-demo",
      "smtp-same-credentials-demo",
      "smtp-password-demo",
      "reuse-demo",
      "ssl-demo",
      "add-inbox",
    ]) {
      assertMarkupControlDisabled(markup, control);
    }
    assert.equal(markup.includes("Remove inbox"), false);

    let backCalls = 0;
    let nextCalls = 0;
    const navigationMarkup = renderToStaticMarkup(
      createElement(NavigationBar, {
        canGoBack: true,
        nextLabel: "Complete setup",
        onBack: () => {
          backCalls += 1;
        },
        onNext: () => {
          nextCalls += 1;
        },
        isBackDisabled: true,
        isNextDisabled: true,
      }),
    );
    assertMarkupControlDisabled(navigationMarkup, "back");
    assertMarkupControlDisabled(navigationMarkup, "next");
    assert.equal(
      invokeNavigationAction(true, () => {
        backCalls += 1;
      }),
      false,
    );
    assert.equal(backCalls, 0);
    assert.equal(nextCalls, 0);
    assert.equal(shouldBlockOnboardingMutation(guard), true);
    assert.equal(
      shouldBlockOnboardingMutation({
        ...guard,
        phase: "reconciliation_required",
      }),
      true,
    );
    assert.equal(shouldBlockOnboardingMutation(null), false);

    const reloadMarkup = renderToStaticMarkup(
      createElement(StepConnectInboxes, {
        selectedInboxes: lockedState.selectedInboxes,
        customInboxes: lockedState.customInboxes,
        inboxConnections: lockedState.inboxConnections,
        onProviderChange: () => undefined,
        onEmailChange: () => undefined,
        onCustomImapChange: () => undefined,
        onCustomSmtpChange: () => undefined,
        onReuseCustomImap: () => undefined,
        onConnectInbox: () => undefined,
        onReloadAccountConfig: async () => ({
          status: "required",
        }),
        onApplyAuthoritativeCustomImapConnection: () =>
          undefined,
        customImapAttemptGuard: {
          ...guard,
          phase: "reconciliation_required",
          recovery: "reload",
        },
        onCustomImapAttemptGuardChange: () => undefined,
        canRemoveInbox: () => true,
        onRemoveInbox: () => undefined,
        onAddInbox: () => undefined,
      }),
    );
    assertMarkupControlEnabled(reloadMarkup, "connect-demo");
    assert.equal(
      reloadMarkup.includes("Reload setup from server"),
      true,
    );
    assert.equal(
      reloadMarkup.includes(
        "Reload setup to reconcile with the server.",
      ),
      true,
    );
    let reloadCalls = 0;
    invokeCustomImapServerReload(() => {
      reloadCalls += 1;
    });
    assert.equal(reloadCalls, 1);
    const globalRecoveryMarkup = renderToStaticMarkup(
      createElement(CustomImapServerReloadRecovery, {
        visible: true,
        onReload: () => {
          reloadCalls += 1;
        },
      }),
    );
    assertMarkupControlEnabled(
      globalRecoveryMarkup,
      "reload-custom-imap-recovery",
    );
    assert.equal(
      globalRecoveryMarkup.includes(
        "Reload setup from server",
      ),
      true,
    );
    assert.equal(
      renderToStaticMarkup(
        createElement(CustomImapServerReloadRecovery, {
          visible: false,
          onReload: () => undefined,
        }),
      ),
      "",
    );
  });

  await test("only authoritative custom IMAP config readback can project connected", () => {
    const serverSession = incompleteSession(2, customImapSelectedState);
    const validReadback: UserAccountConfigReadResult = {
      status: "found",
      config: {
        onboardingSession: serverSession,
        managedInboxes: [connectedCustomImapManagedInbox()],
      },
    };
    const resolved = accountConfigOrchestration.resolveCustomImapReadback(
      customImapSelectedState,
      customImapAttemptSnapshot,
      validReadback,
    );
    assert.equal(resolved.status, "matched");
    if (resolved.status !== "matched") {
      throw new Error("Expected authoritative custom IMAP match");
    }
    assert.equal(resolved.serverMailboxId, "imap-server-1");
    assert.equal(
      resolved.connection.serverMailboxId,
      "imap-server-1",
    );
    assert.equal(resolved.connection.connected, true);
    assert.equal(
      resolved.connection.connectionStatus,
      "connected",
    );
    assert.equal(resolved.connection.connectionMethod, "imap");
    assert.equal(resolved.connection.provider, "custom_imap");
    assert.equal(resolved.connection.imapConnectionStatus, "connected");
    assert.equal(
      resolved.connection.smtpConnectionStatus,
      "not_configured",
    );
    assert.equal(resolved.connection.fullyConnected, false);
    assert.deepEqual(resolved.connection.customImap, {
      host: "imap.example.com",
      port: "993",
      ssl: true,
      username: "verified.imap@example.com",
      password: "",
    });
    assert.equal(resolved.connection.customImap.password, "");
    assert.equal(resolved.connection.customSmtp.password, "");
    assert.equal(isConnectionReady(resolved.connection, "", ""), false);
    assert.equal(
      isConnectionReady(
        resolved.connection,
        "",
        "one-use-smtp-password",
      ),
      true,
    );
    const partialSameCredentialsConnection: InboxConnection = {
      ...resolved.connection,
      customSmtp: {
        ...resolved.connection.customSmtp,
        username: "",
        useSameCredentials: true,
      },
    };
    const partialMarkup = renderToStaticMarkup(
      createElement(StepConnectInboxes, {
        selectedInboxes: ["demo"],
        customInboxes: [],
        inboxConnections: {
          demo: partialSameCredentialsConnection,
        },
        onProviderChange: () => undefined,
        onEmailChange: () => undefined,
        onCustomImapChange: () => undefined,
        onCustomSmtpChange: () => undefined,
        onReuseCustomImap: () => undefined,
        onConnectInbox: () => undefined,
        onReloadAccountConfig: async () => ({ status: "required" }),
        onApplyAuthoritativeCustomImapConnection: () => undefined,
        customImapAttemptGuard: null,
        onCustomImapAttemptGuardChange: () => undefined,
        onAddInbox: () => undefined,
      }),
    );
    assert.equal(partialMarkup.includes("Incoming connected"), true);
    assert.equal(
      partialMarkup.includes("Outgoing mail not configured"),
      true,
    );
    assert.equal(
      partialMarkup.includes('data-attempt-control="password-demo"'),
      false,
    );
    assertMarkupControlEnabled(partialMarkup, "smtp-host-demo");
    assertMarkupControlEnabled(partialMarkup, "connect-demo");
    assert.equal(partialMarkup.includes("Connect outgoing mail"), true);
    const fullReadback = accountConfigOrchestration.resolveCustomImapReadback(
      customImapSelectedState,
      customImapAttemptSnapshot,
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox({
              smtpConnectionStatus: "connected",
              fullyConnected: true,
              customSmtp: {
                host: "smtp.example.com",
                port: "587",
                security: "starttls",
                username: "smtp-user@example.com",
                password: "",
                useSameCredentials: false,
              },
            }),
          ],
        },
      },
    );
    assert.equal(fullReadback.status, "matched");
    if (fullReadback.status !== "matched") {
      throw new Error("Expected authoritative SMTP match");
    }
    assert.equal(fullReadback.connection.smtpConnectionStatus, "connected");
    assert.equal(fullReadback.connection.fullyConnected, true);
    assert.equal(fullReadback.connection.customSmtp.password, "");
    const readbackWithUnrelatedFullSmtp: UserAccountConfigReadResult = {
      status: "found",
      config: {
        onboardingSession: serverSession,
        managedInboxes: [
          connectedCustomImapManagedInbox(),
          connectedCustomImapManagedInbox({
            id: "imap-server-2",
            onboardingInboxId: undefined,
            email: "other@example.com",
            smtpConnectionStatus: "connected",
            fullyConnected: true,
            customImap: {
              host: "imap.other.example.com",
              port: "993",
              ssl: true,
              username: "other@example.com",
              password: "",
            },
            customSmtp: {
              host: "smtp.other.example.com",
              port: "587",
              security: "starttls",
              username: "",
              password: "",
              useSameCredentials: true,
            },
          }),
        ],
      },
    };
    assert.equal(
      accountConfigOrchestration.resolveCustomImapReadback(
        customImapSelectedState,
        customImapAttemptSnapshot,
        readbackWithUnrelatedFullSmtp,
      ).status,
      "matched",
    );
    const stateWithConnectedGmail: OnboardingState = {
      ...customImapSelectedState,
      inboxConnections: {
        ...customImapSelectedState.inboxConnections,
        main: selectedState.inboxConnections.main,
      },
    };
    const appliedState =
      applyAuthoritativeCustomImapConnection(
        stateWithConnectedGmail,
        customImapAttemptSnapshot,
        resolved,
      );
    assert.equal(
      appliedState.inboxConnections.demo.connected,
      true,
    );
    assert.equal(
      appliedState.inboxConnections.demo.serverMailboxId,
      "imap-server-1",
    );
    assert.equal(
      appliedState.inboxConnections.main,
      stateWithConnectedGmail.inboxConnections.main,
    );
    assert.equal(
      appliedState.inboxConnections.main.connected,
      true,
    );
    assert.deepEqual(
      appliedState.selectedInboxes,
      customImapSelectedState.selectedInboxes,
    );
    assert.equal(
      appliedState.inboxCount,
      customImapSelectedState.inboxCount,
    );
    assert.deepEqual(
      appliedState.customInboxes,
      customImapSelectedState.customInboxes,
    );
    const newerDraftState = {
      ...customImapSelectedState,
      inboxConnections: {
        ...customImapSelectedState.inboxConnections,
        demo: {
          ...customImapSelectedState.inboxConnections.demo,
          email: "newer@example.com",
        },
      },
    };
    assert.equal(
      applyAuthoritativeCustomImapConnection(
        newerDraftState,
        customImapAttemptSnapshot,
        resolved,
      ),
      newerDraftState,
    );
    assert.deepEqual(
      accountConfigOrchestration.resolveCustomImapReadback(
        newerDraftState,
        customImapAttemptSnapshot,
        validReadback,
      ),
      { status: "conflict" },
    );
    assert.deepEqual(
      accountConfigOrchestration.resolveCustomImapReadback(
        newerDraftState,
        customImapAttemptSnapshot,
        {
          status: "found",
          config: {
            onboardingSession: serverSession,
            managedInboxes: [],
          },
        },
      ),
      { status: "absent" },
    );

    assert.deepEqual(
      accountConfigOrchestration.resolveCustomImapReadback(
        customImapSelectedState,
        customImapAttemptSnapshot,
        { status: "missing", config: null },
      ),
      { status: "absent" },
    );
    assert.deepEqual(
      accountConfigOrchestration.resolveCustomImapReadback(
        customImapSelectedState,
        customImapAttemptSnapshot,
        {
          status: "found",
          config: {
            onboardingSession: serverSession,
            managedInboxes: [],
          },
        },
      ),
      { status: "absent" },
    );

    const rejectedReadbacks: UserAccountConfigReadResult[] = [
      {
        status: "network_error",
        error: { code: "network_error", message: "offline" },
      },
      {
        status: "found",
        config: {
          onboardingSession: { completed: false, choices: "malformed" },
          managedInboxes: [connectedCustomImapManagedInbox()],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: incompleteSession(2, selectedChoiceState),
          managedInboxes: [connectedCustomImapManagedInbox()],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: completedV1Session(2, customImapSelectedState),
          managedInboxes: [connectedCustomImapManagedInbox()],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox(),
            connectedCustomImapManagedInbox({
              id: "imap-server-2",
              email: "second@example.com",
            }),
          ],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox(),
            connectedCustomImapManagedInbox({
              id: "imap-server-2",
              onboardingInboxId: "main",
              email: " VERIFIED.IMAP@example.com ",
            }),
          ],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox({
              onboardingInboxId: "main",
            }),
          ],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox({
              email: "wrong@example.com",
            }),
          ],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox({
              imapPasswordSet: false,
            }),
          ],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox({
              smtpPasswordSet: true,
            }),
          ],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox({
              provider: "google",
            }),
          ],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox(),
            connectedCustomImapManagedInbox({
              id: "imap-server-1",
              onboardingInboxId: "main",
              email: "other@example.com",
            }),
          ],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox({ id: "draft-client-id" }),
          ],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox({
              id: "DRAFT-client-id",
            }),
          ],
        },
      },
      ...[
        { credentialGeneration: 4 },
        { secretGeneration: 4 },
        { authorization: "must-not-be-authority" },
        { authToken: "must-not-be-authority" },
        { clientSecret: "must-not-be-authority" },
        { imapPasswordHash: "must-not-be-authority" },
        { authorizationBearer: "must-not-be-authority" },
        { accessTokenExpires: "must-not-be-authority" },
        { credentialBlob: "must-not-be-authority" },
        { privateKey: "must-not-be-authority" },
        { apiKey: "must-not-be-authority" },
        { bearer: "must-not-be-authority" },
        { encryptedValue: "must-not-be-authority" },
        { session: "must-not-be-authority" },
        { messages: [{ id: "cached-message" }] },
        { connectionMessage: {} },
        { internalRole: "not-a-role" },
        { focusPreferences: { demos: "urgent" } },
        {
          oauthAuthorizationUrl:
            "https://oauth.example.test/stale",
        },
      ].map((unsafeMetadata) => ({
        status: "found" as const,
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox(
              unsafeMetadata as any,
            ),
          ],
        },
      })),
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox({ connected: false }),
          ],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox({
              smtpConnectionStatus: "connected",
              fullyConnected: true,
              customSmtp: {
                host: "smtp.must-not-be-authoritative.example",
                password: "",
              },
            }),
          ],
        },
      },
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [
            connectedCustomImapManagedInbox({
              credentialReference: "must-not-be-authority",
            } as any),
          ],
        },
      },
      ...[
        undefined,
        null,
        {},
        {
          host: " imap.example.com",
          port: "993",
          ssl: true,
          username: "verified.imap@example.com",
        },
        {
          host: "imap .example.com",
          port: "993",
          ssl: true,
          username: "verified.imap@example.com",
        },
        {
          host: "imap.example.com",
          port: "993",
          ssl: true,
          username: "verified.imap@example.com ",
        },
        {
          host: "imap.example.com",
          port: "993",
          ssl: false,
          username: "verified.imap@example.com",
        },
        {
          host: "imap.example.com",
          port: "",
          ssl: true,
          username: "verified.imap@example.com",
        },
        {
          host: "imap.example.com",
          port: "0",
          ssl: true,
          username: "verified.imap@example.com",
        },
        {
          host: "imap.example.com",
          port: "65536",
          ssl: true,
          username: "verified.imap@example.com",
        },
        {
          host: "imap.example.com",
          port: "993x",
          ssl: true,
          username: "verified.imap@example.com",
        },
        {
          host: "imap.other.example",
          port: "993",
          ssl: true,
          username: "verified.imap@example.com",
        },
        {
          host: "imap.example.com",
          port: "993",
          ssl: true,
          username: "different-user",
        },
        {
          host: "imap.example.com",
          port: "993",
          ssl: true,
          username: "verified.imap@example.com",
          password: "server-secret",
        },
        {
          host: "imap.example.com",
          port: "993",
          ssl: true,
          username: "verified.imap@example.com",
          credentialReference: "must-not-be-authority",
        },
        {
          host: "imap.example.com",
          port: "993",
          ssl: true,
          username: "verified.imap@example.com",
          privateKey: "must-not-be-authority",
        },
        {
          host: "imap.example.com",
          port: "993",
          ssl: true,
          username: "verified.imap@example.com",
          apiKey: "must-not-be-authority",
        },
      ].map((customImap) => ({
        status: "found" as const,
        config: {
          onboardingSession: serverSession,
          managedInboxes: [connectedCustomImapManagedInbox({ customImap })],
        },
      })),
    ];
    for (const rejected of rejectedReadbacks) {
      const rejection = accountConfigOrchestration.resolveCustomImapReadback(
        customImapSelectedState,
        customImapAttemptSnapshot,
        rejected,
      );
      assert.equal(rejection.status, "required");
    }
    for (const unrelatedMalformedMailbox of [
      {},
      null,
    ]) {
      assert.equal(
        accountConfigOrchestration.resolveCustomImapReadback(
          customImapSelectedState,
          customImapAttemptSnapshot,
          {
            status: "found",
            config: {
              onboardingSession: serverSession,
              managedInboxes: [unrelatedMalformedMailbox as any],
            },
          },
        ).status,
        "absent",
      );
    }
    for (const unrelatedMetadata of [
      { connectionType: "gmail" },
      { oauthOwnerEmail: "owner@gmail.com" },
      { provider: "evil" },
      { connectionStatus: "bogus" },
      { connectionMethod: "oauth" },
      { connectionMessage: {} },
      { internalRole: "not-a-role" },
      { focusPreferences: { demos: "urgent" } },
    ]) {
      assert.equal(
        accountConfigOrchestration.resolveCustomImapReadback(
          customImapSelectedState,
          customImapAttemptSnapshot,
          {
            status: "found",
            config: {
              onboardingSession: serverSession,
              managedInboxes: [
                connectedCustomImapManagedInbox(),
                connectedGoogleManagedInbox({
                  id: "managed-google-unrelated",
                  onboardingInboxId: "main",
                  email: "owner@gmail.com",
                  ...unrelatedMetadata,
                } as any),
              ],
            },
          },
        ).status,
        "matched",
      );
    }
  });

  await test("production incoming-only fixture hydrates target-scoped across safe SMTP absence forms", async () => {
    const productionState = createProductionIncomingOnlyState();
    const serverSession = incompleteSession(2, productionState);
    const gmailMailbox = productionGmailWithServerMetadata();
    const smtpAbsenceForms: Array<{
      name: string;
      value: unknown;
      omit?: boolean;
    }> = [
      { name: "missing", value: undefined, omit: true },
      { name: "empty object", value: {} },
      { name: "legacy empty password", value: { password: "" } },
      {
        name: "legacy empty password aliases",
        value: {
          password: "",
          smtpPassword: "",
          encryptedPassword: "",
        },
      },
    ];

    let partialConnection: InboxConnection | null = null;
    for (const smtpAbsenceForm of smtpAbsenceForms) {
      const customMailbox = productionIncomingOnlyManagedInbox({
        customSmtp: smtpAbsenceForm.value,
      });
      if (smtpAbsenceForm.omit) {
        delete (customMailbox as Record<string, unknown>).customSmtp;
      }
      const config: UserAccountConfig = {
        onboardingSession: serverSession,
        managedInboxes: [
          gmailMailbox,
          customMailbox,
        ],
      };
      const storage = new MemoryStorage();
      const firstHydration = await hydrateResult(
        { status: "found", config },
        storage,
      );
      const refreshedHydration = await hydrateResult(
        { status: "found", config },
        storage,
      );

      for (const outcome of [firstHydration, refreshedHydration]) {
        assert.equal(
          outcome.status,
          "found",
          smtpAbsenceForm.name,
        );
        if (outcome.status !== "found") {
          throw new Error(`Expected ${smtpAbsenceForm.name} hydration`);
        }
        const hydratedState = outcome.accountState.onboardingState;
        const hydratedCustom =
          hydratedState.inboxConnections[PRODUCTION_CUSTOM_INBOX_ID];
        assert.equal(outcome.accountState.view, "onboarding");
        assert.equal(outcome.accountState.onboardingStep, 2);
        assert.equal(
          outcome.accountState.persistedOnboardingSession?.completed,
          false,
        );
        assert.equal(hydratedCustom.connected, true);
        assert.equal(
          hydratedCustom.serverMailboxId,
          PRODUCTION_SERVER_MAILBOX_ID,
        );
        assert.equal(hydratedCustom.connectionStatus, "connected");
        assert.equal(hydratedCustom.imapConnectionStatus, "connected");
        assert.equal(
          hydratedCustom.smtpConnectionStatus,
          "not_configured",
        );
        assert.equal(hydratedCustom.fullyConnected, false);
        assert.deepEqual(hydratedCustom.customImap, {
          host: "mail.example.com",
          port: "993",
          ssl: true,
          username: "promo@example.com",
          password: "",
        });
        assert.equal(
          hydratedState.inboxConnections.main.connected,
          true,
        );
        assert.equal(
          hydratedState.inboxConnections.main.serverMailboxId,
          "managed-google-production",
        );
        assert.deepEqual(
          hydratedState.customInboxes,
          productionState.customInboxes,
        );
        assert.equal(
          areSelectedOnboardingInboxesFullyConnected(hydratedState),
          false,
        );
        assert.equal(
          Object.values(hydratedState.inboxConnections).filter(
            (connection) =>
              connection.serverMailboxId ===
              PRODUCTION_SERVER_MAILBOX_ID,
          ).length,
          1,
        );
        partialConnection = hydratedCustom;
      }

      if (!partialConnection) {
        throw new Error("Expected an incoming-only connection");
      }
      const reconciliationState: OnboardingState = {
        ...productionState,
        inboxConnections: {
          ...productionState.inboxConnections,
          [PRODUCTION_CUSTOM_INBOX_ID]: {
            ...partialConnection,
            customSmtp: {
              ...productionState.inboxConnections[
                PRODUCTION_CUSTOM_INBOX_ID
              ].customSmtp,
              password: "",
            },
          },
        },
      };
      const snapshot = createCustomImapOnboardingAttemptSnapshot({
        onboardingInboxId: PRODUCTION_CUSTOM_INBOX_ID,
        selectedInboxes: reconciliationState.selectedInboxes,
        connection:
          reconciliationState.inboxConnections[
            PRODUCTION_CUSTOM_INBOX_ID
          ],
        passwordRevision: 0,
        smtpPasswordRevision: 0,
      });
      const resolved =
        accountConfigOrchestration.resolveCustomImapReadback(
          reconciliationState,
          snapshot,
          {
            status: "found",
            config,
          },
        );
      assert.equal(resolved.status, "matched", smtpAbsenceForm.name);
      if (resolved.status !== "matched") {
        throw new Error(`Expected ${smtpAbsenceForm.name} reconciliation`);
      }
      assert.equal(
        resolved.serverMailboxId,
        PRODUCTION_SERVER_MAILBOX_ID,
      );
      assert.equal(resolved.connection.imapConnectionStatus, "connected");
      assert.equal(
        resolved.connection.smtpConnectionStatus,
        "not_configured",
      );
      assert.equal(
        resolved.connection.customSmtp.host,
        "smtp.example.com",
      );
    }

    if (!partialConnection) {
      throw new Error("Expected a rendered incoming-only connection");
    }
    const partialMarkup = renderToStaticMarkup(
      createElement(StepConnectInboxes, {
        selectedInboxes: [PRODUCTION_CUSTOM_INBOX_ID],
        customInboxes: productionState.customInboxes,
        inboxConnections: {
          [PRODUCTION_CUSTOM_INBOX_ID]: {
            ...partialConnection,
            customSmtp: {
              ...productionState.inboxConnections[
                PRODUCTION_CUSTOM_INBOX_ID
              ].customSmtp,
              password: "",
            },
          },
        },
        onProviderChange: () => undefined,
        onEmailChange: () => undefined,
        onCustomImapChange: () => undefined,
        onCustomSmtpChange: () => undefined,
        onReuseCustomImap: () => undefined,
        onConnectInbox: () => undefined,
        onReloadAccountConfig: async () => ({ status: "required" }),
        onApplyAuthoritativeCustomImapConnection: () => undefined,
        customImapAttemptGuard: null,
        onCustomImapAttemptGuardChange: () => undefined,
        onAddInbox: () => undefined,
      }),
    );
    assert.equal(partialMarkup.includes("Incoming connected"), true);
    assert.equal(
      partialMarkup.includes("Outgoing mail not configured"),
      true,
    );
    assert.equal(
      partialMarkup.includes("Connection status could not be confirmed"),
      false,
    );
    assert.equal(
      partialMarkup.includes(
        `data-attempt-control="password-${PRODUCTION_CUSTOM_INBOX_ID}"`,
      ),
      false,
    );
    assert.equal(partialMarkup.includes("mail.example.com"), true);
    assert.equal(partialMarkup.includes("promo@example.com"), true);
    assertMarkupControlEnabled(
      partialMarkup,
      `smtp-host-${PRODUCTION_CUSTOM_INBOX_ID}`,
    );
    assertMarkupControlEnabled(
      partialMarkup,
      `connect-${PRODUCTION_CUSTOM_INBOX_ID}`,
    );
    assert.equal(partialMarkup.includes("Connect outgoing mail"), true);
    assert.equal(partialMarkup.includes(">Connected</button>"), false);
    const partialFlowMarkup = renderToStaticMarkup(
      createElement(OnboardingFlow, {
        state: {
          ...productionState,
          selectedInboxes: [PRODUCTION_CUSTOM_INBOX_ID],
          inboxConnections: {
            ...productionState.inboxConnections,
            [PRODUCTION_CUSTOM_INBOX_ID]: partialConnection,
          },
        },
        currentStep: 2,
        onStepChange: () => undefined,
        onStateChange: () => undefined,
        onSafeStateChange: () => undefined,
        onOpenWorkspace: () => undefined,
      }),
    );
    assert.equal(partialFlowMarkup.includes("Complete setup"), true);
    assertMarkupControlDisabled(partialFlowMarkup, "next");

    const otherDraft = {
      ...productionState.inboxConnections.business,
      provider: "custom_imap" as const,
      email: "draft@example.com",
      customImap: {
        ...productionState.inboxConnections.business.customImap,
        host: "draft.example.com",
        username: "draft@example.com",
      },
    };
    const stateWithOtherDraft: OnboardingState = {
      ...productionState,
      selectedInboxes: [
        ...productionState.selectedInboxes,
        "business",
      ],
      inboxConnections: {
        ...productionState.inboxConnections,
        business: otherDraft,
      },
    };
    const projectedWithOtherDraft =
      accountConfigOrchestration.projectConnectedManagedInboxes(
        stateWithOtherDraft,
        [
          gmailMailbox,
          productionIncomingOnlyManagedInbox(),
          {
            id: "draft-client-only",
            onboardingInboxId: "business",
            email: "draft@example.com",
            provider: "custom_imap",
            connected: false,
            connectionMethod: "imap",
            connectionStatus: "not_connected",
            connectionType: "draft",
          } as any,
        ],
      );
    assert.equal(
      projectedWithOtherDraft.inboxConnections.business,
      otherDraft,
    );
    assert.equal(
      projectedWithOtherDraft.inboxConnections[
        PRODUCTION_CUSTOM_INBOX_ID
      ].serverMailboxId,
      PRODUCTION_SERVER_MAILBOX_ID,
    );
  });

  await test("custom SMTP capability semantics preserve incoming after auth failure and reject invalid partial metadata", async () => {
    const productionState = createProductionIncomingOnlyState();
    const serverSession = incompleteSession(2, productionState);
    const gmailMailbox = productionGmailWithServerMetadata();
    const partialConnection: InboxConnection = {
      ...productionState.inboxConnections[PRODUCTION_CUSTOM_INBOX_ID],
      serverMailboxId: PRODUCTION_SERVER_MAILBOX_ID,
      connected: true,
      connectionStatus: "connected",
      imapConnectionStatus: "connected",
      smtpConnectionStatus: "not_configured",
      fullyConnected: false,
    };
    const reconciliationState: OnboardingState = {
      ...productionState,
      inboxConnections: {
        ...productionState.inboxConnections,
        [PRODUCTION_CUSTOM_INBOX_ID]: partialConnection,
      },
    };
    const snapshot = createCustomImapOnboardingAttemptSnapshot({
      onboardingInboxId: PRODUCTION_CUSTOM_INBOX_ID,
      selectedInboxes: reconciliationState.selectedInboxes,
      connection: partialConnection,
      passwordRevision: 0,
      smtpPasswordRevision: 1,
    });
    const completeSmtp = {
      host: "smtp.example.com",
      port: "587",
      security: "starttls",
      username: "",
      password: "",
      useSameCredentials: true,
    };
    const resolveMailbox = (mailbox: ManagedProjectionInput) =>
      accountConfigOrchestration.resolveCustomImapReadback(
        reconciliationState,
        snapshot,
        {
          status: "found",
          config: {
            onboardingSession: serverSession,
            managedInboxes: [gmailMailbox, mailbox],
          },
        },
      );

    const failedSmtpMailbox = productionIncomingOnlyManagedInbox({
      smtpConnectionStatus: "connection_failed",
      smtpPasswordSet: false,
      fullyConnected: false,
      customSmtp: completeSmtp,
    });
    const failedReadback = resolveMailbox(failedSmtpMailbox);
    assert.equal(failedReadback.status, "matched");
    if (failedReadback.status !== "matched") {
      throw new Error("Expected failed SMTP readback to preserve incoming");
    }
    assert.equal(failedReadback.connection.connected, true);
    assert.equal(
      failedReadback.connection.imapConnectionStatus,
      "connected",
    );
    assert.equal(
      failedReadback.connection.smtpConnectionStatus,
      "connection_failed",
    );
    assert.equal(failedReadback.connection.fullyConnected, false);
    assert.equal(
      areSelectedOnboardingInboxesFullyConnected({
        ...reconciliationState,
        selectedInboxes: [PRODUCTION_CUSTOM_INBOX_ID],
        inboxConnections: {
          ...reconciliationState.inboxConnections,
          [PRODUCTION_CUSTOM_INBOX_ID]:
            failedReadback.connection,
        },
      }),
      false,
    );

    const failedHydration = await hydrateResult(
      {
        status: "found",
        config: {
          onboardingSession: serverSession,
          managedInboxes: [gmailMailbox, failedSmtpMailbox],
        },
      },
      new MemoryStorage(),
    );
    assert.equal(failedHydration.status, "found");
    if (failedHydration.status !== "found") {
      throw new Error("Expected SMTP failure hydration");
    }
    assert.equal(
      failedHydration.accountState.onboardingState.inboxConnections[
        PRODUCTION_CUSTOM_INBOX_ID
      ].imapConnectionStatus,
      "connected",
    );
    assert.equal(
      failedHydration.accountState.onboardingState.inboxConnections[
        PRODUCTION_CUSTOM_INBOX_ID
      ].smtpConnectionStatus,
      "connection_failed",
    );

    const fullMailbox = productionIncomingOnlyManagedInbox({
      smtpConnectionStatus: "connected",
      smtpPasswordSet: true,
      fullyConnected: true,
      customSmtp: completeSmtp,
    });
    const fullReadback = resolveMailbox(fullMailbox);
    assert.equal(fullReadback.status, "matched");
    if (fullReadback.status !== "matched") {
      throw new Error("Expected complete SMTP readback");
    }
    assert.equal(fullReadback.connection.fullyConnected, true);
    const fullyProjected =
      accountConfigOrchestration.projectConnectedManagedInboxes(
        productionState,
        [gmailMailbox, fullMailbox],
      );
    assert.equal(
      areSelectedOnboardingInboxesFullyConnected(fullyProjected),
      true,
    );
    assert.equal(
      fullyProjected.inboxConnections.main.connected,
      true,
    );
    const fullFlowMarkup = renderToStaticMarkup(
      createElement(OnboardingFlow, {
        state: fullyProjected,
        currentStep: 2,
        onStepChange: () => undefined,
        onStateChange: () => undefined,
        onSafeStateChange: () => undefined,
        onOpenWorkspace: () => undefined,
      }),
    );
    assertMarkupControlEnabled(fullFlowMarkup, "next");

    const notConfiguredWithSafeMetadata =
      productionIncomingOnlyManagedInbox({
        smtpConnectionStatus: "not_configured",
        smtpPasswordSet: false,
        fullyConnected: false,
        customSmtp: completeSmtp,
      });
    assert.equal(
      resolveMailbox(notConfiguredWithSafeMetadata).status,
      "matched",
    );

    const invalidMailbox = productionIncomingOnlyManagedInbox({
      smtpConnectionStatus: "not_configured",
      smtpPasswordSet: false,
      fullyConnected: false,
      customSmtp: { host: "smtp.example.com" },
    });
    assert.equal(resolveMailbox(invalidMailbox).status, "required");
    const invalidProjection =
      accountConfigOrchestration.projectConnectedManagedInboxes(
        productionState,
        [gmailMailbox, invalidMailbox],
      );
    assert.equal(
      invalidProjection.inboxConnections[
        PRODUCTION_CUSTOM_INBOX_ID
      ].connected,
      false,
    );
    assert.equal(
      invalidProjection.inboxConnections.main.connected,
      true,
    );

    const mismatchedFailure = productionIncomingOnlyManagedInbox({
      smtpConnectionStatus: "connection_failed",
      smtpPasswordSet: false,
      fullyConnected: false,
      customSmtp: {
        ...completeSmtp,
        host: "smtp.other.example.com",
      },
    });
    assert.equal(resolveMailbox(mismatchedFailure).status, "required");
    assert.equal(
      resolveMailbox(
        productionIncomingOnlyManagedInbox({
          smtpConnectionStatus: "not_configured",
          smtpPasswordSet: false,
          fullyConnected: false,
          customSmtp: {
            ...completeSmtp,
            host: "smtp.other.example.com",
          },
        }),
      ).status,
      "required",
    );
  });

  await test("production config GET and resolver compose with the same bounded signal", async () => {
    const previousFetch = globalThis.fetch;
    const controller = new AbortController();
    let capturedRequest:
      | { url: RequestInfo | URL; init?: RequestInit }
      | null = null;
    const serverConfig: UserAccountConfig = {
      onboardingSession: incompleteSession(
        2,
        customImapSelectedState,
      ),
      managedInboxes: [connectedCustomImapManagedInbox()],
    };
    globalThis.fetch = (async (url, init) => {
      capturedRequest = { url, init };
      return {
        status: 200,
        json: async () => ({
          ok: true,
          configState: "found",
          config: serverConfig,
        }),
      } as Response;
    }) as typeof fetch;

    try {
      const loaded = await loadUserAccountConfig(controller.signal);
      const resolved =
        accountConfigOrchestration.resolveCustomImapReadback(
          customImapSelectedState,
          customImapAttemptSnapshot,
          loaded,
        );
      assert.equal(capturedRequest?.url, "/api/user/config");
      assert.equal(capturedRequest?.init?.method, "GET");
      assert.equal(
        capturedRequest?.init?.signal,
        controller.signal,
      );
      assert.equal(resolved.status, "matched");
      if (resolved.status !== "matched") {
        throw new Error("Expected production GET to resolve a match");
      }
      assert.equal(resolved.serverMailboxId, "imap-server-1");
    } finally {
      globalThis.fetch = previousFetch;
    }
  });

  await test("server custom IMAP projection survives refresh and a clean profile", async () => {
    const serverConfig: UserAccountConfig = {
      onboardingSession: incompleteSession(2, customImapSelectedState),
      managedInboxes: [connectedCustomImapManagedInbox()],
    };
    const browserAStorage = new MemoryStorage();
    const firstHydration = await hydrateResult(
      { status: "found", config: serverConfig },
      browserAStorage,
    );
    const refreshedHydration = await hydrateResult(
      { status: "found", config: serverConfig },
      browserAStorage,
    );
    const browserBHydration = await hydrateResult(
      { status: "found", config: serverConfig },
      new MemoryStorage(),
    );

    for (const outcome of [
      firstHydration,
      refreshedHydration,
      browserBHydration,
    ]) {
      assert.equal(outcome.status, "found");
      if (outcome.status !== "found") throw new Error("Expected found outcome");
      assert.equal(outcome.accountState.view, "onboarding");
      assert.equal(outcome.accountState.onboardingStep, 2);
      assert.equal(outcome.accountState.persistedOnboardingSession?.completed, false);
      assert.equal(
        outcome.accountState.onboardingState.inboxConnections.demo.connected,
        true,
      );
      assert.equal(
        outcome.accountState.onboardingState.inboxConnections.demo
          .serverMailboxId,
        "imap-server-1",
      );
      assert.deepEqual(
        outcome.accountState.onboardingState.selectedInboxes,
        customImapSelectedState.selectedInboxes,
      );
      assert.equal(
        outcome.accountState.onboardingState.inboxCount,
        customImapSelectedState.inboxCount,
      );
      assert.deepEqual(
        outcome.accountState.onboardingState.customInboxes,
        customImapSelectedState.customInboxes,
      );
    }

    const dynamicInboxId = "custom:partner-inbox";
    const dynamicState: OnboardingState = {
      ...customImapSelectedState,
      primaryInbox: dynamicInboxId,
      inboxCount: "1",
      selectedInboxes: [dynamicInboxId],
      customInboxes: [
        { id: dynamicInboxId, name: "Partner inbox" },
      ],
      inboxConnections: {
        ...customImapSelectedState.inboxConnections,
        [dynamicInboxId]: {
          ...customImapSelectedState.inboxConnections.demo,
          email: "partner@example.com",
          customImap: {
            ...customImapSelectedState.inboxConnections.demo.customImap,
            username: "partner@example.com",
          },
        },
      },
    };
    const dynamicHydration = await hydrateResult(
      {
        status: "found",
        config: {
          onboardingSession: incompleteSession(2, dynamicState),
          managedInboxes: [
            connectedCustomImapManagedInbox({
              id: "imap-server-dynamic",
              onboardingInboxId: dynamicInboxId,
              email: "partner@example.com",
              customImap: {
                host: "imap.example.com",
                port: "993",
                ssl: true,
                username: "partner@example.com",
                password: "",
              },
            }),
          ],
        },
      },
      new MemoryStorage(),
    );
    assert.equal(dynamicHydration.status, "found");
    if (dynamicHydration.status !== "found") {
      throw new Error("Expected dynamic inbox hydration");
    }
    const dynamicConnection =
      dynamicHydration.accountState.onboardingState.inboxConnections[
        dynamicInboxId
      ];
    assert.equal(dynamicConnection.serverMailboxId, "imap-server-dynamic");
    assert.equal(dynamicConnection.imapConnectionStatus, "connected");
    assert.equal(
      dynamicConnection.smtpConnectionStatus,
      "not_configured",
    );
    assert.equal(dynamicConnection.fullyConnected, false);
    assert.equal(
      areSelectedOnboardingInboxesFullyConnected(
        dynamicHydration.accountState.onboardingState,
      ),
      false,
    );

    const fullSmtpHydration = await hydrateResult(
      {
        status: "found",
        config: {
          ...serverConfig,
          managedInboxes: [
            connectedCustomImapManagedInbox({
              smtpConnectionStatus: "connected",
              fullyConnected: true,
              customSmtp: {
                host: "smtp.example.com",
                port: "587",
                security: "starttls",
                username: "",
                password: "",
                useSameCredentials: true,
              },
            }),
          ],
        },
      },
      new MemoryStorage(),
    );
    assert.equal(fullSmtpHydration.status, "found");
    if (fullSmtpHydration.status !== "found") {
      throw new Error("Expected full SMTP hydration");
    }
    assert.equal(
      fullSmtpHydration.accountState.onboardingState.inboxConnections.demo
        .connected,
      true,
    );
    assert.equal(
      fullSmtpHydration.accountState.onboardingState.inboxConnections.demo
        .serverMailboxId,
      "imap-server-1",
    );
    assert.deepEqual(
      fullSmtpHydration.accountState.onboardingState.inboxConnections.demo
        .customSmtp,
      {
        host: "smtp.example.com",
        port: "587",
        security: "starttls",
        username: "",
        password: "",
        useSameCredentials: true,
      },
    );

    for (const [malformedMailbox, expectedConnected] of [
      [
        connectedCustomImapManagedInbox({ connectionMessage: {} } as any),
        false,
      ],
      [
        connectedCustomImapManagedInbox({
          id: "imap-server-2",
          onboardingInboxId: undefined,
          email: "other@example.com",
          provider: "evil",
        } as any),
        true,
      ],
    ] as const) {
      const malformedHydration = await hydrateResult(
        {
          status: "found",
          config: {
            ...serverConfig,
            managedInboxes: [
              connectedCustomImapManagedInbox(),
              malformedMailbox,
            ],
          },
        },
        new MemoryStorage(),
      );
      assert.equal(malformedHydration.status, "found");
      if (malformedHydration.status !== "found") {
        throw new Error("Expected fail-closed hydration");
      }
      assert.equal(
        malformedHydration.accountState.onboardingState.inboxConnections.demo
          .connected,
        expectedConnected,
      );
    }

    const sensitiveStorage = new MemoryStorage();
    const sensitiveHydration = await hydrateResult(
      {
        status: "found",
        config: {
          ...serverConfig,
          managedInboxes: [
            connectedCustomImapManagedInbox({
              clientSecret: "client-secret-value",
              credentialGeneration: 7,
              authorizationBearer: "bearer-value",
              privateKey: "private-key-value",
              apiKey: "api-key-value",
              encryptedValue: "encrypted-value",
              messages: [
                {
                  id: "cached-message",
                  body: "cached-message-body",
                },
              ],
              customImap: {
                host: "imap.example.com",
                port: "993",
                ssl: true,
                username: "verified.imap@example.com",
                password: "imap-secret-value",
                privateKey: "nested-private-key-value",
              },
              customSmtp: {
                password: "",
                apiKey: "nested-api-key-value",
              },
            } as any),
          ],
        },
      },
      sensitiveStorage,
    );
    assert.equal(sensitiveHydration.status, "found");
    if (sensitiveHydration.status !== "found") {
      throw new Error("Expected sensitive hydration result");
    }
    assert.equal(
      sensitiveHydration.accountState.onboardingState.inboxConnections.demo
        .connected,
      false,
    );
    const storedManagedInboxes =
      sensitiveStorage.snapshot()[MANAGED_INBOXES_KEY] ?? "";
    for (const forbidden of [
      "clientSecret",
      "client-secret-value",
      "credentialGeneration",
      "authorizationBearer",
      "bearer-value",
      "privateKey",
      "private-key-value",
      "apiKey",
      "api-key-value",
      "encryptedValue",
      "encrypted-value",
      "nested-private-key-value",
      "nested-api-key-value",
      "messages",
      "cached-message-body",
      "imap-secret-value",
    ]) {
      assert.equal(storedManagedInboxes.includes(forbidden), false);
    }
  });

  await test("production Gmail hint validation is optional and has no synthetic failures", () => {
    const gmailConnection = {
      ...cleanState.inboxConnections.main,
      provider: "google" as const,
      email: "",
      connected: false,
      connectionMethod: "oauth" as const,
      connectionStatus: "oauth_required" as const,
      connectionMessage: null,
      oauthAuthorizationUrl: null,
    };

    assert.equal(isConnectionReady(gmailConnection), true);
    assert.equal(getConnectionFeedback(gmailConnection), null);

    const legitimateServerAddress = {
      ...gmailConnection,
      email: "server@gmail.com",
    };
    assert.equal(isConnectionReady(legitimateServerAddress), true);
    assert.equal(getConnectionFeedback(legitimateServerAddress), null);

    const malformedHint = {
      ...gmailConnection,
      email: "not-an-email",
    };
    assert.equal(isConnectionReady(malformedHint), true);
    const malformedFeedback = getConnectionFeedback(malformedHint);
    assert.equal(typeof malformedFeedback?.email, "string");
    assert.equal(malformedFeedback?.general, undefined);
  });

  await test("production Step Gmail start sends only the safe OAuth correlation payload", async () => {
    const previousFetch = globalThis.fetch;
    let capturedRequest: { url: string; init?: RequestInit } | null = null;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      capturedRequest = { url, init };
      return {
        ok: true,
        text: async () =>
          JSON.stringify({
            ok: true,
            connectionStatus: "waiting_for_authentication",
            authorizationUrl: "  https://accounts.google.test/authorize  ",
            message: "Continue with Google.",
          }),
      } as Response;
    }) as typeof fetch;

    try {
      assert.deepEqual(
        inboxConnectionApi.buildOAuthInboxRequest({
          provider: "google",
          email: "   ",
          inboxPosition: "main",
        }),
        { provider: "google", inboxPosition: "main" },
      );

      const productionStepOptions = buildOnboardingInboxConnectionOptions({
        inboxId: "main",
        connection: {
          ...cleanState.inboxConnections.main,
          provider: "google",
          email: "  hinted.account@gmail.com  ",
          connected: false,
          connectionMethod: "oauth",
          connectionStatus: "oauth_required",
          connectionMessage: null,
          oauthAuthorizationUrl: null,
        },
        internalRole: "label_manager",
        focusPreferences: {
          demos: "low",
          promo: "medium",
          finance: "medium",
          legal: "medium",
          business: "medium",
          updates: "medium",
          distribution: "medium",
          royalties: "medium",
          promoReminders: "medium",
          paymentReminders: "medium",
        },
        selectedInboxes: ["main", "demo"],
      });
      assert.equal(productionStepOptions.inboxPosition, "main");

      const startResult = await inboxConnectionApi.beginInboxConnection(
        productionStepOptions,
      );
      assert.notEqual(capturedRequest, null);
      assert.equal(capturedRequest!.url, "/api/inboxes/connect-oauth");
      assert.equal(capturedRequest!.init?.method, "POST");
      assert.equal(capturedRequest!.init?.credentials, "include");
      assert.deepEqual(JSON.parse(String(capturedRequest!.init?.body)), {
        provider: "google",
        email: "hinted.account@gmail.com",
        inboxPosition: "main",
      });
      assert.deepEqual(
        Object.keys(JSON.parse(String(capturedRequest!.init?.body))).sort(),
        ["email", "inboxPosition", "provider"],
      );
      assert.equal(startResult.ok, true);
      assert.equal(startResult.connected, false);
      assert.equal(startResult.connectionStatus, "waiting_for_authentication");
      assert.equal(
        startResult.oauthAuthorizationUrl,
        "https://accounts.google.test/authorize",
      );
      assert.deepEqual(buildSuccessfulOnboardingConnectionUpdate(startResult), {
        connected: false,
        connectionMethod: "oauth",
        connectionStatus: "waiting_for_authentication",
        connectionMessage: "Continue with Google.",
        oauthAuthorizationUrl: null,
      });

      globalThis.fetch = (async () => ({
        ok: true,
        text: async () =>
          JSON.stringify({
            ok: true,
            connectionStatus: "connected",
            authorizationUrl: "https://accounts.google.test/authorize",
          }),
      })) as typeof fetch;
      const unsafeConnectedStart = await inboxConnectionApi.beginInboxConnection(
        productionStepOptions,
      );
      assert.equal(unsafeConnectedStart.ok, false);
      assert.equal(unsafeConnectedStart.connected, false);
      assert.equal(unsafeConnectedStart.connectionStatus, "connection_failed");
      assert.equal(unsafeConnectedStart.oauthAuthorizationUrl, null);
      assert.equal(
        unsafeConnectedStart.error?.code,
        "oauth_invalid_start_response",
      );
    } finally {
      globalThis.fetch = previousFetch;
    }
  });

  await test("Google OAuth callback metadata is consumed only as a one-shot reload signal", () => {
    const callbackSignal = {
      status: "success",
      provider: "google",
      inboxPosition: "main",
      email: "verified.account@gmail.com",
      mailboxId: "managed-google-1",
      message: "Gmail connected.",
    };
    const localManagedMirror = JSON.stringify([
      { id: "local-only", email: "hinted.account@gmail.com", connected: false },
    ]);
    const storage = new MemoryStorage({
      [OAUTH_CALLBACK_RESULT_KEY]: JSON.stringify(callbackSignal),
      [MANAGED_INBOXES_KEY]: localManagedMirror,
    });
    const onboardingBefore = JSON.stringify(selectedChoiceState);
    let reloadRequests = 0;

    assert.equal(
      accountConfigOrchestration.processGoogleOAuthCallbackSignal(storage, () => {
        reloadRequests += 1;
      }),
      true,
    );
    assert.equal(reloadRequests, 1);
    assert.equal(storage.getItem(OAUTH_CALLBACK_RESULT_KEY), null);
    assert.equal(storage.getItem(MANAGED_INBOXES_KEY), localManagedMirror);
    assert.equal(JSON.stringify(selectedChoiceState), onboardingBefore);
    assert.deepEqual(storage.mutations, [
      { type: "remove", key: OAUTH_CALLBACK_RESULT_KEY },
    ]);

    const rejectedSignals = [
      {
        status: "error",
        provider: "google",
        inboxPosition: "main",
        email: "verified.account@gmail.com",
        mailboxId: "managed-google-1",
        message: "Registration failed.",
      },
      {
        ...callbackSignal,
        accessToken: "must-never-be-accepted",
      },
      {
        ...callbackSignal,
        state: "must-never-be-accepted",
      },
    ];

    for (const rejectedSignal of rejectedSignals) {
      const rejectedStorage = new MemoryStorage({
        [OAUTH_CALLBACK_RESULT_KEY]: JSON.stringify(rejectedSignal),
      });
      let rejectedReloadRequests = 0;
      assert.equal(
        accountConfigOrchestration.processGoogleOAuthCallbackSignal(
          rejectedStorage,
          () => {
            rejectedReloadRequests += 1;
          },
        ),
        false,
      );
      assert.equal(rejectedReloadRequests, 0);
      assert.equal(rejectedStorage.getItem(OAUTH_CALLBACK_RESULT_KEY), null);
      assert.deepEqual(rejectedStorage.mutations, [
        { type: "remove", key: OAUTH_CALLBACK_RESULT_KEY },
      ]);
    }

    const malformedStorage = new MemoryStorage({
      [OAUTH_CALLBACK_RESULT_KEY]: "{not-json",
    });
    let malformedReloadRequests = 0;
    assert.equal(
      accountConfigOrchestration.processGoogleOAuthCallbackSignal(
        malformedStorage,
        () => {
          malformedReloadRequests += 1;
        },
      ),
      false,
    );
    assert.equal(malformedReloadRequests, 0);
    assert.equal(malformedStorage.getItem(OAUTH_CALLBACK_RESULT_KEY), null);
  });

  await test("only one matching authoritative Google mailbox projects connected", () => {
    const hintedState: OnboardingState = {
      ...selectedChoiceState,
      inboxConnections: {
        ...selectedChoiceState.inboxConnections,
        main: {
          ...selectedChoiceState.inboxConnections.main,
          provider: "google",
          email: "hinted.account@gmail.com",
          connected: false,
          connectionMethod: "oauth",
          connectionStatus: "waiting_for_authentication",
          connectionMessage: "Waiting for Google.",
          oauthAuthorizationUrl: null,
        },
      },
    };
    const verifiedMailbox = connectedGoogleManagedInbox();
    const projected = accountConfigOrchestration.projectConnectedManagedInboxes(
      hintedState,
      [verifiedMailbox],
    );

    assert.equal(hintedState.inboxConnections.main.connected, false);
    assert.equal(hintedState.inboxConnections.main.email, "hinted.account@gmail.com");
    assert.equal(projected.inboxConnections.main.connected, true);
    assert.equal(projected.inboxConnections.main.connectionStatus, "connected");
    assert.equal(projected.inboxConnections.main.connectionMethod, "oauth");
    assert.equal(projected.inboxConnections.main.provider, "google");
    assert.equal(
      projected.inboxConnections.main.email,
      "verified.account@gmail.com",
      "the Google-verified server identity must replace the browser hint",
    );
    assert.equal(projected.inboxConnections.main.oauthAuthorizationUrl, null);

    const empty = accountConfigOrchestration.projectConnectedManagedInboxes(
      hintedState,
      [],
    );
    assert.equal(empty.inboxConnections.main.connected, false);

    const unmatched = accountConfigOrchestration.projectConnectedManagedInboxes(
      hintedState,
      [connectedGoogleManagedInbox({ onboardingInboxId: "demo" })],
    );
    assert.equal(unmatched.inboxConnections.main.connected, false);
    assert.equal(unmatched.inboxConnections.main.email, "hinted.account@gmail.com");

    const ambiguous = accountConfigOrchestration.projectConnectedManagedInboxes(
      hintedState,
      [
        connectedGoogleManagedInbox(),
        connectedGoogleManagedInbox({
          id: "managed-google-2",
          email: "second.verified@gmail.com",
        }),
      ],
    );
    assert.equal(ambiguous.inboxConnections.main.connected, false);

    const unsafeShapes: ManagedProjectionInput[] = [
      connectedGoogleManagedInbox({ connected: false }),
      connectedGoogleManagedInbox({ connectionStatus: "connection_failed" }),
      connectedGoogleManagedInbox({ connectionMethod: "imap" }),
      connectedGoogleManagedInbox({ provider: "microsoft" }),
      connectedGoogleManagedInbox({ id: "" }),
      connectedGoogleManagedInbox({ email: "not-an-email" }),
    ];
    for (const mailbox of unsafeShapes) {
      const unsafeProjection =
        accountConfigOrchestration.projectConnectedManagedInboxes(
          hintedState,
          [mailbox],
        );
      assert.equal(unsafeProjection.inboxConnections.main.connected, false);
    }

    const safeSession = accountConfigOrchestration.createIncompleteSession(projected, 2);
    assert.equal(safeSession.completed, false);
    const serializedSession = JSON.stringify(safeSession);
    assert.equal(serializedSession.includes("verified.account@gmail.com"), false);
    assert.equal(serializedSession.includes("managed-google-1"), false);
    assert.equal(serializedSession.includes("connected"), false);
  });

  await test("server Gmail projection survives refresh and a second clean profile", async () => {
    const serverSession = incompleteSession(2, selectedChoiceState);
    const serverConfig: UserAccountConfig = {
      onboardingSession: serverSession,
      managedInboxes: [connectedGoogleManagedInbox()],
    };
    const browserAStorage = new MemoryStorage();
    const firstHydration = await hydrateResult(
      { status: "found", config: serverConfig },
      browserAStorage,
    );
    const refreshedHydration = await hydrateResult(
      { status: "found", config: serverConfig },
      browserAStorage,
    );
    const browserBStorage = new MemoryStorage();
    const browserBHydration = await hydrateResult(
      { status: "found", config: serverConfig },
      browserBStorage,
      false,
      () => undefined,
      SECOND_ACCOUNT_KEY,
    );

    for (const outcome of [
      firstHydration,
      refreshedHydration,
      browserBHydration,
    ]) {
      assert.equal(outcome.status, "found");
      if (outcome.status !== "found") {
        throw new Error("Expected authoritative Gmail config hydration");
      }
      assert.equal(outcome.accountState.view, "onboarding");
      assert.equal(outcome.accountState.userConfig, null);
      assert.equal(outcome.accountState.persistedOnboardingSession?.completed, false);
      assert.equal(outcome.accountState.onboardingStep, 2);
      assert.equal(outcome.accountState.onboardingState.inboxConnections.main.connected, true);
      assert.equal(
        outcome.accountState.onboardingState.inboxConnections.main.email,
        "verified.account@gmail.com",
      );
      assert.deepEqual(
        accountConfigOrchestration.projectChoices(
          outcome.accountState.onboardingState,
        ),
        serverSession.choices,
      );
    }

    assert.equal(
      JSON.parse(browserBStorage.getItem(MANAGED_INBOXES_KEY) ?? "[]")[0]
        .onboardingInboxId,
      "main",
    );
    assert.equal(
      accountConfigOrchestration.canOpenWorkspaceWithoutServerCompletion("member"),
      false,
    );

    const failureStorage = new MemoryStorage({
      [OAUTH_CALLBACK_RESULT_KEY]: JSON.stringify({
        status: "success",
        provider: "google",
        inboxPosition: "main",
        email: "verified.account@gmail.com",
        mailboxId: "managed-google-1",
        message: "Gmail connected.",
      }),
    });
    let failureReloadRequests = 0;
    assert.equal(
      accountConfigOrchestration.processGoogleOAuthCallbackSignal(
        failureStorage,
        () => {
          failureReloadRequests += 1;
        },
      ),
      true,
    );
    assert.equal(failureReloadRequests, 1);
    const failedReload = await hydrateResult(
      {
        status: "network_error",
        error: { code: "network_error", message: "offline" },
      },
      failureStorage,
    );
    assert.deepEqual(failedReload, {
      status: "error",
      errorStatus: "network_error",
    });
    assert.deepEqual(failureStorage.mutations, [
      { type: "remove", key: OAUTH_CALLBACK_RESULT_KEY },
    ]);
    assert.equal(failureStorage.getItem(MANAGED_INBOXES_KEY), null);
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

  await test("save queue retries CAS conflicts boundedly and newer state supersedes a pending retry", async () => {
    const scheduled: Array<{
      handle: number;
      callback: () => void;
      delayMs: number;
      cancelled: boolean;
    }> = [];
    let nextHandle = 1;
    const scheduleRetry = (callback: () => void, delayMs: number) => {
      const handle = nextHandle;
      nextHandle += 1;
      scheduled.push({ handle, callback, delayMs, cancelled: false });
      return handle;
    };
    const cancelRetry = (handle: number) => {
      const pending = scheduled.find((entry) => entry.handle === handle);
      if (pending) pending.cancelled = true;
    };
    const conflictResult: UserAccountConfigSaveResult = {
      status: "conflict",
      error: {
        code: "user_config_write_conflict",
        message: "retry",
      },
    };
    const alwaysConflictingConfigs: UserAccountConfig[] = [];
    const boundedQueue = accountConfigOrchestration.createSaveQueue({
      save: async (config) => {
        alwaysConflictingConfigs.push(config);
        return conflictResult;
      },
      scheduleRetry,
      cancelRetry,
    });
    boundedQueue.reset(ACCOUNT_KEY);
    boundedQueue.markDirty(ACCOUNT_KEY);
    boundedQueue.enqueue({
      accountKey: ACCOUNT_KEY,
      config: { onboardingSession: incompleteSession(1) },
    });
    await flushAsyncWork();
    assert.equal(alwaysConflictingConfigs.length, 1);
    assert.equal(scheduled[0].delayMs, 120);

    scheduled[0].callback();
    await flushAsyncWork();
    assert.equal(alwaysConflictingConfigs.length, 2);
    assert.equal(scheduled[1].delayMs, 320);

    const callsBeforeCancelledRetry = alwaysConflictingConfigs.length;
    boundedQueue.cancel();
    assert.equal(scheduled[1].cancelled, true);
    scheduled[1].callback();
    await flushAsyncWork();
    assert.equal(
      alwaysConflictingConfigs.length,
      callsBeforeCancelledRetry,
      "unmount cleanup must prevent a scheduled conflict retry",
    );
    assert.equal(
      scheduled.length,
      2,
      "the queue must stop after two automatic conflict retries",
    );
    assert.equal(boundedQueue.isDirty(ACCOUNT_KEY), false);

    const exhaustedQueue = accountConfigOrchestration.createSaveQueue({
      save: async (config) => {
        alwaysConflictingConfigs.push(config);
        return conflictResult;
      },
      scheduleRetry,
      cancelRetry,
    });
    const callsBeforeExhaustionCheck = alwaysConflictingConfigs.length;
    exhaustedQueue.reset(ACCOUNT_KEY);
    exhaustedQueue.markDirty(ACCOUNT_KEY);
    exhaustedQueue.enqueue({
      accountKey: ACCOUNT_KEY,
      config: { onboardingSession: incompleteSession(1) },
    });
    await flushAsyncWork();
    scheduled[2].callback();
    await flushAsyncWork();
    scheduled[3].callback();
    await flushAsyncWork();
    assert.equal(scheduled.length, 4);
    assert.equal(
      alwaysConflictingConfigs.length - callsBeforeExhaustionCheck,
      3,
    );
    assert.equal(exhaustedQueue.isDirty(ACCOUNT_KEY), true);

    const supersedingScheduled: typeof scheduled = [];
    const savedConfigs: UserAccountConfig[] = [];
    const supersedingQueue = accountConfigOrchestration.createSaveQueue({
      save: async (config) => {
        savedConfigs.push(config);
        return savedConfigs.length === 1
          ? conflictResult
          : { status: "found", config };
      },
      scheduleRetry: (callback, delayMs) => {
        const handle = nextHandle;
        nextHandle += 1;
        supersedingScheduled.push({
          handle,
          callback,
          delayMs,
          cancelled: false,
        });
        return handle;
      },
      cancelRetry: (handle) => {
        const pending = supersedingScheduled.find(
          (entry) => entry.handle === handle,
        );
        if (pending) pending.cancelled = true;
      },
    });
    supersedingQueue.reset(ACCOUNT_KEY);
    supersedingQueue.markDirty(ACCOUNT_KEY);
    supersedingQueue.enqueue({
      accountKey: ACCOUNT_KEY,
      config: { onboardingSession: incompleteSession(1) },
    });
    await flushAsyncWork();
    assert.equal(supersedingScheduled.length, 1);

    supersedingQueue.markDirty(ACCOUNT_KEY);
    assert.equal(supersedingScheduled[0].cancelled, true);
    supersedingScheduled[0].callback();
    await flushAsyncWork();
    assert.equal(
      savedConfigs.length,
      1,
      "a dirty newer revision must suppress the older scheduled retry before debounce enqueue",
    );
    supersedingQueue.enqueue({
      accountKey: ACCOUNT_KEY,
      config: { onboardingSession: incompleteSession(3) },
    });
    await flushAsyncWork();
    assert.equal(savedConfigs.length, 2);
    assert.equal(
      (savedConfigs[1].onboardingSession as OnboardingSessionV1).currentStep,
      3,
    );
    assert.equal(JSON.stringify(savedConfigs).includes("password"), false);
    assert.equal(supersedingQueue.isDirty(ACCOUNT_KEY), false);
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
