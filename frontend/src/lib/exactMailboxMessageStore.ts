import {
  areExactMailboxSourcesEqual,
  matchesExactMailboxMessageIdentity,
  type ExactMailboxMessageIdentity,
  type ExactMailboxMessageResponse,
  type ExactMailboxSourceRef,
} from "./exactMailboxMessageApi";
import { applyLiveThreadIdentity } from "./inboxEngine";
import type { normalizeMailMessage } from "../components/workspace/WorkspaceShell";

export type ExactMailboxFolder =
  | "Inbox" | "Drafts" | "Sent" | "Archive" | "Filtered" | "Spam" | "Trash";

/** Values come from current authenticated state, never from the fetch response.
 * Increment generation on every account/workspace change (including A -> B -> A).
 * configRevision is the existing monotonically increasing mailbox connection epoch.
 */
export type ExactMailboxMessageScope = Readonly<{
  accountId: string;
  workspaceId: string;
  mailboxId: string;
  provider: ExactMailboxSourceRef["provider"];
  configRevision: number;
  generation: number;
}>;

export type ExactMailboxMessageTicket = Readonly<{
  scope: ExactMailboxMessageScope;
  sourceRef: ExactMailboxSourceRef;
  signal?: AbortSignal;
}>;

export type ExactMailboxMessageProjection<T extends ExactMailboxMessageIdentity> = {
  scope: ExactMailboxMessageScope | null;
  mailboxes: Record<string, Record<ExactMailboxFolder, T[]>>;
  selection: { mailboxId: string; messageId: string; folder: ExactMailboxFolder } | null;
};

const FOLDERS: readonly ExactMailboxFolder[] = [
  "Inbox", "Drafts", "Sent", "Archive", "Filtered", "Spam", "Trash",
];

/** Feed this canonical seed into WorkspaceShell's existing normalizeMailMessage.
 * The source locator was verified by the API; thread identity only enriches the
 * display projection and is never used to resolve the exact target.
 */
export function buildExactMailboxMessageSeed(response: ExactMailboxMessageResponse) {
  const seed = applyLiveThreadIdentity({ ...response.message, time: response.message.timestamp }, {
    mailboxId: response.mailboxId,
    provider: response.sourceRef.provider,
    folder: response.message.providerFolder,
    uidValidity: response.sourceRef.provider === "custom_imap" ? response.sourceRef.uidValidity : "gmail-api",
  });
  return seed satisfies Parameters<typeof normalizeMailMessage>[0];
}

function sameScope(left: ExactMailboxMessageScope | null, right: ExactMailboxMessageScope) {
  return left !== null &&
    left.accountId === right.accountId && left.workspaceId === right.workspaceId &&
    left.mailboxId === right.mailboxId && left.provider === right.provider &&
    left.configRevision === right.configRevision && left.generation === right.generation;
}

export function captureExactMailboxMessageTicket(
  scope: ExactMailboxMessageScope,
  sourceRef: ExactMailboxSourceRef,
  signal?: AbortSignal,
): ExactMailboxMessageTicket {
  return Object.freeze({
    scope: Object.freeze({ ...scope }),
    sourceRef: Object.freeze({ ...sourceRef }),
    ...(signal ? { signal } : {}),
  });
}

/** Pure publication reducer: call inside the current-state updater, after using
 * the existing normalizeMailMessage renderer adapter. Augmentation and selection
 * are published together, with no asynchronous callback or stale closure window.
 * This does not perform fetching, polling, persistence, or provider mutations.
 */
export function reduceExactMailboxMessagePublication<T extends ExactMailboxMessageIdentity>(
  current: ExactMailboxMessageProjection<T>,
  ticket: ExactMailboxMessageTicket,
  response: ExactMailboxMessageResponse,
  normalizedMessage: T,
): ExactMailboxMessageProjection<T> {
  const scope = current.scope;
  if (
    ticket.signal?.aborted || !sameScope(scope, ticket.scope) || !scope ||
    !scope.accountId || !scope.workspaceId || !scope.mailboxId ||
    !Number.isSafeInteger(scope.configRevision) || scope.configRevision < 0 ||
    !Number.isSafeInteger(scope.generation) || scope.generation < 0 ||
    scope.provider !== ticket.sourceRef.provider || response.mailboxId !== scope.mailboxId ||
    !areExactMailboxSourcesEqual(response.sourceRef, ticket.sourceRef) ||
    normalizedMessage.id !== response.message.id ||
    normalizedMessage.providerFolder !== response.message.providerFolder ||
    !matchesExactMailboxMessageIdentity(response.message, scope.mailboxId, ticket.sourceRef) ||
    !matchesExactMailboxMessageIdentity(normalizedMessage, scope.mailboxId, ticket.sourceRef)
  ) return current;

  const collections = current.mailboxes[scope.mailboxId];
  if (!collections) return current;

  let existing: { message: T; folder: ExactMailboxFolder } | null = null;
  for (const folder of FOLDERS) {
    for (const message of collections[folder]) {
      if (matchesExactMailboxMessageIdentity(message, scope.mailboxId, ticket.sourceRef)) {
        existing ??= { message, folder };
      } else if (message.id === normalizedMessage.id) {
        // An ambiguous renderer ID cannot be selected safely, even if subjects match.
        return current;
      }
    }
  }

  const providerFolder = response.message.providerFolder;
  const targetFolder = existing?.folder ??
    (providerFolder === "INBOX" ? "Inbox" : providerFolder);
  if (!FOLDERS.includes(targetFolder as ExactMailboxFolder)) return current;
  const folder = targetFolder as ExactMailboxFolder;
  const selectedMessage = existing?.message ?? normalizedMessage;
  if (FOLDERS.some((candidateFolder) => collections[candidateFolder].some((message) =>
    message.id === selectedMessage.id &&
    !matchesExactMailboxMessageIdentity(message, scope.mailboxId, ticket.sourceRef)
  ))) return current;
  let keptExisting = false;
  let nextCollections = collections;
  for (const candidateFolder of FOLDERS) {
    const messages = collections[candidateFolder].filter((message) => {
      if (!matchesExactMailboxMessageIdentity(message, scope.mailboxId, ticket.sourceRef)) return true;
      if (existing && message === selectedMessage && candidateFolder === folder && !keptExisting) {
        keptExisting = true;
        return true;
      }
      return false;
    });
    if (messages.length !== collections[candidateFolder].length) {
      nextCollections = { ...nextCollections, [candidateFolder]: messages };
    }
  }
  if (!existing) {
    nextCollections = { ...nextCollections, [folder]: [...nextCollections[folder], selectedMessage] };
  }
  const selection = { mailboxId: scope.mailboxId, messageId: selectedMessage.id, folder };
  if (
    nextCollections === collections && current.selection?.mailboxId === selection.mailboxId &&
    current.selection.messageId === selection.messageId && current.selection.folder === selection.folder
  ) return current;
  return {
    ...current,
    mailboxes: nextCollections === collections ? current.mailboxes : { ...current.mailboxes, [scope.mailboxId]: nextCollections },
    selection,
  };
}
