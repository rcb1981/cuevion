/**
 * Persisted mailbox message identity contract.
 *
 * Run with:
 *   cd frontend && node -e "require('./node_modules/sucrase/register/ts.js'); require('./src/lib/mailboxMessageIdentity.test.ts')"
 */

import assert from "node:assert/strict";
import {
  addPersistedMessageIdentityKeys,
  getPersistedMessageIdentityKeys,
  getPersistedMessageOwnershipIdentityKeys,
  isPersistedMessageIdentityImap,
  migrateLegacyImapOwnershipStateRecord,
  migrateLegacyImapStateRecord,
  migrateLegacyImapStateKeys,
  migrateLegacyMailboxPrefixedImapStateKeys,
  removePersistedMessageIdentityKeys,
  removePersistedMessageStateValue,
  resolvePersistedMessageOwnershipStateValue,
  resolvePersistedMessageStateValue,
  writePersistedMessageOwnershipStateValue,
  writePersistedMessageStateValue,
  type PersistedMessageIdentityCandidate,
  type PersistedMessageIdentityContext,
  type PersistedMessageIdentitySource,
} from "./mailboxMessageIdentity";

let passed = 0;
let failed = 0;

function test(name: string, fn: () => void) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed += 1;
  } catch (error) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${(error as Error).message}`);
    failed += 1;
  }
}

function imapMessage(
  overrides: Partial<PersistedMessageIdentitySource> = {},
): PersistedMessageIdentitySource {
  return {
    id: "imap-uid-1",
    imapUid: "1",
    subject: "Scoped identity",
    from: "sender@example.com",
    timestamp: "2026-08-02T10:00:00.000Z",
    threadIdentityContext: {
      mailboxId: "mailbox-a",
      provider: "custom_imap",
      folder: "INBOX",
      uidValidity: "epoch-1",
    },
    ...overrides,
  };
}

function candidate(
  overrides: Partial<PersistedMessageIdentitySource> = {},
): PersistedMessageIdentityCandidate {
  return { message: imapMessage(overrides) };
}

console.log("\nPersisted mailbox message identity");

test("mailbox A UID 1 and mailbox B UID 1 do not share unread state", () => {
  const mailboxA = imapMessage();
  const mailboxB = imapMessage({
    threadIdentityContext: {
      mailboxId: "mailbox-b",
      provider: "custom_imap",
      folder: "INBOX",
      uidValidity: "epoch-1",
    },
  });
  const unreadState = writePersistedMessageStateValue({}, mailboxA, true);

  assert.equal(resolvePersistedMessageStateValue(unreadState, mailboxA), true);
  assert.equal(resolvePersistedMessageStateValue(unreadState, mailboxB), undefined);
});

test("mailboxes with the same UID do not share manual priority", () => {
  const mailboxA = imapMessage();
  const mailboxB = imapMessage({
    threadIdentityContext: {
      mailboxId: "mailbox-b",
      provider: "custom_imap",
      folder: "INBOX",
      uidValidity: "epoch-1",
    },
  });
  const priorityState = writePersistedMessageStateValue({}, mailboxA, "priority");

  assert.equal(resolvePersistedMessageStateValue(priorityState, mailboxA), "priority");
  assert.equal(resolvePersistedMessageStateValue(priorityState, mailboxB), undefined);
});

test("mailboxes with the same UID do not share manual labels", () => {
  const mailboxA = imapMessage();
  const mailboxB = imapMessage({
    threadIdentityContext: {
      mailboxId: "mailbox-b",
      provider: "custom_imap",
      folder: "INBOX",
      uidValidity: "epoch-1",
    },
  });
  const labelState = writePersistedMessageStateValue({}, mailboxA, "Promo");

  assert.equal(resolvePersistedMessageStateValue(labelState, mailboxA), "Promo");
  assert.equal(resolvePersistedMessageStateValue(labelState, mailboxB), undefined);
});

test("mailboxes with the same UID do not share Organizer inclusion", () => {
  const mailboxA = imapMessage();
  const mailboxB = imapMessage({
    threadIdentityContext: {
      mailboxId: "mailbox-b",
      provider: "custom_imap",
      folder: "INBOX",
      uidValidity: "epoch-1",
    },
  });
  const organizerState = writePersistedMessageStateValue({}, mailboxA, "demo");

  assert.equal(resolvePersistedMessageStateValue(organizerState, mailboxA), "demo");
  assert.equal(resolvePersistedMessageStateValue(organizerState, mailboxB), undefined);
});

test("a UIDVALIDITY change isolates the reused UID", () => {
  const oldGeneration = imapMessage();
  const newGeneration = imapMessage({
    threadIdentityContext: {
      mailboxId: "mailbox-a",
      provider: "custom_imap",
      folder: "INBOX",
      uidValidity: "epoch-2",
    },
  });
  const state = writePersistedMessageStateValue({}, oldGeneration, true);

  assert.equal(resolvePersistedMessageStateValue(state, newGeneration), undefined);
});

test("folder is part of the technical fallback when semantic identity is absent", () => {
  const inboxMessage = imapMessage();
  const archiveMessage = imapMessage({
    threadIdentityContext: {
      mailboxId: "mailbox-a",
      provider: "custom_imap",
      folder: "Archive",
      uidValidity: "epoch-1",
    },
  });

  assert.notDeepEqual(
    getPersistedMessageIdentityKeys(inboxMessage),
    getPersistedMessageIdentityKeys(archiveMessage),
  );
});

test("valid RFC Message-ID remains semantic identity across Inbox to Archive", () => {
  const inboxMessage = imapMessage({
    id: "message-1@example.com",
    rfcMessageId: "<message-1@example.com>",
  });
  const archiveMessage = imapMessage({
    id: "message-1@example.com",
    rfcMessageId: "message-1@example.com",
    threadIdentityContext: {
      mailboxId: "mailbox-a",
      provider: "custom_imap",
      folder: "Archive",
      uidValidity: "epoch-1",
    },
  });

  assert.deepEqual(getPersistedMessageIdentityKeys(inboxMessage), [
    "imap-semantic:v2:mailbox-a:message-1%40example.com",
  ]);
  assert.deepEqual(
    getPersistedMessageIdentityKeys(archiveMessage),
    getPersistedMessageIdentityKeys(inboxMessage),
  );
  assert.deepEqual(
    getPersistedMessageOwnershipIdentityKeys(archiveMessage),
    getPersistedMessageOwnershipIdentityKeys(inboxMessage),
  );

  const state = writePersistedMessageStateValue({}, inboxMessage, "kept");
  const ownership = writePersistedMessageOwnershipStateValue(
    {},
    inboxMessage,
    { owner: "user-a" },
  );
  assert.equal(resolvePersistedMessageStateValue(state, archiveMessage), "kept");
  assert.deepEqual(
    resolvePersistedMessageOwnershipStateValue(ownership, archiveMessage),
    { owner: "user-a" },
  );
});

test("the same RFC Message-ID remains isolated between mailboxes", () => {
  const mailboxA = imapMessage({
    rfcMessageId: "<message-1@example.com>",
  });
  const mailboxB = imapMessage({
    rfcMessageId: "<message-1@example.com>",
    threadIdentityContext: {
      mailboxId: "mailbox-b",
      provider: "custom_imap",
      folder: "INBOX",
      uidValidity: "epoch-1",
    },
  });
  const state = writePersistedMessageStateValue({}, mailboxA, "mailbox-a");

  assert.equal(resolvePersistedMessageStateValue(state, mailboxA), "mailbox-a");
  assert.equal(resolvePersistedMessageStateValue(state, mailboxB), undefined);
  assert.notDeepEqual(
    getPersistedMessageOwnershipIdentityKeys(mailboxA),
    getPersistedMessageOwnershipIdentityKeys(mailboxB),
  );
});

test("an email-shaped UI id is not promoted to RFC semantic authority", () => {
  const message = imapMessage({
    id: "looks-semantic@example.com",
    rfcMessageId: null,
  });

  assert.equal(
    getPersistedMessageIdentityKeys(message)[0]?.startsWith("imap-scoped:v2:"),
    true,
  );
});

test("Gmail persisted identity keys remain unchanged", () => {
  const gmailMessage = {
    id: "gmail-message-id",
    subject: "Gmail subject",
    from: "sender@example.com",
    timestamp: "August 2 at 10:00",
    providerMessageId: "provider-id",
    threadIdentityContext: {
      mailboxId: "gmail-mailbox",
      provider: "google",
      folder: "Inbox",
      uidValidity: "gmail-api",
    },
  };

  assert.deepEqual(getPersistedMessageIdentityKeys(gmailMessage), [
    "id:gmail-message-id",
    "preview:Gmail subject|sender@example.com|August 2 at 10:00",
  ]);
  assert.deepEqual(getPersistedMessageOwnershipIdentityKeys(gmailMessage), [
    "gmail-message-id",
  ]);
});

test("Gmail write, read, and remove keep their old contract", () => {
  const gmailMessage = {
    id: "gmail-message-id",
    subject: "Gmail subject",
    from: "sender@example.com",
    timestamp: "August 2 at 10:00",
    threadIdentityContext: {
      mailboxId: "gmail-mailbox",
      provider: "google",
      folder: "Inbox",
      uidValidity: "gmail-api",
    },
  };
  const state = writePersistedMessageStateValue(
    { "imap:1": "inactive-pending-legacy" },
    gmailMessage,
    "gmail-value",
  );

  assert.equal(
    resolvePersistedMessageStateValue(state, gmailMessage),
    "gmail-value",
  );
  assert.deepEqual(removePersistedMessageStateValue(state, gmailMessage), {
    "imap:1": "inactive-pending-legacy",
  });
});

test("a Gmail ownership id that resembles an IMAP fallback is preserved", () => {
  const gmailMessage = {
    id: "imap-uid-1",
    subject: "Gmail collision guard",
    from: "sender@example.com",
    timestamp: "August 2 at 10:00",
    threadIdentityContext: {
      mailboxId: "gmail-mailbox",
      provider: "google",
      folder: "Inbox",
      uidValidity: "gmail-api",
    },
  };
  const ownership = { "imap-uid-1": { owner: "gmail-user" } };
  const gmailWithoutContext = {
    id: gmailMessage.id,
    subject: gmailMessage.subject,
    from: gmailMessage.from,
    timestamp: gmailMessage.timestamp,
  };

  assert.equal(isPersistedMessageIdentityImap(gmailMessage), false);
  assert.equal(isPersistedMessageIdentityImap(gmailWithoutContext), false);
  assert.deepEqual(getPersistedMessageOwnershipIdentityKeys(gmailWithoutContext), [
    "imap-uid-1",
  ]);
  assert.deepEqual(
    migrateLegacyImapOwnershipStateRecord(ownership, [
      candidate(),
      { message: gmailMessage },
    ]),
    ownership,
  );
  assert.deepEqual(
    migrateLegacyImapOwnershipStateRecord(
      ownership,
      [candidate()],
      { knownNonImapMailboxIds: ["gmail-mailbox"] },
    ),
    ownership,
  );

  const unrelatedImapWrite = writePersistedMessageOwnershipStateValue(
    ownership,
    imapMessage({ id: "imap-uid-2", imapUid: "2" }),
    { owner: "imap-user" },
  );
  assert.deepEqual(unrelatedImapWrite["imap-uid-1"], {
    owner: "gmail-user",
  });
});

test("one unambiguous legacy imap UID entry migrates to its exact scoped key", () => {
  const migrated = migrateLegacyImapStateRecord(
    { "imap:1": "priority" },
    [candidate()],
  );
  const [scopedKey] = getPersistedMessageIdentityKeys(imapMessage());

  assert.deepEqual(migrated, { [scopedKey]: "priority" });
});

test("an ambiguous legacy imap UID entry is applied nowhere", () => {
  const migrated = migrateLegacyImapStateRecord(
    { "imap:1": "Promo" },
    [
      candidate(),
      candidate({
        threadIdentityContext: {
          mailboxId: "mailbox-b",
          provider: "custom_imap",
          folder: "INBOX",
          uidValidity: "epoch-1",
        },
      }),
    ],
  );

  assert.deepEqual(migrated, {});
});

test("one complete and one unresolved candidate are treated as ambiguous", () => {
  const migrated = migrateLegacyImapStateRecord(
    { "imap:1": "unsafe" },
    [
      candidate(),
      candidate({
        threadIdentityContext: {
          mailboxId: "mailbox-a",
          provider: "custom_imap",
          folder: "Archive",
          uidValidity: null,
        },
      }),
    ],
  );

  assert.deepEqual(migrated, {});
});

test("conflicting UID fields block legacy migration for every signaled UID", () => {
  const conflictingCandidate = candidate({ id: "imap-uid-2" });

  assert.deepEqual(
    migrateLegacyImapStateRecord(
      { "imap:1": "unsafe" },
      [candidate(), conflictingCandidate],
    ),
    {},
  );
  assert.deepEqual(
    migrateLegacyImapStateRecord(
      { "imap:2": "unsafe" },
      [
        candidate({ id: "imap-uid-2", imapUid: "2" }),
        conflictingCandidate,
      ],
    ),
    {},
  );
});

test("multiple known IMAP mailboxes block a seemingly unique candidate", () => {
  const migrated = migrateLegacyImapStateRecord(
    { "imap:1": "unsafe" },
    [candidate()],
    { knownImapMailboxIds: ["mailbox-a", "mailbox-b"] },
  );

  assert.deepEqual(migrated, {});
});

test("zero-candidate legacy state stays inactive until exact hydration", () => {
  const pending = migrateLegacyImapStateRecord(
    { "imap:1": "priority" },
    [],
  );

  assert.deepEqual(pending, { "imap:1": "priority" });
  assert.equal(resolvePersistedMessageStateValue(pending, imapMessage()), undefined);

  const migrated = migrateLegacyImapStateRecord(pending, [candidate()]);
  const [scopedKey] = getPersistedMessageIdentityKeys(imapMessage());
  assert.deepEqual(migrated, { [scopedKey]: "priority" });
});

test("an explicit IMAP mutation consumes pending legacy state for that UID", () => {
  const message = imapMessage();
  const pendingRecord = { "imap:1": "stale" };
  const written = writePersistedMessageStateValue(
    pendingRecord,
    message,
    "current",
  );

  assert.equal(Object.prototype.hasOwnProperty.call(written, "imap:1"), false);
  assert.deepEqual(removePersistedMessageStateValue(pendingRecord, message), {});

  const added = addPersistedMessageIdentityKeys(["imap:1"], [message]);
  assert.equal(added.includes("imap:1"), false);
  assert.deepEqual(removePersistedMessageIdentityKeys(["imap:1"], [message]), []);
});

test("spam array legacy keys migrate uniquely and expire when ambiguous", () => {
  const [scopedKey] = getPersistedMessageIdentityKeys(imapMessage());

  assert.deepEqual(
    migrateLegacyImapStateKeys(["imap:1"], [candidate()]),
    [scopedKey],
  );
  assert.deepEqual(
    migrateLegacyImapStateKeys(
      ["imap:1"],
      [
        candidate(),
        candidate({
          threadIdentityContext: {
            mailboxId: "mailbox-b",
            provider: "custom_imap",
            folder: "INBOX",
            uidValidity: "epoch-2",
          },
        }),
      ],
    ),
    [],
  );
});

test("array, ownership, and mailbox-prefixed migrations share pending and mailbox guards", () => {
  const multipleMailboxOptions = {
    knownImapMailboxIds: ["mailbox-a", "mailbox-b"],
  };

  assert.deepEqual(migrateLegacyImapStateKeys(["imap:1"], []), ["imap:1"]);
  assert.deepEqual(
    migrateLegacyImapOwnershipStateRecord(
      { "imap-uid-1": { owner: "pending" } },
      [],
    ),
    { "imap-uid-1": { owner: "pending" } },
  );
  assert.deepEqual(
    migrateLegacyMailboxPrefixedImapStateKeys(
      ["mailbox:mailbox-a:imap:1"],
      [],
    ),
    ["mailbox:mailbox-a:imap:1"],
  );

  assert.deepEqual(
    migrateLegacyImapStateKeys(
      ["imap:1"],
      [candidate()],
      multipleMailboxOptions,
    ),
    [],
  );
  assert.deepEqual(
    migrateLegacyImapOwnershipStateRecord(
      { "imap-uid-1": { owner: "unsafe" } },
      [candidate()],
      multipleMailboxOptions,
    ),
    {},
  );
  assert.deepEqual(
    migrateLegacyMailboxPrefixedImapStateKeys(
      ["mailbox:mailbox-a:imap:1"],
      [candidate()],
      multipleMailboxOptions,
    ),
    [],
  );
});

test("legacy technical ID and preview aliases are never read for IMAP state", () => {
  const message = imapMessage();
  const legacyAliases = {
    "id:imap-uid-1": "legacy-id",
    "preview:Scoped identity|sender@example.com|2026-08-02T10:00:00.000Z":
      "legacy-preview",
  };

  assert.equal(
    resolvePersistedMessageStateValue(legacyAliases, message),
    undefined,
  );
});

test("new persisted writes never create a global imap UID key", () => {
  const state = writePersistedMessageStateValue({}, imapMessage(), true);
  const keys = Object.keys(state);

  assert.equal(keys.includes("imap:1"), false);
  assert.equal(keys.some((key) => key.startsWith("imap-scoped:v2:")), true);
});

test("incomplete IMAP context fails closed without weak ID or preview aliases", () => {
  const message = imapMessage({
    threadIdentityContext: {
      mailboxId: "mailbox-a",
      provider: "custom_imap",
      folder: "INBOX",
      uidValidity: null,
    },
  });

  assert.deepEqual(getPersistedMessageIdentityKeys(message), []);
  assert.deepEqual(writePersistedMessageStateValue({}, message, true), {});
});

test("conflicting IMAP context fails closed for every locator component", () => {
  const conflictingInputs: Array<
    [PersistedMessageIdentitySource, PersistedMessageIdentityContext?]
  > = [
    [imapMessage({ serverMailboxId: "mailbox-b" })],
    [imapMessage(), { mailboxId: "mailbox-b" }],
    [imapMessage(), { provider: "google" }],
    [imapMessage(), { folder: "Archive" }],
    [imapMessage({ uidValidity: "epoch-2" })],
    [imapMessage({ id: "imap-uid-2" })],
    [imapMessage({ imapUid: "not-a-uid" })],
  ];

  conflictingInputs.forEach(([message, context]) => {
    assert.equal(isPersistedMessageIdentityImap(message, context), true);
    assert.deepEqual(getPersistedMessageIdentityKeys(message, context), []);
  });
});

test("existing scoped state wins over a conflicting unique legacy value", () => {
  const [scopedKey] = getPersistedMessageIdentityKeys(imapMessage());
  const migrated = migrateLegacyImapStateRecord(
    { "imap:1": "legacy", [scopedKey]: "current" },
    [candidate()],
  );

  assert.deepEqual(migrated, { [scopedKey]: "current" });
});

test("ownership state uses scoped IMAP fallback and migrates only uniquely", () => {
  const message = imapMessage();
  const ownership = writePersistedMessageOwnershipStateValue(
    {},
    message,
    { count: 2 },
  );
  const [ownershipKey] = getPersistedMessageOwnershipIdentityKeys(message);

  assert.deepEqual(ownership, { [ownershipKey]: { count: 2 } });
  assert.deepEqual(
    migrateLegacyImapOwnershipStateRecord(
      { "imap-uid-1": { count: 1 } },
      [candidate()],
    ),
    { [ownershipKey]: { count: 1 } },
  );
  assert.deepEqual(
    migrateLegacyImapOwnershipStateRecord(
      { "imap-uid-1": { count: 1 } },
      [
        candidate(),
        candidate({
          threadIdentityContext: {
            mailboxId: "mailbox-b",
            provider: "custom_imap",
            folder: "INBOX",
            uidValidity: "epoch-1",
          },
        }),
      ],
    ),
    {},
  );
});

test("ownership interaction state does not cross mailbox boundaries", () => {
  const mailboxA = imapMessage();
  const mailboxB = imapMessage({
    threadIdentityContext: {
      mailboxId: "mailbox-b",
      provider: "custom_imap",
      folder: "INBOX",
      uidValidity: "epoch-1",
    },
  });
  const state = writePersistedMessageOwnershipStateValue(
    {},
    mailboxA,
    { count: 1 },
  );

  assert.deepEqual(getPersistedMessageOwnershipIdentityKeys(mailboxA).length, 1);
  assert.equal(
    getPersistedMessageOwnershipIdentityKeys(mailboxB).some((key) => key in state),
    false,
  );
});

test("spam key writes and removals use only safe persisted identities", () => {
  const message = imapMessage();
  const added = addPersistedMessageIdentityKeys([], [message]);

  assert.deepEqual(added, getPersistedMessageIdentityKeys(message));
  assert.deepEqual(removePersistedMessageIdentityKeys(added, [message]), []);
  assert.deepEqual(
    removePersistedMessageStateValue(
      Object.fromEntries(added.map((key) => [key, true])),
      message,
    ),
    {},
  );
});

test("mailbox-prefixed legacy priority state validates the outer mailbox", () => {
  const [stateKey] = getPersistedMessageIdentityKeys(imapMessage());

  assert.deepEqual(
    migrateLegacyMailboxPrefixedImapStateKeys(
      ["mailbox:mailbox-a:imap:1"],
      [candidate()],
    ),
    [`mailbox:mailbox-a:${stateKey}`],
  );
  assert.deepEqual(
    migrateLegacyMailboxPrefixedImapStateKeys(
      ["mailbox:mailbox-b:imap:1"],
      [candidate()],
    ),
    [],
  );
});

if (failed > 0) {
  console.error(`\n${failed} mailbox message identity test(s) failed; ${passed} passed.`);
  process.exitCode = 1;
} else {
  console.log(`\nAll ${passed} mailbox message identity tests passed.`);
}
