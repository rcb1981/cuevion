import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const css = readFileSync(
  resolve(process.cwd(), "src/responsiveWorkspaceSidebar.css"),
  "utf8",
);
const mainSource = readFileSync(resolve(process.cwd(), "src/main.tsx"), "utf8");
const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);

assert.match(
  mainSource,
  /import "\.\/responsiveWorkspaceSidebar\.css";/,
  "the responsive sidebar stylesheet must load with the application shell",
);
assert.match(
  css,
  /@media \(min-width: 768px\) and \(max-width: 1279px\)/,
  "the compact rail rules must stay scoped between md and xl",
);
const mailboxGridMarker =
  "grid h-0 min-h-0 flex-1 items-stretch gap-6 overflow-hidden";
assert.equal(
  workspaceShellSource.split(mailboxGridMarker).length - 1,
  1,
  "the responsive mailbox layout must remain scoped to one canonical mailbox grid",
);
assert.match(
  css,
  /\.grid\.h-0\.min-h-0\.flex-1\.items-stretch\.gap-6\.overflow-hidden\s*\{[\s\S]*?grid-template-columns:\s*96px minmax\(220px, 0\.9fr\) minmax\(0, 1\.35fr\);[\s\S]*?gap:\s*10px;/,
  "medium-width mailbox content must keep folders, list and reading pane side-by-side",
);
assert.match(
  css,
  /\.grid\.h-0\.min-h-0\.flex-1\.items-stretch\.gap-6\.overflow-hidden:not\([\s\S]*?:has\(> div:nth-child\(3\)\)[\s\S]*?grid-template-columns:\s*minmax\(240px, 0\.9fr\) minmax\(0, 1\.35fr\);/,
  "medium-width Smart Folder views must keep list and reading pane side-by-side",
);
assert.match(
  css,
  /button[\s\S]*?> span\.xl\\:hidden[\s\S]*?> span:last-child[\s\S]*?display: none;/,
  "compact navigation must hide abbreviated text while retaining its icon",
);
assert.match(
  css,
  /:hover,[\s\S]*?:focus-within[\s\S]*?width: 240px;/,
  "mouse and keyboard interaction must temporarily expand the compact rail",
);
assert.match(
  css,
  /\.hidden\.xl\\:flex[\s\S]*?display: flex !important;/,
  "expanded compact navigation must expose mailbox rows that are normally xl-only",
);
assert.match(
  css,
  /\.hidden\.xl\\:inline-flex[\s\S]*?display: inline-flex !important;/,
  "expanded compact navigation must restore full navigation labels and indicators",
);
assert.match(
  css,
  /li:has\(> button\[aria-label="Smart Folders"\]\)[\s\S]*?> ul[\s\S]*?display: none;/,
  "an open Smart Folders tree must not leak labels into the collapsed icon rail",
);
assert.match(
  css,
  /li\[class~="pt-1"\][\s\S]*?button\.hidden\.xl\\:flex[\s\S]*?display: flex !important;/,
  "single-mailbox users must retain a reachable inbox control in the collapsed rail",
);
assert.match(
  css,
  /button\.hidden\.xl\\:flex::before[\s\S]*?mask:/,
  "the single-mailbox compact control must render as an inbox icon rather than clipped text",
);
