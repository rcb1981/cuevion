import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const source = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf8");

const appStart = source.indexOf("export default function App()");
assert.notEqual(appStart, -1);
const initializer = source.indexOf("consumeTesterInviteRoute()", appStart);
const continuationInitializer = source.indexOf(
  "parseTeamInviteContinuationRoute()",
  appStart,
);
assert.ok(initializer > appStart);
assert.ok(initializer < continuationInitializer);

const testerGate = source.indexOf("if (testerInviteRoute)", appStart);
const loginGate = source.indexOf('if (appRoute === "login")', appStart);
assert.ok(testerGate > appStart);
assert.ok(testerGate < loginGate);

const viewStart = source.indexOf("export function TesterInviteRouteView");
assert.notEqual(viewStart, -1);
const viewEnd = source.indexOf("export function TeamInviteRouteView", viewStart);
const view = source.slice(viewStart, viewEnd);
assert.match(view, /fetchTesterInvite\(route\.inviteToken!\)/);
assert.match(view, /startTesterInviteAuthentication\(route\.inviteToken\)/);
assert.doesNotMatch(view, /localStorage|sessionStorage/);

console.log("App tester invite access tests passed");
