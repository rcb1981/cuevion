import assert from "node:assert/strict";
import {
  resolveMailMessageBehaviorSuggestion,
  resolveMailMessageSuggestion,
  resolveMessageSuggestionBanner,
  resolveSuggestedMessageAction,
  shouldSuppressReplySuggestion,
} from "./suggestionEngine";
import type { SenderCategoryLearningStore } from "./learningEngine";
import type { NormalizedMessageNoiseAssessment } from "./messageNoiseGate";

const baseMessage = {
  from: "person@example.com",
  sender: "Person",
  subject: "Next steps",
  snippet: "Can you send the final artwork?",
  body: ["Can you send the final artwork?"],
  attachments: [],
};

const systemLowCategorization = {
  category: "Primary" as const,
  categorySource: "system" as const,
  categoryConfidence: "low" as const,
};
const userCategorization = {
  category: "Primary" as const,
  categorySource: "user" as const,
  categoryConfidence: "medium" as const,
};

function withNoise(
  assessment: NormalizedMessageNoiseAssessment,
): typeof baseMessage & NormalizedMessageNoiseAssessment {
  return {
    ...baseMessage,
    ...assessment,
  };
}

const noneMessage = withNoise({
  noiseDisposition: "none",
  noiseConfidence: "high",
  noiseReasons: [],
});
const strongSpamMessage = withNoise({
  noiseDisposition: "strong_spam",
  noiseConfidence: "high",
  noiseReasons: ["provider_spam_evidence"],
});
const unsolicitedMessage = withNoise({
  noiseDisposition: "unsolicited_low_value",
  noiseConfidence: "medium",
  noiseReasons: ["cold_sales_outreach"],
});
const lowConfidenceBulkMessage = withNoise({
  noiseDisposition: "bulk_marketing",
  noiseConfidence: "low",
  noiseReasons: ["bulk_mail_evidence"],
});
const mediumConfidenceBulkMessage = withNoise({
  noiseDisposition: "bulk_marketing",
  noiseConfidence: "medium",
  noiseReasons: ["bulk_mail_evidence"],
});

assert.equal(
  resolveSuggestedMessageAction(baseMessage, "Primary").type,
  "reply",
);
assert.equal(
  resolveSuggestedMessageAction(noneMessage, "Primary").type,
  "reply",
);
assert.equal(shouldSuppressReplySuggestion(noneMessage, "Primary"), false);
assert.equal(resolveMessageSuggestionBanner({ ...noneMessage, category: "Primary" })?.type, "reply");

const partialRuntimeAssessment = {
  ...baseMessage,
  noiseDisposition: "strong_spam" as const,
};
assert.equal(
  resolveSuggestedMessageAction(partialRuntimeAssessment, "Primary").type,
  "reply",
);
assert.deepEqual(
  resolveMailMessageSuggestion(
    partialRuntimeAssessment,
    systemLowCategorization,
    true,
  ),
  {
    type: "confirm_category",
    proposedCategory: "Primary",
  },
);

for (const blockedMessage of [strongSpamMessage, unsolicitedMessage]) {
  assert.equal(
    resolveSuggestedMessageAction(blockedMessage, "Primary").type,
    "none",
  );
  assert.equal(shouldSuppressReplySuggestion(blockedMessage, "Primary"), true);
  assert.equal(
    resolveMessageSuggestionBanner({ ...blockedMessage, category: "Primary" }),
    undefined,
  );
  assert.equal(
    resolveMailMessageSuggestion(
      {
        ...blockedMessage,
        suggestion: {
          type: "confirm_category" as const,
          proposedCategory: "Primary" as const,
        },
      },
      systemLowCategorization,
      true,
    ),
    undefined,
  );
}

for (const bulkMessage of [
  lowConfidenceBulkMessage,
  mediumConfidenceBulkMessage,
]) {
  assert.equal(resolveSuggestedMessageAction(bulkMessage, "Primary").type, "none");
  assert.equal(shouldSuppressReplySuggestion(bulkMessage, "Primary"), true);
  assert.equal(
    resolveMessageSuggestionBanner({ ...bulkMessage, category: "Primary" }),
    undefined,
  );
}

assert.deepEqual(
  resolveMailMessageSuggestion(
    lowConfidenceBulkMessage,
    systemLowCategorization,
    true,
  ),
  {
    type: "confirm_category",
    proposedCategory: "Primary",
  },
);
assert.equal(
  resolveMailMessageSuggestion(
    mediumConfidenceBulkMessage,
    systemLowCategorization,
    true,
  ),
  undefined,
);
assert.deepEqual(
  resolveMailMessageSuggestion(noneMessage, systemLowCategorization, true),
  {
    type: "confirm_category",
    proposedCategory: "Primary",
  },
);

const senderCategoryLearning: SenderCategoryLearningStore = {
  "person@example.com": {
    learnedCategory: "Primary",
    learnedFromCount: 2,
    autoCategoryEnabled: false,
    mailboxAction: "keep",
    sourcePrioritySelection: "Important",
  },
};

assert.deepEqual(
  resolveMailMessageBehaviorSuggestion(
    noneMessage,
    userCategorization,
    senderCategoryLearning,
    true,
  ),
  {
    type: "auto_category",
    sender: "person@example.com",
    category: "Primary",
  },
);
assert.deepEqual(
  resolveMailMessageBehaviorSuggestion(
    lowConfidenceBulkMessage,
    userCategorization,
    senderCategoryLearning,
    true,
  ),
  {
    type: "auto_category",
    sender: "person@example.com",
    category: "Primary",
  },
);
assert.equal(
  resolveMailMessageBehaviorSuggestion(
    mediumConfidenceBulkMessage,
    userCategorization,
    senderCategoryLearning,
    true,
  ),
  undefined,
);
assert.equal(
  resolveMailMessageBehaviorSuggestion(
    strongSpamMessage,
    userCategorization,
    senderCategoryLearning,
    true,
  ),
  undefined,
);

assert.equal(
  resolveSuggestedMessageAction(
    strongSpamMessage,
    "Primary",
    senderCategoryLearning,
  ).type,
  "none",
);
