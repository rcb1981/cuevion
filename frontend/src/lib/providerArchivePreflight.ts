import { ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE } from "./mailboxRefreshSemantics";

export const PROVIDER_ARCHIVE_INVALID_SOURCE_MESSAGE =
  "Archive needs one exact connected Inbox message with provider identity.";

export const PROVIDER_ARCHIVE_RECONCILIATION_MESSAGE =
  "Mailbox status is still being refreshed. Please wait a moment.";

export const PROVIDER_ARCHIVE_PENDING_MAILBOX_MESSAGE =
  "Archive is already in progress for this mailbox.";

export const PROVIDER_ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE =
  ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE;

export type ProviderArchiveInvalidSourceReason =
  | "selection"
  | "mailbox"
  | "folder"
  | "provider_identity";

export type ProviderArchivePreflightBlock =
  | {
      reason: "invalid_source";
      invalidSourceReason: ProviderArchiveInvalidSourceReason;
      message: string;
    }
  | {
      reason:
        | "reconciliation_running"
        | "capability_unavailable"
        | "mutation_pending";
      message: string;
    };

export function resolveProviderArchivePreflightBlock({
  invalidSourceReason,
  isGmailArchiveReconciliationRunning,
  isProviderArchiveCapabilityUnavailable,
  hasPendingArchiveMutation,
}: {
  invalidSourceReason: ProviderArchiveInvalidSourceReason | null;
  isGmailArchiveReconciliationRunning: boolean;
  isProviderArchiveCapabilityUnavailable: boolean;
  hasPendingArchiveMutation: boolean;
}): ProviderArchivePreflightBlock | null {
  if (isGmailArchiveReconciliationRunning) {
    return {
      reason: "reconciliation_running",
      message: PROVIDER_ARCHIVE_RECONCILIATION_MESSAGE,
    };
  }
  if (invalidSourceReason) {
    return {
      reason: "invalid_source",
      invalidSourceReason,
      message: PROVIDER_ARCHIVE_INVALID_SOURCE_MESSAGE,
    };
  }
  if (isProviderArchiveCapabilityUnavailable) {
    return {
      reason: "capability_unavailable",
      message: PROVIDER_ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
    };
  }
  if (hasPendingArchiveMutation) {
    return {
      reason: "mutation_pending",
      message: PROVIDER_ARCHIVE_PENDING_MAILBOX_MESSAGE,
    };
  }
  return null;
}
