import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const sidebarStart = workspaceShellSource.indexOf("function WorkspaceSidebar(");
const sidebarEnd = workspaceShellSource.indexOf("function TopCards(", sidebarStart);

assert.notEqual(sidebarStart, -1, "WorkspaceSidebar must remain present");
assert.notEqual(sidebarEnd, -1, "WorkspaceSidebar must remain structurally bounded");

const sidebarSource = workspaceShellSource.slice(sidebarStart, sidebarEnd);

assert.match(
  sidebarSource,
  /flex h-9 w-full items-center justify-center rounded-xl/,
  "ordinary desktop navigation must retain the compact 36px row geometry",
);
assert.match(
  sidebarSource,
  /hidden h-8 w-full items-center justify-between gap-3 rounded-xl/,
  "connected inbox rows must retain the compact 32px nested geometry",
);
assert.match(
  sidebarSource,
  /mb-3 rounded-\[18px\] border[\s\S]*?p-2[\s\S]*?aria-label="Organizer"/,
  "Organizer must retain its compact, bordered module treatment",
);
assert.match(
  sidebarSource,
  /overflow-y-auto/,
  "the desktop sidebar must retain its independent vertical scrolling",
);
assert.doesNotMatch(
  sidebarSource,
  /Contact|Log out|Version 1\.0\.0/,
  "Contact, Log out, and the version label must stay out of the permanent desktop sidebar",
);

const primaryNavigationSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("const primaryNavigationItems = ["),
  workspaceShellSource.indexOf("] as const;", workspaceShellSource.indexOf("const primaryNavigationItems = [")),
);
assert.deepEqual(
  [...primaryNavigationSource.matchAll(/label: "([^"]+)"/g)].map((match) => match[1]),
  ["Dashboard", "For You", "Priority", "Inboxes", "Notifications", "Team"],
  "primary navigation labels and order must remain unchanged",
);

const utilityNavigationSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("const utilityNavigationItems = ["),
  workspaceShellSource.indexOf("] as const;", workspaceShellSource.indexOf("const utilityNavigationItems = [")),
);
assert.deepEqual(
  [...utilityNavigationSource.matchAll(/label: "([^"]+)"/g)].map((match) => match[1]),
  ["Settings", "Help"],
  "utility navigation labels and order must remain unchanged",
);
assert.match(sidebarSource, /aria-label="Organizer"[\s\S]*?utilityNavigationItems\.map\(renderItem\)/);

const accountSettingsSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("const AccountSettingsCard"),
  workspaceShellSource.indexOf("type SettingsTab", workspaceShellSource.indexOf("const AccountSettingsCard")),
);
assert.match(
  accountSettingsSource,
  /actionLabel="Contact support"[\s\S]*?onActionClick=\{onOpenContact\}/,
  "Contact must remain reachable from Settings Account through the existing destination callback",
);
assert.match(
  accountSettingsSource,
  /onClick=\{onLogoutClick\}[\s\S]*?>\s*Log out\s*</,
  "Log out must remain reachable from Settings Account through the existing callback",
);

const settingsUsageSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf('activeSection === "Settings"'),
  workspaceShellSource.indexOf('activeSection === "Help"', workspaceShellSource.indexOf('activeSection === "Settings"')),
);
assert.match(
  settingsUsageSource,
  /onOpenContact=\{\(\) => handleChangeSection\("Contact"\)\}/,
  "Settings Contact must reuse the existing Contact section navigation",
);
assert.match(
  settingsUsageSource,
  /onLogoutClick=\{\(\) => setIsLogoutConfirmationOpen\(true\)\}/,
  "Settings Log out must reuse the existing logout confirmation flow",
);
