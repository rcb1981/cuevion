import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  ARCHIVE_CAPABILITY_UNAVAILABLE_MESSAGE,
  ARCHIVE_REFRESH_ERROR_MESSAGE,
  resolveMailboxRefreshPlan,
  resolveProviderArchiveRefreshSemantics,
  resolveSuccessfulInboxRefreshPresentation,
  shouldApplyProviderArchiveResponse,
  startIndependentMailboxFetches,
  summarizeStartupMailboxRefreshResults,
} from "./mailboxRefreshSemantics";

const archiveFailure = (code: string) => ({
  ok: false as const,
  error: { code },
});

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

const archiveRefresh = section(
  "const refreshProviderArchiveById = async",
  "const refreshMailboxById = async",
);
assert.match(archiveRefresh, /providerArchiveFetchMailboxIdsRef\.current\.has/);
assert.match(archiveRefresh, /providerArchiveFetchMailboxIdsRef\.current\.add/);
assert.match(archiveRefresh, /providerArchiveFetchMailboxIdsRef\.current\.delete/);
assert.match(archiveRefresh, /fetchProviderArchive\(managedMailbox\.id\)/);
assert.match(archiveRefresh, /applyProviderArchiveFetchSnapshot/);
assert.match(archiveRefresh, /resolveProviderArchiveRefreshSemantics\(\{/);
assert.match(archiveRefresh, /providerArchiveSnapshotMailboxIdsRef\.current\.add/);
assert.match(archiveRefresh, /hasPendingProviderArchiveForMailbox/);
assert.match(archiveRefresh, /shouldApplyProviderArchiveResponse\(\{/);
assert.doesNotMatch(archiveRefresh, /setMailboxSyncError|clearMailboxSyncError/);
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
assert.match(refresh, /startArchive: refreshPlan\.shouldFetchArchive/);
assert.match(refresh, /void archivePromise;/);
assert.match(refresh, /let response = await inboxPromise;/);
assert.doesNotMatch(refresh, /Promise\.all|fetchProviderArchive\(/);
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
  /changedMailboxIds[\s\S]*providerArchiveCapabilitiesRef\.current[\s\S]*Archive: \[\]/,
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

void verifyIndependentInboxCommit()
  .then(() => {
    console.log("providerArchive Workspace wiring tests passed");
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
