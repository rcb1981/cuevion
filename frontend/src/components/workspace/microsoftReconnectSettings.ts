/**
 * Resolve the one saved, exact Microsoft mailbox eligible for the existing
 * OAuth reconnect flow. Never use an unsaved/draft provider or email as the
 * authority for a reconnect request.
 */
type SavedMailboxCandidate = {
  id: string;
  provider: string | null;
  email: string;
};

export type PersistedMicrosoftReconnectTarget = {
  id: string;
  email: string;
};

export function getPersistedMicrosoftReconnectTarget(
  savedMailboxes: ReadonlyArray<SavedMailboxCandidate>,
  selectedMailbox: SavedMailboxCandidate | null,
): PersistedMicrosoftReconnectTarget | null {
  if (
    !selectedMailbox ||
    selectedMailbox.provider !== "microsoft" ||
    !selectedMailbox.id ||
    selectedMailbox.id.startsWith("draft-")
  ) return null;

  const matches = savedMailboxes.filter(
    (saved) =>
      saved.id === selectedMailbox.id &&
      saved.provider === "microsoft",
  );
  if (matches.length !== 1) return null;

  const email = matches[0].email.trim();
  if (
    !email ||
    email.toLowerCase() !== selectedMailbox.email.trim().toLowerCase()
  ) return null;

  return { id: matches[0].id, email };
}


/**
 * This is only a *recorded* Microsoft OAuth connection, not a live Graph
 * health check. The mailboxProviderHealth engine checks Gmail/IMAP only.
 */
export function hasRecordedMicrosoftOAuthConnection(mailbox: {
  provider: string | null;
  connected: boolean;
  connectionStatus: string;
}): boolean {
  return mailbox.provider === "microsoft" &&
    mailbox.connected === true &&
    mailbox.connectionStatus === "connected";
}
