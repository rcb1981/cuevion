import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  buildProviderArchiveMutationTarget,
  createProviderArchiveCoordinator,
  executeProviderArchiveAction,
  hasPendingProviderArchiveForMailbox,
  type ProviderArchiveCandidate,
  type ProviderArchiveMutationRequest,
  type ProviderArchiveMutationResponse,
} from "./providerArchiveAction";
import {
  PROVIDER_ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
  PROVIDER_ARCHIVE_INVALID_SOURCE_MESSAGE,
  PROVIDER_ARCHIVE_PENDING_MAILBOX_MESSAGE,
  PROVIDER_ARCHIVE_RECONCILIATION_MESSAGE,
  resolveProviderArchivePreflightBlock,
  type ProviderArchiveInvalidSourceReason,
} from "./providerArchivePreflight";
import {
  ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
  ARCHIVE_REFRESH_ERROR_MESSAGE,
  createGmailInboxAuthority,
  createGmailArchiveReconciliationCoordinator,
  resolveMailboxRefreshPlan,
  resolveProviderArchiveRefreshSemantics,
  removeProvenGmailInboxReentriesFromArchive,
  resolveSuccessfulInboxRefreshPresentation,
  shouldApplyProviderArchiveResponse,
  startIndependentMailboxFetches,
  summarizeStartupMailboxRefreshResults,
} from "./mailboxRefreshSemantics";

const archiveFailure = (code: string) => ({
  ok: false as const,
  error: { code },
});

const exactGmailArchiveCandidate: ProviderArchiveCandidate = {
  provider: "google",
  mailboxId: "gmail-sync-mailbox",
  folder: "Inbox",
  providerMessageId: "gmail-sync-provider-message",
};
const exactImapArchiveCandidate: ProviderArchiveCandidate = {
  provider: "custom_imap",
  mailboxId: "imap-sync-mailbox",
  folder: "INBOX",
  imapUid: "42",
  uidValidity: "900",
};

function resolveCandidatePreflight(
  candidate: ProviderArchiveCandidate,
  overrides: {
    invalidSourceReason?: ProviderArchiveInvalidSourceReason;
    isGmailArchiveReconciliationRunning?: boolean;
    isProviderArchiveCapabilityUnavailable?: boolean;
    hasPendingArchiveMutation?: boolean;
  } = {},
) {
  const target = buildProviderArchiveMutationTarget(candidate);
  const targetInvalidSourceReason: ProviderArchiveInvalidSourceReason | null =
    target.ok
      ? null
      : target.reason === "invalid_mailbox_id"
        ? "mailbox"
        : target.reason === "invalid_gmail_source_folder" ||
            target.reason === "invalid_imap_source_folder"
          ? "folder"
          : "provider_identity";
  return resolveProviderArchivePreflightBlock({
    invalidSourceReason:
      overrides.invalidSourceReason ?? targetInvalidSourceReason,
    isGmailArchiveReconciliationRunning:
      overrides.isGmailArchiveReconciliationRunning ?? false,
    isProviderArchiveCapabilityUnavailable:
      overrides.isProviderArchiveCapabilityUnavailable ?? false,
    hasPendingArchiveMutation:
      overrides.hasPendingArchiveMutation ?? false,
  });
}

const gmailSyncPresentation = {
  isSyncingMailbox: true,
  candidate: exactGmailArchiveCandidate,
};
const imapSyncPresentation = {
  isSyncingMailbox: true,
  candidate: exactImapArchiveCandidate,
};
assert.equal(gmailSyncPresentation.isSyncingMailbox, true);
assert.equal(imapSyncPresentation.isSyncingMailbox, true);
assert.equal(resolveCandidatePreflight(gmailSyncPresentation.candidate), null);
assert.equal(resolveCandidatePreflight(imapSyncPresentation.candidate), null);

assert.deepEqual(
  resolveCandidatePreflight(exactGmailArchiveCandidate, {
    isGmailArchiveReconciliationRunning: true,
  }),
  {
    reason: "reconciliation_running",
    message: PROVIDER_ARCHIVE_RECONCILIATION_MESSAGE,
  },
);
assert.deepEqual(
  resolveCandidatePreflight(exactImapArchiveCandidate, {
    isProviderArchiveCapabilityUnavailable: true,
  }),
  {
    reason: "capability_unavailable",
    message: PROVIDER_ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
  },
);
assert.deepEqual(
  resolveCandidatePreflight(exactImapArchiveCandidate, {
    isProviderArchiveCapabilityUnavailable: true,
    hasPendingArchiveMutation: true,
  }),
  {
    reason: "capability_unavailable",
    message: PROVIDER_ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
  },
);
assert.deepEqual(
  resolveCandidatePreflight(
    { ...exactImapArchiveCandidate, imapUid: null },
    {
      isProviderArchiveCapabilityUnavailable: true,
      hasPendingArchiveMutation: true,
    },
  ),
  {
    reason: "invalid_source",
    invalidSourceReason: "provider_identity",
    message: PROVIDER_ARCHIVE_INVALID_SOURCE_MESSAGE,
  },
);
assert.deepEqual(
  resolveCandidatePreflight(
    { ...exactImapArchiveCandidate, imapUid: null },
    {
      isGmailArchiveReconciliationRunning: true,
      isProviderArchiveCapabilityUnavailable: true,
      hasPendingArchiveMutation: true,
    },
  ),
  {
    reason: "reconciliation_running",
    message: PROVIDER_ARCHIVE_RECONCILIATION_MESSAGE,
  },
);
assert.deepEqual(
  resolveCandidatePreflight(exactGmailArchiveCandidate, {
    hasPendingArchiveMutation: true,
  }),
  {
    reason: "mutation_pending",
    message: PROVIDER_ARCHIVE_PENDING_MAILBOX_MESSAGE,
  },
);

for (const [candidate, invalidSourceReason] of [
  [
    { ...exactGmailArchiveCandidate, providerMessageId: null },
    "provider_identity",
  ],
  [{ ...exactGmailArchiveCandidate, folder: "Archive" }, "folder"],
  [{ ...exactGmailArchiveCandidate, mailboxId: "" }, "mailbox"],
] as const) {
  assert.deepEqual(resolveCandidatePreflight(candidate), {
    reason: "invalid_source",
    invalidSourceReason,
    message: PROVIDER_ARCHIVE_INVALID_SOURCE_MESSAGE,
  });
}
for (const invalidSourceReason of [
  "selection",
  "folder",
  "mailbox",
] as const) {
  assert.deepEqual(
    resolveCandidatePreflight(exactGmailArchiveCandidate, {
      invalidSourceReason,
    }),
    {
      reason: "invalid_source",
      invalidSourceReason,
      message: PROVIDER_ARCHIVE_INVALID_SOURCE_MESSAGE,
    },
    invalidSourceReason,
  );
}
assert.deepEqual(
  resolveCandidatePreflight(
    { ...exactGmailArchiveCandidate, providerMessageId: null },
    { isGmailArchiveReconciliationRunning: true },
  ),
  {
    reason: "reconciliation_running",
    message: PROVIDER_ARCHIVE_RECONCILIATION_MESSAGE,
  },
);

const plan = (
  reason: Parameters<typeof resolveMailboxRefreshPlan>[0]["reason"],
  overrides: Partial<Parameters<typeof resolveMailboxRefreshPlan>[0]> = {},
) =>
  resolveMailboxRefreshPlan({
    reason,
    inboxFetchInFlight: false,
    archiveFetchInFlight: false,
    hasArchiveSnapshot: false,
    archiveCapability: "unknown",
    ...overrides,
  });

assert.deepEqual(plan("mailbox_open"), {
  shouldFetchInbox: true,
  shouldFetchArchive: false,
  archiveErrorScope: null,
});
assert.deepEqual(plan("interval"), {
  shouldFetchInbox: true,
  shouldFetchArchive: false,
  archiveErrorScope: null,
});
assert.deepEqual(plan("startup"), {
  shouldFetchInbox: true,
  shouldFetchArchive: true,
  archiveErrorScope: "background",
});
assert.deepEqual(plan("manual", { hasArchiveSnapshot: true }), {
  shouldFetchInbox: true,
  shouldFetchArchive: true,
  archiveErrorScope: "background",
});
assert.deepEqual(plan("reconcile", { hasArchiveSnapshot: true }), {
  shouldFetchInbox: true,
  shouldFetchArchive: true,
  archiveErrorScope: "background",
});
assert.deepEqual(plan("archive_open"), {
  shouldFetchInbox: false,
  shouldFetchArchive: true,
  archiveErrorScope: "folder",
});
assert.equal(
  plan("archive_open", { hasArchiveSnapshot: true }).shouldFetchArchive,
  false,
);
assert.equal(
  plan("archive_open", { archiveFetchInFlight: true }).shouldFetchArchive,
  false,
);
assert.equal(
  plan("startup", { archiveFetchInFlight: true }).shouldFetchArchive,
  false,
);
assert.equal(
  plan("archive_open", { archiveCapability: "unavailable" }).shouldFetchArchive,
  false,
);
assert.equal(
  plan("manual", { archiveCapability: "unavailable" }).shouldFetchArchive,
  true,
);
assert.deepEqual(plan("manual", { inboxFetchInFlight: true }), {
  shouldFetchInbox: false,
  shouldFetchArchive: true,
  archiveErrorScope: "background",
});
assert.deepEqual(plan("manual", { archiveFetchInFlight: true }), {
  shouldFetchInbox: true,
  shouldFetchArchive: false,
  archiveErrorScope: null,
});

const gmailInboxAuthority = createGmailInboxAuthority();
const archivedProviderMessageId = "gmail-message-1";
const otherGmailInboxMessage = {
  serverMailboxId: "mailbox-a",
  providerFolder: "Inbox",
  providerMessageId: "gmail-message-2",
  labelIds: ["INBOX"],
};
const archivedGmailInboxMessage = {
  serverMailboxId: "mailbox-a",
  providerFolder: "Inbox",
  providerMessageId: archivedProviderMessageId,
  labelIds: ["INBOX"],
};
const preArchiveGeneration =
  gmailInboxAuthority.captureGeneration("mailbox-a");

assert.equal(preArchiveGeneration, 0);
assert.equal(
  gmailInboxAuthority.confirmArchive(
    "mailbox-a",
    archivedProviderMessageId,
  ),
  1,
);
assert.equal(
  gmailInboxAuthority.isRecentlyArchived(
    "mailbox-a",
    archivedProviderMessageId,
  ),
  true,
);
assert.deepEqual(
  gmailInboxAuthority.resolveFetchResponse({
    mailboxId: "mailbox-a",
    generationAtFetchStart: preArchiveGeneration,
    messages: [archivedGmailInboxMessage, otherGmailInboxMessage],
  }),
  {
    stale: true,
    messages: [],
    provenReentryProviderMessageIds: [],
  },
);
assert.deepEqual(
  gmailInboxAuthority.filterSnapshotMessages("mailbox-a", [
    archivedGmailInboxMessage,
    otherGmailInboxMessage,
  ]),
  [otherGmailInboxMessage],
);
assert.deepEqual(
  gmailInboxAuthority.filterSnapshotMessages("mailbox-b", [
    {
      ...archivedGmailInboxMessage,
      serverMailboxId: "mailbox-b",
    },
  ]),
  [
    {
      ...archivedGmailInboxMessage,
      serverMailboxId: "mailbox-b",
    },
  ],
);

const postArchiveGeneration =
  gmailInboxAuthority.captureGeneration("mailbox-a");
const missingLabels = gmailInboxAuthority.resolveFetchResponse({
  mailboxId: "mailbox-a",
  generationAtFetchStart: postArchiveGeneration,
  messages: [
    {
      serverMailboxId: "mailbox-a",
      providerMessageId: archivedProviderMessageId,
    },
    otherGmailInboxMessage,
  ],
});
assert.equal(missingLabels.stale, false);
assert.deepEqual(missingLabels.messages, [otherGmailInboxMessage]);
assert.equal(
  gmailInboxAuthority.isRecentlyArchived(
    "mailbox-a",
    archivedProviderMessageId,
  ),
  true,
);

const noInboxLabel = gmailInboxAuthority.resolveFetchResponse({
  mailboxId: "mailbox-a",
  generationAtFetchStart: postArchiveGeneration,
  messages: [
    {
      ...archivedGmailInboxMessage,
      labelIds: ["UNREAD", "STARRED"],
    },
  ],
});
assert.equal(noInboxLabel.stale, false);
assert.deepEqual(noInboxLabel.messages, []);
assert.equal(
  gmailInboxAuthority.isRecentlyArchived(
    "mailbox-a",
    archivedProviderMessageId,
  ),
  true,
);

const duplicateInboxLabels = gmailInboxAuthority.resolveFetchResponse({
  mailboxId: "mailbox-a",
  generationAtFetchStart: postArchiveGeneration,
  messages: [
    {
      ...archivedGmailInboxMessage,
      labelIds: ["INBOX", "INBOX"],
    },
  ],
});
assert.equal(duplicateInboxLabels.stale, false);
assert.deepEqual(duplicateInboxLabels.messages, []);
assert.equal(
  gmailInboxAuthority.isRecentlyArchived(
    "mailbox-a",
    archivedProviderMessageId,
  ),
  true,
);

const wrongMailboxProof = gmailInboxAuthority.resolveFetchResponse({
  mailboxId: "mailbox-a",
  generationAtFetchStart: postArchiveGeneration,
  messages: [
    {
      ...archivedGmailInboxMessage,
      serverMailboxId: "mailbox-b",
    },
  ],
});
assert.equal(wrongMailboxProof.stale, false);
assert.deepEqual(wrongMailboxProof.messages, []);
assert.equal(
  gmailInboxAuthority.isRecentlyArchived(
    "mailbox-a",
    archivedProviderMessageId,
  ),
  true,
);

const duplicateProviderProof = gmailInboxAuthority.resolveFetchResponse({
  mailboxId: "mailbox-a",
  generationAtFetchStart: postArchiveGeneration,
  messages: [
    archivedGmailInboxMessage,
    {
      ...archivedGmailInboxMessage,
      labelIds: ["STARRED"],
    },
  ],
});
assert.equal(duplicateProviderProof.stale, false);
assert.deepEqual(duplicateProviderProof.messages, []);
assert.deepEqual(
  duplicateProviderProof.provenReentryProviderMessageIds,
  [],
);
assert.equal(
  gmailInboxAuthority.isRecentlyArchived(
    "mailbox-a",
    archivedProviderMessageId,
  ),
  true,
);

const provenReentry = gmailInboxAuthority.resolveFetchResponse({
  mailboxId: "mailbox-a",
  generationAtFetchStart: postArchiveGeneration,
  messages: [archivedGmailInboxMessage, otherGmailInboxMessage],
});
assert.equal(provenReentry.stale, false);
assert.deepEqual(provenReentry.messages, [
  archivedGmailInboxMessage,
  otherGmailInboxMessage,
]);
assert.deepEqual(provenReentry.provenReentryProviderMessageIds, [
  archivedProviderMessageId,
]);
assert.equal(
  gmailInboxAuthority.isRecentlyArchived(
    "mailbox-a",
    archivedProviderMessageId,
  ),
  false,
);
const existingArchiveMessages = [
  {
    serverMailboxId: "mailbox-a",
    providerFolder: "Archive",
    providerMessageId: archivedProviderMessageId,
  },
  {
    serverMailboxId: "mailbox-b",
    providerFolder: "Archive",
    providerMessageId: archivedProviderMessageId,
  },
  {
    serverMailboxId: "mailbox-a",
    providerFolder: "Archive",
    providerMessageId: "gmail-message-2",
  },
];
const archiveAfterProvenReentry =
  removeProvenGmailInboxReentriesFromArchive(
    "mailbox-a",
    new Set(provenReentry.provenReentryProviderMessageIds),
    existingArchiveMessages,
  );
assert.deepEqual(
  archiveAfterProvenReentry,
  existingArchiveMessages.slice(1),
);
assert.equal(
  provenReentry.messages.filter(
    (message) => message.providerMessageId === archivedProviderMessageId,
  ).length,
  1,
);
assert.equal(
  archiveAfterProvenReentry.filter(
    (message) =>
      message.serverMailboxId === "mailbox-a" &&
      message.providerMessageId === archivedProviderMessageId,
  ).length,
  0,
);
const resetGmailInboxAuthority = createGmailInboxAuthority();
resetGmailInboxAuthority.confirmArchive("mailbox-reset", "provider-reset");
resetGmailInboxAuthority.resetMailbox("mailbox-reset");
assert.equal(
  resetGmailInboxAuthority.captureGeneration("mailbox-reset"),
  0,
);
assert.equal(
  resetGmailInboxAuthority.isRecentlyArchived(
    "mailbox-reset",
    "provider-reset",
  ),
  false,
);

assert.equal(
  shouldApplyProviderArchiveResponse({
    requestConnectionKey: "connection-1",
    currentConnectionKey: "connection-1",
    requestConnectionEpoch: 1,
    currentConnectionEpoch: 1,
    archiveStateVersionAtRequest: "archive-v1",
    currentArchiveStateVersion: "archive-v1",
  }),
  true,
);
assert.equal(
  shouldApplyProviderArchiveResponse({
    requestConnectionKey: "connection-1",
    currentConnectionKey: "connection-2",
    requestConnectionEpoch: 1,
    currentConnectionEpoch: 2,
    archiveStateVersionAtRequest: "archive-v1",
    currentArchiveStateVersion: "archive-v1",
  }),
  false,
);
assert.equal(
  shouldApplyProviderArchiveResponse({
    requestConnectionKey: "connection-1",
    currentConnectionKey: "connection-1",
    requestConnectionEpoch: 1,
    currentConnectionEpoch: 1,
    archiveStateVersionAtRequest: "archive-v1",
    currentArchiveStateVersion: "archive-after-mutation",
  }),
  false,
);
assert.equal(
  shouldApplyProviderArchiveResponse({
    requestConnectionKey: "connection-1",
    currentConnectionKey: "connection-1",
    requestConnectionEpoch: 1,
    currentConnectionEpoch: 3,
    archiveStateVersionAtRequest: "archive-empty",
    currentArchiveStateVersion: "archive-empty",
  }),
  false,
);

const unsupportedArchive = resolveProviderArchiveRefreshSemantics({
  provider: "custom_imap",
  archiveResponse: archiveFailure("archive_folder_unavailable"),
  archiveSnapshotApplied: false,
});
const unsupportedArchivePresentation =
  resolveSuccessfulInboxRefreshPresentation({
    inboxWarningMessage: null,
    archiveSemantics: unsupportedArchive,
  });

assert.deepEqual(unsupportedArchive, {
  capability: "unavailable",
  capabilityMessage: ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
  preserveExistingArchive: true,
  mailboxSyncError: null,
  contributesPartial: false,
  shouldRetryArchive: false,
  fallbackFolderName: null,
});
assert.deepEqual(unsupportedArchivePresentation, {
  result: "synced",
  mailboxSyncError: null,
});
assert.deepEqual(
  resolveSuccessfulInboxRefreshPresentation({ inboxWarningMessage: null }),
  {
    result: "synced",
    mailboxSyncError: null,
  },
);

const existingArchive = Object.freeze([
  Object.freeze({ id: "existing-archive-message", subject: "Keep exactly" }),
]);
const freshInbox = Object.freeze([
  Object.freeze({ id: "fresh-inbox-message", subject: "Fresh Inbox" }),
]);
const refreshedCollections = {
  Inbox: freshInbox,
  Archive: unsupportedArchive.preserveExistingArchive
    ? existingArchive
    : Object.freeze([]),
};
assert.strictEqual(refreshedCollections.Inbox, freshInbox);
assert.strictEqual(refreshedCollections.Archive, existingArchive);
assert.deepEqual(refreshedCollections.Archive, existingArchive);

for (const code of [
  "archive_folder_ambiguous",
  "archive_snapshot_failed",
  "invalid_credentials",
  "reconnect_required",
  "archive_response_invalid",
  "archive_fetch_failed",
  "unknown_archive_failure",
]) {
  const semantics = resolveProviderArchiveRefreshSemantics({
    provider: "custom_imap",
    archiveResponse: archiveFailure(code),
    archiveSnapshotApplied: false,
  });
  assert.equal(semantics.capability, "unknown", `${code} is not a capability absence`);
  assert.equal(semantics.mailboxSyncError, ARCHIVE_REFRESH_ERROR_MESSAGE);
  assert.equal(semantics.contributesPartial, true);
  assert.equal(
    resolveSuccessfulInboxRefreshPresentation({
      inboxWarningMessage: null,
      archiveSemantics: semantics,
    }).result,
    "partial",
  );
}

const backgroundArchiveFailure = resolveProviderArchiveRefreshSemantics({
  provider: "google",
  archiveResponse: archiveFailure("archive_fetch_failed"),
  archiveSnapshotApplied: false,
});
assert.equal(backgroundArchiveFailure.contributesPartial, true);
assert.deepEqual(
  resolveSuccessfulInboxRefreshPresentation({ inboxWarningMessage: null }),
  {
    result: "synced",
    mailboxSyncError: null,
  },
);

for (const code of ["archive_folder_unavailable", "gmail_fetch_failed"]) {
  const gmailFailure = resolveProviderArchiveRefreshSemantics({
    provider: "google",
    archiveResponse: archiveFailure(code),
    archiveSnapshotApplied: false,
  });
  assert.equal(gmailFailure.capability, "unknown");
  assert.equal(gmailFailure.contributesPartial, true);
  assert.equal(gmailFailure.mailboxSyncError, ARCHIVE_REFRESH_ERROR_MESSAGE);
}

const malformedSuccessfulArchive = resolveProviderArchiveRefreshSemantics({
  provider: "custom_imap",
  archiveResponse: { ok: true },
  archiveSnapshotApplied: false,
});
assert.equal(malformedSuccessfulArchive.contributesPartial, true);
assert.equal(malformedSuccessfulArchive.mailboxSyncError, ARCHIVE_REFRESH_ERROR_MESSAGE);

const incompleteInboxWithUnavailableArchive =
  resolveProviderArchiveRefreshSemantics({
    provider: "custom_imap",
    archiveResponse: archiveFailure("archive_folder_unavailable"),
    archiveSnapshotApplied: false,
  });
assert.equal(incompleteInboxWithUnavailableArchive.capability, "unavailable");
assert.equal(incompleteInboxWithUnavailableArchive.contributesPartial, false);
assert.equal(
  resolveSuccessfulInboxRefreshPresentation({
    inboxWarningMessage: "Some older messages could not be refreshed.",
    archiveSemantics: incompleteInboxWithUnavailableArchive,
  }).result,
  "partial",
);

const successfulGmailArchive = resolveProviderArchiveRefreshSemantics({
  provider: "google",
  archiveResponse: { ok: true },
  archiveSnapshotApplied: true,
});
const successfulCustomImapArchive = resolveProviderArchiveRefreshSemantics({
  provider: "custom_imap",
  archiveResponse: { ok: true },
  archiveSnapshotApplied: true,
});
assert.equal(successfulCustomImapArchive.capability, "available");
assert.equal(successfulCustomImapArchive.capabilityMessage, null);
assert.equal(successfulCustomImapArchive.mailboxSyncError, null);
assert.equal(successfulCustomImapArchive.contributesPartial, false);
const successfulGmailPresentation = resolveSuccessfulInboxRefreshPresentation({
  inboxWarningMessage: null,
  archiveSemantics: successfulGmailArchive,
});
const mixedMailboxStartup = summarizeStartupMailboxRefreshResults([
  unsupportedArchivePresentation.result,
  successfulGmailPresentation.result,
]);
assert.deepEqual(mixedMailboxStartup, {
  status: "done",
  feedbackMessage: "Inbox refresh complete",
});
assert.doesNotMatch(
  `${unsupportedArchivePresentation.mailboxSyncError ?? ""} ${mixedMailboxStartup.feedbackMessage}`,
  /quota|Some inboxes could not be refreshed/i,
);
assert.deepEqual(summarizeStartupMailboxRefreshResults(["failed"]), {
  status: "partial_error",
  feedbackMessage: "Some inboxes could not be refreshed",
});

const source = fs.readFileSync(
  path.resolve(__dirname, "../components/workspace/WorkspaceShell.tsx"),
  "utf8",
);

function section(start: string, end: string) {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(startIndex, -1, `missing WorkspaceShell marker: ${start}`);
  assert.notEqual(endIndex, -1, `missing WorkspaceShell marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

function nestedSection(content: string, start: string, end: string) {
  const startIndex = content.indexOf(start);
  const endIndex = content.indexOf(end, startIndex + start.length);
  assert.notEqual(startIndex, -1, `missing nested marker: ${start}`);
  assert.notEqual(endIndex, -1, `missing nested marker: ${end}`);
  return content.slice(startIndex, endIndex);
}

const archiveHandler = section(
  "async function archiveMessagesFromEntryPoint",
  "const archiveSelectedMessages",
);
const providerAuthoritativeMailbox = section(
  "const isProviderAuthoritativeArchiveMailbox",
  "const archiveMessagesLocally",
);
assert.match(providerAuthoritativeMailbox, /managedMailbox\?\.connected/);
assert.match(
  providerAuthoritativeMailbox,
  /managedMailbox\.connectionStatus === "connected"/,
);
assert.match(
  providerAuthoritativeMailbox,
  /managedMailbox\.provider === "google"/,
);
assert.match(
  providerAuthoritativeMailbox,
  /managedMailbox\.provider === "custom_imap"/,
);
assert.match(archiveHandler, /executeProviderArchiveAction\(\{/);
assert.equal(
  (archiveHandler.match(/executeProviderArchiveAction\(\{/g) ?? []).length,
  1,
);
assert.match(archiveHandler, /coordinator: providerArchiveCoordinator/);
assert.match(
  archiveHandler,
  /applySuccess:[\s\S]*onApplyProviderArchiveMutationSuccess/,
);
assert.match(archiveHandler, /const result = await archivePromise/);
assert.match(archiveHandler, /if \(!result\.applied\)/);
assert.match(archiveHandler, /hasPendingProviderArchiveForMailbox/);
assert.match(archiveHandler, /messageIds\.length !== 1/);
assert.match(archiveHandler, /isSharedView/);
assert.match(archiveHandler, /activeSmartFolder/);
assert.doesNotMatch(
  archiveHandler,
  /isSyncingMailbox/,
  "ordinary Gmail and custom-IMAP sync must not block an exact Archive action",
);
assert.match(archiveHandler, /location\?\.folder !== "Inbox"/);
assert.match(
  archiveHandler,
  /const reconciliationPreflightBlock = resolveProviderArchivePreflightBlock\(\{[\s\S]*isGmailArchiveReconciliationRunning\(location\.mailboxId\)[\s\S]*setMailboxActionToastMessage\(reconciliationPreflightBlock\.message\)/,
);
assert.match(
  archiveHandler,
  /const pendingPreflightBlock = resolveProviderArchivePreflightBlock\(\{[\s\S]*hasPendingProviderArchiveForMailbox[\s\S]*setMailboxActionToastMessage\(pendingPreflightBlock\.message\)/,
);
assert.match(
  archiveHandler,
  /sourceManagedMailbox\.provider === "custom_imap" &&\s+isProviderArchiveCapabilityUnavailable\(sourceLocation\.mailboxId\)/,
);
assert.match(
  archiveHandler,
  /resolveExactGmailArchiveMutationTarget\(\{/,
);
assert.doesNotMatch(
  archiveHandler,
  /sourceThreadKey|groupedMessages|groupedProviderMessageIds/,
);
assert.match(archiveHandler, /exactSourceMessageMatches\.length === 1/);
assert.match(
  archiveHandler,
  /sourceMessage\.serverMailboxId !== sourceManagedMailbox\.id/,
);
const executeIndex = archiveHandler.indexOf(
  "const archivePromise = executeProviderArchiveAction",
);
const runningReconciliationGuardIndex = archiveHandler.indexOf(
  "isGmailArchiveReconciliationRunning(",
);
const genericGuardIndex = archiveHandler.indexOf("messageIds.length !== 1");
const sourceFolderGuardIndex = archiveHandler.indexOf(
  'location?.folder !== "Inbox"',
);
const exactMailboxIndex = archiveHandler.indexOf(
  "sourceMessage.serverMailboxId !== sourceManagedMailbox.id",
);
const exactTargetIndex = archiveHandler.indexOf(
  "resolveExactGmailArchiveMutationTarget({",
);
const targetValidationIndex = archiveHandler.indexOf("if (!target.ok)");
const reconciliationResolverIndex = archiveHandler.indexOf(
  "const reconciliationPreflightBlock",
);
const pendingResolverIndex = archiveHandler.indexOf(
  "const pendingPreflightBlock",
);
const pendingMailboxIndex = archiveHandler.indexOf(
  "hasPendingProviderArchiveForMailbox",
);
const capabilityUnavailableIndex = archiveHandler.indexOf(
  "isProviderArchiveCapabilityUnavailable(sourceLocation.mailboxId)",
);
const messageResolutionIndex = archiveHandler.indexOf("const messageId");
const genericPreflightGuard = archiveHandler.slice(
  genericGuardIndex,
  reconciliationResolverIndex,
);
const reconciliationPreflightGuard = archiveHandler.slice(
  reconciliationResolverIndex,
  messageResolutionIndex,
);
const pendingVisibleIndex = archiveHandler.indexOf(
  "setPendingProviderArchiveKeys",
  executeIndex,
);
const awaitIndex = archiveHandler.indexOf(
  "const result = await archivePromise",
  pendingVisibleIndex,
);
const pendingReleasedIndex = archiveHandler.indexOf(
  "setPendingProviderArchiveKeys",
  awaitIndex,
);
assert.ok(executeIndex >= 0);
assert.ok(genericGuardIndex >= 0);
assert.ok(genericGuardIndex < sourceFolderGuardIndex);
assert.ok(sourceFolderGuardIndex < reconciliationResolverIndex);
assert.ok(runningReconciliationGuardIndex >= 0);
assert.match(
  genericPreflightGuard,
  /showProviderArchiveBlockedMessage\(invalidEntryPointReason\)/,
);
assert.doesNotMatch(genericPreflightGuard, /isGmailArchiveReconciliationRunning/);
assert.ok(reconciliationResolverIndex < runningReconciliationGuardIndex);
assert.ok(runningReconciliationGuardIndex < exactMailboxIndex);
assert.match(
  reconciliationPreflightGuard,
  /setMailboxActionToastMessage\(reconciliationPreflightBlock\.message\)/,
);
assert.doesNotMatch(
  reconciliationPreflightGuard,
  /showProviderArchiveBlockedMessage/,
);
assert.ok(exactMailboxIndex < exactTargetIndex);
assert.ok(exactTargetIndex < targetValidationIndex);
assert.ok(targetValidationIndex < capabilityUnavailableIndex);
assert.ok(capabilityUnavailableIndex < pendingMailboxIndex);
assert.ok(targetValidationIndex < pendingResolverIndex);
assert.ok(pendingResolverIndex < pendingMailboxIndex);
assert.ok(pendingMailboxIndex < executeIndex);
assert.match(
  archiveHandler,
  /if \(pendingPreflightBlock\) \{\s+setMailboxActionToastMessage\(pendingPreflightBlock\.message\);\s+closeMenus\(\);\s+return;/,
);
assert.ok(executeIndex < pendingVisibleIndex);
assert.ok(pendingVisibleIndex < awaitIndex);
assert.ok(awaitIndex < pendingReleasedIndex);
assert.match(
  archiveHandler,
  /sourceMessage\.serverMailboxId !== sourceManagedMailbox\.id\s+\) \{\s+closeMenus\(\);\s+showProviderArchiveBlockedMessage\("mailbox"\);/,
);
assert.match(
  archiveHandler,
  /if \(sourceManagedMailbox\.provider === "google"\) \{\s+if \(!gmailArchiveResolution\) \{\s+closeMenus\(\);\s+showProviderArchiveBlockedMessage\("provider_identity"\);/,
);
const reconciliationIndex = archiveHandler.indexOf(
  "onReconcileGmailArchive(sourceManagedMailbox.id as InboxId)",
);
const mutationSettledIndex = archiveHandler.indexOf(
  "onProviderArchiveMutationSettled(sourceManagedMailbox.id as InboxId)",
);
const capabilityResultIndex = archiveHandler.indexOf(
  'result.classification === "capability_unavailable"',
);
const uncertainResultIndex = archiveHandler.indexOf(
  'result.classification === "uncertain"',
);
assert.ok(pendingReleasedIndex < reconciliationIndex);
assert.ok(pendingReleasedIndex < mutationSettledIndex);
assert.ok(mutationSettledIndex < reconciliationIndex);
assert.ok(mutationSettledIndex < capabilityResultIndex);
assert.ok(capabilityResultIndex < uncertainResultIndex);
const capabilityResultBranch = archiveHandler.slice(
  capabilityResultIndex,
  uncertainResultIndex,
);
assert.match(
  capabilityResultBranch,
  /onProviderArchiveCapabilityUnavailable\([\s\S]*sourceManagedMailbox\.id/,
);
assert.match(
  capabilityResultBranch,
  /PROVIDER_ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE/,
);
assert.doesNotMatch(
  capabilityResultBranch,
  /onReconcileGmailArchive|executeProviderArchiveAction|Could not archive|provider identity/,
);
assert.match(
  archiveHandler,
  /result\.response\.status === "mutation_unconfirmed"/,
);
assert.match(
  archiveHandler,
  /sourceManagedMailbox\.provider === "google"/,
);
assert.match(
  archiveHandler,
  /Archive may have completed; mailbox status is being refreshed\./,
);
assert.equal(
  (
    archiveHandler.match(
      /onReconcileGmailArchive\(sourceManagedMailbox\.id as InboxId\)/g,
    ) ?? []
  ).length,
  1,
);
assert.equal(
  (
    archiveHandler.match(
      /onProviderArchiveMutationSettled\(sourceManagedMailbox\.id as InboxId\)/g,
    ) ?? []
  ).length,
  1,
);

const detailMenu = section(
  'openShareCollaboration(message.id);',
  'deleteMessages([message.id]);',
);
assert.match(detailMenu, /archiveMessagesFromEntryPoint\(\[message\.id\]\)/);
assert.match(
  detailMenu,
  /disabled=\{pendingProviderArchiveKeys\.length > 0\}/,
);

const selectionHandler = section(
  "const archiveSelectedMessages",
  "const deleteSelectedMessages",
);
assert.match(
  selectionHandler,
  /archiveMessagesFromEntryPoint\(actionableSelectionIds\)/,
);

const keyboardHandler = section(
  "const handleMailboxKeydown",
  'window.addEventListener("keydown"',
);
assert.match(
  keyboardHandler,
  /event\.key\.toLowerCase\(\) === "e"[\s\S]*archiveSelectedMessages\(\)/,
);

const toolbar = section(
  '<MailToolbarIconButton\n            label={\n              pendingProviderArchiveKeys.length > 0',
  '<MailToolbarIconButton\n            label="Attachments"',
);
assert.match(toolbar, /onClick=\{archiveSelectedMessages\}/);
assert.match(toolbar, /pendingProviderArchiveKeys\.length > 0/);
assert.match(toolbar, /"Archiving\.\.\."/);

const manualMove = section(
  "const moveMessagesToManualTarget",
  "const moveSubmenuPosition",
);
assert.match(
  manualMove,
  /targetFolder === "Archive"[\s\S]*archiveMessagesFromEntryPoint\(messageIds\)/,
);

const dragDrop = section(
  "const handleDropToTarget",
  "const openSmartFolder",
);
assert.match(
  dragDrop,
  /target\.folder === "Archive"[\s\S]*archiveMessagesFromEntryPoint/,
);

assert.equal(
  (source.match(/archiveMessagesFromEntryPoint\(contextMenuSelectionIds\)/g) ?? [])
    .length,
  1,
);
assert.match(
  source,
  /archiveMessagesFromEntryPoint\(contextMenuSelectionIds\);[\s\S]{0,180}disabled=\{pendingProviderArchiveKeys\.length > 0\}/,
);
assert.ok(
  (
    source.match(
      /target\.folder === "Archive" &&\s+pendingProviderArchiveKeys\.length > 0/g,
    ) ?? []
  ).length >= 2,
);

const mutationApply = section(
  "const applyProviderArchiveMutationSuccess",
  "const applyProviderArchiveFetchSnapshot",
);
const gmailApply = nestedSection(
  mutationApply,
  'if (provider === "google")',
  'if (!("folders" in response))',
);
const gmailSnapshotCleanup = section(
  "const removeConfirmedArchivedGmailMessageFromPersistedInboxSnapshot",
  "const applyProviderArchiveMutationSuccess",
);
assert.match(
  gmailSnapshotCleanup,
  /removeAndPersistGmailInboxProviderMessageFromSnapshot\([\s\S]*saveLiveInboxSnapshot/,
);
assert.match(gmailSnapshotCleanup, /try \{[\s\S]*\} catch \{/);
assert.doesNotMatch(
  gmailSnapshotCleanup,
  /fetchProviderArchive|fetchGmailInbox|mutateProviderArchiveMessage|retry|reconcil/i,
);
assert.match(gmailApply, /response\.delta\.Archive\.upsertMessage/);
assert.match(gmailApply, /response\.delta\.Inbox\.removeProviderMessageId/);
assert.match(gmailApply, /normalizeGmailArchiveDeltaMessage/);
assert.match(gmailApply, /applyGmailProviderArchiveDelta/);
assert.match(gmailApply, /flushSync\(\(\) => \{/);
assert.equal((gmailApply.match(/setMailboxStore\(/g) ?? []).length, 1);
assert.equal((gmailApply.match(/flushSync\(/g) ?? []).length, 1);
assert.doesNotMatch(gmailApply, /replaceProviderArchiveReadback/);
assert.doesNotMatch(gmailApply, /fetchProviderArchive/);
const archiveAuthorityIndex = gmailApply.indexOf(
  "gmailInboxAuthorityRef.current.confirmArchive(",
);
const archiveSnapshotRemovalIndex = gmailApply.indexOf(
  "removeConfirmedArchivedGmailMessageFromPersistedInboxSnapshot(",
);
const archiveDeltaApplyIndex = gmailApply.indexOf(
  "applyGmailProviderArchiveDelta(",
);
const archiveUnreadClearIndex = gmailApply.indexOf(
  "clearUnreadOverridesForProviderMessages(",
);
assert.ok(archiveAuthorityIndex >= 0);
assert.ok(archiveAuthorityIndex < archiveSnapshotRemovalIndex);
assert.ok(archiveSnapshotRemovalIndex < archiveDeltaApplyIndex);
assert.ok(archiveDeltaApplyIndex < archiveUnreadClearIndex);

const liveInboxMerge = section(
  "const mergeLiveInboxMessages = (",
  "const normalizeProviderFolderSnapshotMessages = (",
);
assert.match(
  liveInboxMerge,
  /threadIdentityContext\.provider === "google"[\s\S]*threadIdentityContext\.folder\.trim\(\)\.toUpperCase\(\) === "INBOX"/,
);
assert.match(
  liveInboxMerge,
  /gmailInboxAuthorityRef\.current\.filterSnapshotMessages\(/,
);
assert.ok(
  liveInboxMerge.indexOf("filterSnapshotMessages(") <
    liveInboxMerge.indexOf("uniqueIncomingMessages"),
);
const liveInboxApply = section(
  "const applyLiveInboxMessagesToMailboxStore = (",
  "const applyCachedLiveInboxSnapshotToMailboxStore = (",
);
assert.match(
  liveInboxApply,
  /Archive: options\.provenGmailInboxReentryProviderMessageIds[\s\S]*removeProvenGmailInboxReentriesFromArchive\(/,
);
assert.match(liveInboxApply, /Inbox: mergeLiveInboxMessages\(/);
assert.equal((liveInboxApply.match(/setMailboxStore\(/g) ?? []).length, 1);

const imapApply = mutationApply.slice(
  mutationApply.indexOf('if (!("folders" in response))'),
);
assert.match(imapApply, /response\.folders\.Inbox/);
assert.match(imapApply, /response\.folders\.Archive/);
assert.match(imapApply, /replaceProviderArchiveReadback/);
assert.doesNotMatch(imapApply, /applyGmailProviderArchiveDelta/);
assert.match(source, /labelIds: mergedMessageState\.labelIds/);

const archiveRefresh = section(
  "const refreshProviderArchiveById = async",
  "const refreshMailboxById = async",
);
assert.match(archiveRefresh, /providerArchiveFetchMailboxIdsRef\.current\.has/);
assert.match(archiveRefresh, /providerArchiveFetchMailboxIdsRef\.current\.add/);
assert.match(archiveRefresh, /providerArchiveFetchMailboxIdsRef\.current\.delete/);
assert.match(
  archiveRefresh,
  /providerArchiveFetchMailboxIdsRef\.current\.delete\(mailboxId\);\s+drainGmailArchiveReconciliation\(mailboxId\)/,
);
assert.match(archiveRefresh, /fetchProviderArchive\(managedMailbox\.id\)/);
assert.match(archiveRefresh, /applyProviderArchiveFetchSnapshot/);
assert.match(archiveRefresh, /resolveProviderArchiveRefreshSemantics\(\{/);
assert.match(archiveRefresh, /providerArchiveSnapshotMailboxIdsRef\.current\.add/);
assert.match(archiveRefresh, /hasPendingProviderArchiveForMailbox/);
assert.match(archiveRefresh, /shouldApplyProviderArchiveResponse\(\{/);
assert.doesNotMatch(archiveRefresh, /setMailboxSyncError|clearMailboxSyncError/);
assert.match(
  archiveRefresh,
  /archiveCapability === "unavailable" &&\s+archiveSemantics\.capability === "unknown"[\s\S]*ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE/,
);
assert.match(
  archiveRefresh,
  /catch \{[\s\S]*archiveCapability === "unavailable"[\s\S]*ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE/,
);
assert.doesNotMatch(
  archiveRefresh,
  /setMailboxSyncFeedbackMessage|localStorage|sessionStorage|Promise\.all|retry/i,
);
assert.equal((archiveRefresh.match(/fetchProviderArchive\(/g) ?? []).length, 1);
assert.ok(
  archiveRefresh.indexOf("shouldApplyProviderArchiveResponse({") <
    archiveRefresh.indexOf("applyProviderArchiveFetchSnapshot("),
);

const refresh = section(
  "const refreshMailboxById = async",
  "const handleSyncActiveMailbox",
);
assert.match(refresh, /hasPendingProviderArchiveForMailbox/);
assert.match(refresh, /resolveMailboxRefreshPlan\(\{/);
assert.match(refresh, /startIndependentMailboxFetches\(\{/);
assert.match(refresh, /startInbox:[\s\S]*fetchGmailInbox\(\{/);
assert.match(refresh, /startInbox:[\s\S]*connectInboxWithImap\(/);
assert.equal((refresh.match(/fetchGmailInbox\(/g) ?? []).length, 1);
assert.match(refresh, /startArchive: refreshPlan\.shouldFetchArchive/);
assert.match(refresh, /void archivePromise;/);
assert.match(refresh, /let response = await inboxPromise;/);
const inboxAuthorityCaptureIndex = refresh.indexOf(
  "gmailInboxAuthorityRef.current.captureGeneration(mailboxId)",
);
const inboxFetchStartIndex = refresh.indexOf(
  "startIndependentMailboxFetches({",
);
const inboxResponseIndex = refresh.indexOf("let response = await inboxPromise;");
const inboxGenerationGuardIndex = refresh.indexOf(
  "gmailInboxAuthorityRef.current.isCurrentGeneration(",
);
const inboxAuthorityResolutionIndex = refresh.indexOf(
  "gmailInboxAuthorityRef.current.resolveFetchResponse({",
);
const inboxSnapshotSaveIndex = refresh.indexOf("saveLiveInboxSnapshot({");
assert.ok(inboxAuthorityCaptureIndex >= 0);
assert.ok(inboxAuthorityCaptureIndex < inboxFetchStartIndex);
assert.ok(inboxFetchStartIndex < inboxResponseIndex);
assert.ok(inboxResponseIndex < inboxGenerationGuardIndex);
assert.ok(inboxGenerationGuardIndex < inboxAuthorityResolutionIndex);
assert.ok(inboxAuthorityResolutionIndex < inboxSnapshotSaveIndex);
assert.match(
  refresh,
  /gmailInboxConnectionKeyAtFetchStart[\s\S]*providerArchiveCurrentConnectionKeysRef\.current\[mailboxId\]/,
);
assert.match(
  refresh,
  /gmailInboxConnectionEpochAtFetchStart[\s\S]*providerArchiveConnectionEpochsRef\.current\[mailboxId\]/,
);
assert.match(
  refresh,
  /provenGmailInboxReentryProviderMessageIds[\s\S]*applyLiveInboxMessagesToMailboxStore\([\s\S]*provenGmailInboxReentryProviderMessageIds/,
);
assert.match(refresh, /const isProviderReconciliation = refreshReason === "reconcile"/);
assert.match(
  refresh,
  /if \(!isProviderReconciliation\) \{\s+clearMailboxSyncError\(mailboxId\)/,
);
assert.match(
  refresh,
  /if \(!response\.ok\) \{\s+if \(isProviderReconciliation\) \{\s+return "failed";/,
);
assert.match(
  refresh,
  /if \(!isProviderReconciliation\) \{\s+if \(refreshPresentation\.mailboxSyncError\)/,
);
assert.match(refresh, /reconciliationArchivePromise = archivePromise/);
assert.match(refresh, /await reconciliationArchivePromise/);
assert.match(
  refresh,
  /\(!isProviderReconciliation && syncingMailboxId === mailboxId\)/,
);
assert.match(
  refresh,
  /syncingMailboxIdsRef\.current\.delete\(mailboxId\);[\s\S]*drainGmailArchiveReconciliation\(mailboxId\)/,
);
assert.match(
  refresh,
  /gmailArchiveReconciliationRefreshRef\.current = \(mailboxId\) =>\s+refreshMailboxById\(mailboxId, \{ reason: "reconcile" \}\)/,
);
assert.doesNotMatch(refresh, /Promise\.all|fetchProviderArchive\(/);
assert.doesNotMatch(refresh, /mutateProviderArchiveMessage|executeProviderArchiveAction/);
assert.doesNotMatch(
  refresh,
  /applyProviderArchiveFetchSnapshot|resolveProviderArchiveRefreshSemantics/,
);
assert.match(refresh, /clearMailboxSyncError\(mailboxId\)/);
assert.doesNotMatch(refresh, /All Mail|All Messages|archiveFolderName|fallbackFolder/);
const failedInboxBranchIndex = refresh.indexOf("if (!response.ok)");
const inboxAwaitIndex = refresh.indexOf("let response = await inboxPromise;");
const inboxApplyIndex = refresh.indexOf("applyLiveInboxMessagesToMailboxStore(");
assert.ok(failedInboxBranchIndex >= 0);
assert.ok(inboxAwaitIndex >= 0);
assert.ok(inboxAwaitIndex < inboxApplyIndex);
assert.ok(failedInboxBranchIndex < inboxApplyIndex);
assert.match(
  refresh.slice(failedInboxBranchIndex, inboxApplyIndex),
  /return "failed";/,
);
const inboxPresentation = nestedSection(
  refresh,
  "const refreshPresentation = resolveSuccessfulInboxRefreshPresentation({",
  "if (refreshPresentation.mailboxSyncError)",
);
assert.match(inboxPresentation, /inboxWarningMessage: refreshWarningMessage/);
assert.doesNotMatch(inboxPresentation, /archive/);

const startupRefresh = section(
  "const runStartupSync = async",
  "void runStartupSync();",
);
assert.match(startupRefresh, /refreshResults\.push\(refreshResult\)/);
assert.match(startupRefresh, /refreshResults\.push\("failed"\)/);
assert.match(startupRefresh, /summarizeStartupMailboxRefreshResults\(refreshResults\)/);
assert.match(startupRefresh, /reason: "startup"/);

const activeMailboxRefreshEffects = section(
  "void refreshMailboxById(activeMailbox.id, { reason: \"mailbox_open\" });",
  "const workspaceShellPaddingClass",
);
assert.match(activeMailboxRefreshEffects, /reason: "interval"/);
assert.equal(
  (activeMailboxRefreshEffects.match(/reason: "interval"/g) ?? []).length,
  1,
);
assert.doesNotMatch(activeMailboxRefreshEffects, /fetchProviderArchive/);

const archiveFolderSwitch = section(
  "const switchToFolder = (folder: MailFolder)",
  "const switchToSharedView",
);
assert.match(
  archiveFolderSwitch,
  /folder === "Archive"[\s\S]*onArchiveFolderOpen\(\)/,
);
assert.match(source, /refreshProviderArchiveById\(activeMailbox\.id, "archive_open"\)/);
assert.match(
  source,
  /const isActiveArchiveCapabilityUnavailable =[\s\S]*activeFolder === "Archive"[\s\S]*isProviderArchiveCapabilityUnavailable\(mailbox\.id\)/,
);
assert.match(
  source,
  /visibleMessages\.length === 0 &&\s+!isActiveArchiveCapabilityUnavailable/,
);
assert.match(
  source,
  /selectedMessage\.id === "main-1"[\s\S]*: isActiveArchiveCapabilityUnavailable \? null/,
);
assert.match(
  source,
  /activeFolder === "Archive" && archiveFolderStatusMessage[\s\S]*role="status"[\s\S]*archiveFolderStatusMessage/,
);

const reconciliationCoordinator = section(
  "const pendingGmailArchiveReconciliationMailboxIdsRef",
  "const startupSyncHasRunRef",
);
assert.match(
  reconciliationCoordinator,
  /runningGmailArchiveReconciliationMailboxIdsRef/,
);
assert.match(
  reconciliationCoordinator,
  /createGmailArchiveReconciliationCoordinator\(\{/,
);
assert.match(
  reconciliationCoordinator,
  /isInboxFetchInFlight:[\s\S]*syncingMailboxIdsRef\.current\.has\(mailboxId\)/,
);
assert.match(
  reconciliationCoordinator,
  /isArchiveFetchInFlight:[\s\S]*providerArchiveFetchMailboxIdsRef\.current\.has\(mailboxId\)/,
);
assert.match(
  reconciliationCoordinator,
  /isProviderArchiveMutationInFlight:[\s\S]*hasPendingProviderArchiveForMailbox\([\s\S]*providerArchivePendingKeys/,
);

const archiveReconciliation = section(
  "onProviderArchiveMutationSettled={",
  "onArchiveFolderOpen=",
);
assert.match(
  archiveReconciliation,
  /onProviderArchiveMutationSettled=\{\s+drainGmailArchiveReconciliation\s+\}/,
);
assert.match(
  archiveReconciliation,
  /onReconcileGmailArchive=\{requestGmailArchiveReconciliation\}/,
);
assert.match(
  archiveReconciliation,
  /isGmailArchiveReconciliationRunning=\{\(mailboxId\) =>[\s\S]*runningGmailArchiveReconciliationMailboxIdsRef\.current\.has\([\s\S]*mailboxId/,
);
assert.doesNotMatch(
  archiveReconciliation,
  /refreshMailboxById|mutateProviderArchiveMessage|executeProviderArchiveAction/,
);

const mobileRefresh = section(
  "onSyncMailbox={async (mailboxId) =>",
  "onComposeMessage=",
);
assert.match(
  mobileRefresh,
  /result === "synced" \|\| result === "partial"/,
);
assert.match(
  mobileRefresh,
  /result === "synced"[\s\S]*`✓ Refresh complete/,
);

assert.match(source, /filterLegacyArchiveHydration<MailMessage>/);
assert.match(source, /mergeLegacyArchiveStorage/);
assert.match(
  source,
  /newlyAuthoritativeMailboxIds[\s\S]*Archive: \[\]/,
);
assert.match(
  source,
  /changedMailboxIds[\s\S]*providerArchiveSnapshotMailboxIdsRef\.current\.delete/,
);
assert.match(
  source,
  /changedMailboxIds[\s\S]*gmailInboxAuthorityRef\.current\.resetMailbox\(mailboxId\)/,
);
assert.match(
  source,
  /changedMailboxIds[\s\S]*providerArchiveCapabilitiesRef\.current[\s\S]*Archive: \[\]/,
);
assert.match(
  source,
  /changedMailboxIds[\s\S]*setProviderArchiveFolderStatusMessages[\s\S]*!changedMailboxIds\.includes/,
);
const capabilityMutationState = section(
  "const markProviderArchiveCapabilityUnavailable",
  "const refreshProviderArchiveById",
);
assert.match(
  capabilityMutationState,
  /providerArchiveCapabilitiesRef\.current =[\s\S]*\[mailboxId\]: "unavailable"/,
);
assert.match(
  capabilityMutationState,
  /setProviderArchiveFolderStatusMessage\([\s\S]*PROVIDER_ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE/,
);
assert.doesNotMatch(
  capabilityMutationState,
  /mailboxStore|localStorage|fetchProviderArchive|reconcil/i,
);
assert.match(source, /replaceProviderArchiveReadback/);
assert.match(source, /applyProviderArchiveFolderReadback/);
assert.match(source, /const applyProviderArchiveFetchSnapshot[\s\S]*applyProviderArchiveFolderReadback/);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function flushReconciliationCleanup() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function gmailArchiveSuccessResponse(
  request: ProviderArchiveMutationRequest,
): ProviderArchiveMutationResponse {
  if (!("messageId" in request)) {
    throw new Error("Expected an exact Gmail Archive request");
  }
  const preview = {
    id: "provider-race@example.test",
    sender: "Sender",
    subject: "Provider race",
    snippet: "Provider body",
    from: "sender@example.test",
    to: "owner@example.test",
    timestamp: "August 3 at 10:00",
    createdAt: "2026-08-03T08:00:00.000Z",
    body: ["Provider body"],
  };
  return {
    ok: true,
    status: "ok",
    action: "archive",
    mailboxId: request.mailboxId,
    archivedMessageIdentity: {
      serverMailboxId: request.mailboxId,
      providerMessageId: request.messageId,
      providerThreadId: "gmail-thread-race",
      providerFolder: "Archive",
      rfcMessageId: "provider-race@example.test",
    },
    delta: {
      Inbox: {
        removeProviderMessageId: request.messageId,
      },
      Archive: {
        upsertMessage: {
          ...preview,
          serverMailboxId: request.mailboxId,
          providerFolder: "Archive",
          providerMessageId: request.messageId,
          providerThreadId: "gmail-thread-race",
          rfcMessageId: "provider-race@example.test",
          labelIds: ["STARRED"],
        },
      },
    },
  };
}

async function verifyPreArchiveGmailInboxResponseIsRejected() {
  const authority = createGmailInboxAuthority();
  const generationAtFetchStart = authority.captureGeneration("mailbox-race");
  const pendingInbox = deferred<
    Array<{
      serverMailboxId: string;
      providerFolder: string;
      providerMessageId: string;
      labelIds: string[];
    }>
  >();
  const appliedResponses: string[][] = [];
  const savedSnapshots: string[][] = [];
  const clearedUnreadOverrides: string[][] = [];
  const responseCommit = pendingInbox.promise.then((messages) => {
    const resolution = authority.resolveFetchResponse({
      mailboxId: "mailbox-race",
      generationAtFetchStart,
      messages,
    });
    if (resolution.stale) {
      return "skipped" as const;
    }

    const providerMessageIds = resolution.messages.flatMap((message) =>
      typeof message.providerMessageId === "string"
        ? [message.providerMessageId]
        : [],
    );
    savedSnapshots.push(providerMessageIds);
    appliedResponses.push(providerMessageIds);
    clearedUnreadOverrides.push(providerMessageIds);
    return "applied" as const;
  });

  assert.equal(
    authority.isCurrentGeneration("mailbox-race", generationAtFetchStart),
    true,
  );
  const candidate: ProviderArchiveCandidate = {
    provider: "google",
    mailboxId: "mailbox-race",
    folder: "Inbox",
    providerMessageId: "provider-race",
  };
  const pendingMutation = deferred<ProviderArchiveMutationResponse>();
  const pendingKeys = new Set<string>();
  let mutationCalls = 0;
  let mutationRequest: ProviderArchiveMutationRequest | null = null;
  const coordinator = createProviderArchiveCoordinator({
    pendingKeys,
    mutate: (request) => {
      mutationCalls += 1;
      mutationRequest = request;
      return pendingMutation.promise;
    },
  });
  assert.equal(resolveCandidatePreflight(candidate), null);
  const archiveExecution = executeProviderArchiveAction({
    coordinator,
    candidate,
    applySuccess: () => {
      authority.confirmArchive("mailbox-race", "provider-race");
      return true;
    },
  });
  assert.equal(mutationCalls, 1);
  assert.equal(
    hasPendingProviderArchiveForMailbox(pendingKeys, "mailbox-race"),
    true,
  );
  assert.deepEqual(
    resolveCandidatePreflight(candidate, {
      hasPendingArchiveMutation: hasPendingProviderArchiveForMailbox(
        pendingKeys,
        "mailbox-race",
      ),
    }),
    {
      reason: "mutation_pending",
      message: PROVIDER_ARCHIVE_PENDING_MAILBOX_MESSAGE,
    },
  );
  assert.equal(mutationCalls, 1);
  assert.ok(mutationRequest);
  pendingMutation.resolve(gmailArchiveSuccessResponse(mutationRequest));
  const archiveResult = await archiveExecution;
  assert.equal(archiveResult.classification, "success");
  assert.equal(archiveResult.applied, true);
  assert.equal(mutationCalls, 1);
  assert.equal(pendingKeys.size, 0);
  assert.equal(
    authority.isCurrentGeneration("mailbox-race", generationAtFetchStart),
    false,
  );
  pendingInbox.resolve([
    {
      serverMailboxId: "mailbox-race",
      providerFolder: "Inbox",
      providerMessageId: "provider-race",
      labelIds: ["INBOX", "UNREAD"],
    },
  ]);

  assert.equal(await responseCommit, "skipped");
  assert.deepEqual(savedSnapshots, []);
  assert.deepEqual(appliedResponses, []);
  assert.deepEqual(clearedUnreadOverrides, []);
}

async function verifyCustomImapCapabilityUnavailableRuntime() {
  const initialState = {
    Inbox: [{ id: "imap-inbox-must-remain" }],
    Archive: [{ id: "imap-archive-must-remain" }],
  };
  let state = initialState;
  let runtimeCapability: "unknown" | "unavailable" = "unknown";
  let folderStatusMessage: string | null = null;
  let mutationCalls = 0;
  let applyCalls = 0;
  let reconciliationStarts = 0;
  const coordinator = createProviderArchiveCoordinator({
    mutate: async () => {
      mutationCalls += 1;
      return {
        ok: false,
        error: {
          code: "archive_folder_unavailable",
          message: "No safe Archive mailbox is available.",
        },
      };
    },
  });

  const archive = async () => {
    const preflight = resolveCandidatePreflight(exactImapArchiveCandidate, {
      isProviderArchiveCapabilityUnavailable:
        runtimeCapability === "unavailable",
    });
    if (preflight) {
      folderStatusMessage = preflight.message;
      return { preflight, result: null };
    }

    const result = await executeProviderArchiveAction({
      coordinator,
      candidate: exactImapArchiveCandidate,
      applySuccess: () => {
        applyCalls += 1;
        state = { Inbox: [], Archive: [] };
        return true;
      },
    });
    if (result.classification === "capability_unavailable") {
      runtimeCapability = "unavailable";
      folderStatusMessage = PROVIDER_ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE;
    } else if (result.classification === "uncertain") {
      reconciliationStarts += 1;
    }
    return { preflight: null, result };
  };

  const first = await archive();
  assert.equal(first.result?.classification, "capability_unavailable");
  assert.equal(runtimeCapability, "unavailable");
  assert.equal(
    folderStatusMessage,
    PROVIDER_ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
  );
  assert.equal(mutationCalls, 1);
  assert.equal(applyCalls, 0);
  assert.equal(reconciliationStarts, 0);
  assert.equal(state, initialState);

  const second = await archive();
  assert.deepEqual(second.preflight, {
    reason: "capability_unavailable",
    message: PROVIDER_ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
  });
  assert.equal(second.result, null);
  assert.equal(mutationCalls, 1);
  assert.equal(applyCalls, 0);
  assert.equal(reconciliationStarts, 0);
  assert.equal(state, initialState);
  assert.equal(
    `${folderStatusMessage}`.includes(PROVIDER_ARCHIVE_INVALID_SOURCE_MESSAGE),
    false,
  );
  assert.equal(
    `${folderStatusMessage}`.includes(
      "Could not archive this message safely. Reload the mailbox and try again.",
    ),
    false,
  );
}

async function verifyGmailArchiveReconciliationCoordinator() {
  const inboxFetchMailboxIds = new Set<string>();
  const archiveFetchMailboxIds = new Set<string>();
  const mutationMailboxIds = new Set<string>();
  const pendingMailboxIds = new Set<string>();
  const runningMailboxIds = new Set<string>();
  const starts: string[] = [];
  const runs = new Map<
    string,
    { promise: Promise<void>; resolve: () => void }
  >();

  const coordinator = createGmailArchiveReconciliationCoordinator({
    pendingMailboxIds,
    runningMailboxIds,
    isInboxFetchInFlight: (mailboxId) =>
      inboxFetchMailboxIds.has(mailboxId),
    isArchiveFetchInFlight: (mailboxId) =>
      archiveFetchMailboxIds.has(mailboxId),
    isProviderArchiveMutationInFlight: (mailboxId) =>
      mutationMailboxIds.has(mailboxId),
    reconcile: (mailboxId) => {
      starts.push(`${mailboxId}:Inbox`, `${mailboxId}:Archive`);
      let resolve!: () => void;
      const promise = new Promise<void>((resolvePromise) => {
        resolve = resolvePromise;
      });
      runs.set(mailboxId, { promise, resolve });
      return promise;
    },
  });

  const startsFor = (mailboxId: string) =>
    starts.filter((entry) => entry.startsWith(`${mailboxId}:`));
  const finish = async (mailboxId: string) => {
    const run = runs.get(mailboxId);
    assert.ok(run, `missing reconciliation run for ${mailboxId}`);
    run.resolve();
    await run.promise;
    await flushReconciliationCleanup();
  };

  coordinator.request("free");
  coordinator.request("free");
  assert.deepEqual(startsFor("free"), ["free:Inbox", "free:Archive"]);
  assert.equal(pendingMailboxIds.has("free"), false);
  assert.equal(runningMailboxIds.has("free"), true);
  assert.equal(pendingMailboxIds.size, 0);
  await finish("free");
  assert.equal(runningMailboxIds.has("free"), false);

  inboxFetchMailboxIds.add("inbox-locked");
  coordinator.request("inbox-locked");
  coordinator.request("inbox-locked");
  assert.deepEqual(startsFor("inbox-locked"), []);
  assert.equal(pendingMailboxIds.has("inbox-locked"), true);
  assert.equal(pendingMailboxIds.size, 1);
  coordinator.drain("inbox-locked");
  assert.deepEqual(startsFor("inbox-locked"), []);
  inboxFetchMailboxIds.delete("inbox-locked");
  coordinator.drain("inbox-locked");
  assert.deepEqual(startsFor("inbox-locked"), [
    "inbox-locked:Inbox",
    "inbox-locked:Archive",
  ]);
  assert.equal(pendingMailboxIds.has("inbox-locked"), false);
  await finish("inbox-locked");

  archiveFetchMailboxIds.add("archive-locked");
  coordinator.request("archive-locked");
  assert.deepEqual(startsFor("archive-locked"), []);
  assert.equal(pendingMailboxIds.has("archive-locked"), true);
  coordinator.drain("archive-locked");
  assert.deepEqual(startsFor("archive-locked"), []);
  archiveFetchMailboxIds.delete("archive-locked");
  coordinator.drain("archive-locked");
  assert.deepEqual(startsFor("archive-locked"), [
    "archive-locked:Inbox",
    "archive-locked:Archive",
  ]);
  assert.equal(pendingMailboxIds.has("archive-locked"), false);
  await finish("archive-locked");

  mutationMailboxIds.add("mutation-locked");
  coordinator.request("mutation-locked");
  assert.deepEqual(startsFor("mutation-locked"), []);
  assert.equal(pendingMailboxIds.has("mutation-locked"), true);
  coordinator.drain("mutation-locked");
  assert.deepEqual(startsFor("mutation-locked"), []);
  mutationMailboxIds.delete("mutation-locked");
  coordinator.drain("mutation-locked");
  assert.deepEqual(startsFor("mutation-locked"), [
    "mutation-locked:Inbox",
    "mutation-locked:Archive",
  ]);
  assert.equal(pendingMailboxIds.has("mutation-locked"), false);
  await finish("mutation-locked");

  inboxFetchMailboxIds.add("mailbox-a");
  coordinator.request("mailbox-a");
  coordinator.request("mailbox-b");
  assert.deepEqual(startsFor("mailbox-a"), []);
  assert.deepEqual(startsFor("mailbox-b"), [
    "mailbox-b:Inbox",
    "mailbox-b:Archive",
  ]);
  assert.equal(pendingMailboxIds.has("mailbox-a"), true);
  assert.equal(runningMailboxIds.has("mailbox-b"), true);
  await finish("mailbox-b");
  inboxFetchMailboxIds.delete("mailbox-a");
  coordinator.drain("mailbox-a");
  assert.deepEqual(startsFor("mailbox-a"), [
    "mailbox-a:Inbox",
    "mailbox-a:Archive",
  ]);
  await finish("mailbox-a");
  assert.equal(pendingMailboxIds.size, 0);
  assert.equal(runningMailboxIds.size, 0);

  let failedReconciliationStarts = 0;
  const failedPendingMailboxIds = new Set<string>();
  const failedRunningMailboxIds = new Set<string>();
  const failedCoordinator = createGmailArchiveReconciliationCoordinator({
    pendingMailboxIds: failedPendingMailboxIds,
    runningMailboxIds: failedRunningMailboxIds,
    isInboxFetchInFlight: () => false,
    isArchiveFetchInFlight: () => false,
    isProviderArchiveMutationInFlight: () => false,
    reconcile: async () => {
      failedReconciliationStarts += 1;
      throw new Error("background reconciliation failed");
    },
  });
  failedCoordinator.request("failure");
  failedCoordinator.request("failure");
  await flushReconciliationCleanup();
  failedCoordinator.drain("failure");
  await flushReconciliationCleanup();
  assert.equal(failedReconciliationStarts, 1);
  assert.equal(failedPendingMailboxIds.size, 0);
  assert.equal(failedRunningMailboxIds.size, 0);
}

async function verifyIndependentInboxCommit() {
  const pendingArchive = deferred<{ ok: true }>();
  const starts: string[] = [];
  let visibleInbox = Object.freeze([{ id: "fresh-gmail-inbox" }]);
  let archiveSettled = false;
  let inboxResult = "idle";

  const { inboxPromise, archivePromise } = startIndependentMailboxFetches({
    startInbox: async () => {
      starts.push("inbox");
      return Object.freeze([{ id: "refreshed-gmail-inbox" }]);
    },
    startArchive: () => {
      starts.push("archive");
      return pendingArchive.promise;
    },
  });
  const observedArchive = archivePromise?.then(
    () => {
      archiveSettled = true;
    },
    () => {
      archiveSettled = true;
    },
  );

  assert.deepEqual(starts, ["inbox", "archive"]);
  visibleInbox = await inboxPromise;
  inboxResult = resolveSuccessfulInboxRefreshPresentation({
    inboxWarningMessage: null,
  }).result;

  assert.deepEqual(visibleInbox, [{ id: "refreshed-gmail-inbox" }]);
  assert.equal(inboxResult, "synced");
  assert.equal(archiveSettled, false);

  pendingArchive.reject(new Error("Archive stays background-only"));
  await observedArchive;

  assert.deepEqual(visibleInbox, [{ id: "refreshed-gmail-inbox" }]);
  assert.equal(inboxResult, "synced");
  assert.equal(archiveSettled, true);
}

void Promise.all([
  verifyPreArchiveGmailInboxResponseIsRejected(),
  verifyCustomImapCapabilityUnavailableRuntime(),
  verifyGmailArchiveReconciliationCoordinator(),
  verifyIndependentInboxCommit(),
])
  .then(() => {
    console.log("providerArchive Workspace wiring tests passed");
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
