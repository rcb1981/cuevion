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
assert.match(ready, /Auto-read enabled · other actions disabled/);
assert.doesNotMatch(ready, />Read-only pilot</);
assert.doesNotMatch(ready, />New message<|>Reply<|>Mark as read<|>Mark as unread</);
assert.match(ready, /Select a message to read it/);
assert.match(ready, /Page 1 of up to 4/);
assert.match(ready, /Newer messages/);
assert.match(ready, /Older messages/);

assert.match(ready, /Refresh/);
assert.match(ready, /aria-pressed="true"[^>]*>Inbox<\/button>/);
assert.match(ready, /aria-pressed="false"[^>]*>Sent<\/button>/);
assert.match(ready, /aria-pressed="false"[^>]*>Archive<\/button>/);
assert.match(ready, /aria-pressed="false"[^>]*>Drafts<\/button>/);
assert.match(ready, /aria-pressed="false"[^>]*>Deleted Items<\/button>/);
assert.match(ready, /aria-pressed="false"[^>]*>Junk Email<\/button>/);
assert.match(ready, /Back to workspace/);
assert.match(ready, /Compose/);
assert.match(ready, /Compose message \(design preview only\)/);
assert.doesNotMatch(ready, /Send message|Reply all|Trash/);

const microsoftSource = readFileSync(
  resolve(__dirname, "MicrosoftMailboxReadOnlyView.tsx"), "utf8",
);
assert.match(microsoftSource, /fetchMicrosoftAttachments/);
assert.match(microsoftSource, /fetchMicrosoftArchive/);
assert.match(microsoftSource, /fetchMicrosoftArchiveMessage/);
assert.match(microsoftSource, /fetchMicrosoftDrafts/);
assert.match(microsoftSource, /fetchMicrosoftDraftMessage/);
assert.match(microsoftSource, /fetchMicrosoftDeleted/);
assert.match(microsoftSource, /fetchMicrosoftDeletedMessage/);
assert.match(microsoftSource, /fetchMicrosoftJunk/);
assert.match(microsoftSource, /fetchMicrosoftJunkMessage/);
assert.match(microsoftSource, /folder === "Junk" \? part/);
assert.match(microsoftSource, /folderRef.current === "Junk"/);
assert.match(microsoftSource, /Download disabled for junk mail/);
assert.match(microsoftSource, /Junk Email is read-only/);
assert.match(microsoftSource, /Links in junk mail are displayed as plain text/);
assert.match(microsoftSource, /<MicrosoftMailboxFolderRail/);
assert.match(microsoftSource, /onSelectFolder=\{selectFolder\}/);
assert.match(microsoftSource, /lg:grid-cols-\[152px_minmax/);
assert.match(microsoftSource, /<MicrosoftPlannedMessageActions\s+unread=\{selectedMessage\?\.unread === true\}/);
assert.match(microsoftSource, /hasMessage=\{Boolean\(selectedId && selectedMessage && !messageBusy\)\}/);
assert.match(microsoftSource, /<MicrosoftMessageListControls/);
assert.match(microsoftSource, /composeAction=\{composeAction\}/);
const folderRailPos = microsoftSource.indexOf("<MicrosoftMailboxFolderRail");
const listControlsPos = microsoftSource.indexOf("<MicrosoftMessageListControls");
const paneToolbarPos = microsoftSource.indexOf('aria-label="Microsoft selected message actions"');
const messageActionsPos = microsoftSource.indexOf("<MicrosoftPlannedMessageActions");
const messageBodyPos = microsoftSource.indexOf('aria-label="Selected Microsoft message"');
assert.ok(folderRailPos > 0 && folderRailPos < listControlsPos,
  "Compose and folders precede the message-list filters");
assert.ok(listControlsPos < paneToolbarPos && paneToolbarPos < messageActionsPos && messageActionsPos < messageBodyPos,
  "All message actions belong in the right read pane, above the message body");
assert.equal((microsoftSource.match(/<MicrosoftPlannedMessageActions/g) ?? []).length, 1,
  "Do not duplicate message actions in the global or list toolbar");

assert.match(microsoftSource, /Deleted Items are read-only/);
assert.match(microsoftSource, /Last changed:/);
assert.match(microsoftSource, /folder === "Drafts"/);
assert.match(microsoftSource, /Drafts are read-only/);
assert.match(microsoftSource, /nextFolder: MicrosoftFolder/);
assert.match(microsoftSource, /attachmentDownloadTicket\.current \+= 1/);
assert.match(microsoftSource, /attachmentRequest !== attachmentTicket.current/);
assert.match(microsoftSource, /selectedIdRef.current !== providerMessageId/);
assert.match(microsoftSource, /downloadMicrosoftAttachmentFile/);
assert.match(microsoftSource, /item\.kind === "file"/);
assert.match(microsoftSource, /item\.size <= 2 \* 1024 \* 1024/);
assert.match(microsoftSource, /application\/octet-stream/);
assert.match(microsoftSource, /link\.download = result\.fileName/);
assert.match(microsoftSource, /currentMessageTicket !== messageTicket\.current/);
assert.match(microsoftSource, /Read-only downloads of file attachments up to 2 MB/);
assert.doesNotMatch(ready, /Download file|Download attachment/);

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
assert.match(viewSource, /const MICROSOFT_READ_ONLY_PILOT = true/);
assert.match(viewSource, /const MICROSOFT_AUTO_MARK_READ_RELEASED = true/);
assert.match(viewSource, /MICROSOFT_READ_ONLY_PILOT \|\| !connected/);
assert.match(viewSource, /<MicrosoftComposeDesignPreview/);
assert.match(viewSource, /setComposeDesignPreviewOpen\(true\)/);
assert.match(viewSource, /!MICROSOFT_READ_ONLY_PILOT && composer !== null/);
assert.match(viewSource, /!MICROSOFT_READ_ONLY_PILOT && \(/);
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
assert.match(viewSource, /isMicrosoftInboxProjection\(folder\) && \(/);
assert.match(viewSource, /folder === "Sent" \|\| folder === "Drafts"/);
assert.match(viewSource, /setMicrosoftMessageUnread\(/);
assert.match(viewSource, /onClick=\{\(\) => void changeReadState\(!selectedMessage\.unread\)\}/);
assert.match(viewSource, /mutationInFlight\.current/);
assert.match(viewSource, /mutationTicket\.current/);
assert.match(viewSource, /applyConfirmedMicrosoftUnread\(/);
assert.ok(
  viewSource.includes("!isMicrosoftInboxProjection(folderRef.current)"),
  "A confirmed auto-read of a virtual Filtered message must update the visible read state",
);
assert.ok(
  viewSource.includes('{isMicrosoftInboxProjection(folder) && ('),
  "Auto-read feedback must be visible in both Inbox and Filtered",
);
assert.ok(
  viewSource.includes('{folder === "Inbox" && !MICROSOFT_READ_ONLY_PILOT && ('),
  "Reply and manual flag actions must not become available in Filtered",
);
assert.match(viewSource, /Opening a message does not change its read status/);
assert.match(viewSource, /setMutationConfirmation\(nextUnread \? "Marked as unread\." : "Marked as read\."\)/);
assert.match(viewSource, /setMutationError\(/);
assert.doesNotMatch(viewSource, /dangerouslySetInnerHTML/);
const viewExecutableSource = viewSource.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
assert.doesNotMatch(viewExecutableSource, /\b(?:setMailboxStore|saveLiveInboxSnapshot|sendGmailMessage|mutateInboxMessageAction|localStorage)\s*(?:\(|\[|\.)/);

console.log("✓ Microsoft read-only workspace render and authority-isolation tests passed.");
