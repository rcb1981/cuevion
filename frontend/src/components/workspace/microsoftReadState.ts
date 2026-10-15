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

/**
 * Only open-and-read a newly displayed, exact Inbox message after consent.
 * This predicate contains NO side effects; preview and Production pilot
 * both leave readWriteEnabled=false until separately released.
 */
export function shouldAutoMarkMicrosoftOpenedMessageRead(input: {
  readWriteEnabled: boolean;
  folder: string;
  selectedMessageId: string | null;
  message: LiveInboxMessageSnapshot | null;
}): boolean {
  const { readWriteEnabled, folder, selectedMessageId, message } = input;
  return readWriteEnabled === true &&
    folder === "Inbox" &&
    typeof selectedMessageId === "string" && selectedMessageId.length > 0 &&
    message?.providerFolder === "INBOX" &&
    message.providerMessageId === selectedMessageId &&
    message.unread === true &&
    Array.isArray(message.body) && message.body.length > 0;
}
