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
assert.match(ready, /Microsoft 365 · Read-only/);
assert.match(ready, /Select a message to read it/);
assert.match(ready, /Refresh/);
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
assert.doesNotMatch(viewSource, /dangerouslySetInnerHTML/);
const viewExecutableSource = viewSource.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
assert.doesNotMatch(viewExecutableSource, /\b(?:setMailboxStore|saveLiveInboxSnapshot|sendGmailMessage|mutateInboxMessageAction|localStorage)\s*(?:\(|\[|\.)/);

console.log("✓ Microsoft read-only workspace render and authority-isolation tests passed.");
