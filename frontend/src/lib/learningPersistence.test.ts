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
import { resolveSuggestedMessageAction } from "./suggestionEngine";

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
    senderBehavior: "do_not_learn",
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
  "persistence must not reinterpret do_not_learn, Important, counts, or metadata",
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
