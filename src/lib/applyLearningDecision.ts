import {
  buildSenderLearningStoreKey,
  normalizeSenderLearningDomain,
  normalizeSenderLearningKey,
  type CuevionMessageCategory,
  type LearningDecisionPrioritySelection,
  type LearningDecisionSourceContext,
  type SenderCategoryLearningEntry,
  type SenderCategoryLearningStore,
} from "./learningEngine";
import {
  buildRecentLearningDecisions,
  type ForYouRecentLearningDecision,
} from "./forYouEngine";
import type { InboxId } from "../types/onboarding";

type LearningDecisionMailboxStoreMessage = {
  id: string;
  from: string;
};

type LearningDecisionMailboxStore = Record<
  string,
  Record<string, LearningDecisionMailboxStoreMessage[]>
>;

export type ApplyLearningDecisionInput = {
  senderCategoryLearning: SenderCategoryLearningStore;
  mailboxStore?: LearningDecisionMailboxStore;
  ruleValue: string;
  ruleType: "sender" | "domain";
  category: CuevionMessageCategory;
  mailboxAction?: "keep" | "move";
  sourceContext?: LearningDecisionSourceContext;
  sourcePrioritySelection?: LearningDecisionPrioritySelection | null;
  sourceMailboxId?: InboxId | null;
  sourceCurrentMailboxId?: InboxId | null;
  updatedAt?: string;
  learnedFromCountFloor?: number;
  autoCategoryEnabled?: boolean;
};

export type ApplyLearningDecisionResult = {
  learningKey: string;
  nextEntry: SenderCategoryLearningEntry;
  nextSenderCategoryLearning: SenderCategoryLearningStore;
  nextRecentLearningDecisions: ForYouRecentLearningDecision[];
  affectedMessageIds: string[];
};

function resolveAffectedMessageIds(
  mailboxStore: LearningDecisionMailboxStore | undefined,
  ruleType: "sender" | "domain",
  ruleValue: string,
) {
  if (!mailboxStore) {
    return [];
  }

  const normalizedRuleValue =
    ruleType === "domain"
      ? normalizeSenderLearningDomain(ruleValue)
      : normalizeSenderLearningKey(ruleValue);

  if (!normalizedRuleValue) {
    return [];
  }

  return Object.values(mailboxStore).flatMap((collections) =>
    Object.values(collections).flatMap((messages) =>
      messages
        .filter((message) => {
          const normalizedMessageValue =
            ruleType === "domain"
              ? normalizeSenderLearningDomain(message.from)
              : normalizeSenderLearningKey(message.from);

          return normalizedMessageValue === normalizedRuleValue;
        })
        .map((message) => message.id),
    ),
  );
}

export function applyLearningDecision(
  input: ApplyLearningDecisionInput,
): ApplyLearningDecisionResult | null {
  const learningKey = buildSenderLearningStoreKey(input.ruleValue, input.ruleType);

  if (!learningKey) {
    return null;
  }

  const existingEntry = input.senderCategoryLearning[learningKey];
  const nextEntry: SenderCategoryLearningEntry = {
    learnedCategory: input.category,
    learnedFromCount: Math.max(
      existingEntry?.learnedFromCount ?? 0,
      input.learnedFromCountFloor ?? 3,
    ),
    autoCategoryEnabled: input.autoCategoryEnabled ?? existingEntry?.autoCategoryEnabled ?? true,
    mailboxAction:
      input.mailboxAction ?? existingEntry?.mailboxAction ?? (input.category === "Primary" ? "keep" : "move"),
    sourceContext: input.sourceContext ?? existingEntry?.sourceContext,
    sourcePrioritySelection:
      input.sourcePrioritySelection ?? existingEntry?.sourcePrioritySelection,
    sourceMailboxId:
      input.sourceMailboxId !== undefined
        ? input.sourceMailboxId
        : existingEntry?.sourceMailboxId,
    sourceCurrentMailboxId:
      input.sourceCurrentMailboxId !== undefined
        ? input.sourceCurrentMailboxId
        : existingEntry?.sourceCurrentMailboxId,
    updatedAt: input.updatedAt ?? new Date().toISOString(),
  };
  const nextSenderCategoryLearning: SenderCategoryLearningStore = {
    ...input.senderCategoryLearning,
    [learningKey]: nextEntry,
  };

  return {
    learningKey,
    nextEntry,
    nextSenderCategoryLearning,
    nextRecentLearningDecisions: buildRecentLearningDecisions(nextSenderCategoryLearning),
    affectedMessageIds: resolveAffectedMessageIds(
      input.mailboxStore,
      input.ruleType,
      input.ruleValue,
    ),
  };
}
