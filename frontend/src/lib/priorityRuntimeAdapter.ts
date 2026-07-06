import { normalizeThreadSubject } from "./inboxEngine";
import {
  resolvePrioritySource,
  type PrioritySourceMessageLike,
  type PrioritySourceResult,
  type ResolvePrioritySourceInput,
} from "./prioritySource";
import {
  resolveReturnedReplyEvidence,
  type ResolveReturnedReplyEvidenceInput,
  type ReturnedReplyEvidence,
  type ReturnedReplyMessageLike,
} from "./returnedReplyEvidence";

export type RuntimePriorityMessageLike = PrioritySourceMessageLike &
  ReturnedReplyMessageLike & {
    imapUid?: string | null;
  };

export type RuntimeMailboxAddressLike = {
  email?: string | null;
};

export type BuildReturnedReplyEvidenceInputOptions = {
  currentMessage: RuntimePriorityMessageLike;
  threadMessages?: RuntimePriorityMessageLike[];
  sentMessages?: RuntimePriorityMessageLike[];
  ownEmailAddresses?: string[];
  connectedMailboxes?: RuntimeMailboxAddressLike[];
  authenticatedUserEmail?: string | null;
};

export type ResolveRuntimePrioritySourceInput = Omit<
  ResolvePrioritySourceInput,
  "hasReturnedReplyEvidence"
> & {
  returnedReplyEvidence?: ReturnedReplyEvidence | null;
  returnedReplyEvidenceInput?: BuildReturnedReplyEvidenceInputOptions | null;
  hasReturnedReplyEvidence?: boolean | null;
};

function normalizeAddress(value: string | null | undefined) {
  const normalizedValue = (value ?? "").trim().toLowerCase();
  const emailMatch = normalizedValue.match(
    /([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})/i,
  );

  return emailMatch?.[1] ?? normalizedValue;
}

export function isSubjectDerivedThreadId(
  message: Pick<RuntimePriorityMessageLike, "threadId" | "subject">,
) {
  const threadId = message.threadId?.trim();

  if (!threadId) {
    return false;
  }

  return threadId.toLowerCase() === normalizeThreadSubject(message.subject);
}

export function resolveExplicitProviderThreadId(
  message: Pick<RuntimePriorityMessageLike, "threadId" | "subject">,
) {
  const threadId = message.threadId?.trim();

  if (!threadId || isSubjectDerivedThreadId(message)) {
    return undefined;
  }

  return threadId;
}

function toReturnedReplyEvidenceMessage(
  message: RuntimePriorityMessageLike,
): ReturnedReplyMessageLike {
  return {
    id: message.id,
    threadId: resolveExplicitProviderThreadId(message),
    subject: message.subject,
    from: message.from,
    sender: message.sender,
    to: message.to,
    cc: message.cc,
    createdAt: message.createdAt,
    timestamp: message.timestamp,
    signal: message.signal,
  };
}

function canCompareForReturnedReplyEvidence(
  currentMessage: RuntimePriorityMessageLike,
  candidateMessage: RuntimePriorityMessageLike,
) {
  const currentThreadId = resolveExplicitProviderThreadId(currentMessage);
  const candidateThreadId = resolveExplicitProviderThreadId(candidateMessage);

  if (currentThreadId) {
    return candidateThreadId === currentThreadId;
  }

  return !candidateThreadId;
}

function buildOwnEmailAddressList({
  ownEmailAddresses,
  connectedMailboxes,
  authenticatedUserEmail,
}: Pick<
  BuildReturnedReplyEvidenceInputOptions,
  "ownEmailAddresses" | "connectedMailboxes" | "authenticatedUserEmail"
>) {
  const addresses = [
    ...(ownEmailAddresses ?? []),
    ...(connectedMailboxes ?? []).map((mailbox) => mailbox.email ?? ""),
    authenticatedUserEmail ?? "",
  ]
    .map(normalizeAddress)
    .filter(Boolean);

  return Array.from(new Set(addresses));
}

export function buildReturnedReplyEvidenceInput(
  options: BuildReturnedReplyEvidenceInputOptions,
): ResolveReturnedReplyEvidenceInput {
  const comparableThreadMessages = (options.threadMessages ?? []).filter((message) =>
    canCompareForReturnedReplyEvidence(options.currentMessage, message),
  );
  const comparableSentMessages = (options.sentMessages ?? []).filter((message) =>
    canCompareForReturnedReplyEvidence(options.currentMessage, message),
  );

  return {
    currentMessage: toReturnedReplyEvidenceMessage(options.currentMessage),
    threadMessages: comparableThreadMessages.map(toReturnedReplyEvidenceMessage),
    sentMessages: comparableSentMessages.map(toReturnedReplyEvidenceMessage),
    ownEmailAddresses: buildOwnEmailAddressList(options),
  };
}

export function resolveRuntimeReturnedReplyEvidence(
  options: BuildReturnedReplyEvidenceInputOptions,
): ReturnedReplyEvidence {
  return resolveReturnedReplyEvidence(buildReturnedReplyEvidenceInput(options));
}

export function resolveRuntimePrioritySource(
  input: ResolveRuntimePrioritySourceInput,
): PrioritySourceResult {
  const returnedReplyEvidence =
    input.returnedReplyEvidence ??
    (input.returnedReplyEvidenceInput
      ? resolveRuntimeReturnedReplyEvidence(input.returnedReplyEvidenceInput)
      : null);

  return resolvePrioritySource({
    ...input,
    hasReturnedReplyEvidence:
      input.hasReturnedReplyEvidence ?? Boolean(returnedReplyEvidence?.hasEvidence),
  });
}
