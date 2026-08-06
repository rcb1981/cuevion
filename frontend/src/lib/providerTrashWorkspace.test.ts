import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createGmailInboxAuthority } from "./mailboxRefreshSemantics";

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

const deleteHandler = section(
  "const deleteMessages = (messageIds: string[]) =>",
  "const emptyTrash = () =>",
);
const liveDeleteIndex = deleteHandler.indexOf(
  'if (workspaceDataMode === "live")',
);
const centralDeleteIndex = deleteHandler.indexOf(
  "trashMessagesFromEntryPoint(messageIds)",
);
const localTrashRemovalIndex = deleteHandler.indexOf(
  "removeMessagesFromTrash(",
);
const localTrashMoveIndex = deleteHandler.indexOf(
  'moveMessages(mailbox.id, activeFolder, mailbox.id, "Trash", messageIds)',
);
assert.ok(liveDeleteIndex >= 0);
assert.ok(liveDeleteIndex < centralDeleteIndex);
assert.ok(centralDeleteIndex < localTrashRemovalIndex);
assert.ok(centralDeleteIndex < localTrashMoveIndex);
assert.match(
  deleteHandler,
  /if \(workspaceDataMode === "live"\) \{\s+void trashMessagesFromEntryPoint\(messageIds\);\s+return;/,
);

const trashHandler = section(
  "async function trashMessagesFromEntryPoint",
  "const archiveSelectedMessages",
);
const liveTrashBranchStart = trashHandler.indexOf("const sourceLocation =");
assert.ok(liveTrashBranchStart >= 0);
const demoTrashFallback = trashHandler.slice(0, liveTrashBranchStart);
const liveTrashBranch = trashHandler.slice(liveTrashBranchStart);
assert.match(
  demoTrashFallback,
  /if \(workspaceDataMode !== "live"\) \{[\s\S]*moveMessages\([\s\S]*"Trash"[\s\S]*messageIds[\s\S]*\);\s+return;/,
);
assert.doesNotMatch(
  liveTrashBranch,
  /\bmoveMessages(?:AcrossWorkspace|ToFolderAcrossWorkspace)?\s*\(|setMailboxStore\s*\(|providerFolder:\s*"Trash"|Trash:\s*\[\s*(?:source|resolution)/,
  "live Gmail Trash must not synthesize a source-message-to-Trash row",
);

const resolutionIndex = liveTrashBranch.indexOf(
  "resolveExactGmailTrashMutationTarget({",
);
const coordinatorIndex = liveTrashBranch.indexOf(
  "createProviderTrashCoordinator({",
);
for (const marker of [
  "messageIds.length !== 1",
  "isMultiSelectActive",
  "isSharedView",
  "Boolean(activeSmartFolder)",
  'sourceLocation.folder !== "Inbox"',
  'sourceManagedMailbox.provider !== "google"',
]) {
  const markerIndex = liveTrashBranch.indexOf(marker);
  assert.ok(markerIndex >= 0, `missing fail-closed Trash marker: ${marker}`);
  assert.ok(
    markerIndex < resolutionIndex,
    `Trash marker must run before exact target resolution: ${marker}`,
  );
}
assert.ok(resolutionIndex >= 0);
assert.ok(resolutionIndex < coordinatorIndex);
assert.match(
  liveTrashBranch,
  /sourceManagedMailbox\.provider === "custom_imap"[\s\S]*Provider-authoritative Trash is not available for custom IMAP yet\./,
);
assert.match(
  liveTrashBranch,
  /reconcileReadOnly: async \(request\) => \{[\s\S]*onReconcileProviderTrash\([\s\S]*request\.mailboxId[\s\S]*request\.providerMessageId[\s\S]*request\.cause === "confirmed_success"/,
);
assert.match(
  liveTrashBranch,
  /const trashPromise = coordinator\.trash\(resolution\.target\);[\s\S]*setPendingProviderTrashKeys\(\[\.\.\.providerTrashPendingKeys\]\);[\s\S]*const result = await trashPromise;[\s\S]*setPendingProviderTrashKeys\(\[\.\.\.providerTrashPendingKeys\]\)/,
);

assert.equal(
  (source.match(/\btrashMessagesFromEntryPoint\(/g) ?? []).length,
  8,
  "every direct Trash path must be one definition plus the seven audited callers",
);

const crossWorkspaceMove = section(
  "const moveMessagesAcrossWorkspace = (",
  "const moveMessagesToFolderAcrossWorkspace = (",
);
assert.match(
  crossWorkspaceMove,
  /targetFolder === "Trash" && workspaceDataMode === "live"[\s\S]*trashMessagesFromEntryPoint\(messageIds\);[\s\S]*return;/,
);

const crossWorkspaceFolderMove = section(
  "const moveMessagesToFolderAcrossWorkspace = (",
  "const moveMessages = (",
);
assert.match(
  crossWorkspaceFolderMove,
  /targetFolder === "Trash" && workspaceDataMode === "live"[\s\S]*trashMessagesFromEntryPoint\(messageIds\);[\s\S]*return;/,
);

const directFolderMove = section(
  "const moveMessages = (",
  "const removeMessagesFromTrash = (",
);
assert.match(
  directFolderMove,
  /targetFolder === "Trash" && workspaceDataMode === "live"[\s\S]*trashMessagesFromEntryPoint\(messageIds, \{[\s\S]*mailboxId: sourceMailboxId[\s\S]*folder: sourceFolder[\s\S]*\}\);[\s\S]*return;/,
);

assert.match(
  source,
  /deleteMessages\(\[message\.id\]\);[\s\S]{0,120}disabled=\{pendingProviderTrashKeys\.length > 0\}/,
  "detail Delete must route through deleteMessages and respect pending Trash",
);

const selectedDelete = section(
  "const deleteSelectedMessages = () =>",
  "const toggleSelectedUnreadState = () =>",
);
assert.match(
  selectedDelete,
  /if \(workspaceDataMode === "live"\) \{\s+void trashMessagesFromEntryPoint\(actionableSelectionIds\);\s+return;/,
);

const manualMove = section(
  "const moveMessagesToManualTarget",
  "const moveSubmenuPosition",
);
assert.match(
  manualMove,
  /targetFolder === "Trash"[\s\S]*trashMessagesFromEntryPoint\(messageIds\)/,
);

const dragDrop = section("const handleDropToTarget", "const openSmartFolder");
assert.match(
  dragDrop,
  /target\.folder === "Trash"[\s\S]*trashMessagesFromEntryPoint\(dragPayload\.messageIds, \{[\s\S]*mailboxId: dragPayload\.sourceMailboxId[\s\S]*folder: dragPayload\.sourceFolder/,
);

const keyboard = section(
  "const handleMailboxKeydown",
  'window.addEventListener("keydown", handleMailboxKeydown)',
);
assert.match(
  keyboard,
  /event\.key === "Delete" \|\| event\.key === "Backspace"[\s\S]*deleteSelectedMessages\(\)/,
);

const toolbar = section(
  '<MailToolbarIconButton\n            label={\n              pendingProviderTrashKeys.length > 0',
  '<MailToolbarIconButton\n            label="Flag"',
);
assert.match(toolbar, /onClick=\{deleteSelectedMessages\}/);
assert.match(toolbar, /pendingProviderTrashKeys\.length > 0/);

assert.match(
  source,
  /onClick=\{\(\) => deleteMessages\(contextMenuSelectionIds\)\}[\s\S]{0,100}disabled=\{pendingProviderTrashKeys\.length > 0\}/,
  "context Delete must route through deleteMessages and respect pending Trash",
);

const inboxRefresh = section(
  "const refreshMailboxById = async",
  "const handleSyncActiveMailbox = async",
);
const inboxAwaitIndex = inboxRefresh.indexOf(
  "let response = await inboxPromise;",
);
const pendingPublicationGuardIndex = inboxRefresh.indexOf(
  "hasPendingProviderTrashForMailbox(providerTrashPendingKeys, mailboxId)",
  inboxAwaitIndex,
);
const inboxSnapshotSaveIndex = inboxRefresh.indexOf("saveLiveInboxSnapshot({");
const inboxStoreApplyIndex = inboxRefresh.indexOf(
  "applyLiveInboxMessagesToMailboxStore(",
);
const trashRefreshAfterInboxApplyIndex = inboxRefresh.indexOf(
  "await refreshProviderTrashById(mailboxId);",
  inboxStoreApplyIndex,
);
assert.ok(inboxAwaitIndex >= 0);
assert.ok(inboxAwaitIndex < pendingPublicationGuardIndex);
assert.ok(pendingPublicationGuardIndex < inboxSnapshotSaveIndex);
assert.ok(pendingPublicationGuardIndex < inboxStoreApplyIndex);
assert.ok(inboxStoreApplyIndex < trashRefreshAfterInboxApplyIndex);
assert.match(
  inboxRefresh.slice(pendingPublicationGuardIndex, inboxSnapshotSaveIndex),
  /return "skipped";/,
);

const startupRefresh = section(
  "const runStartupSync = async () =>",
  "void runStartupSync();",
);
assert.match(
  startupRefresh,
  /await refreshMailboxById\(\s*mailboxId,\s*\{\s*reason: "startup",?\s*\},?\s*\)/,
  "a new live session must flow through Inbox refresh and its provider Trash readback",
);

const trashRefresh = section(
  "const refreshProviderTrashById = async",
  "const reconcileProviderTrashById = async",
);
const trashFetchIndex = trashRefresh.indexOf(
  "const response = await fetchGmailTrash(managedMailbox.id);",
);
const trashPendingPublicationGuardIndex = trashRefresh.indexOf(
  "hasPendingProviderTrashForMailbox(providerTrashPendingKeys, mailboxId)",
  trashFetchIndex,
);
const trashStoreApplyIndex = trashRefresh.indexOf(
  "applyProviderAuthoritativeGmailTrashSnapshot(",
);
assert.ok(trashFetchIndex >= 0);
assert.ok(trashFetchIndex < trashPendingPublicationGuardIndex);
assert.ok(trashPendingPublicationGuardIndex < trashStoreApplyIndex);
assert.match(
  trashRefresh.slice(trashFetchIndex, trashStoreApplyIndex),
  /if \([\s\S]*hasPendingProviderTrashForMailbox\(providerTrashPendingKeys, mailboxId\)[\s\S]*\) \{\s+return "skipped";\s+\}/,
  "a Trash response received during a pending mutation must return before store publication",
);

const trashReconciliation = section(
  "const reconcileProviderTrashById = async",
  "const connectedInboxCount",
);
const readOnlyInboxFetchIndex = trashReconciliation.indexOf(
  "fetchGmailInbox({",
);
const readOnlyTrashFetchIndex = trashReconciliation.indexOf(
  "fetchGmailTrash(managedMailbox.id)",
);
const strictInboxValidationIndex = trashReconciliation.indexOf(
  "readStrictGmailInboxReconciliationMessages(",
);
const reconciledStoreApplyIndex = trashReconciliation.indexOf(
  "applyProviderAuthoritativeGmailTrashReconciliation({",
);
const reconciledSnapshotSaveIndex = trashReconciliation.indexOf(
  "saveLiveInboxSnapshot({",
);
const confirmedFenceBranchIndex = trashReconciliation.indexOf(
  "if (mutationConfirmed) {",
);
const confirmedFenceIndex = trashReconciliation.indexOf(
  "gmailInboxAuthorityRef.current.confirmArchive(",
  confirmedFenceBranchIndex,
);
const reconciliationGenerationCaptureIndex = trashReconciliation.indexOf(
  "gmailInboxAuthorityRef.current.captureGeneration(mailboxId)",
);
const confirmedFenceBranch = trashReconciliation.slice(
  confirmedFenceBranchIndex,
  reconciliationGenerationCaptureIndex,
);
assert.match(trashReconciliation, /await Promise\.all\(\[/);
assert.ok(readOnlyInboxFetchIndex >= 0);
assert.ok(readOnlyTrashFetchIndex >= 0);
assert.ok(confirmedFenceBranchIndex >= 0);
assert.ok(confirmedFenceBranchIndex < confirmedFenceIndex);
assert.ok(confirmedFenceIndex < reconciliationGenerationCaptureIndex);
assert.ok(reconciliationGenerationCaptureIndex < readOnlyInboxFetchIndex);
assert.match(
  confirmedFenceBranch,
  /if \(mutationConfirmed\) \{\s+gmailInboxAuthorityRef\.current\.confirmArchive\(\s+mailboxId,\s+providerMessageId,\s+\);\s+removeConfirmedArchivedGmailMessageFromPersistedInboxSnapshot\(\s+mailboxId,\s+providerMessageId,\s+\);\s+\}/,
  "confirmed Trash must fence the exact mailbox/provider identity before paired readback",
);
assert.ok(readOnlyInboxFetchIndex < strictInboxValidationIndex);
assert.ok(readOnlyTrashFetchIndex < strictInboxValidationIndex);
assert.ok(strictInboxValidationIndex < reconciledStoreApplyIndex);
assert.ok(reconciledStoreApplyIndex < reconciledSnapshotSaveIndex);
assert.doesNotMatch(
  trashReconciliation,
  /mutateProviderTrashMessage|coordinator\.trash\(|\bmoveMessages(?:AcrossWorkspace|ToFolderAcrossWorkspace)?\s*\(/,
  "Trash reconciliation must remain read-only until provider snapshots are validated",
);

const reconciliationApply = section(
  "const applyProviderAuthoritativeGmailTrashReconciliation",
  "const setProviderTrashFolderStatusMessage",
);
assert.match(reconciliationApply, /setMailboxStore\(/);
assert.match(
  reconciliationApply,
  /normalizeGmailProviderMessages\([\s\S]*"Trash"[\s\S]*trashSnapshot\.messages/,
);
assert.match(
  reconciliationApply,
  /replaceGmailProviderInboxAndTrashReadback\(/,
);

const folderSwitch = section(
  "const switchToFolder = (folder: MailFolder)",
  "const switchToSharedView",
);
assert.match(
  folderSwitch,
  /folder === "Trash"[\s\S]*onTrashFolderOpen\(\)/,
);
const trashFolderOpen = section(
  "const handleOpenActiveMailboxTrash = () =>",
  "useEffect(() => {",
);
assert.match(
  trashFolderOpen,
  /refreshProviderTrashById\(activeMailbox\.id\)/,
);
assert.match(trashRefresh, /fetchGmailTrash\(managedMailbox\.id\)/);
assert.match(source, /onTrashFolderOpen=\{handleOpenActiveMailboxTrash\}/);

const authoritativeGmailTrash = section(
  "const providerAuthoritativeGmailTrashMailboxIds = useMemo",
  "const providerAuthoritativeGmailTrashMailboxKey",
);
assert.match(authoritativeGmailTrash, /workspaceDataMode === "live"/);
assert.match(authoritativeGmailTrash, /mailbox\.provider === "google"/);
assert.match(authoritativeGmailTrash, /mailbox\.connected/);
assert.match(
  authoritativeGmailTrash,
  /mailbox\.connectionStatus === "connected"/,
);

const initialMailboxStore = section(
  "function createInitialMailboxStore(",
  "function createEmptyMailboxCollections()",
);
assert.match(
  initialMailboxStore,
  /if \(workspaceDataMode === "live"\) \{\s+if \(gmailOAuthMailboxIds\.has\(inboxId\)\) \{\s+store\[inboxId\] = createEmptyMailboxCollections\(\);\s+return store;/,
  "a new live Gmail session must start without a locally synthesized Trash folder",
);

const trashHydration = section(
  "const storedValue = window.localStorage.getItem(trashMessagesStorageKey);",
  "const storedValue = window.localStorage.getItem(spamMessagesStorageKey);",
);
assert.match(
  trashHydration,
  /if \(providerAuthoritativeGmailTrashMailboxIds\.has\(mailbox\.id\)\) \{\s+return;/,
);

const trashPersistence = section(
  "const trashMessagesByMailbox = Object.fromEntries",
  "const spamMessagesByMailbox = Object.fromEntries",
);
assert.match(trashPersistence, /orderedMailboxes\.flatMap/);
assert.match(
  trashPersistence,
  /providerAuthoritativeGmailTrashMailboxIds\.has\(mailbox\.id\)[\s\S]*\? \[\]/,
);

const liveInboxApply = section(
  "const applyLiveInboxMessagesToMailboxStore = (",
  "const applyCachedLiveInboxSnapshotToMailboxStore = (",
);
assert.match(
  liveInboxApply,
  /Trash: options\.provenGmailInboxReentryProviderMessageIds[\s\S]*removeProvenGmailInboxReentriesFromArchive\([\s\S]*currentCollections\.Trash/,
  "a proven Gmail Inbox reentry must evict the same provider id from Trash",
);

const mailboxId = "gmail-trash-race-mailbox";
const providerMessageId = "gmail-trash-race-message";
const authority = createGmailInboxAuthority();
const generationBeforeConfirmedTrash = authority.captureGeneration(mailboxId);
const staleInboxRow = {
  serverMailboxId: mailboxId,
  providerFolder: "Inbox",
  providerMessageId,
  labelIds: ["INBOX"],
};
const otherInboxRow = {
  serverMailboxId: mailboxId,
  providerFolder: "Inbox",
  providerMessageId: "gmail-trash-race-other-message",
  labelIds: ["INBOX"],
};

authority.confirmArchive(mailboxId, providerMessageId);
assert.deepEqual(
  authority.resolveFetchResponse({
    mailboxId,
    generationAtFetchStart: generationBeforeConfirmedTrash,
    messages: [staleInboxRow, otherInboxRow],
  }),
  {
    stale: true,
    messages: [],
    provenReentryProviderMessageIds: [],
  },
  "an Inbox fetch started before confirmed Trash must never publish",
);

const currentGeneration = authority.captureGeneration(mailboxId);
const ambiguousFencedRow = {
  serverMailboxId: mailboxId,
  providerFolder: "Inbox",
  providerMessageId,
};
assert.deepEqual(
  authority.resolveFetchResponse({
    mailboxId,
    generationAtFetchStart: currentGeneration,
    messages: [ambiguousFencedRow, otherInboxRow],
  }),
  {
    stale: false,
    messages: [otherInboxRow],
    provenReentryProviderMessageIds: [],
  },
  "a current fetch must still respect the confirmed-removal fence without exact reentry proof",
);
assert.equal(authority.isRecentlyArchived(mailboxId, providerMessageId), true);

console.log("providerTrash Workspace wiring tests passed");
