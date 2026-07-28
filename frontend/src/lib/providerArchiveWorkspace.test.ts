import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

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
