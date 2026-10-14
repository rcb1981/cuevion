import assert from "node:assert/strict";
import { filterMicrosoftLoadedMessages } from "./microsoftMailboxListFilters";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";

const item = (
  id: string, sender: string, subject: string, snippet: string,
  createdAt: string, unread: boolean,
): LiveInboxMessageSnapshot => ({
  id,
  providerMessageId: id,
  sender,
  subject,
  snippet,
  from: sender,
  to: "recipient@example.com",
  createdAt,
  timestamp: createdAt,
  body: [],
  unread,
});

const rows = [
  item("id-1", "Kallak", "Daddy", "New promo ready", "2026-10-13T12:00:00Z", true),
  item("id-2", "Team", "Contract", "Please approve", "2026-10-14T10:00:00Z", false),
  item("id-3", "Kallak", "Mix", "Dropbox link", "2026-10-14T10:00:00Z", true),
];
const defaults = {
  folder: "Inbox" as const,
  search: "",
  unreadOnly: false,
  sortOrder: "newest" as const,
};

assert.deepEqual(
  filterMicrosoftLoadedMessages(rows, defaults).map((message) => message.id),
  ["id-2", "id-3", "id-1"],
);
assert.deepEqual(
  filterMicrosoftLoadedMessages(rows, { ...defaults, sortOrder: "oldest" })
    .map((message) => message.id),
  ["id-1", "id-2", "id-3"],
);
assert.deepEqual(
  filterMicrosoftLoadedMessages(rows, { ...defaults, unreadOnly: true })
    .map((message) => message.id),
  ["id-3", "id-1"],
);
assert.deepEqual(
  filterMicrosoftLoadedMessages(rows, { ...defaults, search: "  KALLAK  " })
    .map((message) => message.id),
  ["id-3", "id-1"],
);
assert.deepEqual(
  filterMicrosoftLoadedMessages(rows, { ...defaults, search: "APPROVE" })
    .map((message) => message.id),
  ["id-2"],
);
assert.deepEqual(
  filterMicrosoftLoadedMessages(rows, { ...defaults, search: "unmatched" }),
  [],
);
assert.deepEqual(
  filterMicrosoftLoadedMessages(rows, { ...defaults, folder: "Sent", unreadOnly: true })
    .map((message) => message.id),
  ["id-2", "id-3", "id-1"],
  "Unread-only filters must never be applied to Sent",
);
assert.equal(rows[0].unread, true);
assert.equal(rows[1].unread, false);
assert.deepEqual(rows.map(({ id }) => id), ["id-1", "id-2", "id-3"], "Filtering never reorders the authoritative snapshot");
console.log("Microsoft loaded-message search, unread filter and stable sort tests: PASS");


assert.deepEqual(
  filterMicrosoftLoadedMessages(rows, { ...defaults, folder: "Archive", unreadOnly: true })
    .map((message) => message.id),
  ["id-3", "id-1"],
  "Archive unread filter should operate on archived read state only",
);


assert.deepEqual(
  filterMicrosoftLoadedMessages(rows, { ...defaults, folder: "Drafts", unreadOnly: true })
    .map((message) => message.id),
  ["id-2", "id-3", "id-1"],
  "Drafts have no unread filter or read/unread semantics",
);


assert.deepEqual(
  filterMicrosoftLoadedMessages(rows, { ...defaults, folder: "Deleted", unreadOnly: true })
    .map((message) => message.id),
  ["id-3", "id-1"],
  "Read status in Deleted Items is only a local visual filter",
);
