import type { InboxId } from "../types/onboarding";
import {
  formatLearningRuleAction,
  formatLearningRuleLabel,
  formatLearningRuleTimestamp,
  inferLearningDecisionMailboxId,
  inferLearningDecisionPrioritySelection,
  inferLearningDecisionSourceContext,
  normalizeSenderLearningKey,
  type CuevionMessageCategory,
  type LearningDecisionPrioritySelection,
  type LearningDecisionSourceContext,
  type SenderCategoryLearningStore,
} from "./learningEngine";

export type ForYouLearningSuggestion = {
  key: string;
  sender: string;
  senderAddress: string;
  subject: string;
  createdAt: string;
  uncertainty: number;
  senderFrequency: number;
  snippet: string[];
  reason: string;
  visualLabel?: string;
  mailboxId: InboxId | null;
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
};

export type ForYouRecentLearningDecision = {
  key: string;
  sender: string;
  action: string;
  timestamp: string;
  ruleType: "sender" | "domain";
  ruleValue: string;
  learnedCategory: CuevionMessageCategory;
  mailboxAction: "keep" | "move";
  sourceContext: LearningDecisionSourceContext | null;
  sourcePrioritySelection: LearningDecisionPrioritySelection | null;
  sourceMailboxId: InboxId | null;
  sourceCurrentMailboxId: InboxId | null;
  updatedAt?: string;
};

export type ForYouDerivationMessage = {
  id: string;
  sender: string;
  from: string;
  subject: string;
  createdAt?: string;
  category: CuevionMessageCategory;
  categorySource: "system" | "user" | "learned";
  categoryConfidence: "low" | "medium" | "high";
  priorityScore: "low" | "medium" | "high";
  unread?: boolean;
  isShared?: boolean;
  snippet: string;
  body: string[];
  suggestion?: {
    type: "confirm_category";
    proposedCategory: CuevionMessageCategory;
  };
};

export type ForYouMailboxStore<TMessage extends ForYouDerivationMessage> = Record<
  string,
  {
    Inbox: TMessage[];
  }
>;

export function formatForYouReason(
  message: Pick<ForYouDerivationMessage, "category">,
  mailboxLabel: string,
) {
  if (message.category === "Promo") {
    return `Cuevion placed this in ${mailboxLabel}, but is not confident yet.`;
  }

  if (message.category === "Updates") {
    return `Cuevion thinks this belongs in ${mailboxLabel}, but still needs confirmation.`;
  }

  return `Cuevion placed this in ${mailboxLabel}, but still needs confirmation.`;
}

export function isReviewUncertainEligible(message: ForYouDerivationMessage) {
  return (
    message.categorySource === "system" &&
    message.suggestion?.type === "confirm_category"
  );
}

export function isRefineCuevionEligible(message: ForYouDerivationMessage) {
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
  mailboxStore: ForYouMailboxStore<TMessage>,
  resolveMailDateMs: (message: TMessage) => number,
  resolveMailboxLabel: (
    category: CuevionMessageCategory,
    mailboxId: InboxId | null,
  ) => string,
): {
  learningSuggestionPool: ForYouLearningSuggestion[];
  uncertainEmailPool: ForYouUncertainEmail[];
} {
  const inboxMessages = Object.entries(mailboxStore).flatMap(([mailboxId, collections]) =>
    collections.Inbox.map((message) => ({
      mailboxId: mailboxId as InboxId,
      message,
    })),
  );
  const senderFrequencyByKey = inboxMessages.reduce<Record<string, number>>(
    (frequencyMap, entry) => {
      const senderKey = normalizeSenderLearningKey(entry.message.from);
      return {
        ...frequencyMap,
        [senderKey]: (frequencyMap[senderKey] ?? 0) + 1,
      };
    },
    {},
  );
  const realUncertainMessages = inboxMessages
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
      const mailboxLabel = resolveMailboxLabel(message.category, mailboxId);

      return {
        key: message.id,
        sender: message.sender,
        senderAddress: message.from,
        subject: message.subject,
        createdAt: message.createdAt ?? new Date(resolveMailDateMs(message)).toISOString(),
        uncertainty: 94,
        senderFrequency,
        snippet: message.body.slice(0, 2).length > 0 ? message.body.slice(0, 2) : [message.snippet],
        reason: formatForYouReason(message, mailboxLabel),
        mailboxId,
      };
    });
  const uncertainEmailPool = realUncertainMessages
    .filter(({ message }) => isReviewUncertainEligible(message))
    .slice(0, 5)
    .map(({ mailboxId, message }): ForYouUncertainEmail => {
      const mailboxLabel = resolveMailboxLabel(message.category, mailboxId);

      return {
        key: message.id,
        sender: message.sender,
        senderAddress: message.from,
        mailboxId,
        subject: message.subject,
        preview: message.body.slice(0, 2).length > 0 ? message.body.slice(0, 2) : [message.snippet],
        reason: formatForYouReason(message, mailboxLabel),
        currentMailboxLabel: mailboxLabel,
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
    .map(([learningKey, entry]) => ({
      key: learningKey,
      sender: formatLearningRuleLabel(learningKey),
      action: formatLearningRuleAction(entry),
      timestamp: formatLearningRuleTimestamp(entry.updatedAt),
      ruleType: learningKey.startsWith("domain:") ? ("domain" as const) : ("sender" as const),
      ruleValue: learningKey.startsWith("domain:")
        ? learningKey.replace("domain:", "")
        : learningKey,
      learnedCategory: entry.learnedCategory,
      mailboxAction: entry.mailboxAction ?? (entry.learnedCategory === "Primary" ? "keep" : "move"),
      sourceContext: inferLearningDecisionSourceContext(
        entry,
        learningKey.startsWith("domain:") ? "domain" : "sender",
      ),
      sourcePrioritySelection: inferLearningDecisionPrioritySelection(entry),
      sourceMailboxId: inferLearningDecisionMailboxId(entry),
      sourceCurrentMailboxId: entry.sourceCurrentMailboxId ?? null,
      updatedAt: entry.updatedAt,
    }))
    .sort((firstDecision, secondDecision) => {
      const firstTime = firstDecision.updatedAt ? new Date(firstDecision.updatedAt).getTime() : 0;
      const secondTime = secondDecision.updatedAt
        ? new Date(secondDecision.updatedAt).getTime()
        : 0;

      return secondTime - firstTime;
    });
}
