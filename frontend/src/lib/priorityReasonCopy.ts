import type {
  PriorityConfidence,
  PriorityLevel,
  PrioritySource,
  PrioritySourceResult,
} from "./prioritySource";

export type PriorityReasonCopy = {
  title: string;
  detail?: string;
  confidenceLabel?: PriorityConfidence;
  shouldShow: boolean;
};

export type PriorityReasonCopyInput = {
  prioritySource?: Partial<PrioritySourceResult> | null;
};

const hiddenPriorityReasonCopy: PriorityReasonCopy = {
  title: "No clear priority reason yet",
  shouldShow: false,
};

const priorityReasonCopyBySource: Record<
  PrioritySource,
  Omit<PriorityReasonCopy, "confidenceLabel" | "shouldShow">
> = {
  manual: {
    title: "Manually marked as priority",
  },
  learning: {
    title: "Marked important by your learning preferences",
  },
  returned_reply: {
    title: "They replied after your last reply",
    detail: "This looks like an active conversation.",
  },
  collaboration: {
    title: "Part of an active collaboration",
  },
  assigned_review: {
    title: "Needs review",
  },
  strong_system_rule: {
    title: "Important system message",
  },
  ai_heuristic: {
    title: "Suggested by Cuevion",
  },
  focus_preference: {
    title: "Matches your focus settings",
  },
  backend_visibility: {
    title: "Marked as priority by Cuevion",
  },
  reply_protection: {
    title: "Kept visible as an active conversation",
  },
  none: {
    title: "No clear priority reason yet",
  },
};

function isPriorityLevel(value: unknown): value is PriorityLevel {
  return value === "priority" || value === "normal" || value === "low";
}

function isPrioritySource(value: unknown): value is PrioritySource {
  return (
    typeof value === "string" &&
    Object.prototype.hasOwnProperty.call(priorityReasonCopyBySource, value)
  );
}

function isPriorityConfidence(value: unknown): value is PriorityConfidence {
  return value === "high" || value === "medium" || value === "low";
}

export function formatPriorityReasonCopy(
  input?: PriorityReasonCopyInput | null,
): PriorityReasonCopy {
  const prioritySource = input?.prioritySource;

  if (
    !prioritySource ||
    !isPrioritySource(prioritySource.source) ||
    !isPriorityLevel(prioritySource.level) ||
    prioritySource.source === "none" ||
    prioritySource.level !== "priority"
  ) {
    return { ...hiddenPriorityReasonCopy };
  }

  return {
    ...priorityReasonCopyBySource[prioritySource.source],
    confidenceLabel: isPriorityConfidence(prioritySource.confidence)
      ? prioritySource.confidence
      : undefined,
    shouldShow: true,
  };
}
