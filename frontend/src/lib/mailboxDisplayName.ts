export type WorkspaceMailboxDisplayRecord = {
  id: string;
  onboardingInboxId?: string;
  title: string;
  email: string;
  provider?: string | null;
  connected?: boolean;
  connectionMethod?: string | null;
  connectionStatus?: string;
};

export type WorkspaceMailboxDisplayItem = {
  id: string;
  title: string;
  email: string;
};

export type OnboardingCustomInboxDisplayRecord = {
  id: string;
  name: string;
};

function trimmedNonEmpty(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }

  const trimmedValue = value.trim();
  return trimmedValue.length > 0 ? trimmedValue : null;
}

function normalizeMailboxEmail(value: unknown): string | null {
  const normalizedEmail = trimmedNonEmpty(value)?.toLowerCase() ?? null;

  return normalizedEmail && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail)
    ? normalizedEmail
    : null;
}

function exactIdentity(value: unknown): string | null {
  if (typeof value !== "string" || value !== value.trim() || value.length === 0) {
    return null;
  }

  return value;
}

function isConnectedCustomImapMailbox(
  mailbox: WorkspaceMailboxDisplayRecord,
): boolean {
  return (
    mailbox.provider === "custom_imap" &&
    mailbox.connected === true &&
    mailbox.connectionMethod === "imap" &&
    mailbox.connectionStatus === "connected"
  );
}

export function findExactAuthoritativeCustomMailbox(
  mailbox: WorkspaceMailboxDisplayRecord,
  authoritativeManagedInboxes: readonly WorkspaceMailboxDisplayRecord[],
): WorkspaceMailboxDisplayRecord | null {
  const mailboxId = exactIdentity(mailbox.id);
  const onboardingInboxId = exactIdentity(mailbox.onboardingInboxId);
  const normalizedEmail = normalizeMailboxEmail(mailbox.email);

  if (
    !isConnectedCustomImapMailbox(mailbox) ||
    !mailboxId ||
    !onboardingInboxId ||
    !onboardingInboxId.startsWith("custom:") ||
    mailboxId === onboardingInboxId ||
    !normalizedEmail
  ) {
    return null;
  }

  const idMatches = authoritativeManagedInboxes.filter(
    (candidate) => exactIdentity(candidate.id) === mailboxId,
  );
  const positionMatches = authoritativeManagedInboxes.filter(
    (candidate) =>
      exactIdentity(candidate.onboardingInboxId) === onboardingInboxId,
  );
  const emailMatches = authoritativeManagedInboxes.filter(
    (candidate) => normalizeMailboxEmail(candidate.email) === normalizedEmail,
  );

  if (
    idMatches.length !== 1 ||
    positionMatches.length !== 1 ||
    emailMatches.length !== 1 ||
    idMatches[0] !== positionMatches[0] ||
    idMatches[0] !== emailMatches[0]
  ) {
    return null;
  }

  const candidate = idMatches[0];
  const candidateId = exactIdentity(candidate.id);
  const candidateOnboardingInboxId = exactIdentity(
    candidate.onboardingInboxId,
  );

  return isConnectedCustomImapMailbox(candidate) &&
    candidateId !== candidateOnboardingInboxId
    ? candidate
    : null;
}

export function resolveWorkspaceMailboxDisplayName({
  mailbox,
  authoritativeManagedInboxes,
  onboardingName,
  titleOverride,
  fallbackTitle,
}: {
  mailbox: WorkspaceMailboxDisplayRecord;
  authoritativeManagedInboxes: readonly WorkspaceMailboxDisplayRecord[];
  onboardingName?: string | null;
  titleOverride?: string | null;
  fallbackTitle?: string | null;
}): string {
  const explicitOverride = trimmedNonEmpty(titleOverride);
  if (explicitOverride) {
    return explicitOverride;
  }

  if (mailbox.provider !== "custom_imap") {
    return (
      trimmedNonEmpty(fallbackTitle) ??
      trimmedNonEmpty(mailbox.title) ??
      trimmedNonEmpty(mailbox.email) ??
      "Inbox"
    );
  }

  const authoritativeMailbox = findExactAuthoritativeCustomMailbox(
    mailbox,
    authoritativeManagedInboxes,
  );
  const authoritativeTitle = trimmedNonEmpty(authoritativeMailbox?.title);
  if (authoritativeTitle) {
    return authoritativeTitle;
  }

  const authoritativeEmail = trimmedNonEmpty(authoritativeMailbox?.email);
  if (authoritativeEmail) {
    return authoritativeEmail;
  }

  return (
    trimmedNonEmpty(onboardingName) ??
    trimmedNonEmpty(fallbackTitle) ??
    trimmedNonEmpty(mailbox.title) ??
    "Custom Inbox"
  );
}

export function buildCanonicalWorkspaceMailboxPresentations<
  TMailbox extends WorkspaceMailboxDisplayItem,
>({
  mailboxes,
  managedInboxes,
  authoritativeManagedInboxes,
  customInboxes,
  mailboxTitleOverrides,
}: {
  mailboxes: readonly TMailbox[];
  managedInboxes: readonly WorkspaceMailboxDisplayRecord[];
  authoritativeManagedInboxes: readonly WorkspaceMailboxDisplayRecord[];
  customInboxes: readonly OnboardingCustomInboxDisplayRecord[];
  mailboxTitleOverrides: Readonly<Record<string, string | undefined>>;
}): TMailbox[] {
  return mailboxes.map((mailbox) => {
    const managedMatches = managedInboxes.filter(
      (candidate) => candidate.id === mailbox.id,
    );
    const managedMailbox =
      managedMatches.length === 1 ? managedMatches[0] : null;

    if (!managedMailbox) {
      return {
        ...mailbox,
        title:
          trimmedNonEmpty(mailboxTitleOverrides[mailbox.id]) ??
          trimmedNonEmpty(mailbox.title) ??
          "Inbox",
      };
    }

    const onboardingMatches = customInboxes.filter(
      (candidate) => candidate.id === managedMailbox.onboardingInboxId,
    );
    const onboardingName =
      onboardingMatches.length === 1 ? onboardingMatches[0].name : null;

    return {
      ...mailbox,
      title: resolveWorkspaceMailboxDisplayName({
        mailbox: managedMailbox,
        authoritativeManagedInboxes,
        onboardingName,
        titleOverride: mailboxTitleOverrides[mailbox.id],
        fallbackTitle: mailbox.title,
      }),
    };
  });
}

export function buildWorkspaceMailboxPresentationLabels(
  canonicalDisplayName: string,
  hasExplicitTitleOverride = false,
) {
  const navigationName = trimmedNonEmpty(canonicalDisplayName) ?? "Inbox";
  const inboxHeading =
    hasExplicitTitleOverride || navigationName.endsWith("Inbox")
      ? navigationName
      : `${navigationName} Inbox`;

  return {
    navigationName,
    mailboxHeader: inboxHeading,
    messageListHeading: inboxHeading,
  };
}
