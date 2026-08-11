import assert from "node:assert/strict";
import {
  MESSAGE_NOISE_CONFIDENCES,
  MESSAGE_NOISE_DISPOSITIONS,
  MESSAGE_NOISE_REASONS,
  isMessageNoiseConfidence,
  isMessageNoiseDisposition,
  isMessageNoiseReason,
  resolveMessageNoiseAssessment,
  resolveMessageNoisePolicy,
  type NormalizedMessageNoiseAssessment,
} from "./messageNoiseGate";

const validAssessment: NormalizedMessageNoiseAssessment = {
  noiseDisposition: "bulk_marketing",
  noiseConfidence: "medium",
  noiseReasons: ["bulk_mail_evidence", "automated_sender_evidence"],
};

assert.deepEqual(resolveMessageNoiseAssessment(validAssessment), validAssessment);
assert.notEqual(
  resolveMessageNoiseAssessment(validAssessment)?.noiseReasons,
  validAssessment.noiseReasons,
);
assert.deepEqual(
  resolveMessageNoiseAssessment({
    noiseDisposition: "none",
    noiseConfidence: "low",
    noiseReasons: [],
  }),
  {
    noiseDisposition: "none",
    noiseConfidence: "low",
    noiseReasons: [],
  },
);

for (const disposition of MESSAGE_NOISE_DISPOSITIONS) {
  assert.equal(isMessageNoiseDisposition(disposition), true);
}
for (const confidence of MESSAGE_NOISE_CONFIDENCES) {
  assert.equal(isMessageNoiseConfidence(confidence), true);
}
for (const reason of MESSAGE_NOISE_REASONS) {
  assert.equal(isMessageNoiseReason(reason), true);
}

assert.equal(isMessageNoiseDisposition("spam"), false);
assert.equal(isMessageNoiseConfidence("certain"), false);
assert.equal(isMessageNoiseReason("arbitrary_reason"), false);

const malformedAssessments: unknown[] = [
  undefined,
  null,
  [],
  {},
  { noiseDisposition: "strong_spam" },
  {
    noiseDisposition: "strong_spam",
    noiseConfidence: "high",
  },
  {
    noiseDisposition: "spam",
    noiseConfidence: "high",
    noiseReasons: ["provider_spam_evidence"],
  },
  {
    noiseDisposition: "strong_spam",
    noiseConfidence: "certain",
    noiseReasons: ["provider_spam_evidence"],
  },
  {
    noiseDisposition: "strong_spam",
    noiseConfidence: "high",
    noiseReasons: "provider_spam_evidence",
  },
  {
    noiseDisposition: "strong_spam",
    noiseConfidence: "high",
    noiseReasons: ["arbitrary_reason"],
  },
  {
    noiseDisposition: "strong_spam",
    noiseConfidence: "high",
    noiseReasons: new Array(1),
  },
  {
    noiseDisposition: "strong_spam",
    noiseConfidence: "high",
    noiseReasons: ["provider_spam_evidence", "provider_spam_evidence"],
  },
  {
    noiseDisposition: "strong_spam",
    noiseConfidence: "high",
    noiseReasons: [...MESSAGE_NOISE_REASONS, "provider_spam_evidence"],
  },
];

const missingAssessmentPolicy = {
  assessmentPresent: false,
  disposition: null,
  confidence: null,
  blocksAutoPriority: false,
  allowsReplyRecommendation: true,
  allowsPositiveActionability: true,
  allowsCategoryLearning: true,
  visibleClassificationOverride: null,
};

for (const malformedAssessment of malformedAssessments) {
  assert.equal(resolveMessageNoiseAssessment(malformedAssessment), undefined);
  assert.deepEqual(
    resolveMessageNoisePolicy(malformedAssessment),
    missingAssessmentPolicy,
  );
}

assert.deepEqual(
  resolveMessageNoisePolicy({
    noiseDisposition: "strong_spam",
    noiseConfidence: "high",
    noiseReasons: ["provider_spam_evidence"],
  }),
  {
    assessmentPresent: true,
    disposition: "strong_spam",
    confidence: "high",
    blocksAutoPriority: true,
    allowsReplyRecommendation: false,
    allowsPositiveActionability: false,
    allowsCategoryLearning: false,
    visibleClassificationOverride: "Spam",
  },
);

assert.deepEqual(
  resolveMessageNoisePolicy({
    noiseDisposition: "unsolicited_low_value",
    noiseConfidence: "low",
    noiseReasons: ["cold_sales_outreach"],
  }),
  {
    assessmentPresent: true,
    disposition: "unsolicited_low_value",
    confidence: "low",
    blocksAutoPriority: true,
    allowsReplyRecommendation: false,
    allowsPositiveActionability: false,
    allowsCategoryLearning: false,
    visibleClassificationOverride: "Other",
  },
);

assert.deepEqual(
  resolveMessageNoisePolicy({
    noiseDisposition: "bulk_marketing",
    noiseConfidence: "low",
    noiseReasons: ["bulk_mail_evidence"],
  }),
  {
    assessmentPresent: true,
    disposition: "bulk_marketing",
    confidence: "low",
    blocksAutoPriority: false,
    allowsReplyRecommendation: false,
    allowsPositiveActionability: false,
    allowsCategoryLearning: true,
    visibleClassificationOverride: null,
  },
);

for (const confidence of ["medium", "high"] as const) {
  assert.deepEqual(
    resolveMessageNoisePolicy({
      noiseDisposition: "bulk_marketing",
      noiseConfidence: confidence,
      noiseReasons: ["bulk_mail_evidence"],
    }),
    {
      assessmentPresent: true,
      disposition: "bulk_marketing",
      confidence,
      blocksAutoPriority: false,
      allowsReplyRecommendation: false,
      allowsPositiveActionability: false,
      allowsCategoryLearning: false,
      visibleClassificationOverride: null,
    },
  );
}

for (const confidence of MESSAGE_NOISE_CONFIDENCES) {
  assert.deepEqual(
    resolveMessageNoisePolicy({
      noiseDisposition: "none",
      noiseConfidence: confidence,
      noiseReasons: [],
    }),
    {
      assessmentPresent: true,
      disposition: "none",
      confidence,
      blocksAutoPriority: false,
      allowsReplyRecommendation: true,
      allowsPositiveActionability: true,
      allowsCategoryLearning: true,
      visibleClassificationOverride: null,
    },
  );
}
