import type {
  NormalPriorityGateInput,
  NormalPriorityGatePrioritySource,
  NormalPriorityGateReturnedReplyEvidence,
  NormalPriorityLegacyState,
} from "./normalPriorityGate";
import type {
  PriorityConfidence,
  PriorityManualOverride,
  PrioritySource,
} from "./prioritySource";

export type NormalPriorityGateAdapterMessageLike = NormalPriorityLegacyState & {
  subject?: string | null;
  threadId?: string | null;
  internalClassification?: string | null;
  category?: string | null;
  isShared?: boolean | null;
  sharedContext?: unknown;
  hasCollaborationContext?: boolean | null;
  hasAssignedReviewContext?: boolean | null;
  hasReviewContext?: boolean | null;
  assignedReviewId?: string | null;
  assignedReviewer?: string | null;
  reviewStatus?: string | null;
  hasReplyProtection?: boolean | null;
  isReplyProtected?: boolean | null;
};

export type NormalPriorityGateRuntimeSignalLike = {
  prioritySource?: NormalPriorityGatePrioritySource | null;
  returnedReplyEvidence?: NormalPriorityGateReturnedReplyEvidence | null;
};

export type BuildNormalPriorityGateInputOptions = {
  message?: NormalPriorityGateAdapterMessageLike | null;
  runtimeSignal?: NormalPriorityGateRuntimeSignalLike | null;
  prioritySource?: NormalPriorityGatePrioritySource | null;
  returnedReplyEvidence?: NormalPriorityGateReturnedReplyEvidence | null;
  currentLegacyPriority?: NormalPriorityLegacyState | null;
  manualOverride?: PriorityManualOverride | null;
  hasCollaborationContext?: boolean | null;
  hasAssignedReviewContext?: boolean | null;
  hasReplyProtection?: boolean | null;
  isStrongSystemRuleConcreteActionable?: boolean | null;
};

function normalizeSignal(value: unknown) {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}

function isPrioritySource(value: unknown): value is PrioritySource {
  return (
    value === "manual" ||
    value === "learning" ||
    value === "returned_reply" ||
    value === "collaboration" ||
    value === "assigned_review" ||
    value === "strong_system_rule" ||
    value === "ai_heuristic" ||
    value === "focus_preference" ||
    value === "backend_visibility" ||
    value === "reply_protection" ||
    value === "none"
  );
}

function isPriorityConfidence(value: unknown): value is PriorityConfidence {
  return value === "high" || value === "medium" || value === "low";
}

function toPriorityConfidence(value: unknown): PriorityConfidence {
  return isPriorityConfidence(value) ? value : "low";
}

function prioritySource(
  source: PrioritySource,
  confidence: PriorityConfidence = "high",
): NormalPriorityGatePrioritySource {
  return {
    level: source === "none" ? "normal" : "priority",
    source,
    confidence,
  };
}

function hasValue(value: unknown) {
  return typeof value === "string" ? value.trim().length > 0 : Boolean(value);
}

function hasCollaborationContext(
  message: NormalPriorityGateAdapterMessageLike | null,
  explicitContext: boolean | null | undefined,
) {
  return Boolean(
    explicitContext === true ||
      message?.hasCollaborationContext === true ||
      message?.isShared === true ||
      message?.sharedContext,
  );
}

function hasAssignedReviewContext(
  message: NormalPriorityGateAdapterMessageLike | null,
  explicitContext: boolean | null | undefined,
) {
  return Boolean(
    explicitContext === true ||
      message?.hasAssignedReviewContext === true ||
      message?.hasReviewContext === true ||
      hasValue(message?.assignedReviewId) ||
      hasValue(message?.assignedReviewer) ||
      normalizeSignal(message?.reviewStatus) === "assigned",
  );
}

function hasExplicitReplyProtectionFlag(
  message: NormalPriorityGateAdapterMessageLike | null,
  explicitContext: boolean | null | undefined,
) {
  return Boolean(
    explicitContext === true ||
      message?.hasReplyProtection === true ||
      message?.isReplyProtected === true,
  );
}

function hasReplyProtectionContext(
  message: NormalPriorityGateAdapterMessageLike | null,
  explicitContext: boolean | null | undefined,
) {
  return Boolean(
    hasExplicitReplyProtectionFlag(message, explicitContext) ||
      normalizeSignal(message?.internalClassification) === "reply",
  );
}

function hasBackendPriorityVisibility(
  legacyState: NormalPriorityLegacyState,
) {
  return (
    normalizeSignal(legacyState.final_visibility) === "show_priority" ||
    normalizeSignal(legacyState.action) === "show_in_priority"
  );
}

function hasAiHeuristicPrioritySignal(
  legacyState: NormalPriorityLegacyState,
) {
  return (
    normalizeSignal(legacyState.signal) === "priority" ||
    normalizeSignal(legacyState.ui_signal) === "priority" ||
    normalizeSignal(legacyState.priorityScore) === "high" ||
    legacyState.hasVisiblePriorityBadge === true
  );
}

function buildLegacyState(
  message: NormalPriorityGateAdapterMessageLike | null,
  currentLegacyPriority: NormalPriorityLegacyState | null,
): NormalPriorityLegacyState {
  return {
    hasVisiblePriorityBadge:
      currentLegacyPriority?.hasVisiblePriorityBadge ??
      message?.hasVisiblePriorityBadge ??
      null,
    signal: currentLegacyPriority?.signal ?? message?.signal ?? null,
    ui_signal: currentLegacyPriority?.ui_signal ?? message?.ui_signal ?? null,
    final_visibility:
      currentLegacyPriority?.final_visibility ?? message?.final_visibility ?? null,
    priorityScore:
      currentLegacyPriority?.priorityScore ?? message?.priorityScore ?? null,
    action: currentLegacyPriority?.action ?? message?.action ?? null,
  };
}

function resolvePrioritySourceForGate(
  options: BuildNormalPriorityGateInputOptions,
  legacyState: NormalPriorityLegacyState,
): NormalPriorityGatePrioritySource {
  const message = options.message ?? null;
  const explicitPrioritySource =
    options.prioritySource ?? options.runtimeSignal?.prioritySource ?? null;
  const returnedReplyEvidence =
    options.returnedReplyEvidence ??
    options.runtimeSignal?.returnedReplyEvidence ??
    null;

  if (explicitPrioritySource && isPrioritySource(explicitPrioritySource.source)) {
    return { ...explicitPrioritySource };
  }

  if (options.manualOverride === "priority") {
    return prioritySource("manual");
  }

  if (options.manualOverride === "removed") {
    return {
      level: "normal",
      source: "manual",
      confidence: "high",
    };
  }

  if (returnedReplyEvidence?.hasEvidence === true) {
    return prioritySource(
      "returned_reply",
      toPriorityConfidence(returnedReplyEvidence.confidence),
    );
  }

  if (hasCollaborationContext(message, options.hasCollaborationContext)) {
    return prioritySource("collaboration");
  }

  if (hasAssignedReviewContext(message, options.hasAssignedReviewContext)) {
    return prioritySource("assigned_review");
  }

  if (hasReplyProtectionContext(message, options.hasReplyProtection)) {
    return prioritySource("reply_protection", "medium");
  }

  if (hasBackendPriorityVisibility(legacyState)) {
    return prioritySource("backend_visibility");
  }

  if (normalizeSignal(message?.internalClassification) === "high_priority_demo") {
    return prioritySource("strong_system_rule");
  }

  if (hasAiHeuristicPrioritySignal(legacyState)) {
    return prioritySource("ai_heuristic", "low");
  }

  return prioritySource("none", "low");
}

export function buildNormalPriorityGateInput(
  options: BuildNormalPriorityGateInputOptions,
): NormalPriorityGateInput {
  const message = options.message ?? null;
  const returnedReplyEvidence =
    options.returnedReplyEvidence ??
    options.runtimeSignal?.returnedReplyEvidence ??
    null;
  const currentLegacyPriority = buildLegacyState(
    message,
    options.currentLegacyPriority ?? null,
  );

  return {
    currentLegacyPriority,
    prioritySource: resolvePrioritySourceForGate(options, currentLegacyPriority),
    returnedReplyEvidence: returnedReplyEvidence ? { ...returnedReplyEvidence } : null,
    internalClassification: message?.internalClassification ?? null,
    hasCollaborationContext: hasCollaborationContext(
      message,
      options.hasCollaborationContext,
    ),
    hasAssignedReviewContext: hasAssignedReviewContext(
      message,
      options.hasAssignedReviewContext,
    ),
    hasReplyProtection: hasExplicitReplyProtectionFlag(
      message,
      options.hasReplyProtection,
    ),
    manualOverride: options.manualOverride ?? null,
    isStrongSystemRuleConcreteActionable:
      options.isStrongSystemRuleConcreteActionable ?? false,
  };
}
