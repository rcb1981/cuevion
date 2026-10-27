import assert from "node:assert/strict";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";
import type { SenderCategoryLearningStore } from "../../lib/learningEngine";
import {
  classifyMicrosoftFilteredMessage,
  isMicrosoftLikelyMarketingMessage,
  isMicrosoftFilteredProtectionActive,
  isMicrosoftPriorityUnderFocus,
  microsoftFocusCategory,
  microsoftFilteredStorageKey,
  microsoftVisibleFolderRows,
  parseMicrosoftSenderPlacements,
  setMicrosoftSenderPlacement,
} from "./microsoftVirtualFiltered";
import { normalizeFocusPreferences } from "../../types/onboarding";

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
const marketingCases = [
  { from: "Store <offers@retail.example>", sender: "Store", subject: "October nieuwsbrief", snippet: "De nieuwste aanbiedingen" },
  { from: "Shop <hello@retail.example>", sender: "Shop", subject: "Actie: 30% korting dit weekend", snippet: "Shop vandaag" },
  { from: "Brand <marketing@brand.example>", sender: "Brand", subject: "October update", snippet: "View in browser · Unsubscribe" },
  { from: "Tickets <hello@tickets.example>", sender: "Tickets", subject: "Black Friday deals", snippet: "Save 25%" },
  { from: "Magazine <newsletter@media.example>", sender: "Magazine", subject: "Monthly news", snippet: "What's new" },
  { from: "Mortgage Ads <info@advertiser.example>", sender: "Hypotheek-berekenen ism Infopages", subject: "Overwaarde van uw hypotheek", snippet: "Webversie Berekening beschikbaar. Ontdek uw voordeel" },
];
for (const [index, bulk] of marketingCases.entries()) {
  const newsletter = { ...message, ...bulk, id: `bulk-${index}`, providerMessageId: `GRAPHbulk${index}` };
  assert.equal(isMicrosoftLikelyMarketingMessage(newsletter), true, bulk.subject);
  assert.equal(classifyMicrosoftFilteredMessage(newsletter, noLearning, {}), "Filtered", bulk.subject);
  assert.equal(classifyMicrosoftFilteredMessage(newsletter, noLearning, { [bulk.from.match(/<([^>]+)>/)?.[1] ?? ""]: "keep" }), "Inbox", "User Keep override wins");
  assert.equal(classifyMicrosoftFilteredMessage(newsletter, noLearning, {}, {
    [bulk.from.match(/<([^>]+)>/)?.[1] ?? ""]: { priority: "important", label: "Business" },
  }), "Inbox", "Microsoft important sender rule wins");
}
const mustStayInInbox = [
  { sender: "Microsoft-accountteam", from: "Microsoft <no-reply@account.microsoft.com>", subject: "Nieuwe apps verbonden met uw Microsoft-account", snippet: "Actie vereist? Controleer uw beveiligingsinstellingen" },
  { sender: "Meta for Business", from: "Meta <notification@facebookmail.com>", subject: "Je Meta-advertenties-ontvangstbewijs", snippet: "Betalingsoverzicht en factuur" },
  { sender: "Music Label", from: "DJ <promos@music.example>", subject: "DJ promo: New release for your sets", snippet: "Promo download on Inflyte" },
  { sender: "Artist", from: "Artist <artist@example.com>", subject: "Demo submission", snippet: "Could you review my new track?" },
  { sender: "Partner", from: "Partner <partner@example.com>", subject: "Re: Special offer", snippet: "Can you confirm the contract?" },
  { sender: "Orders", from: "Orders <offers@shop.example>", subject: "Order confirmation and receipt", snippet: "Your order has shipped. Unsubscribe" },
  { sender: "Microsoft Azure", from: "Azure <noreply@azure.example>", subject: "New account security alert", snippet: "Sign-in notification" },
  { sender: "Bank", from: "Bank <service@bank.example>", subject: "Uw hypotheekoverzicht", snippet: "Een update over de actuele hypotheekstand" },
];
for (const normal of mustStayInInbox) {
  const candidate = { ...message, ...normal };
  assert.equal(isMicrosoftLikelyMarketingMessage(candidate), false, normal.subject);
  assert.equal(classifyMicrosoftFilteredMessage(candidate, noLearning, {}), "Inbox", normal.subject);
}
assert.equal(classifyMicrosoftFilteredMessage({
  ...message, ...marketingCases[0], signal: "Priority",
}, noLearning, {}), "Inbox", "Priority signals are never hidden by marketing heuristics");
const promotedNewsletter = { ...message, ...marketingCases[0], id: "newsletter-1", providerMessageId: "GRAPHnewsletter" };
assert.deepEqual(
  microsoftVisibleFolderRows([promotedNewsletter, regular], "Filtered", noLearning, {}, true)
    .map((item) => item.providerMessageId),
  ["GRAPHnewsletter"],
  "Newsletters in the loaded Graph Inbox appear only in Microsoft Filtered",
);
assert.deepEqual(
  microsoftVisibleFolderRows([promotedNewsletter, regular], "Inbox", noLearning, {}, true)
    .map((item) => item.providerMessageId),
  ["GRAPHm2"],
  "Automatic filtering cannot duplicate or move the Graph message",
);
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

// Microsoft's effective per-mailbox Settings/onboarding categories must obey
// the same normal/low choices as Gmail/IMAP. A Graph LIST projection has only
// the subject/sender/preview, so use unambiguous music-industry examples.
const categoryCases = [
  { key: "demos", subject: "New demo for your label", snippet: "Private SoundCloud link to my track", sender: "Producer" },
  { key: "promo", subject: "DJ promo: New release", snippet: "Listen on Inflyte and download for your sets", sender: "Label Promo" },
  { key: "finance", subject: "Invoice for mastering", snippet: "Please find the factuur attached", sender: "Studio" },
  { key: "legal", subject: "Master rights clearance request", snippet: "Please review the contract for signature", sender: "Licensing" },
  { key: "business", subject: "Booking request for your DJ", snippet: "Let's plan the event", sender: "Promoter" },
  { key: "updates", subject: "Scheduled maintenance", snippet: "System update tomorrow evening", sender: "Support" },
  { key: "distribution", subject: "LabelWorx release delivered", snippet: "Delivery status for Beatport", sender: "LabelWorx" },
  { key: "royalties", subject: "H1 royalty statement", snippet: "Your royalty accounting is ready", sender: "RoyaltyWorx" },
  { key: "promoReminders", subject: "(Reminder) Promo Invite from ALXB Records", snippet: "Your promo access request is still available on Inflyte", sender: "ALXB Records" },
  { key: "paymentReminders", subject: "Payment reminder: overdue invoice", snippet: "Please settle the openstaande factuur", sender: "Accounting" },
] as const;
const defaults = normalizeFocusPreferences(null);
for (const [index, item] of categoryCases.entries()) {
  const candidate: LiveInboxMessageSnapshot = {
    ...message,
    id: `focus-${index}`, providerMessageId: `Graph-focus-${index}`,
    from: `${item.sender} <focus-${index}@examples.test>`, sender: item.sender,
    subject: item.subject, snippet: item.snippet,
  };
  assert.equal(microsoftFocusCategory(candidate), item.key, `${item.key} classification`);
  assert.equal(classifyMicrosoftFilteredMessage(candidate, {}, {}, {}, defaults), "Inbox", `${item.key} Normal`);
  const low = { ...defaults, [item.key]: "low" as const };
  assert.equal(classifyMicrosoftFilteredMessage(candidate, {}, {}, {}, low), "Filtered", `${item.key} Low`);
  assert.deepEqual(microsoftVisibleFolderRows([candidate], "Inbox", {}, {}, true, {}, low), [], `${item.key} excluded from Inbox`);
  assert.deepEqual(microsoftVisibleFolderRows([candidate], "Filtered", {}, {}, true, {}, low), [candidate], `${item.key} visible in Filtered`);
  assert.equal(classifyMicrosoftFilteredMessage(candidate, {}, {}, {
    [`focus-${index}@examples.test`]: { priority: "important", label: "Other" },
  }, low), "Inbox", `${item.key}: manual Important wins`);
  assert.equal(classifyMicrosoftFilteredMessage(candidate, {}, {
    [`focus-${index}@examples.test`]: "keep",
  }, {}, low), "Inbox", `${item.key}: Keep wins`);
  assert.equal(classifyMicrosoftFilteredMessage(candidate, {}, {}, {}, { ...low, [item.key]: "medium" }), "Inbox", `${item.key} settings update takes effect`);
}

// Same message, different mailbox-effective choices: never leak one mailbox's
// overrides into another mailbox or into shared Gmail/IMAP routing.
const labelPromo = { ...message, id: "music-promo", providerMessageId: "Graph-promo",
  sender: "Label", from: "Label <dj@label.example>",
  subject: "DJ promo: Upcoming single", snippet: "Listen and download on Fatdrop for your sets" };
const separateMailboxPrefs = { ...defaults, promo: "low" as const };
assert.equal(classifyMicrosoftFilteredMessage(labelPromo, {}, {}, {}, defaults), "Inbox");
assert.equal(classifyMicrosoftFilteredMessage(labelPromo, {}, {}, {}, separateMailboxPrefs), "Filtered");
assert.equal(classifyMicrosoftFilteredMessage(labelPromo, {}, {}, {}, defaults), "Inbox");
assert.equal(classifyMicrosoftFilteredMessage(labelPromo, {}, {}, {}, separateMailboxPrefs), "Filtered");
const stalePriorityPromo = { ...labelPromo, signal: "Priority" };
assert.equal(classifyMicrosoftFilteredMessage(stalePriorityPromo, {}, {}, {}, defaults), "Inbox");
assert.equal(classifyMicrosoftFilteredMessage(stalePriorityPromo, {}, {}, {}, separateMailboxPrefs), "Filtered",
  "Explicit Promo Low overrides stale classifier Priority only for a recognised promo");
assert.equal(isMicrosoftPriorityUnderFocus(stalePriorityPromo, {}, defaults), false,
  "A recognised Promo set to Normal is not promoted by a stale backend Priority signal");
assert.equal(isMicrosoftPriorityUnderFocus(stalePriorityPromo, {}, separateMailboxPrefs), false);
assert.equal(isMicrosoftPriorityUnderFocus(stalePriorityPromo, {
  "dj@label.example": { priority: "important", label: "Promo" },
}, separateMailboxPrefs), true, "Manual Important remains authoritative");

const doubtful = { ...message, subject: "Thoughts on this?", snippet: "Do you have some time?" };
assert.equal(microsoftFocusCategory(doubtful), null);
assert.equal(classifyMicrosoftFilteredMessage(doubtful, {}, {}, {}, Object.fromEntries(
  Object.keys(defaults).map((key) => [key, "low"]),
) as typeof defaults), "Inbox", "Unknown messages stay visible even if all focus categories are Low");
assert.equal(classifyMicrosoftFilteredMessage({ ...labelPromo, subject: "Re: DJ promo: Upcoming single" },
  {}, {}, {}, separateMailboxPrefs), "Inbox", "Replies stay Inbox when Promo is Low");
assert.equal(classifyMicrosoftFilteredMessage({ ...labelPromo,
  from: "Security <alerts@account.microsoft.com>", subject: "New sign-in security alert",
}, {}, {}, {}, separateMailboxPrefs), "Inbox", "Account security overrides Low");
assert.equal(classifyMicrosoftFilteredMessage({ ...message, subject: "30% korting in onze nieuwsbrief", snippet: "Unsubscribe" },
  {}, {}, {}, defaults), "Filtered", "Retail marketing stays quiet even when Updates is Normal");

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
