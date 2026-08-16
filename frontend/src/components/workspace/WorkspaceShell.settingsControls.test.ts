import assert from "node:assert/strict";
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
