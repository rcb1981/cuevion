import type { ProviderId } from "../types/onboarding";

export type MailboxRefreshResult = "synced" | "skipped" | "failed" | "partial";
export type StartupSyncStatus = "idle" | "running" | "done" | "partial_error";
export type ProviderArchiveCapability = "available" | "unavailable" | "unknown";
export type MailboxRefreshReason =
  | "startup"
  | "mailbox_open"
  | "interval"
  | "manual"
  | "archive_open"
  | "reconcile";

export type MailboxRefreshPlan = {
  shouldFetchInbox: boolean;
  shouldFetchArchive: boolean;
  archiveErrorScope: "background" | "folder" | null;
};

export type GmailArchiveReconciliationCoordinator<MailboxId extends string> = {
  request: (mailboxId: MailboxId) => void;
  drain: (mailboxId: MailboxId) => void;
};

export type GmailInboxAuthorityMessage = {
  serverMailboxId?: unknown;
  providerFolder?: unknown;
  providerMessageId?: unknown;
  labelIds?: unknown;
};

export type GmailInboxAuthority = {
  captureGeneration: (mailboxId: string) => number;
  confirmArchive: (mailboxId: string, providerMessageId: string) => number;
  resetMailbox: (mailboxId: string) => void;
  isCurrentGeneration: (mailboxId: string, generation: number) => boolean;
  isRecentlyArchived: (mailboxId: string, providerMessageId: string) => boolean;
  filterSnapshotMessages: <Message extends GmailInboxAuthorityMessage>(
    mailboxId: string,
    messages: readonly Message[],
  ) => Message[];
  resolveFetchResponse: <Message extends GmailInboxAuthorityMessage>(args: {
    mailboxId: string;
    generationAtFetchStart: number;
    messages: readonly Message[];
  }) =>
    | {
        stale: true;
        messages: [];
        provenReentryProviderMessageIds: [];
      }
    | {
        stale: false;
        messages: Message[];
        provenReentryProviderMessageIds: string[];
  };
};

export type CustomImapInboxAuthorityMessage = {
  serverMailboxId?: unknown;
  providerFolder?: unknown;
  imapUid?: unknown;
  uidValidity?: unknown;
};

export type CustomImapInboxAuthority = {
  captureGeneration: (mailboxId: string) => number;
  confirmSourceRemoval: (
    mailboxId: string,
    uidValidity: string,
    imapUid: string,
  ) => number;
  resetMailbox: (mailboxId: string) => void;
  isCurrentGeneration: (mailboxId: string, generation: number) => boolean;
  isRecentlyRemoved: (
    mailboxId: string,
    uidValidity: string,
    imapUid: string,
  ) => boolean;
  resolveFetchResponse: <Message extends CustomImapInboxAuthorityMessage>(args: {
    mailboxId: string;
    generationAtFetchStart: number;
    uidValidity: unknown;
    messages: readonly Message[];
  }) =>
    | {
        stale: true;
        messages: [];
      }
    | {
        stale: false;
        messages: Message[];
      };
};

const MAX_IMAP_UID = 4_294_967_295;
const CANONICAL_IMAP_UID = /^[1-9][0-9]*$/;
const CANONICAL_UID_VALIDITY = /^[1-9][0-9]{0,19}$/;

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

function isCanonicalUidValidity(value: unknown): value is string {
  return typeof value === "string" && CANONICAL_UID_VALIDITY.test(value);
}

function buildCustomImapRemovalIdentity(
  uidValidity: string,
  imapUid: string,
) {
  return JSON.stringify([uidValidity, imapUid]);
}

function hasExactCurrentCustomImapInboxAuthority<
  Message extends CustomImapInboxAuthorityMessage,
>(mailboxId: string, uidValidity: string, messages: readonly Message[]) {
  const seenUids = new Set<string>();
  for (const message of messages) {
    if (
      message.serverMailboxId !== mailboxId ||
      message.providerFolder !== "INBOX" ||
      message.uidValidity !== uidValidity ||
      !isCanonicalImapUid(message.imapUid) ||
      seenUids.has(message.imapUid)
    ) {
      return false;
    }
    seenUids.add(message.imapUid);
  }
  return true;
}

export function createCustomImapInboxAuthority(): CustomImapInboxAuthority {
  const generationByMailbox = new Map<string, number>();
  const recentlyRemovedByMailbox = new Map<string, Set<string>>();
  const readGeneration = (mailboxId: string) =>
    generationByMailbox.get(mailboxId) ?? 0;
  const readFence = (mailboxId: string) =>
    recentlyRemovedByMailbox.get(mailboxId);

  return {
    captureGeneration: readGeneration,
    confirmSourceRemoval: (mailboxId, uidValidity, imapUid) => {
      if (
        !isExactAuthorityIdentifier(mailboxId) ||
        !isCanonicalUidValidity(uidValidity) ||
        !isCanonicalImapUid(imapUid)
      ) {
        return readGeneration(mailboxId);
      }

      const nextGeneration = readGeneration(mailboxId) + 1;
      generationByMailbox.set(mailboxId, nextGeneration);
      const fence = readFence(mailboxId) ?? new Set<string>();
      fence.add(buildCustomImapRemovalIdentity(uidValidity, imapUid));
      recentlyRemovedByMailbox.set(mailboxId, fence);
      return nextGeneration;
    },
    resetMailbox: (mailboxId) => {
      generationByMailbox.set(mailboxId, readGeneration(mailboxId) + 1);
      recentlyRemovedByMailbox.delete(mailboxId);
    },
    isCurrentGeneration: (mailboxId, generation) =>
      readGeneration(mailboxId) === generation,
    isRecentlyRemoved: (mailboxId, uidValidity, imapUid) =>
      readFence(mailboxId)?.has(
        buildCustomImapRemovalIdentity(uidValidity, imapUid),
      ) ?? false,
    resolveFetchResponse: ({
      mailboxId,
      generationAtFetchStart,
      uidValidity,
      messages,
    }) => {
      if (readGeneration(mailboxId) !== generationAtFetchStart) {
        return { stale: true, messages: [] };
      }

      const fence = readFence(mailboxId);
      if (!fence?.size) {
        return { stale: false, messages: [...messages] };
      }

      if (
        isCanonicalUidValidity(uidValidity) &&
        hasExactCurrentCustomImapInboxAuthority(
          mailboxId,
          uidValidity,
          messages,
        )
      ) {
        const retainedFence = new Set(
          [...fence].filter((identity) => {
            try {
              const parts = JSON.parse(identity);
              return Array.isArray(parts) && parts[0] === uidValidity;
            } catch {
              return true;
            }
          }),
        );
        if (retainedFence.size === 0) {
          recentlyRemovedByMailbox.delete(mailboxId);
        } else if (retainedFence.size !== fence.size) {
          recentlyRemovedByMailbox.set(mailboxId, retainedFence);
        }
      }

      const currentFence = readFence(mailboxId);
      if (!currentFence?.size) {
        return { stale: false, messages: [...messages] };
      }

      return {
        stale: false,
        messages: messages.filter((message) => {
          if (
            message.serverMailboxId !== mailboxId ||
            message.providerFolder !== "INBOX" ||
            !isCanonicalUidValidity(message.uidValidity) ||
            !isCanonicalImapUid(message.imapUid)
          ) {
            return true;
          }
          return !currentFence.has(
            buildCustomImapRemovalIdentity(
              message.uidValidity,
              message.imapUid,
            ),
          );
        }),
      };
    },
  };
}

function isExactAuthorityIdentifier(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value === value.trim()
  );
}

function isExactMailboxProviderMessage(
  message: GmailInboxAuthorityMessage,
  mailboxId: string,
  providerMessageId: string,
) {
  return (
    message.serverMailboxId === mailboxId &&
    message.providerMessageId === providerMessageId
  );
}

function hasExplicitGmailInboxMembership(message: GmailInboxAuthorityMessage) {
  if (message.providerFolder !== "Inbox" || !Array.isArray(message.labelIds)) {
    return false;
  }

  const labelIds = message.labelIds;
  return (
    labelIds.length > 0 &&
    labelIds.every(isExactAuthorityIdentifier) &&
    new Set(labelIds).size === labelIds.length &&
    labelIds.includes("INBOX")
  );
}

export function removeProvenGmailInboxReentriesFromArchive<
  Message extends GmailInboxAuthorityMessage,
>(
  mailboxId: string,
  providerMessageIds: ReadonlySet<string>,
  archiveMessages: Message[],
): Message[] {
  if (providerMessageIds.size === 0) {
    return archiveMessages;
  }

  return archiveMessages.filter((message) => {
    const providerMessageId = message.providerMessageId;
    return !(
      isExactAuthorityIdentifier(providerMessageId) &&
      providerMessageIds.has(providerMessageId) &&
      isExactMailboxProviderMessage(message, mailboxId, providerMessageId)
    );
  });
}

export function createGmailInboxAuthority(): GmailInboxAuthority {
  const generationByMailbox = new Map<string, number>();
  const recentlyArchivedByMailbox = new Map<string, Set<string>>();
  const readGeneration = (mailboxId: string) =>
    generationByMailbox.get(mailboxId) ?? 0;

  const readFence = (mailboxId: string) =>
    recentlyArchivedByMailbox.get(mailboxId);

  const filterSnapshotMessages = <Message extends GmailInboxAuthorityMessage>(
    mailboxId: string,
    messages: readonly Message[],
  ) => {
    const fence = readFence(mailboxId);
    if (!fence?.size) {
      return [...messages];
    }

    return messages.filter((message) => {
      const providerMessageId = message.providerMessageId;
      return !(
        isExactAuthorityIdentifier(providerMessageId) &&
        fence.has(providerMessageId)
      );
    });
  };

  return {
    captureGeneration: readGeneration,
    confirmArchive: (mailboxId, providerMessageId) => {
      const nextGeneration = readGeneration(mailboxId) + 1;
      generationByMailbox.set(mailboxId, nextGeneration);

      const currentFence = readFence(mailboxId) ?? new Set<string>();
      currentFence.add(providerMessageId);
      recentlyArchivedByMailbox.set(mailboxId, currentFence);

      return nextGeneration;
    },
    resetMailbox: (mailboxId) => {
      generationByMailbox.delete(mailboxId);
      recentlyArchivedByMailbox.delete(mailboxId);
    },
    isCurrentGeneration: (mailboxId, generation) =>
      readGeneration(mailboxId) === generation,
    isRecentlyArchived: (mailboxId, providerMessageId) =>
      readFence(mailboxId)?.has(providerMessageId) ?? false,
    filterSnapshotMessages,
    resolveFetchResponse: ({
      mailboxId,
      generationAtFetchStart,
      messages,
    }) => {
      if (readGeneration(mailboxId) !== generationAtFetchStart) {
        return {
          stale: true,
          messages: [],
          provenReentryProviderMessageIds: [],
        };
      }

      const fence = readFence(mailboxId);
      if (!fence?.size) {
        return {
          stale: false,
          messages: [...messages],
          provenReentryProviderMessageIds: [],
        };
      }

      const fencedProviderMessageIds = new Set(fence);
      const candidatesByProviderMessageId = new Map<
        string,
        Array<(typeof messages)[number]>
      >();
      messages.forEach((message) => {
        const providerMessageId = message.providerMessageId;
        if (
          isExactAuthorityIdentifier(providerMessageId) &&
          fencedProviderMessageIds.has(providerMessageId)
        ) {
          const candidates =
            candidatesByProviderMessageId.get(providerMessageId) ?? [];
          candidates.push(message);
          candidatesByProviderMessageId.set(providerMessageId, candidates);
        }
      });

      const provenReentries = new Map<string, (typeof messages)[number]>();
      candidatesByProviderMessageId.forEach((candidates, providerMessageId) => {
        if (
          candidates.length === 1 &&
          isExactMailboxProviderMessage(
            candidates[0],
            mailboxId,
            providerMessageId,
          ) &&
          hasExplicitGmailInboxMembership(candidates[0])
        ) {
          provenReentries.set(providerMessageId, candidates[0]);
          fence.delete(providerMessageId);
        }
      });

      if (fence.size === 0) {
        recentlyArchivedByMailbox.delete(mailboxId);
      }

      return {
        stale: false,
        messages: messages.filter((message) => {
          const providerMessageId = message.providerMessageId;
          if (
            !isExactAuthorityIdentifier(providerMessageId) ||
            !fencedProviderMessageIds.has(providerMessageId)
          ) {
            return true;
          }

          return provenReentries.get(providerMessageId) === message;
        }),
        provenReentryProviderMessageIds: [...provenReentries.keys()],
      };
    },
  };
}

type ArchiveFetchOutcome =
  | null
  | { ok: true }
  | {
      ok: false;
      error?: {
        code?: string;
      };
    };

export type ProviderArchiveRefreshSemantics = {
  capability: ProviderArchiveCapability;
  capabilityMessage: string | null;
  preserveExistingArchive: boolean;
  mailboxSyncError: string | null;
  contributesPartial: boolean;
  shouldRetryArchive: false;
  fallbackFolderName: null;
};

export const ARCHIVE_REFRESH_ERROR_MESSAGE =
  "Archive could not be refreshed safely. Existing Archive messages were kept.";
export const ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE =
  "Archive is not available for this connected mailbox.";

export function resolveMailboxRefreshPlan({
  reason,
  inboxFetchInFlight,
  archiveFetchInFlight,
  hasArchiveSnapshot,
  archiveCapability,
}: {
  reason: MailboxRefreshReason;
  inboxFetchInFlight: boolean;
  archiveFetchInFlight: boolean;
  hasArchiveSnapshot: boolean;
  archiveCapability: ProviderArchiveCapability;
}): MailboxRefreshPlan {
  const shouldFetchInbox = reason !== "archive_open" && !inboxFetchInFlight;
  const archiveRequested =
    reason === "startup" ||
    reason === "manual" ||
    reason === "reconcile" ||
    (reason === "archive_open" && !hasArchiveSnapshot);
  const shouldFetchArchive =
    archiveRequested &&
    !archiveFetchInFlight &&
    !(reason === "archive_open" && archiveCapability === "unavailable");

  return {
    shouldFetchInbox,
    shouldFetchArchive,
    archiveErrorScope:
      shouldFetchArchive && reason === "archive_open"
        ? "folder"
        : shouldFetchArchive
          ? "background"
          : null,
  };
}

export function startIndependentMailboxFetches<TInbox, TArchive>({
  startInbox,
  startArchive,
}: {
  startInbox: () => Promise<TInbox>;
  startArchive?: () => Promise<TArchive>;
}): {
  inboxPromise: Promise<TInbox>;
  archivePromise: Promise<TArchive> | null;
} {
  const inboxPromise = startInbox();
  const archivePromise = startArchive?.() ?? null;

  return { inboxPromise, archivePromise };
}

export function createGmailArchiveReconciliationCoordinator<
  MailboxId extends string,
>({
  pendingMailboxIds,
  runningMailboxIds,
  isInboxFetchInFlight,
  isArchiveFetchInFlight,
  isProviderArchiveMutationInFlight,
  reconcile,
}: {
  pendingMailboxIds: Set<MailboxId>;
  runningMailboxIds: Set<MailboxId>;
  isInboxFetchInFlight: (mailboxId: MailboxId) => boolean;
  isArchiveFetchInFlight: (mailboxId: MailboxId) => boolean;
  isProviderArchiveMutationInFlight: (mailboxId: MailboxId) => boolean;
  reconcile: (mailboxId: MailboxId) => Promise<unknown>;
}): GmailArchiveReconciliationCoordinator<MailboxId> {
  const drain = (mailboxId: MailboxId) => {
    if (
      !pendingMailboxIds.has(mailboxId) ||
      runningMailboxIds.has(mailboxId) ||
      isInboxFetchInFlight(mailboxId) ||
      isArchiveFetchInFlight(mailboxId) ||
      isProviderArchiveMutationInFlight(mailboxId)
    ) {
      return;
    }

    pendingMailboxIds.delete(mailboxId);
    runningMailboxIds.add(mailboxId);

    let reconciliation: Promise<unknown>;
    try {
      reconciliation = Promise.resolve(reconcile(mailboxId));
    } catch (error) {
      reconciliation = Promise.reject(error);
    }

    void reconciliation
      .catch(() => undefined)
      .finally(() => {
        runningMailboxIds.delete(mailboxId);
        drain(mailboxId);
      });
  };

  const request = (mailboxId: MailboxId) => {
    if (runningMailboxIds.has(mailboxId)) {
      return;
    }

    pendingMailboxIds.add(mailboxId);
    drain(mailboxId);
  };

  return { request, drain };
}

export function shouldApplyProviderArchiveResponse({
  requestConnectionKey,
  currentConnectionKey,
  requestConnectionEpoch,
  currentConnectionEpoch,
  archiveStateVersionAtRequest,
  currentArchiveStateVersion,
}: {
  requestConnectionKey: string | null;
  currentConnectionKey: string | null;
  requestConnectionEpoch: number;
  currentConnectionEpoch: number;
  archiveStateVersionAtRequest: string;
  currentArchiveStateVersion: string;
}): boolean {
  return (
    requestConnectionKey !== null &&
    requestConnectionKey === currentConnectionKey &&
    requestConnectionEpoch === currentConnectionEpoch &&
    archiveStateVersionAtRequest === currentArchiveStateVersion
  );
}

export function resolveProviderArchiveRefreshSemantics({
  provider,
  archiveResponse,
  archiveSnapshotApplied,
}: {
  provider: ProviderId | null;
  archiveResponse: ArchiveFetchOutcome;
  archiveSnapshotApplied: boolean;
}): ProviderArchiveRefreshSemantics {
  if (archiveResponse === null) {
    return {
      capability: "unknown",
      capabilityMessage: null,
      preserveExistingArchive: true,
      mailboxSyncError: null,
      contributesPartial: false,
      shouldRetryArchive: false,
      fallbackFolderName: null,
    };
  }

  if (archiveResponse.ok && archiveSnapshotApplied) {
    return {
      capability: "available",
      capabilityMessage: null,
      preserveExistingArchive: false,
      mailboxSyncError: null,
      contributesPartial: false,
      shouldRetryArchive: false,
      fallbackFolderName: null,
    };
  }

  const isExpectedMissingCustomImapArchiveCapability =
    provider === "custom_imap" &&
    archiveResponse.ok === false &&
    archiveResponse.error?.code === "archive_folder_unavailable";

  if (isExpectedMissingCustomImapArchiveCapability) {
    return {
      capability: "unavailable",
      capabilityMessage: ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
      preserveExistingArchive: true,
      mailboxSyncError: null,
      contributesPartial: false,
      shouldRetryArchive: false,
      fallbackFolderName: null,
    };
  }

  return {
    capability: "unknown",
    capabilityMessage: null,
    preserveExistingArchive: true,
    mailboxSyncError: ARCHIVE_REFRESH_ERROR_MESSAGE,
    contributesPartial: true,
    shouldRetryArchive: false,
    fallbackFolderName: null,
  };
}

export function resolveSuccessfulInboxRefreshPresentation({
  inboxWarningMessage,
  archiveSemantics,
}: {
  inboxWarningMessage: string | null;
  archiveSemantics?: ProviderArchiveRefreshSemantics;
}): {
  result: Extract<MailboxRefreshResult, "synced" | "partial">;
  mailboxSyncError: string | null;
} {
  const normalizedInboxWarning = inboxWarningMessage?.trim() || null;
  const mailboxSyncError =
    normalizedInboxWarning ?? archiveSemantics?.mailboxSyncError ?? null;

  return {
    result: mailboxSyncError ? "partial" : "synced",
    mailboxSyncError,
  };
}

export function summarizeStartupMailboxRefreshResults(
  results: readonly MailboxRefreshResult[],
): {
  status: Extract<StartupSyncStatus, "done" | "partial_error">;
  feedbackMessage: "Inbox refresh complete" | "Some inboxes could not be refreshed";
} {
  const hasRefreshError = results.some(
    (result) => result === "failed" || result === "partial",
  );

  return hasRefreshError
    ? {
        status: "partial_error",
        feedbackMessage: "Some inboxes could not be refreshed",
      }
    : {
        status: "done",
        feedbackMessage: "Inbox refresh complete",
      };
}
