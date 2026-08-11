import assert from "node:assert/strict";
import {
  buildForYouLearningPools,
  isRefineCuevionEligible,
  isReviewUncertainEligible,
  type ForYouDerivationMessage,
  type ForYouMailboxStore,
} from "./forYouEngine";
import type { NormalizedMessageNoiseAssessment } from "./messageNoiseGate";

function createMessage(
  id: string,
  overrides: Partial<ForYouDerivationMessage> = {},
): ForYouDerivationMessage {
  return {
    id,
    sender: "Person",
    from: `${id}@example.com`,
    subject: `Subject ${id}`,
    createdAt: "2026-08-11T10:00:00.000Z",
    category: "Primary",
    categorySource: "system",
    categoryConfidence: "low",
    priorityScore: "medium",
    unread: true,
    snippet: `Preview ${id}`,
    body: [`Body ${id}`],
    attachments: [],
    suggestion: {
      type: "confirm_category",
      proposedCategory: "Primary",
    },
    ...overrides,
  };
}

function withNoise(
  message: ForYouDerivationMessage,
  assessment: NormalizedMessageNoiseAssessment,
): ForYouDerivationMessage {
  return {
    ...message,
    ...assessment,
  };
}

function buildPools(messages: ForYouDerivationMessage[], isAIEnabled = true) {
  const mailboxStore: ForYouMailboxStore<ForYouDerivationMessage> = {
    main: {
      Inbox: messages,
    },
  };

  return buildForYouLearningPools(
    isAIEnabled,
    mailboxStore,
    (message) => new Date(message.createdAt ?? 0).getTime(),
    () => "Primary",
  );
}

const legacyMessage = createMessage("legacy", {
  from: "person@example.com",
});
const noneMessage = withNoise(createMessage("none"), {
  noiseDisposition: "none",
  noiseConfidence: "high",
  noiseReasons: [],
});
const strongSpamMessage = withNoise(createMessage("strong", {
  from: "person@example.com",
}), {
  noiseDisposition: "strong_spam",
  noiseConfidence: "high",
  noiseReasons: ["provider_spam_evidence"],
});
const unsolicitedMessage = withNoise(createMessage("unsolicited"), {
  noiseDisposition: "unsolicited_low_value",
  noiseConfidence: "medium",
  noiseReasons: ["cold_sales_outreach"],
});
const lowConfidenceBulkMessage = withNoise(createMessage("bulk-low"), {
  noiseDisposition: "bulk_marketing",
  noiseConfidence: "low",
  noiseReasons: ["bulk_mail_evidence"],
});
const mediumConfidenceBulkMessage = withNoise(createMessage("bulk-medium"), {
  noiseDisposition: "bulk_marketing",
  noiseConfidence: "medium",
  noiseReasons: ["bulk_mail_evidence"],
});
const highConfidenceBulkMessage = withNoise(createMessage("bulk-high"), {
  noiseDisposition: "bulk_marketing",
  noiseConfidence: "high",
  noiseReasons: ["bulk_mail_evidence"],
});

for (const eligibleMessage of [legacyMessage, noneMessage, lowConfidenceBulkMessage]) {
  assert.equal(isRefineCuevionEligible(eligibleMessage), true);
  assert.equal(isReviewUncertainEligible(eligibleMessage), true);
}

for (const blockedMessage of [
  strongSpamMessage,
  unsolicitedMessage,
  mediumConfidenceBulkMessage,
  highConfidenceBulkMessage,
]) {
  assert.equal(isRefineCuevionEligible(blockedMessage), false);
  assert.equal(isReviewUncertainEligible(blockedMessage), false);
}

const partialRuntimeAssessment = createMessage("partial", {
  noiseDisposition: "strong_spam",
});
assert.equal(isRefineCuevionEligible(partialRuntimeAssessment), true);
assert.equal(isReviewUncertainEligible(partialRuntimeAssessment), true);

const pools = buildPools([
  legacyMessage,
  noneMessage,
  strongSpamMessage,
  unsolicitedMessage,
  lowConfidenceBulkMessage,
  mediumConfidenceBulkMessage,
  highConfidenceBulkMessage,
]);
const learningSuggestionKeys = pools.learningSuggestionPool.map(({ key }) => key);
const uncertainEmailKeys = pools.uncertainEmailPool.map(({ key }) => key);

assert.deepEqual(new Set(learningSuggestionKeys), new Set(["legacy", "none", "bulk-low"]));
assert.deepEqual(new Set(uncertainEmailKeys), new Set(["legacy", "none", "bulk-low"]));
assert.equal(
  pools.learningSuggestionPool.find(({ key }) => key === "legacy")?.senderFrequency,
  1,
);

assert.deepEqual(buildPools([legacyMessage], false), {
  learningSuggestionPool: [],
  uncertainEmailPool: [],
});

const datedEligibleMessages = Array.from({ length: 6 }, (_, index) =>
  createMessage(`eligible-${index}`, {
    createdAt: `2026-08-11T10:0${index}:00.000Z`,
  }),
);
const newerNoiseMessages = [
  withNoise(
    createMessage("newer-noise-1", {
      createdAt: "2026-08-11T10:20:00.000Z",
    }),
    {
      noiseDisposition: "strong_spam",
      noiseConfidence: "high",
      noiseReasons: ["provider_spam_evidence"],
    },
  ),
  withNoise(
    createMessage("newer-noise-2", {
      createdAt: "2026-08-11T10:21:00.000Z",
    }),
    {
      noiseDisposition: "bulk_marketing",
      noiseConfidence: "high",
      noiseReasons: ["bulk_mail_evidence"],
    },
  ),
];
const topFivePools = buildPools([...newerNoiseMessages, ...datedEligibleMessages]);

assert.equal(topFivePools.uncertainEmailPool.length, 5);
assert.deepEqual(
  topFivePools.uncertainEmailPool.map(({ key }) => key),
  ["eligible-5", "eligible-4", "eligible-3", "eligible-2", "eligible-1"],
);
