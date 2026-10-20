import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const path = resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx");
const source = readFileSync(path, "utf8");
const start = source.indexOf("function MailboxView({");
const end = source.indexOf("\nfunction ", start + 20);
assert.ok(start > 0 && end > start);
const view = source.slice(start, end);

// Gmail and IMAP continue to share this MailboxView; Microsoft retains its
// separate Graph-oriented view. Visual presentation is the only demo change.
assert.match(source, /activeMicrosoftReadMailbox \? \(/);
assert.match(source, /<MicrosoftMailboxReadOnlyView/);
assert.match(source, /<MailboxView/);
assert.match(view, /data-cuevion-mailbox-visual="unified"/);
assert.match(view, /data-mailbox-toolbar="unified"/);
// Keep Gmail/IMAP Sync at the left of their header; actual shown counts
// are based on the rendered filtered and thread-deduplicated rows.
assert.match(view, /const displayedConversationCount = sortedMessages\.length;/);
assert.match(view, /const displayedUnreadConversationCount = sortedMessages\.filter\([\s\S]*?message\.unread === true/);
const summaryRegion = view.slice(
  view.indexOf("data-mailbox-toolbar-sync"),
  view.indexOf("data-mailbox-toolbar-actions"),
);
assert.ok(
  summaryRegion.indexOf('label={mailboxSyncPresentation.inboxRefreshing') <
  summaryRegion.indexOf('displayedConversationCount'),
  "Sync should stay to the left of the counts",
);
assert.match(summaryRegion, /displayedUnreadConversationCount/);
assert.match(summaryRegion, /border-r border-\[var\(--workspace-border-soft\)\]/);
const filteredRegion = view.slice(
  view.indexOf('onClick={() => switchToFolder("Filtered")}'),
  view.indexOf("onClick={switchToSharedView}"),
);
assert.match(filteredRegion, /border-l-\[var\(--workspace-accent-border\)\]/);
assert.match(filteredRegion, /activeFolder === "Filtered"/);
assert.match(filteredRegion, /dragTargetKey === "folder-Filtered"/);
const sharedRegion = view.slice(
  view.indexOf("onClick={switchToSharedView}"),
  view.indexOf("</button>", view.indexOf("onClick={switchToSharedView}")),
);
assert.match(sharedRegion, /border-l-\[var\(--workspace-accent-border\)\]/);
assert.match(sharedRegion, /isSharedView/);


for (const region of [
  "data-mailbox-toolbar-compose",
  "data-mailbox-toolbar-sync",
  "data-mailbox-toolbar-actions",
]) assert.ok(view.includes(region), "Missing unified toolbar region: " + region);
assert.match(view, /xl:grid-cols-\[152px_minmax\(275px,37%\)_minmax\(0,1fr\)\]/);
assert.match(view, /border-l-\[var\(--workspace-accent-border\)\] bg-\[var\(--workspace-card-subtle\)\]/);
assert.match(view, /data-single-message=\{threadMessages\.length === 1 && density === "split"\}/);
assert.match(view, /\[&_\[data-thread-message-id\]\]:rounded-none/);
assert.match(view, /\[&_\[data-thread-message-actions\]\]:hidden/);
assert.match(view, /text-xl font-semibold tracking-tight text-\[var\(--workspace-text\)\]/);

assert.match(view, /<header[^>]*>[\s\S]*?onClick=\{handleMailboxBack\}[\s\S]*?Back[\s\S]*?className="flex min-w-0 items-center gap-3 text-right"/);
assert.match(view, /<MailboxTitleEditor[\s\S]*?onCommit=\{\(nextTitle\) =>[\s\S]*?onRenameMailbox/);
assert.match(view, /<MailboxConnectionState[\s\S]*?onActionRequired=\{onOpenConnectionSettings\}/);
assert.match(view, /xl:grid-cols-\[152px_minmax\(275px,37%\)_minmax\(0,1fr\)\]/);
assert.match(view, /items-stretch gap-0 overflow-hidden/);
assert.match(view, /border-r border-\[var\(--workspace-border-soft\)\]/);
assert.match(view, /border-l-moss bg-\[var\(--workspace-card-subtle\)\]/);
assert.match(view, />Folders<\/div>/);
assert.match(view, /\["Inbox", "Sent", "Archive", "Drafts", "Trash", "Spam"\]/);

assert.match(view, /min-h-\[92px\] w-full cursor-pointer flex-col rounded-none/);
assert.match(view, /className="cuevion-dark-scroll cuevion-soft-scroll flex min-h-0/);
// Existing provider-aware event handlers and decision controls remain in place.
for (const handler of [
  "handleSelectMessage", "openComposeFromMessage",
  "handleDragStart", "onContextMenu", "archiveSelectedMessages",
  "renderThreadTimeline", "toggleReadingLearningMenu",
]) {
  assert.ok(view.includes(handler), "MailboxView lost handler: " + handler);
}
assert.match(source, /const MAIL_FOLDER_COLUMN_WIDTH = 152;/);
assert.match(source, /const MAIL_SPLIT_GAP = 0;/);
// Hard regression gate: visual relocations must not silently change ANY
// existing Gmail/IMAP JSX action callback or guard/disabled state. These
// digests originate from the pre-styling MailboxView commit b844d9f.
const ts = require("typescript") as typeof import("typescript");
const ast = ts.createSourceFile(
  "WorkspaceShell.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX,
);
let mailboxNode: import("typescript").Node | null = null;
function findMailbox(node: import("typescript").Node): void {
  if (ts.isFunctionDeclaration(node) && node.name?.text === "MailboxView") {
    mailboxNode = node;
    return;
  }
  ts.forEachChild(node, findMailbox);
}
findMailbox(ast);
assert.ok(mailboxNode);
const actionAttributes: string[] = [];
const guardAttributes: string[] = [];
function captureMailboxActionAttributes(node: import("typescript").Node): void {
  if (ts.isJsxAttribute(node)) {
    const name = node.name.text;
    const value = node.initializer?.getText(ast).replace(/\s+/g, " ") ?? "";
    if (/^on[A-Z]/.test(name)) actionAttributes.push(name + ":" + value);
    if (["disabled", "draggable", "href", "aria-haspopup"].includes(name)) {
      guardAttributes.push(name + ":" + value);
    }
  }
  ts.forEachChild(node, captureMailboxActionAttributes);
}
captureMailboxActionAttributes(mailboxNode);
const fingerprint = (values: string[]) => {
  const counts = new Map<string, number>();
  values.forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1));
  return createHash("sha256").update(
    [...counts].map(([key, count]) => key + "=" + count).sort().join("\n"),
  ).digest("hex");
};
assert.equal(actionAttributes.length, 195, "A Gmail/IMAP event binding changed");
assert.equal(guardAttributes.length, 26, "A Gmail/IMAP action guard changed");
assert.equal(fingerprint(actionAttributes),
  "22955684deeed867eb1ae7c1bed5d3853cc22520d037c4cc4273bf58a082b30f",
  "Gmail/IMAP action callback semantics differ from the stable original");
assert.equal(fingerprint(guardAttributes),
  "df2ea7188772200a22777238d0770e9e9d3427ef3e21dc5c752431fa4753dce4",
  "Gmail/IMAP disabled, drag or navigation guards differ from the original");

console.log("Unified Gmail/IMAP visual chrome and existing interaction hooks PASS");
