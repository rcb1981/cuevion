import type { InboxId } from "../types/onboarding";
import {
  formatLearningRuleAction,
  formatLearningRuleLabel,
  formatLearningRuleTimestamp,
  inferLearningDecisionPrioritySelection,
  inferLearningDecisionSourceContext,
  isLearningExclusionEntry,
  normalizeSenderLearningKey,
  type CuevionMessageCategory,
  type CuevionLearningLabel,
  type LearningDecisionPrioritySelection,
  type LearningDecisionSourceContext,
  type SenderLearningBehavior,
  type SenderCategoryLearningEntry,
  type SenderCategoryLearningStore,
} from "./learningEngine";
import {
  resolveMessageNoisePolicy,
  type MessageNoiseAssessment,
} from "./messageNoiseGate";

export type ForYouLearningSuggestion = {
  key: string;
  sender: string;
  senderAddress: string;
  subject: string;
  createdAt: string;
  timestamp?: string;
  uncertainty: number;
  senderFrequency: number;
  snippet: string[];
  fullBody: string[];
  attachments: ForYouLearningAttachment[];
  reason: string;
  visualLabel?: string;
  mailboxId: InboxId | null;
  category: CuevionMessageCategory;
  categoryConfidence: "low" | "medium" | "high";
  priorityScore: "low" | "medium" | "high";
  displayLabel: CuevionLearningLabel;
  displayPriority: CuevionLearningPriorityLevel;
};

export type ForYouLearningAttachment = {
  id?: string;
  name: string;
  mimeType?: string;
  size?: number;
};

export type ForYouUncertainEmail = {
  key: string;
  sender: string;
  senderAddress: string;
  mailboxId: InboxId | null;
  subject: string;
  preview: string[];
  reason: string;
  currentMailboxLabel: string;
  displayLabel: CuevionLearningLabel;
  displayPriority: CuevionLearningPriorityLevel;
};

export type ForYouRecentLearningDecision = {
  key: string;
  sender: string;
  action: string;
  timestamp: string;
  ruleType: "sender" | "domain";
  ruleValue: string;
  learnedCategory: CuevionMessageCategory;
  learnedLabel?: CuevionLearningLabel;
  mailboxAction: "keep" | "move";
  senderBehavior?: SenderLearningBehavior;
  sourceContext: LearningDecisionSourceContext | null;
  sourcePrioritySelection: LearningDecisionPrioritySelection | null;
  sourceMailboxId: InboxId | null;
  sourceCurrentMailboxId: InboxId | null;
  updatedAt?: string;
};

export type ForYouDerivationMessage = MessageNoiseAssessment & {
  id: string;
  sender: string;
  from: string;
  subject: string;
  createdAt?: string;
  timestamp?: string;
  time?: string;
  category: CuevionMessageCategory;
  signal?: string;
  ui_signal?: string;
  internalClassification?: string | null;
  final_visibility?: string;
  action?: string;
  categorySource: "system" | "user" | "learned";
  categoryConfidence: "low" | "medium" | "high";
  priorityScore: "low" | "medium" | "high";
  unread?: boolean;
  isShared?: boolean;
  snippet: string;
  body: string[];
  attachments?: Array<{
    id?: string;
    name?: string;
    mimeType?: string;
    size?: number;
  }>;
  suggestion?: {
    type: "confirm_category";
    proposedCategory: CuevionMessageCategory;
  };
};

export type CuevionLearningPriorityLevel = "Priority" | "Normal" | "Low";

export type ForYouMailboxStore<TMessage extends ForYouDerivationMessage> = Record<
  string,
  {
    Inbox: TMessage[];
  }
>;

export function formatForYouReason(
  displayLabel: CuevionLearningLabel,
  displayPriority: CuevionLearningPriorityLevel,
) {
  return `Cuevion labelled this as ${displayPriority} · ${displayLabel}, but is not fully sure yet.`;
}

function formatForYouCategoryLabel(category: CuevionMessageCategory): CuevionLearningLabel {
  if (category === "Promo") {
    return "Promo";
  }

  if (category === "Updates") {
    return "Update";
  }

  return "Other";
}

function normalizeLearningSignal(value?: string | null) {
  return value?.trim().toLowerCase().replace(/[\s-]+/g, "_") ?? "";
}

function resolveLabelFromClassification(
  value?: string | null,
): CuevionLearningLabel | null {
  switch (normalizeLearningSignal(value)) {
    case "demo":
    case "high_priority_demo":
    case "incomplete_demo":
      return "Demo";
    case "promo":
    case "promo_reminder":
      return "Promo";
    case "business":
    case "business_reminder":
      return "Business";
    case "finance":
    case "royalty_statement":
      return "Finance";
    case "workflow_update":
    case "distributor_update":
    case "info":
    case "update":
    case "updates":
      return "Update";
    case "reply":
      return "Reply";
    case "spam":
      return "Spam";
    case "primary":
    case "unknown":
      return "Other";
    default:
      return null;
  }
}

export function resolveForYouLearningDisplayLabel(
  message: Pick<
    ForYouDerivationMessage,
    "category" | "internalClassification" | "signal" | "ui_signal"
  >,
): CuevionLearningLabel {
  return (
    resolveLabelFromClassification(message.internalClassification) ??
    resolveLabelFromClassification(message.ui_signal) ??
    resolveLabelFromClassification(message.signal) ??
    formatForYouCategoryLabel(message.category)
  );
}

export function resolveForYouLearningDisplayPriority(
  message: Pick<
    ForYouDerivationMessage,
    "priorityScore" | "signal" | "ui_signal" | "final_visibility" | "action"
  >,
): CuevionLearningPriorityLevel {
  const explicitSignals = [
    message.signal,
    message.ui_signal,
    message.final_visibility,
    message.action,
  ].map(normalizeLearningSignal);

  if (
    explicitSignals.some((signal) =>
      ["priority", "high", "important", "show_priority"].includes(signal),
    ) ||
    message.priorityScore === "high"
  ) {
    return "Priority";
  }

  if (
    explicitSignals.some((signal) =>
      ["low", "show_low", "show_less", "show_in_quiet_view"].includes(signal),
    ) ||
    message.priorityScore === "low"
  ) {
    return "Low";
  }

  return "Normal";
}

export function formatLearningDecisionSummary(
  displayPriority: CuevionLearningPriorityLevel,
  displayLabel: CuevionLearningLabel,
) {
  return `${displayPriority} · ${displayLabel}`;
}

function formatForYouRecentLearningAction(
  entry: SenderCategoryLearningEntry,
  sourceContext: LearningDecisionSourceContext | null,
) {
  const label =
    entry.learnedLabel ??
    (entry.sourcePrioritySelection === "Spam"
      ? "Spam"
      : formatForYouCategoryLabel(entry.learnedCategory));
  const priority =
    entry.sourcePrioritySelection === "Important"
      ? "Priority"
      : entry.sourcePrioritySelection === "Show Less" || entry.sourcePrioritySelection === "Spam"
        ? "Low"
        : "Normal";
  const behavior =
    entry.senderBehavior === "always_prioritize"
      ? "Always prioritize"
      : entry.senderBehavior === "normal"
        ? "Show normally"
        : entry.senderBehavior === "show_less"
          ? "Show less"
          : entry.senderBehavior === "spam"
            ? "Mark sender as spam"
            : entry.senderBehavior === "do_not_learn"
              ? "Do not learn from sender"
              : null;

  return `${priority} · ${label}${behavior ? ` · ${behavior}` : ""}`;
}

export function isReviewUncertainEligible(message: ForYouDerivationMessage) {
  if (!resolveMessageNoisePolicy(message).allowsCategoryLearning) {
    return false;
  }

  return (
    message.categorySource === "system" &&
    message.suggestion?.type === "confirm_category"
  );
}

export function isRefineCuevionEligible(message: ForYouDerivationMessage) {
  if (!resolveMessageNoisePolicy(message).allowsCategoryLearning) {
    return false;
  }

  if (message.categorySource !== "system") {
    return false;
  }

  if (message.categoryConfidence === "low") {
    return true;
  }

  return (
    message.categoryConfidence === "medium" &&
    (message.priorityScore === "high" || message.unread || message.isShared)
  );
}

export function buildForYouLearningPools<TMessage extends ForYouDerivationMessage>(
  isAIEnabled: boolean,
  mailboxStore: ForYouMailboxStore<TMessage>,
  resolveMailDateMs: (message: TMessage) => number,
  _resolveMailboxLabel: (
    category: CuevionMessageCategory,
    mailboxId: InboxId | null,
  ) => string,
): {
  learningSuggestionPool: ForYouLearningSuggestion[];
  uncertainEmailPool: ForYouUncertainEmail[];
} {
  if (!isAIEnabled) {
    return {
      learningSuggestionPool: [],
      uncertainEmailPool: [],
    };
  }

  const inboxMessages = Object.entries(mailboxStore).flatMap(([mailboxId, collections]) =>
    collections.Inbox.map((message) => ({
      mailboxId: mailboxId as InboxId,
      message,
    })),
  );
  const learningEligibleInboxMessages = inboxMessages.filter(({ message }) =>
    resolveMessageNoisePolicy(message).allowsCategoryLearning,
  );
  const senderFrequencyByKey = learningEligibleInboxMessages.reduce<Record<string, number>>(
    (frequencyMap, entry) => {
      const senderKey = normalizeSenderLearningKey(entry.message.from);
      return {
        ...frequencyMap,
        [senderKey]: (frequencyMap[senderKey] ?? 0) + 1,
      };
    },
    {},
  );
  const realUncertainMessages = learningEligibleInboxMessages
    .filter(({ message }) => isRefineCuevionEligible(message) || isReviewUncertainEligible(message))
    .sort((firstEntry, secondEntry) => {
      const firstLow = firstEntry.message.categoryConfidence === "low" ? 1 : 0;
      const secondLow = secondEntry.message.categoryConfidence === "low" ? 1 : 0;

      if (secondLow !== firstLow) {
        return secondLow - firstLow;
      }

      return resolveMailDateMs(secondEntry.message) - resolveMailDateMs(firstEntry.message);
    });
  const learningSuggestionPool = realUncertainMessages
    .filter(({ message }) => isRefineCuevionEligible(message))
    .map(({ mailboxId, message }): ForYouLearningSuggestion => {
      const senderFrequency =
        senderFrequencyByKey[normalizeSenderLearningKey(message.from)] ?? 1;
      const displayLabel = resolveForYouLearningDisplayLabel(message);
      const displayPriority = resolveForYouLearningDisplayPriority(message);
      const messageTimestamp =
        message.createdAt ?? message.timestamp ?? message.time;
      const attachments = (message.attachments ?? []).map((attachment, index) => ({
        id: attachment.id ?? `${message.id}-attachment-${index}`,
        name: attachment.name?.trim() || "Attachment",
        mimeType: attachment.mimeType,
        size: attachment.size,
      }));

      return {
        key: message.id,
        sender: message.sender,
        senderAddress: message.from,
        subject: message.subject,
        createdAt: message.createdAt ?? new Date(resolveMailDateMs(message)).toISOString(),
        timestamp: messageTimestamp,
        uncertainty: 94,
        senderFrequency,
        snippet: message.body.slice(0, 2).length > 0 ? message.body.slice(0, 2) : [message.snippet],
        fullBody: message.body,
        attachments,
        reason: formatForYouReason(displayLabel, displayPriority),
        mailboxId,
        category: message.category,
        categoryConfidence: message.categoryConfidence,
        priorityScore: message.priorityScore,
        displayLabel,
        displayPriority,
      };
    });
  const uncertainEmailPool = realUncertainMessages
    .filter(({ message }) => isReviewUncertainEligible(message))
    .slice(0, 5)
    .map(({ mailboxId, message }): ForYouUncertainEmail => {
      const displayLabel = resolveForYouLearningDisplayLabel(message);
      const displayPriority = resolveForYouLearningDisplayPriority(message);
      const decisionSummary = formatLearningDecisionSummary(displayPriority, displayLabel);

      return {
        key: message.id,
        sender: message.sender,
        senderAddress: message.from,
        mailboxId,
        subject: message.subject,
        preview: message.body.slice(0, 2).length > 0 ? message.body.slice(0, 2) : [message.snippet],
        reason: formatForYouReason(displayLabel, displayPriority),
        currentMailboxLabel: decisionSummary,
        displayLabel,
        displayPriority,
      };
    });

  return {
    learningSuggestionPool,
    uncertainEmailPool,
  };
}

export function buildRecentLearningDecisions(
  senderCategoryLearning: SenderCategoryLearningStore,
): ForYouRecentLearningDecision[] {
  return Object.entries(senderCategoryLearning)
    .filter(([, entry]) => !isLearningExclusionEntry(entry))
    .map(([learningKey, entry]) => {
      const ruleType = learningKey.startsWith("domain:") ? ("domain" as const) : ("sender" as const);
      const sourceContext = inferLearningDecisionSourceContext(
        entry,
        ruleType,
      );

      return {
        key: learningKey,
        sender: formatLearningRuleLabel(learningKey),
        action: formatForYouRecentLearningAction(entry, sourceContext),
        timestamp: formatLearningRuleTimestamp(entry.updatedAt),
        ruleType,
        ruleValue: learningKey.startsWith("domain:")
          ? learningKey.replace("domain:", "")
          : learningKey,
        learnedCategory: entry.learnedCategory,
        learnedLabel: entry.learnedLabel,
        mailboxAction: entry.mailboxAction ?? (entry.learnedCategory === "Primary" ? "keep" : "move"),
        senderBehavior: entry.senderBehavior,
        sourceContext,
        sourcePrioritySelection: inferLearningDecisionPrioritySelection(entry),
        sourceMailboxId: entry.sourceMailboxId ?? null,
        sourceCurrentMailboxId: entry.sourceCurrentMailboxId ?? null,
        updatedAt: entry.updatedAt,
      };
    })
    .sort((firstDecision, secondDecision) => {
      const firstTime = firstDecision.updatedAt ? new Date(firstDecision.updatedAt).getTime() : 0;
      const secondTime = secondDecision.updatedAt
        ? new Date(secondDecision.updatedAt).getTime()
        : 0;

      return secondTime - firstTime;
    });
}
