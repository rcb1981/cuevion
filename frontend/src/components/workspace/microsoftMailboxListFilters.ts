import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";

export type MicrosoftMessageSortOrder = "newest" | "oldest";

export type MicrosoftMessageListOptions = {
  folder: "Inbox" | "Filtered" | "Sent" | "Archive" | "Drafts" | "Deleted" | "Junk";
  search: string;
  unreadOnly: boolean;
  sortOrder: MicrosoftMessageSortOrder;
};

/**
 * Local filtering of the already-authorized, bounded Graph snapshot.
 * Never describes this as a server-side or entire-mailbox search.
 * Does not change the authoritative read/unread status.
 */
export function filterMicrosoftLoadedMessages(
  rows: ReadonlyArray<LiveInboxMessageSnapshot>,
  options: MicrosoftMessageListOptions,
): LiveInboxMessageSnapshot[] {
  const normalizedSearch = options.search.trim().toLocaleLowerCase();
  const items = rows
    .map((message, index) => ({ message, index }))
    .filter(({ message }) => {
      if ((options.folder === "Inbox" || options.folder === "Filtered" || options.folder === "Archive" || options.folder === "Deleted" || options.folder === "Junk") && options.unreadOnly && message.unread !== true) {
        return false;
      }
      if (!normalizedSearch) {
        return true;
      }
      return [message.sender, message.subject, message.snippet, message.from, message.to]
        .some((field) => typeof field === "string" &&
          field.toLocaleLowerCase().includes(normalizedSearch));
    });
  items.sort((a, b) => {
    const aTime = Date.parse(a.message.createdAt);
    const bTime = Date.parse(b.message.createdAt);
    const difference = (Number.isFinite(bTime) ? bTime : 0) -
      (Number.isFinite(aTime) ? aTime : 0);
    if (difference === 0) {
      return a.index - b.index;
    }
    return options.sortOrder === "oldest" ? -difference : difference;
  });
  return items.map(({ message }) => message);
}
