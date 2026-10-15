import assert from "node:assert/strict";
import { applyConfirmedMicrosoftUnread } from "./microsoftReadState";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";

const entry: LiveInboxMessageSnapshot = {
  id: "m-1",
  providerMessageId: "m-1",
  serverMailboxId: "microsoft-01",
  providerFolder: "INBOX",
  labelIds: ["INBOX", "UNREAD"],
  sender: "Artist",
  subject: "Demo",
  snippet: "Listen",
  from: "Artist <artist@example.com>",
  to: "label@example.com",
  timestamp: "October 9 at 10:00",
  createdAt: "2026-10-09T10:00:00Z",
  body: ["Full private message"],
  unread: true,
  flagged: false,
  attachments: [],
};
const original = JSON.stringify(entry);
const read = applyConfirmedMicrosoftUnread(entry, "m-1", false);
assert.notEqual(read, entry);
assert.equal(read.unread, false);
assert.deepEqual(read.labelIds, ["INBOX"]);
assert.deepEqual(read.body, entry.body);
assert.equal(JSON.stringify(entry), original, "projection must not mutate source");
const unread = applyConfirmedMicrosoftUnread(read, "m-1", true);
assert.equal(unread.unread, true);
assert.deepEqual(unread.labelIds, ["INBOX", "UNREAD"]);
const other = applyConfirmedMicrosoftUnread(entry, "another-id", false);
assert.equal(other, entry, "unrelated message must keep exact object identity");
const malformed = applyConfirmedMicrosoftUnread(entry, "m-1", "false" as unknown as boolean);
assert.equal(malformed, entry);
console.log("✓ Confirmed Microsoft unread state projection tests passed.");

// Opening a verified unread Inbox message is the only future automatic
// mutation. Other folders, stale selections and the read-only pilot never write.
const { shouldAutoMarkMicrosoftOpenedMessageRead } = require("./microsoftReadState") as typeof import("./microsoftReadState");
const open = {
  readWriteEnabled: true,
  folder: "Inbox",
  selectedMessageId: "m-1",
  message: entry,
};
assert.equal(shouldAutoMarkMicrosoftOpenedMessageRead(open), true);
assert.equal(shouldAutoMarkMicrosoftOpenedMessageRead({...open, readWriteEnabled: false}), false);
assert.equal(shouldAutoMarkMicrosoftOpenedMessageRead({...open, folder: "Sent"}), false);
assert.equal(shouldAutoMarkMicrosoftOpenedMessageRead({...open, folder: "Archive"}), false);
assert.equal(shouldAutoMarkMicrosoftOpenedMessageRead({...open, folder: "Drafts"}), false);
assert.equal(shouldAutoMarkMicrosoftOpenedMessageRead({...open, folder: "Junk"}), false);
assert.equal(shouldAutoMarkMicrosoftOpenedMessageRead({...open, folder: "Deleted"}), false);
assert.equal(shouldAutoMarkMicrosoftOpenedMessageRead({...open, selectedMessageId: "other"}), false);
assert.equal(shouldAutoMarkMicrosoftOpenedMessageRead({...open, message: {...entry, unread: false}}), false);
assert.equal(shouldAutoMarkMicrosoftOpenedMessageRead({...open, message: {...entry, body: []}}), false);
assert.equal(shouldAutoMarkMicrosoftOpenedMessageRead({...open, message: {...entry, providerFolder: "ARCHIVE"}}), false);
console.log("Microsoft open-to-read gate tests: PASS");
