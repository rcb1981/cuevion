export type CollaborationOwnerLookupSourceRef =
  | {
      readonly providerMessageId: string;
    }
  | {
      readonly folder: "INBOX";
      readonly uidValidity: string;
      readonly imapUid: string;
    };

export type CollaborationOwnerSourceLocator = {
  readonly mailboxId: string;
  readonly sourceRef: CollaborationOwnerLookupSourceRef;
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
const trustedSourceLocators = new WeakSet<object>();

function isExactRecord(
  value: unknown,
  keys: readonly string[],
): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }

  const receivedKeys = Object.keys(value);
  return (
    receivedKeys.length === keys.length &&
    keys.every((key) => receivedKeys.includes(key))
  );
}

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

function trustSourceLocator(
  locator: CollaborationOwnerSourceLocator,
): CollaborationOwnerSourceLocator {
  const trusted = Object.freeze({
    mailboxId: locator.mailboxId,
    sourceRef: Object.freeze({ ...locator.sourceRef }),
  });
  trustedSourceLocators.add(trusted);
  return trusted;
}

export function isCanonicalCollaborationOwnerSourceLocator(
  value: unknown,
): value is CollaborationOwnerSourceLocator {
  if (
    !isExactRecord(value, ["mailboxId", "sourceRef"]) ||
    typeof value.mailboxId !== "string" ||
    value.mailboxId.length === 0 ||
    value.mailboxId !== value.mailboxId.trim()
  ) {
    return false;
  }

  const sourceRef = value.sourceRef;
  if (
    isExactRecord(sourceRef, ["providerMessageId"]) &&
    typeof sourceRef.providerMessageId === "string" &&
    EXACT_PROVIDER_MESSAGE_ID_PATTERN.test(sourceRef.providerMessageId)
  ) {
    return true;
  }

  return (
    isExactRecord(sourceRef, ["folder", "uidValidity", "imapUid"]) &&
    sourceRef.folder === "INBOX" &&
    isCanonicalPositiveDecimal(sourceRef.uidValidity) &&
    isCanonicalPositiveDecimal(sourceRef.imapUid)
  );
}

export function isTrustedCollaborationOwnerSourceLocator(
  value: unknown,
): value is CollaborationOwnerSourceLocator {
  return (
    isCanonicalCollaborationOwnerSourceLocator(value) &&
    trustedSourceLocators.has(value)
  );
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

    return trustSourceLocator({
      mailboxId: sourceMailboxId,
      sourceRef: { providerMessageId: message.providerMessageId },
    });
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

  return trustSourceLocator({
    mailboxId: sourceMailboxId,
    sourceRef: {
      folder: "INBOX",
      uidValidity: uidValidityValues[0] as string,
      imapUid: message.imapUid,
    },
  });
}
