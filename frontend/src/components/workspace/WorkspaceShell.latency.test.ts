import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { shouldScheduleMessageOpenSideEffects } from "./fullMessageModalState";

assert.equal(
  shouldScheduleMessageOpenSideEffects(0),
  true,
  "keyboard-synthesized clicks must retain one logical open side effect",
);
assert.equal(
  shouldScheduleMessageOpenSideEffects(1),
  true,
  "a single pointer click must retain one logical open side effect",
);
assert.equal(
  shouldScheduleMessageOpenSideEffects(2),
  false,
  "the second click of a double-click must not repeat open side effects",
);

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const mailboxViewStart = workspaceShellSource.indexOf("function MailboxView(");
const mailboxViewEnd = workspaceShellSource.indexOf(
  "function WorkbenchView(",
  mailboxViewStart,
);
const mailboxViewSource = workspaceShellSource.slice(
  mailboxViewStart,
  mailboxViewEnd,
);

assert.ok(mailboxViewStart >= 0 && mailboxViewEnd > mailboxViewStart);

const selectHandlerStart = mailboxViewSource.indexOf(
  "const handleSelectMessage =",
);
const selectHandlerEnd = mailboxViewSource.indexOf(
  "const moveMessagesAcrossWorkspace =",
  selectHandlerStart,
);
const selectHandlerSource = mailboxViewSource.slice(
  selectHandlerStart,
  selectHandlerEnd,
);

assert.match(
  selectHandlerSource,
  /setMailboxScopedSelectionState\([\s\S]*setIsFullMessageOpen\(/,
  "selection and modal state must remain synchronous urgent updates",
);
assert.doesNotMatch(
  selectHandlerSource,
  /async\s*\(|\bawait\b|fetch\(|mutateInboxMessageAction/,
  "selection must not await or initiate provider network work",
);
assert.match(
  selectHandlerSource,
  /options\?\.recordOwnership !== false[\s\S]*onRecordMessageOwnershipInteraction/,
  "non-row selection entry points must retain ownership semantics",
);

const deferredSideEffectStart = mailboxViewSource.indexOf(
  "const scheduleMessageOpenSideEffects =",
);
const deferredSideEffectEnd = mailboxViewSource.indexOf(
  "const toggleMessageFlagState =",
  deferredSideEffectStart,
);
const deferredSideEffectSource = mailboxViewSource.slice(
  deferredSideEffectStart,
  deferredSideEffectEnd,
);

assert.match(
  deferredSideEffectSource,
  /useEffect\([\s\S]*startTransition\([\s\S]*onRecordMessageOwnershipInteraction\(message\)[\s\S]*markInboxMessageReadOnOpen\(message\)/,
  "ownership and read-on-open must run after the urgent selection commit",
);

const rowEventStart = mailboxViewSource.indexOf("data-message-row-id=");
const rowEventEnd = mailboxViewSource.indexOf(
  "onContextMenu=",
  rowEventStart,
);
const rowEventSource = mailboxViewSource.slice(rowEventStart, rowEventEnd);

assert.match(
  rowEventSource,
  /shouldScheduleMessageOpenSideEffects\(event\.detail\)[\s\S]*handleSelectMessage\([\s\S]*recordOwnership:\s*false[\s\S]*scheduleMessageOpenSideEffects\(message\)/,
  "the first click must select immediately and queue one deferred side-effect unit",
);
assert.match(
  rowEventSource,
  /onDoubleClick=[\s\S]*openFull:\s*true,[\s\S]*recordOwnership:\s*false/,
  "double-click must open the modal without recording ownership again",
);
assert.equal(
  rowEventSource.match(/scheduleMessageOpenSideEffects\(message\)/g)?.length,
  1,
  "one row gesture path must enqueue ownership/read side effects exactly once",
);
assert.doesNotMatch(
  rowEventSource,
  /\bawait\b|fetch\(|requestPriority|workflow-authority|collaborationOwner|semantic/i,
  "selection/open must not add body, workflow, semantic, or collaboration requests",
);

const sharedProjectionUseStart = mailboxViewSource.indexOf(
  "buildWorkspaceSharedMessageProjection(mailboxStore, currentUserId)",
);
const sharedProjectionUseSource = mailboxViewSource.slice(
  Math.max(0, sharedProjectionUseStart - 280),
  sharedProjectionUseStart + 220,
);
assert.match(
  sharedProjectionUseSource,
  /useMemo\([\s\S]*\[currentUserId, mailboxStore\]/,
  "workspace-wide shared flattening, dedupe, and locations must be memoized",
);

const smartProjectionStart = mailboxViewSource.indexOf(
  "entries: smartFolderEntries",
);
const smartProjectionEnd = mailboxViewSource.indexOf(
  "const currentMessageLocationById =",
  smartProjectionStart,
);
const smartProjectionSource = mailboxViewSource.slice(
  smartProjectionStart,
  smartProjectionEnd,
);
assert.match(smartProjectionSource, /useMemo\(/);
assert.doesNotMatch(
  smartProjectionSource,
  /mailboxScopedSelectionState|selectedMessageId/,
  "smart-folder projection memoization must remain selection-independent",
);

const rowProjectionStart = mailboxViewSource.indexOf(
  "selectableMessageRows,\n    selectableMessageSelections",
);
const rowProjectionEnd = mailboxViewSource.indexOf(
  "const buildManualPriorityUpdateOptions =",
  rowProjectionStart,
);
const rowProjectionSource = mailboxViewSource.slice(
  rowProjectionStart,
  rowProjectionEnd,
);
assert.match(
  rowProjectionSource,
  /useMemo\([\s\S]*nextVisibleMessages[\s\S]*nextThreadDedupedMessages[\s\S]*nextSortedMessages[\s\S]*nextSelectableMessageRows/,
  "visible, deduped, sorted, and selectable row projections must share one memo",
);
assert.doesNotMatch(
  rowProjectionSource,
  /mailboxScopedSelectionState|selectedMessageId/,
  "row projection memoization must not depend on selected-message state",
);

const threadProjectionStart = mailboxViewSource.indexOf(
  "fullMessageModalThreadMessages,\n    selectedMessageThreadMessages",
);
const threadProjectionEnd = mailboxViewSource.indexOf(
  "const selectedConversationTitle =",
  threadProjectionStart,
);
const threadProjectionSource = mailboxViewSource.slice(
  threadProjectionStart,
  threadProjectionEnd,
);
assert.match(
  threadProjectionSource,
  /useMemo\([\s\S]*fullMessageModalThreadRenderTarget === selectedMessageThreadRenderTarget[\s\S]*nextSelectedMessageThreadMessages/,
  "split and modal views of one message must reuse one thread projection",
);

const threadTimelineStart = mailboxViewSource.indexOf(
  "const renderThreadTimeline =",
);
const threadTimelineEnd = mailboxViewSource.indexOf(
  "const activeStoredCollaborationMessage =",
  threadTimelineStart,
);
const threadTimelineSource = mailboxViewSource.slice(
  threadTimelineStart,
  threadTimelineEnd,
);
assert.doesNotMatch(
  threadTimelineSource,
  /getThreadMessages\(/,
  "the shared timeline must consume its already-derived thread projection",
);

assert.match(
  mailboxViewSource,
  /useDeferredValue\(selectedMessage\)[\s\S]*useDeferredValue\([\s\S]*fullMessageModalMessage/,
  "message timelines and body rendering must use React deferred rendering",
);
const modalStart = mailboxViewSource.indexOf(
  "data-full-message-modal-message-id=",
);
const modalEnd = mailboxViewSource.indexOf(
  "<SettingsConfirmationModal",
  modalStart,
);
const modalSource = mailboxViewSource.slice(modalStart, modalEnd);
assert.ok(
  modalSource.indexOf("<DesktopWindowToolbar") >= 0 &&
    modalSource.indexOf("<DesktopWindowToolbar") <
      modalSource.indexOf("data-full-message-body-pending"),
  "the modal toolbar must render before deferred rich-body completion",
);
assert.match(
  modalSource,
  /actions=\{renderMessageActions\(fullMessageModalMessage, "full"\)\}/,
  "Reply, Reply All, Forward, More, and Priority actions must remain in the urgent shell",
);

const emailHtmlStageStart = workspaceShellSource.indexOf(
  "function EmailHtmlStage(",
);
const emailHtmlStageEnd = workspaceShellSource.indexOf(
  "const allowedImportedEmailInlineStyleProperties",
  emailHtmlStageStart,
);
const emailHtmlStageSource = workspaceShellSource.slice(
  emailHtmlStageStart,
  emailHtmlStageEnd,
);
assert.match(emailHtmlStageSource, /sandbox="allow-popups allow-popups-to-escape-sandbox"/);
assert.match(emailHtmlStageSource, /scheduleDarkModeReadabilityFix\(800\)/);
assert.match(emailHtmlStageSource, /scheduleLightEmailStageTextLinkFix\(800\)/);

console.log("\nWorkspaceShell latency interaction tests passed.");
