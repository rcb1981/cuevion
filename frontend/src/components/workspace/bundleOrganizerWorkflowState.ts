export type BundleOrganizerReadState = "read" | "unread";

export type BundleOrganizerWorkflowStateEntry = {
  readState?: BundleOrganizerReadState;
  readAt?: string;
  unreadAt?: string;
  shortlisted?: boolean;
  shortlistedAt?: string;
  manualCategory?: "demo" | "promo";
  manualCategoryAt?: string;
  manualPriority?: boolean;
  manualPriorityAt?: string;
  organizerFollowUp?: boolean;
  organizerFollowUpAt?: string;
  trashed?: boolean;
  trashedAt?: string;
};

export type BundleOrganizerWorkflowState = Record<
  string,
  BundleOrganizerWorkflowStateEntry
>;

export type BundleOrganizerWorkspaceIdentitySource = {
  id: string;
  imapUid?: string | number | null;
  threadId?: string | null;
};

export const BUNDLE_ORGANIZER_WORKFLOW_STATE_CHANGED_EVENT =
  "cuevion-bundle-organizer-workflow-state-changed";

const bundleOrganizerWorkflowStorageKey =
  "cuevion-bundle-organizer-workflow-state";

export function buildBundleOrganizerWorkspaceMessageIdentityKey(
  mailboxId: string,
  message: BundleOrganizerWorkspaceIdentitySource,
) {
  if (message.imapUid) {
    return `${mailboxId}:imap:${message.imapUid}`;
  }

  if (message.threadId) {
    return `${mailboxId}:thread:${message.threadId}`;
  }

  return `${mailboxId}:id:${message.id}`;
}

export function getBundleOrganizerWorkflowIdentityKey(message: {
  id: string;
  identityKey?: string | null;
}) {
  return message.identityKey ?? message.id;
}

export function readBundleOrganizerWorkflowState(): BundleOrganizerWorkflowState {
  if (typeof window === "undefined") {
    return {};
  }

  try {
    const storedValue = window.localStorage.getItem(
      bundleOrganizerWorkflowStorageKey,
    );
    if (!storedValue) {
      return {};
    }

    const parsedValue = JSON.parse(storedValue);
    if (!parsedValue || typeof parsedValue !== "object") {
      return {};
    }

    return parsedValue as BundleOrganizerWorkflowState;
  } catch {
    return {};
  }
}

export function writeBundleOrganizerWorkflowState(
  state: BundleOrganizerWorkflowState,
  options: { notify?: boolean } = {},
) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(
      bundleOrganizerWorkflowStorageKey,
      JSON.stringify(state),
    );

    if (options.notify) {
      window.dispatchEvent(
        new CustomEvent(BUNDLE_ORGANIZER_WORKFLOW_STATE_CHANGED_EVENT),
      );
    }
  } catch {
    // Local workflow state is optional; mailbox data must never depend on it.
  }
}
