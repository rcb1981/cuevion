import assert from "node:assert/strict";
import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import "sucrase/register/tsx.js";

const { MicrosoftFilteredSenderAction } = require(
  resolve(process.cwd(), "src/components/workspace/MicrosoftFilteredSenderAction.tsx"),
) as typeof import("./MicrosoftFilteredSenderAction");

const sender = (folder: "Inbox"|"Filtered", disabled = false) =>
  renderToStaticMarkup(createElement(MicrosoftFilteredSenderAction, {
    folder, disabled, onChange: () => {},
  }));
const inbox = sender("Inbox");
const filtered = sender("Filtered");
assert.match(inbox, /Show less from this sender/);
assert.match(filtered, /Keep this sender in Inbox/);
assert.match(inbox, /No message is moved in Outlook/);
assert.match(filtered, /Affects this Microsoft mailbox only/);
assert.match(sender("Inbox", true), /disabled=""/);
assert.doesNotMatch(inbox + filtered, /type="submit"|href=|form=/);

const src = readFileSync(resolve(
  process.cwd(), "src/components/workspace/MicrosoftFilteredSenderAction.tsx",
), "utf8");
assert.match(src, /onChange\(placement\)/);
assert.doesNotMatch(src, /fetch\(|setMicrosoftMessageUnread\(|localStorage/);

const view = readFileSync(resolve(
  process.cwd(), "src/components/workspace/MicrosoftMailboxReadOnlyView.tsx",
), "utf8");
assert.match(view, /selectFolder\("Filtered"\)/);
assert.match(view, /microsoftVisibleFolderRows\(/);
assert.match(view, /useMicrosoftFilteredLearning\(mailbox\.id, connected\)/);
assert.match(view, /fetchMicrosoftInbox\(/);
assert.match(view, /folder: requestedFolder === "Filtered" \? "Inbox" : requestedFolder/);
assert.match(view, /folder: currentFolder === "Filtered" \? "Inbox" : currentFolder/);
assert.match(view, /<MicrosoftFilteredSenderAction/);

// Integration guard: the return-to-Inbox action must not be nested inside
// an Inbox-only JSX guard. The component's own test cannot catch this.
const ts = require("typescript") as typeof import("typescript");
const parsedView = ts.createSourceFile(
  "MicrosoftMailboxReadOnlyView.tsx", view, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX,
);
const senderActionNodes: import("typescript").Node[] = [];
function findSenderActions(node: import("typescript").Node): void {
  if (ts.isJsxSelfClosingElement(node) &&
      node.tagName.getText(parsedView) === "MicrosoftFilteredSenderAction") {
    senderActionNodes.push(node);
  }
  ts.forEachChild(node, findSenderActions);
}
findSenderActions(parsedView);
assert.equal(senderActionNodes.length, 1);
let parent: import("typescript").Node | undefined = senderActionNodes[0].parent;
while (parent) {
  if (ts.isJsxExpression(parent) && parent.expression &&
      ts.isBinaryExpression(parent.expression) &&
      parent.expression.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) {
    assert.notEqual(
      parent.expression.left.getText(parsedView).trim(),
      'folder === "Inbox"',
      "Keep this sender in Inbox must remain reachable from the Filtered folder",
    );
  }
  parent = parent.parent;
}
assert.match(view, /microsoftFiltering\.savePlacement\(message\.from, placement\)/);
assert.match(view, /setPlacementNotice\(/);

const hook = readFileSync(resolve(
  process.cwd(), "src/components/workspace/useMicrosoftFilteredLearning.ts",
), "utf8");
assert.match(hook, /loadStartupSession\(\)/);
assert.match(hook, /session\.status !== "authenticated"/);
assert.match(hook, /session\.authSource !== "auth0"/);
assert.match(hook, /buildLearningStorageKey\(/);
assert.match(hook, /microsoftFilteredStorageKey\(/);
assert.match(hook, /hydrateScopedSenderCategoryLearning\(/);
assert.match(hook, /parseMicrosoftSenderPlacements\(/);
assert.match(hook, /window\.localStorage\.setItem\(current\.storageKey/);
assert.match(hook, /scoped\?\.mailboxId === mailboxId/);
assert.doesNotMatch(hook, /persistScopedSenderCategoryLearning|saveUserAccountConfig|saveLiveInboxSnapshot/);

console.log("Microsoft Filtered actions: read-only Graph, exact scoped learning, visual behavior PASS");
