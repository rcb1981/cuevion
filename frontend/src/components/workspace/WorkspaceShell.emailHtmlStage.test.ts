import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const emailHtmlStageSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("function EmailHtmlStage("),
  workspaceShellSource.indexOf("const allowedImportedEmailInlineStyleProperties"),
);

assert.ok(emailHtmlStageSource.length > 0, "EmailHtmlStage source must be present");
assert.match(
  emailHtmlStageSource,
  /height:\s*"clamp\(320px, 50dvh, 600px\)"/,
  "rich external HTML must use the bounded responsive stage height",
);
assert.match(
  emailHtmlStageSource,
  /maxHeight:\s*"calc\(100dvh - 96px\)"/,
  "very small viewports must override the normal 320px minimum",
);
assert.doesNotMatch(
  emailHtmlStageSource,
  /fallbackHeight|measureContentHeight|updateHeight|scheduleMeasure|scrollHeight|iframeDoc\.images|handleImageEvent|\b2200\b/,
  "opaque rich HTML must not retain content-height measurement or its 2200px fallback authority",
);
assert.match(
  workspaceShellSource,
  /overflow-y:\s*auto !important/,
  "long rich HTML must retain native vertical scrolling inside the iframe document",
);

const sandboxMatch = emailHtmlStageSource.match(/sandbox="([^"]+)"/);
assert.ok(sandboxMatch, "EmailHtmlStage must declare an iframe sandbox");
const sandboxTokens = sandboxMatch[1].split(/\s+/).sort();
assert.deepEqual(sandboxTokens, [
  "allow-popups",
  "allow-popups-to-escape-sandbox",
]);
assert.ok(!sandboxTokens.includes("allow-same-origin"));
assert.ok(!sandboxTokens.includes("allow-scripts"));

const resolveStageHeight = (viewportHeight: number) =>
  Math.min(Math.min(600, Math.max(320, viewportHeight * 0.5)), viewportHeight - 96);

[
  { viewport: "1920x1080", viewportHeight: 1080, expected: 540 },
  { viewport: "1440x900", viewportHeight: 900, expected: 450 },
  { viewport: "1280x720", viewportHeight: 720, expected: 360 },
  { viewport: "1024x700", viewportHeight: 700, expected: 350 },
  { viewport: "480x360", viewportHeight: 360, expected: 264 },
].forEach(({ viewport, viewportHeight, expected }) => {
  assert.equal(
    resolveStageHeight(viewportHeight),
    expected,
    `${viewport} must resolve to a ${expected}px email stage`,
  );
});
