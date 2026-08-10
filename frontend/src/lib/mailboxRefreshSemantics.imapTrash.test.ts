import assert from "node:assert/strict";
import { createCustomImapInboxAuthority } from "./mailboxRefreshSemantics";

const mailboxId = "imap-trash-authority-mailbox";
const source = {
  id: "source",
  serverMailboxId: mailboxId,
  providerFolder: "INBOX",
  imapUid: "42",
  uidValidity: "900",
};
const sibling = {
  id: "sibling",
  serverMailboxId: mailboxId,
  providerFolder: "INBOX",
  imapUid: "43",
  uidValidity: "900",
};

{
  const authority = createCustomImapInboxAuthority();
  const generationBeforeConfirmation =
    authority.captureGeneration(mailboxId);

  authority.confirmSourceRemoval(mailboxId, "900", "42");

  assert.deepEqual(
    authority.resolveFetchResponse({
      mailboxId,
      generationAtFetchStart: generationBeforeConfirmation,
      uidValidity: "900",
      messages: [source, sibling],
    }),
    { stale: true, messages: [] },
    "an Inbox fetch started before confirmed Trash must never publish",
  );

  const currentGeneration = authority.captureGeneration(mailboxId);
  assert.deepEqual(
    authority.resolveFetchResponse({
      mailboxId,
      generationAtFetchStart: currentGeneration,
      uidValidity: "900",
      messages: [source, sibling],
    }),
    { stale: false, messages: [sibling] },
    "a current snapshot must still exclude the exact confirmed source identity",
  );
  assert.equal(authority.isRecentlyRemoved(mailboxId, "900", "42"), true);
}

{
  const authority = createCustomImapInboxAuthority();
  authority.confirmSourceRemoval(mailboxId, "900", "42");
  const currentGeneration = authority.captureGeneration(mailboxId);
  const sameUidUnderNewAuthority = {
    ...source,
    id: "new-authority-source",
    uidValidity: "901",
  };

  assert.deepEqual(
    authority.resolveFetchResponse({
      mailboxId,
      generationAtFetchStart: currentGeneration,
      uidValidity: "901",
      messages: [sameUidUnderNewAuthority],
    }),
    { stale: false, messages: [sameUidUnderNewAuthority] },
    "the same UID under a new exact UIDVALIDITY authority is a different identity",
  );
  assert.equal(authority.isRecentlyRemoved(mailboxId, "900", "42"), false);
}

{
  const authority = createCustomImapInboxAuthority();
  authority.confirmSourceRemoval(mailboxId, "900", "42");
  const currentGeneration = authority.captureGeneration(mailboxId);
  const wrongMailbox = {
    ...source,
    id: "wrong-mailbox",
    serverMailboxId: "another-mailbox",
  };
  const trashCollision = {
    ...source,
    id: "trash-collision",
    providerFolder: "Trash",
  };

  assert.deepEqual(
    authority.resolveFetchResponse({
      mailboxId,
      generationAtFetchStart: currentGeneration,
      uidValidity: "900",
      messages: [wrongMailbox, trashCollision, sibling],
    }),
    {
      stale: false,
      messages: [wrongMailbox, trashCollision, sibling],
    },
    "mailbox and provider-folder collisions must not match an Inbox fence",
  );
}

{
  const authority = createCustomImapInboxAuthority();
  authority.confirmSourceRemoval(mailboxId, "900", "42");
  const currentGeneration = authority.captureGeneration(mailboxId);
  const malformedNewAuthority = {
    ...source,
    uidValidity: "901",
    providerFolder: "Inbox",
  };

  authority.resolveFetchResponse({
    mailboxId,
    generationAtFetchStart: currentGeneration,
    uidValidity: "901",
    messages: [malformedNewAuthority],
  });
  assert.equal(
    authority.isRecentlyRemoved(mailboxId, "900", "42"),
    true,
    "a malformed snapshot cannot retire a confirmed-removal fence",
  );

  const generationBeforeReset = authority.captureGeneration(mailboxId);
  authority.resetMailbox(mailboxId);
  assert.equal(
    authority.captureGeneration(mailboxId),
    generationBeforeReset + 1,
    "a connection reset must advance authority monotonically",
  );
  assert.equal(authority.isRecentlyRemoved(mailboxId, "900", "42"), false);
  assert.deepEqual(
    authority.resolveFetchResponse({
      mailboxId,
      generationAtFetchStart: generationBeforeReset,
      uidValidity: "900",
      messages: [source],
    }),
    { stale: true, messages: [] },
    "a pre-reset fetch generation must stay stale after reconnect",
  );
}

console.log("custom IMAP Trash Inbox authority tests passed");
