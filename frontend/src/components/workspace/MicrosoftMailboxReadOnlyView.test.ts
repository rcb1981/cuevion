import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "sucrase/register/tsx.js";

const { MicrosoftMailboxReadOnlyView } = require(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMailboxReadOnlyView.tsx"),
) as typeof import("./MicrosoftMailboxReadOnlyView");

const mailbox = {
  id: "microsoft-0123456789abcdefabcd12345678",
  title: "Microsoft Work",
  email: "owner@example.com",
};
const noOp = () => {};

const ready = renderToStaticMarkup(createElement(MicrosoftMailboxReadOnlyView, {
  mailbox,
  connected: true,
  onBack: noOp,
  onOpenConnectionSettings: noOp,
}));
assert.match(ready, /Microsoft Work/);
assert.match(ready, /owner@example\.com/);
assert.match(ready, /Microsoft 365 · Mail/);
assert.match(ready, /Select a message to read it/);
assert.match(ready, /Refresh/);
assert.match(ready, /aria-pressed="true"[^>]*>Inbox<\/button>/);
assert.match(ready, /aria-pressed="false"[^>]*>Sent<\/button>/);
assert.match(ready, /Back to workspace/);
assert.doesNotMatch(ready, /Send message|Reply all|Compose|Trash/);

const disconnected = renderToStaticMarkup(createElement(MicrosoftMailboxReadOnlyView, {
  mailbox,
  connected: false,
  onBack: noOp,
  onOpenConnectionSettings: noOp,
}));
assert.match(disconnected, /Microsoft mailbox not connected/);
assert.match(disconnected, /Connection settings/);
assert.doesNotMatch(disconnected, /Select a message to read it/);

// Verify that the normal WorkspaceShell owns the provider decision, but keeps
// all Gmail/IMAP provider refresh and mutations in their original view.
const shellSource = readFileSync(resolve(__dirname, "WorkspaceShell.tsx"), "utf8");
assert.match(shellSource, /activeMicrosoftReadMailbox =[\s\S]*?hasAuthenticatedMemberAuthority[\s\S]*?item\.provider === "microsoft"/);
assert.match(shellSource, /\{activeMicrosoftReadMailbox \? \([\s\S]*?<MicrosoftMailboxReadOnlyView[\s\S]*?\) : \([\s\S]*?<MailboxView/);

const viewSource = readFileSync(resolve(__dirname, "MicrosoftMailboxReadOnlyView.tsx"), "utf8");
assert.match(viewSource, /fetchMicrosoftInbox\(/);
assert.match(viewSource, /fetchMicrosoftMessage\(/);
assert.match(viewSource, /const ticket = \+\+inboxTicket\.current/);
assert.match(viewSource, /const ticket = \+\+messageTicket\.current/);
assert.match(viewSource, /inboxTicket\.current \+= 1/);
assert.match(viewSource, /messageTicket\.current \+= 1/);
assert.match(viewSource, /selectedIdRef\.current !== providerMessageId/);
assert.match(viewSource, /response\.error\?\.code === "microsoft_reconnect_required"/);
assert.match(viewSource, /fetchMicrosoftSent\(/);
assert.match(viewSource, /fetchMicrosoftSentMessage\(/);
assert.match(viewSource, /const selectFolder = useCallback/);
assert.match(viewSource, /folderRef\.current = nextFolder/);
assert.match(viewSource, /folderRef\.current !== requestedFolder/);
assert.match(viewSource, /folderRef\.current !== "Inbox"/);
assert.match(viewSource, /folder === "Inbox" && \(/);
assert.match(viewSource, /folder === "Sent" \? "To: " \+ selectedMessage\.to/);
assert.match(viewSource, /setMicrosoftMessageUnread\(/);
assert.match(viewSource, /onClick=\{\(\) => void changeReadState\(!selectedMessage\.unread\)\}/);
assert.match(viewSource, /mutationInFlight\.current/);
assert.match(viewSource, /mutationTicket\.current/);
assert.match(viewSource, /applyConfirmedMicrosoftUnread\(/);
assert.match(viewSource, /Opening a message does not change its read status/);
assert.match(viewSource, /setMutationConfirmation\(nextUnread \? "Marked as unread\." : "Marked as read\."\)/);
assert.match(viewSource, /setMutationError\(/);
assert.doesNotMatch(viewSource, /dangerouslySetInnerHTML/);
const viewExecutableSource = viewSource.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
assert.doesNotMatch(viewExecutableSource, /\b(?:setMailboxStore|saveLiveInboxSnapshot|sendGmailMessage|mutateInboxMessageAction|localStorage)\s*(?:\(|\[|\.)/);

console.log("✓ Microsoft read-only workspace render and authority-isolation tests passed.");
