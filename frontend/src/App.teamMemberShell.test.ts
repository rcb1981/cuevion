import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test } from "node:test";
import React from "react";
import "sucrase/register/tsx.js";
import type { StartupSessionResult, WorkspaceRole } from "./lib/authApi";

const {
  Auth0SessionRoute,
  TeamInviteContinuationRouteView,
  TeamInviteRouteView,
} = require("./App.tsx") as typeof import("./App");
const source = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf8");

function authenticated(workspaceRole: WorkspaceRole): StartupSessionResult {
  return {
    status: "authenticated", authSource: "auth0",
    user: {
      userType: "member", workspaceRole, email: "invited@example.com", name: "Invited",
      userId: "usr_AAAAAAAAAAAAAAAAAAAAAA", workspaceId: "wsp_BBBBBBBBBBBBBBBBBBBBBB",
    },
  };
}

function route(session: StartupSessionResult | null, search = "", appRoute: "app" | "preview" = "app") {
  const originalWindow = globalThis.window;
  const originalFetch = globalThis.fetch;
  const originalReact = (globalThis as any).React;
  (globalThis as any).React = React; // The existing sucrase runner uses classic JSX.
  const forbidden = () => { throw new Error("Owner startup/storage/network must not run at the role boundary"); };
  Object.defineProperty(globalThis, "window", {
    configurable: true, writable: true,
    value: {
      location: { search, pathname: "/", hash: "" },
      get localStorage() { return forbidden(); },
      get sessionStorage() { return forbidden(); },
    },
  });
  globalThis.fetch = forbidden as typeof fetch;
  try {
    return Auth0SessionRoute({ session, appRoute, onExitPreview: forbidden });
  } finally {
    globalThis.window = originalWindow;
    globalThis.fetch = originalFetch;
    (globalThis as any).React = originalReact;
  }
}

test("MEMBER selects a separate shell without mounting owner config, setup, or storage", () => {
  const session = authenticated("member");
  const result = route(session);
  assert.equal(result.type, React.Suspense);
  assert.equal(result.props.children.props.authenticatedUser, session.user);
  assert.deepEqual(Object.keys(result.props.children.props), ["authenticatedUser"]);
  assert.match(String(result.props.children.key), /^wsp_.*:usr_/);
});

for (const role of ["owner", "admin"] as const) {
  test(`${role} keeps the existing owner startup and the exact probed session`, () => {
    const session = authenticated(role);
    const result = route(session);
    assert.equal(result.type.name, "OwnerAppStartup");
    assert.equal(result.props.session, session);
    assert.equal(result.props.authenticatedUser, undefined);
  });
}

for (const [search, appRoute] of [
  ["?section=inboxes", "app"],
  ["?view=workspace&workspaceRole=owner", "app"],
  ["?reset_onboarding=1&onboarding_complete=true", "app"],
  ["?preview=onboarding", "preview"],
  ["?collab_invite=legacy-token&message_id=mail&invitee=owner@example.com", "app"],
] as const) {
  test(`MEMBER cannot select owner startup through ${search}`, () => {
    const baseline = route(authenticated("member"));
    const manipulated = route(authenticated("member"), search, appRoute);
    assert.equal(manipulated.type, React.Suspense);
    assert.equal(manipulated.props.children.type, baseline.props.children.type);
    assert.equal(manipulated.props.children.props.userConfig, undefined);
    assert.equal(manipulated.props.children.props.onboardingState, undefined);
  });
}

test("unknown or missing server role fails closed even if routing receives an invalid object", () => {
  for (const role of [undefined, null, "OWNER", "superuser", {}, ["member"]]) {
    const session = authenticated(role as WorkspaceRole);
    const result = route(session, "?preview=onboarding", "preview");
    assert.equal(result.type, "main");
    assert.equal(result.props.children.props.role, "alert");
  }
});

test("unresolved and unavailable sessions never mount owner startup", () => {
  assert.equal(route(null).type.name, "WorkspaceLoadingFallback");
  const unavailable = route({ status: "unavailable", authSource: null, user: null });
  assert.equal(unavailable.type, "main");
});

test("ordinary unauthenticated owner login retains Auth0's existing view", () => {
  const result = route({ status: "unauthenticated", authSource: null, user: null });
  assert.equal(result.type.name, "Auth0LoginView");
});

test("pending Team URL mounts only the existing invite card before session resolution", () => {
  const token = `tinv_bound.${"a".repeat(43)}`;
  const result = route(null, `?team_invite=${token}`);
  assert.equal(result.type, TeamInviteRouteView);
  assert.equal(result.props.route.inviteToken, token);
  assert.equal(result.props.sessionStatus, "loading");
  assert.equal(result.props.sessionUser, null);
});

test("ambiguous or empty Team credentials remain on the fail-closed invite route", () => {
  for (const search of ["?team_invite=", "?team_invite=one&team_invite=two"]) {
    const result = route(authenticated("member"), search);
    assert.equal(result.type, TeamInviteRouteView);
    assert.equal(result.props.route.inviteToken, "");
  }
});

test("accepted invite callback at / routes from the MEMBER session alone", () => {
  const result = route(authenticated("member"));
  assert.equal(result.type, React.Suspense);
  assert.equal(result.props.children.props.authenticatedUser.workspaceRole, "member");
  assert.equal("completed" in result.props.children.props, false);
  assert.equal("userConfig" in result.props.children.props, false);
});

for (const role of ["owner", "admin", "member"] as const) {
  test(`fixed Team continuation isolates ${role} until a fresh session is loaded at /`, () => {
    const session = authenticated(role);
    const continuing = route(session, "?team_continue=1");
    assert.equal(continuing.type, TeamInviteContinuationRouteView);
    assert.deepEqual(continuing.props, { route: { valid: true } });
    const completed = route(session);
    if (role === "member") {
      assert.equal(completed.type, React.Suspense);
      assert.equal(completed.props.children.props.authenticatedUser, session.user);
    } else {
      assert.equal(completed.type.name, "OwnerAppStartup");
      assert.equal(completed.props.session, session);
    }
  });
}

test("Team continuation precedes unresolved or unavailable session routing", () => {
  for (const session of [
    null,
    { status: "unauthenticated", authSource: null, user: null },
    { status: "unavailable", authSource: null, user: null },
  ] as const) {
    const result = route(session, "?team_continue=1");
    assert.equal(result.type, TeamInviteContinuationRouteView);
    assert.deepEqual(result.props, { route: { valid: true } });
  }
});

test("malformed continuation markers cannot mount any role's shell or original token invite", () => {
  for (const role of ["owner", "admin", "member"] as const) {
    for (const search of [
      "?team_continue=",
      "?team_continue=1&team_continue=1",
      "?team_continue=1&workspaceRole=owner",
      "?team_continue=1&team_invite=raw-token",
    ]) {
      const result = route(authenticated(role), search);
      assert.equal(result.type, TeamInviteContinuationRouteView);
      assert.deepEqual(result.props, { route: { valid: false } });
    }
  }
});

test("the fixed callback route is handled before guest, login, or Auth0 startup mounts", () => {
  const root = source.slice(source.indexOf("export default function App"));
  const continuation = root.indexOf("if (teamInviteContinuationRoute)");
  assert.notEqual(continuation, -1);
  assert.ok(continuation < root.indexOf("if (collaborationGuestRoute)"));
  assert.ok(continuation < root.indexOf('if (appRoute === "login")'));
  assert.ok(continuation < root.indexOf("<Auth0SessionBoundary"));
  const viewStart = source.indexOf("export function TeamInviteContinuationRouteView");
  const viewEnd = source.indexOf("export function TeamInviteRouteView", viewStart);
  const view = source.slice(viewStart, viewEnd);
  assert.doesNotMatch(view, /loadStartupSession|OwnerAppStartup|TeamMemberShell|localStorage|sessionStorage/);
  assert.match(view, /continueTeamInvite\(\)/);
});

test("owner hydration receives the cached probe; member mounting cannot run the mailbox scrub", () => {
  const ownerStart = source.indexOf("function OwnerAppStartup");
  const boundaryStart = source.indexOf("export function Auth0SessionRoute");
  const appStart = source.indexOf("export default function App");
  assert.match(source.slice(ownerStart, boundaryStart), /scrubManagedInboxBrowserStorage\(\)/);
  assert.doesNotMatch(source.slice(appStart), /scrubManagedInboxBrowserStorage/);
  assert.match(source, /startupSession \? Promise\.resolve\(startupSession\) : null/);
  assert.match(source.slice(boundaryStart, appStart), /session\.user\.workspaceRole === "member"/);
});

test("external guest routing remains ahead of the Auth0 boundary with its own identity view", () => {
  const root = source.slice(source.indexOf("export default function App"));
  assert.ok(root.indexOf("if (collaborationGuestRoute)") < root.indexOf("<Auth0SessionBoundary"));
  assert.match(root, /<ExternalCollaborationGuestView[\s\S]*?initialInviteToken=\{collaborationGuestRoute\.token\}/);
  const start = source.indexOf("function normalizeCollaborationUser");
  const end = source.indexOf("function parseCollaborationInviteRoute", start);
  assert.doesNotMatch(source.slice(start, end), /workspaceRole|workspaceId|userId:/);
});
