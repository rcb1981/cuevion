import assert from "node:assert/strict";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";
import type { SenderCategoryLearningStore } from "../../lib/learningEngine";
import {
  classifyMicrosoftFilteredMessage,
  isMicrosoftFilteredProtectionActive,
  microsoftFilteredStorageKey,
  microsoftVisibleFolderRows,
  parseMicrosoftSenderPlacements,
  setMicrosoftSenderPlacement,
} from "./microsoftVirtualFiltered";

const message: LiveInboxMessageSnapshot = {
  id: "m1", serverMailboxId: "microsoft-A", providerMessageId: "GRAPHm1",
  providerFolder: "INBOX",
  from: "Microsoft Azure <updates@sample.example>",
  to: "owner@example.com", sender: "Microsoft Azure", subject: "News", snippet: "News",
  createdAt: "2026-10-16T12:00:00Z", timestamp: "16 Oct", body: [], unread: true,
};
const regular = { ...message, id: "m2", providerMessageId: "GRAPHm2",
  from: "Artist <artist@other.example>", subject: "New demo" };
const noLearning: SenderCategoryLearningStore = {};
const learned: SenderCategoryLearningStore = {
  "updates@sample.example": {
    learnedCategory: "Updates", learnedFromCount: 2, mailboxAction: "move",
    senderBehavior: "show_less", sourcePrioritySelection: "Show Less",
  },
};

assert.equal(isMicrosoftFilteredProtectionActive(message), false);
assert.equal(isMicrosoftFilteredProtectionActive({...message, signal: "Priority"}), true);
assert.equal(classifyMicrosoftFilteredMessage(message, noLearning, {}), "Inbox");
assert.equal(classifyMicrosoftFilteredMessage(message, learned, {}), "Filtered");
assert.equal(classifyMicrosoftFilteredMessage(regular, learned, {}), "Inbox");
assert.equal(classifyMicrosoftFilteredMessage(message, learned, {"updates@sample.example": "keep"}), "Inbox");
assert.equal(classifyMicrosoftFilteredMessage(message, noLearning, {"updates@sample.example": "show_less"}), "Filtered");
assert.equal(classifyMicrosoftFilteredMessage(message, learned, {"updates@sample.example": "show_less"}), "Filtered");

for (const protectedMessage of [
  {...message, providerFolder: "JUNK"},
  {...message, providerMessageId: ""},
  {...message, signal: "Priority"},
  {...message, signal: "Active"},
  {...message, signal: "For review"},
  {...message, signal: "Shortlist"},
  {...message, final_visibility: "show_priority"},
  {...message, internalClassification: "reply"},
  {...message, categoryConfidence: "low"},
  {...message, v7_final_priority: "PRIORITY"},
]) {
  assert.equal(
    classifyMicrosoftFilteredMessage(protectedMessage, learned, {"updates@sample.example": "show_less"}),
    "Inbox", "Protected, unknown or non-Inbox messages must fail open",
  );
}

const domainLearning: SenderCategoryLearningStore = {
  "domain:sample.example": {
    learnedCategory: "Updates", learnedFromCount: 1, mailboxAction: "move",
    sourcePrioritySelection: "Show Less",
  },
};
assert.equal(classifyMicrosoftFilteredMessage(message, domainLearning, {}), "Filtered");
assert.equal(classifyMicrosoftFilteredMessage(message, {
  ...domainLearning,
  "updates@sample.example": { learnedCategory: "Primary", learnedFromCount: 1,
    senderBehavior: "normal" },
}, {}), "Inbox", "Exact sender overrides learned domain");
assert.equal(classifyMicrosoftFilteredMessage(message, {
  "updates@sample.example": { learnedCategory: "Primary", learnedFromCount: 1,
    senderBehavior: "always_prioritize" },
}, {}), "Inbox");
assert.equal(classifyMicrosoftFilteredMessage(message, {
  "updates@sample.example": { learnedCategory: "Updates", learnedFromCount: 1,
    senderBehavior: "do_not_learn" },
}, {}), "Inbox");
assert.equal(classifyMicrosoftFilteredMessage({
  ...message, final_visibility: "show_low", action: "show_in_quiet_view",
  v7_final_priority: "LOW",
}, noLearning, {}), "Filtered");
assert.equal(classifyMicrosoftFilteredMessage({
  ...message, final_visibility: "show_low", action: "show_in_quiet_view",
  v7_final_priority: "NORMAL",
}, noLearning, {}), "Inbox");

const source = [message, regular];
const initial = JSON.stringify(source);
assert.deepEqual(
  microsoftVisibleFolderRows(source, "Filtered", learned, {}, true).map(m => m.id),
  ["m1"],
);
assert.deepEqual(
  microsoftVisibleFolderRows(source, "Inbox", learned, {}, true).map(m => m.id),
  ["m2"],
);
assert.deepEqual(microsoftVisibleFolderRows(source, "Filtered", learned, {}, false), []);
assert.deepEqual(microsoftVisibleFolderRows(source, "Inbox", learned, {}, false), source);
assert.deepEqual(microsoftVisibleFolderRows(source, "Archive", learned, {}, true), source);
assert.equal(JSON.stringify(source), initial, "Partition must never mutate Graph DTOs");

// Canonical owner/workspace/mailbox isolation; never use an address alone.
const key = microsoftFilteredStorageKey("workspace-1", "auth0|owner", "microsoft-A");
assert.ok(key);
assert.notEqual(key, microsoftFilteredStorageKey("workspace-2", "auth0|owner", "microsoft-A"));
assert.notEqual(key, microsoftFilteredStorageKey("workspace-1", "auth0|other", "microsoft-A"));
assert.notEqual(key, microsoftFilteredStorageKey("workspace-1", "auth0|owner", "microsoft-B"));
assert.equal(microsoftFilteredStorageKey(null, "auth0|owner", "microsoft-A"), null);
assert.equal(microsoftFilteredStorageKey("ws", null, "microsoft-A"), null);
assert.equal(microsoftFilteredStorageKey("ws", "owner", "../../other"), null);

assert.deepEqual({...parseMicrosoftSenderPlacements(null)}, {});
assert.deepEqual({...parseMicrosoftSenderPlacements("{bad")}, {});
assert.deepEqual({...parseMicrosoftSenderPlacements('{"__proto__":"show_less"}')}, {});
assert.deepEqual({...parseMicrosoftSenderPlacements('{"test@example.com":"archive"}')}, {});
assert.deepEqual({...parseMicrosoftSenderPlacements('{"test@example.com":"show_less"}')},
  {"test@example.com": "show_less"});
assert.deepEqual({...parseMicrosoftSenderPlacements("x".repeat(25000))}, {});

const next = setMicrosoftSenderPlacement({}, "Person <person@example.com>", "show_less");
assert.deepEqual(next, {"person@example.com": "show_less"});
assert.equal(setMicrosoftSenderPlacement({}, "invalid from", "show_less"), null);
assert.deepEqual(setMicrosoftSenderPlacement(next!, "person@example.com", "keep"), {"person@example.com": "keep"});

console.log("Microsoft virtual Filtered: learned, explicit, safe, owner/mailbox isolated PASS");
