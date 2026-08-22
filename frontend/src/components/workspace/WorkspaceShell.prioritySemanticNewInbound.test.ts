import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import "sucrase/register/tsx.js";

const workspaceRuntime = require("./WorkspaceShell.tsx") as Record<
  string,
  (...args: never[]) => unknown
>;
const newInboundRuntime = require("../../lib/prioritySemanticNewInbound") as Record<
  string,
  (...args: never[]) => unknown
>;

assert.equal(
  typeof workspaceRuntime.coordinatePrioritySemanticNewInboundAssessmentCommit,
  "function",
  "a direct assessed/cached new-inbound result must have an async revalidated runtime commit path",
);
assert.equal(
  typeof workspaceRuntime.mergePrioritySemanticNewInboundPromotionsIntoCanonicalPriorityEntries,
  "function",
  "active new-inbound promotions must merge into the one canonical Priority collection",
);
assert.equal(
  typeof workspaceRuntime.isPrioritySemanticNewInboundCurrentUserContainmentSatisfied,
  "function",
  "current manual-removal, completion, and sender ownership must share one runtime gate",
);
assert.equal(
  typeof workspaceRuntime.resolvePrioritySemanticNewInboundHydrationCommitPolicy,
  "function",
  "ACTIVE hydration must detect direct-result mutation races before replacement",
);

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
  /hasDeterministicPriority = broadLivePriorityInboxEntries\.some[\s\S]*?resolveOrganizerCategory\([\s\S]*?productAccess === "bundle"[\s\S]*?resolveMessageNoisePolicy\(message\)\.allowsPositiveActionability[\s\S]*?isPrioritySemanticNewInboundEligible/,
  "deterministic Priority, Organizer, and normalized noise exclusions must precede semantic analysis",
);
assert.match(
  drainSource,
  /Math\.max\([\s\S]*?incomingLocator\.imapUid[\s\S]*?providerDateMs[\s\S]*?Math\.min\([\s\S]*?snapshotRank/,
  "multiple unseen messages in one conversation must select one provider-latest row and fail closed on ambiguity",
);
assert.match(
  drainSource,
  /coordinatePrioritySemanticNewInboundAssessmentCommit\(\{[\s\S]*?response: requestPrioritySemanticNewInboundAssessment\(\{[\s\S]*?mailboxId,[\s\S]*?trigger: "new_inbound",[\s\S]*?incomingLocator: selectedEntry\.candidate\.incomingLocator/,
  "the browser request must contain only mailboxId, the exact trigger, and the provider locator",
);
assert.match(
  drainSource,
  /isCurrent: \(record\)[\s\S]*?acceptedRefreshAuthority\.newInboundMode[\s\S]*?acceptedRefreshAuthority\.authorityGeneration[\s\S]*?providerArchiveCurrentConnectionKeysRef[\s\S]*?providerArchiveConnectionEpochsRef[\s\S]*?isCurrentGeneration[\s\S]*?providerImapTrashInboxMutationPublicationEpochsRef[\s\S]*?resolvePrioritySemanticNewInboundCurrentInboxMessage[\s\S]*?resolveManualPriorityOverride\([\s\S]*?manualPriorityOverridesRef\.current[\s\S]*?priorityClearedKeysRef\.current[\s\S]*?getReturnedReplySenderAddress[\s\S]*?prioritySemanticNewInboundOwnedAddressSetRef\.current[\s\S]*?commit: \(record\)[\s\S]*?prioritySemanticNewInboundMutationRevisionsRef\.current[\s\S]*?commitPrioritySemanticNewInboundHydrationRecords/,
  "a direct result must revalidate provider mode/generation, current turn, manual removal/completion, and sender ownership before synchronously revising the shared runtime bucket",
);
assert.doesNotMatch(
  drainSource,
  /recordPrioritySemanticObservation|setPrioritySemanticObservationState|setWaitingOnOtherStore|setManualPriorityOverrides|authoredText|messageText/,
  "direct new-inbound results cannot mutate deterministic workflow or manual Priority authority",
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
const hydrationOrchestrationStart = source.lastIndexOf(
  "useEffect(() => {",
  hydrationRequestStart,
);
const hydrationOrchestrationEnd = source.indexOf(
  "const externalInboundConversationEntries",
  hydrationRequestStart,
);
const hydrationOrchestrationSource = source.slice(
  hydrationOrchestrationStart,
  hydrationOrchestrationEnd,
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
assert.match(
  source.slice(hydrationRequestStart, hydrationRequestEnd + 500),
  /response\.newInboundMode === "off" \? \[\] : response\.records/,
  "shadow and active hydration records must share one bounded runtime bucket while off clears it",
);
assert.match(
  hydrationOrchestrationSource,
  /acceptedRefreshAuthority[\s\S]*?requestAcceptedMode[\s\S]*?requestAuthorityGeneration[\s\S]*?requestMutationRevision[\s\S]*?requestKey/,
  "each bounded hydration request must capture accepted mode/generation and the per-mailbox direct mutation revision",
);
assert.match(
  hydrationRequestSource,
  /currentAcceptedRefreshAuthority[\s\S]*?newInboundMode !==[\s\S]*?requestAcceptedMode[\s\S]*?authorityGeneration !==[\s\S]*?requestAuthorityGeneration[\s\S]*?resolvePrioritySemanticNewInboundHydrationCommitPolicy[\s\S]*?preserve_and_rehydrate[\s\S]*?setPrioritySemanticNewInboundHydrationRefreshEpoch[\s\S]*?return;/,
  "an obsolete authority response or ACTIVE/direct race cannot replace current records and the latter schedules fresh model-free hydration",
);
assert.match(
  hydrationOrchestrationSource,
  /requestAcceptedMode === "off"[\s\S]*?commitPrioritySemanticNewInboundHydrationRecords\([\s\S]*?\[\],[\s\S]*?true,[\s\S]*?return;[\s\S]*?requestPrioritySemanticNewInboundHydration/,
  "accepted off mode must clear immediately without waiting for a hydration round trip",
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

const broadPriorityAssignment = source.indexOf(
  "const broadLivePriorityInboxEntries =",
);
const hydratedProjectionStart = source.indexOf(
  "const prioritySemanticNewInboundHydratedObservations = useMemo",
);
const hydratedProjectionEnd = source.indexOf(
  "const livePriorityInboxEntries =",
  hydratedProjectionStart,
);
const hydratedProjectionSource = source.slice(
  hydratedProjectionStart,
  hydratedProjectionEnd,
);
assert.ok(
  broadPriorityAssignment >= 0 &&
    hydratedProjectionStart > broadPriorityAssignment &&
    hydratedProjectionEnd > hydratedProjectionStart,
  "current semantic observations must be assembled after deterministic Priority and before the one canonical merge",
);
assert.match(
  hydratedProjectionSource,
  /acceptedRefreshAuthority[\s\S]*?bucket\.connectionKey[\s\S]*?providerArchiveCurrentConnectionKeysRef[\s\S]*?bucket\.connectionEpoch[\s\S]*?providerArchiveConnectionEpochsRef[\s\S]*?isCurrentGeneration/,
  "local snapshots and reconnect/account replacement cannot project before a matching provider-accepted generation",
);
assert.match(
  hydratedProjectionSource,
  /canonicalFolderOrder\.flatMap[\s\S]*?collapsePrioritySemanticNewInboundGmailFolderCopies[\s\S]*?record\.identity\.conversationId[\s\S]*?currentEntry\.folder !== "Inbox"[\s\S]*?resolvePrioritySemanticNewInboundHydrationLiveIdentity/,
  "hydration collapses one authoritative Gmail Inbox/folder copy while still requiring the exact current canonical latest turn to remain Inbox-visible",
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
  /acceptedRefreshAuthority\.newInboundMode === "off"[\s\S]*?readyInboxMessageKeys[\s\S]*?isWorkspaceMessageSpamSuppressed[\s\S]*?record\.priorityEffect === "promote_new_inbound"[\s\S]*?acceptedRefreshAuthority\.newInboundMode !== "active"[\s\S]*?resolveManualPriorityOverride\([\s\S]*?isPriorityMessageCleared[\s\S]*?getReturnedReplySenderAddress[\s\S]*?ownedAddressSet[\s\S]*?resolveOrganizerCategory[\s\S]*?resolveMessageNoisePolicy\(message\)\.allowsPositiveActionability[\s\S]*?isPrioritySemanticNewInboundHydratedObservationCurrent/,
  "mode rollback, explicit removal/completion, external sender, LOW/Filtered, Spam/Trash/Archive, Organizer, noise, mailbox, version, and latest-turn gates must precede observation projection",
);
assert.doesNotMatch(
  hydratedProjectionSource,
  /setManualPriorityOverrides|setPriorityClearedKeys|setWaitingOnOtherStore|livePriorityInboxEntries\s*=/,
  "record projection cannot mutate canonical Priority or deterministic workflow state",
);
assert.match(
  source.slice(hydratedProjectionEnd),
  /const livePriorityInboxEntries =\s*mergePrioritySemanticNewInboundPromotionsIntoCanonicalPriorityEntries\([\s\S]*?broadLivePriorityInboxEntries,[\s\S]*?normalPriorityGateCandidateEntries,[\s\S]*?prioritySemanticNewInboundHydratedObservations/,
  "active records must join the same canonical Priority collection consumed by count, list, and actions",
);
assert.match(
  source,
  /mergePrioritySemanticNewInboundPromotionsIntoCanonicalPriorityEntries\([\s\S]*?priorityEffect === "promote_new_inbound"[\s\S]*?meetsPrioritySemanticNewInboundPromotionThreshold[\s\S]*?dedupeLatestCanonicalConversationEntries/,
  "only threshold-qualified active records promote and manual/semantic or direct/hydrated overlap dedupes canonically",
);
assert.match(
  source.slice(hydratedProjectionEnd),
  /livePriorityInboxItems: ReviewItem\[\] = livePriorityInboxEntries\.map[\s\S]*?resolvePrioritySemanticNewInboundCanonicalActionTarget\([\s\S]*?livePriorityInboxEntries[\s\S]*?priorityInboxCount=\{[\s\S]*?livePriorityInboxItems\.length[\s\S]*?supplementalItems=\{livePriorityInboxItems\}/,
  "Done/Remove targeting, dashboard count, and Priority list must consume the same canonical merged collection",
);
assert.match(
  refreshSource,
  /applyLiveInboxMessagesToMailboxStore\([\s\S]*?prioritySemanticNewInboundAcceptedRefreshAuthorityRef\.current\[mailboxId\]/,
  "only a successfully accepted and published provider refresh grants live-row projection authority",
);
assert.match(
  refreshSource,
  /discoveredCandidates\.forEach[\s\S]*?newInboundMode:[\s\S]*?response\.prioritySemanticNewInboundMode === "active"[\s\S]*?authorityGeneration:/,
  "every direct candidate must retain the exact accepted shadow/active mode and provider generation",
);
assert.match(
  refreshSource,
  /previousAcceptedRefreshAuthority[\s\S]*?nextAcceptedRefreshAuthority[\s\S]*?newInboundMode:[\s\S]*?response\.prioritySemanticNewInboundMode === "active"[\s\S]*?response\.prioritySemanticNewInboundMode === "shadow"[\s\S]*?authorityGeneration: acceptedAuthorityGeneration[\s\S]*?previousAcceptedRefreshAuthority\.authorityGeneration !==[\s\S]*?previousAcceptedRefreshAuthority\.newInboundMode !==[\s\S]*?setPrioritySemanticNewInboundHydrationRefreshEpoch/,
  "accepted provider generation or mode transitions must wake bounded hydration for zero-model activation and rollback",
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

const dismissalRequestStart = source.indexOf(
  "const dismissExactPrioritySemanticNewInboundObservation = useCallback",
);
const dismissalRequestEnd = source.indexOf(
  "const priorityReasonCopyForCandidates",
  dismissalRequestStart,
);
const dismissalRequestSource = source.slice(
  dismissalRequestStart,
  dismissalRequestEnd,
);
assert.ok(
  dismissalRequestStart >= 0 && dismissalRequestEnd > dismissalRequestStart,
  "the exact semantic Done/Remove dismissal bridge must exist",
);
assert.match(
  dismissalRequestSource,
  /requestPrioritySemanticNewInboundDismissal\(\{[\s\S]*?operation: "dismiss_new_inbound",[\s\S]*?mailboxId,[\s\S]*?conversationId: record\.identity\.conversationId,[\s\S]*?latestTurnId: record\.identity\.latestTurnId,[\s\S]*?semanticVersion: record\.identity\.semanticVersion/,
  "dismissal submits only the exact hydrated mailbox/conversation/latest-turn identity",
);
assert.doesNotMatch(
  dismissalRequestSource,
  /assessment:|confidence:|reasonCode:|priorityEffect:|subject:|body:|sender:|recipient:|localStorage|sessionStorage/,
  "dismissal cannot submit content or semantic policy and remains runtime-only",
);
assert.match(
  dismissalRequestSource,
  /didConnectionFenceChange =[\s\S]*?prioritySemanticNewInboundHydrationScopeRef[\s\S]*?providerArchiveCurrentConnectionKeysRef[\s\S]*?providerArchiveConnectionEpochsRef[\s\S]*?if \(didConnectionFenceChange\) \{[\s\S]*?return false;[\s\S]*?if \(!response\.ok\) \{[\s\S]*?setPrioritySemanticNewInboundDismissalFailure/,
  "reconnect races cancel silently while true server failures remain observable",
);
assert.match(
  dismissalRequestSource,
  /confirmedPrioritySemanticNewInboundDismissalKeysByMailboxRef[\s\S]*?rememberPrioritySemanticNewInboundDismissalFence\([\s\S]*?confirmedDismissalsByMailbox,[\s\S]*?mailboxId,[\s\S]*?runtimeDismissalKey[\s\S]*?setPrioritySemanticNewInboundHydrationState[\s\S]*?bucket\.records\.filter/,
  "each mailbox has its own bounded confirmation bucket and loses only the exact stale observation",
);
assert.match(
  source.slice(hydrationStateStart, dismissalRequestEnd),
  /nonDismissedRecords = records\.filter[\s\S]*?confirmedPrioritySemanticNewInboundDismissalKeysByMailboxRef[\s\S]*?\.get\(mailboxId\)/,
  "an older in-flight hydration response cannot re-add a server-confirmed dismissal",
);
assert.match(
  source,
  /confirmedPrioritySemanticNewInboundDismissalKeysByMailboxRef = useRef<[\s\S]*?Map<InboxId, Set<string>>[\s\S]*?new Map\(\)/,
  "two mailboxes retain independent confirmation fences instead of sharing one global 64-entry cap",
);

const priorityActionBridgeStart = source.indexOf(
  "const applyManualPriorityUpdate =",
);
const priorityActionBridgeEnd = source.indexOf(
  "const handleSetManualLabelOverride =",
  priorityActionBridgeStart,
);
const priorityActionBridgeSource = source.slice(
  priorityActionBridgeStart,
  priorityActionBridgeEnd,
);
const removalCoordinatorStart = source.indexOf(
  "export async function coordinatePrioritySemanticNewInboundRemoval",
);
const removalCoordinatorEnd = source.indexOf(
  "function isCanonicalPrioritySemanticNewInboundImapInteger",
  removalCoordinatorStart,
);
const removalCoordinatorSource = source.slice(
  removalCoordinatorStart,
  removalCoordinatorEnd,
);
const currentTargetResolverStart = source.indexOf(
  "function collapsePrioritySemanticNewInboundGmailFolderCopies",
);
const currentTargetResolverEnd = source.indexOf(
  "const sharedCollaborationMailboxId",
  currentTargetResolverStart,
);
const currentTargetResolverSource = source.slice(
  currentTargetResolverStart,
  currentTargetResolverEnd,
);
assert.ok(
  priorityActionBridgeStart >= 0 &&
    priorityActionBridgeEnd > priorityActionBridgeStart,
  "existing Priority Done/Remove actions must have a narrow semantic bridge",
);
assert.ok(
  removalCoordinatorStart >= 0 &&
    removalCoordinatorEnd > removalCoordinatorStart &&
    currentTargetResolverStart >= 0 &&
    currentTargetResolverEnd > currentTargetResolverStart,
  "the removal coordinator and exact current-Inbox resolver must exist",
);
assert.match(
  priorityActionBridgeSource,
  /!shouldBePriority[\s\S]*?!options\.skipSemanticNewInboundDismissal[\s\S]*?resolveExactPrioritySemanticNewInboundObservation/,
  "only explicit removal of an exact hydrated observation enters semantic dismissal",
);
assert.match(
  removalCoordinatorSource,
  /if \(!input\.observation\)[\s\S]*?input\.applyLocal\(null\)[\s\S]*?input\.resolveCurrentTarget\(input\.observation\)[\s\S]*?await input\.dismiss\(input\.observation\)[\s\S]*?currentTarget = input\.resolveCurrentTarget\(input\.observation\)[\s\S]*?input\.applyLocal\(currentTarget\)/,
  "the coordinator preserves unrelated local behavior but defers semantic local mutation until dismissal and a post-await identity recheck",
);
assert.match(
  priorityActionBridgeSource,
  /handleSetManualPriority = async[\s\S]*?coordinatePrioritySemanticNewInboundRemoval<MailMessage>[\s\S]*?observation: semanticObservation[\s\S]*?dismissExactPrioritySemanticNewInboundObservation[\s\S]*?resolveCurrentPrioritySemanticNewInboundDismissalTarget[\s\S]*?mailboxStoreRef\.current[\s\S]*?applyManualPriorityUpdate/,
  "Remove delegates exact dismissal and current-store application to the ordered coordinator",
);
assert.match(
  priorityActionBridgeSource,
    /manualPriorityOverridesRef\.current\s*=\s*immediateManualPriorityOverrides[\s\S]*?setManualPriorityOverrides\(\(current\) => \{[\s\S]*?manualPriorityOverridesRef\.current\s*=\s*next[\s\S]*?priorityClearedKeysRef\.current\s*=\s*immediateRestoredPriorityClearedKeys[\s\S]*?setPriorityClearedKeys\(\(current\) => \{[\s\S]*?priorityClearedKeysRef\.current\s*=\s*next[\s\S]*?applyMarkPriorityItemDone[\s\S]*?priorityClearedKeysRef\.current\s*=\s*immediateDonePriorityClearedKeys[\s\S]*?setPriorityClearedKeys\(\(current\) => \{[\s\S]*?priorityClearedKeysRef\.current\s*=\s*next/,
  "manual Remove, manual Priority restoration, and Done must publish their ref fences before deferred setState while retaining updater reconciliation",
);
assert.match(
  priorityActionBridgeSource,
  /handleMarkPriorityItemDone = async[\s\S]*?coordinatePrioritySemanticNewInboundRemoval<MailMessage>[\s\S]*?observation: semanticObservation[\s\S]*?dismissExactPrioritySemanticNewInboundObservation[\s\S]*?resolveCurrentPrioritySemanticNewInboundDismissalTarget[\s\S]*?applyMarkPriorityItemDone/,
  "Done delegates exact dismissal and current-store application to the ordered coordinator",
);
assert.match(
  dismissalRequestSource,
  /resolveCurrentPrioritySemanticNewInboundDismissalTarget[\s\S]*?prioritySemanticNewInboundHydrationScopeRef[\s\S]*?providerArchiveCurrentConnectionKeysRef[\s\S]*?providerArchiveConnectionEpochsRef[\s\S]*?mailboxStoreRef\.current[\s\S]*?conversationEntries[\s\S]*?resolvePrioritySemanticNewInboundCurrentInboxMessage/,
  "each pre/post-await check revalidates scope, account, and the current canonical conversation store",
);
assert.match(
  currentTargetResolverSource,
  /providerMessageId[\s\S]*?exactInboxCopies\.length === 1[\s\S]*?currentEntry\.folder !== "Inbox"[\s\S]*?isPrioritySemanticNewInboundDismissalTurnCurrent/,
  "same-message Gmail folder copies collapse only to one authoritative Inbox copy before the exact latest-turn fence",
);
assert.match(
  source,
  /composeMode === "forward"[\s\S]*?skipSemanticNewInboundDismissal: true/,
  "automatic post-send cleanup does not create a semantic dismissal tombstone",
);
assert.equal(
  source.match(/requestPrioritySemanticNewInboundDismissal\(/g)?.length,
  1,
  "Workspace has one exact user-action dismissal request site",
);
assert.match(
  source,
  /open=\{Boolean\(prioritySemanticNewInboundDismissalFailure\)\}[\s\S]*?Removal not saved[\s\S]*?\{prioritySemanticNewInboundDismissalFailure\}/,
  "durability failure is explicitly visible and retryable",
);
assert.match(
  dismissalRequestSource,
  /Could not save this Done\/Remove action across devices\. Nothing was removed\. Please try again\./,
  "server failure explains that no durable or local removal occurred",
);
assert.doesNotMatch(
  dismissalRequestSource,
  /setPrioritySemanticNewInboundDismissalFailure\(null\)/,
  "one successful dismissal cannot hide another concurrent dismissal failure",
);
assert.doesNotMatch(
  source.slice(
    source.indexOf(
      "open={Boolean(prioritySemanticNewInboundDismissalFailure)}",
    ),
    source.indexOf(
      "open={Boolean(manualChangeConfirmationMessage)}",
    ),
  ),
  /Change applied/,
  "dismissal failure is never presented as a successful local change",
);

function buildRuntimeGmailMessage(folder: "INBOX" | "ARCHIVE") {
  return {
    id: "message-new",
    serverMailboxId: "mailbox-1",
    providerFolder: folder,
    providerMessageId: "message-new",
    providerThreadId: "thread-new",
    threadId: "gmail:mailbox-1:thread-new",
    threadIdentityAuthority: "gmail",
    labelIds: folder === "INBOX" ? ["INBOX"] : [],
    threadIdentityContext: {
      mailboxId: "mailbox-1",
      provider: "google",
      folder,
    },
    sender: "Action Sender",
    subject: "Cuevion indexed action test",
    snippet: "Please complete this action.",
    time: "10:01",
    from: "sender@example.test",
    to: "owner@example.test",
    timestamp: "2026-08-22T10:01:00.000Z",
    body: ["Please complete this action."],
    unread: true,
    priorityScore: "medium",
    category: "Primary",
    categorySource: "system",
    categoryConfidence: "medium",
  };
}

function buildRuntimeMailboxStore(
  inboxMessage: ReturnType<typeof buildRuntimeGmailMessage>,
  archiveMessage: ReturnType<typeof buildRuntimeGmailMessage>,
) {
  return {
    "mailbox-1": {
      Trash: [],
      Spam: [],
      Filtered: [],
      Archive: [archiveMessage],
      Sent: [],
      Drafts: [],
      Inbox: [inboxMessage],
    },
  };
}

async function runActiveDirectAssessmentRuntimeRegression() {
  const inboxMessage = buildRuntimeGmailMessage("INBOX");
  const archiveMessage = buildRuntimeGmailMessage("ARCHIVE");
  const canonicalEntry = {
    mailboxId: "mailbox-1",
    mailboxTitle: "Mailbox 1",
    message: inboxMessage,
  };
  const activeResponse = {
    ok: true,
    status: "assessed",
    semanticTrigger: "new_inbound",
    newInboundMode: "active",
    priorityEffect: "promote_new_inbound",
    assessment: {
      state: "needs_user_action",
      confidence: 0.99,
      reasonCode: "explicit_request",
    },
    effectiveSemanticState: "needs_user_action",
    identity: {
      mailboxId: "mailbox-1",
      conversationId: "thread:mailbox-1|gmail:mailbox-1:thread-new",
      latestTurnId: "message-new",
      semanticVersion: "priority-semantic-state-v1",
    },
    assessedAt: "2026-08-22T10:02:00.000Z",
  } as const;
  type ActiveRecord = {
    assessment: {
      state: "needs_user_action";
      confidence: number;
      reasonCode: "explicit_request";
    };
    effectiveSemanticState: "needs_user_action";
    priorityEffect: "observe_only" | "promote_new_inbound";
    identity: typeof activeResponse.identity;
    assessedAt: string;
  };
  const coordinateAssessmentCommit =
    workspaceRuntime.coordinatePrioritySemanticNewInboundAssessmentCommit as (
      input: {
        response: Promise<unknown>;
        isCurrent: (record: ActiveRecord) => boolean;
        commit: (record: ActiveRecord) => void;
      },
    ) => Promise<boolean>;
  const mergeCanonicalEntries =
    workspaceRuntime.mergePrioritySemanticNewInboundPromotionsIntoCanonicalPriorityEntries as (
      broadEntries: typeof canonicalEntry[],
      candidateEntries: typeof canonicalEntry[],
      observations: Record<string, ActiveRecord>,
    ) => typeof canonicalEntry[];
  const isCurrentUserContainmentSatisfied =
    workspaceRuntime.isPrioritySemanticNewInboundCurrentUserContainmentSatisfied as (
      input: {
        manualPriorityOverride: unknown;
        isPriorityCleared: boolean;
        senderAddress: string;
        ownedAddressSet: ReadonlySet<string>;
      },
    ) => boolean;
  const resolveHydrationCommitPolicy =
    workspaceRuntime.resolvePrioritySemanticNewInboundHydrationCommitPolicy as (
      input: {
        newInboundMode: "off" | "shadow" | "active";
        requestMutationRevision: number;
        currentMutationRevision: number;
      },
    ) => "replace" | "preserve_and_rehydrate";
  const resolveExactObservation =
    workspaceRuntime.resolveExactPrioritySemanticNewInboundObservationForMessage as (
      observations: Record<string, ActiveRecord>,
      mailboxId: string,
      message: typeof inboxMessage,
    ) => ActiveRecord | null;
  const resolveCurrentInboxMessage =
    workspaceRuntime.resolvePrioritySemanticNewInboundCurrentInboxMessage as (
      input: {
        mailboxId: string;
        messageId: string;
        record: ActiveRecord;
        conversationEntries: Array<{
          folder: "Archive" | "Inbox";
          message: typeof inboxMessage;
        }>;
      },
    ) => typeof inboxMessage | null;
  const coordinateRemoval =
    workspaceRuntime.coordinatePrioritySemanticNewInboundRemoval as (input: {
      observation: ActiveRecord | null;
      dismiss: (record: ActiveRecord) => Promise<boolean>;
      resolveCurrentTarget: (record: ActiveRecord) => typeof inboxMessage | null;
      applyLocal: (target: typeof inboxMessage | null) => boolean;
    }) => Promise<boolean>;
  const buildIdentityKey =
    newInboundRuntime.buildPrioritySemanticNewInboundIdentityKey as (
      identity: typeof activeResponse.identity,
    ) => string;
  const buildDismissalWireRequest =
    newInboundRuntime.buildPrioritySemanticNewInboundDismissalWireRequest as (
      request: unknown,
    ) => unknown;

  const ownedAddressSet = new Set(["owner@example.test"]);
  const currentContainment = {
    manualPriorityOverride: null,
    isPriorityCleared: false,
    senderAddress: "sender@example.test",
    ownedAddressSet,
  };
  assert.equal(
    isCurrentUserContainmentSatisfied(currentContainment),
    true,
    "one current external Inbox turn remains eligible",
  );
  assert.equal(
    isCurrentUserContainmentSatisfied({
      ...currentContainment,
      manualPriorityOverride: "removed",
    }),
    false,
    "an explicit current removal must fence a late direct or hydrated promotion",
  );
  assert.equal(
    isCurrentUserContainmentSatisfied({
      ...currentContainment,
      isPriorityCleared: true,
    }),
    false,
    "Done/priority-cleared exact identity must fence a late direct or hydrated promotion",
  );
  assert.equal(
    isCurrentUserContainmentSatisfied({
      ...currentContainment,
      manualPriorityOverride: "priority",
    }),
    true,
    "independent manual Priority authority must remain eligible",
  );
  assert.equal(
    isCurrentUserContainmentSatisfied({
      ...currentContainment,
      senderAddress: "owner@example.test",
    }),
    false,
    "cross-device hydration and custom IMAP must not promote the user's own message",
  );
  assert.equal(
    resolveHydrationCommitPolicy({
      newInboundMode: "active",
      requestMutationRevision: 2,
      currentMutationRevision: 3,
    }),
    "preserve_and_rehydrate",
    "a slow ACTIVE snapshot must preserve a newer direct record and request fresh hydration",
  );
  assert.equal(
    resolveHydrationCommitPolicy({
      newInboundMode: "active",
      requestMutationRevision: 3,
      currentMutationRevision: 3,
    }),
    "replace",
  );
  for (const newInboundMode of ["shadow", "off"] as const) {
    assert.equal(
      resolveHydrationCommitPolicy({
        newInboundMode,
        requestMutationRevision: 2,
        currentMutationRevision: 3,
      }),
      "replace",
      `${newInboundMode} must replace immediately so rollout rollback demotes in-session`,
    );
  }

  let resolveDirectResponse: ((response: typeof activeResponse) => void) | null =
    null;
  const directResponse = new Promise<typeof activeResponse>((resolve) => {
    resolveDirectResponse = resolve;
  });
  const directlyCommittedRecords: ActiveRecord[] = [];
  let asyncCurrentnessChecks = 0;
  const directCommit = coordinateAssessmentCommit({
    response: directResponse,
    isCurrent: (record) => {
      asyncCurrentnessChecks += 1;
      return record.identity.latestTurnId === inboxMessage.providerMessageId;
    },
    commit: (record) => directlyCommittedRecords.push(record),
  });
  await Promise.resolve();
  assert.deepEqual(
    directlyCommittedRecords,
    [],
    "Priority cannot change before the direct assessment resolves",
  );
  resolveDirectResponse?.(activeResponse);
  assert.equal(await directCommit, true);
  assert.equal(asyncCurrentnessChecks, 1);
  assert.equal(
    directlyCommittedRecords.length,
    1,
    "the active direct result must enter the shared runtime record bucket without hydration",
  );
  const activeRecord = directlyCommittedRecords[0];
  const activeObservations = {
    [buildIdentityKey(activeRecord.identity)]: activeRecord,
  };
  assert.deepEqual(
    mergeCanonicalEntries([], [canonicalEntry], activeObservations),
    [canonicalEntry],
    "a current active needs_user_action result at or above threshold must promote immediately",
  );
  const readCanonicalEntry = {
    ...canonicalEntry,
    message: { ...inboxMessage, unread: false },
  };
  assert.deepEqual(
    mergeCanonicalEntries([], [readCanonicalEntry], activeObservations),
    [readCanonicalEntry],
    "read/unread presentation changes must not demote the same exact promoted turn",
  );
  assert.deepEqual(
    mergeCanonicalEntries([canonicalEntry], [canonicalEntry], {}),
    [canonicalEntry],
    "missing semantic observations must not erase independent broad/manual Priority authority",
  );
  assert.equal(
    mergeCanonicalEntries(
      [canonicalEntry],
      [canonicalEntry, canonicalEntry],
      activeObservations,
    ).length,
    1,
    "manual/deterministic plus semantic and hydration/direct overlap must count as one canonical row",
  );
  assert.deepEqual(
    mergeCanonicalEntries([], [canonicalEntry], {
      [buildIdentityKey(activeRecord.identity)]: {
        ...activeRecord,
        priorityEffect: "observe_only",
      },
    }),
    [],
    "shadow/observe-only records must never promote",
  );
  assert.deepEqual(
    mergeCanonicalEntries([], [canonicalEntry], {
      [buildIdentityKey(activeRecord.identity)]: {
        ...activeRecord,
        assessment: { ...activeRecord.assessment, confidence: 0.899 },
      },
    }),
    [],
    "even a forged promote effect below threshold must fail closed",
  );

  let staleCommits = 0;
  assert.equal(
    await coordinateAssessmentCommit({
      response: Promise.resolve(activeResponse),
      isCurrent: () => false,
      commit: () => {
        staleCommits += 1;
      },
    }),
    false,
    "a newer turn, reconnect, or authority-generation change must cancel a stale direct result",
  );
  assert.equal(staleCommits, 0);
  let explicitRemovalResurrections = 0;
  assert.equal(
    await coordinateAssessmentCommit({
      response: Promise.resolve(activeResponse),
      isCurrent: () =>
        isCurrentUserContainmentSatisfied({
          ...currentContainment,
          manualPriorityOverride: "removed",
        }),
      commit: () => {
        explicitRemovalResurrections += 1;
      },
    }),
    false,
    "an in-flight direct response must not resurrect an explicit current removal",
  );
  assert.equal(explicitRemovalResurrections, 0);

  const exactObservation = resolveExactObservation(
    activeObservations,
    "mailbox-1",
    inboxMessage,
  );
  assert.equal(exactObservation, activeRecord);
  const duplicateConversationEntries = [
    { folder: "Archive" as const, message: archiveMessage },
    { folder: "Inbox" as const, message: inboxMessage },
  ];
  const dismissalRequests: unknown[] = [];
  let localDoneMutations = 0;
  assert.equal(
    await coordinateRemoval({
      observation: exactObservation,
      dismiss: async (record) => {
        dismissalRequests.push(
          buildDismissalWireRequest({
            operation: "dismiss_new_inbound",
            mailboxId: record.identity.mailboxId,
            identity: {
              conversationId: record.identity.conversationId,
              latestTurnId: record.identity.latestTurnId,
              semanticVersion: record.identity.semanticVersion,
            },
          }),
        );
        return true;
      },
      resolveCurrentTarget: (record) =>
        resolveCurrentInboxMessage({
          mailboxId: "mailbox-1",
          messageId: inboxMessage.id,
          record,
          conversationEntries: duplicateConversationEntries,
        }),
      applyLocal: (target) => {
        assert.equal(target, inboxMessage);
        localDoneMutations += 1;
        return true;
      },
    }),
    true,
    "Done must durably dismiss a directly assessed active row before any hydration refresh",
  );
  assert.deepEqual(dismissalRequests, [
    {
      operation: "dismiss_new_inbound",
      mailboxId: "mailbox-1",
      identity: {
        conversationId: "thread:mailbox-1|gmail:mailbox-1:thread-new",
        latestTurnId: "message-new",
        semanticVersion: "priority-semantic-state-v1",
      },
    },
  ]);
  assert.equal(localDoneMutations, 1);
}

async function runManualPriorityDismissalRuntimeRegression() {
  const inboxMessage = buildRuntimeGmailMessage("INBOX");
  const archiveMessage = buildRuntimeGmailMessage("ARCHIVE");
  const mailboxStore = buildRuntimeMailboxStore(inboxMessage, archiveMessage);
  const record = {
    assessment: {
      state: "needs_user_action",
      confidence: 0.99,
      reasonCode: "explicit_request",
    },
    effectiveSemanticState: "needs_user_action",
    priorityEffect: "observe_only",
    identity: {
      mailboxId: "mailbox-1",
      conversationId: "thread:mailbox-1|gmail:mailbox-1:thread-new",
      latestTurnId: "message-new",
      semanticVersion: "priority-semantic-state-v1",
    },
    assessedAt: "2026-08-22T10:02:00.000Z",
  };

  const getMailboxMessageById = workspaceRuntime.getMailboxMessageById as (
    store: typeof mailboxStore,
    mailboxId: string,
    messageId: string,
  ) => typeof inboxMessage | null;
  const resolveManualTarget =
    workspaceRuntime.resolveMailboxScopedManualPriorityTarget as (input: {
      store: typeof mailboxStore;
      messageId: string;
      storageMailboxId: string;
      sourceMailboxId: string;
      sourceMessage: typeof inboxMessage;
    }) => { message: typeof inboxMessage } | null;

  assert.equal(
    getMailboxMessageById(mailboxStore, "mailbox-1", "message-new"),
    archiveMessage,
    "the regression must preserve the Production bare-ID Archive-before-Inbox collision",
  );
  assert.equal(
    resolveManualTarget({
      store: mailboxStore,
      messageId: "message-new",
      storageMailboxId: "mailbox-1",
      sourceMailboxId: "mailbox-1",
      sourceMessage: inboxMessage,
    })?.message,
    inboxMessage,
    "manual Mark as Priority must retain the exact supplied Inbox source instead of replacing it with a same-ID Archive copy",
  );
  assert.equal(
    resolveManualTarget({
      store: mailboxStore,
      messageId: "message-new",
      storageMailboxId: "mailbox-1",
      sourceMailboxId: "mailbox-1",
      sourceMessage: { ...inboxMessage },
    }),
    null,
    "a detached source object must fail closed instead of falling back to a different same-ID folder copy",
  );

  const reviewItem = {
    id: "live-priority-mailbox-1-message-new",
    sourceId: "message-new",
    linkedEntityIds: ["mailbox:mailbox-1"],
  };
  const canonicalEntries = [
    {
      mailboxId: "mailbox-1",
      mailboxTitle: "Mailbox 1",
      message: inboxMessage,
    },
  ];
  const resolveCanonicalActionTarget =
    workspaceRuntime.resolvePrioritySemanticNewInboundCanonicalActionTarget as (
      entries: typeof canonicalEntries,
      item: typeof reviewItem,
    ) => (typeof canonicalEntries)[number] | null;
  const resolveExactObservation =
    workspaceRuntime.resolveExactPrioritySemanticNewInboundObservationForMessage as (
      observations: Record<string, typeof record>,
      mailboxId: string,
      message: typeof inboxMessage,
    ) => typeof record | null;
  const coordinateRemoval =
    workspaceRuntime.coordinatePrioritySemanticNewInboundRemoval as (input: {
      observation: typeof record | null;
      dismiss: (observation: typeof record) => Promise<boolean>;
      resolveCurrentTarget: (
        observation: typeof record,
      ) => typeof inboxMessage | null;
      applyLocal: (target: typeof inboxMessage | null) => boolean;
    }) => Promise<boolean>;
  const resolveCurrentInboxMessage =
    workspaceRuntime.resolvePrioritySemanticNewInboundCurrentInboxMessage as (
      input: {
        mailboxId: string;
        messageId: string;
        record: typeof record;
        conversationEntries: Array<{
          folder: "Archive" | "Inbox";
          message: typeof inboxMessage;
        }>;
      },
    ) => typeof inboxMessage | null;
  const buildIdentityKey =
    newInboundRuntime.buildPrioritySemanticNewInboundIdentityKey as (
      identity: typeof record.identity,
    ) => string;
  const buildDismissalWireRequest =
    newInboundRuntime.buildPrioritySemanticNewInboundDismissalWireRequest as (
      request: unknown,
    ) => unknown;
  const hydratedObservations = {
    [buildIdentityKey(record.identity)]: record,
  };
  const initiallyAcceptedObservation = resolveExactObservation(
    hydratedObservations,
    "mailbox-1",
    inboxMessage,
  );
  assert.equal(initiallyAcceptedObservation, record);
  const duplicateConversationEntries = [
    { folder: "Archive" as const, message: archiveMessage },
    { folder: "Inbox" as const, message: inboxMessage },
  ];
  const projectedInboxMessage = resolveCurrentInboxMessage({
    mailboxId: "mailbox-1",
    messageId: "message-new",
    record,
    conversationEntries: duplicateConversationEntries,
  });
  assert.equal(
    projectedInboxMessage,
    inboxMessage,
    "an equal-timestamp Gmail Archive copy must not displace the one authoritative Inbox copy",
  );
  assert.equal(
    projectedInboxMessage
      ? resolveExactObservation(
          hydratedObservations,
          "mailbox-1",
          projectedInboxMessage,
        )
      : null,
    record,
    "a collision present before manual Priority must retain the exact hydrated observation",
  );

  for (const action of ["mark_done", "remove_priority"] as const) {
    let manualPriority: "priority" | "removed" = "priority";
    let isDone = false;
    let localMutationCount = 0;
    let hydratedRecords = [record];
    const dismissalRequests: unknown[] = [];
    let confirmDismissal: (() => void) | null = null;
    const dismissalGate = new Promise<void>((resolve) => {
      confirmDismissal = resolve;
    });

    const canonicalTarget = resolveCanonicalActionTarget(
      canonicalEntries,
      reviewItem,
    );
    assert.equal(
      canonicalTarget?.message,
      inboxMessage,
      `${action} must recover the exact canonical Inbox object, not the bare-ID Archive match`,
    );
    const observation = canonicalTarget
      ? resolveExactObservation(
          hydratedObservations,
          canonicalTarget.mailboxId,
          canonicalTarget.message,
        )
      : null;
    assert.equal(
      observation,
      record,
      `${action} must retain the exact observation across a pre-existing or later Archive readback`,
    );

    const actionPromise = coordinateRemoval({
      observation,
      dismiss: async (currentRecord) => {
        dismissalRequests.push(
          buildDismissalWireRequest({
            operation: "dismiss_new_inbound",
            mailboxId: currentRecord.identity.mailboxId,
            identity: {
              conversationId: currentRecord.identity.conversationId,
              latestTurnId: currentRecord.identity.latestTurnId,
              semanticVersion: currentRecord.identity.semanticVersion,
            },
          }),
        );
        await dismissalGate;
        hydratedRecords = [];
        return true;
      },
      resolveCurrentTarget: (currentRecord) =>
        resolveCurrentInboxMessage({
          mailboxId: "mailbox-1",
          messageId: "message-new",
          record: currentRecord,
          conversationEntries: duplicateConversationEntries,
        }),
      applyLocal: (target) => {
        assert.equal(target, inboxMessage);
        localMutationCount += 1;
        if (action === "mark_done") {
          isDone = true;
        } else {
          manualPriority = "removed";
        }
        return true;
      },
    });

    await Promise.resolve();
    assert.deepEqual(dismissalRequests, [
      {
        operation: "dismiss_new_inbound",
        mailboxId: "mailbox-1",
        identity: {
          conversationId: "thread:mailbox-1|gmail:mailbox-1:thread-new",
          latestTurnId: "message-new",
          semanticVersion: "priority-semantic-state-v1",
        },
      },
    ]);
    assert.equal(
      localMutationCount,
      0,
      `${action} must not remove locally before durable dismissal confirmation`,
    );
    assert.equal(
      manualPriority === "priority" && !isDone,
      true,
      `${action} must leave the canonical Priority row visible while dismissal is pending`,
    );

    confirmDismissal?.();
    assert.equal(await actionPromise, true);
    assert.equal(localMutationCount, 1);
    assert.equal(
      manualPriority === "priority" && !isDone,
      false,
      `${action} must remove the canonical row only after dismissal success`,
    );
    assert.deepEqual(
      hydratedRecords,
      [],
      `${action} dismissal must make the next hydration omit the exact turn`,
    );
  }

  let failedDismissalRequests = 0;
  let failedLocalMutations = 0;
  assert.equal(
    await coordinateRemoval({
      observation: record,
      dismiss: async () => {
        failedDismissalRequests += 1;
        return false;
      },
      resolveCurrentTarget: (currentRecord) =>
        resolveCurrentInboxMessage({
          mailboxId: "mailbox-1",
          messageId: "message-new",
          record: currentRecord,
          conversationEntries: duplicateConversationEntries,
        }),
      applyLocal: () => {
        failedLocalMutations += 1;
        return true;
      },
    }),
    false,
    "a failed server dismissal must report that the action was not applied",
  );
  assert.equal(failedDismissalRequests, 1);
  assert.equal(
    failedLocalMutations,
    0,
    "server failure must leave the Priority row locally visible and retryable",
  );

  let unrelatedDismissalRequests = 0;
  let unrelatedLocalMutations = 0;
  assert.equal(
    await coordinateRemoval({
      observation: null,
      dismiss: async () => {
        unrelatedDismissalRequests += 1;
        return true;
      },
      resolveCurrentTarget: () => inboxMessage,
      applyLocal: (target) => {
        assert.equal(target, null);
        unrelatedLocalMutations += 1;
        return true;
      },
    }),
    true,
    "a non-semantic Priority row must preserve its existing local action",
  );
  assert.equal(
    unrelatedDismissalRequests,
    0,
    "unrelated rows must not create semantic dismissal persistence",
  );
  assert.equal(unrelatedLocalMutations, 1);

  const newerInboxMessage = {
    ...inboxMessage,
    id: "message-newer",
    providerMessageId: "message-newer",
    time: "10:03",
    timestamp: "2026-08-22T10:03:00.000Z",
  };
  let currentConversationEntries = duplicateConversationEntries;
  let newerTurnDismissalRequests = 0;
  let newerTurnLocalMutations = 0;
  assert.equal(
    await coordinateRemoval({
      observation: record,
      dismiss: async () => {
        newerTurnDismissalRequests += 1;
        currentConversationEntries = [
          ...duplicateConversationEntries,
          { folder: "Inbox" as const, message: newerInboxMessage },
        ];
        return true;
      },
      resolveCurrentTarget: (currentRecord) =>
        resolveCurrentInboxMessage({
          mailboxId: "mailbox-1",
          messageId: "message-new",
          record: currentRecord,
          conversationEntries: currentConversationEntries,
        }),
      applyLocal: () => {
        newerTurnLocalMutations += 1;
        return true;
      },
    }),
    false,
    "a newer turn arriving during dismissal must fail the post-await exact-turn fence",
  );
  assert.equal(newerTurnDismissalRequests, 1);
  assert.equal(
    newerTurnLocalMutations,
    0,
    "the old turn's dismissal must not remove the newer Priority row locally",
  );
}

runActiveDirectAssessmentRuntimeRegression()
  .then(() => runManualPriorityDismissalRuntimeRegression())
  .then(() =>
    console.log(
      "\nWorkspaceShell new_inbound active integration and runtime tests passed.",
    ),
  )
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
