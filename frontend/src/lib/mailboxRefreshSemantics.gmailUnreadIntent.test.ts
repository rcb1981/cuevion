import assert from "node:assert/strict";
import fs from "node:fs";
import {
  createCustomImapInboxAuthority,
  createGmailUnreadIntentAuthority,
} from "./mailboxRefreshSemantics";

const mailboxA = "gmail-mailbox-a";
const mailboxB = "gmail-mailbox-b";

function gmailMessage(
  providerMessageId: string,
  unread: boolean,
  serverMailboxId = mailboxA,
) {
  return {
    id: `local-${serverMailboxId}-${providerMessageId}`,
    serverMailboxId,
    providerMessageId,
    providerFolder: "INBOX",
    unread,
  };
}

{
  const authority = createGmailUnreadIntentAuthority();
  const intent = authority.beginIntent(mailboxA, "new-message", false);
  assert.ok(intent);
  assert.equal(
    authority.applyPendingIntents(mailboxA, [
      gmailMessage("new-message", true),
    ])[0]?.unread,
    false,
    "a Gmail Read intent must be available to the immediate optimistic render",
  );
}

{
  const authority = createGmailUnreadIntentAuthority();
  const fetchGeneration = authority.captureGeneration();
  authority.beginIntent(mailboxA, "stale-read", false);
  const result = authority.resolveFetchResponse({
    mailboxId: mailboxA,
    generationAtFetchStart: fetchGeneration,
    messages: [gmailMessage("stale-read", true)],
  });
  assert.equal(result.messages[0]?.unread, false);
  assert.deepEqual(result.confirmedMessages, []);
  assert.deepEqual(result.overrideClearableMessages, []);
  assert.equal(
    authority.getPendingIntent(mailboxA, "stale-read")?.desiredUnread,
    false,
    "a fetch started before Read must neither reverse nor retire the intent",
  );
}

{
  const authority = createGmailUnreadIntentAuthority();
  authority.beginIntent(mailboxA, "agreed-read", false);
  const providerAgreement = gmailMessage("agreed-read", false);
  const result = authority.resolveFetchResponse({
    mailboxId: mailboxA,
    generationAtFetchStart: authority.captureGeneration(),
    messages: [providerAgreement],
  });
  assert.deepEqual(result.messages, [providerAgreement]);
  assert.deepEqual(result.confirmedMessages, [providerAgreement]);
  assert.deepEqual(result.overrideClearableMessages, [providerAgreement]);
  assert.equal(authority.getPendingIntent(mailboxA, "agreed-read"), null);
}

{
  const authority = createGmailUnreadIntentAuthority();
  authority.beginIntent(mailboxA, "lagging-read", false);
  const result = authority.resolveFetchResponse({
    mailboxId: mailboxA,
    generationAtFetchStart: authority.captureGeneration(),
    messages: [gmailMessage("lagging-read", true)],
  });
  assert.equal(result.messages[0]?.unread, false);
  assert.deepEqual(result.confirmedMessages, []);
  assert.deepEqual(result.overrideClearableMessages, []);
  assert.equal(
    authority.getPendingIntent(mailboxA, "lagging-read")?.desiredUnread,
    false,
    "the first post-intent provider disagreement must remain locally Read",
  );
}

{
  const authority = createGmailUnreadIntentAuthority();
  const failedIntent = authority.beginIntent(mailboxA, "failed-read", false);
  assert.ok(failedIntent);
  assert.equal(authority.failIntent(failedIntent), true);
  assert.equal(authority.getPendingIntent(mailboxA, "failed-read"), null);
  assert.equal(
    authority.applyPendingIntents(mailboxA, [
      gmailMessage("failed-read", true),
    ])[0]?.unread,
    true,
    "definitive action failure must remove the local fiction",
  );
}

{
  const authority = createGmailUnreadIntentAuthority();
  const fetchGeneration = authority.captureGeneration();
  authority.beginIntent(mailboxA, "mark-unread", true);
  const result = authority.resolveFetchResponse({
    mailboxId: mailboxA,
    generationAtFetchStart: fetchGeneration,
    messages: [gmailMessage("mark-unread", false)],
  });
  assert.equal(result.messages[0]?.unread, true);
  assert.equal(
    authority.getPendingIntent(mailboxA, "mark-unread")?.desiredUnread,
    true,
    "Mark Unread must use the same stale-response fence as Read",
  );
}

{
  const authority = createGmailUnreadIntentAuthority();
  const fetchGeneration = authority.captureGeneration();
  const olderRead = authority.beginIntent(mailboxA, "superseded", false);
  const newerUnread = authority.beginIntent(mailboxA, "superseded", true);
  assert.ok(olderRead);
  assert.ok(newerUnread);
  assert.equal(authority.isCurrentIntent(olderRead), false);
  assert.equal(authority.isCurrentIntent(newerUnread), true);
  assert.equal(authority.failIntent(olderRead), false);
  assert.equal(
    authority.resolveFetchResponse({
      mailboxId: mailboxA,
      generationAtFetchStart: fetchGeneration,
      messages: [gmailMessage("superseded", false)],
    }).messages[0]?.unread,
    true,
    "an older Read action or response must not beat a newer Mark Unread intent",
  );
}

{
  const authority = createGmailUnreadIntentAuthority();
  authority.beginIntent(mailboxA, "message-a", false);
  const result = authority.applyPendingIntents(mailboxA, [
    gmailMessage("message-a", true),
    gmailMessage("message-b", true),
  ]);
  assert.equal(result[0]?.unread, false);
  assert.equal(result[1]?.unread, true);
}

{
  const authority = createGmailUnreadIntentAuthority();
  authority.beginIntent(mailboxA, "shared-provider-id", false);
  const otherMailboxMessage = gmailMessage(
    "shared-provider-id",
    true,
    mailboxB,
  );
  assert.strictEqual(
    authority.applyPendingIntents(mailboxB, [otherMailboxMessage])[0],
    otherMailboxMessage,
    "provider message IDs must remain isolated by mailbox",
  );
}

{
  const authority = createGmailUnreadIntentAuthority();
  authority.beginIntent(mailboxA, "snapshot-message", false);
  const staleSnapshot = [gmailMessage("snapshot-message", true)];
  assert.equal(
    authority.applyPendingIntents(mailboxA, staleSnapshot)[0]?.unread,
    false,
    "snapshot hydration must respect a pending Gmail intent without retiring it",
  );
  assert.ok(authority.getPendingIntent(mailboxA, "snapshot-message"));
}

{
  const authority = createGmailUnreadIntentAuthority();
  authority.beginIntent(mailboxA, "agreement-required", false);
  const disagreement = authority.resolveFetchResponse({
    mailboxId: mailboxA,
    generationAtFetchStart: authority.captureGeneration(),
    messages: [gmailMessage("agreement-required", true)],
  });
  assert.deepEqual(disagreement.confirmedMessages, []);
  assert.ok(authority.getPendingIntent(mailboxA, "agreement-required"));
  const missingUnread = authority.resolveFetchResponse({
    mailboxId: mailboxA,
    generationAtFetchStart: authority.captureGeneration(),
    messages: [
      {
        ...gmailMessage("agreement-required", true),
        unread: undefined,
      },
    ],
  });
  assert.deepEqual(missingUnread.confirmedMessages, []);
  assert.ok(authority.getPendingIntent(mailboxA, "agreement-required"));
}

{
  const authority = createGmailUnreadIntentAuthority();
  authority.beginIntent(mailboxA, "thread-member-a", false);
  authority.beginIntent(mailboxA, "thread-member-b", false);
  const threadRows = authority.resolveFetchResponse({
    mailboxId: mailboxA,
    generationAtFetchStart: 0,
    messages: [
      gmailMessage("thread-member-a", true),
      gmailMessage("thread-member-b", true),
    ],
  }).messages;
  assert.deepEqual(
    threadRows.map((message) => message.unread),
    [false, false],
    "loaded Gmail thread members must each use their provider message identity",
  );
}

{
  const imapAuthority = createCustomImapInboxAuthority();
  const generation = imapAuthority.captureGeneration(mailboxA);
  const imapMessage = {
    serverMailboxId: mailboxA,
    providerFolder: "INBOX",
    uidValidity: "900",
    imapUid: "42",
    unread: true,
  };
  assert.deepEqual(
    imapAuthority.resolveFetchResponse({
      mailboxId: mailboxA,
      generationAtFetchStart: generation,
      uidValidity: "900",
      messages: [imapMessage],
    }),
    { stale: false, messages: [imapMessage] },
    "the Gmail unread fence must not alter custom IMAP authority",
  );
}

{
  const authority = createGmailUnreadIntentAuthority();
  authority.beginIntent(mailboxA, "override-compatibility", false);
  const disagreement = gmailMessage("override-compatibility", true);
  const laggingResult = authority.resolveFetchResponse({
    mailboxId: mailboxA,
    generationAtFetchStart: authority.captureGeneration(),
    messages: [disagreement],
  });
  assert.deepEqual(laggingResult.confirmedMessages, []);
  assert.deepEqual(laggingResult.overrideClearableMessages, []);
  const agreement = gmailMessage("override-compatibility", false);
  const agreedResult = authority.resolveFetchResponse({
    mailboxId: mailboxA,
    generationAtFetchStart: authority.captureGeneration(),
    messages: [agreement],
  });
  assert.deepEqual(
    agreedResult.confirmedMessages,
    [agreement],
    "only exact provider agreement may clear the compatible persisted override",
  );
  assert.deepEqual(agreedResult.overrideClearableMessages, [agreement]);
  const unfencedProviderRow = gmailMessage("legacy-persisted-override", true);
  assert.deepEqual(
    authority.resolveFetchResponse({
      mailboxId: mailboxA,
      generationAtFetchStart: authority.captureGeneration(),
      messages: [unfencedProviderRow],
    }).overrideClearableMessages,
    [unfencedProviderRow],
    "an existing persisted override without a pending intent remains clearable",
  );
}

{
  const source = fs.readFileSync(
    "src/components/workspace/WorkspaceShell.tsx",
    "utf8",
  );
  const beginIntentIndex = source.indexOf(
    "const requests = unresolvedRequests.map((entry) => ({",
  );
  const optimisticUpdateIndex = source.indexOf(
    "if (nonImapTargetMessageIds.length > 0)",
    beginIntentIndex,
  );
  const mutationAwaitIndex = source.indexOf(
    "await mutateInboxMessageAction",
    optimisticUpdateIndex,
  );
  assert.ok(
    beginIntentIndex >= 0 &&
      beginIntentIndex < optimisticUpdateIndex &&
      optimisticUpdateIndex < mutationAwaitIndex,
    "Gmail intent must be recorded before optimistic UI and provider await",
  );
  assert.match(
    source,
    /onFailGmailUnreadIntent\(entry\.gmailUnreadIntent\)[\s\S]{0,180}updateUnreadStateInMailboxStore/,
    "only the current failed action may roll back optimistic Gmail unread state",
  );
  const fetchCaptureIndex = source.indexOf(
    "gmailUnreadIntentAuthorityRef.current.captureGeneration()",
  );
  const fetchStartIndex = source.indexOf(
    "startIndependentMailboxFetches",
    fetchCaptureIndex,
  );
  const fetchResolutionIndex = source.indexOf(
    "gmailUnreadIntentAuthorityRef.current.resolveFetchResponse",
    fetchStartIndex,
  );
  assert.ok(
    fetchCaptureIndex >= 0 &&
      fetchCaptureIndex < fetchStartIndex &&
      fetchStartIndex < fetchResolutionIndex,
    "Gmail refresh must capture intent generation before fetch and reconcile after it",
  );
  assert.match(
    source,
    /clearUnreadOverridesForProviderMessages\([\s\S]{0,180}gmailUnreadIntentResolution\?\.overrideClearableMessages/,
    "provider boolean presence alone must not clear Gmail unread overrides",
  );
}

console.log("Gmail unread intent authority tests passed");
