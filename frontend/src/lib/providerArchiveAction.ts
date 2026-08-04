import {
  isProviderArchiveMutationSuccessResponse,
  sanitizeProviderArchiveMutationUncertainResponse,
} from "./inboxConnectionApi";
import {
  PROVIDER_ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
} from "./providerArchivePreflight";

const MAX_PROVIDER_IDENTIFIER_LENGTH = 256;
const MAX_IMAP_UID = 4_294_967_295;
const CANONICAL_IMAP_UID = /^[1-9][0-9]*$/;
const CANONICAL_UID_VALIDITY = /^[1-9][0-9]{0,19}$/;

export type ProviderArchiveCandidate = {
  provider: string;
  mailboxId: unknown;
  folder?: unknown;
  providerMessageId?: unknown;
  imapUid?: unknown;
  uidValidity?: unknown;
};

export type ExactGmailArchiveSourceMessage = {
  id: string;
  serverMailboxId?: string | null;
  providerFolder?: string | null;
  providerMessageId?: string | null;
};

export type ProviderArchiveMutationRequest =
  | {
      mailboxId: string;
      messageId: string;
      action: "archive";
    }
  | {
      mailboxId: string;
      folder: "INBOX";
      uid: string;
      uidValidity: string;
      action: "archive";
    };

export type ProviderArchiveMutationResponse = {
  ok: boolean;
  status?: string;
  action?: string;
  mailboxId?: string;
  archivedMessageIdentity?: unknown;
  delta?: unknown;
  folders?: unknown;
  error?: {
    code?: string;
    message?: string;
  };
};

export type ProviderArchiveMutation = (
  request: ProviderArchiveMutationRequest,
) => Promise<ProviderArchiveMutationResponse>;

export type ProviderArchiveBlockReason =
  | "invalid_mailbox_id"
  | "unsupported_provider"
  | "invalid_gmail_source_folder"
  | "missing_gmail_provider_message_id"
  | "invalid_imap_source_folder"
  | "invalid_imap_uid"
  | "invalid_imap_uid_validity"
  | "already_pending";

export type ProviderArchiveMutationTarget = {
  ok: true;
  inFlightKey: string;
  request: ProviderArchiveMutationRequest;
};

export type ProviderArchiveBlockedTarget = {
  ok: false;
  classification: "blocked";
  reason: Exclude<ProviderArchiveBlockReason, "already_pending">;
};

export type ProviderArchiveTargetResult =
  | ProviderArchiveMutationTarget
  | ProviderArchiveBlockedTarget;

export type ProviderArchiveResult =
  | {
      classification:
        | "success"
        | "capability_unavailable"
        | "ordinary_failure"
        | "uncertain";
      inFlightKey: string;
      request: ProviderArchiveMutationRequest;
      response: ProviderArchiveMutationResponse;
    }
  | {
      classification: "blocked";
      reason: ProviderArchiveBlockReason;
      inFlightKey?: string;
      request?: ProviderArchiveMutationRequest;
    };

export type ProviderArchiveCoordinator = {
  archive(candidate: ProviderArchiveCandidate): Promise<ProviderArchiveResult>;
  execute(
    candidate: ProviderArchiveCandidate,
    applySuccess: (response: ProviderArchiveMutationResponse) => boolean,
  ): Promise<ProviderArchiveExecutionResult>;
};

export type ProviderArchiveExecutionResult = ProviderArchiveResult & {
  applied: boolean;
};

export function buildProviderArchiveStateIdentity(message: {
  serverMailboxId?: string | null;
  providerFolder?: string | null;
  providerMessageId?: string | null;
  imapUid?: string | null;
  uidValidity?: string | null;
}) {
  if (
    message.serverMailboxId &&
    message.providerFolder &&
    message.providerMessageId
  ) {
    return [
      "gmail",
      message.serverMailboxId,
      message.providerFolder,
      message.providerMessageId,
    ].join("::");
  }
  if (
    message.serverMailboxId &&
    message.providerFolder &&
    message.imapUid &&
    message.uidValidity
  ) {
    return [
      "imap",
      message.serverMailboxId,
      message.providerFolder,
      message.uidValidity,
      message.imapUid,
    ].join("::");
  }
  return null;
}

function isExactProviderIdentifier(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= MAX_PROVIDER_IDENTIFIER_LENGTH &&
    value === value.trim() &&
    !Array.from(value).some((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint < 32 || codePoint === 127;
    })
  );
}

function isCanonicalImapUid(value: unknown): value is string {
  if (typeof value !== "string" || !CANONICAL_IMAP_UID.test(value)) {
    return false;
  }

  const maximum = String(MAX_IMAP_UID);
  return (
    value.length < maximum.length ||
    (value.length === maximum.length && value <= maximum)
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
      lowered.startsWith(prefix))
  );
}

function isCanonicalUidValidity(value: unknown): value is string {
  return typeof value === "string" && CANONICAL_UID_VALIDITY.test(value);
}

function buildInFlightKey(
  provider: "google" | "custom_imap",
  request: ProviderArchiveMutationRequest,
) {
  return JSON.stringify(
    "messageId" in request
      ? ["archive", provider, request.mailboxId, "Inbox", request.messageId]
      : [
          "archive",
          provider,
          request.mailboxId,
          request.folder,
          request.uidValidity,
          request.uid,
        ],
  );
}

export function hasPendingProviderArchiveForMailbox(
  pendingKeys: ReadonlySet<string>,
  mailboxId: string,
) {
  return [...pendingKeys].some((key) => {
    try {
      const parts = JSON.parse(key);
      return (
        Array.isArray(parts) &&
        parts[0] === "archive" &&
        parts[2] === mailboxId
      );
    } catch {
      return false;
    }
  });
}

export function buildProviderArchiveMutationTarget(
  candidate: ProviderArchiveCandidate,
): ProviderArchiveTargetResult {
  if (!isExactProviderIdentifier(candidate.mailboxId)) {
    return {
      ok: false,
      classification: "blocked",
      reason: "invalid_mailbox_id",
    };
  }

  if (candidate.provider === "google") {
    if (candidate.folder !== "Inbox") {
      return {
        ok: false,
        classification: "blocked",
        reason: "invalid_gmail_source_folder",
      };
    }
    if (!isConcreteGmailProviderMessageId(candidate.providerMessageId)) {
      return {
        ok: false,
        classification: "blocked",
        reason: "missing_gmail_provider_message_id",
      };
    }

    const request: ProviderArchiveMutationRequest = {
      mailboxId: candidate.mailboxId,
      messageId: candidate.providerMessageId,
      action: "archive",
    };
    return {
      ok: true,
      request,
      inFlightKey: buildInFlightKey("google", request),
    };
  }

  if (candidate.provider !== "custom_imap") {
    return {
      ok: false,
      classification: "blocked",
      reason: "unsupported_provider",
    };
  }

  if (candidate.folder !== "INBOX") {
    return {
      ok: false,
      classification: "blocked",
      reason: "invalid_imap_source_folder",
    };
  }
  if (!isCanonicalImapUid(candidate.imapUid)) {
    return {
      ok: false,
      classification: "blocked",
      reason: "invalid_imap_uid",
    };
  }
  if (!isCanonicalUidValidity(candidate.uidValidity)) {
    return {
      ok: false,
      classification: "blocked",
      reason: "invalid_imap_uid_validity",
    };
  }

  const request: ProviderArchiveMutationRequest = {
    mailboxId: candidate.mailboxId,
    folder: "INBOX",
    uid: candidate.imapUid,
    uidValidity: candidate.uidValidity,
    action: "archive",
  };
  return {
    ok: true,
    request,
    inFlightKey: buildInFlightKey("custom_imap", request),
  };
}

export function resolveExactGmailArchiveMutationTarget<
  Message extends ExactGmailArchiveSourceMessage,
>({
  selectedMessageIds,
  sourceFolder,
  sourceMailboxId,
  sourceMessages,
}: {
  selectedMessageIds: readonly string[];
  sourceFolder: unknown;
  sourceMailboxId: string;
  sourceMessages: readonly Message[];
}): {
  sourceMessage: Message;
  candidate: ProviderArchiveCandidate;
  target: ProviderArchiveMutationTarget;
} | null {
  if (selectedMessageIds.length !== 1 || sourceFolder !== "Inbox") {
    return null;
  }

  const exactSourceMessageMatches = sourceMessages.filter(
    (message) => message.id === selectedMessageIds[0],
  );
  if (exactSourceMessageMatches.length !== 1) {
    return null;
  }

  const sourceMessage = exactSourceMessageMatches[0];
  if (sourceMessage.serverMailboxId !== sourceMailboxId) {
    return null;
  }

  const candidate: ProviderArchiveCandidate = {
    provider: "google",
    mailboxId: sourceMessage.serverMailboxId,
    folder: sourceMessage.providerFolder,
    providerMessageId: sourceMessage.providerMessageId,
  };
  const target = buildProviderArchiveMutationTarget(candidate);
  if (!target.ok) {
    return null;
  }

  return { sourceMessage, candidate, target };
}

function validateMutationResponse(
  response: ProviderArchiveMutationResponse,
  request: ProviderArchiveMutationRequest,
): {
  classification:
    | "success"
    | "capability_unavailable"
    | "ordinary_failure"
    | "uncertain";
  response: ProviderArchiveMutationResponse;
} {
  const uncertain = sanitizeProviderArchiveMutationUncertainResponse(
    response,
    request,
  );
  if (uncertain) {
    return {
      classification: "uncertain",
      response: uncertain,
    };
  }
  if (
    isCustomImapArchiveCapabilityUnavailableResponse(response, request)
  ) {
    return {
      classification: "capability_unavailable",
      response: {
        ok: false,
        error: {
          code: "archive_folder_unavailable",
          message: PROVIDER_ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
        },
      },
    };
  }
  if (isProviderArchiveMutationSuccessResponse(response, request)) {
    return {
      classification: "success",
      response,
    };
  }
  return {
    classification: "ordinary_failure",
    response: {
      ok: false,
      error: {
        code: "archive_mutation_failed",
        message: "Could not archive this message.",
      },
    },
  };
}

export function replaceProviderArchiveReadback<
  Message,
  Collections extends {
    Inbox: Message[];
    Archive: Message[];
  },
>(
  current: Collections,
  readback: {
    Inbox: readonly Message[];
    Archive: readonly Message[];
  },
): Collections {
  return {
    ...current,
    Inbox: [...readback.Inbox],
    Archive: [...readback.Archive],
  };
}

type GmailProviderArchiveStateMessage = {
  serverMailboxId?: string | null;
  providerFolder?: string | null;
  providerMessageId?: string | null;
};

function isExactGmailProviderArchiveStateIdentity(
  message: GmailProviderArchiveStateMessage,
  mailboxId: string,
  providerMessageId: string,
) {
  return (
    message.serverMailboxId === mailboxId &&
    message.providerMessageId === providerMessageId
  );
}

export function applyGmailProviderArchiveDelta<
  Message extends GmailProviderArchiveStateMessage,
  Collections extends {
    Inbox: Message[];
    Archive: Message[];
  },
>(
  current: Collections,
  delta: {
    mailboxId: string;
    removeProviderMessageId: string;
    upsertMessage: Message;
  },
): {
  applied: boolean;
  state: Collections;
} {
  if (
    !isExactProviderIdentifier(delta.mailboxId) ||
    !isConcreteGmailProviderMessageId(delta.removeProviderMessageId) ||
    delta.upsertMessage.providerFolder !== "Archive" ||
    !isExactGmailProviderArchiveStateIdentity(
      delta.upsertMessage,
      delta.mailboxId,
      delta.removeProviderMessageId,
    )
  ) {
    return {
      applied: false,
      state: current,
    };
  }

  let inboxMatchCount = 0;
  const nextInbox = current.Inbox.filter((message) => {
    const matches = isExactGmailProviderArchiveStateIdentity(
      message,
      delta.mailboxId,
      delta.removeProviderMessageId,
    );
    if (matches) {
      inboxMatchCount += 1;
    }
    return !matches;
  });
  if (inboxMatchCount !== 1) {
    return {
      applied: false,
      state: current,
    };
  }

  const nextArchive = current.Archive.filter(
    (message) =>
      !isExactGmailProviderArchiveStateIdentity(
        message,
        delta.mailboxId,
        delta.removeProviderMessageId,
      ),
  );
  nextArchive.push(delta.upsertMessage);

  return {
    applied: true,
    state: {
      ...current,
      Inbox: nextInbox,
      Archive: nextArchive,
    },
  };
}

export function replaceProviderArchiveFolder<
  Message,
  Collections extends {
    Archive: Message[];
  },
>(
  current: Collections,
  archiveMessages: readonly Message[],
): Collections {
  return {
    ...current,
    Archive: [...archiveMessages],
  };
}

export function applyProviderArchiveFolderReadback<
  Message,
  Collections extends {
    Archive: Message[];
  },
>(
  current: Collections,
  archiveMessages: readonly Message[] | null | undefined,
): {
  applied: boolean;
  state: Collections;
} {
  if (!archiveMessages) {
    return {
      applied: false,
      state: current,
    };
  }
  return {
    applied: true,
    state: replaceProviderArchiveFolder(current, archiveMessages),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isCustomImapArchiveCapabilityUnavailableResponse(
  response: unknown,
  request: ProviderArchiveMutationRequest,
) {
  if (
    "messageId" in request ||
    !isRecord(response) ||
    response.ok !== false ||
    !isRecord(response.error)
  ) {
    return false;
  }

  return response.error.code === "archive_folder_unavailable";
}

export function filterLegacyArchiveHydration<Message>(
  stored: unknown,
  mailboxIds: readonly string[],
  providerAuthoritativeMailboxIds: ReadonlySet<string>,
): Partial<Record<string, Message[]>> {
  if (!isRecord(stored)) {
    return {};
  }

  return Object.fromEntries(
    mailboxIds.flatMap((mailboxId) => {
      const messages = stored[mailboxId];
      return !providerAuthoritativeMailboxIds.has(mailboxId) &&
        Array.isArray(messages)
        ? [[mailboxId, [...messages] as Message[]]]
        : [];
    }),
  );
}

export function mergeLegacyArchiveStorage<Message>(
  stored: unknown,
  localArchiveMessages: Readonly<Record<string, readonly Message[]>>,
  providerAuthoritativeMailboxIds: ReadonlySet<string>,
): Partial<Record<string, Message[]>> {
  const next: Partial<Record<string, Message[]>> = isRecord(stored)
    ? Object.fromEntries(
        Object.entries(stored).map(([mailboxId, messages]) => [
          mailboxId,
          Array.isArray(messages) ? [...messages] : messages,
        ]),
      ) as Partial<Record<string, Message[]>>
    : {};

  Object.entries(localArchiveMessages).forEach(([mailboxId, messages]) => {
    if (!providerAuthoritativeMailboxIds.has(mailboxId)) {
      next[mailboxId] = [...messages];
    }
  });
  return next;
}

export function createProviderArchiveCoordinator({
  mutate,
  pendingKeys = new Set<string>(),
}: {
  mutate: ProviderArchiveMutation;
  pendingKeys?: Set<string>;
}): ProviderArchiveCoordinator {
  const executeCandidate = async (
    candidate: ProviderArchiveCandidate,
    applySuccess?: (response: ProviderArchiveMutationResponse) => boolean,
  ): Promise<ProviderArchiveExecutionResult> => {
    const target = buildProviderArchiveMutationTarget(candidate);
    if (!target.ok) {
      return {
        classification: "blocked",
        reason: target.reason,
        applied: false,
      };
    }

    if (pendingKeys.has(target.inFlightKey)) {
      return {
        classification: "blocked",
        reason: "already_pending",
        inFlightKey: target.inFlightKey,
        request: target.request,
        applied: false,
      };
    }

    pendingKeys.add(target.inFlightKey);
    try {
      const response = await mutate(target.request);
      const validated = validateMutationResponse(response, target.request);
      const result: ProviderArchiveResult = {
        classification: validated.classification,
        inFlightKey: target.inFlightKey,
        request: target.request,
        response: validated.response,
      };
      if (result.classification !== "success" || !applySuccess) {
        return {
          ...result,
          applied: false,
        };
      }

      let applied = false;
      try {
        applied = applySuccess(validated.response);
      } catch {
        applied = false;
      }
      return {
        ...result,
        applied,
      };
    } catch {
      return {
        classification: "ordinary_failure",
        inFlightKey: target.inFlightKey,
        request: target.request,
        response: {
          ok: false,
          error: {
            code: "archive_mutation_failed",
            message: "Could not archive this message.",
          },
        },
        applied: false,
      };
    } finally {
      pendingKeys.delete(target.inFlightKey);
    }
  };

  return {
    async archive(candidate) {
      const { applied: _applied, ...result } =
        await executeCandidate(candidate);
      return result as ProviderArchiveResult;
    },
    execute(candidate, applySuccess) {
      return executeCandidate(candidate, applySuccess);
    },
  };
}

export async function executeProviderArchiveAction({
  coordinator,
  candidate,
  applySuccess,
}: {
  coordinator: ProviderArchiveCoordinator;
  candidate: ProviderArchiveCandidate;
  applySuccess: (response: ProviderArchiveMutationResponse) => boolean;
}): Promise<ProviderArchiveExecutionResult> {
  return coordinator.execute(candidate, applySuccess);
}
