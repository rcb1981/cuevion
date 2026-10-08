import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const source = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);

assert.match(
  source,
  /loadOutOfOfficeSettings,\s*saveOutOfOfficeSettings/,
  "Workspace settings must use the server-authoritative OOO API",
);
assert.match(
  source,
  /showOutOfOfficeSettings=\{!isDemoWorkspace\}/,
  "OOO settings must be enabled in live workspaces rather than demo-only",
);
assert.match(
  source,
  /loadOutOfOfficeSettings\(inboxId, controller\.signal\)/,
  "OOO status must hydrate from the server per mailbox",
);
assert.match(
  source,
  /saveOutOfOfficeSettings\(inboxId, \{[\s\S]*?subject:[\s\S]*?message:/,
  "OOO modal must save the server draft rather than mutate local state only",
);
assert.match(source, /type="datetime-local"/, "OOO must expose scheduling controls");
assert.match(source, /Subject/, "OOO must expose a subject field");
assert.match(source, /24-hour period/, "OOO must explain sender suppression behavior");
assert.doesNotMatch(
  source,
  /cuevion-mail-out-of-office|cuevion-out-of-office-reply-log|OUT_OF_OFFICE_SUPPRESSION_WINDOW_MS/,
  "OOO must not persist or suppress replies in browser localStorage",
);
assert.doesNotMatch(
  source,
  /autoReplyId = `\$\{mailbox\.id\}-ooo-/,
  "WorkspaceShell must not synthesize fake Sent auto-replies",
);
