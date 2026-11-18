import type { MailboxSnapshotSession } from "./mailboxSnapshotSession";

// This is an editor snapshot, never a provider draft or a browser-storage DTO.
// In particular, File objects must not be JSON-serialized and silently lost.
export type DraftSaveStatus =
  | "unsaved" | "saving" | "saved_at_provider"
  | "save_failed" | "save_status_uncertain" | "conflict";

export type ComposeScope = Readonly<{
  ownerId: string;
  workspaceId: string;
  sessionEpoch: string;
  mailboxId: string;
  connectionIdentity: string;
}>;
export type ComposeIdentity = Readonly<{ compositionId: string; revision: number }>;
export type ComposeOperation = ComposeIdentity & Readonly<{
  scope: ComposeScope;
  operationId: string;
  kind: "send" | "local_copy";
}>;
export type DraftAttachment = Readonly<{
  id: string;
  name: string;
  mimeType?: string;
  size?: number;
  contentId?: string;
  disposition?: string;
  inlineSrc?: string;
  file?: File;
  receivedSource?: Readonly<{
    mailboxId: string;
    messageId: string;
    messageUid?: string;
    folder?: string;
    providerFolder?: string;
    uidValidity?: string;
  }>;
}>;
export type DraftSourceContext = Readonly<{
  id: string;
  serverMailboxId?: string;
  providerMessageId?: string;
  providerThreadId?: string;
  providerFolder?: string;
  imapUid?: string;
  uidValidity?: string;
  rfcMessageId?: string;
  threadId?: string;
  threadIdentityAuthority?: "gmail" | "rfc" | "unique_message" | "heuristic";
  threadIdentityContext?: Readonly<{
    mailboxId: string;
    provider: "google" | "custom_imap";
    folder: string;
    uidValidity: string | null;
  }>;
  from: string;
  to: string;
  cc?: string;
  subject: string;
}>;
export type DraftCompositionContent = Readonly<{
  to: string;
  cc: string;
  bcc: string;
  subject: string;
  bodyHtml: string;
  bodyText: string;
  // Retain the mobile input and the base markup separately; do not double-prepend
  // signature/quote or lose mobile text when a later provider adapter is added.
  mobileBodyText: string;
  baseBodyHtml: string;
  signatureSelection: string;
  signature: Readonly<{
    html: string;
    useByDefault: boolean;
    logoImageUrl: string | null;
    layout: "text-only" | "logo-below" | "logo-left";
    showDivider: boolean;
  }> | null;
  mode: "new" | "reply" | "reply_all" | "forward";
  source: DraftSourceContext | null;
  replyAnchor: DraftSourceContext | null;
  attachments: readonly DraftAttachment[];
}>;
export type DraftCompositionSnapshot = Omit<DraftCompositionContent, "attachments"> & ComposeIdentity & Readonly<{
  scope: ComposeScope;
  attachments: readonly (DraftAttachment & Readonly<{
    availability: "in_memory" | "reference_unverified" | "unavailable";
  }>)[];
  saveStatus: DraftSaveStatus;
  operation: ComposeOperation | null;
  persistence: "memory_only";
}>;

function copySource(source: DraftSourceContext | null): DraftSourceContext | null {
  if (!source) return null;
  // Explicit allowlist: do not copy entire mailbox/connection/classification objects.
  const { id, serverMailboxId, providerMessageId, providerThreadId, providerFolder,
    imapUid, uidValidity, rfcMessageId, threadId, threadIdentityAuthority, threadIdentityContext, from, to, cc, subject } = source;
  return Object.freeze({ id, serverMailboxId, providerMessageId, providerThreadId,
    providerFolder, imapUid, uidValidity, rfcMessageId, threadId, threadIdentityAuthority,
    threadIdentityContext: threadIdentityContext ? Object.freeze({ ...threadIdentityContext }) : undefined,
    from, to, cc, subject });
}

export function captureDraftComposition(
  identity: ComposeIdentity,
  scope: ComposeScope,
  content: DraftCompositionContent,
): DraftCompositionSnapshot {
  return Object.freeze({
    to: content.to, cc: content.cc, bcc: content.bcc, subject: content.subject,
    bodyHtml: content.bodyHtml, bodyText: content.bodyText,
    mobileBodyText: content.mobileBodyText, baseBodyHtml: content.baseBodyHtml,
    signatureSelection: content.signatureSelection,
    signature: content.signature ? Object.freeze({ ...content.signature }) : null,
    mode: content.mode,
    ...identity,
    scope: Object.freeze({ ...scope }),
    source: copySource(content.source),
    replyAnchor: copySource(content.replyAnchor),
    attachments: Object.freeze(content.attachments.map((attachment) => {
      const { id, name, mimeType, size, contentId, disposition, inlineSrc, file, receivedSource } = attachment;
      return Object.freeze({ id, name, mimeType, size, contentId, disposition, inlineSrc, file,
        receivedSource: receivedSource ? Object.freeze({ ...receivedSource }) : undefined,
        availability: file ? "in_memory" as const
          : receivedSource ? "reference_unverified" as const : "unavailable" as const,
      });
    })),
    saveStatus: "unsaved",
    operation: null,
    persistence: "memory_only",
  });
}

export function mobileComposeBodyHtml(text: string, baseHtml: string): string {
  return text.split("\n").map((line) => line.trim() === ""
    ? "<p><br></p>"
    : `<p>${line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}</p>`
  ).join("") + baseHtml;
}

export function createComposeSafety(
  session: MailboxSnapshotSession | null,
  getConnectionIdentity: (mailboxId: string) => string | null,
  newId = () => crypto.randomUUID(),
) {
  let identity: ComposeIdentity = { compositionId: newId(), revision: 0 };
  let pending: ComposeOperation | null = null;
  let localCopy: DraftCompositionSnapshot | null = null;
  let mounted = true;
  const scope = (mailboxId: string): ComposeScope | null => {
    const connectionIdentity = getConnectionIdentity(mailboxId);
    return mounted && session?.isActive() && connectionIdentity ? Object.freeze({
      ownerId: session.owner.userId, workspaceId: session.owner.workspaceId,
      sessionEpoch: session.epoch, mailboxId, connectionIdentity,
    }) : null;
  };
  const matchesScope = (expected: ComposeScope) => {
    const current = scope(expected.mailboxId);
    return current !== null && Object.keys(expected).every(
      (key) => expected[key as keyof ComposeScope] === current[key as keyof ComposeScope],
    );
  };
  const isCurrent = (operation: ComposeOperation) => pending === operation &&
    identity.compositionId === operation.compositionId && identity.revision === operation.revision &&
    matchesScope(operation.scope);
  const isSameEditor = (operation: ComposeOperation) => mounted && session?.isActive() === true &&
    session.owner.userId === operation.scope.ownerId && session.owner.workspaceId === operation.scope.workspaceId &&
    session.epoch === operation.scope.sessionEpoch && identity.compositionId === operation.compositionId &&
    identity.revision === operation.revision;
  return {
    identity: () => identity,
    scope,
    isCurrent,
    isPending: () => pending !== null,
    // All editor setters and contenteditable input use this synchronous fence.
    // No React state publication is needed for each desktop keystroke.
    edit: () => {
      if (pending || !mounted) return false;
      identity = { ...identity, revision: identity.revision + 1 };
      return true;
    },
    startComposition: () => {
      if (pending) return false;
      identity = { compositionId: newId(), revision: 0 };
      localCopy = null;
      return true;
    },
    begin: (kind: ComposeOperation["kind"], snapshot: DraftCompositionSnapshot) => {
      if (pending || snapshot.compositionId !== identity.compositionId ||
          snapshot.revision !== identity.revision || !matchesScope(snapshot.scope)) return null;
      pending = Object.freeze({ ...identity, scope: snapshot.scope, operationId: newId(), kind });
      return pending;
    },
    keepLocalCopy: (operation: ComposeOperation, snapshot: DraftCompositionSnapshot) => {
      if (!isCurrent(operation) || operation.kind !== "local_copy" ||
          snapshot.scope !== operation.scope ||
          snapshot.compositionId !== operation.compositionId || snapshot.revision !== operation.revision) return false;
      localCopy = Object.freeze({ ...snapshot, operation, saveStatus: "unsaved" });
      return true;
    },
    readLocalCopy: () => localCopy && matchesScope(localCopy.scope) ? localCopy : null,
    finish: (operation: ComposeOperation) => {
      if (!isCurrent(operation)) return false;
      pending = null;
      return true;
    },
    // A reconnect invalidates result publication, but must not leave the exact
    // original editor permanently locked. This grants no result authority and
    // can never release another operation, revision, composition or account.
    releaseAfterConnectionChange: (operation: ComposeOperation) => {
      if (pending !== operation || !isSameEditor(operation) || isCurrent(operation)) return false;
      pending = null;
      return true;
    },
    clearLocalCopy: () => { localCopy = null; },
    activate: () => { mounted = true; },
    invalidate: () => {
      mounted = false;
      identity = { compositionId: newId(), revision: 0 };
      pending = null;
      localCopy = null;
    },
  };
}
export type ComposeSafety = ReturnType<typeof createComposeSafety>;
