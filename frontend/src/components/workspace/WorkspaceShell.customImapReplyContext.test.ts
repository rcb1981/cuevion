import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  buildGmailReplyContext,
  buildImapReplyContext,
  buildSendInboxWireRequest,
} from "../../lib/inboxConnectionApi";
import { selectLatestEligibleAuthoritativeConversationMessage } from "../../lib/inboxEngine";

const authoritativeImapSource = {
  serverMailboxId: "imap-mailbox-1",
  providerFolder: "INBOX",
  imapUid: "42",
  uidValidity: "900",
  threadIdentityContext: {
    provider: "custom_imap",
    mailboxId: "imap-mailbox-1",
  },
};

for (const composeMode of ["reply", "reply_all"] as const) {
  assert.deepEqual(
    buildImapReplyContext({
      sendProvider: "custom_imap",
      composeMode,
      mailboxId: "imap-mailbox-1",
      sourceMessage: authoritativeImapSource,
    }),
    {
      sourceProviderFolder: "INBOX",
      sourceImapUid: "42",
      sourceUidValidity: "900",
    },
  );
}

const physicalImapSource = {
  ...authoritativeImapSource,
  threadIdentityContext: undefined,
};
assert.deepEqual(
  buildImapReplyContext({
    sendProvider: "custom_imap",
    composeMode: "reply",
    mailboxId: "imap-mailbox-1",
    sourceMessage: physicalImapSource,
  }),
  {
    sourceProviderFolder: "INBOX",
    sourceImapUid: "42",
    sourceUidValidity: "900",
  },
  "the server mailbox envelope is sufficient when thread context is absent",
);

assert.deepEqual(
  buildImapReplyContext({
    sendProvider: "custom_imap",
    composeMode: "reply",
    mailboxId: "imap-mailbox-1",
    sourceMessage: {
      ...authoritativeImapSource,
      providerFolder: "Archive/2026",
    },
  }),
  {
    sourceProviderFolder: "Archive/2026",
    sourceImapUid: "42",
    sourceUidValidity: "900",
  },
);

for (const composeMode of ["new", "forward"] as const) {
  assert.equal(
    buildImapReplyContext({
      sendProvider: "custom_imap",
      composeMode,
      mailboxId: "imap-mailbox-1",
      sourceMessage: authoritativeImapSource,
    }),
    undefined,
  );
}

assert.equal(
  buildImapReplyContext({
    sendProvider: "google",
    composeMode: "reply",
    mailboxId: "imap-mailbox-1",
    sourceMessage: authoritativeImapSource,
  }),
  undefined,
);

const invalidImapSources = [
  { ...authoritativeImapSource, serverMailboxId: undefined },
  { ...authoritativeImapSource, serverMailboxId: "imap-mailbox-2" },
  { ...authoritativeImapSource, providerFolder: undefined },
  { ...authoritativeImapSource, providerFolder: " INBOX" },
  { ...authoritativeImapSource, providerFolder: "INBOX\r\nInjected" },
  { ...authoritativeImapSource, imapUid: undefined },
  { ...authoritativeImapSource, imapUid: "0" },
  { ...authoritativeImapSource, imapUid: "4294967296" },
  { ...authoritativeImapSource, uidValidity: undefined },
  { ...authoritativeImapSource, uidValidity: "0" },
  {
    ...authoritativeImapSource,
    threadIdentityContext: {
      provider: "google",
      mailboxId: "imap-mailbox-1",
    },
  },
  {
    ...authoritativeImapSource,
    threadIdentityContext: {
      provider: "custom_imap",
      mailboxId: "imap-mailbox-2",
    },
  },
] as const;

for (const sourceMessage of invalidImapSources) {
  assert.equal(
    buildImapReplyContext({
      sendProvider: "custom_imap",
      composeMode: "reply",
      mailboxId: "imap-mailbox-1",
      sourceMessage,
    }),
    undefined,
  );
}

for (const mailboxId of ["", " imap-mailbox-1"] as const) {
  assert.equal(
    buildImapReplyContext({
      sendProvider: "custom_imap",
      composeMode: "reply",
      mailboxId,
      sourceMessage: {
        ...authoritativeImapSource,
        serverMailboxId: mailboxId,
      },
    }),
    undefined,
  );
}

assert.equal(
  buildImapReplyContext({
    sendProvider: "custom_imap",
    composeMode: "reply",
    mailboxId: "imap-mailbox-1",
    sourceMessage: {
      serverMailboxId: "imap-mailbox-1",
      threadId: "imap:rfc:browser-local-root",
      rfcMessageId: "browser-local-root@example.com",
      threadIdentityAuthority: "rfc",
      signal: "Sent",
    } as any,
  }),
  undefined,
  "a synthetic Sent row or RFC-only browser identity is not a provider locator",
);

assert.equal(
  buildImapReplyContext({
    sendProvider: "custom_imap",
    composeMode: "reply",
    mailboxId: "imap-mailbox-1",
    sourceMessage: {
      id: "imap-uid-42",
      serverMailboxId: "imap-mailbox-1",
      threadIdentityContext: {
        provider: "custom_imap",
        mailboxId: "imap-mailbox-1",
        folder: "INBOX",
        uidValidity: "900",
      },
    } as any,
  }),
  undefined,
  "local IDs and thread context must not substitute for the physical provider locator",
);

const providerBackedConversationSource = {
  id: "imap-uid-42",
  ...authoritativeImapSource,
  threadId: "imap:rfc:root%40example.com",
  threadIdentityAuthority: "rfc" as const,
  subject: "Re: Release timing",
  from: "Partner <partner@example.com>",
  to: "owner@example.com",
  createdAt: "2026-08-24T08:00:00.000Z",
  timestamp: "2026-08-24T08:00:00.000Z",
};
const syntheticSentConversationSource = {
  id: "imap-mailbox-1-sent-1",
  threadId: providerBackedConversationSource.threadId,
  threadIdentityAuthority: "rfc" as const,
  subject: "Re: Release timing",
  from: "owner@example.com",
  to: "Partner <partner@example.com>",
  cc: "Team <team@example.com>",
  signal: "Sent",
  createdAt: "2026-08-24T09:00:00.000Z",
  timestamp: "2026-08-24T09:00:00.000Z",
};
const differentConversationSource = {
  ...providerBackedConversationSource,
  id: "imap-uid-99",
  imapUid: "99",
  threadId: "imap:rfc:other-root%40example.com",
  createdAt: "2026-08-24T10:00:00.000Z",
  timestamp: "2026-08-24T10:00:00.000Z",
};
const differentMailboxSource = {
  ...providerBackedConversationSource,
  id: "imap-uid-100",
  serverMailboxId: "imap-mailbox-2",
  imapUid: "100",
  threadIdentityContext: {
    provider: "custom_imap",
    mailboxId: "imap-mailbox-2",
  },
  createdAt: "2026-08-24T11:00:00.000Z",
  timestamp: "2026-08-24T11:00:00.000Z",
};
const heuristicLocatorCollision = {
  ...providerBackedConversationSource,
  id: "imap-uid-43",
  imapUid: "43",
  threadIdentityAuthority: "heuristic" as const,
  createdAt: "2026-08-24T12:00:00.000Z",
  timestamp: "2026-08-24T12:00:00.000Z",
};
const unknownDateSyntheticSource = {
  ...syntheticSentConversationSource,
  id: "imap-mailbox-1-sent-unknown-date",
  createdAt: undefined,
  timestamp: undefined,
};
const isEligibleImapReplyAnchor = (candidate: any) =>
  Boolean(
    buildImapReplyContext({
      sendProvider: "custom_imap",
      composeMode: "reply",
      mailboxId: "imap-mailbox-1",
      sourceMessage: candidate,
    }),
  );
const resolvedReplyAfterSentAnchor =
  selectLatestEligibleAuthoritativeConversationMessage(
    syntheticSentConversationSource,
    [
      syntheticSentConversationSource,
      differentConversationSource,
      differentMailboxSource,
      heuristicLocatorCollision,
      unknownDateSyntheticSource,
      providerBackedConversationSource,
    ],
    "imap-mailbox-1",
    isEligibleImapReplyAnchor,
  );

assert.equal(
  resolvedReplyAfterSentAnchor.id,
  providerBackedConversationSource.id,
  "a synthetic Sent row may use only a provider-backed anchor from its canonical mailbox conversation",
);
assert.deepEqual(
  buildImapReplyContext({
    sendProvider: "custom_imap",
    composeMode: "reply",
    mailboxId: "imap-mailbox-1",
    sourceMessage: resolvedReplyAfterSentAnchor,
  }),
  {
    sourceProviderFolder: "INBOX",
    sourceImapUid: "42",
    sourceUidValidity: "900",
  },
  "the second send uses the original physical IMAP locator without fabricating Sent identity",
);

const genuinelyUnanchoredLocalSource = {
  ...syntheticSentConversationSource,
  id: "local-only",
  threadId: "browser-local-thread",
  threadIdentityAuthority: "heuristic" as const,
};
assert.equal(
  selectLatestEligibleAuthoritativeConversationMessage(
    genuinelyUnanchoredLocalSource,
    [genuinelyUnanchoredLocalSource, providerBackedConversationSource],
    "imap-mailbox-1",
    isEligibleImapReplyAnchor,
  ),
  genuinelyUnanchoredLocalSource,
  "a local-only message cannot borrow an unrelated provider locator",
);
assert.equal(
  buildImapReplyContext({
    sendProvider: "custom_imap",
    composeMode: "reply",
    mailboxId: "imap-mailbox-1",
    sourceMessage: genuinelyUnanchoredLocalSource,
  }),
  undefined,
  "the provider safety guard still has no context for a genuinely unanchored message",
);

const imapWireRequest = JSON.parse(
  JSON.stringify(
    buildSendInboxWireRequest({
      mailboxId: "imap-mailbox-1",
      to: "recipient@example.com",
      subject: "Re: Subject",
      bodyHtml: "<p>Reply</p>",
      bodyText: "Reply",
      imapReplyContext: {
        sourceProviderFolder: "INBOX",
        sourceImapUid: "42",
        sourceUidValidity: "900",
        threadId: "browser-local-thread",
        rfcMessageId: "browser-local@example.com",
        References: "<browser-local@example.com>",
        "In-Reply-To": "<browser-local@example.com>",
      },
      providerFolder: "must-not-cross-the-wire",
      imapUid: "must-not-cross-the-wire",
      uidValidity: "must-not-cross-the-wire",
      rfcMessageId: "must-not-cross-the-wire@example.com",
      password: "must-not-cross-the-wire",
    } as any),
  ),
);
assert.deepEqual(imapWireRequest, {
  mailboxId: "imap-mailbox-1",
  to: "recipient@example.com",
  subject: "Re: Subject",
  bodyHtml: "<p>Reply</p>",
  bodyText: "Reply",
  imapReplyContext: {
    sourceProviderFolder: "INBOX",
    sourceImapUid: "42",
    sourceUidValidity: "900",
  },
});

const gmailReplyContext = buildGmailReplyContext({
  sendProvider: "google",
  composeMode: "reply",
  mailboxId: "gmail-mailbox-1",
  sourceMessage: {
    providerMessageId: "gmail-source-message-1",
    threadIdentityAuthority: "gmail",
    threadIdentityContext: {
      provider: "google",
      mailboxId: "gmail-mailbox-1",
    },
  },
});
assert.deepEqual(gmailReplyContext, {
  sourceProviderMessageId: "gmail-source-message-1",
});
assert.deepEqual(
  JSON.parse(
    JSON.stringify(
      buildSendInboxWireRequest({
        mailboxId: "gmail-mailbox-1",
        to: "recipient@example.com",
        subject: "Re: Subject",
        bodyHtml: "<p>Reply</p>",
        bodyText: "Reply",
        replyContext: gmailReplyContext,
      }),
    ),
  ),
  {
    mailboxId: "gmail-mailbox-1",
    to: "recipient@example.com",
    subject: "Re: Subject",
    bodyHtml: "<p>Reply</p>",
    bodyText: "Reply",
    replyContext: {
      sourceProviderMessageId: "gmail-source-message-1",
    },
  },
  "the existing Gmail reply DTO remains unchanged",
);

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const composeStateSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("const [composeTo, setComposeTo]"),
  workspaceShellSource.indexOf("const [pendingComposeAttachmentPickerOpen"),
);
const resetComposeSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("const resetComposeState ="),
  workspaceShellSource.indexOf("const normalizeRememberedRecipient ="),
);
assert.match(
  composeStateSource,
  /composeReplyAnchorMessage, setComposeReplyAnchorMessage/,
  "the authoritative provider anchor must be scoped to one compose session",
);
assert.match(
  resetComposeSource,
  /setComposeReplyAnchorMessage\(null\)/,
  "closing, discarding, or completing compose must clear the provider anchor",
);
const openComposeSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("const openComposeFromMessage ="),
  workspaceShellSource.indexOf(
    "useEffect(() =>",
    workspaceShellSource.indexOf("const openComposeFromMessage ="),
  ),
);
const preserveLocatorlessSourceIndex = openComposeSource.indexOf(
  "const preserveLocatorlessCustomReplySource =",
);
const authoritativeSelectionIndex = openComposeSource.indexOf(
  "selectLatestAuthoritativeConversationMessage(",
);
const eligibleAnchorSelectionIndex = openComposeSource.indexOf(
  "selectLatestEligibleAuthoritativeConversationMessage(",
);

assert.ok(
  preserveLocatorlessSourceIndex >= 0 &&
    preserveLocatorlessSourceIndex < authoritativeSelectionIndex,
  "a selected locator-less custom source must be recognized before thread source selection",
);
assert.match(
  openComposeSource.slice(
    preserveLocatorlessSourceIndex,
    authoritativeSelectionIndex,
  ),
  /selectedSourceProvider === "custom_imap"[\s\S]*!buildImapReplyContext\([\s\S]*preserveLocatorlessCustomReplySource[\s\S]*\? message/,
  "a synthetic Sent row must remain the compose presentation source",
);
assert.ok(
  eligibleAnchorSelectionIndex > authoritativeSelectionIndex,
  "provider anchor selection must remain separate from presentation-source selection",
);
assert.match(
  openComposeSource.slice(authoritativeSelectionIndex, eligibleAnchorSelectionIndex + 1200),
  /selectedSourceProvider === "custom_imap"[\s\S]*selectLatestEligibleAuthoritativeConversationMessage\([\s\S]*message,[\s\S]*normalReplyCandidates,[\s\S]*selectedSourceMailboxId,[\s\S]*buildImapReplyContext\(/,
  "custom IMAP continuity must filter canonical candidates through the physical provider-context validator",
);
assert.match(
  openComposeSource,
  /setComposeSourceMessage\(effectiveMessage\)[\s\S]*setComposeReplyAnchorMessage\(replyAnchorMessage\)/,
  "quote and recipient source must remain distinct from the provider reply anchor",
);

const sendSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("const sendMessage = async"),
  workspaceShellSource.indexOf("const closeMenus ="),
);
const imapContextIndex = sendSource.indexOf("buildImapReplyContext({");
const sendingStateIndex = sendSource.indexOf("setIsSendingCompose(true)");
const attachmentSerializationIndex = sendSource.indexOf(
  "const serializedAttachments",
);
const sendRequestIndex = sendSource.indexOf(
  "const sendResponse = await sendGmailMessage({",
);

assert.ok(imapContextIndex >= 0, "the compose path must build IMAP reply context");
assert.match(
  sendSource.slice(imapContextIndex, sendingStateIndex),
  /sourceMessage: composeReplyAnchorMessage \?\? composeSourceMessage/,
  "the second send must prefer the separately validated provider anchor",
);
assert.ok(
  imapContextIndex < sendingStateIndex &&
    imapContextIndex < attachmentSerializationIndex &&
    imapContextIndex < sendRequestIndex,
  "custom IMAP source validation must happen before sending or attachment work",
);
assert.match(
  sendSource.slice(imapContextIndex, sendingStateIndex),
  /sendProvider === "custom_imap"[\s\S]*isReplyComposeMode[\s\S]*!imapReplyContext[\s\S]*Refresh the mailbox or select a provider-backed message before replying\.[\s\S]*return;/,
);
assert.match(
  sendSource.slice(sendRequestIndex, sendSource.indexOf("\n      });", sendRequestIndex)),
  /imapReplyContext\s*\?\s*\{ imapReplyContext \}/,
);

const sentSeedStart = sendSource.indexOf("const sentMessageSeed:");
const sentSeedEnd = sendSource.indexOf("const hasGmailProviderIdentity", sentSeedStart);
const sentSeedSource = sendSource.slice(sentSeedStart, sentSeedEnd);
assert.match(
  sentSeedSource,
  /sendProvider === "custom_imap" && isReplyComposeMode[\s\S]*composeSourceMessage\?\.threadId[\s\S]*composeSourceMessage\?\.threadIdentityAuthority/,
  "the visible custom Sent row must retain only the proven canonical conversation identity",
);
assert.doesNotMatch(
  sentSeedSource,
  /imapUid|uidValidity|providerFolder|rfcMessageId/,
  "the visible custom Sent row must not fabricate a physical IMAP or RFC locator",
);
assert.match(
  sendSource,
  /Sent: \[sentMessage, \.\.\.currentStore\[activeComposeMailbox\.id\]\.Sent\]/,
  "a successful Reply must still insert the visible Sent row immediately",
);
assert.match(
  sendSource,
  /if \(!sendResponse\.ok\) \{[\s\S]*setComposeSendError\([\s\S]*return;[\s\S]*setMailboxStore\([\s\S]*setIsComposeOpen\(false\)/,
  "failed sends must return before insertion/close while successful sends still close",
);
const providerFailureStart = sendSource.indexOf("if (!sendResponse.ok)");
const providerFailureEnd = sendSource.indexOf(
  "rememberSentRecipients(",
  providerFailureStart,
);
const providerFailureSource = sendSource.slice(
  providerFailureStart,
  providerFailureEnd,
);
assert.doesNotMatch(
  providerFailureSource,
  /resetComposeState|setIsComposeOpen\(false\)|setComposeReplyAnchorMessage/,
  "a provider-declared send failure must leave the composer and its anchor intact",
);
const caughtFailureStart = sendSource.lastIndexOf("} catch (error) {");
const caughtFailureEnd = sendSource.indexOf("} finally {", caughtFailureStart);
const caughtFailureSource = sendSource.slice(caughtFailureStart, caughtFailureEnd);
assert.doesNotMatch(
  caughtFailureSource,
  /resetComposeState|setIsComposeOpen\(false\)|setComposeReplyAnchorMessage/,
  "a thrown send failure must leave the composer and its anchor intact",
);
assert.match(
  sendSource.slice(caughtFailureEnd),
  /setIsSendingCompose\(false\)/,
  "every settled send attempt must release the sending state",
);
assert.match(
  sendSource,
  /onSuccessfulConversationReply\([\s\S]*activeComposeMailbox\.id,[\s\S]*composeSourceMessage,[\s\S]*composeMode,[\s\S]*sentAt/,
  "waiting state must continue from the visible conversation source",
);
assert.doesNotMatch(
  sendSource,
  /onSuccessfulConversationReply\([\s\S]{0,240}composeReplyAnchorMessage/,
  "the provider-only anchor must never replace waiting-state conversation identity",
);
assert.match(
  sendSource,
  /hasGmailProviderIdentity[\s\S]*applyLiveThreadIdentity\([\s\S]*providerMessageId: sendResponse\.providerMessageId,[\s\S]*providerThreadId: sendResponse\.providerThreadId/,
  "Gmail must continue using its authoritative send-result message and thread identity",
);

const apiSource = readFileSync(
  resolve(process.cwd(), "src/lib/inboxConnectionApi.ts"),
  "utf8",
);
const sendApiSource = apiSource.slice(
  apiSource.indexOf("export async function sendGmailMessage("),
  apiSource.indexOf(
    "const abortController",
    apiSource.indexOf("export async function sendGmailMessage("),
  ),
);
assert.match(
  sendApiSource,
  /request\.imapReplyContext !== undefined[\s\S]*!isValidImapReplyContext\(request\.imapReplyContext\)/,
  "an explicitly malformed IMAP context must fail before the request starts",
);

console.log("WorkspaceShell custom IMAP reply context tests passed");
