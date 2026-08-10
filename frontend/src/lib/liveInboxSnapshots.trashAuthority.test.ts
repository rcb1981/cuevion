import assert from "node:assert/strict";
import {
  MUSIC_CLASSIFIER_VERSION,
  hydrateLiveInboxSnapshot,
  readLiveInboxSnapshots,
  removeAndPersistCustomImapInboxMessageFromSnapshot,
  removeCustomImapInboxMessageFromSnapshot,
  saveLiveInboxSnapshot,
  type LiveInboxSnapshot,
  type TrustedLiveInboxSnapshotContexts,
} from "./liveInboxSnapshots";

const STORAGE_KEY = "cuevion-live-inbox-snapshots";

function message(
  overrides: Partial<LiveInboxSnapshot["messages"][number]> = {},
): LiveInboxSnapshot["messages"][number] {
  return {
    id: "message-1",
    sender: "Sender",
    subject: "Subject",
    snippet: "Body",
    from: "sender@example.test",
    to: "owner@example.test",
    timestamp: "August 10 at 10:00",
    createdAt: "2026-08-10T08:00:00.000Z",
    body: ["Body"],
    ui_signal: "NEW",
    ...overrides,
  } as LiveInboxSnapshot["messages"][number];
}

function snapshot(
  provider: "google" | "custom_imap",
  inboxId: string,
  overrides: Partial<LiveInboxSnapshot> = {},
): LiveInboxSnapshot {
  const isGoogle = provider === "google";
  return {
    schemaVersion: 5,
    classifierVersion: MUSIC_CLASSIFIER_VERSION,
    provider,
    inboxId,
    email: `${inboxId}@example.test`,
    fetchedAt: "2026-08-10T08:01:00.000Z",
    folder: "INBOX",
    uidValidity: isGoogle ? "gmail-api" : "900",
    messages: [
      message({
        serverMailboxId: inboxId,
        providerFolder: isGoogle ? "Inbox" : "INBOX",
        providerMessageId: isGoogle ? `${inboxId}-provider-message` : undefined,
        imapUid: isGoogle ? undefined : "42",
        uidValidity: isGoogle ? undefined : "900",
      }),
    ],
    ...overrides,
  };
}

function withMemoryLocalStorage(run: (store: Map<string, string>) => void) {
  const previousWindow = (globalThis as { window?: unknown }).window;
  const store = new Map<string, string>();
  let rejectWrites = false;
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        if (rejectWrites) {
          throw new Error("localStorage unavailable");
        }
        store.set(key, value);
      },
    },
  };

  try {
    run(store);
    rejectWrites = true;
    assert.doesNotThrow(() =>
      saveLiveInboxSnapshot(snapshot("google", "write-failure")),
    );
  } finally {
    (globalThis as { window?: unknown }).window = previousWindow;
  }
}

function testTrustedInboxAuthority() {
  withMemoryLocalStorage((store) => {
    const gmailInbox = snapshot("google", "gmail-inbox");
    const customInbox = snapshot("custom_imap", "imap-inbox");
    const legacyInbox = {
      ...snapshot("custom_imap", "legacy-inbox"),
      provider: undefined,
      folder: undefined,
    };
    const gmailTrash = snapshot("google", "gmail-trash", {
      folder: "Trash",
      messages: [
        message({
          serverMailboxId: "gmail-trash",
          providerFolder: "Trash",
          providerMessageId: "gmail-trash-provider-message",
        }),
      ],
    });
    const customTrash = snapshot("custom_imap", "imap-trash", {
      folder: "Trash",
      messages: [
        message({
          serverMailboxId: "imap-trash",
          providerFolder: "Trash",
          imapUid: "42",
          uidValidity: "900",
        }),
      ],
    });
    const gmailTrashRow = snapshot("google", "gmail-trash-row", {
      messages: [
        message({
          serverMailboxId: "gmail-trash-row",
          providerFolder: "Trash",
          providerMessageId: "gmail-trash-row-provider-message",
        }),
      ],
    });
    const customTrashRow = snapshot("custom_imap", "imap-trash-row", {
      messages: [
        message({
          serverMailboxId: "imap-trash-row",
          providerFolder: "Trash",
          imapUid: "42",
          uidValidity: "900",
        }),
      ],
    });
    const mailboxMismatch = snapshot("google", "different-mailbox");
    const customMailboxMismatch = snapshot("custom_imap", "different-imap-mailbox");
    const customMixedIdentity = snapshot("custom_imap", "imap-mixed-identity", {
      messages: [
        message({
          serverMailboxId: "imap-mixed-identity",
          providerFolder: "INBOX",
          imapUid: "42",
          uidValidity: "900",
          providerMessageId: "gmail-provider-message",
          providerThreadId: "gmail-provider-thread",
          labelIds: ["INBOX"],
        }),
      ],
    });
    const providerMismatch = snapshot("google", "provider-mismatch");

    store.set(
      STORAGE_KEY,
      JSON.stringify({
        "gmail-inbox": gmailInbox,
        "imap-inbox": customInbox,
        "legacy-inbox": legacyInbox,
        "gmail-trash": gmailTrash,
        "imap-trash": customTrash,
        "gmail-trash-row": gmailTrashRow,
        "imap-trash-row": customTrashRow,
        "mailbox-mismatch": mailboxMismatch,
        "imap-mailbox-mismatch": customMailboxMismatch,
        "imap-mixed-identity": customMixedIdentity,
        "provider-mismatch": providerMismatch,
      }),
    );

    const trustedContexts: TrustedLiveInboxSnapshotContexts = {
      "gmail-inbox": {
        mailboxId: "gmail-inbox",
        provider: "google",
        folder: "INBOX",
      },
      "imap-inbox": {
        mailboxId: "imap-inbox",
        provider: "custom_imap",
        folder: "INBOX",
      },
      "legacy-inbox": {
        mailboxId: "legacy-inbox",
        provider: "custom_imap",
        folder: "INBOX",
      },
      "gmail-trash": {
        mailboxId: "gmail-trash",
        provider: "google",
        folder: "INBOX",
      },
      "imap-trash": {
        mailboxId: "imap-trash",
        provider: "custom_imap",
        folder: "INBOX",
      },
      "gmail-trash-row": {
        mailboxId: "gmail-trash-row",
        provider: "google",
        folder: "INBOX",
      },
      "imap-trash-row": {
        mailboxId: "imap-trash-row",
        provider: "custom_imap",
        folder: "INBOX",
      },
      "mailbox-mismatch": {
        mailboxId: "mailbox-mismatch",
        provider: "google",
        folder: "INBOX",
      },
      "imap-mailbox-mismatch": {
        mailboxId: "imap-mailbox-mismatch",
        provider: "custom_imap",
        folder: "INBOX",
      },
      "imap-mixed-identity": {
        mailboxId: "imap-mixed-identity",
        provider: "custom_imap",
        folder: "INBOX",
      },
      "provider-mismatch": {
        mailboxId: "provider-mismatch",
        provider: "custom_imap",
        folder: "INBOX",
      },
    };
    const stored = readLiveInboxSnapshots(trustedContexts);

    assert.deepEqual(Object.keys(stored).sort(), [
      "gmail-inbox",
      "imap-inbox",
      "legacy-inbox",
    ]);
    for (const inboxId of Object.keys(stored)) {
      const hydrated = hydrateLiveInboxSnapshot(stored[inboxId]);
      assert.ok(hydrated.context);
      assert.equal(hydrated.context?.mailboxId, inboxId);
      assert.equal(hydrated.context?.folder, "INBOX");
      assert.equal(hydrated.messages.length, 1);
    }
    assert.equal(stored["legacy-inbox"].provider, "custom_imap");
    assert.equal(stored["legacy-inbox"].folder, "INBOX");
    assert.deepEqual(
      Object.keys(JSON.parse(store.get(STORAGE_KEY) ?? "{}")).sort(),
      ["gmail-inbox", "imap-inbox", "legacy-inbox"],
    );

    for (const poisoned of [gmailTrash, customTrash]) {
      const hydrated = hydrateLiveInboxSnapshot(poisoned);
      assert.equal(hydrated.context, null);
      assert.deepEqual(hydrated.messages, []);
    }

    for (const poisoned of [
      snapshot("google", "gmail-row-mailbox", {
        messages: [
          message({
            serverMailboxId: "other-mailbox",
            providerFolder: "Inbox",
            providerMessageId: "gmail-row-mailbox-provider-message",
          }),
        ],
      }),
      snapshot("custom_imap", "imap-row-mailbox", {
        messages: [
          message({
            serverMailboxId: "other-mailbox",
            providerFolder: "INBOX",
            uidValidity: "900",
            imapUid: "42",
          }),
        ],
      }),
      customMixedIdentity,
    ]) {
      const hydrated = hydrateLiveInboxSnapshot(poisoned);
      assert.equal(hydrated.context, null);
      assert.deepEqual(hydrated.messages, []);
    }
  });
}

function testInboxOnlyPersistence() {
  withMemoryLocalStorage((store) => {
    saveLiveInboxSnapshot(snapshot("google", "gmail-inbox"));
    saveLiveInboxSnapshot(snapshot("custom_imap", "imap-inbox"));
    const beforePoisonedWrites = store.get(STORAGE_KEY);

    saveLiveInboxSnapshot(
      snapshot("google", "gmail-trash", { folder: "Trash" }),
    );
    saveLiveInboxSnapshot(
      snapshot("custom_imap", "imap-archive", { folder: "Archive" }),
    );
    saveLiveInboxSnapshot(snapshot("google", " invalid-mailbox "));

    assert.equal(store.get(STORAGE_KEY), beforePoisonedWrites);
    const persisted = JSON.parse(store.get(STORAGE_KEY) ?? "{}");
    assert.deepEqual(Object.keys(persisted).sort(), [
      "gmail-inbox",
      "imap-inbox",
    ]);
    assert.equal(persisted["gmail-inbox"].folder, "INBOX");
    assert.equal(persisted["imap-inbox"].folder, "INBOX");
  });
}

function testExactCustomImapSnapshotRemoval() {
  const target = message({
    id: "target",
    serverMailboxId: "imap-mailbox",
    providerFolder: "INBOX",
    uidValidity: "900",
    imapUid: "42",
  });
  const other = message({
    id: "other",
    serverMailboxId: "imap-mailbox",
    providerFolder: "INBOX",
    uidValidity: "900",
    imapUid: "43",
  });
  const source = snapshot("custom_imap", "imap-mailbox", {
    uidValidity: "900",
    messages: [target, other],
  });

  const removed = removeCustomImapInboxMessageFromSnapshot(
    source,
    "imap-mailbox",
    "900",
    "42",
  );
  assert.notStrictEqual(removed, source);
  assert.deepEqual(removed?.messages, [other]);

  const duplicate = {
    ...source,
    messages: [target, { ...target, id: "duplicate-target" }],
  };
  assert.strictEqual(
    removeCustomImapInboxMessageFromSnapshot(
      duplicate,
      "imap-mailbox",
      "900",
      "42",
    ),
    duplicate,
  );

  for (const [mailboxId, uidValidity, imapUid] of [
    ["other-mailbox", "900", "42"],
    ["imap-mailbox", "901", "42"],
    ["imap-mailbox", "900", "44"],
  ] as const) {
    assert.strictEqual(
      removeCustomImapInboxMessageFromSnapshot(
        source,
        mailboxId,
        uidValidity,
        imapUid,
      ),
      source,
    );
  }

  let persistedSnapshot: LiveInboxSnapshot | null = null;
  const persisted = removeAndPersistCustomImapInboxMessageFromSnapshot(
    source,
    "imap-mailbox",
    "900",
    "42",
    (nextSnapshot) => {
      persistedSnapshot = nextSnapshot;
    },
  );
  assert.equal(persisted.changed, true);
  assert.strictEqual(persisted.snapshot, persistedSnapshot);
  assert.deepEqual(persisted.snapshot?.messages, [other]);

  const bestEffort = removeAndPersistCustomImapInboxMessageFromSnapshot(
    source,
    "imap-mailbox",
    "900",
    "42",
    () => {
      throw new Error("persistence unavailable");
    },
  );
  assert.equal(bestEffort.changed, true);
  assert.deepEqual(bestEffort.snapshot?.messages, [other]);

  let duplicatePersistCalls = 0;
  const duplicateResult = removeAndPersistCustomImapInboxMessageFromSnapshot(
    duplicate,
    "imap-mailbox",
    "900",
    "42",
    () => {
      duplicatePersistCalls += 1;
    },
  );
  assert.equal(duplicateResult.changed, false);
  assert.strictEqual(duplicateResult.snapshot, duplicate);
  assert.equal(duplicatePersistCalls, 0);
}

testTrustedInboxAuthority();
testInboxOnlyPersistence();
testExactCustomImapSnapshotRemoval();

console.log("liveInboxSnapshots Trash authority tests passed");
