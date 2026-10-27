import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  microsoftEffectiveListPaneWidth,
  readMicrosoftMailListPaneWidth,
  restoreMicrosoftMessageId,
} from "./microsoftMailboxUiParity";

const rows = [
  { providerMessageId: "immutable-newest" },
  { providerMessageId: "immutable-remembered" },
];
assert.equal(restoreMicrosoftMessageId(rows, "immutable-remembered"), "immutable-remembered");
assert.equal(restoreMicrosoftMessageId(rows, "message-from-other-folder"), "immutable-newest");
assert.equal(restoreMicrosoftMessageId(rows, null), "immutable-newest");
assert.equal(restoreMicrosoftMessageId([], "immutable-remembered"), null);
assert.equal(restoreMicrosoftMessageId([{ providerMessageId: null }], null), null);

// The same desktop widths and clamps as Gmail/IMAP, without changing their
// existing preference or letting the Microsoft pane crowd out the reader.
assert.equal(microsoftEffectiveListPaneWidth(460, 1550), 460);
assert.equal(microsoftEffectiveListPaneWidth(200, 1550), 320);
assert.equal(microsoftEffectiveListPaneWidth(3000, 1100), 548);
assert.equal(readMicrosoftMailListPaneWidth(), 520); // server-side default

const viewer = readFileSync(
  resolve(__dirname, "MicrosoftMailboxReadOnlyView.tsx"), "utf8",
);
const workspace = readFileSync(resolve(__dirname, "WorkspaceShell.tsx"), "utf8");
const helper = readFileSync(resolve(__dirname, "microsoftMailboxUiParity.ts"), "utf8");
assert.match(viewer, /void openMessage\(candidateId, false\)/);
assert.match(viewer, /autoMarkRead &&\s*shouldAutoMarkMicrosoftOpenedMessageRead/);
assert.match(viewer, /lastFolderSelectionRef\.current\[folderRef\.current\]/);
assert.match(viewer, /selectedCallbackRef\.current\?\.\(providerMessageId\)/);
assert.match(viewer, /matchingSplitGridStyle/);
assert.match(workspace, /<MicrosoftMailboxReadOnlyView[\s\S]*?initialSelectedMessageId=\{lastMailboxSelectionRef\.current\[activeMailbox\.id\]/);
assert.match(helper, /cuevion-mail-list-pane-width/);
assert.doesNotMatch(helper, /localStorage\.setItem|sessionStorage|Mail\.Send|fetch\(/);
console.log("Microsoft-only mailbox selection and Gmail/IMAP layout preference parity PASS");
