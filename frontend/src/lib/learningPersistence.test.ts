import assert from "node:assert/strict";
import {
  buildLearningStorageKey,
  hydrateScopedSenderCategoryLearning,
  persistScopedSenderCategoryLearning,
  selectScopedSenderCategoryLearning,
  updateScopedSenderCategoryLearning,
} from "./learningPersistence";
import {
  resolveSenderLearningEntry,
  type SenderCategoryLearningStore,
} from "./learningEngine";
import {
  applyCurrentMessageCategoryDecision,
  applyLearningDecision,
} from "./applyLearningDecision";
import { buildRecentLearningDecisions } from "./forYouEngine";
import {
  resolveMailMessageBehaviorSuggestion,
  resolveSuggestedMessageAction,
} from "./suggestionEngine";

const LEGACY_LEARNING_STORAGE_KEY = "cuevion-sender-category-learning";

class MemoryStorage {
  private readonly values = new Map<string, string>();

  constructor(entries: Array<[string, string]> = []) {
    entries.forEach(([key, value]) => this.values.set(key, value));
  }

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}

function requireLearningStorageKey(workspaceId: string, userId: string) {
  const storageKey = buildLearningStorageKey(workspaceId, userId);
  assert.ok(storageKey);
  return storageKey;
}

const workspaceAUserAKey = requireLearningStorageKey(
  "workspace:alpha",
  "user/alpha",
);
const workspaceAUserBKey = requireLearningStorageKey(
  "workspace:alpha",
  "user/beta",
);
const workspaceBUserAKey = requireLearningStorageKey(
  "workspace:beta",
  "user/alpha",
);

assert.notEqual(
  workspaceAUserAKey,
  workspaceAUserBKey,
  "the same workspace must isolate different users",
);
assert.notEqual(
  workspaceAUserAKey,
  workspaceBUserAKey,
  "the same user must be isolated across workspaces",
);
assert.equal(
  workspaceAUserAKey,
  buildLearningStorageKey("workspace:alpha", "user/alpha"),
  "the same authenticated identity must produce a stable key",
);
assert.equal(
  workspaceAUserAKey,
  "cuevion-sender-category-learning:v2:workspace%3Aalpha:user%2Falpha",
  "opaque identity components must be encoded without sender normalization",
);
assert.notEqual(workspaceAUserAKey, LEGACY_LEARNING_STORAGE_KEY);

const mailboxContexts = [
  { mailboxId: "main", provider: "google" },
  { mailboxId: "promo", provider: "custom_imap" },
];
assert.deepEqual(
  mailboxContexts.map(() =>
    buildLearningStorageKey("workspace:alpha", "user/alpha"),
  ),
  [workspaceAUserAKey, workspaceAUserAKey],
  "mailbox and provider context must not create another Learning namespace",
);
assert.equal(buildLearningStorageKey("", "user/alpha"), null);
assert.equal(buildLearningStorageKey("workspace:alpha", "  "), null);

const unsupportedIdentityStorage = new MemoryStorage();
const unsupportedIdentityState = hydrateScopedSenderCategoryLearning(
  unsupportedIdentityStorage,
  null,
);
assert.deepEqual(
  selectScopedSenderCategoryLearning(unsupportedIdentityState, null),
  {},
);
assert.strictEqual(
  updateScopedSenderCategoryLearning(
    unsupportedIdentityState,
    null,
    {
      "unsupported@example.com": {
        learnedCategory: "Primary",
        learnedFromCount: 1,
      },
    },
  ),
  unsupportedIdentityState,
  "missing authenticated identity must keep Learning empty",
);
assert.equal(
  persistScopedSenderCategoryLearning(
    unsupportedIdentityStorage,
    unsupportedIdentityState,
    null,
  ),
  false,
  "missing authenticated identity must never persist Learning",
);

const userALearning: SenderCategoryLearningStore = {
  "person@example.com": {
    learnedCategory: "Primary",
    learnedLabel: "Business",
    learnedFromCount: 4,
    autoCategoryEnabled: false,
    mailboxAction: "keep",
    senderBehavior: "normal",
    sourceContext: "refine",
    sourcePrioritySelection: "Important",
    sourceMailboxId: "main",
    sourceCurrentMailboxId: "promo",
    updatedAt: "2026-08-23T12:00:00.000Z",
  },
  "domain:example.net": {
    learnedCategory: "Updates",
    learnedLabel: "Update",
    learnedFromCount: 3,
    autoCategoryEnabled: true,
    mailboxAction: "move",
    senderBehavior: "show_less",
    sourceContext: "paste_sender_or_domain",
    sourcePrioritySelection: "Show Less",
    sourceMailboxId: "promo",
    sourceCurrentMailboxId: null,
    updatedAt: "2026-08-23T13:00:00.000Z",
  },
};
const workspaceBLearning: SenderCategoryLearningStore = {
  "workspace-b@example.org": {
    learnedCategory: "Promo",
    learnedFromCount: 2,
    mailboxAction: "move",
  },
};
const legacyRawValue = JSON.stringify({
  "legacy@example.com": {
    learnedCategory: "Updates",
    learnedFromCount: 99,
  },
});

const legacyOnlyStorage = new MemoryStorage([
  [LEGACY_LEARNING_STORAGE_KEY, legacyRawValue],
]);
const emptyScopedState = hydrateScopedSenderCategoryLearning(
  legacyOnlyStorage,
  workspaceAUserAKey,
);
assert.deepEqual(
  emptyScopedState.store,
  {},
  "legacy bare-key records must never hydrate as active v2 Learning",
);
assert.equal(
  persistScopedSenderCategoryLearning(
    legacyOnlyStorage,
    emptyScopedState,
    workspaceAUserAKey,
  ),
  true,
);
assert.equal(
  legacyOnlyStorage.getItem(LEGACY_LEARNING_STORAGE_KEY),
  legacyRawValue,
  "scoped hydration and writes must leave legacy bytes untouched",
);

const roundTripStorage = new MemoryStorage();
let roundTripState = hydrateScopedSenderCategoryLearning(
  roundTripStorage,
  workspaceAUserAKey,
);
roundTripState = updateScopedSenderCategoryLearning(
  roundTripState,
  workspaceAUserAKey,
  userALearning,
);
assert.equal(
  persistScopedSenderCategoryLearning(
    roundTripStorage,
    roundTripState,
    workspaceAUserAKey,
  ),
  true,
);
assert.deepEqual(
  hydrateScopedSenderCategoryLearning(
    roundTripStorage,
    workspaceAUserAKey,
  ).store,
  userALearning,
  "update, write, and hydration must preserve the complete flat Learning store",
);
const invalidRuntimeStore = {
  "person@example.com": {
    learnedCategory: "Primary",
    learnedFromCount: 1,
    senderBehavior: "invalid",
  },
} as unknown as SenderCategoryLearningStore;
assert.strictEqual(
  updateScopedSenderCategoryLearning(
    roundTripState,
    workspaceAUserAKey,
    invalidRuntimeStore,
  ),
  roundTripState,
  "invalid runtime updates must not enter scoped state",
);
assert.equal(
  persistScopedSenderCategoryLearning(
    roundTripStorage,
    { storageKey: workspaceAUserAKey, store: invalidRuntimeStore },
    workspaceAUserAKey,
  ),
  false,
  "invalid runtime state must not cross the scoped write boundary",
);

const switchStorage = new MemoryStorage([
  [workspaceAUserAKey, JSON.stringify(userALearning)],
]);
let activeLearningState = hydrateScopedSenderCategoryLearning(
  switchStorage,
  workspaceAUserAKey,
);
assert.deepEqual(
  selectScopedSenderCategoryLearning(activeLearningState, workspaceAUserAKey),
  userALearning,
  "matching v2 data must hydrate for its exact identity",
);
assert.deepEqual(
  selectScopedSenderCategoryLearning(activeLearningState, workspaceAUserBKey),
  {},
  "an identity change must immediately stop using the previous scope",
);
activeLearningState = hydrateScopedSenderCategoryLearning(
  switchStorage,
  workspaceAUserBKey,
);
assert.deepEqual(
  selectScopedSenderCategoryLearning(activeLearningState, workspaceAUserBKey),
  {},
  "a user with no v2 data must receive empty Learning",
);
activeLearningState = hydrateScopedSenderCategoryLearning(
  switchStorage,
  workspaceAUserAKey,
);
assert.deepEqual(
  selectScopedSenderCategoryLearning(activeLearningState, workspaceAUserAKey),
  userALearning,
  "switching back must restore only the original identity's v2 data",
);

const workspaceIsolationStorage = new MemoryStorage([
  [workspaceAUserAKey, JSON.stringify(userALearning)],
  [workspaceBUserAKey, JSON.stringify(workspaceBLearning)],
]);
assert.deepEqual(
  hydrateScopedSenderCategoryLearning(
    workspaceIsolationStorage,
    workspaceAUserAKey,
  ).store,
  userALearning,
);
assert.deepEqual(
  hydrateScopedSenderCategoryLearning(
    workspaceIsolationStorage,
    workspaceBUserAKey,
  ).store,
  workspaceBLearning,
  "workspace identity must isolate Learning for the same user",
);

const raceStorage = new MemoryStorage([
  [workspaceAUserAKey, JSON.stringify(userALearning)],
]);
const staleUserAState = hydrateScopedSenderCategoryLearning(
  raceStorage,
  workspaceAUserAKey,
);
assert.strictEqual(
  updateScopedSenderCategoryLearning(
    staleUserAState,
    workspaceAUserBKey,
    workspaceBLearning,
  ),
  staleUserAState,
  "a stale state update must be rejected after the active identity changes",
);
assert.equal(
  persistScopedSenderCategoryLearning(
    raceStorage,
    staleUserAState,
    workspaceAUserBKey,
  ),
  false,
  "A state must never be written under B's key",
);
assert.equal(raceStorage.getItem(workspaceAUserBKey), null);
assert.equal(
  raceStorage.getItem(workspaceAUserAKey),
  JSON.stringify(userALearning),
  "a rejected cross-identity write must not mutate A either",
);

const crossMailboxState = hydrateScopedSenderCategoryLearning(
  switchStorage,
  workspaceAUserAKey,
);
const crossMailboxMessages = [
  { mailboxId: "main", from: "Person <person@example.com>" },
  { mailboxId: "promo", from: "person@example.com" },
];
assert.deepEqual(
  crossMailboxMessages.map(({ from }) =>
    resolveSenderLearningEntry(from, crossMailboxState.store)?.entry.learnedCategory,
  ),
  ["Primary", "Primary"],
  "one exact-sender rule must remain available to messages in different mailboxes",
);
assert.deepEqual(
  [
    { mailboxId: "main", from: "first@example.net" },
    { mailboxId: "promo", from: "second@example.net" },
  ].map(({ from }) =>
    resolveSenderLearningEntry(from, crossMailboxState.store)?.entry.learnedCategory,
  ),
  ["Updates", "Updates"],
  "one domain rule must remain available to messages in different mailboxes",
);

assert.deepEqual(
  crossMailboxState.store,
  userALearning,
  "persistence must preserve positive Important, counts, and metadata",
);
const informationalMessage = {
  from: "person@example.com",
  sender: "Person",
  subject: "Project update",
  snippet: "The latest project update is attached for reference.",
  body: ["The latest project update is attached for reference."],
  attachments: [],
  noiseDisposition: "none" as const,
  noiseConfidence: "high" as const,
  noiseReasons: [],
};
const priorityBehaviorBeforePersistence = resolveSuggestedMessageAction(
  informationalMessage,
  "Primary",
  userALearning,
);
const priorityBehaviorAfterPersistence = resolveSuggestedMessageAction(
  informationalMessage,
  "Primary",
  crossMailboxState.store,
);
assert.deepEqual(
  priorityBehaviorAfterPersistence,
  priorityBehaviorBeforePersistence,
  "scoped persistence must be transparent to the frozen Priority decision",
);
assert.notEqual(
  priorityBehaviorAfterPersistence.type,
  "reply",
  "persisted Learning Important must remain non-canonical Priority authority",
);

const staleSenderExclusionStore: SenderCategoryLearningStore = {
  "domain:example.com": {
    learnedCategory: "Updates",
    learnedLabel: "Update",
    learnedFromCount: 4,
    autoCategoryEnabled: true,
    mailboxAction: "move",
    senderBehavior: "show_less",
    sourcePrioritySelection: "Show Less",
  },
  "person@example.com": {
    learnedCategory: "Primary",
    learnedLabel: "Business",
    learnedFromCount: 99,
    autoCategoryEnabled: true,
    mailboxAction: "keep",
    senderBehavior: "do_not_learn",
    sourcePrioritySelection: "Important",
    updatedAt: "2026-08-23T14:00:00.000Z",
  },
};

assert.equal(
  resolveSenderLearningEntry("person@example.com", staleSenderExclusionStore),
  null,
  "an exact sender exclusion must expose no stale category, routing, Priority, or action authority",
);
assert.equal(
  resolveSenderLearningEntry("Person <person@example.com>", staleSenderExclusionStore),
  null,
  "an exact sender exclusion must block positive domain fallback",
);
assert.equal(
  resolveSenderLearningEntry("other@example.com", staleSenderExclusionStore)?.key,
  "domain:example.com",
  "a sender exclusion must leave the domain rule active for other senders",
);
assert.deepEqual(
  resolveSuggestedMessageAction(informationalMessage, "Primary", staleSenderExclusionStore),
  resolveSuggestedMessageAction(informationalMessage, "Primary"),
  "an exclusion must not create a Learning-derived action suggestion",
);
assert.equal(
  resolveMailMessageBehaviorSuggestion(
    informationalMessage,
    {
      category: "Primary",
      categorySource: "user",
      categoryConfidence: "medium",
    },
    staleSenderExclusionStore,
    true,
  ),
  undefined,
  "an exclusion must not create an auto-category suggestion",
);

const existingSenderRule: SenderCategoryLearningStore = {
  "person@example.com": {
    learnedCategory: "Primary",
    learnedLabel: "Business",
    learnedFromCount: 5,
    autoCategoryEnabled: true,
    mailboxAction: "keep",
    senderBehavior: "always_prioritize",
    sourcePrioritySelection: "Important",
  },
};
const excludedSenderResult = applyLearningDecision({
  senderCategoryLearning: existingSenderRule,
  ruleValue: "person@example.com",
  ruleType: "sender",
  category: "Promo",
  learnedLabel: "Promo",
  mailboxAction: "move",
  senderBehavior: "do_not_learn",
  sourcePrioritySelection: "Show Less",
  updatedAt: "2026-08-23T15:00:00.000Z",
});
assert.ok(excludedSenderResult);
assert.deepEqual(
  excludedSenderResult.nextEntry,
  {
    learnedCategory: "Promo",
    learnedFromCount: 0,
    senderBehavior: "do_not_learn",
    sourceContext: undefined,
    sourceMailboxId: undefined,
    sourceCurrentMailboxId: undefined,
    updatedAt: "2026-08-23T15:00:00.000Z",
  },
  "Do not learn must replace an existing exact rule with exclusion-only state",
);
assert.equal(
  resolveSenderLearningEntry(
    "person@example.com",
    excludedSenderResult.nextSenderCategoryLearning,
  ),
  null,
);
assert.deepEqual(
  excludedSenderResult.nextRecentLearningDecisions,
  [],
  "exclusions must not appear as active Recent Learning Decisions",
);

const retrainedSenderResult = applyLearningDecision({
  senderCategoryLearning: excludedSenderResult.nextSenderCategoryLearning,
  ruleValue: "person@example.com",
  ruleType: "sender",
  category: "Updates",
  learnedLabel: "Update",
  mailboxAction: "move",
  senderBehavior: "normal",
  sourcePrioritySelection: "Normal",
  learnedFromCountFloor: 3,
  updatedAt: "2026-08-24T09:00:00.000Z",
});
assert.ok(retrainedSenderResult);
assert.equal(
  resolveSenderLearningEntry(
    "person@example.com",
    retrainedSenderResult.nextSenderCategoryLearning,
  )?.entry.learnedCategory,
  "Updates",
  "explicit positive sender retraining must replace the exclusion",
);

const domainExclusionResult = applyLearningDecision({
  senderCategoryLearning: {
    "domain:example.net": userALearning["domain:example.net"],
    "vip@example.net": userALearning["person@example.com"],
  },
  ruleValue: "example.net",
  ruleType: "domain",
  category: "Updates",
  senderBehavior: "do_not_learn",
  updatedAt: "2026-08-23T16:00:00.000Z",
});
assert.ok(domainExclusionResult);
assert.equal(
  resolveSenderLearningEntry(
    "other@example.net",
    domainExclusionResult.nextSenderCategoryLearning,
  ),
  null,
  "a domain exclusion must block domain Learning",
);
assert.equal(
  resolveSenderLearningEntry(
    "vip@example.net",
    domainExclusionResult.nextSenderCategoryLearning,
  )?.matchType,
  "sender",
  "an exact positive sender rule must win over a domain exclusion",
);

const retrainedDomainResult = applyLearningDecision({
  senderCategoryLearning: domainExclusionResult.nextSenderCategoryLearning,
  ruleValue: "example.net",
  ruleType: "domain",
  category: "Promo",
  learnedLabel: "Promo",
  mailboxAction: "move",
  senderBehavior: "normal",
  sourcePrioritySelection: "Normal",
  learnedFromCountFloor: 3,
  updatedAt: "2026-08-24T10:00:00.000Z",
});
assert.ok(retrainedDomainResult);
assert.equal(
  resolveSenderLearningEntry(
    "other@example.net",
    retrainedDomainResult.nextSenderCategoryLearning,
  )?.entry.learnedCategory,
  "Promo",
  "explicit positive domain retraining must replace the exclusion",
);

assert.deepEqual(
  buildRecentLearningDecisions({
    ...staleSenderExclusionStore,
    "positive@example.org": userALearning["person@example.com"],
  }).map(({ key }) => key),
  ["positive@example.org", "domain:example.com"],
  "Recent Learning Decisions must hide exclusions and retain positive rules",
);

const directMessageStore = {
  main: {
    Inbox: [
      {
        id: "current-message",
        category: "Primary" as const,
        categorySource: "system" as const,
        categoryConfidence: "low" as const,
        suggestion: { type: "confirm_category" },
      },
      {
        id: "other-message",
        category: "Primary" as const,
        categorySource: "system" as const,
        categoryConfidence: "low" as const,
      },
    ],
  },
};
const directMessageDecisionStore = applyCurrentMessageCategoryDecision(
  directMessageStore,
  "main",
  "current-message",
  "Promo",
);
assert.deepEqual(directMessageDecisionStore.main.Inbox[0], {
  id: "current-message",
  category: "Promo",
  categorySource: "user",
  categoryConfidence: "high",
  suggestion: undefined,
});
assert.strictEqual(
  directMessageDecisionStore.main.Inbox[1],
  directMessageStore.main.Inbox[1],
  "a direct decision must update only the concrete reviewed message",
);

const exclusionRoundTripStorage = new MemoryStorage();
let exclusionRoundTripState = hydrateScopedSenderCategoryLearning(
  exclusionRoundTripStorage,
  workspaceAUserAKey,
);
exclusionRoundTripState = updateScopedSenderCategoryLearning(
  exclusionRoundTripState,
  workspaceAUserAKey,
  {
    ...staleSenderExclusionStore,
    "person@example.com": excludedSenderResult.nextEntry,
  },
);
assert.equal(
  persistScopedSenderCategoryLearning(
    exclusionRoundTripStorage,
    exclusionRoundTripState,
    workspaceAUserAKey,
  ),
  true,
);
const hydratedExclusionStore = hydrateScopedSenderCategoryLearning(
  exclusionRoundTripStorage,
  workspaceAUserAKey,
).store;
assert.equal(
  resolveSenderLearningEntry("person@example.com", hydratedExclusionStore),
  null,
  "a sender exclusion must still block domain fallback after scoped v2 hydration",
);
assert.equal(
  hydrateScopedSenderCategoryLearning(
    exclusionRoundTripStorage,
    workspaceAUserBKey,
  ).store["person@example.com"],
  undefined,
  "an exclusion must remain isolated by workspace and user",
);

for (const invalidRawValue of [
  "not-json",
  "[]",
  JSON.stringify({
    "person@example.com": {
      learnedCategory: "Unknown",
      learnedFromCount: 1,
    },
  }),
  JSON.stringify({
    "person@example.com": {
      learnedCategory: "Primary",
      learnedFromCount: -1,
    },
  }),
  JSON.stringify({
    "person@example.com": {
      learnedCategory: "Primary",
      learnedFromCount: 1.5,
    },
  }),
  JSON.stringify({
    "person@example.com": {
      learnedCategory: "Primary",
      learnedFromCount: 1,
      senderBehavior: "invalid",
    },
  }),
  JSON.stringify({
    "person@example.com": {
      learnedCategory: "Primary",
      learnedFromCount: 1,
      sourcePrioritySelection: "Priority",
    },
  }),
  JSON.stringify({
    "domain:": {
      learnedCategory: "Primary",
      learnedFromCount: 1,
    },
  }),
  JSON.stringify({
    "Person@Example.com": {
      learnedCategory: "Primary",
      learnedFromCount: 1,
    },
  }),
  '{"__proto__":{"learnedCategory":"Primary","learnedFromCount":1}}',
]) {
  assert.deepEqual(
    hydrateScopedSenderCategoryLearning(
      new MemoryStorage([[workspaceAUserAKey, invalidRawValue]]),
      workspaceAUserAKey,
    ).store,
    {},
    "obviously malformed v2 payloads must fail closed",
  );
}
