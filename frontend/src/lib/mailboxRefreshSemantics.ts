import type { ProviderId } from "../types/onboarding";

export type MailboxRefreshResult = "synced" | "skipped" | "failed" | "partial";
export type StartupSyncStatus = "idle" | "running" | "done" | "partial_error";
export type ProviderArchiveCapability = "available" | "unavailable" | "unknown";

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

export function resolveProviderArchiveRefreshSemantics({
  provider,
  inboxFetchFullySucceeded,
  archiveResponse,
  archiveSnapshotApplied,
}: {
  provider: ProviderId | null;
  inboxFetchFullySucceeded: boolean;
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
    inboxFetchFullySucceeded &&
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
  archiveSemantics: ProviderArchiveRefreshSemantics;
}): {
  result: Extract<MailboxRefreshResult, "synced" | "partial">;
  mailboxSyncError: string | null;
} {
  const normalizedInboxWarning = inboxWarningMessage?.trim() || null;
  const mailboxSyncError =
    normalizedInboxWarning ?? archiveSemantics.mailboxSyncError;

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
