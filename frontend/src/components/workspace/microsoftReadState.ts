import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";

/**
 * Update one Microsoft projection only after the mutation endpoint has
 * confirmed the exact immutable Graph message ID and unread boolean.
 * All unrelated messages and their object identities remain unchanged.
 */
export function applyConfirmedMicrosoftUnread(
  message: LiveInboxMessageSnapshot,
  expectedMessageId: string,
  unread: boolean,
): LiveInboxMessageSnapshot {
  if (
    message.providerMessageId !== expectedMessageId ||
    !expectedMessageId ||
    typeof unread !== "boolean"
  ) {
    return message;
  }
  return {
    ...message,
    unread,
    labelIds: unread ? ["INBOX", "UNREAD"] : ["INBOX"],
  };
}
