import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const appSource = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf8");
const routeSource = readFileSync(
  resolve(process.cwd(), "src/lib/collaborationGuestInviteLink.ts"),
  "utf8",
);
const viewSource = readFileSync(
  resolve(process.cwd(), "src/components/collaboration/ExternalCollaborationGuestView.tsx"),
  "utf8",
);

const appStart = appSource.indexOf("export default function App()");
assert.notEqual(appStart, -1);
const rootApp = appSource.slice(appStart);
const safeRouteRender = rootApp.indexOf("if (collaborationGuestRoute)");
assert.notEqual(safeRouteRender, -1);
assert.ok(safeRouteRender < rootApp.indexOf('if (appRoute === "login")'));
assert.ok(safeRouteRender < rootApp.indexOf('if (appRoute === "preview")'));
assert.ok(safeRouteRender < rootApp.indexOf("return <CuevionApp />"));
assert.match(
  rootApp,
  /if \(collaborationGuestRoute\) \{[\s\S]*?<ExternalCollaborationGuestView[\s\S]*?initialInviteToken=\{collaborationGuestRoute\.token\}/,
);
assert.equal(appSource.includes("parseCollaborationGuestEntryRoute"), true);
assert.match(
  rootApp,
  /if \(!collaborationGuestRoute\) \{[\s\S]*?scrubManagedInboxBrowserStorage\(\);/,
);
assert.match(rootApp, /window\.addEventListener\("hashchange", handleGuestRouteChange\)/);

assert.match(
  appSource,
  /const WorkspaceShell = lazy\(\(\) =>[\s\S]*?import\("\.\/components\/workspace\/WorkspaceShell"\)/,
);
assert.equal(appSource.includes("parseTeamInviteRoute()"), true);
assert.equal(appSource.includes("parseCollaborationInviteRoute()"), true);
assert.equal(appSource.includes("memberSessionProbeRef.current ??= loadStartupSession()"), true);
assert.equal(appSource.includes("OAUTH_CALLBACK_RESULT_STORAGE_KEY"), true);
assert.equal(appSource.includes("<OnboardingFlow"), true);
assert.equal(appSource.includes("<Auth0LoginView />"), true);

assert.equal(routeSource.includes("external_review"), false);
assert.equal(routeSource.includes("collab_invite"), false);
assert.equal(routeSource.includes("URLSearchParams"), false);
assert.equal(routeSource.includes("decodeURIComponent"), false);
assert.equal(viewSource.includes("collaborationApi"), false);
assert.equal(viewSource.includes("external_review"), false);
assert.equal(viewSource.includes("collab_invite"), false);
assert.equal(viewSource.includes("localStorage.setItem"), false);
assert.equal(viewSource.includes("sessionStorage.setItem"), false);
assert.equal(viewSource.includes("document.title"), false);
