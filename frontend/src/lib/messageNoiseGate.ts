export const MESSAGE_NOISE_DISPOSITIONS = [
  "none",
  "bulk_marketing",
  "unsolicited_low_value",
  "strong_spam",
] as const;

export type MessageNoiseDisposition = (typeof MESSAGE_NOISE_DISPOSITIONS)[number];

export const MESSAGE_NOISE_CONFIDENCES = ["low", "medium", "high"] as const;

export type MessageNoiseConfidence = (typeof MESSAGE_NOISE_CONFIDENCES)[number];

export const MESSAGE_NOISE_REASONS = [
  "provider_spam_evidence",
  "authentication_failure_evidence",
  "phishing_credential_request",
  "unsolicited_financial_solicitation",
  "unsolicited_investment_solicitation",
  "cold_sales_outreach",
  "cold_recruitment_outreach",
  "cold_call_to_action",
  "bulk_mail_evidence",
  "mailbox_relevance_mismatch",
  "no_conversation_evidence",
  "automated_sender_evidence",
] as const;

export type MessageNoiseReason = (typeof MESSAGE_NOISE_REASONS)[number];

export type MessageNoiseAssessment = {
  noiseDisposition?: MessageNoiseDisposition;
  noiseConfidence?: MessageNoiseConfidence;
  noiseReasons?: MessageNoiseReason[];
};

export type NormalizedMessageNoiseAssessment = {
  noiseDisposition: MessageNoiseDisposition;
  noiseConfidence: MessageNoiseConfidence;
  noiseReasons: MessageNoiseReason[];
};

export type MessageNoisePolicy = {
  assessmentPresent: boolean;
  disposition: MessageNoiseDisposition | null;
  confidence: MessageNoiseConfidence | null;
  blocksAutoPriority: boolean;
  allowsReplyRecommendation: boolean;
  allowsPositiveActionability: boolean;
  allowsCategoryLearning: boolean;
  visibleClassificationOverride: "Spam" | "Other" | null;
};

const messageNoiseDispositionSet = new Set<string>(MESSAGE_NOISE_DISPOSITIONS);
const messageNoiseConfidenceSet = new Set<string>(MESSAGE_NOISE_CONFIDENCES);
const messageNoiseReasonSet = new Set<string>(MESSAGE_NOISE_REASONS);

export function isMessageNoiseDisposition(
  value: unknown,
): value is MessageNoiseDisposition {
  return typeof value === "string" && messageNoiseDispositionSet.has(value);
}

export function isMessageNoiseConfidence(
  value: unknown,
): value is MessageNoiseConfidence {
  return typeof value === "string" && messageNoiseConfidenceSet.has(value);
}

export function isMessageNoiseReason(value: unknown): value is MessageNoiseReason {
  return typeof value === "string" && messageNoiseReasonSet.has(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isMessageNoiseReasonList(value: unknown): value is MessageNoiseReason[] {
  if (!Array.isArray(value) || value.length > MESSAGE_NOISE_REASONS.length) {
    return false;
  }

  const seenReasons = new Set<MessageNoiseReason>();

  for (const reason of value) {
    if (!isMessageNoiseReason(reason) || seenReasons.has(reason)) {
      return false;
    }

    seenReasons.add(reason);
  }

  return true;
}

export function resolveMessageNoiseAssessment(
  value: unknown,
): NormalizedMessageNoiseAssessment | undefined {
  try {
    if (!isRecord(value)) {
      return undefined;
    }

    const noiseDisposition = value.noiseDisposition;
    const noiseConfidence = value.noiseConfidence;
    const noiseReasons = value.noiseReasons;

    if (
      !isMessageNoiseDisposition(noiseDisposition) ||
      !isMessageNoiseConfidence(noiseConfidence) ||
      !isMessageNoiseReasonList(noiseReasons)
    ) {
      return undefined;
    }

    return {
      noiseDisposition,
      noiseConfidence,
      noiseReasons: [...noiseReasons],
    };
  } catch {
    return undefined;
  }
}

const neutralMessageNoisePolicy: MessageNoisePolicy = {
  assessmentPresent: false,
  disposition: null,
  confidence: null,
  blocksAutoPriority: false,
  allowsReplyRecommendation: true,
  allowsPositiveActionability: true,
  allowsCategoryLearning: true,
  visibleClassificationOverride: null,
};

export function resolveMessageNoisePolicy(value: unknown): MessageNoisePolicy {
  const assessment = resolveMessageNoiseAssessment(value);

  if (!assessment) {
    return { ...neutralMessageNoisePolicy };
  }

  if (assessment.noiseDisposition === "strong_spam") {
    return {
      assessmentPresent: true,
      disposition: assessment.noiseDisposition,
      confidence: assessment.noiseConfidence,
      blocksAutoPriority: true,
      allowsReplyRecommendation: false,
      allowsPositiveActionability: false,
      allowsCategoryLearning: false,
      visibleClassificationOverride: "Spam",
    };
  }

  if (assessment.noiseDisposition === "unsolicited_low_value") {
    return {
      assessmentPresent: true,
      disposition: assessment.noiseDisposition,
      confidence: assessment.noiseConfidence,
      blocksAutoPriority: true,
      allowsReplyRecommendation: false,
      allowsPositiveActionability: false,
      allowsCategoryLearning: false,
      visibleClassificationOverride: "Other",
    };
  }

  if (assessment.noiseDisposition === "bulk_marketing") {
    return {
      assessmentPresent: true,
      disposition: assessment.noiseDisposition,
      confidence: assessment.noiseConfidence,
      blocksAutoPriority: false,
      allowsReplyRecommendation: false,
      allowsPositiveActionability: false,
      allowsCategoryLearning: assessment.noiseConfidence === "low",
      visibleClassificationOverride: null,
    };
  }

  return {
    ...neutralMessageNoisePolicy,
    assessmentPresent: true,
    disposition: assessment.noiseDisposition,
    confidence: assessment.noiseConfidence,
  };
}
