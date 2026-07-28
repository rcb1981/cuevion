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

const detailMenu = section(
  'openShareCollaboration(message.id);',
  'deleteMessages([message.id]);',
);
assert.match(detailMenu, /archiveMessagesFromEntryPoint\(\[message\.id\]\)/);

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
assert.match(source, /flushSync/);

console.log("providerArchive Workspace wiring tests passed");
