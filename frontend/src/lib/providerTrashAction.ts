import { mutateProviderTrashMessage } from "./inboxConnectionApi";

const MAX_PROVIDER_IDENTIFIER_LENGTH = 256;
const MAX_UI_MESSAGE_IDENTIFIER_LENGTH = 512;

export type ProviderTrashMutationRequest = {
  mailboxId: string;
  action: "trash";
  providerMessageId: string;
  sourceFolder: "INBOX";
};

export type ProviderTrashConfirmedResponse = {
  ok: true;
  action: "trash";
  provider: "gmail";
  mailboxId: string;
  providerMessageId: string;
  sourceFolder: "INBOX";
  destinationFolder: "TRASH";
  readback: {
    inSource: false;
    inTrash: true;
  };
};

export type ProviderTrashUncertainResponse = {
  ok: false;
  status: "mutation_unconfirmed";
  action: "trash";
  provider: "gmail";
  mailboxId: string;
  providerMessageId: string;
  sourceFolder: "INBOX";
  destinationFolder: "TRASH";
  error: {
    code: "trash_mutation_unconfirmed";
    message: string;
  };
};

export type ProviderTrashOrdinaryFailureResponse = {
  ok: false;
  error: {
    code: string;
    message: string;
  };
};

export type ProviderTrashMutationResponse =
  | ProviderTrashConfirmedResponse
  | ProviderTrashUncertainResponse
  | ProviderTrashOrdinaryFailureResponse;

export type ProviderTrashMutation = (
  request: ProviderTrashMutationRequest,
) => Promise<ProviderTrashMutationResponse>;

export type ExactGmailTrashSourceMessage = {
  id: string;
  serverMailboxId?: string | null;
  providerFolder?: string | null;
  providerMessageId?: string | null;
};

export type LiveManagedGmailTrashMailbox = {
  id?: unknown;
  provider?: unknown;
  connected?: unknown;
  connectionStatus?: unknown;
};

export type ProviderTrashMutationTarget = {
  ok: true;
  inFlightKey: string;
  request: ProviderTrashMutationRequest;
};

export type ProviderTrashTargetResolution<
  Message extends ExactGmailTrashSourceMessage,
> = {
  sourceMessage: Message;
  target: ProviderTrashMutationTarget;
};

export type ProviderTrashReconciliationCause =
  | "confirmed_success"
  | "mutation_unconfirmed";

export type ProviderTrashReadOnlyReconciliationRequest = {
  mailboxId: string;
  providerMessageId: string;
  sourceFolder: "INBOX";
  cause: ProviderTrashReconciliationCause;
};

export type ProviderTrashReadOnlyReconcile = (
  request: ProviderTrashReadOnlyReconciliationRequest,
) => void | Promise<void>;

export type ProviderTrashConfirmedSourceRemoval = (
  response: ProviderTrashConfirmedResponse,
) => void;

export type ProviderTrashPendingKeysChange = (
  pendingKeys: ReadonlySet<string>,
) => void;

type ProviderTrashCompletedResult = {
  inFlightKey: string;
  request: ProviderTrashMutationRequest;
  response: ProviderTrashMutationResponse;
};

export type ProviderTrashActionResult =
  | (ProviderTrashCompletedResult & {
      classification: "success";
      mutationClassification: "success";
      reconciliationAttempted: true;
      reconciled: true;
    })
  | (ProviderTrashCompletedResult & {
      classification: "uncertain";
      mutationClassification: "uncertain";
      reconciliationAttempted: true;
      reconciled: true;
    })
  | (ProviderTrashCompletedResult & {
      classification: "ordinary_failure";
      mutationClassification: "ordinary_failure";
      reconciliationAttempted: false;
      reconciled: false;
    })
  | (ProviderTrashCompletedResult & {
      classification: "reconciliation_failed";
      mutationClassification: "success" | "uncertain";
      reconciliationAttempted: true;
      reconciled: false;
      error: {
        code: "trash_reconciliation_failed";
        message: string;
      };
    })
  | {
      classification: "blocked";
      reason: "already_pending" | "invalid_target";
      reconciliationAttempted: false;
      reconciled: false;
      inFlightKey?: string;
      request?: ProviderTrashMutationRequest;
    };

export type ProviderTrashCoordinator = {
  trash(target: ProviderTrashMutationTarget): Promise<ProviderTrashActionResult>;
};

export type GmailProviderTrashStateMessage = {
  serverMailboxId?: string | null;
  providerFolder?: string | null;
  providerMessageId?: string | null;
};

export function applyConfirmedGmailTrashSourceRemoval<
  Message extends Pick<
    GmailProviderTrashStateMessage,
    "serverMailboxId" | "providerMessageId"
  >,
  Collections extends { Inbox: Message[] },
>(
  current: Collections,
  target: Pick<ProviderTrashMutationRequest, "mailboxId" | "providerMessageId">,
): { applied: boolean; state: Collections } {
  if (
    !isExactProviderIdentifier(target.mailboxId) ||
    !isConcreteGmailProviderMessageId(target.providerMessageId)
  ) {
    return { applied: false, state: current };
  }

  const exactMatches = current.Inbox.filter(
    (message) =>
      message.serverMailboxId === target.mailboxId &&
      message.providerMessageId === target.providerMessageId,
  );
  if (exactMatches.length !== 1) {
    return { applied: false, state: current };
  }

  const exactMatch = exactMatches[0];
  return {
    applied: true,
    state: {
      ...current,
      Inbox: current.Inbox.filter((message) => message !== exactMatch),
    },
  };
}

function readExactGmailFolderProviderMessageIds<
  Message extends GmailProviderTrashStateMessage,
>(
  messages: readonly Message[],
  mailboxId: string,
  providerFolder: "Inbox" | "Trash",
): Set<string> | null {
  const providerMessageIds = new Set<string>();
  for (const message of messages) {
    const providerMessageId = message.providerMessageId;
    if (
      message.serverMailboxId !== mailboxId ||
      message.providerFolder !== providerFolder ||
      !isConcreteGmailProviderMessageId(providerMessageId) ||
      providerMessageIds.has(providerMessageId)
    ) {
      return null;
    }
    providerMessageIds.add(providerMessageId);
  }
  return providerMessageIds;
}

export function applyGmailProviderTrashFolderReadback<
  Message extends GmailProviderTrashStateMessage,
  Collections extends { Inbox: Message[]; Trash: Message[] },
>(
  current: Collections,
  readback: {
    mailboxId: string;
    Trash: Message[];
  },
): { applied: boolean; state: Collections } {
  if (!isExactProviderIdentifier(readback.mailboxId)) {
    return { applied: false, state: current };
  }
  const trashProviderMessageIds = readExactGmailFolderProviderMessageIds(
    readback.Trash,
    readback.mailboxId,
    "Trash",
  );
  if (!trashProviderMessageIds) {
    return { applied: false, state: current };
  }

  return {
    applied: true,
    state: {
      ...current,
      Inbox: current.Inbox.filter((message) => {
        const providerMessageId = message.providerMessageId;
        return !(
          message.serverMailboxId === readback.mailboxId &&
          isConcreteGmailProviderMessageId(providerMessageId) &&
          trashProviderMessageIds.has(providerMessageId)
        );
      }),
      Trash: [...readback.Trash],
    },
  };
}

export function replaceGmailProviderInboxAndTrashReadback<
  Message extends GmailProviderTrashStateMessage,
  Collections extends { Inbox: Message[]; Trash: Message[] },
>(
  current: Collections,
  readback: {
    mailboxId: string;
    targetProviderMessageId: string;
    mutationConfirmed: boolean;
    Inbox: Message[];
    Trash: Message[];
  },
): { applied: boolean; state: Collections } {
  if (
    !isExactProviderIdentifier(readback.mailboxId) ||
    !isConcreteGmailProviderMessageId(readback.targetProviderMessageId)
  ) {
    return { applied: false, state: current };
  }
  const inboxProviderMessageIds = readExactGmailFolderProviderMessageIds(
    readback.Inbox,
    readback.mailboxId,
    "Inbox",
  );
  const trashProviderMessageIds = readExactGmailFolderProviderMessageIds(
    readback.Trash,
    readback.mailboxId,
    "Trash",
  );
  if (
    !inboxProviderMessageIds ||
    !trashProviderMessageIds ||
    [...inboxProviderMessageIds].some((providerMessageId) =>
      trashProviderMessageIds.has(providerMessageId),
    )
  ) {
    return { applied: false, state: current };
  }

  const targetInInbox = inboxProviderMessageIds.has(
    readback.targetProviderMessageId,
  );
  const targetInTrash = trashProviderMessageIds.has(
    readback.targetProviderMessageId,
  );
  if (
    readback.mutationConfirmed
      ? targetInInbox || !targetInTrash
      : targetInInbox === targetInTrash
  ) {
    return { applied: false, state: current };
  }

  return {
    applied: true,
    state: {
      ...current,
      Inbox: [...readback.Inbox],
      Trash: [...readback.Trash],
    },
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  expectedKeys: readonly string[],
) {
  const actualKeys = Object.keys(value).sort();
  const sortedExpectedKeys = [...expectedKeys].sort();
  return (
    actualKeys.length === sortedExpectedKeys.length &&
    actualKeys.every((key, index) => key === sortedExpectedKeys[index])
  );
}

function hasNoControlCharacters(value: string) {
  return !Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return codePoint < 32 || codePoint === 127;
  });
}

function isExactProviderIdentifier(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= MAX_PROVIDER_IDENTIFIER_LENGTH &&
    value === value.trim() &&
    hasNoControlCharacters(value)
  );
}

function isExactUiMessageIdentifier(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= MAX_UI_MESSAGE_IDENTIFIER_LENGTH &&
    value === value.trim() &&
    hasNoControlCharacters(value)
  );
}

function isConcreteGmailProviderMessageId(value: unknown): value is string {
  if (
    !isExactProviderIdentifier(value) ||
    !/^[\x20-\x7e]+$/.test(value)
  ) {
    return false;
  }

  const lowered = value.toLowerCase();
  return (
    !value.includes("@") &&
    !value.includes("<") &&
    !value.includes(">") &&
    !["imap-uid-", "rfc-", "thread-"].some((prefix) =>
      lowered.startsWith(prefix),
    )
  );
}

export function buildProviderTrashInFlightKey(
  request: ProviderTrashMutationRequest,
) {
  return JSON.stringify([
    "trash",
    "google",
    request.mailboxId,
    request.sourceFolder,
    request.providerMessageId,
  ]);
}

export function hasPendingProviderTrashForMailbox(
  pendingKeys: ReadonlySet<string>,
  mailboxId: string,
) {
  return [...pendingKeys].some((key) => {
    try {
      const parts = JSON.parse(key);
      return (
        Array.isArray(parts) &&
        parts.length === 5 &&
        parts[0] === "trash" &&
        parts[1] === "google" &&
        parts[2] === mailboxId &&
        parts[3] === "INBOX"
      );
    } catch {
      return false;
    }
  });
}

export function resolveExactGmailTrashMutationTarget<
  Message extends ExactGmailTrashSourceMessage,
>({
  isLiveMailbox,
  selectedMessageIds,
  sourceFolder,
  sourceManagedMailbox,
  sourceMessages,
}: {
  isLiveMailbox: unknown;
  selectedMessageIds: readonly string[];
  sourceFolder: unknown;
  sourceManagedMailbox: LiveManagedGmailTrashMailbox | null | undefined;
  sourceMessages: readonly Message[];
}): ProviderTrashTargetResolution<Message> | null {
  if (
    isLiveMailbox !== true ||
    sourceFolder !== "Inbox" ||
    !sourceManagedMailbox ||
    sourceManagedMailbox.provider !== "google" ||
    sourceManagedMailbox.connected !== true ||
    sourceManagedMailbox.connectionStatus !== "connected" ||
    !isExactProviderIdentifier(sourceManagedMailbox.id) ||
    selectedMessageIds.length !== 1 ||
    !isExactUiMessageIdentifier(selectedMessageIds[0])
  ) {
    return null;
  }

  const exactSourceMessageMatches = sourceMessages.filter(
    (message) => message.id === selectedMessageIds[0],
  );
  if (exactSourceMessageMatches.length !== 1) {
    return null;
  }

  const sourceMessage = exactSourceMessageMatches[0];
  if (
    sourceMessage.serverMailboxId !== sourceManagedMailbox.id ||
    sourceMessage.providerFolder !== "Inbox" ||
    !isConcreteGmailProviderMessageId(sourceMessage.providerMessageId)
  ) {
    return null;
  }

  const request: ProviderTrashMutationRequest = {
    mailboxId: sourceManagedMailbox.id,
    action: "trash",
    providerMessageId: sourceMessage.providerMessageId,
    sourceFolder: "INBOX",
  };
  return {
    sourceMessage,
    target: {
      ok: true,
      request,
      inFlightKey: buildProviderTrashInFlightKey(request),
    },
  };
}

function isExactProviderTrashMutationRequest(
  request: unknown,
): request is ProviderTrashMutationRequest {
  return (
    isRecord(request) &&
    hasExactKeys(request, [
      "mailboxId",
      "action",
      "providerMessageId",
      "sourceFolder",
    ]) &&
    isExactProviderIdentifier(request.mailboxId) &&
    request.action === "trash" &&
    isConcreteGmailProviderMessageId(request.providerMessageId) &&
    request.sourceFolder === "INBOX"
  );
}

function isExactProviderTrashMutationTarget(
  target: unknown,
): target is ProviderTrashMutationTarget {
  if (
    !isRecord(target) ||
    !hasExactKeys(target, ["ok", "inFlightKey", "request"]) ||
    target.ok !== true ||
    typeof target.inFlightKey !== "string" ||
    !isExactProviderTrashMutationRequest(target.request)
  ) {
    return false;
  }

  return target.inFlightKey === buildProviderTrashInFlightKey(target.request);
}

function isStrictConfirmedResponse(
  response: unknown,
  request: ProviderTrashMutationRequest,
): response is ProviderTrashConfirmedResponse {
  if (
    !isRecord(response) ||
    !hasExactKeys(response, [
      "ok",
      "action",
      "provider",
      "mailboxId",
      "providerMessageId",
      "sourceFolder",
      "destinationFolder",
      "readback",
    ]) ||
    response.ok !== true ||
    response.action !== "trash" ||
    response.provider !== "gmail" ||
    response.mailboxId !== request.mailboxId ||
    response.providerMessageId !== request.providerMessageId ||
    response.sourceFolder !== "INBOX" ||
    response.destinationFolder !== "TRASH" ||
    !isRecord(response.readback) ||
    !hasExactKeys(response.readback, ["inSource", "inTrash"])
  ) {
    return false;
  }

  return (
    response.readback.inSource === false && response.readback.inTrash === true
  );
}

function isStrictUncertainResponse(
  response: unknown,
  request: ProviderTrashMutationRequest,
): response is ProviderTrashUncertainResponse {
  if (
    !isRecord(response) ||
    !hasExactKeys(response, [
      "ok",
      "status",
      "action",
      "provider",
      "mailboxId",
      "providerMessageId",
      "sourceFolder",
      "destinationFolder",
      "error",
    ]) ||
    response.ok !== false ||
    response.status !== "mutation_unconfirmed" ||
    response.action !== "trash" ||
    response.provider !== "gmail" ||
    response.mailboxId !== request.mailboxId ||
    response.providerMessageId !== request.providerMessageId ||
    response.sourceFolder !== "INBOX" ||
    response.destinationFolder !== "TRASH" ||
    !isRecord(response.error) ||
    !hasExactKeys(response.error, ["code", "message"])
  ) {
    return false;
  }

  return (
    response.error.code === "trash_mutation_unconfirmed" &&
    typeof response.error.message === "string" &&
    response.error.message.length > 0
  );
}

function sanitizeOrdinaryFailure(
  response: unknown,
): ProviderTrashOrdinaryFailureResponse {
  if (
    isRecord(response) &&
    hasExactKeys(response, ["ok", "error"]) &&
    response.ok === false &&
    isRecord(response.error) &&
    hasExactKeys(response.error, ["code", "message"]) &&
    typeof response.error.code === "string" &&
    response.error.code.length > 0 &&
    typeof response.error.message === "string" &&
    response.error.message.length > 0
  ) {
    return {
      ok: false,
      error: {
        code: response.error.code,
        message: response.error.message,
      },
    };
  }

  return {
    ok: false,
    error: {
      code: "trash_mutation_failed",
      message: "Could not move this message to Trash safely.",
    },
  };
}

function uncertainResponseForException(
  request: ProviderTrashMutationRequest,
): ProviderTrashUncertainResponse {
  return {
    ok: false,
    status: "mutation_unconfirmed",
    action: "trash",
    provider: "gmail",
    mailboxId: request.mailboxId,
    providerMessageId: request.providerMessageId,
    sourceFolder: "INBOX",
    destinationFolder: "TRASH",
    error: {
      code: "trash_mutation_unconfirmed",
      message:
        "Trash may have completed, but provider confirmation was not definitive.",
    },
  };
}

const defaultProviderTrashMutation: ProviderTrashMutation = async (request) =>
  (await mutateProviderTrashMessage(request)) as ProviderTrashMutationResponse;

export function createProviderTrashCoordinator({
  pendingKeys = new Set<string>(),
  mutate = defaultProviderTrashMutation,
  reconcileReadOnly,
  applyConfirmedSourceRemoval,
  onPendingKeysChange,
}: {
  pendingKeys?: Set<string>;
  mutate?: ProviderTrashMutation;
  reconcileReadOnly: ProviderTrashReadOnlyReconcile;
  applyConfirmedSourceRemoval?: ProviderTrashConfirmedSourceRemoval;
  onPendingKeysChange?: ProviderTrashPendingKeysChange;
}): ProviderTrashCoordinator {
  const notifyPendingKeysChange = () => {
    try {
      onPendingKeysChange?.(pendingKeys);
    } catch {
      // Pending presentation must not affect the provider mutation contract.
    }
  };

  return {
    async trash(target) {
      if (!isExactProviderTrashMutationTarget(target)) {
        return {
          classification: "blocked",
          reason: "invalid_target",
          reconciliationAttempted: false,
          reconciled: false,
        };
      }

      const { inFlightKey, request } = target;
      if (pendingKeys.has(inFlightKey)) {
        return {
          classification: "blocked",
          reason: "already_pending",
          inFlightKey,
          request,
          reconciliationAttempted: false,
          reconciled: false,
        };
      }

      pendingKeys.add(inFlightKey);
      notifyPendingKeysChange();
      let mutationPending = true;
      const releaseMutationPending = () => {
        if (!mutationPending) {
          return;
        }
        mutationPending = false;
        pendingKeys.delete(inFlightKey);
        notifyPendingKeysChange();
      };
      try {
        let response: ProviderTrashMutationResponse;
        try {
          response = await mutate(request);
        } catch {
          response = uncertainResponseForException(request);
        }

        const strictConfirmedResponse = isStrictConfirmedResponse(
          response,
          request,
        )
          ? response
          : null;
        const mutationClassification = strictConfirmedResponse
          ? "success"
          : isStrictUncertainResponse(response, request)
            ? "uncertain"
            : "ordinary_failure";

        if (mutationClassification === "ordinary_failure") {
          return {
            classification: "ordinary_failure",
            mutationClassification,
            inFlightKey,
            request,
            response: sanitizeOrdinaryFailure(response),
            reconciliationAttempted: false,
            reconciled: false,
          };
        }

        if (strictConfirmedResponse) {
          try {
            applyConfirmedSourceRemoval?.(strictConfirmedResponse);
          } catch {
            // Strict provider success remains authoritative; read-only
            // reconciliation still gets one chance to publish provider state.
          }
          releaseMutationPending();
        }

        try {
          await reconcileReadOnly({
            mailboxId: request.mailboxId,
            providerMessageId: request.providerMessageId,
            sourceFolder: request.sourceFolder,
            cause:
              mutationClassification === "success"
                ? "confirmed_success"
                : "mutation_unconfirmed",
          });
        } catch {
          return {
            classification: "reconciliation_failed",
            mutationClassification,
            inFlightKey,
            request,
            response,
            reconciliationAttempted: true,
            reconciled: false,
            error: {
              code: "trash_reconciliation_failed",
              message:
                "Trash provider state could not be refreshed safely.",
            },
          };
        }

        if (mutationClassification === "success") {
          return {
            classification: "success",
            mutationClassification: "success",
            inFlightKey,
            request,
            response,
            reconciliationAttempted: true,
            reconciled: true,
          };
        }

        return {
          classification: "uncertain",
          mutationClassification: "uncertain",
          inFlightKey,
          request,
          response,
          reconciliationAttempted: true,
          reconciled: true,
        };
      } finally {
        releaseMutationPending();
      }
    },
  };
}
