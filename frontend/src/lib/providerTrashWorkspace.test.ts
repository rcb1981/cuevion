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

const customResolutionIndex = liveTrashBranch.indexOf(
  "resolveExactCustomImapTrashMutationTarget({",
);
const customCoordinatorIndex = liveTrashBranch.indexOf(
  "createProviderImapTrashCoordinator({",
);
const customMutationFenceIndex = liveTrashBranch.indexOf(
  "onBeginProviderImapTrashMutation(",
);
const gmailResolutionIndex = liveTrashBranch.indexOf(
  "resolveExactGmailTrashMutationTarget({",
);
const gmailCoordinatorIndex = liveTrashBranch.indexOf(
  "createProviderTrashCoordinator({",
);
for (const marker of [
  "messageIds.length !== 1",
  "isMultiSelectActive",
  "isSharedView",
  "Boolean(activeSmartFolder)",
  'sourceLocation.folder !== "Inbox"',
  "!hasAuthenticatedMemberAuthority",
  "!sourceManagedMailbox?.connected",
  'sourceManagedMailbox.connectionStatus !== "connected"',
]) {
  const markerIndex = liveTrashBranch.indexOf(marker);
  assert.ok(markerIndex >= 0, `missing fail-closed Trash marker: ${marker}`);
  assert.ok(
    markerIndex < customResolutionIndex,
    `Trash marker must run before exact target resolution: ${marker}`,
  );
}
assert.ok(customResolutionIndex >= 0);
assert.ok(customResolutionIndex < customMutationFenceIndex);
assert.ok(customMutationFenceIndex < customCoordinatorIndex);
assert.ok(customResolutionIndex < customCoordinatorIndex);
assert.ok(customCoordinatorIndex < gmailResolutionIndex);
assert.ok(gmailResolutionIndex < gmailCoordinatorIndex);

const customProviderBranch = liveTrashBranch.slice(
  customResolutionIndex,
  gmailResolutionIndex,
);
const gmailProviderBranch = liveTrashBranch.slice(gmailResolutionIndex);
assert.match(
  customProviderBranch,
  /sourceManagedMailbox: \{\s+id: sourceManagedMailbox\.id,\s+provider: sourceManagedMailbox\.provider,\s+connected: sourceManagedMailbox\.connected,\s+connectionStatus: sourceManagedMailbox\.connectionStatus,\s+\}/,
  "custom IMAP eligibility must project only the non-secret managed-mailbox contract",
);
assert.match(
  customProviderBranch,
  /sourceMessages: \([\s\S]*\.map\(\(message\) => \(\{\s+id: message\.id,\s+serverMailboxId: message\.serverMailboxId,\s+providerFolder: message\.providerFolder,\s+imapUid: message\.imapUid,\s+uidValidity: message\.uidValidity,\s+providerMessageId: message\.providerMessageId,\s+providerThreadId: message\.providerThreadId,\s+labelIds: message\.labelIds,\s+\}\)\)/,
  "custom IMAP target resolution must retain mixed-provider sentinels for strict rejection",
);
assert.doesNotMatch(
  customProviderBranch,
  /(?:password|host|port|username|email)\s*:/,
  "the mutation target path must never receive client-side IMAP credentials",
);
assert.match(
  customProviderBranch,
  /providerImapTrashPendingKeys\.has\(resolution\.target\.inFlightKey\)[\s\S]*hasPendingProviderImapTrashForMailbox\(\s+providerImapTrashPendingKeys,\s+sourceLocation\.mailboxId,\s+\)[\s\S]*Trash is already in progress for this IMAP mailbox\.[\s\S]*return;[\s\S]*onBeginProviderImapTrashMutation/,
  "all entrypoints must coalesce mailbox-overlapping custom mutations before creating a new fence",
);
assert.match(
  customProviderBranch,
  /applyConfirmedSourceRemoval: \(response\) => \{[\s\S]*onApplyConfirmedProviderImapTrashSourceRemoval\(\s+response,\s+mutationFence,\s+\)[\s\S]*advanceSelectionAfterAction\(\[resolution\.sourceMessage\.id\]\)/,
  "strict custom success must apply exact source removal and repair selection",
);
assert.match(
  customProviderBranch,
  /applyConfirmedSourceRemoval: \(response\) => \{\s+onClassifyProviderImapTrashMutation\(mutationFence, "success"\);[\s\S]*onApplyConfirmedProviderImapTrashSourceRemoval/,
  "confirmed mutation classification must release active readbacks before coordinator reconciliation",
);
assert.match(
  customProviderBranch,
  /pendingKeys: providerImapTrashPendingKeys,[\s\S]*onPendingKeysChange: publishPendingProviderTrashKeys,[\s\S]*refreshProviderTrashReadOnly: \(request\) => \{[\s\S]*request\.cause === "mutation_unconfirmed"[\s\S]*onClassifyProviderImapTrashMutation\(mutationFence, "uncertain"\)[\s\S]*onReconcileProviderImapTrash\(request, mutationFence\)/,
  "custom IMAP Trash must use one coordinator for pending state and read-only reconciliation",
);
assert.equal(
  (customProviderBranch.match(/coordinator\.trash\(resolution\.target\)/g) ?? [])
    .length,
  1,
  "custom IMAP Trash must send exactly one mutation request",
);
assert.match(
  customProviderBranch,
  /result\.classification === "capability_unavailable"[\s\S]*CUSTOM_IMAP_TRASH_CAPABILITY_UNAVAILABLE_MESSAGE/,
  "all mutation capability failures must use the exact connected-mailbox UX contract",
);
assert.match(
  customProviderBranch,
  /result\.classification === "uncertain"[\s\S]*No second Trash request was sent\./,
  "uncertainty must be surfaced without mutation retry",
);
assert.match(
  customProviderBranch,
  /result\.classification === "ordinary_failure"[\s\S]*Could not move this message to IMAP Trash safely\. No changes were applied\./,
);

assert.match(
  gmailProviderBranch,
  /reconcileReadOnly: async \(request\) => \{[\s\S]*onReconcileProviderTrash\([\s\S]*request\.mailboxId[\s\S]*request\.providerMessageId[\s\S]*request\.cause === "confirmed_success"/,
);
assert.match(
  gmailProviderBranch,
  /applyConfirmedSourceRemoval: \(response\) => \{[\s\S]*onApplyConfirmedProviderTrashSourceRemoval\([\s\S]*response\.mailboxId[\s\S]*response\.providerMessageId[\s\S]*advanceSelectionAfterAction\(\[resolution\.sourceMessage\.id\]\)/,
  "strict provider success must apply exact source removal and repair selection before reconciliation completes",
);
assert.match(
  gmailProviderBranch,
  /onPendingKeysChange: \(\) => \{\s+publishPendingProviderTrashKeys\(\);\s+\}/,
  "Gmail pending presentation must include both provider coordinators without changing its mutation contract",
);
assert.match(
  gmailProviderBranch,
  /const result = await coordinator\.trash\(resolution\.target\);/,
);
assert.match(
  gmailProviderBranch,
  /result\.classification === "reconciliation_failed"[\s\S]*result\.mutationClassification === "success"[\s\S]*Gmail confirmed Trash, but Inbox and Trash could not be reconciled safely\./,
  "a failed background readback must surface the existing safe confirmed-Trash message",
);
assert.match(
  gmailProviderBranch,
  /result\.classification === "ordinary_failure"[\s\S]*Could not move this message to Gmail Trash safely\. No changes were applied\./,
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
const customPendingPublicationGuardIndex = inboxRefresh.indexOf(
  "hasPendingProviderImapTrashForMailbox(",
  inboxAwaitIndex,
);
const inboxSnapshotSaveIndex = inboxRefresh.indexOf("saveLiveInboxSnapshot({");
const inboxStoreApplyIndex = inboxRefresh.indexOf(
  "applyLiveInboxMessagesToMailboxStore(",
);
const customImapAuthorityCaptureIndex = inboxRefresh.indexOf(
  "customImapInboxAuthorityRef.current.captureGeneration(mailboxId)",
);
const customImapConnectIndex = inboxRefresh.indexOf("connectInboxWithImap(");
const customMutationPublicationCaptureIndex = inboxRefresh.indexOf(
  "customImapTrashMutationPublicationEpochAtFetchStart",
);
const customImapResponseResolutionIndex = inboxRefresh.indexOf(
  "customImapInboxAuthorityRef.current.resolveFetchResponse({",
);
const guardedCustomImapResponseResolutionIndex = inboxRefresh.lastIndexOf(
  "customImapInboxAuthorityRef.current.resolveFetchResponse({",
);
const trashRefreshAfterInboxApplyIndex = inboxRefresh.indexOf(
  "await refreshProviderTrashById(mailboxId);",
  inboxStoreApplyIndex,
);
const quotaRetryAwaitIndex = inboxRefresh.indexOf(
  "const retryResponse = await connectInboxWithImap(",
);
const postRetryPublicationGuardIndex = inboxRefresh.indexOf(
  "customImapTrashMutationPublicationEpochAtFetchStart !==",
  quotaRetryAwaitIndex,
);
const responseFailureIndex = inboxRefresh.indexOf(
  "if (!response.ok)",
  quotaRetryAwaitIndex,
);
assert.ok(inboxAwaitIndex >= 0);
assert.ok(inboxAwaitIndex < pendingPublicationGuardIndex);
assert.ok(inboxAwaitIndex < customPendingPublicationGuardIndex);
assert.ok(pendingPublicationGuardIndex < inboxSnapshotSaveIndex);
assert.ok(pendingPublicationGuardIndex < inboxStoreApplyIndex);
assert.ok(customPendingPublicationGuardIndex < inboxSnapshotSaveIndex);
assert.ok(customPendingPublicationGuardIndex < inboxStoreApplyIndex);
assert.ok(inboxStoreApplyIndex < trashRefreshAfterInboxApplyIndex);
assert.ok(customImapAuthorityCaptureIndex >= 0);
assert.ok(customImapAuthorityCaptureIndex < customImapConnectIndex);
assert.ok(customMutationPublicationCaptureIndex >= 0);
assert.ok(customMutationPublicationCaptureIndex < customImapConnectIndex);
assert.ok(customImapConnectIndex < customImapResponseResolutionIndex);
assert.ok(customImapResponseResolutionIndex < inboxSnapshotSaveIndex);
assert.ok(customImapResponseResolutionIndex < inboxStoreApplyIndex);
assert.ok(
  customImapResponseResolutionIndex < guardedCustomImapResponseResolutionIndex,
);
assert.ok(guardedCustomImapResponseResolutionIndex < inboxSnapshotSaveIndex);
assert.ok(quotaRetryAwaitIndex >= 0);
assert.ok(quotaRetryAwaitIndex < postRetryPublicationGuardIndex);
assert.ok(postRetryPublicationGuardIndex < responseFailureIndex);
assert.match(
  inboxRefresh.slice(quotaRetryAwaitIndex, responseFailureIndex),
  /hasPendingProviderImapTrashForMailbox\([\s\S]*customImapInboxConnectionKeyAtFetchStart !==[\s\S]*customImapInboxConnectionEpochAtFetchStart !==[\s\S]*customImapTrashMutationPublicationEpochAtFetchStart !==[\s\S]*customImapInboxAuthorityRef\.current\.isCurrentGeneration[\s\S]*return "skipped";/,
  "the post-quota-retry publication guard must recheck pending, connection and both generations",
);
assert.match(
  inboxRefresh.slice(pendingPublicationGuardIndex, inboxSnapshotSaveIndex),
  /return "skipped";/,
);
assert.match(
  inboxRefresh.slice(inboxAwaitIndex, inboxSnapshotSaveIndex),
  /!isProviderImapTrashReconciliation[\s\S]*hasPendingProviderImapTrashForMailbox\([\s\S]*providerImapTrashPendingKeys[\s\S]*return "skipped";/,
  "ordinary Inbox refresh must not publish while custom Trash is pending",
);
assert.match(
  inboxRefresh,
  /const shouldFetchProviderArchive =\s+refreshPlan\.shouldFetchArchive && !isProviderImapTrashReconciliation/,
  "uncertainty reconciliation must pair only Inbox and Trash, never start Archive",
);
assert.match(
  inboxRefresh,
  /const firstAttemptWasQuota =\s+canUseImapFetch &&\s+!isProviderImapTrashReconciliation &&/,
  "uncertainty reconciliation must make only one Inbox readback request",
);

const startupRefresh = section(
  "const runStartupSync = async () =>",
  "void runStartupSync();",
);
assert.match(
  startupRefresh,
  /await refreshMailboxById\(\s*mailboxId,\s*\{\s*reason: "startup",?\s*\},?\s*\)/,
  "a new live session must flow through Inbox refresh",
);
assert.doesNotMatch(
  inboxRefresh,
  /canUseImapFetch[\s\S]{0,120}refreshProviderImapTrashById\(mailboxId\)/,
  "ordinary custom Inbox refresh must not download Trash",
);

const imapTrashRefresh = section(
  "const performProviderImapTrashRefreshById = async",
  "const refreshProviderTrashById = async",
);
const imapTrashFetchIndex = imapTrashRefresh.indexOf(
  "const response = await fetchProviderImapTrash({ mailboxId });",
);
const imapTrashPreFetchPendingGuardIndex = imapTrashRefresh.indexOf(
  "hasPendingProviderImapTrashForMailbox(",
);
const imapTrashPostFetchPendingGuardIndex = imapTrashRefresh.indexOf(
  "hasPendingProviderImapTrashForMailbox(",
  imapTrashFetchIndex,
);
const imapTrashClassificationGateAwaitIndex = imapTrashRefresh.indexOf(
  "await classificationGateAtResponse.promise;",
  imapTrashFetchIndex,
);
const imapTrashStoreApplyIndex = imapTrashRefresh.indexOf(
  "applyProviderAuthoritativeCustomImapTrashSnapshot(",
);
assert.ok(imapTrashPreFetchPendingGuardIndex >= 0);
assert.ok(imapTrashPreFetchPendingGuardIndex < imapTrashFetchIndex);
assert.ok(imapTrashFetchIndex < imapTrashClassificationGateAwaitIndex);
assert.ok(
  imapTrashClassificationGateAwaitIndex <
    imapTrashPostFetchPendingGuardIndex,
);
assert.ok(imapTrashFetchIndex < imapTrashPostFetchPendingGuardIndex);
assert.ok(imapTrashPostFetchPendingGuardIndex < imapTrashStoreApplyIndex);
assert.equal(
  (imapTrashRefresh.match(/fetchProviderImapTrash\(\{ mailboxId \}\)/g) ?? [])
    .length,
  1,
  "custom Trash readback must issue one read-only fetch",
);
assert.match(
  imapTrashRefresh,
  /allowPendingCoordinatorReconciliation[\s\S]*!allowPendingCoordinatorReconciliation[\s\S]*hasPendingProviderImapTrashForMailbox/,
  "only the coordinator uncertainty reconciliation may bypass its retained pending lock",
);
assert.match(
  imapTrashRefresh,
  /const mutationPublicationEpochAtFetchStart =[\s\S]*providerImapTrashOutcomePublicationEpochsRef\.current\[mailboxId\][\s\S]*classificationGateAtResponse[\s\S]*await classificationGateAtResponse\.promise[\s\S]*mutationPublicationEpochAtFetchStart !==[\s\S]*providerImapTrashOutcomePublicationEpochsRef\.current\[mailboxId\]/,
  "an active Trash response must await mutation classification and reject mutating outcomes",
);
assert.match(
  imapTrashRefresh,
  /managedMailbox\.provider !== "custom_imap"/,
);
for (const capabilityCode of [
  "trash_folder_unavailable",
  "trash_folder_ambiguous",
]) {
  assert.match(imapTrashRefresh, new RegExp(capabilityCode));
}
assert.match(
  imapTrashRefresh,
  /isTrashDiscoveryCapabilityFailure[\s\S]*CUSTOM_IMAP_TRASH_CAPABILITY_UNAVAILABLE_MESSAGE/,
  "Trash listing discovery failures must use the same exact capability message",
);
assert.doesNotMatch(
  imapTrashRefresh,
  /mutateProviderImapTrashMessage|createProviderImapTrashCoordinator|coordinator\.trash\(|\bmoveMessages(?:AcrossWorkspace|ToFolderAcrossWorkspace)?\s*\(/,
  "custom Trash refresh must remain read-only",
);
assert.match(
  imapTrashRefresh,
  /providerImapTrashRefreshTailSequencerRef\.current\.run\(mailboxId, \{\s+queueAfterActive: options\?\.queueAfterActiveFetch === true/,
  "ordinary folder-open readback must keep its non-queued skip semantics",
);
assert.match(
  imapTrashRefresh,
  /queueAfterActive: options\?\.queueAfterActiveFetch === true,[\s\S]*perform: \(\) =>\s+performProviderImapTrashRefreshById/,
  "coordinator readbacks must serialize a fresh fetch after the active mailbox fetch",
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
const reconciliationGenerationCaptureIndex = trashReconciliation.indexOf(
  "gmailInboxAuthorityRef.current.captureGeneration(mailboxId)",
);
const reconciliationFetchStatusAddIndex = trashReconciliation.indexOf(
  "providerTrashFetchMailboxIdsRef.current.add(mailboxId)",
);
const reconciliationFetchStatusDeleteIndex = trashReconciliation.lastIndexOf(
  "providerTrashFetchMailboxIdsRef.current.delete(mailboxId)",
);
assert.match(trashReconciliation, /await Promise\.all\(\[/);
assert.ok(readOnlyInboxFetchIndex >= 0);
assert.ok(readOnlyTrashFetchIndex >= 0);
assert.ok(reconciliationGenerationCaptureIndex < readOnlyInboxFetchIndex);
assert.ok(reconciliationGenerationCaptureIndex < reconciliationFetchStatusAddIndex);
assert.ok(reconciliationFetchStatusAddIndex < readOnlyInboxFetchIndex);
assert.ok(readOnlyInboxFetchIndex < reconciliationFetchStatusDeleteIndex);
assert.match(
  trashReconciliation.slice(reconciliationFetchStatusDeleteIndex - 180),
  /providerTrashFetchSequenceByMailboxRef\.current\[mailboxId\] === sequence[\s\S]*providerTrashFetchMailboxIdsRef\.current\.delete\(mailboxId\)/,
  "background reconciliation must own the existing Trash fetch status without releasing a newer fetch",
);
assert.doesNotMatch(
  trashReconciliation,
  /gmailInboxAuthorityRef\.current\.confirmArchive\(|removeConfirmedArchivedGmailMessageFromPersistedInboxSnapshot\(/,
  "background reconciliation must not delay or repeat strict-success source removal",
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

const imapTrashReconciliation = section(
  "const reconcileProviderImapTrashById = async",
  "const handleSyncActiveMailbox = async",
);
const confirmedImapReconciliation = imapTrashReconciliation.slice(
  imapTrashReconciliation.indexOf('request.cause === "confirmed_success"'),
  imapTrashReconciliation.indexOf('request.cause !== "mutation_unconfirmed"'),
);
const uncertainImapReconciliation = imapTrashReconciliation.slice(
  imapTrashReconciliation.indexOf('request.cause !== "mutation_unconfirmed"'),
);
assert.match(
  imapTrashReconciliation,
  /hasAuthenticatedMemberAuthority[\s\S]*providerAuthoritativeTrashMailboxIds\.has\(mailboxId\)[\s\S]*managedMailbox\.provider !== "custom_imap"[\s\S]*return false/,
  "custom reconciliation must fail closed before any provider fetch",
);
assert.match(
  confirmedImapReconciliation,
  /refreshProviderImapTrashById\(mailboxId, \{\s+queueAfterActiveFetch: true/,
  "confirmed success must trigger only the background provider Trash readback",
);
assert.doesNotMatch(
  confirmedImapReconciliation,
  /refreshMailboxById|allowPendingCoordinatorReconciliation/,
  "confirmed success must not start a full Inbox reconciliation",
);
const imapReconciliationInboxIndex = uncertainImapReconciliation.indexOf(
  "const inboxResult = await refreshMailboxById(",
);
const imapReconciliationTrashIndex = uncertainImapReconciliation.indexOf(
  "const trashResult = await refreshProviderImapTrashById(",
);
const imapReconciliationFenceRecheckIndex =
  uncertainImapReconciliation.indexOf(
    "isCurrentProviderImapTrashMutationFence(mailboxId, fence)",
  );
assert.ok(imapReconciliationInboxIndex >= 0);
assert.ok(imapReconciliationInboxIndex < imapReconciliationFenceRecheckIndex);
assert.ok(imapReconciliationFenceRecheckIndex < imapReconciliationTrashIndex);
assert.ok(imapReconciliationInboxIndex < imapReconciliationTrashIndex);
assert.match(
  uncertainImapReconciliation,
  /request\.cause !== "mutation_unconfirmed"[\s\S]*allowPendingProviderImapTrashReconciliation: true[\s\S]*allowPendingCoordinatorReconciliation: true,[\s\S]*queueAfterActiveFetch: true/,
  "only mutation uncertainty may reconcile through the coordinator's retained pending lock",
);
assert.doesNotMatch(
  imapTrashReconciliation,
  /mutateProviderImapTrashMessage|createProviderImapTrashCoordinator|coordinator\.trash\(|\bmoveMessages(?:AcrossWorkspace|ToFolderAcrossWorkspace)?\s*\(/,
  "custom reconciliation must perform sequential read-only Inbox and Trash fetches",
);

const confirmedSourceRemoval = section(
  "const applyConfirmedProviderTrashSourceRemoval = (",
  "const readStrictGmailInboxReconciliationMessages = (",
);
const confirmedSourceFenceIndex = confirmedSourceRemoval.indexOf(
  "gmailInboxAuthorityRef.current.confirmArchive(",
);
const confirmedSourceStoreIndex = confirmedSourceRemoval.indexOf(
  "setMailboxStore(",
);
const exactSourceRemovalIndex = confirmedSourceRemoval.indexOf(
  "applyConfirmedGmailTrashSourceRemoval(",
);
const confirmedSnapshotRemovalIndex = confirmedSourceRemoval.indexOf(
  "removeConfirmedArchivedGmailMessageFromPersistedInboxSnapshot(",
);
assert.ok(confirmedSourceFenceIndex >= 0);
assert.ok(confirmedSourceFenceIndex < confirmedSourceStoreIndex);
assert.ok(confirmedSourceStoreIndex < exactSourceRemovalIndex);
assert.ok(exactSourceRemovalIndex < confirmedSnapshotRemovalIndex);
assert.match(
  confirmedSourceRemoval,
  /applyConfirmedGmailTrashSourceRemoval\(\s+currentCollections,\s+\{\s+mailboxId,\s+providerMessageId,\s+\},\s+\)/,
);
assert.match(
  confirmedSourceRemoval,
  /managedMailbox\?\.provider !== "google"/,
  "Gmail strict-success application must remain provider-specific after shared Trash authority generalization",
);
assert.doesNotMatch(
  confirmedSourceRemoval,
  /providerThreadId|rfcMessageId|subject|sender|timestamp|\bTrash\s*:|\bArchive\s*:/,
  "confirmed source removal must use only mailbox/provider identity and must not mutate another folder",
);

const confirmedImapSourceRemoval = section(
  "const applyConfirmedProviderImapTrashSourceRemoval = (",
  "const readStrictGmailInboxReconciliationMessages = (",
);
const confirmedImapFenceIndex = confirmedImapSourceRemoval.indexOf(
  "customImapInboxAuthorityRef.current.confirmSourceRemoval(",
);
const confirmedImapConnectionFenceIndex = confirmedImapSourceRemoval.indexOf(
  "isCurrentProviderImapTrashMutationFence(mailboxId, fence)",
);
const confirmedImapStoreIndex = confirmedImapSourceRemoval.indexOf(
  "setMailboxStore(",
);
const exactImapRemovalIndex = confirmedImapSourceRemoval.indexOf(
  "applyConfirmedImapTrashSourceRemoval(",
);
const confirmedImapSnapshotRemovalIndex = confirmedImapSourceRemoval.indexOf(
  "removeConfirmedCustomImapMessageFromPersistedInboxSnapshot(",
);
assert.ok(confirmedImapFenceIndex >= 0);
assert.ok(confirmedImapConnectionFenceIndex >= 0);
assert.ok(confirmedImapConnectionFenceIndex < confirmedImapFenceIndex);
assert.ok(confirmedImapFenceIndex < confirmedImapStoreIndex);
assert.ok(confirmedImapStoreIndex < exactImapRemovalIndex);
assert.ok(exactImapRemovalIndex < confirmedImapSnapshotRemovalIndex);
assert.match(
  confirmedImapSourceRemoval,
  /isProviderImapTrashMutationSuccessResponse\(response\)/,
  "only the strict custom success envelope may remove an Inbox row",
);
assert.match(
  confirmedImapSourceRemoval,
  /confirmSourceRemoval\(\s+mailboxId,\s+response\.sourceUidValidity,\s+response\.sourceImapUid,\s+\)/,
);
assert.match(
  confirmedImapSourceRemoval,
  /removeConfirmedCustomImapMessageFromPersistedInboxSnapshot\(\s+mailboxId,\s+response\.sourceUidValidity,\s+response\.sourceImapUid,\s+\)/,
);
assert.doesNotMatch(
  confirmedImapSourceRemoval,
  /providerMessageId|providerThreadId|rfcMessageId|subject|sender|timestamp|\bTrash\s*:|\bArchive\s*:/,
  "custom confirmed removal must use only exact source UID identity and never synthesize a target row",
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

const customTrashSnapshotApply = section(
  "const applyProviderAuthoritativeCustomImapTrashSnapshot = (",
  "const applyProviderAuthoritativeGmailTrashReconciliation",
);
assert.match(
  customTrashSnapshotApply,
  /normalizeProviderFolderSnapshotMessages\(\s+mailboxId,\s+"custom_imap",\s+snapshot,\s+"Trash",\s+currentStore,\s+\)/,
);
assert.match(
  customTrashSnapshotApply,
  /replaceCustomImapTrashFolderReadback\(\s+currentCollections,\s+snapshot,\s+\)/,
);
assert.match(customTrashSnapshotApply, /Trash: normalizedTrash/);
assert.doesNotMatch(
  customTrashSnapshotApply,
  /\bInbox\s*:|applyConfirmedImapTrashSourceRemoval|confirmSourceRemoval|\bmoveMessages(?:AcrossWorkspace|ToFolderAcrossWorkspace)?\s*\(/,
  "custom Trash readback may replace only provider-authoritative Trash",
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
  /managedMailbox\?\.provider === "custom_imap"[\s\S]*refreshProviderImapTrashById\(activeMailbox\.id\)[\s\S]*return;/,
);
assert.match(
  trashFolderOpen,
  /refreshProviderTrashById\(activeMailbox\.id\)/,
);
assert.match(trashRefresh, /fetchGmailTrash\(managedMailbox\.id\)/);
assert.match(imapTrashRefresh, /fetchProviderImapTrash\(\{ mailboxId \}\)/);
assert.match(source, /onTrashFolderOpen=\{handleOpenActiveMailboxTrash\}/);

const authoritativeTrash = section(
  "const providerAuthoritativeTrashMailboxIds = useMemo",
  "const providerAuthoritativeTrashMailboxKey",
);
assert.match(authoritativeTrash, /workspaceDataMode === "live"/);
assert.match(authoritativeTrash, /mailbox\.provider === "google"/);
assert.match(authoritativeTrash, /mailbox\.provider === "custom_imap"/);
assert.match(authoritativeTrash, /mailbox\.connected/);
assert.match(
  authoritativeTrash,
  /mailbox\.connectionStatus === "connected"/,
);

const authoritativeTrashTransition = section(
  "const previousMailboxIds =\n      previousProviderAuthoritativeTrashMailboxIdsRef.current;",
  "const [hydratedArchiveMessagesStorageKey",
);
assert.match(
  authoritativeTrashTransition,
  /newlyAuthoritativeMailboxIds[\s\S]*providerAuthoritativeTrashMailboxIds[\s\S]*Trash: \[\]/,
  "a mailbox becoming provider-authoritative must discard locally synthesized Trash",
);

assert.match(
  source,
  /providerTrashFetchMailboxIdsRef\.current\.delete\(mailboxId\);\s+providerImapTrashRefreshTailSequencerRef\.current\.reset\(mailboxId\);[\s\S]*delete providerImapTrashMutationClassificationGatesRef\.current\[mailboxId\];\s+classificationGate\?\.resolve\(\);[\s\S]*gmailInboxAuthorityRef\.current\.resetMailbox\(mailboxId\);\s+customImapInboxAuthorityRef\.current\.resetMailbox\(mailboxId\);/,
  "a connection identity change must reset both provider Inbox fences",
);

const persistedCustomImapRemoval = section(
  "const removeConfirmedCustomImapMessageFromPersistedInboxSnapshot = (",
  "const applyProviderArchiveMutationSuccess = (",
);
assert.match(
  persistedCustomImapRemoval,
  /removeAndPersistCustomImapInboxMessageFromSnapshot\(\s+snapshot,\s+mailboxId,\s+sourceUidValidity,\s+sourceImapUid,\s+saveLiveInboxSnapshot/,
  "strict custom IMAP success must have an exact persisted Inbox-removal path",
);

const imapMutationFence = section(
  "const beginProviderImapTrashMutation = (",
  "const applyProviderArchiveMutationSuccess = (",
);
const beginImapMutationFence = section(
  "const beginProviderImapTrashMutation = (",
  "const isCurrentProviderImapTrashMutationFence = (",
);
assert.match(
  imapMutationFence,
  /managedMailbox\.provider !== "custom_imap"[\s\S]*return null/,
  "a mutation fence must be issued only for the current connected custom mailbox",
);
assert.match(
  beginImapMutationFence,
  /providerImapTrashInboxMutationPublicationEpochsRef\.current\[mailboxId\] =[\s\S]*\+ 1/,
  "mutation start must invalidate pre-MOVE Inbox publications",
);
assert.doesNotMatch(
  beginImapMutationFence,
  /providerImapTrashOutcomePublicationEpochsRef/,
  "mutation start must not invalidate an active Trash readback before its outcome is known",
);
assert.match(
  imapMutationFence,
  /return \{\s+mailboxId,\s+connectionKey,\s+connectionEpoch:[\s\S]*providerArchiveConnectionEpochsRef\.current\[mailboxId\][\s\S]*mutationGeneration/,
);
assert.match(
  imapMutationFence,
  /providerArchiveCurrentConnectionKeysRef\.current\[mailboxId\] ===\s+fence\.connectionKey[\s\S]*providerArchiveConnectionEpochsRef\.current\[mailboxId\][\s\S]*===\s+fence\.connectionEpoch/,
  "mutation results must match both exact connection key and epoch",
);
assert.match(
  imapMutationFence,
  /outcome !== "definitive_failure"[\s\S]*isCurrentProviderImapTrashMutationFence[\s\S]*providerImapTrashOutcomePublicationEpochsRef\.current\[mailboxId\] =[\s\S]*\+\s+1[\s\S]*delete providerImapTrashMutationClassificationGatesRef\.current\[mailboxId\];\s+classificationGate\.resolve\(\)/,
  "only current success or uncertainty may invalidate Trash before releasing the classification gate",
);
assert.doesNotMatch(
  imapMutationFence,
  /console\.|setMailboxActionToastMessage|JSON\.stringify\(fence/,
  "connection fence internals must never be logged or presented",
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
  /if \(providerAuthoritativeTrashMailboxIds\.has\(mailbox\.id\)\) \{\s+return;/,
);

const trashPersistence = section(
  "const trashMessagesByMailbox = Object.fromEntries",
  "const spamMessagesByMailbox = Object.fromEntries",
);
assert.match(trashPersistence, /orderedMailboxes\.flatMap/);
assert.match(
  trashPersistence,
  /providerAuthoritativeTrashMailboxIds\.has\(mailbox\.id\)[\s\S]*\? \[\]/,
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
