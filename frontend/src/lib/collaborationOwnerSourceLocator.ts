export type CollaborationOwnerLookupSourceRef =
  | {
      providerMessageId: string;
    }
  | {
      folder: "INBOX";
      uidValidity: string;
      imapUid: string;
    };

export type CollaborationOwnerSourceLocator = {
  mailboxId: string;
  sourceRef: CollaborationOwnerLookupSourceRef;
};

type ManagedMailboxAuthority = {
  id: unknown;
  provider: unknown;
  connected: unknown;
  connectionStatus: unknown;
};

type ThreadIdentityContext = {
  mailboxId?: unknown;
  provider?: unknown;
  folder?: unknown;
  uidValidity?: unknown;
};

type SourceMessageIdentity = {
  id?: unknown;
  serverMailboxId?: unknown;
  providerFolder?: unknown;
  providerMessageId?: unknown;
  uidValidity?: unknown;
  imapUid?: unknown;
  threadIdentityContext?: ThreadIdentityContext | null;
};

export type CollaborationOwnerSourceLocatorInput = {
  workspaceDataMode: unknown;
  hasAuthenticatedMemberAuthority: unknown;
  managedMailbox: ManagedMailboxAuthority | null;
  sourceMailboxId: unknown;
  trustedFolder: unknown;
  trustedUidValidity?: unknown;
  message: SourceMessageIdentity | null;
};

const CANONICAL_POSITIVE_DECIMAL_PATTERN = /^[1-9][0-9]*$/;
const EXACT_PROVIDER_MESSAGE_ID_PATTERN = /^\S+$/;

function isCanonicalPositiveDecimal(value: unknown): value is string {
  return (
    typeof value === "string" && CANONICAL_POSITIVE_DECIMAL_PATTERN.test(value)
  );
}

function hasExactMailboxBinding(
  value: unknown,
  mailboxId: string,
): boolean {
  return value === undefined || value === mailboxId;
}

export function deriveCollaborationOwnerSourceLocator({
  workspaceDataMode,
  hasAuthenticatedMemberAuthority,
  managedMailbox,
  sourceMailboxId,
  trustedFolder,
  trustedUidValidity,
  message,
}: CollaborationOwnerSourceLocatorInput): CollaborationOwnerSourceLocator | null {
  if (
    workspaceDataMode !== "live" ||
    hasAuthenticatedMemberAuthority !== true ||
    !managedMailbox ||
    typeof sourceMailboxId !== "string" ||
    sourceMailboxId.length === 0 ||
    sourceMailboxId !== sourceMailboxId.trim() ||
    managedMailbox.id !== sourceMailboxId ||
    managedMailbox.connected !== true ||
    managedMailbox.connectionStatus !== "connected" ||
    !message ||
    !hasExactMailboxBinding(message.serverMailboxId, sourceMailboxId)
  ) {
    return null;
  }

  const threadContext = message.threadIdentityContext;
  if (
    threadContext &&
    (threadContext.mailboxId !== sourceMailboxId ||
      threadContext.provider !== managedMailbox.provider)
  ) {
    return null;
  }

  if (managedMailbox.provider === "google") {
    if (
      typeof message.providerMessageId !== "string" ||
      !EXACT_PROVIDER_MESSAGE_ID_PATTERN.test(message.providerMessageId)
    ) {
      return null;
    }

    return {
      mailboxId: sourceMailboxId,
      sourceRef: { providerMessageId: message.providerMessageId },
    };
  }

  if (managedMailbox.provider !== "custom_imap") {
    return null;
  }

  if (
    trustedFolder !== "INBOX" ||
    (message.providerFolder !== undefined && message.providerFolder !== "INBOX") ||
    (threadContext && threadContext.folder !== "INBOX") ||
    !isCanonicalPositiveDecimal(message.imapUid)
  ) {
    return null;
  }

  const uidValidityValues = [
    message.uidValidity,
    threadContext?.uidValidity,
    trustedUidValidity,
  ].filter((value) => value !== undefined && value !== null);

  if (
    uidValidityValues.length === 0 ||
    !uidValidityValues.every(isCanonicalPositiveDecimal) ||
    !uidValidityValues.every((value) => value === uidValidityValues[0])
  ) {
    return null;
  }

  return {
    mailboxId: sourceMailboxId,
    sourceRef: {
      folder: "INBOX",
      uidValidity: uidValidityValues[0] as string,
      imapUid: message.imapUid,
    },
  };
}
