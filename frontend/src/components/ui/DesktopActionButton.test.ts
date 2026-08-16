import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const buttonSource = readFileSync(
  resolve(process.cwd(), "src/components/ui/DesktopActionButton.tsx"),
  "utf8",
);

for (const variant of ["primary", "secondary", "destructive"] as const) {
  assert.match(
    buttonSource,
    new RegExp(`\\b${variant}:`),
    `${variant} variant must exist`,
  );
}

assert.match(buttonSource, /<button\b/, "the primitive must render a native button");
assert.match(buttonSource, /(?:^|\s)h-9(?:\s|$)/, "regular height must be 36px");
assert.doesNotMatch(buttonSource, /(?:^|\s)uppercase(?:\s|$)/);
assert.doesNotMatch(buttonSource, /tracking-\[/, "wide action tracking is forbidden");
assert.match(buttonSource, /focus-visible:(?:ring|outline)/);
assert.match(buttonSource, /disabled:/, "a clear disabled state must exist");
assert.match(
  buttonSource,
  /extends ButtonHTMLAttributes<HTMLButtonElement>/,
  "normal native button attributes must be accepted",
);
assert.match(buttonSource, /type=\{type\}/, "button type must be forwarded");
assert.match(
  buttonSource,
  /\{\.\.\.buttonProps\}/,
  "disabled, onClick, children, and other native attributes must be forwarded",
);

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const confirmationModalSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("function SettingsConfirmationModal"),
  workspaceShellSource.indexOf("function ContextSubmenuTriggerRow"),
);

assert.equal(
  (confirmationModalSource.match(/<DesktopActionButton/g) ?? []).length,
  2,
  "SettingsConfirmationModal must render DesktopActionButton for Cancel and Confirm",
);
assert.doesNotMatch(
  confirmationModalSource,
  /confirmClassName/,
  "SettingsConfirmationModal must not retain the arbitrary class escape hatch",
);
assert.match(
  confirmationModalSource,
  /confirmVariant\?: "primary" \| "destructive"/,
  "confirmVariant must expose only the preserved confirmation semantics",
);
assert.match(
  confirmationModalSource,
  /variant=\{confirmVariant\}/,
  "confirmVariant must control confirm button rendering",
);
