import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(
  "src/components/workspace/WorkspaceShell.tsx",
  "utf8",
);

const refreshStart = source.indexOf("const refreshMailboxById = async");
const refreshEnd = source.indexOf(
  "gmailArchiveReconciliationRefreshRef.current =",
  refreshStart,
);
const refreshSource = source.slice(refreshStart, refreshEnd);
assert.ok(
  refreshStart >= 0 && refreshEnd > refreshStart,
  "authoritative mailbox refresh source must exist",
);

const finalFence = refreshSource.indexOf("const gmailInboxResolution =");
const discovery = refreshSource.indexOf(
  "observePrioritySemanticNewInboundSnapshot({",
);
const snapshotPersistence = refreshSource.indexOf("saveLiveInboxSnapshot({");
const storePublication = refreshSource.indexOf(
  "applyLiveInboxMessagesToMailboxStore(",
);
assert.ok(
  finalFence >= 0 &&
    discovery > finalFence &&
    snapshotPersistence > discovery &&
    storePublication > snapshotPersistence,
  "new-inbound discovery must run once at the accepted provider refresh boundary before persistence and React publication",
);
assert.match(
  refreshSource.slice(finalFence, snapshotPersistence),
  /gmailInboxResolution\?\.stale[\s\S]*?customImapInboxResolution\?\.stale[\s\S]*?prioritySemanticNewInboundScopeRef\.current[\s\S]*?mode: response\.prioritySemanticNewInboundMode[\s\S]*?inboxUidSet: response\.inboxUidSet[\s\S]*?messages,/,
  "discovery must consume only accepted server snapshots, the server-derived mode, and the full provider identity set",
);
assert.match(
  refreshSource.slice(discovery, snapshotPersistence),
  /connectionKeyAtFetchStart[\s\S]*?connectionEpochAtFetchStart[\s\S]*?authorityGenerationAtFetchStart[\s\S]*?imapTrashMutationPublicationEpoch:/,
  "every returned candidate must retain the exact connection, generation, and IMAP mutation fences from its fetch",
);
assert.match(
  refreshSource.slice(discovery, snapshotPersistence),
  /pendingPrioritySemanticNewInboundCandidatesRef\.current\.size >[\s\S]*?PRIORITY_SEMANTIC_NEW_INBOUND_MAX_PENDING/,
  "the in-memory handoff must remain bounded",
);
assert.doesNotMatch(
  source.slice(0, refreshStart),
  /observePrioritySemanticNewInboundSnapshot\(\{/,
  "render and hydration paths must not discover new inbound mail",
);

const drainStart = source.indexOf("void prioritySemanticNewInboundDrainEpoch;");
const drainEnd = source.indexOf(
  "const priorityReasonCopyForCandidates",
  drainStart,
);
const drainSource = source.slice(drainStart, drainEnd);
assert.ok(
  drainStart >= 0 && drainEnd > drainStart,
  "post-reconciliation new-inbound drain must exist",
);
assert.match(
  drainSource,
  /if \(!isPrioritySemanticDeterministicStoreCommitted\) \{[\s\S]*?return;/,
  "new_inbound must wait until deterministic waiting/returned reconciliation is committed",
);
assert.match(
  drainSource,
  /providerArchiveCurrentConnectionKeysRef[\s\S]*?providerArchiveConnectionEpochsRef[\s\S]*?gmailInboxAuthorityRef\.current\.isCurrentGeneration[\s\S]*?customImapInboxAuthorityRef\.current\.isCurrentGeneration[\s\S]*?providerImapTrashInboxMutationPublicationEpochsRef/,
  "the drain must revalidate every provider fence before using a candidate",
);
assert.match(
  drainSource,
  /canonicalFolderOrder\.flatMap[\s\S]*?exactLocations\.length !== 1[\s\S]*?getMailboxReadyInboxMessagesForWorkspaceMailbox[\s\S]*?exactReadyMessages\.length !== 1/,
  "only one exact current ready-Inbox row may continue",
);
assert.match(
  drainSource,
  /resolveCanonicalConversationIdentity[\s\S]*?!canonicalConversation\.isAuthoritativeConversation[\s\S]*?getReturnedReplySenderAddress[\s\S]*?hasActiveOpenLoop = Object\.values[\s\S]*?effectiveWaitingOnOtherStore/,
  "authoritative conversation, external sender, and incoming_reply ownership must be checked after reconciliation",
);
assert.match(
  drainSource,
  /hasDeterministicPriority = livePriorityInboxEntries\.some[\s\S]*?resolveOrganizerCategory\([\s\S]*?productAccess === "bundle"[\s\S]*?resolveMessageNoisePolicy\(message\)\.allowsPositiveActionability[\s\S]*?isPrioritySemanticNewInboundEligible/,
  "current Priority, Organizer, and normalized noise exclusions must precede shadow analysis",
);
assert.match(
  drainSource,
  /Math\.max\([\s\S]*?incomingLocator\.imapUid[\s\S]*?providerDateMs[\s\S]*?Math\.min\([\s\S]*?snapshotRank/,
  "multiple unseen messages in one conversation must select one provider-latest row and fail closed on ambiguity",
);
assert.match(
  drainSource,
  /requestPrioritySemanticNewInboundAssessment\(\{[\s\S]*?mailboxId: selected\.candidate\.mailboxId,[\s\S]*?trigger: "new_inbound",[\s\S]*?incomingLocator: selected\.candidate\.incomingLocator,[\s\S]*?\}\)\.catch\(\(\) => undefined\)/,
  "the browser request must contain only mailboxId, the exact trigger, and the provider locator",
);
assert.doesNotMatch(
  drainSource,
  /recordPrioritySemanticObservation|setPrioritySemanticObservationState|setWaitingOnOtherStore|setManualPriorityOverrides|authoredText|messageText/,
  "new-inbound responses must not enter active observations or mutate deterministic/Priority state",
);
assert.doesNotMatch(
  drainSource,
  /subject\.includes|snippet\.includes|body\.includes|actionKeyword|action_keyword/,
  "discovery must not use an action-keyword prefilter",
);
assert.equal(
  source.match(/requestPrioritySemanticNewInboundAssessment\(/g)?.length,
  1,
  "Workspace must have exactly one new-inbound shadow request site",
);

console.log("\nWorkspaceShell new_inbound shadow integration tests passed.");
