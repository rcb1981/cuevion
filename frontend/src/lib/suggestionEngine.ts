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

const DIRECT_REPLY_PATTERNS = [
  "can you",
  "could you",
  "would you",
  "please confirm",
  "please reply",
  "please advise",
  "let me know",
  "need your response",
  "get back to",
  "are you able",
  "do you approve",
  "can we",
  "when can",
  "please share",
  "please send",
  "please review and confirm",
  "does this work",
  "what do you think",
];

const COLLABORATION_REPLY_PATTERNS = [
  "approve",
  "approval",
  "feedback",
  "thoughts",
  "input",
  "confirm timing",
  "confirm availability",
  "next step",
  "deadline",
  "cutoff",
  "as soon as possible",
  "today",
  "tomorrow",
  "before friday",
  "before monday",
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
  const replySuppressed = shouldSuppressReplySuggestion(message, category);
  const replyScore =
    (replySuppressed ? 0 : countPatternMatches(normalizedText, DIRECT_REPLY_PATTERNS)) +
    (replySuppressed ? 0 : countPatternMatches(normalizedText, COLLABORATION_REPLY_PATTERNS)) +
    (replySuppressed ? 0 : Math.min(questionCount, 2));
  let reviewScore = countPatternMatches(normalizedText, REVIEW_PATTERNS);
  let informationalScore =
    countPatternMatches(normalizedText, INFORMATIONAL_PATTERNS) +
    countPatternMatches(normalizedText, REPLY_SUPPRESSION_TEXT_PATTERNS);

  if (
    attachmentCount > 0 &&
    (reviewScore > 0 ||
      /attached|attachment|please check|review|revised|draft/.test(normalizedText))
  ) {
    reviewScore += 1;
  }

  if (replySuppressed) {
    informationalScore += 2;
  }

  if (replyScore >= 3) {
    return {
      type: "reply",
      confidence: 0.9,
      reason: "Direct ask or clear response expected",
    };
  }

  if (replyScore >= 2 && reviewScore <= replyScore) {
    return {
      type: "reply",
      confidence: 0.78,
      reason: "Contains question or coordination request",
    };
  }

  if (reviewScore >= 2) {
    return {
      type: "review",
      confidence: attachmentCount > 0 ? 0.83 : 0.72,
      reason: "Contains material to inspect before acting",
    };
  }

  if (informationalScore >= 2 || replySuppressed) {
    return {
      type: "none",
      confidence: 0.86,
      reason: replySuppressed
        ? "Looks automated, confirmational, or informational"
        : "Looks informational without a clear ask",
    };
  }

  if (category === "Primary") {
    return {
      type: "review",
      confidence: 0.58,
      reason: "Needs a quick human check before deciding",
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
) : MessageSuggestionBanner | undefined {
  const actionSuggestion = resolveSuggestedMessageAction(message, message.category);

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
