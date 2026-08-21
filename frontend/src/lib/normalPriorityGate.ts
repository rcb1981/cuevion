import type {
  PriorityLevel,
  PriorityManualOverride,
  PrioritySource,
  PrioritySourceResult,
} from "./prioritySource";
import type { ReturnedReplyEvidence } from "./returnedReplyEvidence";
import type { MessageNoiseDisposition } from "./messageNoiseGate";

export type NormalPriorityLegacyState = {
  hasVisiblePriorityBadge?: boolean | null;
  signal?: string | null;
  ui_signal?: string | null;
  final_visibility?: string | null;
  priorityScore?: string | null;
  action?: string | null;
};

export type NormalPriorityGatePrioritySource = Partial<
  Pick<PrioritySourceResult, "level" | "source" | "confidence">
>;

export type NormalPriorityGateReturnedReplyEvidence = Partial<
  Pick<ReturnedReplyEvidence, "hasEvidence" | "confidence">
>;

export type NormalPriorityGateInput = {
  currentLegacyPriority?: NormalPriorityLegacyState | null;
  prioritySource?: NormalPriorityGatePrioritySource | null;
  returnedReplyEvidence?: NormalPriorityGateReturnedReplyEvidence | null;
  internalClassification?: string | null;
  hasCollaborationContext?: boolean | null;
  hasAssignedReviewContext?: boolean | null;
  hasReplyProtection?: boolean | null;
  isFromOwnAddress?: boolean | null;
  manualOverride?: PriorityManualOverride | null;
  isStrongSystemRuleConcreteActionable?: boolean | null;
  noiseDisposition?: MessageNoiseDisposition | null;
};

function isPriorityLevel(value: unknown): value is PriorityLevel {
  return value === "priority" || value === "normal" || value === "low";
}

function isPrioritySource(value: unknown): value is PrioritySource {
  return (
    value === "manual" ||
    value === "learning" ||
    value === "waiting_on_other" ||
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

function hasPriorityLevel(prioritySource: NormalPriorityGatePrioritySource | null) {
  return isPriorityLevel(prioritySource?.level) && prioritySource.level === "priority";
}

function hasHighConfidenceReturnedReplyEvidence(
  evidence: NormalPriorityGateReturnedReplyEvidence | null,
) {
  return evidence?.hasEvidence === true && evidence.confidence === "high";
}

export function shouldAllowNormalPriority(input: NormalPriorityGateInput) {
  const prioritySource = input.prioritySource ?? null;
  const source = isPrioritySource(prioritySource?.source)
    ? prioritySource.source
    : "none";
  const hasHighConfidenceReturnedReply = hasHighConfidenceReturnedReplyEvidence(
    input.returnedReplyEvidence ?? null,
  );

  if (
    input.noiseDisposition === "strong_spam" ||
    input.noiseDisposition === "unsolicited_low_value"
  ) {
    return false;
  }

  if (input.manualOverride === "priority") {
    return true;
  }

  if (input.manualOverride === "removed") {
    return false;
  }

  switch (source) {
    case "manual":
    case "learning":
      return hasPriorityLevel(prioritySource);

    case "waiting_on_other":
      return hasPriorityLevel(prioritySource);

    case "returned_reply":
      return input.isFromOwnAddress === true ? false : hasHighConfidenceReturnedReply;

    case "collaboration":
      return true;

    case "assigned_review":
      return true;

    case "reply_protection":
      return input.isFromOwnAddress === true ? false : hasHighConfidenceReturnedReply;

    case "strong_system_rule":
      return input.isStrongSystemRuleConcreteActionable === true;

    case "backend_visibility":
    case "ai_heuristic":
    case "focus_preference":
    case "none":
      break;
  }

  if (input.hasCollaborationContext === true) {
    return true;
  }

  if (input.hasAssignedReviewContext === true) {
    return true;
  }

  if (input.hasReplyProtection === true) {
    return input.isFromOwnAddress === true ? false : hasHighConfidenceReturnedReply;
  }

  return false;
}
