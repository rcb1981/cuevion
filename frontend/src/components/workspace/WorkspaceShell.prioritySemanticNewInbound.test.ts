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
  3,
  "hydrated records are held only as an isolated shadow projection and its exact-action resolver",
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

runManualPriorityDismissalRuntimeRegression()
  .then(() =>
    console.log(
      "\nWorkspaceShell new_inbound shadow integration and runtime tests passed.",
    ),
  )
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
