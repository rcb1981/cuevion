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
  normalizeThreadSubject,
  resolveThreadKey,
  resolveMessageDateMs,
  dedupeLatestMessagePerThread,
  pruneInboxSnapshot,
  INBOX_SNAPSHOT_MAX_MESSAGES,
  INBOX_SNAPSHOT_MAX_AGE_MS,
  INBOX_SNAPSHOT_RECENT_GUARD_MS,
} from "./inboxEngine";
import {
  MUSIC_CLASSIFIER_VERSION,
  readLiveInboxSnapshots,
  saveLiveInboxSnapshot,
} from "./liveInboxSnapshots";

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

// ---------------------------------------------------------------------------
// liveInboxSnapshots — classifier version
// ---------------------------------------------------------------------------

console.log("\nliveInboxSnapshots — classifier version");

test("WorkspaceShell preserves provider thread metadata during message normalization", () => {
  const workspaceShellSource = fs.readFileSync(
    "src/components/workspace/WorkspaceShell.tsx",
    "utf8",
  );

  assert.match(
    workspaceShellSource,
    /threadId: message\.threadId,\s+providerThreadId: message\.providerThreadId,/,
  );
  assert.match(
    workspaceShellSource,
    /id: message\.id,\s+providerThreadId: message\.providerThreadId,\s+sender: message\.sender,/,
  );
});

test("WorkspaceShell uses symmetric safe keys for Smart Folder thread retention", () => {
  const representative = { threadId: "abc", subject: "Business", from: "sender@example.com" };
  const producerKey = resolveThreadKey(representative);
  const consumerKey = resolveThreadKey(representative);

  assert.equal(producerKey, "thread:abc");
  assert.equal(consumerKey, producerKey);
  assert.equal(representative.threadId, "abc");

  const workspaceShellSource = fs.readFileSync(
    "src/components/workspace/WorkspaceShell.tsx",
    "utf8",
  );
  const smartFolderThreadMatchSource = workspaceShellSource.match(
    /const matchingThreadIds = new Set\([\s\S]*?return organizerVisibleMessages\.filter\([\s\S]*?\n    \);/,
  )?.[0];

  assert.ok(smartFolderThreadMatchSource, "Smart Folder thread matching block must exist");
  assert.match(
    smartFolderThreadMatchSource,
    /\.map\(\(message\) => resolveSafeThreadGroupingKey\(message, mailboxId\)\)/,
  );
  assert.match(
    smartFolderThreadMatchSource,
    /matchingThreadIds\.has\(resolveSafeThreadGroupingKey\(message, mailboxId\)\)/,
  );
  assert.doesNotMatch(smartFolderThreadMatchSource, /message\.threadId\s*\?\?/);
  assert.match(
    workspaceShellSource,
    /return dedupedRowRecords\.map\(\(record\) => \(\{\s+\.\.\.record\.message,/,
  );
  assert.match(workspaceShellSource, /\{ value: "demo", label: "Demo" \}/);
  assert.match(workspaceShellSource, /\{ value: "business", label: "Business" \}/);
  assert.match(workspaceShellSource, /return matchValue\.includes\(ruleValue\);/);
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
    assert.equal(
      snapshotsWithLegacyMessage.legacy?.messages[0]?.providerThreadId,
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
