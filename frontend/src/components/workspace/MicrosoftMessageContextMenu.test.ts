import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "sucrase/register/tsx.js";

const { MicrosoftMessageContextMenu, getMicrosoftContextSubmenuPosition } = require(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMessageContextMenu.tsx"),
) as typeof import("./MicrosoftMessageContextMenu");

const noOp = () => {};
const message = {
  providerMessageId: "right-clicked-graph-id",
  serverMailboxId: "microsoft-owner",
  providerFolder: "INBOX",
  unread: true,
  from: "artist@example.org",
};
const props = {
  message,
  x: 20,
  y: 40,
  busy: false,
  canChangeInbox: true,
  canLearn: true,
  onOpen: noOp,
  onReadState: noOp,
  onArchive: noOp,
  onDelete: noOp,
  onSaveLearning: noOp,
};
const ready = renderToStaticMarkup(createElement(MicrosoftMessageContextMenu, props));
for (const label of [
  "Open", "Reply", "Reply All", "Forward", "Collaboration…", "Flag", "Mark as priority",
  "Mark as read", "Archive", "Delete", "Move to...", "Learning",
]) {
  assert.ok(ready.includes(label), `Missing standard context-menu action: ${label}`);
}
assert.match(ready, /aria-label="Microsoft message context menu"/);
assert.match(ready, /rounded-\[20px\]/);
assert.match(ready, /disabled=""[^>]*>Reply/);
assert.match(ready, /disabled=""[^>]*>Collaboration…/);
assert.doesNotMatch(ready, /disabled=""[^>]*>Mark as read/);
assert.doesNotMatch(ready, /disabled=""[^>]*>Archive/);

const otherFolder = renderToStaticMarkup(createElement(MicrosoftMessageContextMenu, {
  ...props, canChangeInbox: false, canLearn: false,
}));
assert.match(otherFolder, /disabled=""[^>]*>Mark as read/);
assert.match(otherFolder, /disabled=""[^>]*>Archive/);
assert.match(otherFolder, /disabled=""[^>]*>Delete/);
assert.match(otherFolder, /disabled=""[^>]*>Learning/);

// The flyout never expands the main menu or covers the Learning trigger.
// Room on the right opens right; cramped right edges open to the left.
assert.deepEqual(getMicrosoftContextSubmenuPosition({
  mainMenuX: 100, triggerY: 220, viewportWidth: 1100, viewportHeight: 800, expectedHeight: 258,
}), { left: 352, top: 220 });
assert.deepEqual(getMicrosoftContextSubmenuPosition({
  mainMenuX: 500, triggerY: 640, viewportWidth: 900, viewportHeight: 700, expectedHeight: 258,
}), { left: 266, top: 434 });
assert.deepEqual(getMicrosoftContextSubmenuPosition({
  mainMenuX: 500, triggerY: 640, viewportWidth: 900, viewportHeight: 700, expectedHeight: 435,
}), { left: 266, top: 257 });
const menuSource = readFileSync(resolve(__dirname, "MicrosoftMessageContextMenu.tsx"), "utf8");
assert.match(menuSource, /createPortal\(/);
assert.match(menuSource, /data-microsoft-context-submenu/);
assert.match(menuSource, /trigger\.closest\("\[data-theme\]"\)\?\.getAttribute\("data-theme"\)/,
  "Portal flyouts must inherit the active mailbox theme instead of the document's light default");
assert.match(menuSource, /data-microsoft-context-submenu data-theme=\{submenuTheme\}/);
assert.doesNotMatch(menuSource, /onClick=\{\(\) => changeLearning\("show_less"\)\}/,
  "Learning parity: Microsoft must not expose a sender-wide Show Less action absent from Gmail/IMAP");
assert.doesNotMatch(menuSource, /<div role="menu" aria-label="Microsoft move destinations" className="space-y-1 border-l/,
  "Moving to another folder must use a separate flyout, not push Learning down");

const viewSource = readFileSync(resolve(__dirname, "MicrosoftMailboxReadOnlyView.tsx"), "utf8");
assert.match(viewSource, /const contextRow = rowContext[\s\S]*?visibleRows\.find\(\(entry\) => entry\.providerMessageId === rowContext\.id\)/);
assert.match(viewSource, /contextRow\.serverMailboxId === mailbox\.id/);
assert.match(viewSource, /contextRow\.providerFolder === "INBOX"/);
assert.doesNotMatch(viewSource, /canShowLess=\{!isMicrosoftFilteredProtectionActive\(contextRow\)\}/);
assert.match(viewSource, /const runContextInboxAction = useCallback\(async/);
assert.match(viewSource, /target\.serverMailboxId !== mailbox\.id \|\| target\.providerFolder !== "INBOX"/);
assert.match(viewSource, /const activeFolder = folderRef\.current/);
assert.match(viewSource, /ticket !== mutationTicket\.current \|\| folderRef\.current !== activeFolder/);
assert.match(viewSource, /current\?\.providerMessageId === providerMessageId/);
assert.match(viewSource, /if \(selectedIdRef\.current === providerMessageId\)/);
assert.match(viewSource, /onReadState=\{\(\) => \{ void runContextInboxAction\(rowContext\.id, "read"\); \}\}/);
assert.match(viewSource, /onArchive=\{\(\) => \{ void runContextInboxAction\(rowContext\.id, "archive"\); \}\}/);
assert.match(viewSource, /onDelete=\{\(\) => \{ void runContextInboxAction\(rowContext\.id, "delete"\); \}\}/);
assert.match(viewSource, /\[data-microsoft-context-submenu\]/,
  "Clicks in a portal flyout must not dismiss the context menu before the action runs");
assert.doesNotMatch(viewSource, /onOpenContextMenu=\{[\s\S]{0,450}openMessage\(/,
  "Right-click must not open or automatically mark the target as read");

console.log("Microsoft context menu parity and exact row-target security: PASS");
