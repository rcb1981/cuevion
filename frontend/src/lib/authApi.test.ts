import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  AUTH0_LOGIN_ENDPOINT,
  AUTH0_LOGOUT_ENDPOINT,
  AUTH0_SESSION_ENDPOINT,
  getSessionAccountStorageKey,
  getSessionViewerStorageKey,
  hasAuthCallbackError,
  isAllowedAuth0LogoutUrl,
  isAuth0LoginPath,
  loadStartupSession,
  logoutAuth0Session,
} from "./authApi";

type FakeResponse = {
  status: number;
  payload?: unknown;
  jsonError?: boolean;
};

type FetchCall = {
  url: string;
  init?: RequestInit;
};

function createFetch(responses: FakeResponse[], calls: FetchCall[]): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    const next = responses.shift();
    assert.ok(next, `Unexpected fetch call to ${String(input)}`);

    return {
      status: next.status,
      ok: next.status >= 200 && next.status < 300,
      json: async () => {
        if (next.jsonError) {
          throw new Error("invalid json");
        }
        return next.payload;
      },
    } as Response;
  }) as typeof fetch;
}

function sourceBetween(source: string, startMarker: string, endMarker: string) {
  const start = source.indexOf(startMarker);
  assert.notEqual(start, -1, `Missing start marker: ${startMarker}`);
  const end = source.indexOf(endMarker, start);
  assert.notEqual(end, -1, `Missing end marker: ${endMarker}`);
  return source.slice(start, end);
}

function enclosingEffect(source: string, callMarker: string) {
  const callIndex = source.lastIndexOf(callMarker);
  assert.notEqual(callIndex, -1, `Missing effect call marker: ${callMarker}`);
  const start = source.lastIndexOf("  useEffect(() => {", callIndex);
  assert.notEqual(start, -1, `Missing effect start for: ${callMarker}`);
  const endCandidates = [
    source.indexOf("\n  useEffect(", callIndex),
    source.indexOf("\n  const handle", callIndex),
  ].filter((candidate) => candidate !== -1);
  assert.ok(endCandidates.length > 0, `Missing effect end for: ${callMarker}`);
  const end = Math.min(...endCandidates);
  return source.slice(start, end);
}

assert.equal(AUTH0_LOGIN_ENDPOINT, "/api/auth/login");
assert.equal(isAuth0LoginPath("/login"), true);
assert.equal(isAuth0LoginPath("/login/"), true);
assert.equal(isAuth0LoginPath("/"), false);
assert.equal(isAuth0LoginPath("/login/anything"), false);
assert.equal(hasAuthCallbackError("?error=anything-sensitive"), true);
assert.equal(hasAuthCallbackError("?auth_error=callback_failed"), true);
assert.equal(hasAuthCallbackError("?next=%2F"), false);

assert.equal(
  getSessionAccountStorageKey("auth0", {
    email: "member@example.com",
    workspaceId: "workspace-1",
  }),
  "workspace-1",
);
assert.equal(
  getSessionAccountStorageKey("collaboration", {
    email: " Collaborator@Example.com ",
    workspaceId: "workspace-ignored",
  }),
  "collaborator@example.com",
);
assert.equal(
  getSessionAccountStorageKey("auth0", { email: "member@example.com" }),
  "",
);
assert.equal(
  getSessionViewerStorageKey("auth0", {
    email: "auth0-identity-must-not-be-a-storage-key@example.com",
    userId: "user-1",
  }),
  "user-1",
);
assert.equal(
  getSessionViewerStorageKey("collaboration", {
    email: " Collaborator@Example.com ",
    userId: "user-ignored",
  }),
  "collaborator@example.com",
);
assert.equal(
  JSON.stringify({
    workspace: getSessionAccountStorageKey("auth0", {
      email: "auth0-identity-must-not-be-a-storage-key@example.com",
      workspaceId: "workspace-1",
    }),
    viewer: getSessionViewerStorageKey("auth0", {
      email: "auth0-identity-must-not-be-a-storage-key@example.com",
      userId: "user-1",
    }),
  }).includes("auth0-identity-must-not-be-a-storage-key@example.com"),
  false,
);

const allowedLogoutUrl =
  "https://cuevion-dev.eu.auth0.com/v2/logout?client_id=client-id&returnTo=https%3A%2F%2Fapp.cuevion.com%2Flogin";
assert.equal(isAllowedAuth0LogoutUrl(allowedLogoutUrl), true);
assert.equal(
  isAllowedAuth0LogoutUrl(
    "https://cuevion-dev.eu.auth0.com/v2/logout?client_id=client-id&returnTo=https%3A%2F%2Fevil.example%2F",
  ),
  false,
);
assert.equal(
  isAllowedAuth0LogoutUrl(
    "https://cuevion-dev.eu.auth0.com.evil.example/v2/logout?client_id=client-id&returnTo=https%3A%2F%2Fapp.cuevion.com%2Flogin",
  ),
  false,
);
assert.equal(
  isAllowedAuth0LogoutUrl(
    "https://another-tenant.eu.auth0.com/v2/logout?client_id=client-id&returnTo=https%3A%2F%2Fapp.cuevion.com%2Flogin",
  ),
  false,
);
assert.equal(
  isAllowedAuth0LogoutUrl(`${allowedLogoutUrl}&returnTo=https%3A%2F%2Fapp.cuevion.com%2Flogin`),
  false,
);
assert.equal(isAllowedAuth0LogoutUrl(`${allowedLogoutUrl}&extra=value`), false);

const loginSource = fs.readFileSync(
  path.resolve(__dirname, "../components/auth/Auth0LoginView.tsx"),
  "utf8",
);
assert.equal((loginSource.match(/<button\b/g) ?? []).length, 1);
assert.equal(loginSource.includes("Sign in with email"), true);
assert.equal(loginSource.includes("secure sign-in code"), true);
assert.equal(loginSource.includes("<input"), false);
assert.equal(loginSource.includes("localStorage"), false);
assert.equal(loginSource.includes("sessionStorage"), false);
assert.equal(loginSource.includes("@auth0"), false);

const appSource = fs.readFileSync(path.resolve(__dirname, "../App.tsx"), "utf8");
assert.ok(
  appSource.indexOf('appRoute === "login"') < appSource.indexOf('appRoute === "preview"'),
  "/login must take precedence over onboarding preview",
);
const startupRegion = sourceBetween(
  appSource,
  "const loadSession = async () =>",
  "void loadSession();",
);
assert.equal(startupRegion.includes("loadStartupSession"), true);
assert.equal((startupRegion.match(/loadStartupSession\(\)/g) ?? []).length, 1);
assert.equal(startupRegion.includes("setSessionUser(startupResult.user)"), true);
assert.equal(startupRegion.includes("localStorage"), false);
assert.equal(startupRegion.includes("setAuthenticatedUser"), false);
assert.equal(appSource.includes("getSessionAccountStorageKey("), true);
const accountHydrationRegion = sourceBetween(
  appSource,
  "const hydrateAccountConfig = async () =>",
  "void hydrateAccountConfig();",
);
assert.equal(accountHydrationRegion.includes("sessionAccountStorageKey"), true);
assert.equal(accountHydrationRegion.includes("sessionUser.email"), false);
assert.equal(appSource.includes("const activeCollaborationUser"), true);
assert.equal(appSource.includes('authenticationContext="collaboration"'), true);
assert.equal(appSource.includes('authenticationContext="auth0"'), true);
assert.equal(appSource.includes("normalizeCollaborationUser"), true);
assert.equal(appSource.includes("memberSessionProbeRef.current ??= loadStartupSession()"), true);
assert.equal(appSource.includes("if (!sessionUser) {\n    return <Auth0LoginView />;"), true);

const collaborationNormalizerRegion = sourceBetween(
  appSource,
  "function normalizeCollaborationUser",
  "function parseCollaborationInviteRoute",
);
assert.equal(
  collaborationNormalizerRegion.includes(
    'nextValue.userType !== "member" && nextValue.userType !== "guest"',
  ),
  true,
);
assert.equal(
  collaborationNormalizerRegion.includes("userType: nextValue.userType"),
  true,
);

const authApiSource = fs.readFileSync(path.resolve(__dirname, "./authApi.ts"), "utf8");
const removedLegacyRoutePrefix = ["/api", "beta"].join("/") + "/";
const removedLegacyGateName = ["Beta", "AccessGate"].join("");
const removedLegacySource = ["\"", "beta", "\""].join("");
assert.equal(authApiSource.includes(removedLegacyRoutePrefix), false);
assert.equal(appSource.includes(removedLegacyRoutePrefix), false);
assert.equal(appSource.includes(removedLegacyGateName), false);
assert.equal(appSource.includes(`authSource: ${removedLegacySource}`), false);

const workspaceSource = fs.readFileSync(
  path.resolve(__dirname, "../components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
assert.equal(
  workspaceSource.includes(
    'authenticationContext === "auth0" && authenticatedUser?.userType === "member"',
  ),
  true,
);
for (const memberApiCall of [
  "getMailboxCredentialStatuses(mailboxIds)",
  "fetchParticipantCollaborationThreads({})",
  "workspaceAccountConfigSaveQueueRef.current?.enqueue(nextAccountConfig)",
]) {
  assert.equal(
    enclosingEffect(workspaceSource, memberApiCall).includes(
      "hasAuthenticatedMemberAuthority",
    ),
    true,
    `${memberApiCall} must require explicit Auth0 member authority`,
  );
}
const mailboxRefreshRegion = sourceBetween(
  workspaceSource,
  "const refreshMailboxById = async",
  "const handleSyncActiveMailbox = async",
);
const archiveRefreshRegion = sourceBetween(
  workspaceSource,
  "const refreshProviderArchiveById = async",
  "const refreshMailboxById = async",
);
assert.ok(
  mailboxRefreshRegion.indexOf("if (!hasAuthenticatedMemberAuthority)") <
    mailboxRefreshRegion.indexOf("fetchGmailInbox("),
  "Mailbox provider I/O must be behind explicit Auth0 member authority",
);
assert.ok(
  mailboxRefreshRegion.indexOf("if (!hasAuthenticatedMemberAuthority)") <
    mailboxRefreshRegion.indexOf("connectInboxWithImap("),
  "IMAP provider I/O must be behind explicit Auth0 member authority",
);
const archiveAuthorityGuardIndex = archiveRefreshRegion.indexOf(
  "if (!hasAuthenticatedMemberAuthority)",
);
const archiveProviderFetchIndex = archiveRefreshRegion.indexOf(
  "fetchProviderArchive(",
);
assert.notEqual(archiveAuthorityGuardIndex, -1);
assert.notEqual(archiveProviderFetchIndex, -1);
assert.ok(
  archiveAuthorityGuardIndex < archiveProviderFetchIndex,
  "Archive provider I/O must be behind explicit Auth0 member authority",
);
assert.ok(
  (workspaceSource.match(/!hasAuthenticatedMemberAuthority \|\| !activeMailbox/g) ?? [])
    .length >= 2,
  "Immediate and interval mailbox refresh effects must require Auth0 member authority",
);
const startupSyncRegion = enclosingEffect(
  workspaceSource,
  'reason: "startup"',
);
assert.equal(startupSyncRegion.includes("!hasAuthenticatedMemberAuthority"), true);
assert.equal(
  workspaceSource.includes(
    "shouldPollTeamMembers={Boolean(\n                    hasAuthenticatedMemberAuthority &&",
  ),
  true,
);
const logoutRegion = sourceBetween(
  workspaceSource,
  "const handleConfirmLogout = async () =>",
  "if (collaborationInviteRoute)",
);
assert.equal(logoutRegion.includes('authenticationContext === "collaboration"'), true);
assert.equal(logoutRegion.includes("logoutAuth0Session()"), true);
assert.equal(logoutRegion.includes("window.location.assign(result.logoutUrl)"), true);
assert.ok(
  logoutRegion.indexOf('authenticationContext === "collaboration"') <
    logoutRegion.indexOf("logoutAuth0Session()"),
  "Collaboration logout must exit before the Auth0 member logout request",
);
assert.equal(workspaceSource.includes("getSessionViewerStorageKey("), true);
assert.equal(workspaceSource.includes("workspacePersistenceScope"), true);
assert.equal(
  workspaceSource.includes("currentViewerPersistenceKey={viewerPersistenceScope}"),
  true,
);
assert.equal(
  workspaceSource.includes(
    "normalizeSenderLearningKey(currentUserEmail || currentUserId)",
  ),
  false,
);

async function runAsyncTests() {
  {
    const calls: FetchCall[] = [];
    const fetchImplementation = createFetch(
      [
        {
          status: 200,
          payload: {
            authenticated: true,
            authSource: "auth0",
            userId: "user-1",
            workspaceId: "workspace-1",
            email: " Member@Example.com ",
            name: " Member Name ",
            userType: "member",
          },
        },
      ],
      calls,
    );

    const result = await loadStartupSession(fetchImplementation);
    assert.deepEqual(result, {
      status: "authenticated",
      authSource: "auth0",
      user: {
        userId: "user-1",
        workspaceId: "workspace-1",
        email: "member@example.com",
        name: "Member Name",
        userType: "member",
      },
    });
    assert.deepEqual(calls.map((call) => call.url), [AUTH0_SESSION_ENDPOINT]);
    assert.equal(calls[0]?.init?.credentials, "include");
    assert.equal(calls[0]?.init?.cache, "no-store");
  }

  {
    const calls: FetchCall[] = [];
    const fetchImplementation = createFetch(
      [
        { status: 401, payload: { authenticated: false } },
        {
          status: 200,
          payload: {
            authenticated: true,
            user: {
              email: "must-not-be-used@example.com",
              name: "Must Not Be Used",
              userType: "member",
            },
          },
        },
      ],
      calls,
    );

    const result = await loadStartupSession(fetchImplementation);
    assert.deepEqual(result, {
      status: "unauthenticated",
      authSource: null,
      user: null,
    });
    assert.deepEqual(calls.map((call) => call.url), [AUTH0_SESSION_ENDPOINT]);
  }

  {
    const calls: FetchCall[] = [];
    const result = await loadStartupSession(
      createFetch([{ status: 200, payload: { authenticated: false } }], calls),
    );
    assert.deepEqual(result, {
      status: "unauthenticated",
      authSource: null,
      user: null,
    });
    assert.deepEqual(calls.map((call) => call.url), [AUTH0_SESSION_ENDPOINT]);
  }

  {
    const calls: FetchCall[] = [];
    const fetchImplementation = createFetch(
      [{ status: 503, payload: { error: { code: "authority_unavailable" } } }],
      calls,
    );

    const result = await loadStartupSession(fetchImplementation);
    assert.deepEqual(result, {
      status: "unavailable",
      authSource: null,
      user: null,
    });
    assert.deepEqual(calls.map((call) => call.url), [AUTH0_SESSION_ENDPOINT]);
  }

  {
    const calls: FetchCall[] = [];
    const fetchImplementation = createFetch(
      [{ status: 200, payload: { authenticated: true, authSource: "auth0" } }],
      calls,
    );

    const result = await loadStartupSession(fetchImplementation);
    assert.deepEqual(result, {
      status: "unavailable",
      authSource: null,
      user: null,
    });
    assert.deepEqual(calls.map((call) => call.url), [AUTH0_SESSION_ENDPOINT]);
  }

  {
    const calls: FetchCall[] = [];
    const result = await loadStartupSession(
      createFetch([{ status: 200, jsonError: true }], calls),
    );
    assert.deepEqual(result, {
      status: "unavailable",
      authSource: null,
      user: null,
    });
    assert.deepEqual(calls.map((call) => call.url), [AUTH0_SESSION_ENDPOINT]);
  }

  {
    const calls: FetchCall[] = [];
    const fetchImplementation = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      throw new Error("network unavailable");
    }) as typeof fetch;
    const result = await loadStartupSession(fetchImplementation);
    assert.deepEqual(result, {
      status: "unavailable",
      authSource: null,
      user: null,
    });
    assert.deepEqual(calls.map((call) => call.url), [AUTH0_SESSION_ENDPOINT]);
  }

  {
    const calls: FetchCall[] = [];
    const result = await logoutAuth0Session(
      createFetch([{ status: 200, payload: { logoutUrl: allowedLogoutUrl } }], calls),
    );
    assert.deepEqual(result, { ok: true, logoutUrl: allowedLogoutUrl });
    assert.equal(calls[0]?.url, AUTH0_LOGOUT_ENDPOINT);
    assert.equal(calls[0]?.init?.method, "POST");
  }

  {
    const calls: FetchCall[] = [];
    const result = await logoutAuth0Session(
      createFetch([{ status: 200, payload: { logoutUrl: null } }], calls),
    );
    assert.deepEqual(result, { ok: false, logoutUrl: null });
    assert.deepEqual(calls.map((call) => call.url), [AUTH0_LOGOUT_ENDPOINT]);
  }

  console.log("authApi tests passed");
}

void runAsyncTests().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
