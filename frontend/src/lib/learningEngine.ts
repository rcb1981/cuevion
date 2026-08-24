import type { InboxId } from "../types/onboarding";

export type CuevionMessageCategory = "Primary" | "Promo" | "Updates";
export type CuevionLearningLabel =
  | "Demo"
  | "Promo"
  | "Business"
  | "Finance"
  | "Update"
  | "Reply"
  | "Other"
  | "Spam";
export type SenderLearningBehavior =
  | "always_prioritize"
  | "normal"
  | "show_less"
  | "spam"
  | "do_not_learn";

export type LearningDecisionSourceContext =
  | "refine"
  | "uncertain"
  | "paste_sender_or_domain";

export type LearningDecisionPrioritySelection =
  | "Important"
  | "Normal"
  | "Show Less"
  | "Spam";

export type SenderCategoryLearningEntry = {
  learnedCategory: CuevionMessageCategory;
  learnedLabel?: CuevionLearningLabel;
  learnedFromCount: number;
  autoCategoryEnabled?: boolean;
  mailboxAction?: "keep" | "move";
  senderBehavior?: SenderLearningBehavior;
  sourceContext?: LearningDecisionSourceContext;
  sourcePrioritySelection?: LearningDecisionPrioritySelection;
  sourceMailboxId?: InboxId | null;
  sourceCurrentMailboxId?: InboxId | null;
  updatedAt?: string;
};

export type SenderCategoryLearningStore = Record<string, SenderCategoryLearningEntry>;

export function isLearningExclusionEntry(
  entry: SenderCategoryLearningEntry | null | undefined,
) {
  return entry?.senderBehavior === "do_not_learn";
}

export function normalizeSenderLearningKey(value: string) {
  const normalizedValue = value.trim().toLowerCase();
  const emailMatch = normalizedValue.match(
    /([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})/i,
  );

  return emailMatch?.[1] ?? normalizedValue;
}

export function normalizeSenderLearningDomain(value: string) {
  const normalizedValue = value.trim().toLowerCase();
  const domainValue = normalizedValue.includes("@")
    ? normalizedValue.split("@")[1] ?? ""
    : normalizedValue;

  return domainValue.trim();
}

export function buildSenderLearningStoreKey(
  value: string,
  matchType: "sender" | "domain" = "sender",
) {
  if (matchType === "domain") {
    const domainKey = normalizeSenderLearningDomain(value);
    return domainKey ? `domain:${domainKey}` : "";
  }

  return normalizeSenderLearningKey(value);
}

export function resolveSenderLearningEntry(
  senderAddress: string,
  senderCategoryLearning: SenderCategoryLearningStore,
) {
  const senderKey = buildSenderLearningStoreKey(senderAddress, "sender");
  const senderEntry = senderCategoryLearning[senderKey];

  if (senderEntry) {
    // An exact sender exclusion is authoritative as a stop condition: it must
    // not expose stale positive fields or continue into domain fallback.
    if (isLearningExclusionEntry(senderEntry)) {
      return null;
    }

    return {
      entry: senderEntry,
      key: senderKey,
      matchType: "sender" as const,
    };
  }

  const domainKey = buildSenderLearningStoreKey(senderAddress, "domain");
  const domainEntry = senderCategoryLearning[domainKey];

  if (domainEntry) {
    if (isLearningExclusionEntry(domainEntry)) {
      return null;
    }

    return {
      entry: domainEntry,
      key: domainKey,
      matchType: "domain" as const,
    };
  }

  return null;
}

export function resolvePasteRuleInputType(value: string) {
  const trimmedValue = value.trim().toLowerCase();

  if (!trimmedValue) {
    return null;
  }

  const emailPattern = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/i;
  const domainPattern =
    /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/i;

  if (emailPattern.test(trimmedValue)) {
    return "sender" as const;
  }

  if (domainPattern.test(trimmedValue)) {
    return "domain" as const;
  }

  return "invalid" as const;
}

export function formatLearningRuleLabel(learningKey: string) {
  if (learningKey.startsWith("domain:")) {
    return `Domain: ${learningKey.replace("domain:", "")}`;
  }

  return learningKey;
}

export function formatLearningRuleAction(entry: SenderCategoryLearningEntry) {
  const label =
    entry.learnedLabel ??
    (entry.sourcePrioritySelection === "Spam"
      ? "Spam"
      : entry.learnedCategory === "Promo"
        ? "Promo"
        : entry.learnedCategory === "Updates"
          ? "Update"
          : "Other");
  const priority =
    entry.sourcePrioritySelection === "Important"
      ? "Priority"
      : entry.sourcePrioritySelection === "Show Less" ||
          entry.sourcePrioritySelection === "Spam"
        ? "Low"
        : "Normal";
  const behavior =
    entry.senderBehavior === "always_prioritize"
      ? " · Always prioritize"
      : entry.senderBehavior === "normal"
        ? " · Show normally"
        : entry.senderBehavior === "show_less"
          ? " · Show less"
          : entry.senderBehavior === "spam"
            ? " · Mark sender as spam"
            : "";

  return `${priority} · ${label}${behavior}`;
}

export function formatLearningRuleTimestamp(value?: string) {
  if (!value) {
    return "Recently";
  }

  const timestamp = new Date(value).getTime();

  if (Number.isNaN(timestamp)) {
    return "Recently";
  }

  const diffMs = Date.now() - timestamp;
  const diffMinutes = Math.max(0, Math.round(diffMs / (60 * 1000)));

  if (diffMinutes < 60) {
    return diffMinutes <= 1 ? "NOW" : `${diffMinutes} MIN AGO`;
  }

  const diffHours = Math.round(diffMinutes / 60);

  if (diffHours < 24) {
    return `${diffHours} HOUR${diffHours === 1 ? "" : "S"} AGO`;
  }

  return new Date(value)
    .toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    })
    .toUpperCase();
}

export function resolveForYouCategoryFromLearningEntry(
  entry: SenderCategoryLearningEntry,
): "Important" | "Review" | "Promo" | "Demo" | "Spam" {
  if (entry.learnedCategory === "Promo") {
    return "Promo";
  }

  if (entry.learnedCategory === "Updates") {
    return entry.mailboxAction === "move" ? "Review" : "Review";
  }

  return "Important";
}

export function resolveCuevionCategoryFromForYouSelection(
  selection: "Important" | "Review" | "Promo" | "Demo" | "Spam" | null,
): CuevionMessageCategory {
  if (selection === "Promo") {
    return "Promo";
  }

  if (selection === "Review" || selection === "Spam") {
    return "Updates";
  }

  return "Primary";
}

export function resolveMailboxActionFromForYouSelection(
  selection:
    | "Important"
    | "Normal"
    | "Show Less"
    | "Spam"
    | "Review"
    | "Promo"
    | "Demo"
    | null,
  category: CuevionMessageCategory,
  explicitMailboxAction?: "keep" | "move" | null,
) {
  if (explicitMailboxAction) {
    return explicitMailboxAction;
  }

  if (selection === "Spam" || selection === "Show Less" || selection === "Review") {
    return "move" as const;
  }

  if (category === "Promo" || category === "Updates") {
    return "move" as const;
  }

  return "keep" as const;
}

export function resolveCuevionCategoryFromMailboxId(
  mailboxId: InboxId | null,
): CuevionMessageCategory {
  if (mailboxId === "promo") {
    return "Promo";
  }

  if (mailboxId === "demo" || mailboxId === "business") {
    return "Updates";
  }

  return "Primary";
}

export function resolveMailboxActionFromMailboxId(
  mailboxId: InboxId | null,
): "keep" | "move" {
  return mailboxId === "main" ? "keep" : "move";
}

export function inferLearningDecisionSourceContext(
  entry: SenderCategoryLearningEntry,
  ruleType: "sender" | "domain",
): LearningDecisionSourceContext | null {
  if (entry.sourceContext) {
    return entry.sourceContext;
  }

  if (entry.sourceCurrentMailboxId !== undefined) {
    return "uncertain";
  }

  if (ruleType === "domain") {
    return "paste_sender_or_domain";
  }

  return "refine";
}

export function inferLearningDecisionMailboxId(
  entry: SenderCategoryLearningEntry,
): InboxId | null {
  if (entry.sourceMailboxId !== undefined) {
    return entry.sourceMailboxId;
  }

  if (entry.learnedCategory === "Promo") {
    return "promo";
  }

  if (entry.mailboxAction === "keep") {
    return "main";
  }

  return null;
}

export function inferLearningDecisionPrioritySelection(
  entry: SenderCategoryLearningEntry,
): LearningDecisionPrioritySelection | null {
  if (entry.sourcePrioritySelection) {
    return entry.sourcePrioritySelection;
  }

  if (entry.learnedCategory === "Promo") {
    return "Normal";
  }

  if (entry.learnedCategory === "Updates" && entry.mailboxAction === "move") {
    return "Show Less";
  }

  if (entry.learnedCategory === "Primary" && entry.mailboxAction === "keep") {
    return "Important";
  }

  return null;
}
