import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const app = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf8");

assert.ok(
  app.includes("function isOutOfOfficePreviewRoute()") &&
    app.includes('"/out-of-office-preview"'),
  "the review-only OOO route must have an explicit path",
);
assert.match(
  app,
  /function resolveReviewPreviewKind\(\)[\s\S]*?isOutOfOfficePreviewRoute\(\)[\s\S]*?"out-of-office"/,
  "OOO must reuse the review-preview route boundary",
);
assert.match(
  app,
  /appRoute === "preview" && session\.status === "unavailable"[\s\S]*?<ReviewPreviewRoute/,
  "OOO preview must remain available when Preview Auth0 is unavailable",
);

const memberIndex = app.indexOf('session.user.workspaceRole === "member"');
const unavailablePreviewIndex = app.indexOf('appRoute === "preview" && session.status === "unavailable"');
assert.ok(memberIndex >= 0 && unavailablePreviewIndex > memberIndex, "team member routing must remain before review preview routing");

assert.doesNotMatch(
  app,
  /cuevion-k7cspbgrj|vercel\.app.*forbidden_host/,
  "Preview review access must not be implemented by loosening host allowlists",
);
