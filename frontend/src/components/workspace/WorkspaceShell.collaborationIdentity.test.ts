import assert from "node:assert/strict";
import "sucrase/register/tsx.js";

const {
  applyMailboxScopedCollaborationSnapshotUpdates,
  applyMailboxScopedCollaborationThreadOverlays,
  buildCollaborationLocalStateKey,
  buildMailboxScopedMessageSelection,
  buildSharedCollaborationProjection,
  buildVisibleNotificationItems,
  buildVisibleActivityItems,
  buildVisibleTeamCollaborationItems,
  getCollaborationThreadIdentityKey,
  getCollaborationMessageSourceMailboxId,
  getMailboxMessageById,
  getMailboxScopedWorkspaceMessageIdentity,
  getSharedCollaborationProjectionIdentityKeys,
  mergeMailboxScopedCollaborationSnapshotMessage,
  moveMailboxScopedSelectionsToFolder,
  reduceMailboxScopedMessageSelection,
  resolveAdjacentMailboxScopedMessageSelection,
  resolveAuthoritativeMessageLocation,
  resolveCollaborationInviteStorageMailboxId,
  resolveEffectiveMailboxScopedContextSelection,
  resolveMailboxScopedSelectionEntries,
  resolveMailboxScopedSelectionAfterAction,
  resolveMailboxScopedManualPriorityTarget,
  resolveMailboxScopedWorkspaceMessageContext,
  resolveInitialComposeAttachments,
  resolveUnambiguousCollaborationMutationTarget,
  shouldPersistMailboxSelectionForRemount,
  updateMailboxMessageById,
  updateMailboxSnapshotCollaboration,
  upsertMailboxScopedInviteMessage,
} = require("./WorkspaceShell.tsx") as typeof import("./WorkspaceShell");
const {
  hydrateLiveInboxSnapshot,
  LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
  LIVE_INBOX_THREAD_IDENTITY_VERSION,
  MUSIC_CLASSIFIER_VERSION,
  readLiveInboxSnapshots,
  saveLiveInboxSnapshot,
} = require("../../lib/liveInboxSnapshots") as typeof import("../../lib/liveInboxSnapshots");

const folders = [
  "Inbox",
  "Drafts",
  "Sent",
  "Archive",
  "Filtered",
  "Spam",
  "Trash",
] as const;

function buildCollaboration(label: string) {
  return {
    state: "needs_review" as const,
    requestedBy: `Reviewer ${label}`,
    requestedUserId: `reviewer-${label.toLowerCase()}`,
    requestedUserName: `Reviewer ${label}`,
    createdAt: 1_800_000_000_000,
    updatedAt: 1_800_000_000_100,
    participants: [],
    messages: [
      {
        id: "reply-1",
        authorId: `reviewer-${label.toLowerCase()}`,
        authorName: `Reviewer ${label}`,
        text: `Mailbox ${label}`,
        timestamp: 1_800_000_000_100,
        visibility: "internal" as const,
      },
    ],
  };
}

function buildMessage(mailboxId: string, label: string) {
  return {
    id: "imap-uid-42",
    serverMailboxId: mailboxId,
    providerFolder: "INBOX",
    imapUid: "42",
    uidValidity: "900",
    threadIdentityContext: {
      mailboxId,
      provider: "custom_imap" as const,
      folder: "INBOX",
      uidValidity: "900",
    },
    sender: `Sender ${label}`,
    subject: `Mailbox ${label}`,
    snippet: `Message in mailbox ${label}`,
    time: "10:00",
    from: `sender-${label.toLowerCase()}@example.test`,
    to: "viewer@example.test",
    timestamp: "2026-08-17T10:00:00.000Z",
    body: [`Mailbox ${label}`],
    isShared: true,
    collaboration: buildCollaboration(label),
    priorityScore: "medium" as const,
    category: "Primary" as const,
    categorySource: "system" as const,
    categoryConfidence: "medium" as const,
  };
}

function buildCollections(message: ReturnType<typeof buildMessage>) {
  return Object.fromEntries(
    folders.map((folder) => [folder, folder === "Inbox" ? [message] : []]),
  );
}

function buildThread(mailboxId: string, label: string) {
  return {
    v: 1 as const,
    workspaceId: "workspace-1",
    mailboxId,
    messageId: "imap-uid-42",
    sourceMessage: {
      id: "imap-uid-42",
      subject: `Mailbox ${label}`,
      sender: `Sender ${label}`,
      from: `sender-${label.toLowerCase()}@example.test`,
      timestamp: "2026-08-17T10:00:00.000Z",
      snippet: `Message in mailbox ${label}`,
      body: [`Mailbox ${label}`],
    },
    isShared: true,
    collaboration: buildCollaboration(label),
  };
}

const mailboxA = buildMessage("mailbox-a", "A");
const mailboxB = buildMessage("mailbox-b", "B");
const mailboxStore = {
  "mailbox-a": buildCollections(mailboxA),
  "mailbox-b": buildCollections(mailboxB),
};
const orderedMailboxes = [
  { id: "mailbox-a", title: "Mailbox A" },
  { id: "mailbox-b", title: "Mailbox B" },
];
const teamNavigationRequests: Array<{
  mailboxId: string;
  messageId: string;
  sourceMailboxId?: string;
}> = [];

const collaborationItems = buildVisibleTeamCollaborationItems({
  mailboxStore: mailboxStore as never,
  orderedMailboxes: orderedMailboxes as never,
  currentUserId: "workspace-1",
  currentUserName: "Current User",
  onOpenCollaborationNavigation: (request) => {
    teamNavigationRequests.push(request);
  },
});

assert.equal(
  collaborationItems.length,
  2,
  "Team Collaborations must retain both mailboxes when their IMAP UID/message ID is equal",
);
collaborationItems.forEach((item) => item.action?.());
assert.deepEqual(
  teamNavigationRequests.map((request) => request.mailboxId).sort(),
  ["mailbox-a", "mailbox-b"],
  "each Team row must navigate through its owning mailbox",
);
assert.deepEqual(
  teamNavigationRequests.map((request) => request.sourceMailboxId).sort(),
  ["mailbox-a", "mailbox-b"],
  "Team navigation must retain the exact source mailbox scope",
);
assert.deepEqual(
  teamNavigationRequests
    .map((request) =>
      request.sourceMailboxId
        ? getMailboxMessageById(
            mailboxStore as never,
            request.sourceMailboxId as never,
            request.messageId,
          )?.subject
        : null,
    )
    .sort(),
  ["Mailbox A", "Mailbox B"],
  "mailbox-scoped Team navigation must resolve A only to A and B only to B",
);

const activityNavigationRequests: Array<{ mailboxId: string; messageId: string }> = [];
const activityItems = buildVisibleActivityItems({
  mailboxStore: mailboxStore as never,
  orderedMailboxes: orderedMailboxes as never,
  authenticatedUser: {
    email: "viewer@example.test",
    name: "Current User",
    userType: "member",
  },
  collaborationLastSeenByKey: {},
  currentUserId: "workspace-1",
  currentViewerPersistenceKey: "viewer@example.test",
  teamActivityEnabled: true,
  onOpenActivityNavigation: (request) => {
    activityNavigationRequests.push(request);
  },
});

assert.equal(
  activityItems.length,
  4,
  "Team Activity must retain create/reply events for both same-ID mailboxes",
);
activityItems.forEach((item) => item.action?.());
assert.deepEqual(
  [...new Set(activityNavigationRequests.map((request) => request.mailboxId))].sort(),
  ["mailbox-a", "mailbox-b"],
  "each Activity event must navigate through its owning mailbox",
);

const notificationNavigationRequests: Array<{
  mailboxId: string;
  messageId: string;
  sourceMailboxId?: string;
}> = [];
const notificationItems = buildVisibleNotificationItems({
  mailboxStore: mailboxStore as never,
  orderedMailboxes: orderedMailboxes as never,
  authenticatedUser: {
    email: "viewer@example.test",
    name: "Current User",
    userType: "member",
  },
  collaborationLastSeenByKey: {},
  currentUserId: "workspace-1",
  currentUserEmail: "viewer@example.test",
  currentViewerPersistenceKey: "viewer@example.test",
  currentUserName: "Current User",
  teamActivityEnabled: true,
  onOpenNotificationNavigation: (request) => {
    notificationNavigationRequests.push(request);
  },
});

assert.equal(notificationItems.length, 4);
assert.equal(new Set(notificationItems.map((item) => item.id)).size, 4);
assert.equal(new Set(notificationItems.flatMap((item) => item.sourceIds)).size, 4);
notificationItems.forEach((item) => item.action());
assert.deepEqual(
  [...new Set(notificationNavigationRequests.map((request) => request.sourceMailboxId))].sort(),
  ["mailbox-a", "mailbox-b"],
  "Notifications must navigate through the source mailbox that created each event",
);

const threadA = buildThread("mailbox-a", "A");
const threadB = buildThread("mailbox-b", "B");
assert.notEqual(
  getCollaborationThreadIdentityKey(threadA),
  getCollaborationThreadIdentityKey(threadB),
);
assert.notDeepEqual(
  getSharedCollaborationProjectionIdentityKeys(threadA),
  getSharedCollaborationProjectionIdentityKeys(threadB),
);
const projectionA = buildSharedCollaborationProjection(threadA);
const projectionB = buildSharedCollaborationProjection(threadB);
assert.notEqual(projectionA.id, projectionB.id);
assert.equal(projectionA.collaborationMessageId, "imap-uid-42");
assert.equal(projectionB.collaborationMessageId, "imap-uid-42");
assert.equal(projectionA.collaborationMailboxId, "mailbox-a");
assert.equal(projectionB.collaborationMailboxId, "mailbox-b");

const gmailA = {
  ...mailboxA,
  id: "same-google-id",
  imapUid: undefined,
  uidValidity: undefined,
  threadIdentityContext: {
    mailboxId: "mailbox-a",
    provider: "google" as const,
    folder: "Inbox",
    uidValidity: "gmail-api",
  },
};
const gmailB = {
  ...gmailA,
  threadIdentityContext: {
    ...gmailA.threadIdentityContext,
    mailboxId: "mailbox-b",
  },
};
assert.notEqual(
  getMailboxScopedWorkspaceMessageIdentity("mailbox-a" as never, gmailA as never),
  getMailboxScopedWorkspaceMessageIdentity("mailbox-b" as never, gmailB as never),
  "the same bare Google ID must remain mailbox-scoped",
);

const selectionA = buildMailboxScopedMessageSelection(
  "mailbox-a" as never,
  mailboxA as never,
  "Inbox",
);
const selectionB = buildMailboxScopedMessageSelection(
  "mailbox-b" as never,
  mailboxB as never,
  "Inbox",
);
assert.notEqual(selectionA.key, selectionB.key);
let scopedSelectionState = reduceMailboxScopedMessageSelection(
  { selected: [], primaryKey: null, anchorKey: null },
  [selectionA, selectionB],
  selectionA,
  "single",
);
scopedSelectionState = reduceMailboxScopedMessageSelection(
  scopedSelectionState,
  [selectionA, selectionB],
  selectionB,
  "toggle",
);
assert.deepEqual(
  scopedSelectionState.selected.map((selection) => selection.mailboxId),
  ["mailbox-a", "mailbox-b"],
  "Cmd/Ctrl selection must treat equal bare IDs in different mailboxes as distinct",
);
scopedSelectionState = reduceMailboxScopedMessageSelection(
  scopedSelectionState,
  [selectionA, selectionB],
  selectionA,
  "toggle",
);
assert.deepEqual(
  scopedSelectionState.selected.map((selection) => selection.mailboxId),
  ["mailbox-b"],
  "toggling A must not remove the equal bare ID from B",
);
const rangeStart = buildMailboxScopedMessageSelection(
  "mailbox-a" as never,
  { ...mailboxA, id: "range-start", imapUid: "40" } as never,
  "Inbox",
);
const rangeEnd = buildMailboxScopedMessageSelection(
  "mailbox-b" as never,
  { ...mailboxB, id: "range-end", imapUid: "44" } as never,
  "Inbox",
);
scopedSelectionState = reduceMailboxScopedMessageSelection(
  { selected: [rangeStart], primaryKey: rangeStart.key, anchorKey: rangeStart.key },
  [rangeStart, selectionA, selectionB, rangeEnd],
  rangeEnd,
  "range",
);
assert.deepEqual(
  scopedSelectionState.selected.map((selection) => selection.key),
  [rangeStart.key, selectionA.key, selectionB.key, rangeEnd.key],
  "Shift selection must retain both equal-ID rows inside the range",
);
assert.equal(
  resolveAdjacentMailboxScopedMessageSelection(
    [rangeStart, selectionA, selectionB, rangeEnd],
    selectionA.key,
    "next",
  )?.key,
  selectionB.key,
  "keyboard movement must advance to the exact adjacent duplicate-ID row",
);
assert.equal(
  resolveMailboxScopedSelectionAfterAction(
    [rangeStart, selectionA, selectionB, rangeEnd],
    selectionB.key,
    [selectionB.key],
  )?.key,
  rangeEnd.key,
  "advance-after-action must remove B without treating equal-ID A as processed",
);
assert.deepEqual(
  resolveEffectiveMailboxScopedContextSelection([selectionA], selectionB).map(
    (selection) => selection.key,
  ),
  [selectionB.key],
  "right-clicking B while only A is selected must target B alone",
);

const reassignedMailboxA = {
  ...mailboxA,
  id: "imap-uid-42-reassigned",
};
const reconciledEntries = resolveMailboxScopedSelectionEntries(
  {
    "mailbox-a": buildCollections(reassignedMailboxA),
    "mailbox-b": buildCollections(mailboxB),
  } as never,
  [selectionA],
);
assert.equal(reconciledEntries.length, 1);
assert.equal(reconciledEntries[0].mailboxId, "mailbox-a");
assert.equal(reconciledEntries[0].message.id, "imap-uid-42-reassigned");
assert.equal(
  resolveMailboxScopedSelectionEntries(
    { "mailbox-b": buildCollections(mailboxB) } as never,
    [selectionA],
  ).length,
  0,
  "selection reconciliation must never cross to another mailbox with the same UID",
);
assert.equal(
  shouldPersistMailboxSelectionForRemount({
    isSharedView: true,
    activeSmartFolderId: null,
    hostMailboxId: "mailbox-a" as never,
    selection: selectionB,
  }),
  false,
  "shared selection from B must not overwrite mailbox A's bare-ID remount memory",
);
assert.equal(
  shouldPersistMailboxSelectionForRemount({
    isSharedView: false,
    activeSmartFolderId: null,
    hostMailboxId: "mailbox-a" as never,
    selection: selectionA,
  }),
  true,
  "normal single-mailbox selection persistence remains unchanged",
);

assert.deepEqual(
  resolveAuthoritativeMessageLocation({
    message: mailboxA as never,
    exactLocation: null,
    explicitMailboxId: "mailbox-a" as never,
    explicitFolder: "Inbox",
    bareIdLocation: { mailboxId: "mailbox-b" as never, folder: "Inbox" },
  }),
  { mailboxId: "mailbox-a", folder: "Inbox" },
  "known source context must outrank a lossy last-wins bare-ID location",
);

assert.deepEqual(
  resolveAuthoritativeMessageLocation({
    message: {
      ...mailboxA,
      threadIdentityContext: {
        ...mailboxA.threadIdentityContext,
        folder: "Archive",
      },
    } as never,
    exactLocation: null,
    bareIdLocation: { mailboxId: "mailbox-b" as never, folder: "Trash" },
  }),
  { mailboxId: "mailbox-a", folder: "Archive" },
  "known message context must not inherit folder provenance from another mailbox",
);

const threadedMailboxA = {
  ...mailboxA,
  providerThreadId: "provider-thread-1",
  attachments: [
    {
      id: "attachment-a",
      name: "mailbox-a.pdf",
      mimeType: "application/pdf",
    },
  ],
  providerFolder: "Archive/2026",
};
const threadedMailboxAReply = {
  ...threadedMailboxA,
  id: "mailbox-a-reply",
  imapUid: "43",
  subject: "Re: Mailbox A",
  timestamp: "2026-08-17T11:00:00.000Z",
};
const threadedMailboxB = {
  ...mailboxB,
  providerThreadId: "provider-thread-1",
};
const threadedMailboxBReply = {
  ...threadedMailboxB,
  id: "mailbox-b-reply",
  imapUid: "43",
  subject: "Re: Mailbox B",
  timestamp: "2026-08-17T12:00:00.000Z",
};
const threadedStore = {
  "mailbox-a": {
    ...buildCollections(threadedMailboxA),
    Inbox: [threadedMailboxAReply],
    Archive: [threadedMailboxA],
  },
  "mailbox-b": {
    ...buildCollections(threadedMailboxB),
    Inbox: [threadedMailboxB, threadedMailboxBReply],
  },
};
const threadedSelectionA = buildMailboxScopedMessageSelection(
  "mailbox-a" as never,
  threadedMailboxA as never,
  "Archive",
);
const threadedContextA = resolveMailboxScopedWorkspaceMessageContext(
  threadedStore as never,
  threadedSelectionA,
);
assert.ok(threadedContextA);
assert.equal(threadedContextA.mailboxId, "mailbox-a");
assert.equal(threadedContextA.folder, "Archive");
assert.ok(
  threadedContextA.threadMessages.every(
    (message) => message.serverMailboxId === "mailbox-a",
  ),
  "Reply and Reply All thread candidates must remain inside the selected mailbox",
);
for (const mode of ["reply", "reply_all"] as const) {
  assert.deepEqual(
    resolveInitialComposeAttachments(
      mode,
      threadedContextA.message as never,
      { mailboxId: threadedContextA.mailboxId, folder: threadedContextA.folder },
    ),
    [],
    `${mode} must retain the source context without changing attachment semantics`,
  );
}
const forwardedAttachments = resolveInitialComposeAttachments(
  "forward",
  threadedContextA.message as never,
  { mailboxId: threadedContextA.mailboxId, folder: threadedContextA.folder },
);
assert.deepEqual(forwardedAttachments[0].receivedSource, {
  mailboxId: "mailbox-a",
  messageId: "imap-uid-42",
  messageUid: "42",
  folder: "Archive",
  providerFolder: "Archive/2026",
  uidValidity: "900",
});

const movedThreadedA = moveMailboxScopedSelectionsToFolder(
  threadedStore as never,
  [threadedSelectionA],
  "Trash",
);
assert.equal(movedThreadedA["mailbox-a"].Archive.length, 0);
assert.ok(
  movedThreadedA["mailbox-a"].Trash.some(
    (message) => message.id === "imap-uid-42",
  ),
);
assert.equal(
  movedThreadedA["mailbox-b"],
  threadedStore["mailbox-b"],
  "Shared context-menu and drag moves for A must leave equal-ID mailbox B untouched",
);

const mutationStore = {
  "mailbox-a": buildCollections({
    ...mailboxA,
    collaborationMessageId: "imap-uid-42",
    collaborationWorkspaceId: "workspace-1",
    collaborationMailboxId: "mailbox-a",
  } as never),
  "mailbox-b": buildCollections({
    ...mailboxB,
    collaborationMessageId: "imap-uid-42",
    collaborationWorkspaceId: "workspace-1",
    collaborationMailboxId: "mailbox-b",
  } as never),
};
const ambiguousMutation = resolveUnambiguousCollaborationMutationTarget({
  store: mutationStore as never,
  storageMailboxId: "mailbox-a" as never,
  messageId: "imap-uid-42",
  fallbackWorkspaceId: "workspace-1",
  sharedMailboxId: "shared-collaboration" as never,
});
assert.equal(ambiguousMutation.ok, false);
assert.equal(
  ambiguousMutation.ok ? null : ambiguousMutation.reason,
  "ambiguous_mailbox",
  "an ambiguous mailbox-less server mutation must fail closed before transport",
);
const uniqueMutation = resolveUnambiguousCollaborationMutationTarget({
  store: { "mailbox-a": mutationStore["mailbox-a"] } as never,
  storageMailboxId: "mailbox-a" as never,
  messageId: "imap-uid-42",
  fallbackWorkspaceId: "workspace-1",
  sharedMailboxId: "shared-collaboration" as never,
});
assert.equal(uniqueMutation.ok, true);
assert.equal(uniqueMutation.ok ? uniqueMutation.target.sourceMailboxId : null, "mailbox-a");
const physicalAndProjectionMutation = resolveUnambiguousCollaborationMutationTarget({
  store: {
    "mailbox-a": mutationStore["mailbox-a"],
    "shared-collaboration": buildCollections(projectionA as never),
  } as never,
  storageMailboxId: "mailbox-a" as never,
  messageId: "imap-uid-42",
  fallbackWorkspaceId: "workspace-1",
  sharedMailboxId: "shared-collaboration" as never,
});
assert.equal(
  physicalAndProjectionMutation.ok,
  true,
  "a physical row and its shared projection must collapse to one source mailbox",
);
const unresolvedSharedMutation = resolveUnambiguousCollaborationMutationTarget({
  store: {
    "mailbox-a": mutationStore["mailbox-a"],
    "shared-collaboration": buildCollections({
      ...projectionA,
      collaborationMailboxId: undefined,
    } as never),
  } as never,
  storageMailboxId: "mailbox-a" as never,
  messageId: "imap-uid-42",
  fallbackWorkspaceId: "workspace-1",
  sharedMailboxId: "shared-collaboration" as never,
});
assert.equal(unresolvedSharedMutation.ok, false);
assert.equal(
  unresolvedSharedMutation.ok ? null : unresolvedSharedMutation.reason,
  "ambiguous_mailbox",
  "a matching shared row without source provenance must block mailbox-less mutation transport",
);

assert.notEqual(
  buildCollaborationLocalStateKey("imap-uid-42", "mailbox-a" as never),
  buildCollaborationLocalStateKey("imap-uid-42", "mailbox-b" as never),
  "async collaboration state completion must retain its captured mailbox key",
);

const manualPriorityTargetB = resolveMailboxScopedManualPriorityTarget({
  store: mailboxStore as never,
  messageId: mailboxB.id,
  sourceMailboxId: "mailbox-b" as never,
  sourceMessage: mailboxB as never,
});
assert.equal(manualPriorityTargetB?.mailboxId, "mailbox-b");
assert.equal(
  manualPriorityTargetB?.message,
  mailboxB,
  "a source-known Manual Priority action must resolve the exact mailbox row, not the first same-ID row",
);
assert.equal(
  resolveMailboxScopedManualPriorityTarget({
    store: mailboxStore as never,
    messageId: mailboxB.id,
    sourceMailboxId: "mailbox-a" as never,
    sourceMessage: mailboxB as never,
  }),
  null,
  "Manual Priority must fail closed when its exact object and mailbox scope drift apart",
);
const externalPriorityProjection = {
  ...projectionA,
  collaborationMailboxId: "external-mailbox",
};
const externalProjectionPriorityTarget =
  resolveMailboxScopedManualPriorityTarget({
    store: {
      "shared-collaboration": buildCollections(
        externalPriorityProjection as never,
      ),
    } as never,
    messageId: externalPriorityProjection.id,
    storageMailboxId: "shared-collaboration" as never,
    sourceMailboxId: "external-mailbox" as never,
    sourceMessage: externalPriorityProjection as never,
  });
assert.equal(
  externalProjectionPriorityTarget?.storageMailboxId,
  "shared-collaboration",
);
assert.equal(externalProjectionPriorityTarget?.mailboxId, "external-mailbox");
assert.equal(
  externalProjectionPriorityTarget?.message,
  externalPriorityProjection,
  "Manual Priority must locate an external projection in shared storage while retaining its canonical source mailbox",
);

assert.deepEqual(
  resolveCollaborationInviteStorageMailboxId({
    authoritativeSourceMailboxId: "external-mailbox" as never,
    decodedSourceMailboxId: null,
    ownedMailboxIds: ["mailbox-a" as never, "mailbox-b" as never],
    sharedMailboxId: "shared-collaboration" as never,
    storedEntries: [],
  }),
  {
    sourceMailboxId: "external-mailbox",
    storageMailboxId: "shared-collaboration",
    storedMessageId: null,
  },
  "known external invite provenance must use shared storage, never the first real mailbox",
);
assert.deepEqual(
  resolveCollaborationInviteStorageMailboxId({
    authoritativeSourceMailboxId: "external-mailbox" as never,
    decodedSourceMailboxId: null,
    ownedMailboxIds: ["mailbox-a" as never, "mailbox-b" as never],
    sharedMailboxId: "shared-collaboration" as never,
    storedEntries: [
      {
        mailboxId: "mailbox-a" as never,
        messageId: "legacy-wrong-storage",
        sourceMailboxId: "external-mailbox" as never,
      },
    ],
  }),
  {
    sourceMailboxId: "external-mailbox",
    storageMailboxId: "shared-collaboration",
    storedMessageId: null,
  },
  "invite resolution must not return a row ID from a different storage mailbox",
);
assert.equal(
  resolveCollaborationInviteStorageMailboxId({
    authoritativeSourceMailboxId: "external-mailbox" as never,
    decodedSourceMailboxId: null,
    ownedMailboxIds: ["mailbox-a" as never, "mailbox-b" as never],
    sharedMailboxId: "shared-collaboration" as never,
    storedEntries: [
      {
        mailboxId: "shared-collaboration" as never,
        messageId: "legacy-unknown-source",
        sourceMailboxId: null,
      },
    ],
  }),
  null,
  "unknown-source historical shared invite state must fail closed",
);
assert.deepEqual(
  resolveCollaborationInviteStorageMailboxId({
    authoritativeSourceMailboxId: "mailbox-b" as never,
    decodedSourceMailboxId: null,
    ownedMailboxIds: ["mailbox-a" as never, "mailbox-b" as never],
    sharedMailboxId: "shared-collaboration" as never,
    storedEntries: [],
  }),
  {
    sourceMailboxId: "mailbox-b",
    storageMailboxId: "mailbox-b",
    storedMessageId: null,
  },
);
assert.equal(
  resolveCollaborationInviteStorageMailboxId({
    authoritativeSourceMailboxId: null,
    decodedSourceMailboxId: null,
    ownedMailboxIds: ["mailbox-a" as never, "mailbox-b" as never],
    sharedMailboxId: "shared-collaboration" as never,
    storedEntries: [
      { mailboxId: "mailbox-a" as never, messageId: "imap-uid-42", sourceMailboxId: null },
      { mailboxId: "mailbox-b" as never, messageId: "imap-uid-42", sourceMailboxId: null },
    ],
  }),
  null,
  "ambiguous legacy invite storage must fail closed",
);
assert.equal(
  resolveCollaborationInviteStorageMailboxId({
    authoritativeSourceMailboxId: null,
    decodedSourceMailboxId: null,
    ownedMailboxIds: ["mailbox-a" as never, "mailbox-b" as never],
    sharedMailboxId: "shared-collaboration" as never,
    storedEntries: [],
  }),
  null,
  "an invite without provenance must not pick the first mailbox",
);

const decodedInviteWithServerProvenance = {
  ...mailboxA,
  collaborationMailboxId: undefined,
  threadIdentityContext: undefined,
  serverMailboxId: "external-mailbox",
};
const decodedInviteSourceMailboxId = getCollaborationMessageSourceMailboxId(
  decodedInviteWithServerProvenance as never,
);
const decodedInviteInitialResolution = resolveCollaborationInviteStorageMailboxId({
  authoritativeSourceMailboxId: null,
  decodedSourceMailboxId: decodedInviteSourceMailboxId,
  ownedMailboxIds: ["mailbox-a" as never, "mailbox-b" as never],
  sharedMailboxId: "shared-collaboration" as never,
  storedEntries: [],
});
assert.equal(decodedInviteInitialResolution?.storageMailboxId, "shared-collaboration");
const decodedInviteFallbackStore = upsertMailboxScopedInviteMessage(
  { "shared-collaboration": buildCollections(mailboxA) } as never,
  "shared-collaboration" as never,
  mailboxA.id,
  decodedInviteWithServerProvenance as never,
);
const persistedDecodedInvite =
  decodedInviteFallbackStore["shared-collaboration"].Inbox[0];
assert.equal(
  persistedDecodedInvite.collaborationMailboxId,
  "external-mailbox",
  "decoded server mailbox provenance must become canonical invite metadata when persisted",
);
assert.deepEqual(
  resolveCollaborationInviteStorageMailboxId({
    authoritativeSourceMailboxId: null,
    decodedSourceMailboxId: decodedInviteSourceMailboxId,
    ownedMailboxIds: ["mailbox-a" as never, "mailbox-b" as never],
    sharedMailboxId: "shared-collaboration" as never,
    storedEntries: [
      {
        mailboxId: "shared-collaboration" as never,
        messageId: persistedDecodedInvite.id,
        sourceMailboxId: getCollaborationMessageSourceMailboxId(
          persistedDecodedInvite,
        ),
      },
    ],
  }),
  {
    sourceMailboxId: decodedInviteInitialResolution?.sourceMailboxId,
    storageMailboxId: decodedInviteInitialResolution?.storageMailboxId,
    storedMessageId: persistedDecodedInvite.id,
  },
  "decoded invite provenance must survive fallback persistence instead of invalidating its next render",
);
const decodedInviteWithContextProvenance = {
  ...mailboxA,
  id: "context-provenance-invite",
  collaborationMailboxId: undefined,
  serverMailboxId: undefined,
  threadIdentityContext: {
    ...mailboxA.threadIdentityContext,
    mailboxId: "external-context-mailbox",
  },
};
const decodedContextInviteStore = upsertMailboxScopedInviteMessage(
  {
    "shared-collaboration": Object.fromEntries(
      folders.map((folder) => [folder, []]),
    ),
  } as never,
  "shared-collaboration" as never,
  null,
  decodedInviteWithContextProvenance as never,
);
assert.equal(
  decodedContextInviteStore["shared-collaboration"].Inbox[0]
    .collaborationMailboxId,
  "external-context-mailbox",
  "decoded thread identity provenance must become canonical invite metadata when persisted",
);

const canonicalExternalProjection = {
  ...projectionA,
  id: "shared-projection-external",
  collaborationMailboxId: "external-mailbox",
  collaborationMessageId: "imap-uid-42",
};
const rawExternalInvite = {
  ...mailboxA,
  id: "imap-uid-42",
  collaborationMailboxId: "external-mailbox",
  collaborationMessageId: "imap-uid-42",
};
const externalInviteStore = {
  "shared-collaboration": buildCollections(rawExternalInvite as never),
};
const canonicalInviteStore = upsertMailboxScopedInviteMessage(
  externalInviteStore as never,
  "shared-collaboration" as never,
  rawExternalInvite.id,
  canonicalExternalProjection as never,
);
assert.equal(
  canonicalInviteStore["shared-collaboration"].Inbox[0].id,
  "shared-projection-external",
  "authoritative unowned invites must persist the canonical synthetic projection ID",
);
const acceptedCanonicalInvite = updateMailboxMessageById(
  canonicalInviteStore,
  "shared-collaboration" as never,
  canonicalExternalProjection.id,
  (message) => ({ ...message, isShared: true }),
);
assert.equal(
  acceptedCanonicalInvite.updatedMessage?.id,
  "shared-projection-external",
  "invite actions must target the canonical row that was actually persisted",
);

const updatedA = {
  ...mailboxA,
  collaboration: buildCollaboration("A updated"),
  collaborationMessageId: "canonical-message-a",
  collaborationWorkspaceId: "workspace-1",
  collaborationMailboxId: "mailbox-a",
};
const updateAResult = updateMailboxMessageById(
  mailboxStore as never,
  "mailbox-a" as never,
  "imap-uid-42",
  () => updatedA as never,
);
assert.equal(
  updateAResult.store["mailbox-a"].Inbox[0].collaboration?.requestedBy,
  "Reviewer A updated",
);
assert.equal(
  updateAResult.store["mailbox-b"].Inbox[0].collaboration?.requestedBy,
  "Reviewer B",
  "updating mailbox A must not mutate mailbox B",
);
assert.equal(updateAResult.store["mailbox-b"], mailboxStore["mailbox-b"]);

const updateBResult = updateMailboxMessageById(
  updateAResult.store,
  "mailbox-b" as never,
  "imap-uid-42",
  (message) => ({
    ...message,
    collaboration: buildCollaboration("B updated"),
  }) as never,
);
assert.equal(
  updateBResult.store["mailbox-a"].Inbox[0].collaboration?.requestedBy,
  "Reviewer A updated",
  "updating mailbox B must not mutate mailbox A",
);
assert.equal(
  updateBResult.store["mailbox-b"].Inbox[0].collaboration?.requestedBy,
  "Reviewer B updated",
);

const cleanMailboxStore = {
  "mailbox-a": buildCollections({
    ...mailboxA,
    collaboration: undefined,
    isShared: false,
  } as never),
  "mailbox-b": buildCollections({
    ...mailboxB,
    collaboration: undefined,
    isShared: false,
  } as never),
};
const overlayAOnly = applyMailboxScopedCollaborationThreadOverlays(
  cleanMailboxStore as never,
  [{ mailboxId: "mailbox-a" as never, thread: threadA }],
);
assert.equal(
  overlayAOnly["mailbox-a"].Inbox[0].collaboration?.requestedBy,
  "Reviewer A",
);
assert.equal(
  overlayAOnly["mailbox-b"].Inbox[0].collaboration,
  undefined,
  "a server overlay for A must never attach to the equal-ID message in B",
);
const overlayBoth = applyMailboxScopedCollaborationThreadOverlays(
  cleanMailboxStore as never,
  [
    { mailboxId: "mailbox-a" as never, thread: threadA },
    { mailboxId: "mailbox-b" as never, thread: threadB },
  ],
);
assert.equal(overlayBoth["mailbox-a"].Inbox[0].collaboration?.requestedBy, "Reviewer A");
assert.equal(overlayBoth["mailbox-b"].Inbox[0].collaboration?.requestedBy, "Reviewer B");

class MemoryLocalStorage {
  private values = new Map<string, string>();

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}

const globalWithWindow = globalThis as { window?: unknown };
const previousWindow = globalWithWindow.window;

function buildSnapshot(mailboxId: string, message: ReturnType<typeof buildMessage>) {
  return {
    schemaVersion: LIVE_INBOX_SNAPSHOT_SCHEMA_VERSION,
    threadIdentityVersion: LIVE_INBOX_THREAD_IDENTITY_VERSION,
    classifierVersion: MUSIC_CLASSIFIER_VERSION,
    provider: "custom_imap" as const,
    inboxId: mailboxId,
    email: `${mailboxId}@example.test`,
    fetchedAt: "2026-08-17T10:05:00.000Z",
    folder: "INBOX",
    uidValidity: "900",
    messages: [
      {
        ...message,
        ui_signal: "REPLY",
        classifierVersion: MUSIC_CLASSIFIER_VERSION,
      },
    ],
  };
}

try {
  globalWithWindow.window = {
    localStorage: new MemoryLocalStorage(),
  };

  saveLiveInboxSnapshot(buildSnapshot("mailbox-a", mailboxA) as never);
  saveLiveInboxSnapshot(buildSnapshot("mailbox-b", mailboxB) as never);
  const persistedSnapshots = readLiveInboxSnapshots({
    "mailbox-a": {
      mailboxId: "mailbox-a",
      provider: "custom_imap",
      folder: "INBOX",
    },
    "mailbox-b": {
      mailboxId: "mailbox-b",
      provider: "custom_imap",
      folder: "INBOX",
    },
  });
  assert.equal(Object.keys(persistedSnapshots).length, 2);
  const mailboxASecond = {
    ...mailboxA,
    id: "imap-uid-43",
    imapUid: "43",
    subject: "Mailbox A second",
  };
  const updatedASecond = {
    ...mailboxASecond,
    collaboration: buildCollaboration("A second updated"),
    collaborationMessageId: "canonical-message-a-second",
    collaborationWorkspaceId: "workspace-1",
    collaborationMailboxId: "mailbox-a",
  };
  const snapshotWithTwoMailboxAMessages = {
    ...buildSnapshot("mailbox-a", mailboxA),
    messages: [
      ...buildSnapshot("mailbox-a", mailboxA).messages,
      {
        ...mailboxASecond,
        ui_signal: "REPLY",
        classifierVersion: MUSIC_CLASSIFIER_VERSION,
      },
    ],
  };
  const batchedSnapshots = applyMailboxScopedCollaborationSnapshotUpdates(
    { "mailbox-a": snapshotWithTwoMailboxAMessages } as never,
    [
      {
        mailboxId: "mailbox-a" as never,
        messageId: updatedA.id,
        updatedMessage: updatedA as never,
      },
      {
        mailboxId: "mailbox-a" as never,
        messageId: updatedASecond.id,
        updatedMessage: updatedASecond as never,
      },
    ],
  );
  assert.deepEqual(
    batchedSnapshots["mailbox-a"].messages.map(
      (message) => message.collaboration?.requestedBy,
    ),
    ["Reviewer A updated", "Reviewer A second updated"],
    "multiple overlays for one mailbox must accumulate in one snapshot before persistence",
  );
  const restoredSnapshotMessage = mergeMailboxScopedCollaborationSnapshotMessage(
    {
      ...mailboxA,
      collaboration: undefined,
      collaborationMessageId: undefined,
      collaborationWorkspaceId: undefined,
      collaborationMailboxId: undefined,
    } as never,
    batchedSnapshots["mailbox-a"].messages[0] as never,
  );
  assert.equal(restoredSnapshotMessage.collaborationMessageId, "canonical-message-a");
  assert.equal(restoredSnapshotMessage.collaborationWorkspaceId, "workspace-1");
  assert.equal(restoredSnapshotMessage.collaborationMailboxId, "mailbox-a");
  assert.equal(
    restoredSnapshotMessage.collaboration?.requestedBy,
    "Reviewer A updated",
    "live-snapshot reconciliation must restore collaboration and its canonical identity together",
  );
  const nextSnapshotA = updateMailboxSnapshotCollaboration(
    persistedSnapshots["mailbox-a"],
    "mailbox-a" as never,
    "imap-uid-42",
    updatedA as never,
  );
  assert.ok(nextSnapshotA);
  saveLiveInboxSnapshot(nextSnapshotA as never);
  const rehydratedSnapshots = readLiveInboxSnapshots({
    "mailbox-a": {
      mailboxId: "mailbox-a",
      provider: "custom_imap",
      folder: "INBOX",
    },
    "mailbox-b": {
      mailboxId: "mailbox-b",
      provider: "custom_imap",
      folder: "INBOX",
    },
  });
  assert.equal(
    (rehydratedSnapshots["mailbox-a"].messages[0] as typeof mailboxA).collaboration
      ?.requestedBy,
    "Reviewer A updated",
  );
  assert.equal(
    (rehydratedSnapshots["mailbox-b"].messages[0] as typeof mailboxB).collaboration
      ?.requestedBy,
    "Reviewer B",
    "persisting mailbox A must leave mailbox B's same-ID snapshot untouched",
  );
  assert.equal(
    (rehydratedSnapshots["mailbox-a"].messages[0] as typeof updatedA)
      .collaborationMessageId,
    "canonical-message-a",
  );
  assert.equal(
    (rehydratedSnapshots["mailbox-a"].messages[0] as typeof updatedA)
      .collaborationWorkspaceId,
    "workspace-1",
  );
  assert.equal(
    (rehydratedSnapshots["mailbox-a"].messages[0] as typeof updatedA)
      .collaborationMailboxId,
    "mailbox-a",
    "canonical source identity must survive snapshot save/read",
  );
  const hydratedA = hydrateLiveInboxSnapshot(rehydratedSnapshots["mailbox-a"]);
  const hydratedB = hydrateLiveInboxSnapshot(rehydratedSnapshots["mailbox-b"]);
  assert.equal(hydratedA.context?.mailboxId, "mailbox-a");
  assert.equal(hydratedB.context?.mailboxId, "mailbox-b");
  assert.equal(
    (hydratedA.messages[0] as typeof mailboxA).collaboration?.requestedBy,
    "Reviewer A updated",
  );
  assert.equal(
    (hydratedB.messages[0] as typeof mailboxB).collaboration?.requestedBy,
    "Reviewer B",
  );
  assert.equal(
    (hydratedA.messages[0] as typeof updatedA).collaborationMessageId,
    "canonical-message-a",
    "canonical source identity must survive snapshot hydration",
  );
} finally {
  if (previousWindow === undefined) {
    delete globalWithWindow.window;
  } else {
    globalWithWindow.window = previousWindow;
  }
}

console.log("WorkspaceShell collaboration mailbox identity tests passed");
