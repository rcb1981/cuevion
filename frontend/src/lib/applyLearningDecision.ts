import {
  buildSenderLearningStoreKey,
  isLearningExclusionEntry,
  normalizeSenderLearningDomain,
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
  learnedLabel?: CuevionLearningLabel;
  learnedFromCount?: number;
  mailboxAction?: "keep" | "move";
  senderBehavior?: SenderLearningBehavior;
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

type CurrentMessageCategoryDecision = {
  id: string;
  category?: CuevionMessageCategory;
  categorySource?: "system" | "user" | "learned";
  categoryConfidence?: "low" | "medium" | "high";
  suggestion?: unknown;
};

export function applyCurrentMessageCategoryDecision<
  TMessage extends CurrentMessageCategoryDecision,
  TStore extends Record<string, Record<string, TMessage[]>>,
>(
  mailboxStore: TStore,
  mailboxId: string | null | undefined,
  messageId: string | null | undefined,
  category: CuevionMessageCategory,
): TStore {
  if (!mailboxId || !messageId || !mailboxStore[mailboxId]) {
    return mailboxStore;
  }

  let didUpdate = false;
  const nextCollections = Object.fromEntries(
    Object.entries(mailboxStore[mailboxId]).map(([folder, messages]) => [
      folder,
      messages.map((message) => {
        if (message.id !== messageId) {
          return message;
        }

        didUpdate = true;
        return {
          ...message,
          category,
          categorySource: "user" as const,
          categoryConfidence: "high" as const,
          suggestion: undefined,
        };
      }),
    ]),
  ) as Record<string, TMessage[]>;

  return didUpdate
    ? ({
        ...mailboxStore,
        [mailboxId]: nextCollections,
      } as TStore)
    : mailboxStore;
}

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
  const existingPositiveEntry = isLearningExclusionEntry(existingEntry)
    ? undefined
    : existingEntry;
  const nextEntry: SenderCategoryLearningEntry =
    input.senderBehavior === "do_not_learn"
      ? {
          // learnedCategory/learnedFromCount remain compatibility fields for
          // the existing flat v2 payload. Central resolution treats this
          // record only as an exclusion and never exposes them as authority.
          learnedCategory: input.category,
          learnedFromCount: 0,
          senderBehavior: "do_not_learn",
          sourceContext: input.sourceContext,
          sourceMailboxId: input.sourceMailboxId,
          sourceCurrentMailboxId: input.sourceCurrentMailboxId,
          updatedAt: input.updatedAt ?? new Date().toISOString(),
        }
      : {
          learnedCategory: input.category,
          learnedLabel: input.learnedLabel ?? existingPositiveEntry?.learnedLabel,
          learnedFromCount:
            input.learnedFromCount ??
            Math.max(
              existingPositiveEntry?.learnedFromCount ?? 0,
              input.learnedFromCountFloor ?? 3,
            ),
          autoCategoryEnabled:
            input.autoCategoryEnabled ??
            existingPositiveEntry?.autoCategoryEnabled ??
            true,
          mailboxAction:
            input.mailboxAction ??
            existingPositiveEntry?.mailboxAction ??
            (input.category === "Primary" ? "keep" : "move"),
          senderBehavior:
            input.senderBehavior ?? existingPositiveEntry?.senderBehavior,
          sourceContext: input.sourceContext ?? existingPositiveEntry?.sourceContext,
          sourcePrioritySelection:
            input.sourcePrioritySelection ??
            existingPositiveEntry?.sourcePrioritySelection,
          sourceMailboxId:
            input.sourceMailboxId !== undefined
              ? input.sourceMailboxId
              : existingPositiveEntry?.sourceMailboxId,
          sourceCurrentMailboxId:
            input.sourceCurrentMailboxId !== undefined
              ? input.sourceCurrentMailboxId
              : existingPositiveEntry?.sourceCurrentMailboxId,
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
