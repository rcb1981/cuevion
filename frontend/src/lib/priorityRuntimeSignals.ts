import {
  resolveRuntimePrioritySource,
  resolveRuntimeReturnedReplyEvidence,
  type RuntimeMailboxAddressLike,
  type RuntimePriorityMessageLike,
} from "./priorityRuntimeAdapter";
import type {
  PriorityFocusPreferenceVisibility,
  PriorityLearningSelection,
  PriorityManualOverride,
  PrioritySourceResult,
} from "./prioritySource";
import type { ReturnedReplyEvidence } from "./returnedReplyEvidence";

export type PriorityRuntimeCandidateMessage = RuntimePriorityMessageLike & {
  mailboxId?: string | null;
};

export type PriorityRuntimeSignal = {
  messageKey: string;
  mailboxId?: string;
  returnedReplyEvidence: ReturnedReplyEvidence;
  prioritySource: PrioritySourceResult;
};

export type PriorityRuntimeSignalInput = {
  candidateMessages: PriorityRuntimeCandidateMessage[];
  messagesByMailboxId?: Record<string, RuntimePriorityMessageLike[]>;
  sentMessagesByMailboxId?: Record<string, RuntimePriorityMessageLike[]>;
  ownEmailAddresses?: string[];
  connectedMailboxes?: RuntimeMailboxAddressLike[];
  authenticatedUserEmail?: string | null;
  manualPriorityOverrides?: Record<string, PriorityManualOverride | undefined>;
  learnedPrioritySelections?: Record<string, PriorityLearningSelection | string | undefined>;
  focusPreferenceVisibilityByMessageKey?: Record<
    string,
    PriorityFocusPreferenceVisibility | string | undefined
  >;
  hasCollaborationContextByMessageKey?: Record<string, boolean | undefined>;
  hasAssignedReviewContextByMessageKey?: Record<string, boolean | undefined>;
  resolveMessageKey?: (
    message: PriorityRuntimeCandidateMessage,
    index: number,
  ) => string;
};

function defaultMessageKey(
  message: PriorityRuntimeCandidateMessage,
  index: number,
) {
  const mailboxId = message.mailboxId?.trim() || "unknown-mailbox";
  const stableIdentity =
    message.id?.trim() ||
    message.imapUid?.trim() ||
    [
      message.subject,
      message.from ?? message.sender ?? "",
      message.createdAt ?? message.timestamp ?? "",
    ].join("|");

  return `${mailboxId}:${stableIdentity || `candidate-${index}`}`;
}

function resolveMailboxScopedMessages(
  mailboxId: string | undefined,
  messagesByMailboxId: Record<string, RuntimePriorityMessageLike[]> | undefined,
) {
  if (!mailboxId) {
    return [];
  }

  return messagesByMailboxId?.[mailboxId] ?? [];
}

export function buildPriorityRuntimeSignalsForCandidates(
  input: PriorityRuntimeSignalInput,
): Record<string, PriorityRuntimeSignal> {
  return input.candidateMessages.reduce<Record<string, PriorityRuntimeSignal>>(
    (signalsByMessageKey, candidateMessage, index) => {
      const messageKey =
        input.resolveMessageKey?.(candidateMessage, index) ??
        defaultMessageKey(candidateMessage, index);
      const mailboxId = candidateMessage.mailboxId?.trim() || undefined;
      const threadMessages = resolveMailboxScopedMessages(
        mailboxId,
        input.messagesByMailboxId,
      );
      const sentMessages = resolveMailboxScopedMessages(
        mailboxId,
        input.sentMessagesByMailboxId,
      );
      const returnedReplyEvidence = resolveRuntimeReturnedReplyEvidence({
        currentMessage: candidateMessage,
        threadMessages,
        sentMessages,
        ownEmailAddresses: input.ownEmailAddresses,
        connectedMailboxes: input.connectedMailboxes,
        authenticatedUserEmail: input.authenticatedUserEmail,
      });
      const prioritySource = resolveRuntimePrioritySource({
        message: candidateMessage,
        manualOverride: input.manualPriorityOverrides?.[messageKey],
        learnedPrioritySelection: input.learnedPrioritySelections?.[messageKey],
        focusPreferenceVisibility:
          input.focusPreferenceVisibilityByMessageKey?.[messageKey],
        hasCollaborationContext:
          input.hasCollaborationContextByMessageKey?.[messageKey],
        hasAssignedReviewContext:
          input.hasAssignedReviewContextByMessageKey?.[messageKey],
        returnedReplyEvidence,
      });

      signalsByMessageKey[messageKey] = {
        messageKey,
        mailboxId,
        returnedReplyEvidence,
        prioritySource,
      };

      return signalsByMessageKey;
    },
    {},
  );
}
