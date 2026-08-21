import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import "sucrase/register/tsx.js";

const { classifyTeamInviteRouteFailure } = require("./App.tsx") as typeof import("./App");

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
  "Sign in with the invited email address, then reopen this link.",
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
