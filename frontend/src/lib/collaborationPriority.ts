import type { PrioritySourceResult } from "./prioritySource";

export function composeCollaborationPrioritySource(source: PrioritySourceResult, active: boolean): PrioritySourceResult {
  if (!active || ["manual", "waiting_on_other", "returned_reply", "assigned_review"].includes(source.source)) return source;
  return { level: "priority", source: "collaboration", confidence: "high", reason: "This exact mail has an active collaboration." };
}

export function isCollaborationPrioritySuppressed(input: {
  removed: boolean;
  cleared: boolean;
  noise: boolean;
}): boolean {
  return input.removed || input.cleared || input.noise;
}

// Retain existing conversation representatives and add only exact active source
// mails. Never backfill an older source when the latest conversation is suppressed.
export function mergeExactCollaborationPriorityCandidates<T>(input: {
  representatives: T[];
  candidates: T[];
  latestByConversation: ReadonlyMap<string, T>;
  conversationKey: (entry: T) => string;
  messageKey: (entry: T) => string;
  active: (entry: T) => boolean;
  suppressed: (entry: T) => boolean;
}): T[] {
  const entries = new Map(input.representatives.map(entry => [input.messageKey(entry), entry]));
  for (const entry of input.candidates) {
    const latest = input.latestByConversation.get(input.conversationKey(entry));
    if (input.active(entry) && !input.suppressed(entry) && (!latest || !input.suppressed(latest))) {
      entries.set(input.messageKey(entry), entry);
    }
  }
  return [...entries.values()];
}
