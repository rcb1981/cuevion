export type PriorityLevel = "priority" | "normal" | "low";

export type PrioritySource =
  | "manual"
  | "learning"
  | "returned_reply"
  | "collaboration"
  | "assigned_review"
  | "strong_system_rule"
  | "ai_heuristic"
  | "focus_preference"
  | "backend_visibility"
  | "reply_protection"
  | "none";

export type PriorityConfidence = "high" | "medium" | "low";

export type PrioritySourceResult = {
  level: PriorityLevel;
  source: PrioritySource;
  reason: string;
  confidence: PriorityConfidence;
};

export type PriorityManualOverride = "priority" | "removed";

export type PriorityLearningSelection =
  | "Important"
  | "Priority"
  | "Normal"
  | "Show Less"
  | "Low"
  | "Spam";

export type PriorityFocusPreferenceVisibility =
  | "priority"
  | "normal"
  | "low"
  | "show_priority"
  | "show_normal"
  | "show_low";

export type PrioritySourceMessageLike = {
  signal?: string | null;
  ui_signal?: string | null;
  priorityScore?: "low" | "medium" | "high" | string | null;
  final_visibility?: string | null;
  action?: string | null;
  internalClassification?: string | null;
  isShared?: boolean | null;
  sharedContext?: unknown;
};

export type ResolvePrioritySourceInput = {
  message?: PrioritySourceMessageLike | null;
  manualOverride?: PriorityManualOverride | null;
  learnedPrioritySelection?: PriorityLearningSelection | string | null;
  hasCollaborationContext?: boolean | null;
  hasAssignedReviewContext?: boolean | null;
  hasReturnedReplyEvidence?: boolean | null;
  focusPreferenceVisibility?: PriorityFocusPreferenceVisibility | string | null;
};

const normalizeSignal = (value: unknown) =>
  typeof value === "string" ? value.trim().toLowerCase() : "";

function normalizePriorityLevel(value: unknown): PriorityLevel | null {
  const normalizedValue = normalizeSignal(value);

  switch (normalizedValue) {
    case "priority":
    case "important":
    case "high":
    case "show_priority":
      return "priority";
    case "low":
    case "show_low":
    case "show_less":
    case "show_in_quiet_view":
    case "spam":
      return "low";
    case "normal":
    case "medium":
    case "show_normal":
      return "normal";
    default:
      return null;
  }
}

function resolveCurrentPriorityLevel(
  message: PrioritySourceMessageLike | null | undefined,
  focusPreferenceVisibility?: string | null,
): PriorityLevel {
  const focusLevel = normalizePriorityLevel(focusPreferenceVisibility);
  if (focusLevel) {
    return focusLevel;
  }

  if (!message) {
    return "normal";
  }

  if (
    normalizeSignal(message.final_visibility) === "show_low" ||
    normalizeSignal(message.action) === "show_in_quiet_view"
  ) {
    return "low";
  }

  if (normalizeSignal(message.final_visibility) === "show_normal") {
    return "normal";
  }

  if (
    normalizeSignal(message.final_visibility) === "show_priority" ||
    normalizeSignal(message.action) === "show_in_priority" ||
    normalizeSignal(message.signal) === "priority" ||
    normalizeSignal(message.ui_signal) === "priority" ||
    normalizeSignal(message.priorityScore) === "high"
  ) {
    return "priority";
  }

  if (normalizeSignal(message.priorityScore) === "low") {
    return "low";
  }

  return "normal";
}

function isReplyProtectedMessage(message: PrioritySourceMessageLike | null | undefined) {
  return (
    normalizeSignal(message?.internalClassification) === "reply" ||
    normalizeSignal(message?.signal) === "follow-up" ||
    normalizeSignal(message?.ui_signal) === "reply"
  );
}

function hasCollaborationSignal(
  message: PrioritySourceMessageLike | null | undefined,
  explicitContext?: boolean | null,
) {
  return Boolean(explicitContext || message?.isShared || message?.sharedContext);
}

function hasBackendPriorityVisibility(message: PrioritySourceMessageLike | null | undefined) {
  return (
    normalizeSignal(message?.final_visibility) === "show_priority" ||
    normalizeSignal(message?.action) === "show_in_priority"
  );
}

function hasStrongSystemRuleSignal(message: PrioritySourceMessageLike | null | undefined) {
  return normalizeSignal(message?.internalClassification) === "high_priority_demo";
}

function hasAiHeuristicPrioritySignal(message: PrioritySourceMessageLike | null | undefined) {
  return (
    normalizeSignal(message?.signal) === "priority" ||
    normalizeSignal(message?.ui_signal) === "priority" ||
    normalizeSignal(message?.priorityScore) === "high"
  );
}

export function resolvePrioritySource(
  input: ResolvePrioritySourceInput,
): PrioritySourceResult {
  const message = input.message ?? null;
  const currentLevel = resolveCurrentPriorityLevel(
    message,
    input.focusPreferenceVisibility,
  );

  if (input.manualOverride === "priority") {
    return {
      level: "priority",
      source: "manual",
      reason: "User manually marked this message or thread as priority.",
      confidence: "high",
    };
  }

  if (input.manualOverride === "removed") {
    return {
      level: currentLevel === "low" ? "low" : "normal",
      source: "manual",
      reason: "User manually removed this message or thread from Priority.",
      confidence: "high",
    };
  }

  const learningLevel = normalizePriorityLevel(input.learnedPrioritySelection);
  if (learningLevel === "priority") {
    return {
      level: "priority",
      source: "learning",
      reason: "A learned sender or domain rule marks similar mail as important.",
      confidence: "high",
    };
  }

  if (input.hasReturnedReplyEvidence) {
    return {
      level: "priority",
      source: "returned_reply",
      reason: "The other party replied after a user response in this thread.",
      confidence: "medium",
    };
  }

  if (hasCollaborationSignal(message, input.hasCollaborationContext)) {
    return {
      level: currentLevel === "low" ? "normal" : currentLevel,
      source: "collaboration",
      reason: "This message has active collaboration or shared context.",
      confidence: "high",
    };
  }

  if (input.hasAssignedReviewContext) {
    return {
      level: currentLevel === "low" ? "normal" : currentLevel,
      source: "assigned_review",
      reason: "This message is connected to an assigned review or decision workflow.",
      confidence: "high",
    };
  }

  const focusLevel = normalizePriorityLevel(input.focusPreferenceVisibility);
  if (focusLevel) {
    return {
      level: focusLevel,
      source: "focus_preference",
      reason: "Workspace focus preferences currently set this content type to this level.",
      confidence: "high",
    };
  }

  if (hasBackendPriorityVisibility(message)) {
    return {
      level: "priority",
      source: "backend_visibility",
      reason: "Backend visibility fields mark this message as priority.",
      confidence: "high",
    };
  }

  if (isReplyProtectedMessage(message)) {
    return {
      level: currentLevel === "low" ? "normal" : currentLevel,
      source: "reply_protection",
      reason: "Reply-like messages are protected as conversation context.",
      confidence: "medium",
    };
  }

  if (hasStrongSystemRuleSignal(message)) {
    return {
      level: "priority",
      source: "strong_system_rule",
      reason: "A precise system classification marks this message as high-priority work.",
      confidence: "high",
    };
  }

  if (hasAiHeuristicPrioritySignal(message)) {
    return {
      level: "priority",
      source: "ai_heuristic",
      reason: "Current heuristic priority fields indicate this may need attention.",
      confidence: "low",
    };
  }

  if (learningLevel === "low") {
    return {
      level: "low",
      source: "learning",
      reason: "A learned sender or domain rule marks similar mail as low priority.",
      confidence: "high",
    };
  }

  return {
    level: currentLevel,
    source: "none",
    reason: "No explicit priority source was found.",
    confidence: "low",
  };
}
