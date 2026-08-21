import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const workspaceSource = readFileSync(
  "src/components/workspace/WorkspaceShell.tsx",
  "utf8",
);
const semanticStateSource = readFileSync(
  "src/lib/prioritySemanticState.ts",
  "utf8",
);

const sendStart = workspaceSource.indexOf("const sendMessage = async");
const sendEnd = workspaceSource.indexOf("const closeMenus =", sendStart);
const sendSource = workspaceSource.slice(sendStart, sendEnd);
assert.ok(sendStart >= 0 && sendEnd > sendStart, "sendMessage source must exist");

const sendFailureReturn = sendSource.indexOf("if (!sendResponse.ok)");
const deterministicWaitingTransition = sendSource.indexOf(
  "onSuccessfulConversationReply(",
);
const outgoingSemanticTrigger = sendSource.indexOf(
  "onPrioritySemanticReplyConfirmed({",
);
assert.ok(
  sendFailureReturn >= 0 &&
    deterministicWaitingTransition > sendFailureReturn &&
    outgoingSemanticTrigger > deterministicWaitingTransition,
  "Trigger A must run only after confirmed send and the deterministic waiting transition",
);
assert.match(
  sendSource,
  /if \(isReplyComposeMode && composeSourceMessage\) \{[\s\S]*?onSuccessfulConversationReply\([\s\S]*?if \(sendResponse\.semanticEventRef\) \{[\s\S]*?onPrioritySemanticReplyConfirmed\(/,
  "only successful Reply / Reply All with a server ticket may dispatch Trigger A",
);
assert.doesNotMatch(
  sendSource.slice(0, sendFailureReturn),
  /onPrioritySemanticReplyConfirmed/,
  "failed sends must not dispatch Trigger A",
);
assert.match(
  sendSource,
  /try \{[\s\S]*?onPrioritySemanticReplyConfirmed\([\s\S]*?\} catch \{[\s\S]*?shadow-only assessment/,
  "post-send semantic orchestration must be contained so it cannot change send UX",
);
assert.doesNotMatch(
  sendSource,
  /await\s+onPrioritySemanticReplyConfirmed|await\s+requestPrioritySemanticAssessment/,
  "email sending must never wait for semantic analysis",
);

const deterministicStoreCommit = workspaceSource.indexOf(
  "setWaitingOnOtherStore(effectiveWaitingOnOtherStore)",
);
const pendingReconciliationSnapshot = workspaceSource.indexOf(
  "pendingPrioritySemanticReconciliationRef.current = {",
  deterministicStoreCommit,
);
const reconciliationStart = workspaceSource.indexOf(
  "findPrioritySemanticReturnedReplyTriggers(",
  pendingReconciliationSnapshot,
);
const commitEffectStart = workspaceSource.indexOf(
  "const isDeterministicReturnedReplyCommitted",
  reconciliationStart,
);
const incomingRequestStart = workspaceSource.indexOf(
  "requestPrioritySemanticAssessment(semanticRequest)",
  commitEffectStart,
);
assert.ok(
  deterministicStoreCommit >= 0 &&
    pendingReconciliationSnapshot > deterministicStoreCommit &&
    reconciliationStart > pendingReconciliationSnapshot &&
    commitEffectStart > deterministicStoreCommit &&
    incomingRequestStart > commitEffectStart,
  "Trigger B must commit returned_reply before discovery, queueing, and request",
);
assert.doesNotMatch(
  workspaceSource.slice(deterministicStoreCommit, reconciliationStart),
  /requestPrioritySemanticAssessment/,
  "the deterministic reconciliation effect must not start semantic work",
);
assert.match(
  workspaceSource.slice(pendingReconciliationSnapshot, commitEffectStart),
  /useEffect\(\(\) => \{[\s\S]*?try \{[\s\S]*?findPrioritySemanticReturnedReplyTriggers\(/,
  "post-commit semantic discovery must be isolated in its own guarded effect",
);
assert.match(
  workspaceSource.slice(commitEffectStart, incomingRequestStart + 200),
  /committedConversationRecord\?\.state === "returned_reply"[\s\S]*?committedConversationRecord\.returnedMessageKey ===[\s\S]*?trigger\.returnedMessageKey[\s\S]*?trigger: "incoming_reply"[\s\S]*?requestPrioritySemanticAssessment\(semanticRequest\)/,
  "the post-commit effect must confirm the exact deterministic returned record",
);
assert.match(
  workspaceSource.slice(reconciliationStart, commitEffectStart),
  /trigger\.incomingLocator\.provider === "google"[\s\S]*?resolvePrioritySemanticActiveEventRef\([\s\S]*?if \(!activeEvent\) \{[\s\S]*?return;/,
  "only Gmail returned replies require a prior signed active event",
);
assert.match(
  workspaceSource.slice(commitEffectStart, incomingRequestStart + 200),
  /trigger\.incomingLocator\.provider === "google"[\s\S]*?activeEventRef: activeEvent!\.activeEventRef[\s\S]*?: \{[\s\S]*?incomingLocator: trigger\.incomingLocator/,
  "custom IMAP returned replies must use the ref-less locator request branch",
);

const outgoingCommitCheck = workspaceSource.indexOf(
  "const isDeterministicWaitingCommitted",
);
const outgoingRequestStart = workspaceSource.indexOf(
  "requestPrioritySemanticAssessment({",
  outgoingCommitCheck,
);
assert.ok(
  outgoingCommitCheck >= 0 && outgoingRequestStart > outgoingCommitCheck,
  "Trigger A request must wait for committed deterministic waiting_on_other",
);
const outgoingHandlerStart = workspaceSource.indexOf(
  "const handlePrioritySemanticReplyConfirmed",
);
assert.doesNotMatch(
  workspaceSource.slice(outgoingHandlerStart, outgoingCommitCheck),
  /requestPrioritySemanticAssessment/,
  "the post-send callback may queue a ticket but must not start the request before commit",
);
assert.match(
  workspaceSource.slice(outgoingCommitCheck, outgoingRequestStart),
  /resolvePrioritySemanticActiveEventRef\([\s\S]*?activeEvent\?\.activeEventRef !== trigger\.activeEventRef/,
  "a superseded signed outgoing event must be dropped before its model request",
);

const requestCalls = workspaceSource.match(
  /requestPrioritySemanticAssessment\(/g,
) ?? [];
assert.equal(
  requestCalls.length,
  2,
  "Workspace may request semantics only for outgoing_reply and incoming_reply",
);
assert.equal(
  workspaceSource.match(/trigger: "outgoing_reply"/g)?.length,
  1,
  "there must be one outgoing semantic trigger site",
);
assert.equal(
  workspaceSource.match(/trigger: "incoming_reply"/g)?.length,
  1,
  "there must be one incoming semantic trigger site",
);

assert.match(
  workspaceSource,
  /prioritySemanticObservationsRef\.current =[\s\S]*?addPrioritySemanticShadowObservation\([\s\S]*?observationKey,[\s\S]*?observation/,
  "semantic results must remain in a private observation ref",
);
assert.match(
  workspaceSource,
  /rememberPrioritySemanticRequestedTriggerKey\([\s\S]*?requestedPrioritySemanticTriggerKeysRef\.current/,
  "long-lived requested trigger keys must use the bounded set helper",
);
assert.match(
  workspaceSource,
  /rememberPrioritySemanticPendingTrigger\([\s\S]*?pendingPrioritySemanticReturnedReplyTriggersRef\.current/,
  "pending returned triggers must use the bounded map helper",
);
assert.doesNotMatch(
  workspaceSource,
  /setPrioritySemantic|localStorage\.setItem\([^)]*observation|JSON\.stringify\([^)]*observation/,
  "semantic assessments must not enter React state or local persistence",
);

const priorityGateStart = workspaceSource.indexOf(
  "const normalPriorityGateCandidateEntries",
);
const priorityItemsEnd = workspaceSource.indexOf(
  "const livePriorityInboxItems",
  priorityGateStart,
);
const priorityAssembly = workspaceSource.slice(priorityGateStart, priorityItemsEnd);
assert.ok(
  priorityGateStart >= 0 && priorityItemsEnd > priorityGateStart,
  "Priority assembly source must exist",
);
assert.doesNotMatch(
  priorityAssembly,
  /prioritySemantic|semanticState|effectiveSemanticState/i,
  "shadow semantics must not enter eligibility, filtering, sorting, or the gate",
);

const dashboardCountStart = workspaceSource.indexOf(
  "priorityCount={",
  priorityItemsEnd,
);
const dashboardCountEnd = workspaceSource.indexOf(
  "supplementalItems={livePriorityInboxItems}",
  dashboardCountStart,
);
assert.doesNotMatch(
  workspaceSource.slice(dashboardCountStart, dashboardCountEnd),
  /prioritySemantic|semanticState|effectiveSemanticState/i,
  "Dashboard Priority count parity must remain independent of shadow semantics",
);

assert.match(
  semanticStateSource,
  /export function readPrioritySemanticActiveEventRefStore\([\s\S]*?try \{[\s\S]*?storage\.getItem\([\s\S]*?\} catch \{[\s\S]*?return \{\};/,
  "semantic ticket reads must swallow browser storage errors",
);
assert.match(
  semanticStateSource,
  /export function persistPrioritySemanticActiveEventRefStore\([\s\S]*?try \{[\s\S]*?storage\.setItem\([\s\S]*?\} catch \{/,
  "semantic ticket writes must swallow browser storage errors",
);

console.log("\nWorkspaceShell Priority semantic shadow integration tests passed.");
