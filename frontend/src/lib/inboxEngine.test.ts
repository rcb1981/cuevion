/**
 * Tests for inboxEngine.ts — pruneInboxSnapshot, thread recovery, and helpers.
 *
 * Run with:
 *   cd frontend && node_modules/.bin/sucrase-node src/lib/inboxEngine.test.ts
 *
 * Uses only Node.js built-in assert; no test framework required.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import {
  applyLiveThreadIdentity,
  buildMailboxScopedThreadGroupingKey,
  buildConservativeLiveCustomImapThreadId,
  buildRenderedConversationRows,
  dedupeMailboxScopedMessageCopies,
  mergeLiveInboxMessageState,
  normalizeThreadSubject,
  resolveLiveCustomImapThreadId,
  resolveThreadKey,
  resolveMessageDateMs,
  dedupeLatestMessagePerThread,
  pruneInboxSnapshot,
  INBOX_SNAPSHOT_MAX_MESSAGES,
  INBOX_SNAPSHOT_MAX_AGE_MS,
  INBOX_SNAPSHOT_RECENT_GUARD_MS,
  type LiveThreadIdentityContext,
} from "./inboxEngine";
import {
  hydrateLiveInboxSnapshot,
  LIVE_INBOX_THREAD_IDENTITY_VERSION,
  MUSIC_CLASSIFIER_VERSION,
  readLiveInboxSnapshots,
  removeAndPersistGmailInboxProviderMessageFromSnapshot,
  removeGmailInboxProviderMessageFromSnapshot,
  saveLiveInboxSnapshot,
} from "./liveInboxSnapshots";
import { createGmailInboxAuthority } from "./mailboxRefreshSemantics";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let passed = 0;
let failed = 0;

function test(name: string, fn: () => void) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (err) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${(err as Error).message}`);
    failed++;
  }
}

function msAgo(ms: number) {
  return new Date(Date.now() - ms).toISOString();
}

const DAY = 24 * 60 * 60 * 1000;

// Minimal message shape accepted by pruneInboxSnapshot
type Msg = {
  id: string;
  subject: string;
  unread?: boolean;
  createdAt?: string;
  timestamp?: string;
  threadId?: string;
};

function msg(overrides: Partial<Msg> & { id: string }): Msg {
  return {
    subject: "Default subject",
    unread: false,
    ...overrides,
  };
}

/** Build a set of `n` distinct old messages (> 90 days) without threadId. */
function oldMsgs(n: number, baseAgo = 100 * DAY): Msg[] {
  return Array.from({ length: n }, (_, i) =>
    msg({
      id: `old-${i}`,
      subject: `Old subject ${i}`,
      createdAt: msAgo(baseAgo + i * 1000),
    }),
  );
}

function withMemoryLocalStorage(fn: (store: Map<string, string>) => void) {
  const store = new Map<string, string>();
  const previousWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
    },
  };

  try {
    fn(store);
  } finally {
    (globalThis as { window?: unknown }).window = previousWindow;
  }
}

// ---------------------------------------------------------------------------
// normalizeThreadSubject
// ---------------------------------------------------------------------------

console.log("\nnormalizeThreadSubject");

test("strips Re: prefix", () => {
  assert.equal(normalizeThreadSubject("Re: Hello"), "hello");
});

test("strips Fwd: prefix case-insensitively", () => {
  assert.equal(normalizeThreadSubject("FWD: Hello"), "hello");
});

test("strips fw: prefix", () => {
  assert.equal(normalizeThreadSubject("fw: Hello"), "hello");
});

test("collapses whitespace", () => {
  assert.equal(normalizeThreadSubject("  Hello   World  "), "hello world");
});

test("lowercases", () => {
  assert.equal(normalizeThreadSubject("HELLO"), "hello");
});

// ---------------------------------------------------------------------------
// resolveThreadKey
// ---------------------------------------------------------------------------

console.log("\nresolveThreadKey");

test("uses threadId when present", () => {
  const key = resolveThreadKey({ threadId: "t123", subject: "Re: x", from: "a@b.com" });
  assert.equal(key, "thread:t123");
});

test("falls back to subject+from when threadId is absent", () => {
  const key = resolveThreadKey({ subject: "Re: Hello", from: "A@B.com" });
  assert.match(key, /^fallback:hello\|a@b\.com$/);
});

test("falls back when threadId is empty string", () => {
  const key = resolveThreadKey({ threadId: "", subject: "Hello", from: "x@y.com" });
  assert.match(key, /^fallback:/);
});

// ---------------------------------------------------------------------------
// resolveMessageDateMs
// ---------------------------------------------------------------------------

console.log("\nresolveMessageDateMs");

const ISO = "2024-01-15T10:00:00.000Z";
const ISO_MS = new Date(ISO).getTime();

test("returns createdAt as ms", () => {
  assert.equal(resolveMessageDateMs({ createdAt: ISO }), ISO_MS);
});

test("falls back to timestamp when createdAt is absent", () => {
  assert.equal(resolveMessageDateMs({ timestamp: ISO }), ISO_MS);
});

test("returns 0 when both fields are absent", () => {
  assert.equal(resolveMessageDateMs({}), 0);
});

test("returns 0 when both fields are invalid", () => {
  assert.equal(resolveMessageDateMs({ createdAt: "not-a-date", timestamp: "also-bad" }), 0);
});

// ---------------------------------------------------------------------------
// pruneInboxSnapshot — fast path
// ---------------------------------------------------------------------------

console.log("\npruneInboxSnapshot — fast path");

test("returns messages unchanged when under budget", () => {
  const messages = oldMsgs(5);
  const result = pruneInboxSnapshot(messages, Date.now());
  assert.deepEqual(result, messages);
});

test("returns messages unchanged when exactly at budget", () => {
  const messages = oldMsgs(INBOX_SNAPSHOT_MAX_MESSAGES);
  const result = pruneInboxSnapshot(messages, Date.now());
  assert.equal(result.length, INBOX_SNAPSHOT_MAX_MESSAGES);
});

// ---------------------------------------------------------------------------
// pruneInboxSnapshot — count cap
// ---------------------------------------------------------------------------

console.log("\npruneInboxSnapshot — count cap");

test("prunes down to budget when over the count limit", () => {
  // 850 old messages (all > 90 days, all read) — should be pruned to 800
  const messages = oldMsgs(INBOX_SNAPSHOT_MAX_MESSAGES + 50);
  const result = pruneInboxSnapshot(messages, Date.now());
  assert.ok(
    result.length <= INBOX_SNAPSHOT_MAX_MESSAGES,
    `Expected ≤ ${INBOX_SNAPSHOT_MAX_MESSAGES}, got ${result.length}`,
  );
});

test("keeps newest messages when pruning for count", () => {
  const nowMs = Date.now();
  // Mix: 750 very old (120 days) + 100 less old (91 days) = 850 total
  const veryOld = Array.from({ length: 750 }, (_, i) =>
    msg({ id: `veryold-${i}`, subject: `VeryOld ${i}`, createdAt: new Date(nowMs - 120 * DAY - i).toISOString() }),
  );
  const lessOld = Array.from({ length: 100 }, (_, i) =>
    msg({ id: `lessold-${i}`, subject: `LessOld ${i}`, createdAt: new Date(nowMs - 91 * DAY - i).toISOString() }),
  );
  const result = pruneInboxSnapshot([...veryOld, ...lessOld], nowMs);
  // The 750 very-old messages exceed INBOX_SNAPSHOT_MAX_AGE_MS — all skipped
  // The 100 less-old messages are within age limit — kept
  assert.ok(result.length <= INBOX_SNAPSHOT_MAX_MESSAGES);
  // All kept messages should be from "lessold" since veryold exceed max age
  const hasVeryOld = result.some((m) => m.id.startsWith("veryold"));
  assert.equal(hasVeryOld, false, "Very old messages should be pruned");
});

// ---------------------------------------------------------------------------
// pruneInboxSnapshot — unread protection
// ---------------------------------------------------------------------------

console.log("\npruneInboxSnapshot — unread protection");

test("never prunes a thread containing an unread message", () => {
  const nowMs = Date.now();
  // Over budget: 801 old messages, all read, plus 1 ancient unread
  const messages = [
    ...oldMsgs(INBOX_SNAPSHOT_MAX_MESSAGES + 1),
    msg({ id: "unread-ancient", subject: "Ancient unread", unread: true, createdAt: msAgo(200 * DAY) }),
  ];
  const result = pruneInboxSnapshot(messages, nowMs);
  const found = result.find((m) => m.id === "unread-ancient");
  assert.ok(found, "Ancient unread message must be preserved");
});

// ---------------------------------------------------------------------------
// pruneInboxSnapshot — recently active protection (14-day guard)
// ---------------------------------------------------------------------------

console.log("\npruneInboxSnapshot — recently active guard");

test("protects threads active within the guard window", () => {
  const nowMs = Date.now();
  // Over budget with old messages
  const messages = [
    ...oldMsgs(INBOX_SNAPSHOT_MAX_MESSAGES + 1),
    msg({ id: "recent-read", subject: "Recent subject", createdAt: msAgo(7 * DAY) }),
  ];
  const result = pruneInboxSnapshot(messages, nowMs);
  const found = result.find((m) => m.id === "recent-read");
  assert.ok(found, "Recently active thread must be preserved");
});

test("does NOT protect threads older than the guard window", () => {
  const nowMs = Date.now();
  // 1 message at exactly 15 days old (outside guard) + 800 other old messages
  const messages = [
    ...oldMsgs(INBOX_SNAPSHOT_MAX_MESSAGES),
    msg({ id: "outside-guard", subject: "Borderline thread", createdAt: msAgo(15 * DAY) }),
  ];
  const result = pruneInboxSnapshot(messages, nowMs);
  // The outside-guard message is eligible but the 800 old ones already fill budget
  assert.ok(result.length <= INBOX_SNAPSHOT_MAX_MESSAGES);
});

// ---------------------------------------------------------------------------
// pruneInboxSnapshot — unknown date (getDateMs edge case fix)
// ---------------------------------------------------------------------------

console.log("\npruneInboxSnapshot — unknown date protection");

test("protects a message with missing createdAt (no accidental ancient treatment)", () => {
  const nowMs = Date.now();
  // Over budget — all old messages plus one with NO date
  const messages = [
    ...oldMsgs(INBOX_SNAPSHOT_MAX_MESSAGES + 1),
    msg({ id: "no-date", subject: "No date at all" }),
  ];
  const result = pruneInboxSnapshot(messages, nowMs);
  const found = result.find((m) => m.id === "no-date");
  assert.ok(found, "Message with missing date must be protected (treated as recent)");
});

test("protects a message with invalid createdAt but valid timestamp", () => {
  const nowMs = Date.now();
  const messages = [
    ...oldMsgs(INBOX_SNAPSHOT_MAX_MESSAGES + 1),
    msg({
      id: "ts-only",
      subject: "Has timestamp only",
      createdAt: "not-a-valid-date",
      timestamp: msAgo(2 * DAY), // recent via timestamp
    }),
  ];
  const result = pruneInboxSnapshot(messages, nowMs);
  const found = result.find((m) => m.id === "ts-only");
  assert.ok(found, "Message with valid timestamp must be protected as recently active");
});

test("protects a message with invalid createdAt and no timestamp (unknown age)", () => {
  const nowMs = Date.now();
  const messages = [
    ...oldMsgs(INBOX_SNAPSHOT_MAX_MESSAGES + 1),
    msg({ id: "bad-date", subject: "Bad date everywhere", createdAt: "not-a-date" }),
  ];
  const result = pruneInboxSnapshot(messages, nowMs);
  const found = result.find((m) => m.id === "bad-date");
  assert.ok(found, "Message with unparseable date must be protected (unknown age = safe)");
});

// ---------------------------------------------------------------------------
// pruneInboxSnapshot — thread recovery (primary scenario)
// ---------------------------------------------------------------------------

console.log("\npruneInboxSnapshot — thread recovery");

test("new message for a pruned thread is added to snapshot and protected", () => {
  const nowMs = Date.now();

  // Step 1: simulate a snapshot that's been pruned (starts clean, under budget)
  const existingSnapshot = oldMsgs(10); // 10 old messages, all read

  // Step 2: a new message arrives for a previously pruned thread
  const newMessage = msg({
    id: "recovery-new",
    subject: "Thread that was pruned",
    threadId: "gmail-thread-abc",
    createdAt: msAgo(1 * DAY), // very recent
  });

  // Simulate mergePersistedLiveInboxSnapshotMessages: upsert newMessage into snapshot
  const merged = [...existingSnapshot, newMessage];

  // Prune the merged result (under budget, so no pruning needed here)
  const result = pruneInboxSnapshot(merged, nowMs);

  // New message must survive
  const found = result.find((m) => m.id === "recovery-new");
  assert.ok(found, "Recovered thread message must be present in snapshot");
});

test("new message for pruned thread survives even when snapshot is over budget", () => {
  const nowMs = Date.now();

  // Snapshot is over budget with old messages
  const existingSnapshot = oldMsgs(INBOX_SNAPSHOT_MAX_MESSAGES + 10);

  // New message arrives for a previously pruned thread
  const newMessage = msg({
    id: "recovery-busy",
    subject: "Recovered thread",
    threadId: "gmail-thread-xyz",
    createdAt: msAgo(2 * DAY),
  });

  const merged = [...existingSnapshot, newMessage];
  const result = pruneInboxSnapshot(merged, nowMs);

  const found = result.find((m) => m.id === "recovery-busy");
  assert.ok(found, "Recovered thread must survive pruning due to recent activity");
  assert.ok(result.length <= INBOX_SNAPSHOT_MAX_MESSAGES, "Total must be within budget");
});

test("thread recovery does not create duplicates", () => {
  const nowMs = Date.now();

  // New message with a threadId that already has older messages in snapshot
  const oldThreadMsg = msg({
    id: "thread-old",
    subject: "Ongoing thread",
    threadId: "gmail-thread-def",
    createdAt: msAgo(5 * DAY),
  });
  const newThreadMsg = msg({
    id: "thread-new",
    subject: "Re: Ongoing thread",
    threadId: "gmail-thread-def",
    createdAt: msAgo(1 * DAY),
  });

  const merged = [...oldMsgs(5), oldThreadMsg, newThreadMsg];
  const result = pruneInboxSnapshot(merged, nowMs);

  // Both messages should be present since thread is active
  const ids = result.map((m) => m.id);
  assert.ok(ids.includes("thread-old"), "Old thread message must be preserved");
  assert.ok(ids.includes("thread-new"), "New thread message must be present");
  // No duplicates
  const uniqueIds = new Set(ids);
  assert.equal(uniqueIds.size, ids.length, "No duplicate messages");
});

// ---------------------------------------------------------------------------
// dedupeLatestMessagePerThread
// ---------------------------------------------------------------------------

console.log("\ndedupeLatestMessagePerThread");

test("returns one message per thread (latest)", () => {
  const base = { from: "a@b.com" };
  const messages = [
    { id: "m1", subject: "Hello", createdAt: msAgo(10 * DAY), ...base },
    { id: "m2", subject: "Re: Hello", createdAt: msAgo(5 * DAY), ...base },
    { id: "m3", subject: "Re: Hello", createdAt: msAgo(2 * DAY), ...base },
  ];
  const result = dedupeLatestMessagePerThread(messages);
  assert.equal(result.length, 1);
  assert.equal(result[0].id, "m3", "Should keep the most recent message");
});

test("preserves distinct threads", () => {
  const messages = [
    { id: "a1", subject: "Thread A", from: "a@a.com", createdAt: msAgo(3 * DAY) },
    { id: "b1", subject: "Thread B", from: "b@b.com", createdAt: msAgo(2 * DAY) },
  ];
  const result = dedupeLatestMessagePerThread(messages);
  assert.equal(result.length, 2);
});

test("authoritative IMAP IDs keep same-subject submissions in separate rendered rows", () => {
  const messages = [
    {
      id: "submission-1",
      threadId: "imap:rfc:first%40example.com",
      subject: "Demo Submission via website",
      from: "Hysteriarecs.com <forms@example.com>",
      createdAt: msAgo(2 * DAY),
    },
    {
      id: "submission-2",
      threadId: "imap:rfc:second%40example.com",
      subject: "Demo Submission via website",
      from: "Hysteriarecs.com <forms@example.com>",
      createdAt: msAgo(1 * DAY),
    },
  ];
  const renderedRows = dedupeLatestMessagePerThread(messages);

  assert.equal(renderedRows.length, 2);
  renderedRows.forEach((row) => {
    const rowThreadKey = resolveThreadKey(row);
    assert.equal(
      messages.filter((message) => resolveThreadKey(message) === rowThreadKey).length,
      1,
    );
  });
});

test("authoritative RFC root groups a real reply chain with the complete count", () => {
  const messages = [
    { id: "root", threadId: "imap:rfc:root%40example.com", subject: "Question", createdAt: msAgo(3 * DAY) },
    { id: "reply", threadId: "imap:rfc:root%40example.com", subject: "Re: Question", createdAt: msAgo(2 * DAY) },
    { id: "reply-2", threadId: "imap:rfc:root%40example.com", subject: "Re: Question", createdAt: msAgo(1 * DAY) },
  ];
  const renderedRows = dedupeLatestMessagePerThread(messages);

  assert.equal(renderedRows.length, 1);
  assert.equal(
    messages.filter(
      (message) => resolveThreadKey(message) === resolveThreadKey(renderedRows[0]),
    ).length,
    3,
  );
});

test("missing live IMAP thread IDs receive message-unique non-subject fallbacks", () => {
  const context: LiveThreadIdentityContext = {
    mailboxId: "demo-mailbox",
    provider: "custom_imap",
    folder: "INBOX",
    uidValidity: "900",
  };
  const first = resolveLiveCustomImapThreadId(
    { id: "message-1", imapUid: "1" },
    context,
  );
  const second = resolveLiveCustomImapThreadId(
    { id: "message-2", imapUid: "2" },
    context,
  );

  assert.notEqual(first, second);
  assert.match(first, /^imap:uid:/);
  assert.doesNotMatch(first, /demo submission via website/);
  assert.equal(
    resolveLiveCustomImapThreadId(
      { id: "message-1", imapUid: "1", threadId: "imap:rfc:root%40example.com" },
      context,
    ),
    "imap:rfc:root%40example.com",
  );
  assert.equal(
    first,
    buildConservativeLiveCustomImapThreadId(
      { id: "message-1", imapUid: "1" },
      context,
    ),
  );
});

test("live identity context is stable across refresh/reload and changes with UIDVALIDITY", () => {
  const message = { id: "message-1", imapUid: "42" };
  const inboxContext: LiveThreadIdentityContext = {
    mailboxId: "mailbox-1",
    provider: "custom_imap",
    folder: "INBOX",
    uidValidity: "900",
  };
  const archiveContext = { ...inboxContext, folder: "Archive" };
  const nextUidValidityContext = { ...inboxContext, uidValidity: "901" };

  const refreshed = applyLiveThreadIdentity(message, inboxContext);
  const reloaded = applyLiveThreadIdentity(message, inboxContext);
  assert.equal(refreshed.threadId, reloaded.threadId);
  assert.notEqual(refreshed.threadId, applyLiveThreadIdentity(message, archiveContext).threadId);
  assert.notEqual(
    refreshed.threadId,
    applyLiveThreadIdentity(message, nextUidValidityContext).threadId,
  );
  assert.equal(
    applyLiveThreadIdentity(
      { ...message, threadId: "imap:rfc:server-root" },
      inboxContext,
    ).threadId,
    "imap:rfc:server-root",
  );
});

// ---------------------------------------------------------------------------
// liveInboxSnapshots — classifier version
// ---------------------------------------------------------------------------

console.log("\nliveInboxSnapshots — classifier version");

test("mailbox-scoped message dedupe retains cross-mailbox UID collisions", () => {
  const mailboxA: LiveThreadIdentityContext = {
    mailboxId: "mailbox-a",
    provider: "custom_imap",
    folder: "INBOX",
    uidValidity: "900",
  };
  const mailboxB = { ...mailboxA, mailboxId: "mailbox-b" };
  const first = { id: "same-provider-id", imapUid: "42", threadId: "same-rfc-root" };
  const duplicateFirst = { ...first };
  const second = { id: "same-provider-id", imapUid: "42", threadId: "same-rfc-root" };

  const deduped = dedupeMailboxScopedMessageCopies([
    { message: first, context: mailboxA },
    { message: duplicateFirst, context: mailboxA },
    { message: second, context: mailboxB },
  ]);

  assert.equal(deduped.length, 2);
  assert.deepEqual(
    deduped.map((record) => record.context.mailboxId).sort(),
    ["mailbox-a", "mailbox-b"],
  );
  assert.notEqual(
    buildMailboxScopedThreadGroupingKey("same-rfc-root", "mailbox-a"),
    buildMailboxScopedThreadGroupingKey("same-rfc-root", "mailbox-b"),
  );
});

type RenderedMessage = {
  id: string;
  imapUid?: string;
  threadId?: string;
  subject: string;
  from: string;
  to: string;
  createdAt: string;
};

const mailboxAContext: LiveThreadIdentityContext = {
  mailboxId: "mailbox-a",
  provider: "custom_imap",
  folder: "INBOX",
  uidValidity: "900",
};
const mailboxBContext: LiveThreadIdentityContext = {
  ...mailboxAContext,
  mailboxId: "mailbox-b",
};

function renderedMessage(
  id: string,
  overrides: Partial<RenderedMessage> = {},
): RenderedMessage {
  return {
    id,
    imapUid: id,
    threadId: `imap:rfc:${id}`,
    subject: "Shared subject",
    from: "sender@example.com",
    to: "owner@example.com",
    createdAt: "2026-07-13T08:00:00.000Z",
    ...overrides,
  };
}

test("normal Inbox row pipeline retains cross-mailbox UID collisions", () => {
  const rows = buildRenderedConversationRows([
    {
      message: renderedMessage("mailbox-a-message", {
        imapUid: "42",
        threadId: "imap:uid:shared",
      }),
      context: mailboxAContext,
    },
    {
      message: renderedMessage("mailbox-b-message", {
        imapUid: "42",
        threadId: "imap:uid:shared",
      }),
      context: mailboxBContext,
    },
  ]);

  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map((row) => row.context.mailboxId).sort(), ["mailbox-a", "mailbox-b"]);
});

test("Smart Folder row pipeline retains cross-mailbox UID collisions", () => {
  const smartFolderRecords = [
    {
      message: renderedMessage("smart-a", { imapUid: "42", threadId: "imap:uid:shared" }),
      context: mailboxAContext,
    },
    {
      message: renderedMessage("smart-b", { imapUid: "42", threadId: "imap:uid:shared" }),
      context: mailboxBContext,
    },
  ];
  const smartFolderRows = buildRenderedConversationRows(smartFolderRecords);
  const normalInboxRows = buildRenderedConversationRows(smartFolderRecords);

  assert.equal(smartFolderRows.length, 2);
  assert.deepEqual(smartFolderRows, normalInboxRows);
});

test("same RFC identity in two mailboxes remains two rendered rows", () => {
  const rows = buildRenderedConversationRows([
    {
      message: renderedMessage("rfc-a", { threadId: "imap:rfc:shared-root" }),
      context: mailboxAContext,
    },
    {
      message: renderedMessage("rfc-b", { threadId: "imap:rfc:shared-root" }),
      context: mailboxBContext,
    },
  ]);

  assert.equal(rows.length, 2);
  assert.notEqual(rows[0]?.threadKey, rows[1]?.threadKey);
});

test("same-mailbox duplicate message produces one rendered row", () => {
  const duplicate = renderedMessage("duplicate", { imapUid: "42" });
  const rows = buildRenderedConversationRows([
    { message: duplicate, context: mailboxAContext },
    { message: { ...duplicate }, context: mailboxAContext },
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0]?.threadCount, 1);
});

test("genuine parent and reply produce one newest representative with count two", () => {
  const rows = buildRenderedConversationRows([
    {
      message: renderedMessage("parent", {
        imapUid: "1",
        threadId: "imap:rfc:root",
        subject: "Question",
        createdAt: "2026-07-13T08:00:00.000Z",
      }),
      context: mailboxAContext,
    },
    {
      message: renderedMessage("reply", {
        imapUid: "2",
        threadId: "imap:rfc:root",
        subject: "Re: Question",
        createdAt: "2026-07-13T09:00:00.000Z",
      }),
      context: mailboxAContext,
    },
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0]?.threadCount, 2);
  assert.equal(rows[0]?.message.id, "reply");
});

test("eight same-subject authoritative threads remain eight single-message rows", () => {
  const records = Array.from({ length: 8 }, (_, index) => ({
    message: renderedMessage(`submission-${index}`, {
      imapUid: String(index + 1),
      threadId: `imap:rfc:submission-${index}`,
      subject: "Demo Submission via website",
    }),
    context: mailboxAContext,
  }));
  const rows = buildRenderedConversationRows(records);

  assert.equal(rows.length, 8);
  assert.ok(rows.every((row) => row.threadCount === 1));
});

test("different authoritative thread IDs never regroup by equal subject and participants", () => {
  const rows = buildRenderedConversationRows([
    {
      message: renderedMessage("first", { threadId: "imap:rfc:first" }),
      context: mailboxAContext,
    },
    {
      message: renderedMessage("second", { threadId: "imap:rfc:second" }),
      context: mailboxAContext,
    },
  ]);

  assert.equal(rows.length, 2);
  assert.ok(rows.every((row) => row.threadCount === 1));
});

function richLiveMessage(overrides: Record<string, unknown> = {}) {
  return {
    id: "message-42",
    imapUid: "42",
    sender: "Sender",
    subject: "Same subject",
    snippet: "Fresh snippet",
    from: "sender@example.com",
    to: "owner@example.com",
    timestamp: "July 13 at 10:00",
    createdAt: "2026-07-13T08:00:00.000Z",
    body: ["Fresh body"],
    bodyHtml: "<p>Fresh body</p>",
    attachments: [{ id: "attachment-1", name: "demo.wav" }],
    unread: false,
    flagged: true,
    ui_signal: "DEMO",
    internalClassification: "demo",
    category: "Primary",
    classifierVersion: MUSIC_CLASSIFIER_VERSION,
    collaboration: { updatedAt: 10, messages: [{ id: "fresh-note" }] },
    isShared: true,
    ...overrides,
  };
}

test("direct refresh merge replaces stale identity and preserves current state rules", () => {
  const existing = richLiveMessage({
    threadId: "same subject",
    unread: true,
    flagged: false,
    collaboration: { updatedAt: 20, messages: [{ id: "newer-local-note" }] },
  });
  const incoming = richLiveMessage({
    threadId: "imap:rfc:mailbox-a:INBOX:root%40example.com",
    collaboration: { updatedAt: 10, messages: [{ id: "older-server-note" }] },
  });
  const merged = mergeLiveInboxMessageState(
    incoming,
    existing,
    mailboxAContext,
    {
      providerStateIsFresh: true,
      preferExistingUnreadWhenProviderStateIsNotFresh: true,
    },
  );

  assert.equal(merged.threadId, "imap:rfc:mailbox-a:INBOX:root%40example.com");
  assert.equal(merged.unread, false);
  assert.equal(merged.flagged, true);
  assert.deepEqual(merged.attachments, [{ id: "attachment-1", name: "demo.wav" }]);
  assert.equal(merged.internalClassification, "demo");
  assert.equal(merged.category, "Primary");
  assert.deepEqual(merged.body, ["Fresh body"]);
  assert.equal(merged.bodyHtml, "<p>Fresh body</p>");
  assert.deepEqual(merged.collaboration, {
    updatedAt: 20,
    messages: [{ id: "newer-local-note" }],
  });
});

test("snapshot save/read/hydration and cached recovery preserve authoritative identity and state", () => {
  withMemoryLocalStorage(() => {
    const frontendFallback = mergeLiveInboxMessageState(
      richLiveMessage({ threadId: undefined }),
      undefined,
      mailboxAContext,
      {
        providerStateIsFresh: false,
        preferExistingUnreadWhenProviderStateIsNotFresh: true,
      },
    );
    const authoritative = mergeLiveInboxMessageState(
      richLiveMessage({ threadId: "imap:rfc:mailbox-a:INBOX:root%40example.com" }),
      frontendFallback,
      mailboxAContext,
      {
        providerStateIsFresh: true,
        preferExistingUnreadWhenProviderStateIsNotFresh: true,
      },
    );

    assert.notEqual(frontendFallback.threadId, authoritative.threadId);
    saveLiveInboxSnapshot({
      provider: "custom_imap",
      inboxId: "mailbox-a",
      email: "owner@example.com",
      fetchedAt: "2026-07-13T08:00:00.000Z",
      folder: "INBOX",
      uidValidity: "900",
      messages: [authoritative] as any,
    });

    const snapshot = readLiveInboxSnapshots({
      "mailbox-a": {
        mailboxId: "mailbox-a",
        provider: "custom_imap",
        folder: "INBOX",
      },
    })["mailbox-a"];
    assert.ok(snapshot);
    assert.equal(snapshot.provider, "custom_imap");
    assert.equal(snapshot.inboxId, "mailbox-a");
    assert.equal(snapshot.folder, "INBOX");
    assert.equal(snapshot.uidValidity, "900");
    assert.equal(snapshot.threadIdentityVersion, LIVE_INBOX_THREAD_IDENTITY_VERSION);

    const hydrated = hydrateLiveInboxSnapshot(snapshot);
    assert.ok(hydrated.context);
    assert.equal(hydrated.messages[0]?.threadId, authoritative.threadId);
    const cached = mergeLiveInboxMessageState(
      hydrated.messages[0] as any,
      undefined,
      hydrated.context as LiveThreadIdentityContext,
      {
        providerStateIsFresh: false,
        preferExistingUnreadWhenProviderStateIsNotFresh: true,
      },
    );
    assert.equal(cached.threadId, authoritative.threadId);
    assert.doesNotMatch(cached.threadId ?? "", /^same subject$/);

    for (const key of [
      "id",
      "unread",
      "flagged",
      "attachments",
      "internalClassification",
      "category",
      "body",
      "bodyHtml",
      "collaboration",
    ] as const) {
      assert.deepEqual((cached as any)[key], (authoritative as any)[key], key);
    }
  });
});

test("non-default folder survives save/read/hydration and UIDVALIDITY changes fallback identity", () => {
  withMemoryLocalStorage(() => {
    const archiveContext: LiveThreadIdentityContext = {
      ...mailboxAContext,
      folder: "Archive/2026",
    };
    const fallback = mergeLiveInboxMessageState(
      richLiveMessage({ threadId: undefined }),
      undefined,
      archiveContext,
      {
        providerStateIsFresh: false,
        preferExistingUnreadWhenProviderStateIsNotFresh: true,
      },
    );
    saveLiveInboxSnapshot({
      provider: "custom_imap",
      inboxId: "mailbox-a",
      email: "owner@example.com",
      fetchedAt: "2026-07-13T08:00:00.000Z",
      folder: "Archive/2026",
      uidValidity: "900",
      messages: [fallback] as any,
    });
    const snapshot = readLiveInboxSnapshots({
      "mailbox-a": {
        mailboxId: "mailbox-a",
        provider: "custom_imap",
        folder: "Archive/2026",
      },
    })["mailbox-a"];
    const hydrated = hydrateLiveInboxSnapshot(snapshot);
    const refreshed = mergeLiveInboxMessageState(
      richLiveMessage({ threadId: undefined }),
      undefined,
      hydrated.context as LiveThreadIdentityContext,
      {
        providerStateIsFresh: true,
        preferExistingUnreadWhenProviderStateIsNotFresh: true,
      },
    );
    const nextUidValidity = mergeLiveInboxMessageState(
      richLiveMessage({ threadId: undefined }),
      undefined,
      { ...(hydrated.context as LiveThreadIdentityContext), uidValidity: "901" },
      {
        providerStateIsFresh: true,
        preferExistingUnreadWhenProviderStateIsNotFresh: true,
      },
    );

    assert.equal(snapshot.folder, "Archive/2026");
    assert.equal(hydrated.context?.folder, "Archive/2026");
    assert.equal(refreshed.threadId, fallback.threadId);
    assert.match(refreshed.threadId ?? "", /Archive%2F2026/);
    assert.notEqual(nextUidValidity.threadId, refreshed.threadId);
  });
});

test("WorkspaceShell lazily resolves Smart Folder labels from a local thread index", () => {
  const workspaceShellSource = fs.readFileSync(
    "src/components/workspace/WorkspaceShell.tsx",
    "utf8",
  );
  const smartFolderEvaluatorSource = workspaceShellSource.match(
    /function doesMessageMatchSmartFolder\([\s\S]*?\n}\n\nfunction hasSignatureContent/,
  )?.[0];
  const smartFolderRuntimeSource = workspaceShellSource.match(
    /const resolveSmartFolderContentLabelForMessage = \([\s\S]*?const smartFolderEntries =/,
  )?.[0];

  assert.ok(smartFolderEvaluatorSource, "Smart Folder rule evaluator must exist");
  assert.match(smartFolderEvaluatorSource, /return folder\.rules\.some\(\(rule\) => \{/);
  assert.match(
    smartFolderEvaluatorSource,
    /if \(rule\.field === "Label" && !didResolveLabelOptions\) \{\s+labelOptions = resolveLabelOptions\?\.\(\);\s+didResolveLabelOptions = true;/,
  );
  assert.match(
    smartFolderEvaluatorSource,
    /rule\.field === "Label" \? labelOptions : undefined/,
  );

  assert.ok(smartFolderRuntimeSource, "Smart Folder mailbox runtime must exist");
  assert.match(
    smartFolderRuntimeSource,
    /let threadMessagesBySafeKey: Map<string, MailMessage\[]> \| null = null;/,
  );
  assert.match(
    smartFolderRuntimeSource,
    /mailboxStore\[mailboxId\] \?\? createEmptyMailboxCollections\(\)/,
  );
  assert.match(
    smartFolderRuntimeSource,
    /canonicalFolderOrder\.forEach\(\(sourceFolder\) => \{\s+mailboxCollectionsForThread\[sourceFolder\]\.forEach\(\(sourceMessage\) => \{/,
  );
  assert.match(
    smartFolderRuntimeSource,
    /existingThreadMessages\.push\(sourceMessage\)/,
  );
  assert.match(
    smartFolderRuntimeSource,
    /nextThreadMessagesBySafeKey\.set\(safeThreadKey, \[sourceMessage\]\)/,
  );
  assert.match(
    smartFolderRuntimeSource,
    /threadMessagesBySafeKey\.get\(resolveSafeThreadGroupingKey\(message, mailboxId\)\) \?\? \[]/,
  );
  assert.match(
    smartFolderRuntimeSource,
    /getRecentThreadMessages\(message, threadSourceMessages, \{\s+mailboxId,\s+useSafeGrouping: true,/,
  );
  assert.match(
    smartFolderRuntimeSource,
    /\(\) =>\s+resolveSmartFolderRuleMatchOptions\(\s+message,\s+mailboxId,\s+getThreadSourceMessagesForSmartFolderLabel\(message\),/,
  );
  assert.match(
    workspaceShellSource,
    /const recentWindowMs = 30 \* 24 \* 60 \* 60 \* 1000;/,
  );
});

test("SmartFolderModal owns isolated local draft state", () => {
  const workspaceShellSource = fs.readFileSync(
    "src/components/workspace/WorkspaceShell.tsx",
    "utf8",
  );
  const smartFolderModalSource = workspaceShellSource.match(
    /const SmartFolderModal = memo\(function SmartFolderModal\([\s\S]*?\n}\);\n\nconst MailSettingsCard/,
  )?.[0];
  const smartFolderRootSource = workspaceShellSource.match(
    /export function WorkspaceShell\([\s\S]*$/,
  )?.[0];

  assert.ok(smartFolderModalSource, "SmartFolderModal component must exist");
  assert.ok(smartFolderRootSource, "WorkspaceShell component must exist");

  for (const removedRootState of [
    "isSmartFolderModalOpen",
    "editingSmartFolderId",
    "smartFolderDraftName",
    "smartFolderDraftScope",
    "smartFolderDraftSelectedInboxIds",
    "smartFolderDraftRules",
  ]) {
    assert.doesNotMatch(smartFolderRootSource, new RegExp(removedRootState));
  }

  assert.match(
    smartFolderRootSource,
    /const \[smartFolderModalTarget, setSmartFolderModalTarget\] =\s+useState<SmartFolderModalTarget>\(null\);/,
  );
  assert.match(
    workspaceShellSource,
    /type SmartFolderModalTarget =\s+\| \{ mode: "create" \}\s+\| \{ mode: "edit"; folderId: string \}\s+\| null;/,
  );
  assert.match(smartFolderModalSource, /const \[draftName, setDraftName\] = useState/);
  assert.match(smartFolderModalSource, /const \[draftScope, setDraftScope\] = useState/);
  assert.match(
    smartFolderModalSource,
    /const \[draftSelectedInboxIds, setDraftSelectedInboxIds\] = useState/,
  );
  assert.match(smartFolderModalSource, /const \[draftRules, setDraftRules\] = useState/);
  assert.match(smartFolderModalSource, /initialFolder\?\.name \?\? ""/);
  assert.match(smartFolderModalSource, /initialFolder\?\.scope \?\? "all"/);
  assert.match(
    smartFolderModalSource,
    /\[\.\.\.\(initialFolder\?\.selectedInboxIds \?\? \[]\)\]/,
  );
  assert.match(
    smartFolderModalSource,
    /initialFolder\.rules\.map\(\(rule\) => \(\{ \.\.\.rule \}\)\)/,
  );
  assert.match(smartFolderModalSource, /\[createEmptySmartFolderRule\(\)\]/);
  assert.match(smartFolderModalSource, /const trimmedName = draftName\.trim\(\);/);
  assert.match(
    smartFolderModalSource,
    /\.map\(\(rule\) => \(\{ \.\.\.rule, value: rule\.value\.trim\(\) \}\)\)\s+\.filter\(\(rule\) => rule\.value\.length > 0\)/,
  );
  assert.match(
    smartFolderModalSource,
    /draftScope === "selected" \? \[\.\.\.draftSelectedInboxIds\] : \[]/,
  );
  assert.match(
    smartFolderRootSource,
    /setSmartFolderModalTarget\(\{ mode: "create" \}\)/,
  );
  assert.match(
    smartFolderRootSource,
    /setSmartFolderModalTarget\(\{ mode: "edit", folderId: folder\.id \}\)/,
  );
  assert.match(
    smartFolderRootSource,
    /return current\.map\(\(folder\) =>\s+folder\.id === modalTarget\.folderId/,
  );
  assert.match(
    smartFolderRootSource,
    /id: `smart-folder-\$\{Date\.now\(\)\}`,\s+\.\.\.input,\s+},\s+\.\.\.current,/,
  );
  assert.match(
    smartFolderRootSource,
    /onCancel=\{\(\) => setSmartFolderModalTarget\(null\)\}/,
  );
  assert.match(
    smartFolderRootSource,
    /key=\{\s+smartFolderModalTarget\.mode === "create"\s+\? "create"\s+: `edit:\$\{smartFolderModalTarget\.folderId\}`/,
  );
});

test("Smart Folder deletion preserves navigation within the ordered folder list", () => {
  const workspaceShellSource = fs.readFileSync(
    "src/components/workspace/WorkspaceShell.tsx",
    "utf8",
  );
  const deleteSmartFolderSource = workspaceShellSource.match(
    /const deleteSmartFolder = \(folderId: string\) => \{[\s\S]*?\n  \};\n  const confirmSmartFolderDelete/,
  )?.[0];
  const confirmSmartFolderDeleteSource = workspaceShellSource.match(
    /const confirmSmartFolderDelete = \(\) => \{[\s\S]*?\n  \};\n\n  useEffect/,
  )?.[0];
  const modalDeleteSource = workspaceShellSource.match(
    /onDelete=\{[\s\S]*?\n              : undefined\n          \}/,
  )?.[0];

  assert.ok(deleteSmartFolderSource, "shared Smart Folder deletion helper must exist");
  assert.ok(confirmSmartFolderDeleteSource, "sidebar delete handler must exist");
  assert.ok(modalDeleteSource, "modal delete callback must exist");
  assert.equal(workspaceShellSource.match(/\bdeleteSmartFolder\b/g)?.length, 3);
  assert.match(
    deleteSmartFolderSource,
    /const deletedIndex = smartFolders\.findIndex\(\(folder\) => folder\.id === folderId\);[\s\S]*const remainingFolders = smartFolders\.filter\(\(folder\) => folder\.id !== folderId\);/,
  );
  assert.match(
    deleteSmartFolderSource,
    /activeSmartFolderId === folderId\s+\? remainingFolders\[deletedIndex\]\?\.id \?\?\s+remainingFolders\[deletedIndex - 1\]\?\.id \?\?\s+null\s+: activeSmartFolderId/,
  );
  assert.match(deleteSmartFolderSource, /setSmartFolders\(remainingFolders\);/);
  assert.match(
    deleteSmartFolderSource,
    /if \(activeSmartFolderId === folderId\) \{\s+setActiveSmartFolderId\(replacementFolderId\);\s+\}/,
  );
  assert.doesNotMatch(deleteSmartFolderSource, /handleOpenSmartFolder|openSmartFolder/);
  assert.match(
    confirmSmartFolderDeleteSource,
    /if \(!smartFolderDeleteId\) \{\s+return;\s+\}\s+deleteSmartFolder\(smartFolderDeleteId\);\s+setSmartFolderDeleteId\(null\);/,
  );
  assert.doesNotMatch(
    confirmSmartFolderDeleteSource,
    /\.filter\(|setSmartFolders|setActiveSmartFolderId|handleOpenSmartFolder|openSmartFolder/,
  );
  assert.match(
    modalDeleteSource,
    /const folderId = smartFolderModalTarget\.folderId;\s+deleteSmartFolder\(folderId\);\s+setSmartFolderModalTarget\(null\);/,
  );
  assert.doesNotMatch(
    modalDeleteSource,
    /\.filter\(|setSmartFolders|setActiveSmartFolderId|handleOpenSmartFolder|openSmartFolder/,
  );

  const resolveDeletion = (folderIds: string[], activeId: string, deletedId: string) => {
    const deletedIndex = folderIds.findIndex((folderId) => folderId === deletedId);
    const remainingFolderIds = folderIds.filter((folderId) => folderId !== deletedId);
    const replacementId =
      activeId === deletedId
        ? remainingFolderIds[deletedIndex] ?? remainingFolderIds[deletedIndex - 1] ?? null
        : activeId;

    return { remainingFolderIds, replacementId };
  };

  assert.deepEqual(resolveDeletion(["A", "B", "C"], "A", "A"), {
    remainingFolderIds: ["B", "C"],
    replacementId: "B",
  });
  assert.deepEqual(resolveDeletion(["A", "B", "C"], "B", "B"), {
    remainingFolderIds: ["A", "C"],
    replacementId: "C",
  });
  assert.deepEqual(resolveDeletion(["A", "B", "C"], "C", "C"), {
    remainingFolderIds: ["A", "B"],
    replacementId: "B",
  });
  assert.deepEqual(resolveDeletion(["A", "B", "C"], "A", "B"), {
    remainingFolderIds: ["A", "C"],
    replacementId: "A",
  });
  assert.deepEqual(resolveDeletion(["A"], "A", "A"), {
    remainingFolderIds: [],
    replacementId: null,
  });
});

test("drops stale snapshots without current classifier version", () => {
  const store = new Map<string, string>();
  const previousWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
    },
  };

  try {
    store.set(
      "cuevion-live-inbox-snapshots",
      JSON.stringify({
        promo: {
          schemaVersion: 5,
          inboxId: "promo",
          email: "promo@example.com",
          fetchedAt: new Date().toISOString(),
          messages: [
            {
              id: "stale-demo",
              sender: "Sender",
              subject: "Promo nieuw vuur!",
              snippet: "Hier echt een dikke promo!!",
              from: "sender@example.com",
              to: "dj@example.com",
              timestamp: "March 27 at 10:00",
              createdAt: new Date().toISOString(),
              body: ["Hier echt een dikke promo!!"],
              ui_signal: "DEMO",
              internalClassification: "demo",
            },
          ],
        },
      }),
    );

    assert.deepEqual(readLiveInboxSnapshots(), {});

    saveLiveInboxSnapshot({
      provider: "google",
      inboxId: "promo",
      email: "promo@example.com",
      fetchedAt: new Date().toISOString(),
      messages: [
        {
          id: "fresh-promo",
          threadId: "existing-thread-456",
          providerThreadId: "provider-thread-123",
          sender: "Sender",
          subject: "Promo nieuw vuur!",
          snippet: "Hier echt een dikke promo!!",
          from: "sender@example.com",
          to: "dj@example.com",
          timestamp: "March 27 at 10:00",
          createdAt: new Date().toISOString(),
          body: ["Hier echt een dikke promo!!"],
          ui_signal: "PROMO",
          internalClassification: "promo",
        },
      ],
      folder: "INBOX",
      uidValidity: "gmail-api",
    });

    const snapshots = readLiveInboxSnapshots();
    assert.equal(snapshots.promo?.classifierVersion, MUSIC_CLASSIFIER_VERSION);
    assert.equal(
      snapshots.promo?.messages[0]?.classifierVersion,
      MUSIC_CLASSIFIER_VERSION,
    );
    assert.equal(
      snapshots.promo?.messages[0]?.providerThreadId,
      "provider-thread-123",
    );
    assert.equal(snapshots.promo?.messages[0]?.threadId, "existing-thread-456");
    assert.equal(snapshots.promo?.provider, "google");
    assert.equal(snapshots.promo?.folder, "INBOX");

    saveLiveInboxSnapshot({
      inboxId: "legacy",
      email: "legacy@example.com",
      fetchedAt: new Date().toISOString(),
      messages: [
        {
          id: "legacy-message",
          sender: "Sender",
          subject: "Legacy message",
          snippet: "Snapshot without provider thread metadata",
          from: "sender@example.com",
          to: "recipient@example.com",
          timestamp: "March 27 at 11:00",
          createdAt: new Date().toISOString(),
          body: ["Snapshot without provider thread metadata"],
          ui_signal: "NEW",
        },
      ],
    });

    const snapshotsWithLegacyMessage = readLiveInboxSnapshots();
    assert.equal(snapshotsWithLegacyMessage.legacy, undefined);
  } finally {
    (globalThis as { window?: unknown }).window = previousWindow;
  }
});

test("migrates stale custom-IMAP subject thread IDs without losing message state", () => {
  const store = new Map<string, string>();
  const previousWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
    },
  };

  const buildMessage = (id: string, imapUid: string) => ({
    id,
    imapUid,
    threadId: "demo submission via website",
    sender: "Hysteriarecs.com",
    subject: "Demo Submission via website",
    snippet: "Submission",
    from: "forms@example.com",
    to: "demo@example.com",
    timestamp: "July 13 at 10:00",
    createdAt: "2026-07-13T08:00:00.000Z",
    body: ["Submission body"],
    bodyHtml: "<p>Submission body</p>",
    attachments: [{ id: "attachment-1", name: "demo.wav" }],
    unread: true,
    flagged: true,
    ui_signal: "DEMO",
    internalClassification: "demo",
    category: "Primary",
    collaboration: { updatedAt: 12, messages: [{ id: "note-1" }] },
    classifierVersion: MUSIC_CLASSIFIER_VERSION,
  });

  try {
    store.set(
      "cuevion-live-inbox-snapshots",
      JSON.stringify({
        demo: {
          schemaVersion: 5,
          classifierVersion: MUSIC_CLASSIFIER_VERSION,
          provider: "custom_imap",
          inboxId: "demo",
          email: "demo@example.com",
          fetchedAt: "2026-07-13T08:00:00.000Z",
          folder: "INBOX",
          uidValidity: "900",
          messages: [buildMessage("message-1", "1"), buildMessage("message-2", "2")],
        },
      }),
    );

    const migrated = readLiveInboxSnapshots().demo;
    assert.ok(migrated);
    assert.equal(migrated.provider, "custom_imap");
    assert.equal(migrated.folder, "INBOX");
    assert.equal(migrated.uidValidity, "900");
    assert.equal(migrated.threadIdentityVersion, LIVE_INBOX_THREAD_IDENTITY_VERSION);
    assert.equal(migrated.messages.length, 2);
    assert.notEqual(migrated.messages[0]?.threadId, migrated.messages[1]?.threadId);
    assert.match(migrated.messages[0]?.threadId ?? "", /^imap:uid:/);
    assert.equal(migrated.messages[0]?.id, "message-1");
    assert.equal(migrated.messages[0]?.unread, true);
    assert.equal(migrated.messages[0]?.flagged, true);
    assert.equal(migrated.messages[0]?.internalClassification, "demo");
    assert.equal((migrated.messages[0] as any)?.category, "Primary");
    assert.equal(migrated.messages[0]?.bodyHtml, "<p>Submission body</p>");
    assert.deepEqual((migrated.messages[0] as any)?.collaboration, {
      updatedAt: 12,
      messages: [{ id: "note-1" }],
    });
    assert.equal(migrated.messages[0]?.attachments?.[0]?.name, "demo.wav");
    const persistedFallback = migrated.messages[1]?.threadId;

    saveLiveInboxSnapshot({
      ...migrated,
      messages: migrated.messages.map((message, index) =>
        index === 0 ? { ...message, threadId: "imap:rfc:fresh%40example.com" } : message,
      ),
    });
    assert.equal(
      readLiveInboxSnapshots().demo?.messages[0]?.threadId,
      "imap:rfc:fresh%40example.com",
    );
    const reloaded = readLiveInboxSnapshots().demo;
    assert.equal(reloaded?.messages[1]?.threadId, persistedFallback);
    const rawSavedSnapshot = JSON.parse(
      store.get("cuevion-live-inbox-snapshots") ?? "{}",
    ).demo;
    assert.equal(rawSavedSnapshot.provider, "custom_imap");
    assert.equal(rawSavedSnapshot.folder, "INBOX");
    assert.equal(rawSavedSnapshot.uidValidity, "900");
    assert.equal(
      rawSavedSnapshot.threadIdentityVersion,
      LIVE_INBOX_THREAD_IDENTITY_VERSION,
    );
  } finally {
    (globalThis as { window?: unknown }).window = previousWindow;
  }
});

test("legacy snapshot migration requires trusted provider context", () => {
  const store = new Map<string, string>();
  const previousWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
    },
  };
  const legacyStore = JSON.stringify({
    legacy: {
      schemaVersion: 5,
      classifierVersion: MUSIC_CLASSIFIER_VERSION,
      inboxId: "legacy",
      email: "owner@example.com",
      fetchedAt: "2026-07-13T08:00:00.000Z",
      uidValidity: "900",
      messages: [{
        id: "legacy-message",
        imapUid: "42",
        threadId: "same subject",
        sender: "Sender",
        subject: "Same subject",
        snippet: "Body",
        from: "sender@example.com",
        to: "owner@example.com",
        timestamp: "July 13 at 10:00",
        createdAt: "2026-07-13T08:00:00.000Z",
        body: ["Body"],
        ui_signal: "NEW",
      }],
    },
  });

  try {
    store.set("cuevion-live-inbox-snapshots", legacyStore);
    const ambiguous = readLiveInboxSnapshots().legacy;
    assert.equal(ambiguous.provider, undefined);
    assert.equal(ambiguous.messages[0]?.threadId, "same subject");

    store.set("cuevion-live-inbox-snapshots", legacyStore);
    const customImap = readLiveInboxSnapshots({
      legacy: { mailboxId: "legacy", provider: "custom_imap", folder: "Archive" },
    }).legacy;
    assert.equal(customImap.provider, "custom_imap");
    assert.equal(customImap.folder, "Archive");
    assert.match(customImap.messages[0]?.threadId ?? "", /^imap:uid:legacy:Archive:900:42$/);

    store.set("cuevion-live-inbox-snapshots", legacyStore);
    const google = readLiveInboxSnapshots({
      legacy: { mailboxId: "legacy", provider: "google", folder: "INBOX" },
    }).legacy;
    assert.equal(google.provider, "google");
    assert.equal(google.messages[0]?.threadId, "same subject");
  } finally {
    (globalThis as { window?: unknown }).window = previousWindow;
  }
});

test("thread migration leaves Gmail provider snapshots unchanged", () => {
  const store = new Map<string, string>();
  const previousWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
    },
  };

  try {
    store.set(
      "cuevion-live-inbox-snapshots",
      JSON.stringify({
        gmail: {
          schemaVersion: 5,
          classifierVersion: MUSIC_CLASSIFIER_VERSION,
          provider: "google",
          inboxId: "gmail",
          email: "owner@example.com",
          fetchedAt: "2026-07-13T08:00:00.000Z",
          folder: "INBOX",
          messages: [{
            id: "gmail-message",
            providerMessageId: "provider-message",
            threadId: "same subject",
            providerThreadId: "gmail-thread-123",
            sender: "Sender",
            subject: "Same subject",
            snippet: "Body",
            from: "sender@example.com",
            to: "owner@example.com",
            timestamp: "July 13 at 10:00",
            createdAt: "2026-07-13T08:00:00.000Z",
            body: ["Body"],
            bodyHtml: "<p>Body</p>",
            attachments: [{ id: "gmail-attachment", name: "note.txt" }],
            unread: true,
            flagged: true,
            ui_signal: "NEW",
            internalClassification: "info",
            category: "Updates",
            collaboration: { updatedAt: 5, messages: [{ id: "gmail-note" }] },
            classifierVersion: MUSIC_CLASSIFIER_VERSION,
          }],
        },
      }),
    );

    const gmailSnapshot = readLiveInboxSnapshots().gmail;
    assert.ok(gmailSnapshot);
    const gmailMessage = hydrateLiveInboxSnapshot(gmailSnapshot).messages[0];
    assert.equal(gmailMessage?.providerMessageId, "provider-message");
    assert.equal(gmailMessage?.providerThreadId, "gmail-thread-123");
    assert.equal(gmailMessage?.threadId, "same subject");
    assert.equal(gmailMessage?.unread, true);
    assert.equal(gmailMessage?.flagged, true);
    assert.equal(gmailMessage?.bodyHtml, "<p>Body</p>");
    assert.equal(gmailMessage?.attachments?.[0]?.name, "note.txt");
    assert.equal(gmailMessage?.internalClassification, "info");
    assert.equal((gmailMessage as any)?.category, "Updates");
    assert.deepEqual((gmailMessage as any)?.collaboration, {
      updatedAt: 5,
      messages: [{ id: "gmail-note" }],
    });
  } finally {
    (globalThis as { window?: unknown }).window = previousWindow;
  }
});

test("removes only the exact mailbox-scoped provider message from a Gmail Inbox snapshot", () => {
  const exactMessage = {
    id: "gmail-ui-1",
    serverMailboxId: "mailbox-a",
    providerFolder: "Inbox",
    providerMessageId: "provider-message-1",
    sender: "Sender",
    subject: "Exact message",
    snippet: "Exact body",
    from: "sender@example.com",
    to: "owner@example.com",
    timestamp: "August 2 at 10:00",
    createdAt: "2026-08-02T08:00:00.000Z",
    body: ["Exact body"],
    unread: true,
    ui_signal: "NEW",
  };
  const sameProviderIdOtherMailbox = {
    ...exactMessage,
    id: "gmail-ui-other-mailbox",
    serverMailboxId: "mailbox-b",
  };
  const otherMessage = {
    ...exactMessage,
    id: "gmail-ui-2",
    providerMessageId: "provider-message-2",
    subject: "Other message",
  };
  const snapshot = {
    schemaVersion: 5,
    threadIdentityVersion: LIVE_INBOX_THREAD_IDENTITY_VERSION,
    classifierVersion: MUSIC_CLASSIFIER_VERSION,
    provider: "google" as const,
    inboxId: "mailbox-a",
    email: "owner@example.com",
    fetchedAt: "2026-08-02T08:01:00.000Z",
    folder: "INBOX",
    uidValidity: "gmail-api",
    messages: [exactMessage, sameProviderIdOtherMailbox, otherMessage],
  };

  const result = removeGmailInboxProviderMessageFromSnapshot(
    snapshot,
    "mailbox-a",
    "provider-message-1",
  );

  assert.ok(result);
  assert.notStrictEqual(result, snapshot);
  assert.deepEqual(result.messages, [sameProviderIdOtherMailbox, otherMessage]);
  assert.strictEqual(result.messages[0], sameProviderIdOtherMailbox);
  assert.strictEqual(result.messages[1], otherMessage);
  assert.equal(result.email, snapshot.email);
  assert.equal(result.fetchedAt, snapshot.fetchedAt);
  assert.equal(result.uidValidity, snapshot.uidValidity);
  assert.equal(result.threadIdentityVersion, snapshot.threadIdentityVersion);
  assert.deepEqual(snapshot.messages, [
    exactMessage,
    sameProviderIdOtherMailbox,
    otherMessage,
  ]);

  let persistedSnapshot;
  const persistenceResult =
    removeAndPersistGmailInboxProviderMessageFromSnapshot(
      snapshot,
      "mailbox-a",
      "provider-message-1",
      (nextSnapshot) => {
        persistedSnapshot = nextSnapshot;
      },
    );

  assert.equal(persistenceResult.changed, true);
  assert.strictEqual(persistenceResult.snapshot, persistedSnapshot);
  assert.deepEqual(persistedSnapshot?.messages, [
    sameProviderIdOtherMailbox,
    otherMessage,
  ]);
  assert.strictEqual(persistedSnapshot?.messages[0], sameProviderIdOtherMailbox);
  assert.strictEqual(persistedSnapshot?.messages[1], otherMessage);
});

test("Gmail Inbox snapshot removal is a reference-preserving no-op without an exact target", () => {
  const message = {
    id: "gmail-ui-1",
    serverMailboxId: "mailbox-a",
    providerFolder: "Inbox",
    providerMessageId: "provider-message-1",
    sender: "Sender",
    subject: "Message",
    snippet: "Body",
    from: "sender@example.com",
    to: "owner@example.com",
    timestamp: "August 2 at 10:00",
    createdAt: "2026-08-02T08:00:00.000Z",
    body: ["Body"],
    ui_signal: "NEW",
  };
  const gmailInboxSnapshot = {
    provider: "google" as const,
    inboxId: "mailbox-a",
    email: "owner@example.com",
    fetchedAt: "2026-08-02T08:01:00.000Z",
    folder: "INBOX",
    messages: [message],
  };
  const customImapSnapshot = {
    ...gmailInboxSnapshot,
    provider: "custom_imap" as const,
  };
  const gmailArchiveSnapshot = {
    ...gmailInboxSnapshot,
    folder: "Archive",
  };
  let persistCalls = 0;
  const persistSnapshot = () => {
    persistCalls += 1;
  };

  assert.equal(
    removeGmailInboxProviderMessageFromSnapshot(
      undefined,
      "mailbox-a",
      "provider-message-1",
    ),
    undefined,
  );
  assert.strictEqual(
    removeGmailInboxProviderMessageFromSnapshot(
      gmailInboxSnapshot,
      "mailbox-b",
      "provider-message-1",
    ),
    gmailInboxSnapshot,
  );
  assert.strictEqual(
    removeGmailInboxProviderMessageFromSnapshot(
      gmailInboxSnapshot,
      "mailbox-a",
      "provider-message-missing",
    ),
    gmailInboxSnapshot,
  );
  assert.strictEqual(
    removeGmailInboxProviderMessageFromSnapshot(
      customImapSnapshot,
      "mailbox-a",
      "provider-message-1",
    ),
    customImapSnapshot,
  );
  assert.strictEqual(
    removeGmailInboxProviderMessageFromSnapshot(
      gmailArchiveSnapshot,
      "mailbox-a",
      "provider-message-1",
    ),
    gmailArchiveSnapshot,
  );
  for (const [snapshot, mailboxId] of [
    [gmailInboxSnapshot, "mailbox-b"],
    [customImapSnapshot, "mailbox-a"],
    [gmailArchiveSnapshot, "mailbox-a"],
  ] as const) {
    const result = removeAndPersistGmailInboxProviderMessageFromSnapshot(
      snapshot,
      mailboxId,
      "provider-message-1",
      persistSnapshot,
    );
    assert.equal(result.changed, false);
    assert.strictEqual(result.snapshot, snapshot);
  }
  assert.equal(persistCalls, 0);
});

test("snapshot persistence failure cannot interrupt confirmed Gmail Archive success", () => {
  const archivedMessage = {
    id: "gmail-ui-1",
    serverMailboxId: "mailbox-a",
    providerFolder: "Inbox",
    providerMessageId: "provider-message-1",
    sender: "Sender",
    subject: "Archive me",
    snippet: "Body",
    from: "sender@example.com",
    to: "owner@example.com",
    timestamp: "August 2 at 10:00",
    createdAt: "2026-08-02T08:00:00.000Z",
    body: ["Body"],
    unread: true,
    ui_signal: "NEW",
  };
  const snapshot = {
    provider: "google" as const,
    inboxId: "mailbox-a",
    email: "owner@example.com",
    fetchedAt: "2026-08-02T08:01:00.000Z",
    folder: "INBOX",
    messages: [archivedMessage],
  };
  const authority = createGmailInboxAuthority();
  const unreadOverrides = new Set(["provider-message-1"]);
  const steps: string[] = [];
  const generationAtFetchStart = authority.captureGeneration("mailbox-a");
  let snapshotChanged = false;

  authority.confirmArchive("mailbox-a", "provider-message-1");
  steps.push("authority");
  assert.doesNotThrow(() => {
    const result = removeAndPersistGmailInboxProviderMessageFromSnapshot(
      snapshot,
      "mailbox-a",
      "provider-message-1",
      (nextSnapshot) => {
        steps.push("snapshot-cleanup");
        assert.deepEqual(nextSnapshot.messages, []);
        throw new Error("local snapshot persistence failed");
      },
    );
    snapshotChanged = result.changed;
    steps.push("delta");
    unreadOverrides.delete("provider-message-1");
    steps.push("unread-clear");
  });

  assert.equal(snapshotChanged, true);
  assert.deepEqual(steps, [
    "authority",
    "snapshot-cleanup",
    "delta",
    "unread-clear",
  ]);
  assert.equal(authority.captureGeneration("mailbox-a"), generationAtFetchStart + 1);
  assert.equal(
    authority.isRecentlyArchived("mailbox-a", "provider-message-1"),
    true,
  );
  assert.equal(unreadOverrides.has("provider-message-1"), false);
});

test("confirmed Gmail Archive blocks unread-clear rehydration of the last stale unread snapshot", () => {
  const store = new Map<string, string>();
  const previousWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
    },
  };

  try {
    saveLiveInboxSnapshot({
      provider: "google",
      inboxId: "mailbox-a",
      email: "owner@example.com",
      fetchedAt: "2026-08-02T08:01:00.000Z",
      folder: "INBOX",
      uidValidity: "gmail-api",
      messages: [
        {
          id: "gmail-ui-1",
          serverMailboxId: "mailbox-a",
          providerFolder: "Inbox",
          providerMessageId: "provider-message-1",
          sender: "Sender",
          subject: "Unread before Archive",
          snippet: "Body",
          from: "sender@example.com",
          to: "owner@example.com",
          timestamp: "August 2 at 10:00",
          createdAt: "2026-08-02T08:00:00.000Z",
          body: ["Body"],
          unread: true,
          ui_signal: "NEW",
        },
      ],
    });

    const snapshot = readLiveInboxSnapshots({
      "mailbox-a": {
        mailboxId: "mailbox-a",
        provider: "google",
        folder: "INBOX",
      },
    })["mailbox-a"];
    assert.ok(snapshot);
    const unreadOverrides = new Map([["provider-message-1", false]]);
    assert.equal(unreadOverrides.get("provider-message-1"), false);
    const authority = createGmailInboxAuthority();
    authority.confirmArchive("mailbox-a", "provider-message-1");
    unreadOverrides.delete("provider-message-1");
    assert.equal(unreadOverrides.has("provider-message-1"), false);
    assert.deepEqual(
      authority.filterSnapshotMessages("mailbox-a", snapshot.messages),
      [],
    );
    const nextSnapshot = removeGmailInboxProviderMessageFromSnapshot(
      snapshot,
      "mailbox-a",
      "provider-message-1",
    );
    assert.ok(nextSnapshot);
    assert.deepEqual(nextSnapshot.messages, []);

    saveLiveInboxSnapshot(nextSnapshot);

    assert.equal(
      readLiveInboxSnapshots({
        "mailbox-a": {
          mailboxId: "mailbox-a",
          provider: "google",
          folder: "INBOX",
        },
      })["mailbox-a"],
      undefined,
    );
  } finally {
    (globalThis as { window?: unknown }).window = previousWindow;
  }
});

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

console.log(`\n${"─".repeat(50)}`);
if (failed === 0) {
  console.log(`✓ All ${passed} tests passed.`);
} else {
  console.error(`✗ ${failed} test(s) failed (${passed} passed).`);
  process.exit(1);
}
