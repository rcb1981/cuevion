export type ThreadableMessage = {
  id: string;
  threadId?: string;
  subject: string;
  from?: string;
  createdAt?: string;
  timestamp?: string;
};

export function normalizeThreadSubject(subject: string) {
  return subject
    .trim()
    .toLowerCase()
    .replace(/^(re|fwd|fw):\s*/gi, "")
    .replace(/\s+/g, " ");
}

export function resolveThreadKey(message: Pick<ThreadableMessage, "threadId" | "subject" | "from">) {
  if (message.threadId?.trim()) {
    return `thread:${message.threadId.trim()}`;
  }

  const normalizedSubject = normalizeThreadSubject(message.subject);
  const normalizedSender = (message.from ?? "").trim().toLowerCase();

  return `fallback:${normalizedSubject}|${normalizedSender}`;
}

export function resolveMessageDateMs(
  message: Pick<ThreadableMessage, "createdAt" | "timestamp">,
) {
  if (message.createdAt) {
    const directDate = new Date(message.createdAt).getTime();
    if (!Number.isNaN(directDate)) {
      return directDate;
    }
  }

  if (message.timestamp) {
    const timestampDate = new Date(message.timestamp).getTime();
    if (!Number.isNaN(timestampDate)) {
      return timestampDate;
    }
  }

  return 0;
}

export function dedupeLatestMessagePerThread<T extends ThreadableMessage>(messages: T[]) {
  const latestByThread = new Map<string, T>();

  for (const message of messages) {
    const threadKey = resolveThreadKey(message);
    const existing = latestByThread.get(threadKey);

    if (!existing) {
      latestByThread.set(threadKey, message);
      continue;
    }

    const currentDate = resolveMessageDateMs(message);
    const existingDate = resolveMessageDateMs(existing);

    if (currentDate >= existingDate) {
      latestByThread.set(threadKey, message);
    }
  }

  return Array.from(latestByThread.values()).sort(
    (a, b) => resolveMessageDateMs(b) - resolveMessageDateMs(a),
  );
}

// ---------------------------------------------------------------------------
// Inbox snapshot pruning
// ---------------------------------------------------------------------------

/** Maximum messages kept in the persisted inbox snapshot per mailbox.
 *  When exceeded, the oldest fully-read threads are pruned on each sync. */
export const INBOX_SNAPSHOT_MAX_MESSAGES = 800;

/** Threads whose newest message is older than this are eligible for pruning
 *  (age-based hard cut, applied before the count cap). */
export const INBOX_SNAPSHOT_MAX_AGE_MS = 90 * 24 * 60 * 60 * 1000; // 90 days

/** Threads with any activity within this window are protected from pruning
 *  even if the count cap would otherwise reach them. */
export const INBOX_SNAPSHOT_RECENT_GUARD_MS = 14 * 24 * 60 * 60 * 1000; // 14 days

/**
 * Prunes a flat list of inbox messages to stay within INBOX_SNAPSHOT_MAX_MESSAGES.
 *
 * Rules (in priority order):
 *  1. Threads that contain any unread message are never pruned.
 *  2. Threads whose newest message falls within INBOX_SNAPSHOT_RECENT_GUARD_MS are
 *     never pruned (recently active).
 *  3. Threads with unknown age (newestMs === 0) are treated as recently active and
 *     never pruned — a missing/invalid date is never grounds for removal.
 *  4. Threads whose newest message exceeds INBOX_SNAPSHOT_MAX_AGE_MS are always
 *     removed first (age-based eviction), then the count cap is applied.
 *  5. Within each eviction tier, oldest threads are removed before newer ones.
 *  6. Original message order is preserved in the output.
 *
 * Generic over T so it works on both LiveInboxMessageSnapshot (snapshot layer)
 * and MailMessage (in-memory layer) without requiring separate implementations.
 */
export function pruneInboxSnapshot<
  T extends {
    unread?: boolean | null;
    subject?: string | null;
    createdAt?: string | null;
    timestamp?: string | null;
    threadId?: string | null;
  },
>(messages: T[], nowMs: number): T[] {
  if (messages.length <= INBOX_SNAPSHOT_MAX_MESSAGES) {
    return messages; // fast path: already within budget
  }

  // Resolve a stable thread-group key for a message.
  // Messages without threadId fall back to normalised subject.
  const getThreadKey = (m: T): string => {
    const tid = m.threadId?.trim();
    if (tid) return `t:${tid}`;
    return `s:${normalizeThreadSubject((m.subject ?? "") as string)}`;
  };

  // Resolve a numeric timestamp, trying both createdAt and timestamp fields.
  // Returns 0 only when neither field carries a parseable date.
  const getDateMs = (m: T): number =>
    resolveMessageDateMs({
      createdAt: m.createdAt ?? undefined,
      timestamp: m.timestamp ?? undefined,
    });

  // Group messages by thread key and compute per-thread metadata in one pass.
  type ThreadBucket = { key: string; items: T[]; newestMs: number };
  const buckets = new Map<string, ThreadBucket>();

  for (const message of messages) {
    const key = getThreadKey(message);
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = { key, items: [], newestMs: 0 };
      buckets.set(key, bucket);
    }
    bucket.items.push(message);
    const ms = getDateMs(message);
    if (ms > bucket.newestMs) bucket.newestMs = ms;
  }

  // Classify each thread as protected or eligible for pruning.
  const protectedKeys = new Set<string>();
  const eligible: Array<{ key: string; newestMs: number; count: number }> = [];

  for (const [key, bucket] of buckets) {
    const hasUnread = bucket.items.some((m) => m.unread === true);
    // newestMs === 0 means the date is unknown — treat as recently active so
    // we never accidentally prune a message whose timestamp we can't parse.
    const ageMs = bucket.newestMs > 0 ? nowMs - bucket.newestMs : 0;
    const isRecentlyActive = ageMs < INBOX_SNAPSHOT_RECENT_GUARD_MS;

    if (hasUnread || isRecentlyActive) {
      protectedKeys.add(key);
    } else {
      eligible.push({ key, newestMs: bucket.newestMs, count: bucket.items.length });
    }
  }

  // Sort eligible threads oldest-first so we can evict from the front.
  eligible.sort((a, b) => a.newestMs - b.newestMs);

  // Start the kept set with all protected threads and count their messages.
  const keepKeys = new Set(protectedKeys);
  let keptCount = [...buckets.entries()]
    .filter(([key]) => protectedKeys.has(key))
    .reduce((sum, [, b]) => sum + b.items.length, 0);

  // Fill remaining budget from newest eligible to oldest, skipping any thread
  // whose age exceeds the hard max-age threshold.
  for (let i = eligible.length - 1; i >= 0; i--) {
    const thread = eligible[i];
    if (keptCount >= INBOX_SNAPSHOT_MAX_MESSAGES) break;
    const ageMs = thread.newestMs > 0 ? nowMs - thread.newestMs : 0;
    if (ageMs > INBOX_SNAPSHOT_MAX_AGE_MS) continue; // hard age cut — skip
    keepKeys.add(thread.key);
    keptCount += thread.count;
  }

  // Rebuild the list in original order to preserve sort stability downstream.
  return messages.filter((m) => keepKeys.has(getThreadKey(m)));
}
