import assert from "node:assert/strict";
import {
  buildProviderMessageActionTarget,
  type ProviderMessageActionCandidate,
} from "./providerMessageAction";

type Test = {
  name: string;
  run: () => void;
};

const tests: Test[] = [];

function test(name: string, run: Test["run"]) {
  tests.push({ name, run });
}

function gmailCandidate(
  overrides: Partial<ProviderMessageActionCandidate> = {},
): ProviderMessageActionCandidate {
  return {
    provider: "google",
    mailboxId: "server-mailbox-1",
    localFolder: "Inbox",
    isSharedView: false,
    providerMessageId: "gmail-provider-message-1",
    action: "mark_read",
    ...overrides,
  };
}

function imapCandidate(
  overrides: Partial<ProviderMessageActionCandidate> = {},
): ProviderMessageActionCandidate {
  return {
    provider: "custom_imap",
    mailboxId: "server-mailbox-1",
    localFolder: "Inbox",
    isSharedView: false,
    imapUid: "42",
    imapFolder: "INBOX",
    imapUidValidity: "900",
    action: "mark_read",
    ...overrides,
  };
}

for (const action of ["mark_read", "mark_unread", "star", "unstar"] as const) {
  test(`Gmail ${action} uses only the concrete provider message id`, () => {
    const target = buildProviderMessageActionTarget(gmailCandidate({ action }));

    assert.equal(target.ok, true);
    if (!target.ok) return;
    assert.deepEqual(target.request, {
      mailboxId: "server-mailbox-1",
      messageId: "gmail-provider-message-1",
      action,
    });
    assert.deepEqual(Object.keys(target.request).sort(), [
      "action",
      "mailboxId",
      "messageId",
    ]);
  });
}

test("Gmail trims and validates its concrete provider message id", () => {
  const target = buildProviderMessageActionTarget(
    gmailCandidate({ providerMessageId: "  gmail-provider-message-1  " }),
  );

  assert.equal(target.ok, true);
  if (!target.ok) return;
  assert.equal(target.request.messageId, "gmail-provider-message-1");

  for (const providerMessageId of [
    "",
    " ",
    "rfc@example.test",
    "<rfc-message>",
    "imap-uid-42",
    "thread-42",
    "message\nid",
    "x".repeat(257),
  ]) {
    assert.deepEqual(
      buildProviderMessageActionTarget(gmailCandidate({ providerMessageId })),
      { ok: false, reason: "missing_gmail_provider_message_id" },
    );
  }
});

test("Gmail never falls back to UI, IMAP, thread, provider-thread, or RFC ids", () => {
  const candidate = {
    ...gmailCandidate({ providerMessageId: undefined }),
    id: "ui-or-preview-id",
    imapUid: "42",
    threadId: "thread-id",
    providerThreadId: "provider-thread-id",
    rfcMessageId: "rfc-message@example.test",
  };

  assert.deepEqual(buildProviderMessageActionTarget(candidate), {
    ok: false,
    reason: "missing_gmail_provider_message_id",
  });
});

test("custom IMAP keeps the exact UID, UIDVALIDITY, and provider-folder request", () => {
  const candidate = {
    ...imapCandidate({ action: "star" }),
    providerMessageId: "gmail-id-must-not-be-used",
    id: "ui-id-must-not-be-used",
    threadId: "thread-id-must-not-be-used",
  };
  const target = buildProviderMessageActionTarget(candidate);

  assert.equal(target.ok, true);
  if (!target.ok) return;
  assert.deepEqual(target.request, {
    mailboxId: "server-mailbox-1",
    folder: "INBOX",
    uid: "42",
    uidValidity: "900",
    action: "star",
  });
  assert.deepEqual(Object.keys(target.request).sort(), [
    "action",
    "folder",
    "mailboxId",
    "uid",
    "uidValidity",
  ]);
});

test("ordinary Inbox and Filtered contexts pass their existing folder guard", () => {
  for (const localFolder of ["Inbox", "Filtered"]) {
    assert.equal(
      buildProviderMessageActionTarget(gmailCandidate({ localFolder })).ok,
      true,
    );
    assert.equal(
      buildProviderMessageActionTarget(imapCandidate({ localFolder })).ok,
      true,
    );
  }
});

test("Archive, Spam, shared, and other unsupported contexts remain blocked", () => {
  for (const localFolder of ["Archive", "Spam", "Drafts", "Sent", "Trash"]) {
    assert.deepEqual(
      buildProviderMessageActionTarget(gmailCandidate({ localFolder })),
      { ok: false, reason: "unsupported_context" },
    );
  }

  assert.deepEqual(
    buildProviderMessageActionTarget(gmailCandidate({ isSharedView: true })),
    { ok: false, reason: "unsupported_context" },
  );
});

let failed = 0;
for (const current of tests) {
  try {
    current.run();
    console.log(`✓ ${current.name}`);
  } catch (error) {
    failed += 1;
    console.error(`✗ ${current.name}`);
    console.error(error);
  }
}

if (failed > 0) {
  process.exitCode = 1;
}
