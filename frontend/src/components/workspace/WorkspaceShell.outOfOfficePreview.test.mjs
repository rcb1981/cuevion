import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const source = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const start = source.indexOf("export function OutOfOfficeSettingsPreview()");
const end = source.indexOf("const SmartFolderModal", start);
assert.ok(start >= 0 && end > start, "OOO preview component must exist");
const preview = source.slice(start, end);

assert.match(preview, /Review-only Out of Office\. Nothing is saved and no email is sent\./);
assert.match(preview, /<OutOfOfficeSettingsModal/);
assert.match(preview, /demo@hysteriarecs\.com/);
assert.doesNotMatch(preview, /fetch\s*\(/);
assert.doesNotMatch(preview, /loadOutOfOfficeSettings\s*\(/);
assert.doesNotMatch(preview, /saveOutOfOfficeSettings\s*\(/);
assert.doesNotMatch(preview, /\/api\/out-of-office/);
