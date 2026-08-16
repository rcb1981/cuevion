import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  buildGmailReplyContext,
  buildImapReplyContext,
  buildSendInboxWireRequest,
} from "../../lib/inboxConnectionApi";

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
  "a synthetic Sent source must not be replaced with an older provider-backed message",
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
