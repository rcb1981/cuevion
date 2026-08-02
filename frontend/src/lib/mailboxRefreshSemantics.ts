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
