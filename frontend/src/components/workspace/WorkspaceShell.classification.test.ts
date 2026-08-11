import assert from "node:assert/strict";
import "sucrase/register/tsx.js";
import type { MessageNoiseAssessment } from "../../lib/messageNoiseGate";

const {
  applyFilteredRoutingToMailboxCollections,
  applyFocusPreferenceRoutingToMailboxCollections,
  applyPromoReminderFocusPreferenceRouting,
  applyPromoReminderFocusPreferenceRoutingToMailboxCollections,
  getVisiblePriorityBadgeForWorkspaceMessage,
  normalizeCuevionInternalClassification,
  normalizeMailMessage,
  resolveVisibleCategoryLabelForMessageInContext,
} = require("./WorkspaceShell.tsx") as typeof import("./WorkspaceShell");
const {
  hydrateLiveInboxSnapshot,
  LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
  MUSIC_CLASSIFIER_VERSION,
  readLiveInboxSnapshots,
  upconvertLegacyPromoReminderSnapshotMessage,
} = require("../../lib/liveInboxSnapshots") as typeof import("../../lib/liveInboxSnapshots");

type MessageSeed = Parameters<typeof normalizeMailMessage>[0];
type MailboxCollections = Parameters<
  typeof applyFilteredRoutingToMailboxCollections
>[0];
type ProviderIdentity = Pick<
  MessageSeed,
  | "providerMessageId"
  | "providerThreadId"
  | "imapUid"
  | "uidValidity"
>;

const gmailIdentity: ProviderIdentity = {
  providerMessageId: "gmail-message-42",
  providerThreadId: "gmail-thread-9",
};
const customImapIdentity: ProviderIdentity = {
  imapUid: "42",
  uidValidity: "9001",
};

function projectMessage(
  rawInternalClassification: unknown,
  providerIdentity: ProviderIdentity = gmailIdentity,
  overrides: Partial<MessageSeed> = {},
) {
  const internalClassificationSeed =
    rawInternalClassification === undefined
      ? {}
      : {
          internalClassification: normalizeCuevionInternalClassification(
            rawInternalClassification,
          ),
        };
  const message = normalizeMailMessage(
    {
      id: "canonical-message",
      sender: "Canonical Sender",
      subject: "Canonical message",
      snippet: "Canonical message body",
      time: "10:00",
      ui_signal: "UPDATE",
      from: "sender@example.test",
      to: "recipient@example.test",
      timestamp: "2026-08-04T08:00:00.000Z",
      body: ["Canonical message body"],
      final_visibility: "show_low",
      action: "show_in_quiet_view",
      ...providerIdentity,
      ...overrides,
      ...internalClassificationSeed,
    },
    "main",
    {},
    {},
    "user-1",
  );

  return {
    message,
    visibleCategoryLabel: resolveVisibleCategoryLabelForMessageInContext(
      message,
      false,
    ),
  };
}

for (const classification of [
  "labelradar_update",
  "trackstack_submission",
] as const) {
  const gmail = projectMessage(classification, gmailIdentity);
  const customImap = projectMessage(classification, customImapIdentity);

  assert.equal(gmail.message.internalClassification, classification);
  assert.equal(gmail.message.category, "Updates");
  assert.equal(gmail.visibleCategoryLabel, "Update");
  assert.equal(gmail.message.priorityScore, "low");
  assert.equal(gmail.message.final_visibility, "show_low");
  assert.equal(gmail.message.action, "show_in_quiet_view");

  assert.equal(customImap.message.internalClassification, classification);
  assert.equal(customImap.message.category, "Updates");
  assert.equal(customImap.visibleCategoryLabel, "Update");

  assert.deepEqual(
    {
      internalClassification: gmail.message.internalClassification,
      category: gmail.message.category,
      visibleCategoryLabel: gmail.visibleCategoryLabel,
      priorityScore: gmail.message.priorityScore,
      final_visibility: gmail.message.final_visibility,
      action: gmail.message.action,
    },
    {
      internalClassification: customImap.message.internalClassification,
      category: customImap.message.category,
      visibleCategoryLabel: customImap.visibleCategoryLabel,
      priorityScore: customImap.message.priorityScore,
      final_visibility: customImap.message.final_visibility,
      action: customImap.message.action,
    },
  );
}

const malformedClassifications = [
  ["missing", undefined],
  ["empty", ""],
  ["null", null],
  ["unsupported", "unsupported_future_value"],
] as const;

for (const [name, rawInternalClassification] of malformedClassifications) {
  const { message, visibleCategoryLabel } = projectMessage(
    rawInternalClassification,
  );

  assert.notEqual(message.category, undefined, name);
  assert.equal(message.category, "Primary", name);
  assert.equal(visibleCategoryLabel, "Update", name);
}

const heuristicFallback = projectMessage("unsupported_future_value", gmailIdentity, {
  signal: "Update",
  ui_signal: undefined,
});
assert.equal(heuristicFallback.message.internalClassification, "workflow_update");
assert.equal(heuristicFallback.message.category, "Updates");
assert.equal(heuristicFallback.visibleCategoryLabel, "Update");

const supportedRegressions = [
  ["workflow_update", "UPDATE", "Updates", "Update"],
  ["distributor_update", "UPDATE", "Updates", "Update"],
  ["promo", "PROMO", "Promo", "Promo"],
  ["finance", "FINANCE", "Updates", "Finance"],
  ["reply", "REPLY", "Primary", "Reply"],
  ["demo", "DEMO", "Primary", "Demo"],
  ["unknown", "NEW", "Primary", "Other"],
] as const;

for (const [classification, uiSignal, category, visibleCategoryLabel] of supportedRegressions) {
  const projected = projectMessage(classification, gmailIdentity, {
    ui_signal: uiSignal,
  });

  assert.equal(projected.message.internalClassification, classification);
  assert.equal(projected.message.category, category);
  assert.equal(projected.visibleCategoryLabel, visibleCategoryLabel);

  if (classification === "distributor_update" || classification === "finance") {
    assert.deepEqual(
      routeSingleInboxMessage(projected.message).Inbox.map((message) => message.id),
      ["canonical-message"],
    );
    assert.equal(routeSingleInboxMessage(projected.message).Filtered.length, 0);
  }
}

const loanBody = [
  "Need a personal or business loan?",
  "Arabian Investment Group offers a simple application process and flexible loan options.",
  "Send us a message today to get started.",
  "Approval is subject to eligibility and terms.",
  "Best wishes, Mr.George Harry, Senior Consultant",
];
const strongNoiseAssessment: MessageNoiseAssessment = {
  noiseDisposition: "strong_spam",
  noiseConfidence: "medium",
  noiseReasons: [
    "unsolicited_financial_solicitation",
    "cold_call_to_action",
    "no_conversation_evidence",
    "mailbox_relevance_mismatch",
  ],
};
const learnedPrimaryStore = {
  "george.harry@wh.commufra.jp": {
    learnedCategory: "Primary" as const,
    learnedFromCount: 5,
    autoCategoryEnabled: false,
    mailboxAction: "keep" as const,
    sourcePrioritySelection: "Important" as const,
  },
};
const normalizedLoan = normalizeMailMessage(
  {
    id: "loan-fixture",
    sender: "Arabian Investment Group",
    subject: "Apply for a Loan Today – Fast Processing",
    snippet: `${loanBody[0]} ${loanBody[1]} ${loanBody[2]} ${loanBody[0]}`,
    time: "10:00",
    from: "Arabian Investment Group <george.harry@wh.commufra.jp>",
    to: "promo@hysteriarecs.com",
    timestamp: "2026-08-11T08:00:00.000Z",
    body: [...loanBody, loanBody[0]],
    ui_signal: "NEW",
    internalClassification: "unknown",
    final_visibility: "show_priority",
    action: "show_in_priority",
    ...strongNoiseAssessment,
  },
  "main",
  learnedPrimaryStore,
  {
    "george.harry@wh.commufra.jp": {
      userId: "user-1",
      count: 3,
    },
  },
  "user-1",
);

assert.notEqual(normalizedLoan.signal, "Promo");
assert.equal(normalizedLoan.internalClassification, "unknown");
assert.equal(normalizedLoan.category, "Primary");
assert.equal(normalizedLoan.categorySource, "learned");
assert.equal(normalizedLoan.priorityScore, "low");
assert.equal(normalizedLoan.focusSignal, null);
assert.equal(normalizedLoan.suggestion, undefined);
assert.equal(normalizedLoan.behaviorSuggestion, undefined);
assert.equal(normalizedLoan.aiSuggestionBanner, undefined);
assert.equal(
  resolveVisibleCategoryLabelForMessageInContext(normalizedLoan, true),
  "Spam",
);
assert.equal(
  getVisiblePriorityBadgeForWorkspaceMessage(
    normalizedLoan,
    "priority",
    {} as Parameters<typeof getVisiblePriorityBadgeForWorkspaceMessage>[2],
  ),
  "LOW",
);

const pluralOfferWithoutNoise = projectMessage("unknown", gmailIdentity, {
  subject: "Apply for a Loan Today",
  snippet: "Our company offers flexible loan options",
  body: ["Our company offers flexible loan options"],
  signal: undefined,
  ui_signal: "NEW",
  final_visibility: undefined,
  action: undefined,
});
assert.notEqual(pluralOfferWithoutNoise.message.signal, "Promo");
assert.notEqual(pluralOfferWithoutNoise.visibleCategoryLabel, "Promo");

const explicitOffer = projectMessage("unknown", gmailIdentity, {
  subject: "Special offer",
  snippet: "A special offer for subscribers",
  body: ["A special offer for subscribers"],
  signal: undefined,
  ui_signal: "NEW",
  final_visibility: undefined,
  action: undefined,
});
assert.ok(
  explicitOffer.message.signal === "Promo" ||
    explicitOffer.message.signal === "Update",
);

const musicPromo = projectMessage("unknown", gmailIdentity, {
  subject: "New remix out now – DJ promo",
  snippet: "New track and remix for your sets",
  body: ["New release and DJ promo for your sets"],
  signal: undefined,
  ui_signal: "NEW",
  final_visibility: undefined,
  action: undefined,
});
assert.equal(musicPromo.message.signal, "Promo");
assert.equal(musicPromo.visibleCategoryLabel, "Promo");

const normalFocusPreferences: Parameters<
  typeof getVisiblePriorityBadgeForWorkspaceMessage
>[2] = {
  demos: "medium",
  promo: "medium",
  finance: "medium",
  legal: "medium",
  business: "medium",
  updates: "medium",
  distribution: "medium",
  royalties: "medium",
  promoReminders: "medium",
  paymentReminders: "medium",
};
const lowPromoReminderFocusPreferences: Parameters<
  typeof getVisiblePriorityBadgeForWorkspaceMessage
>[2] = {
  ...normalFocusPreferences,
  promoReminders: "low",
};
const highPromoReminderFocusPreferences: Parameters<
  typeof getVisiblePriorityBadgeForWorkspaceMessage
>[2] = {
  ...normalFocusPreferences,
  promoReminders: "high",
};
const lowPaymentReminderFocusPreferences: Parameters<
  typeof getVisiblePriorityBadgeForWorkspaceMessage
>[2] = {
  ...normalFocusPreferences,
  paymentReminders: "low",
};
const alxbReminderOverrides: Partial<MessageSeed> = {
  id: "alxb-promo-reminder",
  sender: "ALXB Records",
  subject: "(Reminder) Promo Invite from ALXB Records",
  snippet: "Your promo invite is still available.",
  from: "ALXB Records <promo@alxbrecords.com>",
  to: "promo@hysteriarecs.com",
  timestamp: "2026-08-11T08:00:00.000Z",
  body: ["Listen and download the promo when you are ready."],
  signal: undefined,
  ui_signal: "PROMO",
};
const alxbPromoAccessRequestOverrides: Partial<MessageSeed> = {
  snippet: "Your promo access request is still available.",
  from: "ALXB Records <promos@inflyteapp.com>",
  body: ["Your promo access request is waiting for you."],
};

function projectAlxbReminder(
  providerIdentity: ProviderIdentity,
  visibility: "low" | "normal",
  rawInternalClassification: unknown,
  contentOverrides: Partial<MessageSeed> = {},
) {
  const providerMetadata: Partial<MessageSeed> = providerIdentity.imapUid
    ? { providerFolder: "INBOX", labelIds: undefined }
    : { providerFolder: "Inbox", labelIds: ["INBOX"] };

  return projectMessage(rawInternalClassification, providerIdentity, {
    ...alxbReminderOverrides,
    ...contentOverrides,
    ...providerMetadata,
    v7_final_priority: visibility === "low" ? "LOW" : "NORMAL",
    final_visibility: visibility === "low" ? "show_low" : "show_normal",
    action:
      visibility === "low" ? "show_in_quiet_view" : "show_in_main_feed",
  });
}

function routeSingleInboxMessage(
  message: ReturnType<typeof normalizeMailMessage>,
  senderLearning: Parameters<typeof applyFilteredRoutingToMailboxCollections>[1] = {},
  collectionOverrides: Partial<MailboxCollections> = {},
) {
  return applyFilteredRoutingToMailboxCollections(
    {
      Drafts: [],
      Sent: [],
      Archive: [],
      Filtered: [],
      Spam: [],
      Trash: [],
      ...collectionOverrides,
      Inbox: collectionOverrides.Inbox ?? [message],
    },
    senderLearning,
  );
}

const alxbGmailLow = projectAlxbReminder(
  gmailIdentity,
  "low",
  "promo_reminder",
  alxbPromoAccessRequestOverrides,
);
const alxbCustomImapLow = projectAlxbReminder(
  customImapIdentity,
  "low",
  "promo_reminder",
  alxbPromoAccessRequestOverrides,
);

for (const projected of [alxbGmailLow, alxbCustomImapLow]) {
  assert.equal(projected.message.internalClassification, "promo_reminder");
  assert.equal(projected.message.category, "Promo");
  assert.equal(projected.message.priorityScore, "low");
  assert.equal(
    getVisiblePriorityBadgeForWorkspaceMessage(
      projected.message,
      undefined,
      lowPromoReminderFocusPreferences,
      { preferPromoMailboxContext: true },
    ),
    "LOW",
  );
  assert.equal(projected.visibleCategoryLabel, "Promo");
}

const alxbGmailNormal = projectAlxbReminder(
  gmailIdentity,
  "normal",
  "promo_reminder",
  alxbPromoAccessRequestOverrides,
);
assert.equal(alxbGmailNormal.message.internalClassification, "promo_reminder");
assert.equal(alxbGmailNormal.visibleCategoryLabel, "Promo");
assert.equal(
  getVisiblePriorityBadgeForWorkspaceMessage(
    alxbGmailNormal.message,
    undefined,
    normalFocusPreferences,
    { preferPromoMailboxContext: true },
  ),
  "NORMAL",
);
assert.deepEqual(
  routeSingleInboxMessage(alxbGmailNormal.message).Inbox.map(
    (message) => message.id,
  ),
  ["alxb-promo-reminder"],
);
assert.equal(routeSingleInboxMessage(alxbGmailNormal.message).Filtered.length, 0);

const genericPromoAccessRequest = projectAlxbReminder(
  gmailIdentity,
  "normal",
  "promo",
  alxbPromoAccessRequestOverrides,
);
assert.equal(genericPromoAccessRequest.message.internalClassification, "promo");
assert.equal(genericPromoAccessRequest.visibleCategoryLabel, "Business");
assert.equal(
  getVisiblePriorityBadgeForWorkspaceMessage(
    genericPromoAccessRequest.message,
    undefined,
    normalFocusPreferences,
    { preferPromoMailboxContext: true },
  ),
  "NORMAL",
);

assert.equal(
  getVisiblePriorityBadgeForWorkspaceMessage(
    alxbGmailLow.message,
    undefined,
    lowPromoReminderFocusPreferences,
    {
      preferPromoMailboxContext: true,
      manualLabelOverride: "Business",
    },
  ),
  "NORMAL",
);

const alxbLowWithStalePriority = {
  ...alxbGmailLow.message,
  signal: "Priority",
};
assert.equal(routeSingleInboxMessage(alxbLowWithStalePriority).Inbox.length, 0);
assert.deepEqual(
  routeSingleInboxMessage(alxbLowWithStalePriority).Filtered.map(
    (message) => message.id,
  ),
  ["alxb-promo-reminder"],
);

const alxbLowFromStalePriority = applyPromoReminderFocusPreferenceRouting(
  {
    ...alxbLowWithStalePriority,
    priorityScore: "high",
    final_visibility: "show_priority",
    action: "show_in_priority",
  },
  "low",
);
assert.equal(alxbLowFromStalePriority.signal, undefined);
assert.equal(alxbLowFromStalePriority.priorityScore, "low");
assert.equal(alxbLowFromStalePriority.final_visibility, "show_low");
assert.equal(alxbLowFromStalePriority.action, "show_in_quiet_view");
assert.equal(routeSingleInboxMessage(alxbLowFromStalePriority).Inbox.length, 0);
assert.deepEqual(
  routeSingleInboxMessage(alxbLowFromStalePriority).Filtered.map(
    (message) => message.id,
  ),
  ["alxb-promo-reminder"],
);

assert.deepEqual(
  {
    classification: alxbGmailLow.message.internalClassification,
    category: alxbGmailLow.message.category,
    label: alxbGmailLow.visibleCategoryLabel,
    priority: alxbGmailLow.message.priorityScore,
    visibility: alxbGmailLow.message.final_visibility,
    action: alxbGmailLow.message.action,
  },
  {
    classification: alxbCustomImapLow.message.internalClassification,
    category: alxbCustomImapLow.message.category,
    label: alxbCustomImapLow.visibleCategoryLabel,
    priority: alxbCustomImapLow.message.priorityScore,
    visibility: alxbCustomImapLow.message.final_visibility,
    action: alxbCustomImapLow.message.action,
  },
);

function createFolderSentinel(id: string, providerFolder: string) {
  return projectMessage(
    "unknown",
    {
      providerMessageId: `provider-${id}`,
      providerThreadId: `thread-${id}`,
    },
    {
      id,
      sender: `Sender ${id}`,
      subject: `Sentinel ${id}`,
      snippet: `Sentinel body ${id}`,
      from: `${id}@example.test`,
      body: [`Sentinel body ${id}`],
      timestamp: `2026-08-11T0${id.length % 9}:00:00.000Z`,
      signal: undefined,
      ui_signal: "NEW",
      final_visibility: "show_normal",
      action: "show_in_main_feed",
      providerFolder,
      labelIds: [providerFolder.toUpperCase()],
    },
  ).message;
}

const safeCollectionInput: MailboxCollections = {
  Inbox: [alxbGmailLow.message],
  Drafts: [createFolderSentinel("draft-sentinel", "DRAFT")],
  Sent: [createFolderSentinel("sent-sentinel", "SENT")],
  Archive: [createFolderSentinel("archive-sentinel", "ARCHIVE")],
  Filtered: [createFolderSentinel("filtered-sentinel", "Inbox")],
  Spam: [createFolderSentinel("spam-sentinel", "SPAM")],
  Trash: [createFolderSentinel("trash-sentinel", "TRASH")],
};
const safeCollectionInputBefore = structuredClone(safeCollectionInput);
const lowCollections = routeSingleInboxMessage(
  alxbGmailLow.message,
  {},
  safeCollectionInput,
);

assert.deepEqual(safeCollectionInput, safeCollectionInputBefore);
assert.equal(lowCollections.Inbox.length, 0);
assert.deepEqual(lowCollections.Filtered.map((message) => message.id), [
  "filtered-sentinel",
  "alxb-promo-reminder",
]);
assert.deepEqual(lowCollections.Drafts, safeCollectionInput.Drafts);
assert.deepEqual(lowCollections.Sent, safeCollectionInput.Sent);
assert.deepEqual(lowCollections.Archive, safeCollectionInput.Archive);
assert.deepEqual(lowCollections.Spam, safeCollectionInput.Spam);
assert.deepEqual(lowCollections.Trash, safeCollectionInput.Trash);

const storedGmailLow = lowCollections.Filtered.find(
  (message) => message.id === "alxb-promo-reminder",
);
assert.ok(storedGmailLow);
assert.equal(storedGmailLow.providerFolder, "Inbox");
assert.deepEqual(storedGmailLow.labelIds, ["INBOX"]);
assert.equal(storedGmailLow.providerMessageId, "gmail-message-42");
assert.equal(storedGmailLow.providerThreadId, "gmail-thread-9");
assert.equal(storedGmailLow.final_visibility, "show_low");
assert.equal(storedGmailLow.action, "show_in_quiet_view");
assert.notEqual(storedGmailLow.action, "delete_or_archive");

const customImapLowCollections = routeSingleInboxMessage(
  alxbCustomImapLow.message,
);
assert.equal(customImapLowCollections.Inbox.length, 0);
assert.deepEqual(
  customImapLowCollections.Filtered.map((message) => message.id),
  ["alxb-promo-reminder"],
);
assert.equal(customImapLowCollections.Filtered[0]?.providerFolder, "INBOX");
assert.equal(customImapLowCollections.Filtered[0]?.labelIds, undefined);
assert.equal(customImapLowCollections.Filtered[0]?.imapUid, "42");
assert.equal(customImapLowCollections.Filtered[0]?.uidValidity, "9001");

for (const projected of [alxbGmailLow, alxbCustomImapLow]) {
  const serializedMessage = JSON.parse(
    JSON.stringify(projected.message),
  ) as MessageSeed;
  const reloadedMessage = normalizeMailMessage(
    serializedMessage,
    "main",
    {},
    {},
    "user-1",
  );
  const reloadedCollections = routeSingleInboxMessage(reloadedMessage);

  assert.equal(reloadedMessage.internalClassification, "promo_reminder");
  assert.equal(reloadedMessage.category, "Promo");
  assert.equal(
    resolveVisibleCategoryLabelForMessageInContext(reloadedMessage, false),
    "Promo",
  );
  assert.equal(
    getVisiblePriorityBadgeForWorkspaceMessage(
      reloadedMessage,
      undefined,
      lowPromoReminderFocusPreferences,
      { preferPromoMailboxContext: true },
    ),
    "LOW",
  );
  assert.equal(reloadedCollections.Inbox.length, 0);
  assert.deepEqual(reloadedCollections.Filtered.map((message) => message.id), [
    "alxb-promo-reminder",
  ]);
  assert.equal(reloadedMessage.providerFolder, projected.message.providerFolder);
  assert.deepEqual(reloadedMessage.labelIds, projected.message.labelIds);
  assert.equal(reloadedMessage.providerMessageId, projected.message.providerMessageId);
  assert.equal(reloadedMessage.providerThreadId, projected.message.providerThreadId);
  assert.equal(reloadedMessage.imapUid, projected.message.imapUid);
  assert.equal(reloadedMessage.uidValidity, projected.message.uidValidity);
}

const storedLowTransitionCollections: MailboxCollections = {
  ...lowCollections,
  Filtered: [storedGmailLow],
};
const normalCollections = applyFocusPreferenceRoutingToMailboxCollections(
  storedLowTransitionCollections,
  normalFocusPreferences,
  {},
  {},
  { preferPromoMailboxContext: true },
);
const alxbNormalFromStoredLow = normalCollections.Inbox.find(
  (message) => message.id === "alxb-promo-reminder",
);
assert.ok(alxbNormalFromStoredLow);
assert.equal(alxbNormalFromStoredLow.internalClassification, "promo_reminder");
assert.equal(alxbNormalFromStoredLow.priorityScore, "medium");
assert.equal(alxbNormalFromStoredLow.final_visibility, "show_normal");
assert.equal(alxbNormalFromStoredLow.action, "show_in_main_feed");
assert.equal(
  getVisiblePriorityBadgeForWorkspaceMessage(
    alxbNormalFromStoredLow,
    undefined,
    normalFocusPreferences,
    { preferPromoMailboxContext: true },
  ),
  "NORMAL",
);
assert.deepEqual(normalCollections.Inbox.map((message) => message.id), [
  "alxb-promo-reminder",
]);
assert.equal(normalCollections.Filtered.length, 0);
assert.deepEqual(normalCollections.Drafts, storedLowTransitionCollections.Drafts);
assert.deepEqual(normalCollections.Sent, storedLowTransitionCollections.Sent);
assert.deepEqual(normalCollections.Archive, storedLowTransitionCollections.Archive);
assert.deepEqual(normalCollections.Spam, storedLowTransitionCollections.Spam);
assert.deepEqual(normalCollections.Trash, storedLowTransitionCollections.Trash);

const reloadedAlxbNormal = normalizeMailMessage(
  JSON.parse(JSON.stringify(alxbNormalFromStoredLow)) as MessageSeed,
  "main",
  {},
  {},
  "user-1",
);
assert.equal(reloadedAlxbNormal.internalClassification, "promo_reminder");
assert.equal(reloadedAlxbNormal.category, "Promo");
assert.equal(reloadedAlxbNormal.final_visibility, "show_normal");
assert.equal(reloadedAlxbNormal.action, "show_in_main_feed");
assert.equal(
  getVisiblePriorityBadgeForWorkspaceMessage(
    reloadedAlxbNormal,
    undefined,
    normalFocusPreferences,
    { preferPromoMailboxContext: true },
  ),
  "NORMAL",
);
assert.deepEqual(
  routeSingleInboxMessage(reloadedAlxbNormal).Inbox.map((message) => message.id),
  ["alxb-promo-reminder"],
);

const lowAgainCollections = applyFocusPreferenceRoutingToMailboxCollections(
  normalCollections,
  lowPromoReminderFocusPreferences,
  {},
  {},
  { preferPromoMailboxContext: true },
);
assert.equal(lowAgainCollections.Inbox.length, 0);
assert.deepEqual(lowAgainCollections.Filtered.map((message) => message.id), [
  "alxb-promo-reminder",
]);

const highCollections = applyFocusPreferenceRoutingToMailboxCollections(
  lowAgainCollections,
  highPromoReminderFocusPreferences,
  {},
  {},
  { preferPromoMailboxContext: true },
);
const alxbHighFromStoredLow = highCollections.Inbox.find(
  (message) => message.id === "alxb-promo-reminder",
);
assert.ok(alxbHighFromStoredLow);
assert.equal(alxbHighFromStoredLow.priorityScore, "low");
assert.equal(alxbHighFromStoredLow.v7_final_priority, "NORMAL");
assert.equal(alxbHighFromStoredLow.final_visibility, "show_low");
assert.equal(alxbHighFromStoredLow.action, "show_in_quiet_view");
assert.equal(
  getVisiblePriorityBadgeForWorkspaceMessage(
    alxbHighFromStoredLow,
    undefined,
    highPromoReminderFocusPreferences,
    { preferPromoMailboxContext: true },
  ),
  "NORMAL",
);
assert.deepEqual(highCollections.Inbox.map((message) => message.id), [
  "alxb-promo-reminder",
]);
assert.equal(highCollections.Filtered.length, 0);

const manualPriorityCollections =
  applyFocusPreferenceRoutingToMailboxCollections(
    lowAgainCollections,
    lowPromoReminderFocusPreferences,
    {},
    { "alxb-promo-reminder": "priority" },
    { preferPromoMailboxContext: true },
  );
assert.deepEqual(manualPriorityCollections.Inbox.map((message) => message.id), [
  "alxb-promo-reminder",
]);
assert.equal(manualPriorityCollections.Filtered.length, 0);
const manuallyRestoredByNormalizationRouter =
  applyFilteredRoutingToMailboxCollections(
    storedLowTransitionCollections,
    {},
    { "alxb-promo-reminder": "priority" },
  );
assert.deepEqual(
  manuallyRestoredByNormalizationRouter.Inbox.map((message) => message.id),
  ["alxb-promo-reminder"],
);
assert.equal(manuallyRestoredByNormalizationRouter.Filtered.length, 0);

const trustedBackendReminder = projectAlxbReminder(
  gmailIdentity,
  "low",
  "promo_reminder",
  alxbPromoAccessRequestOverrides,
);
assert.equal(trustedBackendReminder.message.internalClassification, "promo_reminder");
assert.equal(trustedBackendReminder.message.categoryConfidence, "high");

const legacyProductionAlxbGmailSnapshotMessage = projectMessage(
  "promo",
  gmailIdentity,
  {
    ...alxbReminderOverrides,
    createdAt: "2026-07-16T08:00:00.000Z",
    timestamp: "2026-07-16T08:00:00.000Z",
    serverMailboxId: "main",
    providerFolder: "Inbox",
    labelIds: ["INBOX"],
    v7_final_priority: "NORMAL",
    final_visibility: "show_normal",
    action: "show_in_main_feed",
  },
).message;
const {
  providerMessageId: _legacyProviderMessageId,
  providerThreadId: _legacyProviderThreadId,
  labelIds: _legacyLabelIds,
  ...legacyProductionAlxbImapBase
} = legacyProductionAlxbGmailSnapshotMessage;
const legacyProductionAlxbImapSnapshotMessage = {
  ...legacyProductionAlxbImapBase,
  id: "alxb-promo-reminder-imap",
  serverMailboxId: "promo",
  providerFolder: "INBOX",
  imapUid: "42",
  uidValidity: "9001",
};
const liveSnapshotStorageKey = "cuevion-live-inbox-snapshots";
assert.equal(LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION, 6);
const liveSnapshotStorage = new Map<string, string>();
liveSnapshotStorage.set(
  liveSnapshotStorageKey,
  JSON.stringify({
    main: {
      schemaVersion: 5,
      classifierVersion: MUSIC_CLASSIFIER_VERSION,
      provider: "google",
      inboxId: "main",
      email: "promo@hysteriarecs.com",
      fetchedAt: "2026-07-16T08:05:00.000Z",
      folder: "INBOX",
      uidValidity: "gmail-api",
      messages: [legacyProductionAlxbGmailSnapshotMessage],
    },
    promo: {
      schemaVersion: 5,
      classifierVersion: MUSIC_CLASSIFIER_VERSION,
      provider: "custom_imap",
      inboxId: "promo",
      email: "promo@hysteriarecs.com",
      fetchedAt: "2026-07-16T08:05:00.000Z",
      folder: "INBOX",
      uidValidity: "9001",
      messages: [legacyProductionAlxbImapSnapshotMessage],
    },
  }),
);
const originalWindowDescriptor = Object.getOwnPropertyDescriptor(
  globalThis,
  "window",
);
Object.defineProperty(globalThis, "window", {
  configurable: true,
  value: {
    localStorage: {
      getItem: (key: string) => liveSnapshotStorage.get(key) ?? null,
      setItem: (key: string, value: string) => {
        liveSnapshotStorage.set(key, value);
      },
    },
  },
});

try {
  const migratedSnapshots = readLiveInboxSnapshots({
    main: { mailboxId: "main", provider: "google", folder: "INBOX" },
    promo: { mailboxId: "promo", provider: "custom_imap", folder: "INBOX" },
  });

  for (const mailboxId of ["main", "promo"] as const) {
    const hydratedSnapshot = hydrateLiveInboxSnapshot(
      migratedSnapshots[mailboxId],
    );
    assert.ok(hydratedSnapshot.context);
    assert.equal(hydratedSnapshot.messages.length, 1);
    const reloadedLegacyMessage = normalizeMailMessage(
      {
        ...hydratedSnapshot.messages[0],
        time: hydratedSnapshot.messages[0].timestamp,
      } as MessageSeed,
      mailboxId,
      {},
      {},
      "user-1",
    );
    const liveInboxSentinel = createFolderSentinel(
      `${mailboxId}-live-inbox-sentinel`,
      mailboxId === "main" ? "Inbox" : "INBOX",
    );
    const liveFilteredSentinel = createFolderSentinel(
      `${mailboxId}-live-filtered-sentinel`,
      mailboxId === "main" ? "Inbox" : "INBOX",
    );
    const legacyProductionCollectionsInput: MailboxCollections = {
      Inbox: [reloadedLegacyMessage, liveInboxSentinel],
      Drafts: [],
      Sent: [],
      Archive: [],
      Filtered: [liveFilteredSentinel],
      Spam: [],
      Trash: [],
    };
    const legacyProductionCollectionsInputBefore = structuredClone(
      legacyProductionCollectionsInput,
    );
    const legacyProductionLowCollections =
      applyPromoReminderFocusPreferenceRoutingToMailboxCollections(
        legacyProductionCollectionsInput,
        lowPromoReminderFocusPreferences,
        {},
      );
    const upgradedLegacyProductionAlxb =
      legacyProductionLowCollections.Filtered.find(
        (message) => message.id === reloadedLegacyMessage.id,
      );

    assert.ok(upgradedLegacyProductionAlxb);
    assert.deepEqual(
      legacyProductionCollectionsInput,
      legacyProductionCollectionsInputBefore,
    );
    assert.equal(
      upgradedLegacyProductionAlxb.internalClassification,
      "promo_reminder",
    );
    assert.equal(upgradedLegacyProductionAlxb.priorityScore, "low");
    assert.equal(upgradedLegacyProductionAlxb.v7_final_priority, "LOW");
    assert.equal(upgradedLegacyProductionAlxb.final_visibility, "show_low");
    assert.equal(upgradedLegacyProductionAlxb.action, "show_in_quiet_view");
    assert.deepEqual(
      legacyProductionLowCollections.Inbox.map((message) => message.id),
      [liveInboxSentinel.id],
    );
    assert.deepEqual(
      legacyProductionLowCollections.Filtered.map((message) => message.id),
      [liveFilteredSentinel.id, upgradedLegacyProductionAlxb.id],
    );
    const unrelatedSenderLearning = {
      "learned-elsewhere@example.test": {
        learnedCategory: "Updates" as const,
        learnedFromCount: 3,
        autoCategoryEnabled: true,
        mailboxAction: "move" as const,
        sourcePrioritySelection: "Show Less" as const,
      },
    };
    const unrelatedSenderLearningBefore = structuredClone(
      unrelatedSenderLearning,
    );
    const legacyLowWithUnrelatedLearning =
      applyPromoReminderFocusPreferenceRoutingToMailboxCollections(
        {
          Inbox: [reloadedLegacyMessage],
          Drafts: [],
          Sent: [],
          Archive: [],
          Filtered: [],
          Spam: [],
          Trash: [],
        },
        lowPromoReminderFocusPreferences,
        unrelatedSenderLearning,
      );
    assert.deepEqual(
      legacyLowWithUnrelatedLearning.Filtered.map((message) => message.id),
      [upgradedLegacyProductionAlxb.id],
    );
    assert.deepEqual(unrelatedSenderLearning, unrelatedSenderLearningBefore);
    assert.equal(
      upgradedLegacyProductionAlxb.createdAt,
      "2026-07-16T08:00:00.000Z",
    );
    assert.equal(
      upgradedLegacyProductionAlxb.serverMailboxId,
      mailboxId,
    );

    if (mailboxId === "main") {
      assert.equal(
        upgradedLegacyProductionAlxb.providerMessageId,
        "gmail-message-42",
      );
      assert.equal(
        upgradedLegacyProductionAlxb.providerThreadId,
        "gmail-thread-9",
      );
      assert.deepEqual(upgradedLegacyProductionAlxb.labelIds, ["INBOX"]);
      assert.equal(upgradedLegacyProductionAlxb.providerFolder, "Inbox");
    } else {
      assert.equal(upgradedLegacyProductionAlxb.imapUid, "42");
      assert.equal(upgradedLegacyProductionAlxb.uidValidity, "9001");
      assert.equal(upgradedLegacyProductionAlxb.providerFolder, "INBOX");
      assert.equal(upgradedLegacyProductionAlxb.providerMessageId, undefined);
      assert.equal(upgradedLegacyProductionAlxb.labelIds, undefined);
    }

    const legacyProductionNormalCollections =
      applyPromoReminderFocusPreferenceRoutingToMailboxCollections(
        legacyProductionLowCollections,
        normalFocusPreferences,
        {},
    );
    const normalLegacyProductionAlxb =
      legacyProductionNormalCollections.Inbox.find(
        (message) => message.id === reloadedLegacyMessage.id,
      );
    assert.ok(normalLegacyProductionAlxb);
    assert.equal(normalLegacyProductionAlxb.internalClassification, "promo_reminder");
    assert.equal(normalLegacyProductionAlxb.priorityScore, "medium");
    assert.equal(normalLegacyProductionAlxb.v7_final_priority, "NORMAL");
    assert.equal(normalLegacyProductionAlxb.final_visibility, "show_normal");
    assert.equal(normalLegacyProductionAlxb.action, "show_in_main_feed");
    assert.deepEqual(
      legacyProductionNormalCollections.Inbox.map((message) => message.id),
      [liveInboxSentinel.id, normalLegacyProductionAlxb.id],
    );
    assert.deepEqual(
      legacyProductionNormalCollections.Filtered.map((message) => message.id),
      [liveFilteredSentinel.id],
    );
    const matchingSenderShowLessLearning = {
      "promo@alxbrecords.com": {
        learnedCategory: "Updates" as const,
        learnedFromCount: 3,
        autoCategoryEnabled: true,
        mailboxAction: "move" as const,
        sourcePrioritySelection: "Show Less" as const,
      },
    };
    const normalWithMatchingSenderLearning =
      applyPromoReminderFocusPreferenceRoutingToMailboxCollections(
        legacyProductionLowCollections,
        normalFocusPreferences,
        matchingSenderShowLessLearning,
      );
    assert.ok(
      normalWithMatchingSenderLearning.Inbox.some(
        (message) => message.id === normalLegacyProductionAlxb.id,
      ),
    );
    assert.equal(
      normalWithMatchingSenderLearning.Filtered.some(
        (message) => message.id === normalLegacyProductionAlxb.id,
      ),
      false,
    );
    if (mailboxId === "main") {
      const manualPriorityLegacyCollections =
        applyPromoReminderFocusPreferenceRoutingToMailboxCollections(
          legacyProductionLowCollections,
          lowPromoReminderFocusPreferences,
          {},
          { [normalLegacyProductionAlxb.id]: "priority" },
        );
      assert.ok(
        manualPriorityLegacyCollections.Inbox.some(
          (message) => message.id === normalLegacyProductionAlxb.id,
        ),
      );
      assert.equal(
        manualPriorityLegacyCollections.Filtered.some(
          (message) => message.id === normalLegacyProductionAlxb.id,
        ),
        false,
      );
    }
  }

  const persistedMigratedSnapshots = JSON.parse(
    liveSnapshotStorage.get(liveSnapshotStorageKey) ?? "{}",
  ) as Record<string, {
    schemaVersion?: number;
    messages?: Array<{ internalClassification?: string }>;
  }>;
  assert.equal(
    persistedMigratedSnapshots.main?.schemaVersion,
    LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
  );
  assert.equal(
    persistedMigratedSnapshots.promo?.schemaVersion,
    LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
  );
  assert.equal(
    persistedMigratedSnapshots.main?.messages?.[0]?.internalClassification,
    "promo_reminder",
  );
  assert.equal(
    persistedMigratedSnapshots.promo?.messages?.[0]?.internalClassification,
    "promo_reminder",
  );

  const reloadedMigratedSnapshots = readLiveInboxSnapshots({
    main: { mailboxId: "main", provider: "google", folder: "INBOX" },
    promo: { mailboxId: "promo", provider: "custom_imap", folder: "INBOX" },
  });
  assert.equal(
    reloadedMigratedSnapshots.main?.messages[0]?.internalClassification,
    "promo_reminder",
  );
  assert.equal(
    reloadedMigratedSnapshots.promo?.messages[0]?.internalClassification,
    "promo_reminder",
  );

  liveSnapshotStorage.set(
    liveSnapshotStorageKey,
    JSON.stringify({
      main: {
        schemaVersion: LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
        classifierVersion: MUSIC_CLASSIFIER_VERSION,
        provider: "google",
        inboxId: "main",
        email: "promo@hysteriarecs.com",
        fetchedAt: "2026-08-11T08:05:00.000Z",
        folder: "INBOX",
        uidValidity: "gmail-api",
        messages: [legacyProductionAlxbGmailSnapshotMessage],
      },
    }),
  );
  const currentSchemaSnapshot = readLiveInboxSnapshots({
    main: { mailboxId: "main", provider: "google", folder: "INBOX" },
  }).main;
  assert.equal(
    currentSchemaSnapshot?.messages[0]?.internalClassification,
    "promo",
  );
} finally {
  if (originalWindowDescriptor) {
    Object.defineProperty(globalThis, "window", originalWindowDescriptor);
  } else {
    Reflect.deleteProperty(globalThis, "window");
  }
}

for (const [id, internalClassification, subject, body, noiseAssessment] of [
  [
    "payment-reminder",
    "business_reminder",
    "Payment reminder for invoice 2026-0811",
    "The invoice remains outstanding.",
    undefined,
  ],
  [
    "promo-payment-reminder",
    "promo",
    "Reminder: DJ promo payment invoice 2026-0811",
    "Listen and download the new release.",
    undefined,
  ],
  [
    "promo-body-payment-reminder",
    "promo",
    "Reminder: action required for your DJ promo",
    "The outstanding invoice payment is still due. Listen and download the new release.",
    undefined,
  ],
  [
    "promo-contract-reminder",
    "promo",
    "Reminder: DJ promo contract approval needed",
    "Listen and download the new release.",
    undefined,
  ],
  [
    "promo-security-reminder",
    "promo",
    "Reminder: DJ promo security verification",
    "Listen and download the new release.",
    undefined,
  ],
  [
    "promo-meeting-reminder",
    "promo",
    "Reminder: DJ promo meeting tomorrow",
    "Listen and download the new release.",
    undefined,
  ],
  [
    "promo-subscription-reminder",
    "promo",
    "Reminder: DJ promo subscription renewal",
    "Listen and download the new release.",
    undefined,
  ],
  [
    "ordinary-promo-compatibility",
    "promo",
    "New DJ promo available",
    "Listen and download the new release.",
    undefined,
  ],
  [
    "forwarded-promo-reminder-compatibility",
    "promo",
    "Fwd: Friendly reminder: new DJ promo available",
    "Listen and download the new release.",
    undefined,
  ],
  [
    "noise-promo-reminder",
    "promo",
    "Friendly reminder: new DJ promo available",
    "Listen and download the new release.",
    strongNoiseAssessment,
  ],
] as const) {
  const candidate = {
    ...legacyProductionAlxbGmailSnapshotMessage,
    id,
    internalClassification,
    subject,
    snippet: body,
    body: [body],
    ...(noiseAssessment ?? {}),
  };
  const candidateBeforeCompatibility = structuredClone(candidate);
  const compatibleMessage =
    upconvertLegacyPromoReminderSnapshotMessage(candidate);

  assert.deepEqual(candidate, candidateBeforeCompatibility, id);
  assert.equal(
    compatibleMessage.internalClassification,
    internalClassification,
    id,
  );
  assert.equal(
    compatibleMessage.providerMessageId,
    candidateBeforeCompatibility.providerMessageId,
  );
  assert.equal(
    compatibleMessage.providerThreadId,
    candidateBeforeCompatibility.providerThreadId,
  );
  assert.deepEqual(
    compatibleMessage.labelIds,
    candidateBeforeCompatibility.labelIds,
  );
  assert.equal(
    compatibleMessage.providerFolder,
    candidateBeforeCompatibility.providerFolder,
  );
}

for (const [id, compatibilityGuard] of [
  ["reply-ui-signal", { ui_signal: "REPLY" }],
  ["reply-signal", { signal: "Follow-up" }],
  ["auto-reply", { isAutoReply: true }],
  ["shared-message", { isShared: true }],
  ["collaboration-message", { collaboration: { updatedAt: 1 } }],
] as const) {
  const guardedMessage = upconvertLegacyPromoReminderSnapshotMessage({
    ...legacyProductionAlxbGmailSnapshotMessage,
    id,
    ...compatibilityGuard,
  });
  assert.equal(guardedMessage.internalClassification, "promo", id);
}

const footerOnlyReminderPromo =
  upconvertLegacyPromoReminderSnapshotMessage({
    ...legacyProductionAlxbGmailSnapshotMessage,
    id: "footer-only-reminder-promo",
    sender: "Artist Updates",
    from: "artist-updates@example.test",
    subject: "New release promo",
    snippet: "Listen to our new release.",
    body: ["We are reminding you about promotional emails."],
  });
assert.equal(footerOnlyReminderPromo.internalClassification, "promo");

const legacyAlxbWithSubscriptionFooter =
  upconvertLegacyPromoReminderSnapshotMessage({
    ...legacyProductionAlxbGmailSnapshotMessage,
    body: [
      "Listen and download the promo when you are ready.",
      "Manage your subscription or unsubscribe from future promo mail.",
    ],
  });
assert.equal(
  legacyAlxbWithSubscriptionFooter.internalClassification,
  "promo_reminder",
);
const legacyPromoWithBodyOnlyPaymentContext =
  upconvertLegacyPromoReminderSnapshotMessage({
    ...legacyProductionAlxbGmailSnapshotMessage,
    subject: "Reminder: action required for your DJ promo",
    snippet: "Your promo invite is still available.",
    body: [
      "The outstanding invoice payment is still due.",
      "Listen and download the new release.",
    ],
  });
assert.equal(
  legacyPromoWithBodyOnlyPaymentContext.internalClassification,
  "promo",
);

for (const rawInternalClassification of [undefined, "unknown"] as const) {
  const legacyReminder = projectAlxbReminder(
    gmailIdentity,
    "low",
    rawInternalClassification,
  );
  const legacyCollections = routeSingleInboxMessage(legacyReminder.message);

  assert.notEqual(
    legacyReminder.message.internalClassification,
    "promo_reminder",
  );
  assert.equal(legacyReminder.visibleCategoryLabel, "Promo");
  assert.equal(
    getVisiblePriorityBadgeForWorkspaceMessage(
      legacyReminder.message,
      undefined,
      lowPromoReminderFocusPreferences,
      { preferPromoMailboxContext: true },
    ),
    "NORMAL",
  );
  assert.deepEqual(legacyCollections.Inbox.map((message) => message.id), [
    "alxb-promo-reminder",
  ]);
  assert.equal(legacyCollections.Filtered.length, 0);
  assert.equal(legacyReminder.message.providerFolder, "Inbox");
  assert.deepEqual(legacyReminder.message.labelIds, ["INBOX"]);
}

for (const [subject, body] of [
  ["Friendly reminder: new DJ promo available", "Listen and download the new release."],
  ["Reminder - remix promo", "The new remix is available to listen and download."],
] as const) {
  const projected = projectMessage("promo_reminder", gmailIdentity, {
    subject,
    snippet: body,
    body: [body],
    ui_signal: "PROMO",
  });
  assert.equal(projected.message.internalClassification, "promo_reminder", subject);
}

for (const [subject, uiSignal] of [
  ["Re: Friendly reminder: new DJ promo available", "REPLY"],
  ["Fwd: Reminder - remix promo", "REPLY"],
] as const) {
  const projected = projectMessage("promo", gmailIdentity, {
    id: `reply-${subject}`,
    subject,
    snippet: "Listen and download the new release.",
    body: ["Listen and download the new release."],
    signal: undefined,
    ui_signal: uiSignal,
  });

  assert.notEqual(
    projected.message.internalClassification,
    "promo_reminder",
    subject,
  );
  assert.deepEqual(
    routeSingleInboxMessage(projected.message).Inbox.map((message) => message.id),
    [`reply-${subject}`],
  );
}

const forwardedPromoWithoutReplyMetadata = projectMessage("promo", gmailIdentity, {
  id: "forwarded-promo",
  subject: "Fwd: Friendly reminder: new DJ promo available",
  snippet: "Listen and download the new release.",
  body: ["Listen and download the new release."],
  signal: undefined,
  ui_signal: "PROMO",
});
assert.equal(forwardedPromoWithoutReplyMetadata.message.internalClassification, "promo");
assert.deepEqual(
  routeSingleInboxMessage(forwardedPromoWithoutReplyMetadata.message).Inbox.map(
    (message) => message.id,
  ),
  ["forwarded-promo"],
);

const autoReplyPromo = projectMessage("promo", gmailIdentity, {
  subject: "Friendly reminder: new DJ promo available",
  snippet: "Automatic response",
  body: ["Automatic response"],
  ui_signal: "PROMO",
  isAutoReply: true,
});
assert.equal(autoReplyPromo.message.internalClassification, "promo");

const trustedForwardedBackendReminder = projectMessage(
  "promo_reminder",
  gmailIdentity,
  {
    subject: "Fwd: (Reminder) Promo Invite from ALXB Records",
    snippet: "Your promo invite is still available.",
    body: ["Listen and download the promo."],
    ui_signal: "PROMO",
  },
);
assert.equal(
  trustedForwardedBackendReminder.message.internalClassification,
  "promo_reminder",
);

for (const subject of [
  "Payment reminder for invoice 2026-0811",
  "Reminder: contract approval needed",
  "Reminder about tomorrow's meeting",
  "Security reminder",
  "Subscription renewal reminder",
] as const) {
  const projected = projectMessage("promo", gmailIdentity, {
    subject,
    snippet: subject,
    body: [subject],
    ui_signal: "PROMO",
  });
  assert.equal(projected.message.internalClassification, "promo", subject);
}

for (const subject of [
  "Reminder: DJ promo payment invoice 2026-0811",
  "Reminder: DJ promo contract approval needed",
  "Reminder: DJ promo meeting tomorrow",
  "Reminder: DJ promo security verification",
  "Reminder: DJ promo subscription renewal",
] as const) {
  const projected = projectMessage("promo", gmailIdentity, {
    subject,
    snippet: "Listen and download the new release.",
    body: ["Listen and download the new release."],
    ui_signal: "PROMO",
  });
  assert.equal(projected.message.internalClassification, "promo", subject);
}

const bodyOnlyPaymentReminder = projectMessage("promo", gmailIdentity, {
  subject: "Reminder: action required",
  snippet: "The outstanding invoice is still payment due.",
  body: [
    "The outstanding invoice is still payment due.",
    "Listen and download the new release DJ promo.",
  ],
  ui_signal: "PROMO",
});
assert.equal(bodyOnlyPaymentReminder.message.internalClassification, "promo");

const ordinaryPromo = projectMessage("promo", gmailIdentity, {
  id: "ordinary-promo",
  subject: "New DJ promo available",
  snippet: "Listen and download the new release.",
  body: ["Listen and download the new release."],
  ui_signal: "PROMO",
  final_visibility: "show_normal",
  action: "show_in_main_feed",
});
assert.equal(ordinaryPromo.message.internalClassification, "promo");
assert.deepEqual(routeSingleInboxMessage(ordinaryPromo.message).Inbox.map((message) => message.id), [
  "ordinary-promo",
]);

const paymentReminder = projectMessage("business_reminder", gmailIdentity, {
  subject: "Payment reminder for invoice 2026-0811",
  snippet: "The invoice remains outstanding.",
  body: ["The invoice remains outstanding."],
  ui_signal: "BUSINESS",
});
assert.equal(paymentReminder.message.internalClassification, "business_reminder");
assert.equal(paymentReminder.message.category, "Updates");
assert.equal(paymentReminder.visibleCategoryLabel, "Business");
assert.equal(
  getVisiblePriorityBadgeForWorkspaceMessage(
    paymentReminder.message,
    undefined,
    lowPaymentReminderFocusPreferences,
  ),
  "LOW",
);
assert.equal(
  getVisiblePriorityBadgeForWorkspaceMessage(
    paymentReminder.message,
    undefined,
    normalFocusPreferences,
  ),
  "NORMAL",
);

const learnedShowLessMessage = projectMessage("info", gmailIdentity, {
  id: "learned-show-less",
  subject: "Sender update",
  snippet: "A routine sender update.",
  body: ["A routine sender update."],
  from: "updates@example.test",
  ui_signal: "UPDATE",
  final_visibility: "show_normal",
  action: "show_in_main_feed",
}).message;
const learnedShowLessCollections = routeSingleInboxMessage(
  learnedShowLessMessage,
  {
    "updates@example.test": {
      learnedCategory: "Updates",
      learnedFromCount: 3,
      autoCategoryEnabled: true,
      mailboxAction: "move",
      sourcePrioritySelection: "Show Less",
    },
  },
);
assert.equal(learnedShowLessCollections.Inbox.length, 0);
assert.deepEqual(learnedShowLessCollections.Filtered.map((message) => message.id), [
  "learned-show-less",
]);

const legacyGlobalShowLessMessage = projectMessage(
  "business_reminder",
  gmailIdentity,
  {
    id: "legacy-global-show-less",
    subject: "Routine business reminder",
    snippet: "A routine low-priority reminder.",
    body: ["A routine low-priority reminder."],
    from: "other-reminders@example.test",
    ui_signal: "BUSINESS",
    final_visibility: "show_low",
    action: "show_in_quiet_view",
  },
).message;
const legacyGlobalShowLessCollections = routeSingleInboxMessage(
  legacyGlobalShowLessMessage,
  {
    "learned-elsewhere@example.test": {
      learnedCategory: "Updates",
      learnedFromCount: 3,
      autoCategoryEnabled: true,
      mailboxAction: "move",
      sourcePrioritySelection: "Show Less",
    },
  },
);
assert.equal(legacyGlobalShowLessCollections.Inbox.length, 0);
assert.deepEqual(
  legacyGlobalShowLessCollections.Filtered.map((message) => message.id),
  ["legacy-global-show-less"],
);

console.log("✓ WorkspaceShell classification contract");
