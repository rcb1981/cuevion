import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);

function componentSource(start: string, end: string) {
  const startIndex = workspaceShellSource.indexOf(start);
  const endIndex = workspaceShellSource.indexOf(end, startIndex + start.length);

  assert.notEqual(startIndex, -1, `${start} must exist`);
  assert.notEqual(endIndex, -1, `${end} must exist after ${start}`);
  return workspaceShellSource.slice(startIndex, endIndex);
}

function desktopActionButtonForHandler(source: string, handler: string) {
  const openingTag = (source.match(/<DesktopActionButton\b[\s\S]*?\n\s*>/g) ?? []).find(
    (candidate) => candidate.includes(`onClick={${handler}}`),
  );

  assert.ok(openingTag, `DesktopActionButton for ${handler} must exist`);
  return openingTag;
}

const settingsComponentsSource = [
  componentSource("const WorkspaceSettingsCard", "const SmartFolderModal"),
  componentSource("const MailSettingsCard", "function createContactRequestId"),
].join("\n");
const workspaceSettingsSource = componentSource(
  "const WorkspaceSettingsCard",
  "function getManagedInboxMissingRequiredFields",
);
const managedInboxEditorSource = componentSource(
  "function ManagedInboxEditor",
  "const ManageInboxesView",
);
const manageInboxesViewSource = componentSource(
  "const ManageInboxesView",
  "const SignatureBlock",
);
const signatureSettingsModalSource = componentSource(
  "const SignatureSettingsModal",
  "const OutOfOfficeSettingsModal",
);
const accountSettingsSource = componentSource(
  "const AccountSettingsCard",
  "function SettingsView",
);
const settingsPageSurfaceSource = componentSource(
  "function settingsPageSurfaceClass",
  "function settingsPillButtonClass",
);
const settingsToggleRowSource = componentSource(
  "const SettingsToggleRow",
  "type SettingsMode",
);
const settingsViewSource = componentSource(
  "function SettingsView",
  "function createContactRequestId",
);

for (const legacyActionClass of [
  "settingsSubtleActionClass",
  "settingsPairedSecondaryActionClass",
  "settingsAccentSecondaryActionClass",
  "settingsGhostActionClass",
  "settingsSecondaryGhostActionClass",
  "settingsPrimaryActionClass",
  "settingsDangerActionClass",
] as const) {
  assert.doesNotMatch(
    settingsComponentsSource,
    new RegExp(`\\b${legacyActionClass}\\b`),
    `${legacyActionClass} must not remain on Settings-owned actions`,
  );
}

for (const variant of ["primary", "secondary", "tertiary"] as const) {
  assert.match(
    workspaceSettingsSource,
    new RegExp(`variant="${variant}"`),
    `Workspace settings must demonstrate the ${variant} hierarchy`,
  );
}

assert.match(
  managedInboxEditorSource,
  /variant="destructive"[\s\S]*size="compact"/,
  "the explicit inbox removal action must retain destructive compact semantics",
);
assert.match(
  settingsComponentsSource,
  /size="compact"/,
  "dense Settings utilities must use the compact action scale",
);

for (const ordinaryActionLabel of [
  "Manage",
  "Close",
  "Cancel",
  "Apply",
  "Save",
  "Add inbox",
  "Move up",
  "Move down",
  "Edit",
  "Set as primary",
  "Remove",
  "Upload image",
  "Reset",
  "Back",
] as const) {
  assert.doesNotMatch(
    settingsComponentsSource,
    new RegExp(`>\\s*${ordinaryActionLabel.toUpperCase()}\\s*<`),
    `${ordinaryActionLabel} must retain source-text title case`,
  );
}

assert.doesNotMatch(
  settingsComponentsSource,
  /<DesktopActionButton[^>]*className="[^"]*(?:uppercase|tracking-\[)/,
  "Settings action call sites must not reintroduce uppercase or wide tracking",
);
assert.match(
  desktopActionButtonForHandler(
    workspaceSettingsSource,
    "handleOpenWorkspaceSettings",
  ),
  /variant="secondary"[^>]*size="compact"|size="compact"[^>]*variant="secondary"/,
  "Workspace Manage must be a compact secondary action",
);
assert.match(
  desktopActionButtonForHandler(accountSettingsSource, "() => setIsManaging(true)"),
  /variant="secondary"[^>]*size="compact"|size="compact"[^>]*variant="secondary"/,
  "Account Manage must be a compact secondary action",
);
assert.doesNotMatch(
  workspaceSettingsSource,
  /navigationCloseBackButtonClass/,
  "Workspace Close must not use the gold navigation action",
);

const connectedInboxListStart = manageInboxesViewSource.indexOf("<aside");
const connectedInboxListEnd = manageInboxesViewSource.indexOf(
  "</aside>",
  connectedInboxListStart,
);
assert.notEqual(connectedInboxListStart, -1, "the connected-inbox list panel must exist");
assert.notEqual(connectedInboxListEnd, -1, "the connected-inbox list panel must be bounded");
const connectedInboxListSource = manageInboxesViewSource.slice(
  connectedInboxListStart,
  connectedInboxListEnd,
);
const contextualAddInboxAction = desktopActionButtonForHandler(
  connectedInboxListSource,
  "handleStartAddInbox",
);
assert.match(
  contextualAddInboxAction,
  /variant="secondary"[^>]*size="compact"|size="compact"[^>]*variant="secondary"/,
  "Add inbox must be a compact secondary action in the connected-inbox list",
);
assert.match(
  contextualAddInboxAction,
  /\bwhitespace-nowrap\b/,
  "the contextual Add inbox label must not wrap",
);
assert.match(
  connectedInboxListSource,
  />\s*\+ Add inbox\s*</,
  "the contextual Add inbox action must retain its label and visible add cue",
);

assert.doesNotMatch(
  settingsPageSurfaceSource,
  /(?:rounded-|\bborder\b|\bbg-|\bshadow)/,
  "the Settings page wrapper must be a layout container rather than another card",
);
assert.match(
  workspaceShellSource,
  /function settingsCardClass[\s\S]*?rounded-\[28px\][\s\S]*?\bborder\b/,
  "actual Settings section cards must retain their boundaries",
);
assert.match(
  connectedInboxListSource,
  /<aside className="[^"]*rounded-\[26px\][^"]*\bborder\b/,
  "the Connected inboxes list must retain its panel boundary",
);

for (const [handler, variant] of [
  ["onCancel", "secondary"],
  ["onSave", "primary"],
] as const) {
  const signatureActionSource = desktopActionButtonForHandler(
    signatureSettingsModalSource,
    handler,
  );
  assert.match(
    signatureActionSource,
    new RegExp(`variant="${variant}"`),
    `Signature ${handler === "onCancel" ? "Cancel" : "Save"} must be a compact ${variant} action`,
  );
  assert.match(
    signatureActionSource,
    /size="compact"/,
    `Signature ${handler === "onCancel" ? "Cancel" : "Save"} must use the 32px compact size`,
  );
}

const goldActionStart = workspaceShellSource.indexOf(
  "const navigationCloseBackButtonClass =",
);
const goldActionEnd = workspaceShellSource.indexOf(
  "const navigationCloseBackButtonDisabledClass =",
  goldActionStart,
);
assert.notEqual(goldActionStart, -1, "the recoverable gold action definition must remain");
assert.notEqual(goldActionEnd, -1, "the gold action definition must remain bounded");
assert.equal(
  createHash("sha256")
    .update(workspaceShellSource.slice(goldActionStart, goldActionEnd).trim())
    .digest("hex"),
  "58b45463591b2a30ada1f7214ce120e117e57dce986982606cf8449295d2f1c0",
  "the exact recoverable gold action definition must remain unchanged",
);

assert.match(settingsViewSource, /role="tablist"/);
assert.match(settingsViewSource, /role="tab"/);
assert.match(settingsViewSource, /aria-selected=\{activeSettingsTab === tab\}/);
assert.match(
  workspaceSettingsSource,
  /aria-pressed=\{appliedMode === option\}/,
  "the selected theme mode must be exposed semantically",
);
assert.match(settingsToggleRowSource, /role="switch"/);
assert.match(settingsToggleRowSource, /aria-checked=\{enabled\}/);

assert.match(
  workspaceShellSource,
  /function settingsPillButtonClass[\s\S]*?focus-visible:ring-2[\s\S]*?\n\}/,
  "Settings pills must have a visible keyboard focus treatment",
);
assert.match(
  workspaceShellSource,
  /function settingsTabButtonClass[\s\S]*?focus-visible:ring-2[\s\S]*?\n\}/,
  "Settings tabs must have a visible keyboard focus treatment",
);
assert.match(
  workspaceShellSource,
  /function settingsToggleButtonClass[\s\S]*?focus-visible:ring-2[\s\S]*?\n\}/,
  "Settings switches must have a visible keyboard focus treatment",
);
