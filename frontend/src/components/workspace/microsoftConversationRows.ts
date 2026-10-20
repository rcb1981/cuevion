import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";

/**
 * An authorized, bounded page-only Graph conversation projection.
 * No subject heuristics, cross-mailbox matches or additional Graph reads.
 * Only messages already visible in the active mailbox/folder may be opened.
 */
export function getMicrosoftConversationRows(
  visibleFolderRows: ReadonlyArray<LiveInboxMessageSnapshot>,
  selectedProviderMessageId: string | null,
  mailboxId: string,
): LiveInboxMessageSnapshot[] {
  if (!selectedProviderMessageId || !mailboxId || visibleFolderRows.length > 25) return [];
  const selected = visibleFolderRows.find((item) =>
    item.providerMessageId === selectedProviderMessageId &&
    item.serverMailboxId === mailboxId,
  );
  const threadId = selected?.providerThreadId;
  if (!threadId || typeof threadId !== "string" || threadId.length > 512) return [];
  const seen = new Set<string>();
  return visibleFolderRows.filter((item) => {
    const id = item.providerMessageId;
    if (!id || typeof id !== "string" || seen.has(id) ||
        item.serverMailboxId !== mailboxId || item.providerFolder !== selected.providerFolder ||
        item.providerThreadId !== threadId) return false;
    seen.add(id);
    return true;
  }).sort((a, b) => {
    const aTime = Date.parse(a.createdAt), bTime = Date.parse(b.createdAt);
    const diff = (Number.isFinite(aTime) ? aTime : 0) - (Number.isFinite(bTime) ? bTime : 0);
    return diff !== 0 ? diff : (a.providerMessageId ?? "").localeCompare(b.providerMessageId ?? "");
  });
}
