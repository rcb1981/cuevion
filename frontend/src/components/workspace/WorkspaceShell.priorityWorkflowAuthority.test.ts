import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const workspaceSource = readFileSync(
  "src/components/workspace/WorkspaceShell.tsx",
  "utf8",
);
const authoritySource = readFileSync(
  "src/lib/priorityWorkflowAuthority.ts",
  "utf8",
);

const bridgeStart = workspaceSource.indexOf(
  "const resolvePriorityWorkflowActionTarget =",
);
const bridgeEnd = workspaceSource.indexOf(
  "const handleSetManualLabelOverride =",
  bridgeStart,
);
const bridge = workspaceSource.slice(bridgeStart, bridgeEnd);

assert.ok(
  bridgeStart >= 0 && bridgeEnd > bridgeStart,
  "the Priority workflow writer bridge must remain narrowly bounded",
);
assert.match(
  bridge,
  /serverAuthorityEnabled:[\s\S]*?workspaceDataMode === "live" && hasAuthenticatedMemberAuthority[\s\S]*?savedManagedInboxes\.find/,
  "only authenticated live managed mailboxes may enter canonical server authority",
);
assert.match(
  bridge,
  /resolution\.status === "local_only"[\s\S]*?applyManualPriorityUpdate/,
  "legacy/demo local-only behavior must remain explicitly isolated",
);
assert.match(
  bridge,
  /resolution\.status === "invalid"[\s\S]*?rejectCanonicalPriorityWorkflowAction/,
  "canonical invalid identities must fail without a local fallback",
);

const manualStart = bridge.indexOf("const handleSetManualPriority = async");
const doneStart = bridge.indexOf("const handleMarkPriorityItemDone = async");
const waitingStart = bridge.indexOf(
  "const handleSuccessfulConversationReply = async",
);
const manual = bridge.slice(manualStart, doneStart);
const done = bridge.slice(doneStart, waitingStart);
const waiting = bridge.slice(waitingStart);

assert.match(
  manual,
  /set_manual_priority[\s\S]*?value: shouldBePriority \? "priority" : "removed"[\s\S]*?commit: \(record\) =>[\s\S]*?applyManualPriorityRecordToMirror/,
  "Add/Remove Priority must apply the local manual mirror only from a canonical write record",
);
assert.match(
  manual,
  /set_cleared[\s\S]*?value: "active"[\s\S]*?applyPriorityClearedRecordToMirror/,
  "the existing Add Priority reopen side effect must be server-authoritative",
);
assert.match(
  manual,
  /set_waiting[\s\S]*?value: "absent"[\s\S]*?clearWaitingRecordFromMirror/,
  "Remove Priority must clear waiting through server authority before its mirror",
);
assert.match(
  done,
  /set_cleared[\s\S]*?value: "cleared"[\s\S]*?applyPriorityClearedRecordToMirror[\s\S]*?set_waiting[\s\S]*?value: "absent"[\s\S]*?clearWaitingRecordFromMirror/,
  "Done must persist cleared and the existing waiting clear in order",
);
assert.match(
  done,
  /coordinatePrioritySemanticNewInboundRemoval<MailMessage>[\s\S]*?dismissExactPrioritySemanticNewInboundObservation[\s\S]*?set_cleared/,
  "Done must retain exact-turn semantic dismissal before workflow clearing",
);
assert.match(
  waiting,
  /set_waiting[\s\S]*?value: "waiting_on_other"[\s\S]*?applyWaitingOnOtherTransitionToMirror/,
  "successful Reply/Reply All must persist waiting_on_other before its mirror",
);
assert.match(
  workspaceSource,
  /pendingPriorityWorkflowWaitingTransitionsRef[\s\S]*?if \(!isDeterministicWaitingCommitted\)[\s\S]*?pendingWorkflowKey[\s\S]*?return;[\s\S]*?requestPrioritySemanticAssessment/,
  "outgoing semantic work must wait for the server-first waiting mirror instead of dropping its trigger",
);

const returnedReplyEffect = workspaceSource.slice(
  workspaceSource.indexOf(
    "const returnedReplyTriggers = findPrioritySemanticReturnedReplyTriggers",
  ),
  bridgeStart,
);
assert.match(
  returnedReplyEffect,
  /hasAuthenticatedMemberAuthority[\s\S]*?commitPriorityWorkflowReturnedReplyTransition/,
  "authenticated returned-reply evidence must enter the workflow writer",
);
assert.match(
  bridge,
  /commitPriorityWorkflowReturnedReplyTransition = async[\s\S]*?value: "returned_reply"[\s\S]*?commit: \(\) =>[\s\S]*?reconcileWaitingOnOtherStore[\s\S]*?setWaitingOnOtherStore/,
  "returned_reply must reach the server before deterministic local evidence is committed",
);
assert.match(
  workspaceSource,
  /onSuccessfulConversationReply=\{[\s\S]*?handleSuccessfulConversationReply[\s\S]*?\}/,
  "the compose success callback must use the canonical waiting writer",
);

assert.match(
  workspaceSource,
  /PriorityWorkflowHydrationCoordinator[\s\S]*?hydrateMailbox\([\s\S]*?targets: targetEntries\.map/,
  "accepted provider generations must enter the bounded workflow hydration coordinator",
);
assert.match(
  workspaceSource,
  /acceptedRefreshAuthority\.connectionKey !== connectionKey[\s\S]*?acceptedRefreshAuthority\.authorityGeneration[\s\S]*?isCurrentGeneration/,
  "workflow hydration must be fenced by the accepted connection and provider authority generation",
);
assert.match(
  workspaceSource,
  /priorityWorkflowHydrationGenerationKeysRef\.current\.get\(mailbox\.id\)[\s\S]*?generationKey[\s\S]*?return;/,
  "one accepted provider generation must schedule workflow hydration only once",
);
assert.match(
  workspaceSource,
  /priorityWorkflowAuthorityStoreRef\.current!\.acceptWrite\([\s\S]*?input\.commit\(record\)[\s\S]*?setPriorityWorkflowAuthorityRevision/,
  "P2 write records must update read authority before their local mirror",
);
const hydrationStart = workspaceSource.indexOf(
  "priorityWorkflowHydrationCoordinatorRef.current!.hydrateMailbox",
);
const hydrationEnd = workspaceSource.indexOf(
  "const commitPriorityWorkflowReturnedReplyTransition = async",
  hydrationStart,
);
const hydrationBridge = workspaceSource.slice(hydrationStart, hydrationEnd);
assert.ok(
  hydrationStart >= 0 && hydrationEnd > hydrationStart,
  "the P3 hydration bridge must remain narrowly bounded",
);
assert.doesNotMatch(
  hydrationBridge,
  /\.setManualPriority\(|\.setCleared\(|\.setWaiting\(/,
  "startup hydration must never backfill legacy workflow state to the server",
);
assert.match(
  authoritySource,
  /else if \(response\.error\.ambiguous\)[\s\S]*?this\.#client\.read/,
  "the dormant read helper may run only to reconcile an ambiguous write",
);
assert.doesNotMatch(
  authoritySource,
  /Date\.now|new Date/,
  "workflow ordering must use server versions and generations, never browser clocks",
);
assert.match(
  workspaceSource,
  /const \[localManualPriorityOverrides,[\s\S]*?window\.localStorage\.getItem\(manualPriorityOverridesStorageKey\)[\s\S]*?const \[localPriorityClearedKeys,[\s\S]*?window\.localStorage\.getItem\(priorityClearedStorageKey\)[\s\S]*?const \[localWaitingOnOtherStore,[\s\S]*?window\.localStorage\.getItem\(waitingOnOtherStorageKey\)/,
  "legacy browser workflow data must remain isolated behind explicit local mirror state",
);
assert.match(
  workspaceSource,
  /const manualPriorityOverrides = useMemo[\s\S]*?removePersistedMessageStateValue[\s\S]*?projection\.manualPriority/,
  "canonical manual Priority reads must mask local history and project server authority",
);
assert.match(
  workspaceSource,
  /const priorityClearedKeys = useMemo[\s\S]*?withoutLegacy[\s\S]*?projection\.cleared === "cleared"/,
  "canonical Done reads must replace local cleared keys",
);
assert.match(
  workspaceSource,
  /const waitingOnOtherStore = useMemo[\s\S]*?clearConversationWaitingOnOther[\s\S]*?projection\.waiting !== "waiting_on_other"/,
  "canonical waiting reads must remove local waiting history before server projection",
);
assert.match(
  workspaceSource,
  /authority\.record\.waiting === "returned_reply"[\s\S]*?hasEvidence: true[\s\S]*?confidence: "high"/,
  "server returned_reply must feed the existing runtime selector as high-confidence evidence",
);
assert.match(
  hydrationBridge,
  /applyManualPriorityRecordToMirror[\s\S]*?applyPriorityClearedRecordToMirror[\s\S]*?applyWaitingRecordToMirror/,
  "successful hydration must normalize all three local workflow mirrors",
);
assert.match(
  workspaceSource,
  /open=\{Boolean\(priorityWorkflowFailure\)\}[\s\S]*?Change not saved[\s\S]*?Try again/,
  "canonical failures must have bounded user-facing copy and an explicit retry",
);

console.log("\nWorkspaceShell Priority workflow authority integration tests passed.");
