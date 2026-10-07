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
const sidebarBehaviorSource = readFileSync(
  resolve(process.cwd(), "src/responsiveWorkspaceSidebarBehavior.ts"),
  "utf8",
);

assert.match(
  mainSource,
  /import "\.\/responsiveWorkspaceSidebar\.css";/,
  "the responsive sidebar stylesheet must load with the application shell",
);
assert.match(
  mainSource,
  /installResponsiveWorkspaceSidebarBehavior\(\);/,
  "the responsive sidebar interaction behavior must initialize before the app renders",
);
assert.match(
  sidebarBehaviorSource,
  /event\.detail > 0/,
  "mouse and pointer clicks may auto-collapse while keyboard activation remains focus-driven",
);
assert.match(
  sidebarBehaviorSource,
  /ariaExpanded !== null/,
  "submenu toggles such as Inboxes and Smart Folders must remain expanded while choosing a child",
);
assert.match(
  sidebarBehaviorSource,
  /ariaLabel\?\.startsWith\("Manage "\)/,
  "Smart Folder management controls must not collapse the rail before their menu action",
);
assert.match(
  sidebarBehaviorSource,
  /setAttribute\(COLLAPSED_ATTRIBUTE, "true"\)[\s\S]*?button\.blur\(\)/,
  "a pointer navigation action must collapse the rail immediately and clear pointer focus",
);
assert.match(
  sidebarBehaviorSource,
  /const handlePointerOut[\s\S]*?relatedTarget[\s\S]*?removeAttribute\(COLLAPSED_ATTRIBUTE\)/,
  "the temporary collapse lock must reset only after the pointer leaves the sidebar",
);
assert.match(
  sidebarBehaviorSource,
  /addEventListener\("pointerout", handlePointerOut\)/,
  "the pointer-leave reset handler must be installed on the document boundary",
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
  /:not\(\[data-responsive-sidebar-collapsed="true"\]\):hover/,
  "a pointer navigation action must be able to suppress hover expansion until the pointer exits",
);
assert.match(
  css,
  /:is\(\[data-responsive-sidebar-collapsed="true"\], :not\(:hover\):not\(:focus-within\)\)/,
  "the forced-collapsed state must reuse the normal compact-rail presentation",
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
