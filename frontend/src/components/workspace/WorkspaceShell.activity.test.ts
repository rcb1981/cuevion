import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);

function sourceBetween(start: string, end: string) {
  const startIndex = workspaceShellSource.indexOf(start);
  const endIndex = workspaceShellSource.indexOf(end, startIndex + start.length);

  assert.notEqual(startIndex, -1, `${start} must exist`);
  assert.notEqual(endIndex, -1, `${end} must exist after ${start}`);
  return workspaceShellSource.slice(startIndex, endIndex);
}

const primaryNavigationSource = sourceBetween(
  "const primaryNavigationItems",
  "const utilityNavigationItems",
);
const workspaceSectionSource = sourceBetween(
  "type WorkspaceSection =",
  "type PendingManagedInboxNavigation",
);
const workbenchSectionSource = sourceBetween(
  "type WorkbenchSection =",
  "type UtilitySection",
);
const workbenchViewSource = sourceBetween(
  "function WorkbenchView",
  "const NotificationsSettingsCard",
);
const teamSelectorSource = sourceBetween(
  "{teamTabs.map((tab) => (",
  "{activeTeamTab === \"Members\"",
);
const teamActivitySource = sourceBetween(
  "{visibleTeamActivityItems.length > 0 ? (",
  'No team activity yet.',
);

assert.doesNotMatch(
  primaryNavigationSource,
  /section:\s*"Activity"/,
  "standalone Activity must not be a primary navigation surface",
);
assert.doesNotMatch(
  workspaceSectionSource,
  /\|\s*"Activity"/,
  "standalone Activity must not remain a workspace section",
);
assert.doesNotMatch(
  workbenchSectionSource,
  /"Activity"/,
  "standalone Activity must not remain a workbench section",
);
assert.doesNotMatch(
  workbenchViewSource,
  /Activity:\s*\{|section === "Activity"|Workspace activity|No activity yet\./,
  "the dormant standalone Activity renderer must be removed",
);

assert.match(
  workbenchViewSource,
  /const teamTabs = \["Members", "Collaborations", "Activity"\] as const;/,
  "Team must retain Members, Collaborations, and Activity in that order",
);
assert.match(teamSelectorSource, /role="tab"/);
assert.match(teamSelectorSource, /aria-selected=\{activeTeamTab === tab\}/);
assert.match(teamSelectorSource, /aria-controls="team-panel"/);
assert.match(
  workbenchViewSource,
  /role="tablist"[\s\S]*?aria-label="Team views"/,
  "the Team selector must expose tablist semantics",
);
assert.match(
  workbenchViewSource,
  /role="tabpanel"[\s\S]*?aria-labelledby=\{`team-tab-\$\{activeTeamTab\.toLowerCase\(\)\}`\}/,
  "the active Team view must be linked to its tab",
);
assert.match(
  teamSelectorSource,
  /focus-visible:ring-2/,
  "Team tabs must have a visible keyboard-focus treatment",
);
assert.match(
  teamSelectorSource,
  /activeTeamTab === tab[\s\S]*?workspace-card[\s\S]*?workspace-text[\s\S]*?shadow-/,
  "the active Team tab must remain visually distinguishable",
);

assert.match(
  teamActivitySource,
  /visibleTeamActivityItems\.map\(\(item\) =>[\s\S]*?item\.action \? \([\s\S]*?<button[\s\S]*?onClick=\{item\.action\}/,
  "Team Activity rows must remain content-driven full-row actions",
);
assert.doesNotMatch(
  teamActivitySource,
  /index === 0/,
  "the newest Team Activity row must not receive a false selected state",
);
assert.match(
  teamActivitySource,
  /hover:bg-\[var\(--workspace-surface-hover\)\]/,
  "clickable Team Activity rows must retain their hover treatment",
);
assert.match(
  teamActivitySource,
  /focus-visible:ring-2/,
  "clickable Team Activity rows must expose a clear keyboard-focus treatment",
);
assert.match(teamActivitySource, /\{item\.type\}/);
assert.match(teamActivitySource, /\{item\.title\}/);
assert.match(teamActivitySource, /\{item\.detail\}/);
assert.match(teamActivitySource, /\{item\.time\}/);

assert.match(
  workbenchViewSource,
  /section === "Notifications"[\s\S]*?onClick=\{\(\) => onOpenNotificationItem\(item\)\}/,
  "Notifications must retain its independent renderer and navigation action",
);
assert.match(
  workspaceShellSource,
  /const liveActivityItems = buildVisibleActivityItems\([\s\S]*?onOpenActivityNavigation: handleOpenNotificationNavigation/,
  "Team Activity must retain the collaboration navigation contract",
);
