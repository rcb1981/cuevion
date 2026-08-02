import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
  ARCHIVE_REFRESH_ERROR_MESSAGE,
  resolveProviderArchiveRefreshSemantics,
  resolveSuccessfulInboxRefreshPresentation,
  summarizeStartupMailboxRefreshResults,
} from "./mailboxRefreshSemantics";

const archiveFailure = (code: string) => ({
  ok: false as const,
  error: { code },
});

const unsupportedArchive = resolveProviderArchiveRefreshSemantics({
  provider: "custom_imap",
  inboxFetchFullySucceeded: true,
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
    inboxFetchFullySucceeded: true,
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

for (const code of ["archive_folder_unavailable", "gmail_fetch_failed"]) {
  const gmailFailure = resolveProviderArchiveRefreshSemantics({
    provider: "google",
    inboxFetchFullySucceeded: true,
    archiveResponse: archiveFailure(code),
    archiveSnapshotApplied: false,
  });
  assert.equal(gmailFailure.capability, "unknown");
  assert.equal(gmailFailure.contributesPartial, true);
  assert.equal(gmailFailure.mailboxSyncError, ARCHIVE_REFRESH_ERROR_MESSAGE);
}

const malformedSuccessfulArchive = resolveProviderArchiveRefreshSemantics({
  provider: "custom_imap",
  inboxFetchFullySucceeded: true,
  archiveResponse: { ok: true },
  archiveSnapshotApplied: false,
});
assert.equal(malformedSuccessfulArchive.contributesPartial, true);
assert.equal(malformedSuccessfulArchive.mailboxSyncError, ARCHIVE_REFRESH_ERROR_MESSAGE);

const incompleteInboxWithUnavailableArchive =
  resolveProviderArchiveRefreshSemantics({
    provider: "custom_imap",
    inboxFetchFullySucceeded: false,
    archiveResponse: archiveFailure("archive_folder_unavailable"),
    archiveSnapshotApplied: false,
  });
assert.equal(incompleteInboxWithUnavailableArchive.capability, "unknown");
assert.equal(incompleteInboxWithUnavailableArchive.contributesPartial, true);
assert.equal(
  resolveSuccessfulInboxRefreshPresentation({
    inboxWarningMessage: "Some older messages could not be refreshed.",
    archiveSemantics: incompleteInboxWithUnavailableArchive,
  }).result,
  "partial",
);

const successfulGmailArchive = resolveProviderArchiveRefreshSemantics({
  provider: "google",
  inboxFetchFullySucceeded: true,
  archiveResponse: { ok: true },
  archiveSnapshotApplied: true,
});
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
assert.match(archiveHandler, /executeProviderArchiveAction\(\{/);
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
assert.match(archiveHandler, /isSyncingMailbox/);
assert.match(archiveHandler, /location\?\.folder !== "Inbox"/);
assert.match(archiveHandler, /groupedMessages\.length !== 1/);
assert.match(archiveHandler, /exactSourceMessageMatches\.length === 1/);
assert.match(
  archiveHandler,
  /sourceMessage\.serverMailboxId !== sourceManagedMailbox\.id/,
);
const executeIndex = archiveHandler.indexOf(
  "const archivePromise = executeProviderArchiveAction",
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
assert.ok(executeIndex < pendingVisibleIndex);
assert.ok(pendingVisibleIndex < awaitIndex);
assert.ok(awaitIndex < pendingReleasedIndex);

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
assert.match(gmailApply, /response\.delta\.Archive\.upsertMessage/);
assert.match(gmailApply, /response\.delta\.Inbox\.removeProviderMessageId/);
assert.match(gmailApply, /normalizeGmailArchiveDeltaMessage/);
assert.match(gmailApply, /applyGmailProviderArchiveDelta/);
assert.match(gmailApply, /flushSync\(\(\) => \{/);
assert.equal((gmailApply.match(/setMailboxStore\(/g) ?? []).length, 1);
assert.equal((gmailApply.match(/flushSync\(/g) ?? []).length, 1);
assert.doesNotMatch(gmailApply, /replaceProviderArchiveReadback/);
assert.doesNotMatch(gmailApply, /fetchProviderArchive/);

const imapApply = mutationApply.slice(
  mutationApply.indexOf('if (!("folders" in response))'),
);
assert.match(imapApply, /response\.folders\.Inbox/);
assert.match(imapApply, /response\.folders\.Archive/);
assert.match(imapApply, /replaceProviderArchiveReadback/);
assert.doesNotMatch(imapApply, /applyGmailProviderArchiveDelta/);
assert.match(source, /labelIds: mergedMessageState\.labelIds/);

const refresh = section(
  "const refreshMailboxById = async",
  "const handleSyncActiveMailbox",
);
assert.match(
  refresh,
  /connectionStatus === "connected"[\s\S]*fetchProviderArchive\(managedMailbox\.id\)[\s\S]*Promise\.all\(\[[\s\S]*inboxFetchPromise[\s\S]*archiveFetchPromise/,
);
assert.match(refresh, /applyProviderArchiveFetchSnapshot/);
assert.match(refresh, /hasPendingProviderArchiveForMailbox/);
assert.equal((refresh.match(/fetchProviderArchive\(/g) ?? []).length, 1);
assert.match(
  refresh,
  /archiveResponse\.ok === true[\s\S]*applyProviderArchiveFetchSnapshot/,
);
assert.match(refresh, /resolveProviderArchiveRefreshSemantics\(\{/);
assert.match(
  refresh,
  /inboxFetchFullySucceeded: response\.ok === true && !response\.warning/,
);
assert.match(refresh, /providerArchiveCapabilitiesRef\.current/);
assert.match(refresh, /clearMailboxSyncError\(mailboxId\)/);
assert.doesNotMatch(refresh, /All Mail|All Messages|archiveFolderName|fallbackFolder/);
const failedInboxBranchIndex = refresh.indexOf("if (!response.ok)");
const inboxApplyIndex = refresh.indexOf("applyLiveInboxMessagesToMailboxStore(");
const archiveSemanticsIndex = refresh.indexOf(
  "resolveProviderArchiveRefreshSemantics({",
);
assert.ok(failedInboxBranchIndex >= 0);
assert.ok(failedInboxBranchIndex < inboxApplyIndex);
assert.ok(inboxApplyIndex < archiveSemanticsIndex);
assert.match(
  refresh.slice(failedInboxBranchIndex, inboxApplyIndex),
  /return "failed";/,
);

const startupRefresh = section(
  "const runStartupSync = async",
  "void runStartupSync();",
);
assert.match(startupRefresh, /refreshResults\.push\(refreshResult\)/);
assert.match(startupRefresh, /refreshResults\.push\("failed"\)/);
assert.match(startupRefresh, /summarizeStartupMailboxRefreshResults\(refreshResults\)/);

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
assert.match(source, /replaceProviderArchiveReadback/);
assert.match(source, /applyProviderArchiveFolderReadback/);
assert.match(source, /const applyProviderArchiveFetchSnapshot[\s\S]*applyProviderArchiveFolderReadback/);

console.log("providerArchive Workspace wiring tests passed");
