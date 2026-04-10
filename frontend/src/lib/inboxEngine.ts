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