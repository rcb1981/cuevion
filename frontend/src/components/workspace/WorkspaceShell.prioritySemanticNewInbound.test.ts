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
  /requestPrioritySemanticNewInboundAssessment\(\{[\s\S]*?mailboxId: selected\.candidate\.mailboxId,[\s\S]*?trigger: "new_inbound",[\s\S]*?incomingLocator: selected\.candidate\.incomingLocator,[\s\S]*?\}\)[\s\S]*?\.catch\(\(\) => undefined\)/,
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

const hydrationRequestStart = source.indexOf(
  "void requestPrioritySemanticNewInboundHydration({",
);
const hydrationRequestEnd = source.indexOf(
  "commitPrioritySemanticNewInboundHydrationRecords(",
  hydrationRequestStart,
);
const hydrationRequestSource = source.slice(
  hydrationRequestStart,
  hydrationRequestEnd,
);
assert.ok(
  hydrationRequestStart >= 0 && hydrationRequestEnd > hydrationRequestStart,
  "one bounded runtime hydration request must exist",
);
assert.match(
  hydrationRequestSource,
  /operation: "hydrate_new_inbound",[\s\S]*?mailboxId: mailbox\.id/,
  "hydration sends only the exact operation and one owned connected mailbox ID",
);
assert.doesNotMatch(
  hydrationRequestSource,
  /incomingLocator|assessment|state:|confidence|reasonCode|priorityEffect|subject|body|requestPrioritySemanticNewInboundAssessment/,
  "hydration cannot submit semantic authority, content, or dispatch assessment",
);
assert.match(
  hydrationRequestSource,
  /providerArchiveCurrentConnectionKeysRef\.current\[mailbox\.id\][\s\S]*?connectionKey[\s\S]*?providerArchiveConnectionEpochsRef\.current\[mailbox\.id\][\s\S]*?connectionEpoch/,
  "a stale hydration response cannot replace the current account bucket after reconnect",
);
assert.equal(
  source.match(/requestPrioritySemanticNewInboundHydration\(/g)?.length,
  1,
  "Workspace must issue hydration from one mailbox-scoped orchestration site",
);

const hydrationStateStart = source.indexOf(
  "const [prioritySemanticNewInboundHydrationState",
);
const hydrationStateEnd = source.indexOf(
  "const [prioritySemanticNewInboundDrainEpoch",
  hydrationStateStart,
);
assert.ok(
  hydrationStateStart >= 0 && hydrationStateEnd > hydrationStateStart,
  "runtime-only hydration state must exist",
);
assert.doesNotMatch(
  source.slice(hydrationStateStart, hydrationStateEnd),
  /localStorage|sessionStorage|setItem|persist/i,
  "semantic hydration observations must never become browser-persisted authority",
);

const canonicalPriorityAssignment = source.indexOf(
  "const livePriorityInboxEntries = broadLivePriorityInboxEntries;",
);
const hydratedProjectionStart = source.indexOf(
  "const prioritySemanticNewInboundHydratedObservations = useMemo",
);
const hydratedProjectionEnd = source.indexOf(
  "const priorityReasonCopyForCandidates",
  hydratedProjectionStart,
);
const hydratedProjectionSource = source.slice(
  hydratedProjectionStart,
  hydratedProjectionEnd,
);
assert.ok(
  canonicalPriorityAssignment >= 0 &&
    hydratedProjectionStart > canonicalPriorityAssignment &&
    hydratedProjectionEnd > hydratedProjectionStart,
  "hydrated shadow projection must be assembled only after the canonical Priority collection",
);
assert.match(
  hydratedProjectionSource,
  /acceptedRefreshAuthority[\s\S]*?bucket\.connectionKey[\s\S]*?providerArchiveCurrentConnectionKeysRef[\s\S]*?bucket\.connectionEpoch[\s\S]*?providerArchiveConnectionEpochsRef[\s\S]*?isCurrentGeneration/,
  "local snapshots and reconnect/account replacement cannot project before a matching provider-accepted generation",
);
assert.match(
  hydratedProjectionSource,
  /canonicalFolderOrder\.flatMap[\s\S]*?record\.identity\.conversationId[\s\S]*?currentEntry\.folder !== "Inbox"[\s\S]*?resolvePrioritySemanticNewInboundHydrationLiveIdentity/,
  "hydration considers newer Sent/folder turns and requires the exact current canonical latest turn to remain Inbox-visible",
);
assert.match(
  hydratedProjectionSource,
  /threadIdentityContext\.folder[\s\S]*?threadIdentityContext\.uidValidity[\s\S]*?imapNamespaceKeys\.size === 1[\s\S]*?resolveMailDateMs/,
  "IMAP UIDs are compared only inside one exact folder/UIDVALIDITY namespace; mixed namespaces use dates or fail closed",
);
assert.match(
  hydratedProjectionSource,
  /isOwnedByDeterministicOpenLoop = Object\.values\([\s\S]*?effectiveWaitingOnOtherStore[\s\S]*?waitingRecord\.conversationKey === record\.identity\.conversationId[\s\S]*?if \(isOwnedByDeterministicOpenLoop\)/,
  "waiting_on_other and returned_reply retain sole ownership of their conversations",
);
assert.match(
  hydratedProjectionSource,
  /readyInboxMessageKeys[\s\S]*?isWorkspaceMessageSpamSuppressed[\s\S]*?resolveOrganizerCategory[\s\S]*?resolveMessageNoisePolicy\(message\)\.allowsPositiveActionability[\s\S]*?isPrioritySemanticNewInboundHydratedObservationCurrent/,
  "LOW/Filtered, Spam/Trash/Archive, Organizer, noise, mailbox, version, and latest-turn gates must precede observation projection",
);
assert.doesNotMatch(
  hydratedProjectionSource,
  /setManualPriorityOverrides|setPriorityClearedKeys|setWaitingOnOtherStore|livePriorityInboxEntries\s*=|priorityEffect:\s*"promote/,
  "hydration remains observe-only and cannot mutate canonical Priority or deterministic workflow state",
);
assert.equal(
  source.match(/prioritySemanticNewInboundHydratedObservations/g)?.length,
  2,
  "hydrated records are held only as an isolated shadow observation projection",
);
assert.match(
  refreshSource,
  /applyLiveInboxMessagesToMailboxStore\([\s\S]*?prioritySemanticNewInboundAcceptedRefreshAuthorityRef\.current\[mailboxId\]/,
  "only a successfully accepted and published provider refresh grants live-row projection authority",
);
assert.match(
  refreshSource,
  /acceptedImapUidValidity[\s\S]*?messages\.every\([\s\S]*?message\.providerFolder === "INBOX"[\s\S]*?message\.uidValidity === acceptedImapUidValidity[\s\S]*?new Set\(messages\.map[\s\S]*?freshImapUids: acceptedFreshImapUids/,
  "accepted custom-IMAP authority must bind to exact fresh response UID identities, never merged local rows",
);
assert.match(
  hydratedProjectionSource,
  /acceptedRefreshAuthority\.provider === "custom_imap"[\s\S]*?acceptedRefreshAuthority\.imapUidValidity === null[\s\S]*?acceptedRefreshAuthority\.freshImapUids === null[\s\S]*?message\.uidValidity !==[\s\S]*?!acceptedRefreshAuthority\.freshImapUids\.has\(message\.imapUid\)/,
  "a retained IMAP row absent from the trusted accepted response must fail closed",
);

console.log("\nWorkspaceShell new_inbound shadow integration tests passed.");
