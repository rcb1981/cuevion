import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import "sucrase/register/tsx.js";

const {
  classifyTeamInviteRouteFailure,
  parseTeamInviteContinuationRoute,
  TeamInviteContinuationRouteView,
  TeamInviteRouteView,
} = require("./App.tsx") as typeof import("./App");

assert.equal(classifyTeamInviteRouteFailure("expired"), "expired");
assert.equal(classifyTeamInviteRouteFailure("used"), "used");
assert.equal(classifyTeamInviteRouteFailure("conflict"), "used");
assert.equal(classifyTeamInviteRouteFailure("invalid"), "invalid");
assert.equal(classifyTeamInviteRouteFailure("unauthorized"), "unauthorized");
assert.equal(classifyTeamInviteRouteFailure("unavailable"), "unavailable");
assert.equal(
  classifyTeamInviteRouteFailure("forbidden", "recipient_mismatch"),
  "wrong-user",
);
assert.equal(
  classifyTeamInviteRouteFailure("forbidden", "wrong-recipient"),
  "wrong-user",
);
assert.equal(
  classifyTeamInviteRouteFailure("forbidden", "management_forbidden"),
  "unavailable",
);

const source = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf8");

const routeStart = source.indexOf("function TeamInviteRouteView({");
assert.notEqual(routeStart, -1);
const routeEnd = source.indexOf("function OnboardingPreviewRoute", routeStart);
assert.notEqual(routeEnd, -1);
const routeBlock = source.slice(routeStart, routeEnd);

for (const message of [
  "This invitation has expired.",
  "This invitation has already been handled.",
  "This invitation belongs to a different signed-in user.",
  "This invitation link is invalid.",
  "Sign in or create an account with the invited email address.",
  "Team invitation authority is temporarily unavailable.",
]) {
  assert.match(routeBlock, new RegExp(message.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
}

assert.match(
  routeBlock,
  /status === "updating"[\s\S]*?sessionStatus !== "authenticated"[\s\S]*?!sessionUser[\s\S]*?return;/,
  "accept/decline must be blocked while in flight or without an authenticated recipient",
);
assert.match(routeBlock, /disabled=\{status === "updating"\}/);
assert.match(
  routeBlock,
  /const result = await mutateTeamInvite\([\s\S]*?setStatus\(result\.invite\.status === "accepted" \? "accepted" : "declined"\)/,
  "the route must wait for the server mutation before rendering a terminal state",
);

const sessionEffectStart = source.indexOf(
  "useEffect(() => {\n    if (collaborationInviteRoute) {",
);
assert.notEqual(sessionEffectStart, -1);
const sessionEffectEnd = source.indexOf(
  "}, [collaborationInviteRoute, teamInviteRoute]);",
  sessionEffectStart,
);
assert.notEqual(sessionEffectEnd, -1);
const sessionEffect = source.slice(sessionEffectStart, sessionEffectEnd);
assert.match(
  sessionEffect,
  /if \(collaborationInviteRoute\) \{[\s\S]*?setSessionStatus\("unauthenticated"\);[\s\S]*?setSessionUser\(null\);[\s\S]*?return;/,
  "the frozen Collaboration invite route must retain its existing local guest boundary",
);
assert.match(
  sessionEffect,
  /memberSessionProbeRef\.current \?\?= loadStartupSession\(\)/,
  "a Team invite route must continue through the authenticated session probe",
);
assert.doesNotMatch(
  sessionEffect,
  /if \(teamInviteRoute\) \{[\s\S]*?setSessionStatus\("unauthenticated"\)/,
  "Team invites must not be forced into the unauthenticated Collaboration path",
);

const teamRouteRender = source.slice(
  source.indexOf("if (teamInviteRoute) {", sessionEffectEnd),
  source.indexOf("if (collaborationInviteRoute) {", sessionEffectEnd),
);
assert.match(teamRouteRender, /key=\{teamInviteRoute\.inviteToken\}/);
assert.match(teamRouteRender, /sessionStatus=\{sessionStatus\}/);
assert.match(teamRouteRender, /sessionUser=\{sessionUser\}/);

// Render just the invite route with deterministic hooks; API parsing and
// authentication navigation remain the actual application implementations.
const React = require("react") as typeof import("react");
type InviteRouteProps = Parameters<typeof TeamInviteRouteView>[0];
type TestElement = {
  type: unknown;
  props: { children?: unknown; onClick?: () => void; disabled?: boolean };
};

function treeElements(node: unknown): TestElement[] {
  if (Array.isArray(node)) {
    return node.flatMap(treeElements);
  }
  if (!React.isValidElement(node)) {
    return [];
  }
  const element = node as TestElement;
  return [element, ...treeElements(element.props.children)];
}

function treeText(node: unknown): string {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(treeText).join(" ");
  }
  return React.isValidElement(node)
    ? treeText((node as TestElement).props.children)
    : "";
}

function createRouteHarness<Props>(
  component: (props: Props) => ReturnType<typeof TeamInviteRouteView>,
  props: Props,
) {
  const slots: unknown[] = [];
  const cleanupEffects: Array<(() => void) | undefined> = [];
  const effectCallbacks: Array<(() => void | (() => void)) | undefined> = [];
  let nextSlot = 0;
  let effects: Array<() => void> = [];
  let unmounted = false;
  let staleStateUpdates = 0;

  return {
    render() {
      nextSlot = 0;
      effects = [];
      const originalHooks = {
        useState: React.useState,
        useRef: React.useRef,
        useEffect: React.useEffect,
        useCallback: React.useCallback,
      };
      React.useState = ((initial: unknown) => {
        const slot = nextSlot++;
        if (!(slot in slots)) {
          slots[slot] = typeof initial === "function" ? initial() : initial;
        }
        return [slots[slot], (next: unknown) => {
          if (unmounted) {
            staleStateUpdates += 1;
            return;
          }
          slots[slot] = typeof next === "function" ? next(slots[slot]) : next;
        }];
      }) as typeof React.useState;
      React.useRef = ((initial: unknown) => {
        const slot = nextSlot++;
        slots[slot] ??= { current: initial };
        return slots[slot];
      }) as typeof React.useRef;
      React.useEffect = ((effect: () => void | (() => void), deps?: unknown[]) => {
        const slot = nextSlot++;
        effectCallbacks[slot] = effect;
        const previous = slots[slot] as unknown[] | undefined;
        if (!deps || !previous || deps.some((value, index) => !Object.is(value, previous[index]))) {
          slots[slot] = deps;
          effects.push(() => {
            cleanupEffects[slot]?.();
            cleanupEffects[slot] = effect() || undefined;
          });
        }
      }) as typeof React.useEffect;
      React.useCallback = ((callback: unknown, deps: unknown[]) => {
        const slot = nextSlot++;
        const previous = slots[slot] as { callback: unknown; deps: unknown[] } | undefined;
        if (!previous || deps.some((value, index) => !Object.is(value, previous.deps[index]))) {
          slots[slot] = { callback, deps };
        }
        return (slots[slot] as { callback: unknown }).callback;
      }) as typeof React.useCallback;
      let tree: ReturnType<typeof TeamInviteRouteView>;
      try {
        tree = component(props);
      } finally {
        Object.assign(React, originalHooks);
      }
      for (const effect of effects) {
        effect();
      }
      return tree;
    },
    replayEffects() {
      for (const cleanup of cleanupEffects) {
        cleanup?.();
      }
      effectCallbacks.forEach((effect, slot) => {
        cleanupEffects[slot] = effect?.() || undefined;
      });
    },
    unmount() {
      unmounted = true;
      for (const cleanup of cleanupEffects) {
        cleanup?.();
      }
    },
    staleStateUpdates: () => staleStateUpdates,
  };
}

const inviteToken = `tinv_team-recipient.${"a".repeat(43)}`;
const signedInUser: NonNullable<InviteRouteProps["sessionUser"]> = {
  email: "current-user@example.com",
  name: "Current User",
  userType: "member",
  userId: "user-1",
  workspaceId: "workspace-1",
  workspaceRole: "member",
};

function inviteResponse(status = "pending") {
  return new Response(JSON.stringify({
    ok: true,
    invite: {
      inviteeName: "Invited Person",
      accessLevel: "Shared",
      status,
      expiresAt: Date.now() + 60_000,
    },
  }), { status: 200 });
}

function failureResponse(status: number, code: string) {
  return new Response(JSON.stringify({ ok: false, error: { code } }), { status });
}

type RouteTest = {
  responses: Array<Response | Promise<Response> | (() => Promise<Response>)>;
  sessionStatus?: InviteRouteProps["sessionStatus"];
  workspaceRole?: NonNullable<InviteRouteProps["sessionUser"]>["workspaceRole"];
  token?: string;
  continuation?: boolean;
  search?: string;
  pathname?: string;
  hash?: string;
};
type CapturedCall = { url: string; init?: RequestInit };
let runtimeCases = 0;

async function runRouteTest(
  options: RouteTest,
  test: (context: {
    render: () => ReturnType<typeof TeamInviteRouteView>;
    settle: () => Promise<ReturnType<typeof TeamInviteRouteView>>;
    calls: CapturedCall[];
    navigations: string[];
    replayEffects: () => void;
    unmount: () => void;
    staleStateUpdates: () => number;
  }) => void | Promise<void>,
) {
  const calls: CapturedCall[] = [];
  const navigations: string[] = [];
  let storageAccesses = 0;
  let unexpectedRequests = 0;
  const forbidStorage = () => {
    storageAccesses += 1;
    throw new Error("Team invite route must not access browser storage");
  };
  const originalGlobals = ["React", "fetch", "window", "localStorage", "sessionStorage"].map(
    (key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)] as const,
  );
  Object.defineProperty(globalThis, "React", { configurable: true, value: React });
  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    value: async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      const response = options.responses.shift();
      if (!response) {
        unexpectedRequests += 1;
        throw new Error("Unexpected Team invite request");
      }
      return typeof response === "function" ? response() : response;
    },
  });
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      location: {
        origin: "https://app.cuevion.com",
        pathname: options.pathname ?? "/",
        search: options.search ?? (options.continuation ? "?team_continue=1" : ""),
        hash: options.hash ?? "",
        replace: (url: string) => navigations.push(url),
      },
      get localStorage() { return forbidStorage(); },
      get sessionStorage() { return forbidStorage(); },
    },
  });
  for (const key of ["localStorage", "sessionStorage"]) {
    Object.defineProperty(globalThis, key, { configurable: true, get: forbidStorage });
  }
  const sessionStatus = options.sessionStatus ?? "unauthenticated";
  const harness = options.continuation
    ? createRouteHarness(TeamInviteContinuationRouteView, {
      route: parseTeamInviteContinuationRoute()!,
    })
    : createRouteHarness(TeamInviteRouteView, {
    route: { inviteToken: options.token ?? inviteToken },
    sessionStatus,
    sessionUser: sessionStatus === "authenticated"
      ? { ...signedInUser, workspaceRole: options.workspaceRole ?? signedInUser.workspaceRole }
      : null,
  });
  const render = () => harness.render();
  const settle = async () => {
    await new Promise<void>((resolve) => setImmediate(resolve));
    return render();
  };
  try {
    render();
    await test({
      render, settle, calls, navigations,
      replayEffects: () => harness.replayEffects(),
      unmount: () => harness.unmount(),
      staleStateUpdates: () => harness.staleStateUpdates(),
    });
    assert.equal(unexpectedRequests, 0);
    assert.equal(storageAccesses, 0);
    assert.equal(options.responses.length, 0, "Every expected response must be consumed");
    for (const call of calls) {
      const url = new URL(call.url, "https://app.cuevion.com");
      assert.equal(url.pathname, "/api/team/invite");
      assert.equal(call.init?.credentials, "include");
      assert.equal(call.init?.cache, "no-store");
    }
    runtimeCases += 1;
  } finally {
    harness.unmount();
    for (const [key, descriptor] of originalGlobals) {
      if (descriptor) {
        Object.defineProperty(globalThis, key, descriptor);
      } else {
        Reflect.deleteProperty(globalThis, key);
      }
    }
  }
}

function buttons(tree: unknown) {
  return treeElements(tree).filter((element) => element.type === "button");
}

function button(tree: unknown, label: string) {
  const match = buttons(tree).find((element) => treeText(element.props.children) === label);
  assert.ok(match, `Expected button: ${label}`);
  return match;
}

function acceptedContinuationResponse() {
  return new Response(JSON.stringify({ ok: true, status: "accepted" }), { status: 200 });
}

function assertContinuationRequest(call: CapturedCall) {
  assert.equal(call.url, "/api/team/invite?op=continue");
  assert.equal(call.init?.method, "POST");
  assert.equal(call.init?.mode, "same-origin");
  assert.equal(call.init?.body, "{}");
  assert.deepEqual(call.init?.headers, {
    Accept: "application/json",
    "Content-Type": "application/json",
  });
}

async function runContinuationRuntimeTests() {
  assert.deepEqual(parseTeamInviteContinuationRoute({
    pathname: "/", search: "?team_continue=1", hash: "",
  }), { valid: true });
  for (const search of ["", "?section=team", `?team_invite=${inviteToken}`]) {
    assert.equal(parseTeamInviteContinuationRoute({ pathname: "/", search, hash: "" }), null);
  }

  let finishPending!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { finishPending = resolve; });
  await runRouteTest({ continuation: true, responses: [pending] }, async ({ render, settle, replayEffects, calls, navigations }) => {
    assert.match(treeText(render()), /Completing your Team invitation/);
    assert.equal(buttons(render()).length, 0);
    assert.equal(calls.length, 1);
    assertContinuationRequest(calls[0]);
    replayEffects();
    assert.equal(calls.length, 1, "StrictMode replay must share the pending POST");
    assert.deepEqual(navigations, []);
    finishPending(acceptedContinuationResponse());
    assert.match(treeText(await settle()), /Opening Cuevion/);
    assert.deepEqual(navigations, ["/"]);
  });

  for (const failure of [
    () => Promise.reject<Response>(new Error("Response lost after server acceptance")),
    failureResponse(503, "continuation_unavailable"),
  ]) {
    await runRouteTest({ continuation: true, responses: [failure, acceptedContinuationResponse()] }, async ({ render, settle, calls, navigations }) => {
      const tree = await settle();
      assert.match(treeText(tree), /could not be completed yet/);
      assert.equal(window.location.search, "?team_continue=1");
      assert.deepEqual(navigations, []);
      const retry = button(tree, "Retry");
      retry.props.onClick?.();
      retry.props.onClick?.();
      render();
      assert.equal(calls.length, 2, "Concurrent retries must produce only one additional request");
      await settle();
      calls.forEach(assertContinuationRequest);
      assert.deepEqual(navigations, ["/"]);
    });
  }

  // Reopening the fixed marker can replay the same completed server-bound
  // continuation. The client still requires the authoritative accepted DTO.
  await runRouteTest({ continuation: true, responses: [acceptedContinuationResponse()] }, async ({ settle, calls, navigations }) => {
    await settle();
    assert.equal(calls.length, 1);
    assertContinuationRequest(calls[0]);
    assert.deepEqual(navigations, ["/"]);
  });

  for (const payload of [
    null,
    [],
    { ok: true },
    { ok: true, status: "pending" },
    { ok: "true", status: "accepted" },
    { ok: true, status: "accepted", workspaceRole: "owner" },
    { ok: true, status: "accepted", token: "unexpected-token" },
  ]) {
    await runRouteTest({ continuation: true, responses: [new Response(JSON.stringify(payload), { status: 200 })] }, async ({ settle, navigations }) => {
      const tree = await settle();
      assert.match(treeText(tree), /could not be completed yet/);
      button(tree, "Retry");
      assert.deepEqual(navigations, [], "Unknown successful payloads must never select a destination");
    });
  }

  for (const location of [
    { search: "?team_continue=" },
    { search: "?team_continue=0" },
    { search: "?team_continue=1&team_continue=1" },
    { search: "?team_continue=1&workspaceRole=owner" },
    { search: "?team_continue=1&workspaceId=foreign" },
    { search: "?team_continue=1&invitationId=foreign" },
    { search: `?team_continue=1&team_invite=${inviteToken}` },
    { search: "?team_continue=1&next=https://example.com" },
    { search: "?team_continue=%31" },
    { search: "?%74eam_continue=1" },
    { hash: "#token=unexpected" },
    { pathname: "/login" },
    { search: "", hash: "#team_continue=1" },
  ]) {
    await runRouteTest({ continuation: true, responses: [], ...location }, async ({ settle, calls, navigations }) => {
      const tree = await settle();
      assert.match(treeText(tree), /continuation link is invalid/);
      assert.deepEqual(buttons(tree).map((element) => treeText(element.props.children)), ["Back to Cuevion"]);
      assert.equal(calls.length, 0);
      assert.deepEqual(navigations, []);
    });
  }

  await runRouteTest({ continuation: true, responses: [failureResponse(401, "unauthorized")] }, async ({ settle, calls, navigations }) => {
    const tree = await settle();
    assert.match(treeText(tree), /Sign in with the invited email address, then reopen the original invitation/);
    assert.deepEqual(buttons(tree).map((element) => treeText(element.props.children)), ["Sign in"]);
    assert.deepEqual(navigations, []);
    button(tree, "Sign in").props.onClick?.();
    assert.deepEqual(navigations, ["/api/auth/login"]);
    assert.equal(calls.length, 1);
  });

  for (const [status, code] of [
    [403, "recipient_mismatch"],
    [404, "continuation_unavailable"],
    [409, "invalid_continuation"],
    [410, "continuation_expired"],
  ] as const) {
    await runRouteTest({ continuation: true, responses: [failureResponse(status, code)] }, async ({ settle, calls, navigations }) => {
      const tree = await settle();
      assert.match(treeText(tree), /could not be continued/);
      assert.deepEqual(buttons(tree).map((element) => treeText(element.props.children)), ["Back to Cuevion"]);
      assert.deepEqual(navigations, [], "A failed continuation must remain isolated until acknowledged");
      button(tree, "Back to Cuevion").props.onClick?.();
      assert.deepEqual(navigations, ["/"]);
      assert.equal(calls.length, 1);
    });
  }

  let finishStale!: (response: Response) => void;
  await runRouteTest({
    continuation: true,
    responses: [new Promise<Response>((resolve) => { finishStale = resolve; })],
  }, async ({ unmount, calls, navigations, staleStateUpdates }) => {
    unmount();
    finishStale(acceptedContinuationResponse());
    await new Promise<void>((resolve) => setImmediate(resolve));
    assert.equal(calls.length, 1);
    assert.deepEqual(navigations, [], "An unmounted continuation must ignore its stale completion");
    assert.equal(staleStateUpdates(), 0);
  });
}

async function runRuntimeTests() {
  await runRouteTest({ responses: [inviteResponse()] }, async ({ settle, calls, navigations }) => {
    const tree = await settle();
    assert.match(treeText(tree), /Sign in or create an account with the invited email address\./);
    assert.deepEqual(buttons(tree).map((element) => treeText(element.props.children)), ["Sign in or create account"]);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].init?.method, "GET");
    assert.equal(calls[0].url, `/api/team/invite?op=lookup&token=${inviteToken}`);
    const signIn = button(tree, "Sign in or create account");
    signIn.props.onClick?.();
    signIn.props.onClick?.();
    assert.deepEqual(navigations, [`/api/auth/login?team_invite=${inviteToken}`]);
    assert.equal(calls.length, 1, "Authentication CTA must not mutate the invitation");
  });

  await runRouteTest({
    responses: [inviteResponse(), failureResponse(403, "recipient_mismatch")],
    sessionStatus: "authenticated",
  }, async ({ settle, calls, navigations }) => {
    const tree = await settle();
    button(tree, "Accept").props.onClick?.();
    const rejected = await settle();
    assert.match(treeText(rejected), /different signed-in user/);
    assert.deepEqual(buttons(rejected).map((element) => treeText(element.props.children)), ["Switch account"]);
    assert.equal(calls.length, 2);
    assert.equal(calls[1].url, `/api/team/invite?op=action&token=${inviteToken}`);
    assert.equal(calls[1].init?.method, "POST");
    assert.deepEqual(JSON.parse(String(calls[1].init?.body)), { action: { type: "accept" } });
    button(rejected, "Switch account").props.onClick?.();
    assert.deepEqual(navigations, [`/api/auth/login?team_invite=${inviteToken}`]);
    assert.equal(calls.length, 2, "Switch account sends no browser identity or mutation");
  });

  for (const [response, expectedMessage] of [
    [failureResponse(404, "invalid_invite"), "This invitation link is invalid."],
    [failureResponse(410, "expired_invite"), "This invitation has expired."],
    [inviteResponse("cancelled"), "This invitation has already been handled."],
    [inviteResponse("accepted"), "Accepted"],
  ] as const) {
    await runRouteTest({ responses: [response] }, async ({ settle, calls, navigations }) => {
      const tree = await settle();
      assert.ok(treeText(tree).includes(expectedMessage));
      assert.equal(buttons(tree).length, 0, "Terminal invites must not start authentication or mutate");
      assert.equal(calls.length, 1);
      assert.deepEqual(navigations, []);
    });
  }

  await runRouteTest({
    responses: [inviteResponse()],
    sessionStatus: "unavailable",
  }, async ({ settle, navigations }) => {
    const tree = await settle();
    assert.match(treeText(tree), /authority is temporarily unavailable/);
    assert.equal(buttons(tree).length, 0);
    assert.deepEqual(navigations, []);
  });

  await runRouteTest({
    responses: [inviteResponse("accepted")],
    sessionStatus: "authenticated",
  }, async ({ settle, calls, navigations }) => {
    await settle();
    assert.deepEqual(navigations, ["/"], "An accepted member must enter through a fresh server session");
    assert.equal(calls.length, 1);
  });

  await runRouteTest({
    responses: [inviteResponse("accepted")],
    sessionStatus: "authenticated",
    workspaceRole: "admin",
  }, async ({ settle, calls, navigations }) => {
    const tree = await settle();
    assert.deepEqual(navigations, [], "Admin access must not be inferred from invite acceptance");
    button(tree, "Continue to Cuevion").props.onClick?.();
    assert.deepEqual(navigations, ["/"]);
    assert.equal(calls.length, 1, "Continuation must refresh the app without mutating an accepted invite");
  });

  for (const [action, acceptedStatus] of [["Accept", "accepted"], ["Decline", "declined"]] as const) {
    let finishMutation!: (response: Response) => void;
    const mutation = new Promise<Response>((resolve) => { finishMutation = resolve; });
    await runRouteTest({
      responses: [inviteResponse(), mutation],
      sessionStatus: "authenticated",
    }, async ({ render, settle, calls, navigations }) => {
      const tree = await settle();
      const actionButton = button(tree, action);
      actionButton.props.onClick?.();
      actionButton.props.onClick?.();
      assert.equal(calls.length, 2, "Same-tick clicks must issue exactly one mutation");
      assert.deepEqual(navigations, [], "Acceptance must wait for the authoritative response");
      assert.equal(buttons(render()).length, 0, "In-flight actions must not remain actionable");
      assert.deepEqual(JSON.parse(String(calls[1].init?.body)), { action: { type: action.toLowerCase() } });
      finishMutation(inviteResponse(acceptedStatus));
      const completed = await settle();
      assert.ok(treeText(completed).includes(action === "Accept" ? "Accepted" : "Declined"));
      assert.deepEqual(navigations, action === "Accept" ? ["/"] : []);
    });
  }

  await runRouteTest({ responses: [inviteResponse()], token: "invalid-token" }, async ({ settle, navigations }) => {
    button(await settle(), "Sign in or create account").props.onClick?.();
    const tree = await settle();
    assert.match(treeText(tree), /authority is temporarily unavailable/);
    assert.equal(buttons(tree).length, 0);
    assert.deepEqual(navigations, []);
  });

  let finishUnmountedAccept!: (response: Response) => void;
  await runRouteTest({
    sessionStatus: "authenticated",
    responses: [inviteResponse(), new Promise<Response>((resolve) => { finishUnmountedAccept = resolve; })],
  }, async ({ settle, unmount, calls, navigations, staleStateUpdates }) => {
    button(await settle(), "Accept").props.onClick?.();
    assert.equal(calls.length, 2);
    unmount();
    finishUnmountedAccept(inviteResponse("accepted"));
    await new Promise<void>((resolve) => setImmediate(resolve));
    assert.deepEqual(navigations, [], "An invite accept completed after unmount must not navigate");
    assert.equal(staleStateUpdates(), 0, "An obsolete invite mutation must not publish state");
  });

  await runContinuationRuntimeTests();
  console.log(`App Team invite route tests passed (${runtimeCases} runtime scenarios)`);
}

void runRuntimeTests().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
