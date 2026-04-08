import {
  normalizeSenderLearningKey,
  resolveSenderLearningEntry,
  type CuevionMessageCategory,
  type SenderCategoryLearningStore,
} from "./learningEngine";

export type SuggestionCategorySource = "system" | "user" | "learned";
export type SuggestionCategoryConfidence = "low" | "medium" | "high";

export type MailMessageSuggestion = {
  type: "confirm_category";
  proposedCategory: CuevionMessageCategory;
};

export type MailMessageBehaviorSuggestion = {
  type: "auto_category";
  sender: string;
  category: CuevionMessageCategory;
};

export type MessageActionSuggestion = {
  type: "reply" | "review" | "none";
  confidence: number;
  reason?: string;
};

export type MessageSuggestionBanner = {
  type: "reply" | "review";
  primary: string;
  secondary: string;
  confidence: number;
};

type SuggestionDerivationMessage = {
  from: string;
  sender: string;
  subject: string;
  snippet: string;
  body: string[];
  signal?: string;
  isAutoReply?: boolean;
  attachments?: Array<unknown>;
};

type SuggestionMessageInput = SuggestionDerivationMessage & {
  suggestionDismissed?: boolean;
  suggestion?: MailMessageSuggestion;
};

type BehaviorSuggestionMessageInput = SuggestionDerivationMessage & {
  behaviorSuggestionDismissed?: boolean;
};

type SuggestionCategorization = {
  category: CuevionMessageCategory;
  categorySource: SuggestionCategorySource;
  categoryConfidence: SuggestionCategoryConfidence;
};

const AUTO_CATEGORY_BEHAVIOR_MIN_COUNT = 2;

const REPLY_SUPPRESSION_SENDER_PATTERNS = [
  "noreply",
  "no-reply",
  "no_reply",
  "donotreply",
  "do-not-reply",
];

const REPLY_SUPPRESSION_TEXT_PATTERNS = [
  "receipt",
  "order receipt",
  "payment receipt",
  "confirmation",
  "invoice confirmation",
  "payment confirmation",
  "submission confirmation",
  "booking confirmation",
  "purchase confirmation",
  "thank you for your payment",
  "thank you for your order",
  "your payment was received",
  "we received your submission",
  "this is an automated message",
  "automated message",
  "automated email",
  "do not reply",
  "newsletter",
  "unsubscribe",
  "manage preferences",
];

const EXPLICIT_REPLY_PATTERNS = [
  "can you",
  "could you",
  "let me know",
  "please confirm",
  "what do you think",
  "do you agree",
  "need your input",
  "reply",
  "respond",
];

const REVIEW_PATTERNS = [
  "review",
  "attached",
  "attachment",
  "agreement",
  "contract",
  "draft",
  "version",
  "changes",
  "revised",
  "legal",
  "terms",
  "comments",
  "please check",
  "see attached",
  "markup",
  "asset package",
  "materials",
  "one-sheet",
  "split sheet",
  "package",
  "document",
  "deck",
  "proposal",
];

const INFORMATIONAL_PATTERNS = [
  "update",
  "snapshot",
  "fyi",
  "for your information",
  "report",
  "performance",
  "summary",
  "recap",
  "status",
  "confirmed",
  "completed",
  "queued",
  "accepted",
  "delivered",
  "went live",
  "now live",
  "tracking",
  "results",
  "newsletter",
];

const PROMOTIONAL_PATTERNS = [
  "sale",
  "offer",
  "discount",
  "launch",
  "exclusive",
  "buy now",
  "shop now",
  "register now",
  "limited time",
  "join us",
  "discover",
  "new release",
  "stream now",
];

const GENERIC_STATEMENT_PATTERNS = [
  "just sharing",
  "for reference",
  "heads up",
  "noted",
  "thanks",
  "thank you",
  "fyi",
  "see below",
  "sharing below",
  "business call",
];

function normalizeSuggestionText(message: SuggestionDerivationMessage) {
  return `${message.subject} ${message.snippet} ${message.body.join(" ")}`
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

function countPatternMatches(text: string, patterns: string[]) {
  return patterns.reduce(
    (score, pattern) => score + (text.includes(pattern) ? 1 : 0),
    0,
  );
}

function hasAnyPattern(text: string, patterns: string[]) {
  return patterns.some((pattern) => text.includes(pattern));
}

function hasQuestionSignal(text: string) {
  return (text.match(/\?/g) ?? []).length;
}

function countLinks(text: string) {
  return (text.match(/https?:\/\/|www\./g) ?? []).length;
}

function isNonInteractiveSignal(message: SuggestionDerivationMessage) {
  return (
    message.signal === "Draft" ||
    message.signal === "Sent" ||
    message.signal === "Archived" ||
    message.signal === "Spam" ||
    message.signal === "Trash" ||
    message.isAutoReply === true
  );
}

function resolveLearningMessageActionOverride(
  message: SuggestionDerivationMessage,
  senderCategoryLearning: SenderCategoryLearningStore | undefined,
): MessageActionSuggestion | undefined {
  if (!senderCategoryLearning) {
    return undefined;
  }

  const learningMatch = resolveSenderLearningEntry(message.from, senderCategoryLearning);

  if (!learningMatch) {
    return undefined;
  }

  const entry = learningMatch.entry;

  if (
    entry.learnedCategory === "Promo" ||
    entry.sourcePrioritySelection === "Show Less" ||
    entry.sourcePrioritySelection === "Spam"
  ) {
    return {
      type: "none",
      confidence: 0.95,
      reason: "Learned as low-value or non-actionable mail",
    };
  }

  if (
    entry.learnedCategory === "Primary" &&
    entry.mailboxAction === "keep" &&
    entry.sourcePrioritySelection === "Important"
  ) {
    return {
      type: "reply",
      confidence: 0.92,
      reason: "Learned as a sender that usually needs a response",
    };
  }

  if (
    entry.learnedCategory === "Primary" &&
    entry.mailboxAction === "keep"
  ) {
    return {
      type: "none",
      confidence: 0.82,
      reason: "Learned to stay in inbox without requiring action",
    };
  }

  if (
    entry.sourceContext === "uncertain" ||
    (entry.learnedCategory === "Updates" && entry.mailboxAction === "move")
  ) {
    return {
      type: "review",
      confidence: 0.86,
      reason: "Learned as mail that should be reviewed rather than replied to",
    };
  }

  if (entry.learnedCategory === "Updates") {
    return {
      type: "none",
      confidence: 0.84,
      reason: "Learned as informational mail",
    };
  }

  return undefined;
}

export function shouldSuppressReplySuggestion(
  message: SuggestionDerivationMessage,
  category?: CuevionMessageCategory,
) {
  const normalizedSender = normalizeSenderLearningKey(message.from);
  const normalizedText = normalizeSuggestionText(message);

  return (
    REPLY_SUPPRESSION_SENDER_PATTERNS.some((pattern) =>
      normalizedSender.includes(pattern),
    ) ||
    hasAnyPattern(normalizedText, REPLY_SUPPRESSION_TEXT_PATTERNS) ||
    category === "Promo" ||
    category === "Updates"
  );
}

export function resolveSuggestedMessageAction(
  message: SuggestionDerivationMessage,
  category: CuevionMessageCategory,
  senderCategoryLearning?: SenderCategoryLearningStore,
): MessageActionSuggestion {
  if (isNonInteractiveSignal(message)) {
    return {
      type: "none",
      confidence: 0.98,
      reason: "Non-interactive system message",
    };
  }

  const normalizedText = normalizeSuggestionText(message);
  const questionCount = hasQuestionSignal(normalizedText);
  const attachmentCount = message.attachments?.length ?? 0;
  const linkCount = countLinks(normalizedText);
  const replySuppressed = shouldSuppressReplySuggestion(message, category);

  if (replySuppressed) {
    return {
      type: "none",
      confidence: 0.94,
      reason: "Looks automated, confirmational, or informational",
    };
  }

  const learningOverride = resolveLearningMessageActionOverride(
    message,
    senderCategoryLearning,
  );

  if (learningOverride) {
    return learningOverride;
  }

  const hasExplicitReplyIntent =
    questionCount > 0 || hasAnyPattern(normalizedText, EXPLICIT_REPLY_PATTERNS);
  let reviewScore = countPatternMatches(normalizedText, REVIEW_PATTERNS);
  const promotionalScore = countPatternMatches(normalizedText, PROMOTIONAL_PATTERNS);
  const genericStatementScore = countPatternMatches(
    normalizedText,
    GENERIC_STATEMENT_PATTERNS,
  );
  let informationalScore =
    countPatternMatches(normalizedText, INFORMATIONAL_PATTERNS) +
    countPatternMatches(normalizedText, REPLY_SUPPRESSION_TEXT_PATTERNS);
  const isMostlyLinks =
    linkCount >= 2 || (linkCount >= 1 && normalizedText.length > 0 && linkCount * 28 >= normalizedText.length);
  const isLowSignalGenericMessage =
    normalizedText.length < 24 || genericStatementScore > 0;

  if (
    attachmentCount > 0 &&
    (reviewScore > 0 ||
      /attached|attachment|please check|review|revised|draft/.test(normalizedText))
  ) {
    reviewScore += 1;
  }

  if (promotionalScore > 0) {
    informationalScore += promotionalScore;
  }

  if (isMostlyLinks || promotionalScore >= 2 || (isLowSignalGenericMessage && reviewScore === 0)) {
    return {
      type: "none",
      confidence: 0.9,
      reason: "Mostly links, promotional language, or low-signal statement",
    };
  }

  if (hasExplicitReplyIntent) {
    return {
      type: "reply",
      confidence: questionCount > 0 ? 0.9 : 0.84,
      reason: "Contains an explicit request for a response",
    };
  }

  if (reviewScore >= 1 || category === "Primary") {
    return {
      type: "review",
      confidence: attachmentCount > 0 || reviewScore >= 2 ? 0.8 : 0.64,
      reason:
        reviewScore >= 1
          ? "Needs a quick human check before deciding"
          : "No explicit reply intent, but worth reviewing",
    };
  }

  if (informationalScore >= 2 || replySuppressed) {
    return {
      type: "none",
      confidence: 0.86,
      reason: "Looks informational without a clear ask",
    };
  }

  return {
    type: "none",
    confidence: 0.62,
    reason: "No clear next action detected",
  };
}

export function resolveMessageSuggestionBanner(
  message: SuggestionDerivationMessage & { category: CuevionMessageCategory },
  senderCategoryLearning?: SenderCategoryLearningStore,
) : MessageSuggestionBanner | undefined {
  const actionSuggestion = resolveSuggestedMessageAction(
    message,
    message.category,
    senderCategoryLearning,
  );

  if (actionSuggestion.type === "reply") {
    return {
      type: "reply",
      primary: "A quick reply may help move this forward",
      secondary:
        actionSuggestion.reason ?? "This message seems to expect a response",
      confidence: actionSuggestion.confidence,
    };
  }

  if (actionSuggestion.type === "review") {
    return {
      type: "review",
      primary: "You may want to take a quick look at this first",
      secondary:
        actionSuggestion.reason ?? "This appears to include material that needs checking",
      confidence: actionSuggestion.confidence,
    };
  }

  return undefined;
}

export function resolveMailMessageSuggestion(
  message: SuggestionMessageInput,
  categorization: SuggestionCategorization,
  isAIEnabled: boolean,
): MailMessageSuggestion | undefined {
  if (!isAIEnabled) {
    return undefined;
  }

  if (message.suggestionDismissed) {
    return undefined;
  }

  if (message.suggestion) {
    return message.suggestion;
  }

  if (
    categorization.categorySource !== "system" ||
    categorization.categoryConfidence !== "low"
  ) {
    return undefined;
  }

  if (isNonInteractiveSignal(message)) {
    return undefined;
  }

  return {
    type: "confirm_category",
    proposedCategory: categorization.category,
  };
}

export function resolveMailMessageBehaviorSuggestion(
  message: BehaviorSuggestionMessageInput,
  categorization: SuggestionCategorization,
  senderCategoryLearning: SenderCategoryLearningStore,
  isAIEnabled: boolean,
): MailMessageBehaviorSuggestion | undefined {
  if (!isAIEnabled) {
    return undefined;
  }

  if (message.behaviorSuggestionDismissed) {
    return undefined;
  }

  const senderLearningMatch = resolveSenderLearningEntry(
    message.from,
    senderCategoryLearning,
  );
  const senderLearning = senderLearningMatch?.entry;

  if (
    !senderLearning ||
    senderLearning.learnedFromCount < AUTO_CATEGORY_BEHAVIOR_MIN_COUNT ||
    senderLearning.autoCategoryEnabled
  ) {
    return undefined;
  }

  if (
    categorization.categorySource !== "user" &&
    categorization.categorySource !== "learned"
  ) {
    return undefined;
  }

  return {
    type: "auto_category",
    sender: senderLearningMatch?.key ?? normalizeSenderLearningKey(message.from),
    category: senderLearning.learnedCategory,
  };
}
