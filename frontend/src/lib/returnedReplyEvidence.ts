import { normalizeThreadSubject, resolveMessageDateMs } from "./inboxEngine";

export type ReturnedReplyConfidence = "high" | "medium" | "low";

export type ReturnedReplyEvidence = {
  hasEvidence: boolean;
  confidence: ReturnedReplyConfidence;
  reason: string;
  lastUserReplyAt?: string;
  returnedReplyAt?: string;
};

export type ReturnedReplyMessageLike = {
  id?: string;
  threadId?: string | null;
  subject: string;
  from?: string | null;
  sender?: string | null;
  to?: string | null;
  cc?: string | null;
  createdAt?: string | null;
  timestamp?: string | null;
  signal?: string | null;
};

export type ResolveReturnedReplyEvidenceInput = {
  currentMessage: ReturnedReplyMessageLike;
  threadMessages?: ReturnedReplyMessageLike[];
  sentMessages?: ReturnedReplyMessageLike[];
  ownEmailAddresses?: string[];
};

const noReturnedReplyEvidence = (
  reason: string,
  confidence: ReturnedReplyConfidence = "low",
): ReturnedReplyEvidence => ({
  hasEvidence: false,
  confidence,
  reason,
});

const returnedReplyConfidenceRank: Record<ReturnedReplyConfidence, number> = {
  high: 3,
  medium: 2,
  low: 1,
};

export function selectStrongestReturnedReplyEvidence(
  ...candidates: Array<ReturnedReplyEvidence | null | undefined>
): ReturnedReplyEvidence {
  const availableCandidates = candidates.filter(
    (candidate): candidate is ReturnedReplyEvidence => Boolean(candidate),
  );

  return (
    availableCandidates.reduce<ReturnedReplyEvidence | null>((strongest, candidate) => {
      if (!strongest) {
        return candidate;
      }

      if (candidate.hasEvidence !== strongest.hasEvidence) {
        return candidate.hasEvidence ? candidate : strongest;
      }

      return returnedReplyConfidenceRank[candidate.confidence] >
        returnedReplyConfidenceRank[strongest.confidence]
        ? candidate
        : strongest;
    }, null) ??
    noReturnedReplyEvidence("No returned-reply evidence source was available.")
  );
}

export function normalizeReturnedReplyEmailAddress(
  value: string | null | undefined,
) {
  const normalizedValue = (value ?? "").trim().toLowerCase();
  const emailMatch = normalizedValue.match(
    /([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})/i,
  );

  return emailMatch?.[1] ?? normalizedValue;
}

function splitAddressList(value: string | null | undefined) {
  return (value ?? "")
    .split(/[,;]/)
    .map(normalizeReturnedReplyEmailAddress)
    .filter(Boolean);
}

export function getReturnedReplySenderAddress(
  message: ReturnedReplyMessageLike,
) {
  return normalizeReturnedReplyEmailAddress(message.from || message.sender);
}

function getMessageDate(message: ReturnedReplyMessageLike) {
  const ms = resolveMessageDateMs({
    createdAt: message.createdAt ?? undefined,
    timestamp: message.timestamp ?? undefined,
  });

  if (!Number.isFinite(ms) || ms <= 0) {
    return null;
  }

  return {
    ms,
    value: message.createdAt || message.timestamp || new Date(ms).toISOString(),
  };
}

function getExplicitThreadId(message: ReturnedReplyMessageLike) {
  return message.threadId?.trim() || "";
}

function isExplicitThreadMatch(
  currentMessage: ReturnedReplyMessageLike,
  sentMessage: ReturnedReplyMessageLike,
) {
  const currentThreadId = getExplicitThreadId(currentMessage);

  return Boolean(
    currentThreadId &&
      getExplicitThreadId(sentMessage) &&
      currentThreadId === getExplicitThreadId(sentMessage),
  );
}

function hasFallbackParticipantEvidence(
  currentMessage: ReturnedReplyMessageLike,
  sentMessage: ReturnedReplyMessageLike,
  threadMessages: ReturnedReplyMessageLike[],
) {
  const currentSender = getReturnedReplySenderAddress(currentMessage);

  if (!currentSender) {
    return false;
  }

  const sentRecipients = new Set([
    ...splitAddressList(sentMessage.to),
    ...splitAddressList(sentMessage.cc),
  ]);

  if (sentRecipients.has(currentSender)) {
    return true;
  }

  const normalizedSubject = normalizeThreadSubject(currentMessage.subject);

  return threadMessages.some((message) => {
    if (normalizeThreadSubject(message.subject) !== normalizedSubject) {
      return false;
    }

    const participants = new Set([
      getReturnedReplySenderAddress(message),
      ...splitAddressList(message.to),
      ...splitAddressList(message.cc),
    ].filter(Boolean));

    return participants.has(currentSender);
  });
}

function resolveThreadMatchConfidence(
  currentMessage: ReturnedReplyMessageLike,
  sentMessage: ReturnedReplyMessageLike,
  threadMessages: ReturnedReplyMessageLike[],
): ReturnedReplyConfidence | null {
  if (isExplicitThreadMatch(currentMessage, sentMessage)) {
    return "high";
  }

  const currentSubject = normalizeThreadSubject(currentMessage.subject);
  const sentSubject = normalizeThreadSubject(sentMessage.subject);

  if (
    currentSubject &&
    sentSubject &&
    currentSubject === sentSubject &&
    hasFallbackParticipantEvidence(currentMessage, sentMessage, threadMessages)
  ) {
    return "medium";
  }

  return null;
}

function isUserAuthoredSentMessage(
  message: ReturnedReplyMessageLike,
  ownAddressSet: Set<string>,
) {
  const sender = getReturnedReplySenderAddress(message);

  if (!sender) {
    return normalizeReturnedReplyEmailAddress(message.signal) === "sent";
  }

  return ownAddressSet.has(sender);
}

export function resolveReturnedReplyEvidence(
  input: ResolveReturnedReplyEvidenceInput,
): ReturnedReplyEvidence {
  const ownAddressSet = new Set(
    (input.ownEmailAddresses ?? [])
      .map(normalizeReturnedReplyEmailAddress)
      .filter(Boolean),
  );

  if (ownAddressSet.size === 0) {
    return noReturnedReplyEvidence(
      "Own email addresses are required before returned replies can be detected.",
    );
  }

  const currentSender = getReturnedReplySenderAddress(input.currentMessage);
  if (!currentSender) {
    return noReturnedReplyEvidence(
      "Current message sender is missing, so returned-reply evidence is incomplete.",
    );
  }

  if (ownAddressSet.has(currentSender)) {
    return noReturnedReplyEvidence(
      "Current message is from one of the user's own addresses.",
      "medium",
    );
  }

  const currentDate = getMessageDate(input.currentMessage);
  if (!currentDate) {
    return noReturnedReplyEvidence(
      "Current message timestamp is missing or invalid.",
    );
  }

  const sentMessages = input.sentMessages ?? [];
  const threadMessages = input.threadMessages ?? [];
  const matchingSentReplies = sentMessages
    .filter((sentMessage) => isUserAuthoredSentMessage(sentMessage, ownAddressSet))
    .flatMap((sentMessage) => {
      const sentDate = getMessageDate(sentMessage);
      if (!sentDate || sentDate.ms >= currentDate.ms) {
        return [];
      }

      const confidence = resolveThreadMatchConfidence(
        input.currentMessage,
        sentMessage,
        threadMessages,
      );

      if (!confidence) {
        return [];
      }

      return [
        {
          confidence,
          sentDate,
        },
      ];
    })
    .sort((first, second) => second.sentDate.ms - first.sentDate.ms);

  const bestMatch = matchingSentReplies[0];
  if (!bestMatch) {
    return noReturnedReplyEvidence(
      "No earlier user-authored sent message was found in the same reliable thread.",
    );
  }

  return {
    hasEvidence: true,
    confidence: bestMatch.confidence,
    reason:
      bestMatch.confidence === "high"
        ? "Explicit threadId matches a user reply sent before this external inbound message."
        : "Normalized thread subject and participant evidence match a user reply sent before this external inbound message.",
    lastUserReplyAt: bestMatch.sentDate.value,
    returnedReplyAt: currentDate.value,
  };
}
