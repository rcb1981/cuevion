import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import "sucrase/register/tsx.js";

const {
  beginInboxConnection,
  buildOAuthInboxRequest,
  GMAIL_OAUTH_RECONNECT_REQUIRED_CONNECTION_MESSAGE,
} = require(resolve(
  process.cwd(),
  "src/lib/inboxConnectionApi.ts",
)) as typeof import("./lib/inboxConnectionApi");
const { accountConfigOrchestration } = require(resolve(
  process.cwd(),
  "src/App.tsx",
)) as typeof import("./App");
const { createInboxConnection, initialOnboardingState } = require(resolve(
  process.cwd(),
  "src/data/onboardingOptions.ts",
)) as typeof import("./data/onboardingOptions");
const { ONBOARDING_STEP_MAX } = require(resolve(
  process.cwd(),
  "src/types/onboarding.ts",
)) as typeof import("./types/onboarding");

type MemoryStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function createMemoryStorage(initial: Record<string, string> = {}): MemoryStorage {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      values.set(key, value);
    },
    removeItem(key) {
      values.delete(key);
    },
  };
}

async function run() {
  assert.deepEqual(
    buildOAuthInboxRequest({
      provider: "google",
      email: " Owner@Example.com ",
      mode: "initial",
      mailboxId: "draft-mailbox",
      inboxPosition: "main",
    }),
    {
      provider: "google",
      mode: "initial",
      email: "Owner@Example.com",
      inboxPosition: "main",
    },
    "first-time Gmail OAuth must not claim an existing mailbox target",
  );
  assert.deepEqual(
    buildOAuthInboxRequest({
      provider: "google",
      email: " owner@example.com ",
      mode: "reconnect",
      mailboxId: " gmail-owner ",
      inboxPosition: "main",
    }),
    {
      provider: "google",
      mode: "reconnect",
      mailboxId: "gmail-owner",
      email: "owner@example.com",
    },
    "Gmail reconnect must carry only its exact stable mailbox ID, not onboarding position",
  );
  assert.throws(
    () =>
      buildOAuthInboxRequest({
        provider: "google",
        email: "owner@example.com",
        mode: "reconnect",
      }),
    /mailbox ID is required/i,
  );
  assert.deepEqual(
    buildOAuthInboxRequest({
      provider: "microsoft",
      email: " owner@example.com ",
      mode: "reconnect",
      mailboxId: "microsoft-owner",
    }),
    { provider: "microsoft", email: "owner@example.com" },
    "the Microsoft OAuth request contract must remain unchanged",
  );

  const originalFetch = globalThis.fetch;
  let capturedBody: Record<string, unknown> | null = null;
  globalThis.fetch = (async (_url: string, init?: RequestInit) => {
    capturedBody = JSON.parse(String(init?.body ?? "{}"));
    return {
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          connectionMethod: "oauth",
          connectionStatus: "waiting_for_authentication",
          authorizationUrl: "https://accounts.google.test/authorize",
          message: "Continue with Google.",
        }),
    } as Response;
  }) as typeof fetch;
  try {
    const result = await beginInboxConnection({
      imapMode: "reconnect",
      mailboxId: "gmail-owner",
      provider: "google",
      email: "owner@example.com",
      customImap: {
        host: "",
        port: "",
        ssl: true,
        username: "",
        password: "",
      },
    });
    assert.equal(result.ok, true);
    assert.deepEqual(capturedBody, {
      provider: "google",
      mode: "reconnect",
      mailboxId: "gmail-owner",
      email: "owner@example.com",
    });
    for (const forbidden of [
      "accessToken",
      "access_token",
      "refreshToken",
      "refresh_token",
      "password",
      "code",
      "codeVerifier",
      "code_verifier",
      "authorizationUrl",
    ]) {
      assert.equal(forbidden in (capturedBody ?? {}), false);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }

  const callbackKey = "cuevion-oauth-callback-result";
  for (const signal of [
    {
      status: "success",
      provider: "google",
      mode: "reconnect",
      email: "owner@example.com",
      mailboxId: "gmail-owner",
      message: "Gmail reconnected.",
    },
    {
      status: "error",
      provider: "google",
      mode: "reconnect",
      email: "owner@example.com",
      mailboxId: "gmail-owner",
      message: "Please reconnect using the Google account for owner@example.com.",
    },
  ]) {
    const storage = createMemoryStorage({
      [callbackKey]: JSON.stringify(signal),
    });
    let reloads = 0;
    assert.equal(
      accountConfigOrchestration.processGoogleOAuthCallbackSignal(
        storage,
        () => {
          reloads += 1;
        },
      ),
      true,
    );
    assert.equal(reloads, 1);
    assert.equal(storage.getItem(callbackKey), null);
  }

  const secretBearingSignal = createMemoryStorage({
    [callbackKey]: JSON.stringify({
      status: "success",
      provider: "google",
      mode: "reconnect",
      email: "owner@example.com",
      mailboxId: "gmail-owner",
      message: "Gmail reconnected.",
      refresh_token: "must-never-be-accepted",
    }),
  });
  let secretBearingReloads = 0;
  assert.equal(
    accountConfigOrchestration.processGoogleOAuthCallbackSignal(
      secretBearingSignal,
      () => {
        secretBearingReloads += 1;
      },
    ),
    false,
  );
  assert.equal(secretBearingReloads, 0);
  assert.equal(secretBearingSignal.getItem(callbackKey), null);

  const secretBearingErrorSignal = createMemoryStorage({
    [callbackKey]: JSON.stringify({
      status: "error",
      provider: "google",
      mode: "reconnect",
      email: "owner@example.com",
      mailboxId: "gmail-owner",
      message: "Reconnect failed.",
      access_token: "must-never-be-accepted",
    }),
  });
  let secretBearingErrorReloads = 0;
  assert.equal(
    accountConfigOrchestration.processGoogleOAuthCallbackSignal(
      secretBearingErrorSignal,
      () => {
        secretBearingErrorReloads += 1;
      },
    ),
    false,
  );
  assert.equal(secretBearingErrorReloads, 0);

  const hydrationState = {
    ...initialOnboardingState,
    selectedInboxes: ["main"],
    inboxConnections: {
      ...initialOnboardingState.inboxConnections,
      main: {
        ...createInboxConnection(),
        provider: "google",
        email: "owner@example.com",
      },
    },
  };
  const reconnectRequiredProjection =
    accountConfigOrchestration.projectConnectedManagedInboxes(
      hydrationState,
      [
        {
          id: "gmail-owner",
          onboardingInboxId: "main",
          email: "owner@example.com",
          provider: "google",
          connectionMethod: "oauth",
          connected: false,
          connectionStatus: "connection_failed",
          connectionMessage:
            GMAIL_OAUTH_RECONNECT_REQUIRED_CONNECTION_MESSAGE,
        },
      ],
    );
  assert.deepEqual(
    {
      serverMailboxId:
        reconnectRequiredProjection.inboxConnections.main?.serverMailboxId,
      provider: reconnectRequiredProjection.inboxConnections.main?.provider,
      email: reconnectRequiredProjection.inboxConnections.main?.email,
      connected: reconnectRequiredProjection.inboxConnections.main?.connected,
      connectionStatus:
        reconnectRequiredProjection.inboxConnections.main?.connectionStatus,
      connectionMessage:
        reconnectRequiredProjection.inboxConnections.main?.connectionMessage,
    },
    {
      serverMailboxId: "gmail-owner",
      provider: "google",
      email: "owner@example.com",
      connected: false,
      connectionStatus: "connection_failed",
      connectionMessage:
        GMAIL_OAUTH_RECONNECT_REQUIRED_CONNECTION_MESSAGE,
    },
    "authoritative hydration must preserve the exact reconnect-required Gmail state",
  );
  const genericFailureProjection =
    accountConfigOrchestration.projectConnectedManagedInboxes(
      hydrationState,
      [
        {
          id: "gmail-owner",
          onboardingInboxId: "main",
          email: "owner@example.com",
          provider: "google",
          connectionMethod: "oauth",
          connected: false,
          connectionStatus: "connection_failed",
          connectionMessage: "A generic mailbox error occurred.",
        },
      ],
    );
  assert.notEqual(
    genericFailureProjection.inboxConnections.main?.serverMailboxId,
    "gmail-owner",
    "generic Gmail failures must not acquire reconnect-required UI authority",
  );

  const authoritativeHydrationStorage = createMemoryStorage();
  const authoritativeHydrator =
    accountConfigOrchestration.createHydrator(async () => ({
      status: "found" as const,
      config: {
        v: 1,
        email: "owner@example.com",
        onboardingSession: {
          schemaVersion: 1,
          currentStep: ONBOARDING_STEP_MAX,
          completed: true,
          choices: accountConfigOrchestration.projectChoices(hydrationState),
        },
        managedInboxes: [
          {
            id: "gmail-owner",
            onboardingInboxId: "main",
            title: "Owner",
            email: "owner@example.com",
            provider: "google",
            connectionMethod: "oauth",
            connected: true,
            connectionStatus: "connected",
          },
          {
            id: "gmail-settings-added",
            title: "Settings added",
            email: "settings@example.com",
            provider: "google",
            connectionMethod: "oauth",
            connected: false,
            connectionStatus: "connection_failed",
            connectionMessage:
              GMAIL_OAUTH_RECONNECT_REQUIRED_CONNECTION_MESSAGE,
          },
        ],
      },
    }));
  const authoritativeOutcome = await authoritativeHydrator.hydrate({
    accountStorageOwnerKey: "owner@example.com",
    storage: authoritativeHydrationStorage,
    resetOnboarding: false,
    clearResetQuery: () => undefined,
  });
  assert.equal(authoritativeOutcome.status, "found");
  if (authoritativeOutcome.status === "found") {
    assert.deepEqual(
      authoritativeOutcome.accountState.authoritativeManagedInboxes.map(
        (mailbox) => mailbox.id,
      ),
      ["gmail-owner", "gmail-settings-added"],
      "authoritative reload must retain Settings-added Gmail mailboxes without onboarding positions",
    );
  }

  const workspaceSource = readFileSync(
    resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
    "utf8",
  );
  assert.match(
    workspaceSource,
    /function isGmailOAuthReconnectRequired[\s\S]*?provider === "google"[\s\S]*?connected === false[\s\S]*?connectionStatus === "connection_failed"[\s\S]*?GMAIL_OAUTH_RECONNECT_REQUIRED_CONNECTION_MESSAGE/,
  );
  assert.match(
    workspaceSource,
    /canReconnectGmail[\s\S]*?mailbox\.connected && mailbox\.connectionStatus === "connected"[\s\S]*?gmailOAuthReconnectRequired/,
    "Settings must retain healthy manual reconnect and add action-required reconnect",
  );
  assert.match(workspaceSource, />\s*Connection needs attention\s*</);
  assert.match(workspaceSource, /"Reconnect Gmail"/);
  assert.match(
    workspaceSource,
    /reconnectingGmailInboxIdsRef\.current\.has\(inboxId\)[\s\S]*?reconnectingGmailInboxIdsRef\.current\.add\(inboxId\)[\s\S]*?finally[\s\S]*?reconnectingGmailInboxIdsRef\.current\.delete\(inboxId\)/,
    "rapid duplicate Gmail reconnect starts must be guarded synchronously",
  );
}

run()
  .then(() => console.log("\n✓ Gmail OAuth reconnect client tests passed."))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
