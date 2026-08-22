import assert from "node:assert/strict";
import {
  PRIORITY_SEMANTIC_NEW_INBOUND_MAX_GMAIL_IDS,
  PRIORITY_SEMANTIC_NEW_INBOUND_MAX_HYDRATION_RECORDS,
  buildPrioritySemanticNewInboundDismissalWireRequest,
  buildPrioritySemanticNewInboundHydrationWireRequest,
  buildPrioritySemanticNewInboundIdentityKey,
  buildPrioritySemanticNewInboundLocatorKey,
  buildPrioritySemanticNewInboundStorageKey,
  buildPrioritySemanticNewInboundWireRequest,
  isPrioritySemanticNewInboundEligible,
  isPrioritySemanticNewInboundDismissalTurnCurrent,
  isPrioritySemanticNewInboundHydratedObservationCurrent,
  meetsPrioritySemanticNewInboundPromotionThreshold,
  normalizePrioritySemanticNewInboundMode,
  observePrioritySemanticNewInboundSnapshot,
  parsePrioritySemanticNewInboundDismissalResponse,
  parsePrioritySemanticNewInboundResponse,
  parsePrioritySemanticNewInboundHydrationResponse,
  rememberPrioritySemanticNewInboundDismissalFence,
  type PrioritySemanticNewInboundStorage,
} from "./prioritySemanticNewInbound";
import { SEMANTIC_SCHEMA_VERSION } from "./prioritySemanticState";

class MemoryStorage implements PrioritySemanticNewInboundStorage {
  readonly values = new Map<string, string>();
  failReads = false;
  failWrites = false;

  getItem(key: string) {
    if (this.failReads) {
      throw new Error("private storage detail");
    }
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    if (this.failWrites) {
      throw new Error("private storage detail");
    }
    this.values.set(key, value);
  }
}

const baseKey = buildPrioritySemanticNewInboundStorageKey(
  "workspace/one",
  "mailbox order:primary",
);

function gmailMessages(...providerMessageIds: string[]) {
  return providerMessageIds.map((providerMessageId) => ({
    providerMessageId,
  }));
}

function imapMessage(imapUid: string, uidValidity = "7") {
  return {
    providerFolder: "INBOX",
    uidValidity,
    imapUid,
  };
}

function observeGoogle(
  storage: MemoryStorage,
  providerMessageIds: string[],
  options?: {
    mailboxId?: string;
    connectionScope?: string;
    mode?: unknown;
    storageKey?: string;
  },
) {
  return observePrioritySemanticNewInboundSnapshot({
    storage,
    storageKey: options?.storageKey ?? baseKey,
    mailboxId: options?.mailboxId ?? "mailbox-1",
    connectionScope: options?.connectionScope ?? "google:account-one",
    provider: "google",
    mode: options?.mode,
    messages: gmailMessages(...providerMessageIds),
  });
}

function observeImap(
  storage: MemoryStorage,
  inboxUidSet: string[] | null,
  messages: ReturnType<typeof imapMessage>[],
  options?: {
    mailboxId?: string;
    connectionScope?: string;
    mode?: unknown;
    uidValidity?: string;
    storageKey?: string;
  },
) {
  return observePrioritySemanticNewInboundSnapshot({
    storage,
    storageKey: options?.storageKey ?? baseKey,
    mailboxId: options?.mailboxId ?? "mailbox-imap",
    connectionScope: options?.connectionScope ?? "imap:account-one",
    provider: "custom_imap",
    mode: options?.mode,
    uidValidity: options?.uidValidity ?? "7",
    inboxUidSet,
    messages,
  });
}

function runBoundaryTests() {
  assert.equal(normalizePrioritySemanticNewInboundMode("shadow"), "shadow");
  assert.equal(normalizePrioritySemanticNewInboundMode(undefined), "off");
  assert.equal(normalizePrioritySemanticNewInboundMode("active"), "off");
  assert.equal(normalizePrioritySemanticNewInboundMode("unknown"), "off");
  assert.equal(
    baseKey,
    "cuevion.priority-semantic.new-inbound-boundary.v1:workspace%2Fone:mailbox%20order%3Aprimary",
  );

  const storage = new MemoryStorage();
  assert.deepEqual(
    observeGoogle(storage, ["gmail-2", "gmail-1"], { mode: "shadow" }),
    [],
    "first load seeds the provider boundary without historical calls",
  );
  assert.deepEqual(
    observeGoogle(storage, ["gmail-2", "gmail-1"], { mode: "shadow" }),
    [],
    "ordinary refreshes do not rediscover known provider IDs",
  );
  assert.deepEqual(
    observeGoogle(storage, ["gmail-3", "gmail-2", "gmail-1"], {
      mode: "shadow",
    }),
    [
      {
        mailboxId: "mailbox-1",
        incomingLocator: {
          provider: "google",
          providerMessageId: "gmail-3",
        },
      },
    ],
    "a newly observed authoritative Gmail identity is returned exactly once",
  );
  assert.deepEqual(
    observeGoogle(storage, ["gmail-3", "gmail-2", "gmail-1"], {
      mode: "shadow",
    }),
    [],
  );

  assert.deepEqual(
    observeGoogle(storage, ["gmail-4", "gmail-3"], { mode: "active" }),
    [],
    "unknown/active modes are fail-closed off but still advance the boundary",
  );
  assert.deepEqual(
    observeGoogle(storage, ["gmail-4", "gmail-3"], { mode: "shadow" }),
    [],
    "off to shadow never backfills provider identities seen while off",
  );
  assert.equal(
    observeGoogle(storage, ["gmail-5", "gmail-4"], { mode: "shadow" })
      .length,
    1,
  );

  const persisted = [...storage.values.values()].join("\n");
  assert.doesNotMatch(persisted, /account-one/);
  assert.doesNotMatch(persisted, /subject|body|snippet|semantic|confidence/i);

  assert.deepEqual(
    observeGoogle(storage, ["gmail-6", "gmail-5"], {
      mode: "shadow",
      connectionScope: "google:reconnected-account",
    }),
    [],
    "a reconnected provider account reseeds without backfill",
  );

  const corruptStorage = new MemoryStorage();
  corruptStorage.values.set(
    `${baseKey}:mailbox:${encodeURIComponent("mailbox-1")}`,
    "{corrupt",
  );
  assert.deepEqual(
    observeGoogle(corruptStorage, ["gmail-existing"], { mode: "shadow" }),
    [],
    "a corrupt marker is replaced by a no-backfill seed",
  );

  const failedStorage = new MemoryStorage();
  observeGoogle(failedStorage, ["gmail-1"], { mode: "shadow" });
  failedStorage.failWrites = true;
  assert.deepEqual(
    observeGoogle(failedStorage, ["gmail-2", "gmail-1"], { mode: "shadow" }),
    [],
    "candidates are never released before their durable boundary persists",
  );
  failedStorage.failWrites = false;
  assert.equal(
    observeGoogle(failedStorage, ["gmail-2", "gmail-1"], { mode: "shadow" })
      .length,
    1,
  );
  failedStorage.failReads = true;
  assert.deepEqual(
    observeGoogle(failedStorage, ["gmail-3", "gmail-2"], { mode: "shadow" }),
    [],
    "unavailable storage fails closed",
  );

  const boundedStorage = new MemoryStorage();
  const oversizedPage = Array.from(
    { length: PRIORITY_SEMANTIC_NEW_INBOUND_MAX_GMAIL_IDS + 30 },
    (_, index) => `gmail-${index}`,
  );
  observeGoogle(boundedStorage, oversizedPage, { mode: "shadow" });
  const boundedRecord = JSON.parse([...boundedStorage.values.values()][0]) as {
    providerMessageIds: string[];
  };
  assert.equal(
    boundedRecord.providerMessageIds.length,
    PRIORITY_SEMANTIC_NEW_INBOUND_MAX_GMAIL_IDS,
  );

  const sharedBaseStorage = new MemoryStorage();
  assert.deepEqual(
    observeGoogle(sharedBaseStorage, ["a-1"], {
      mailboxId: "mailbox-a",
      mode: "shadow",
    }),
    [],
  );
  assert.deepEqual(
    observeGoogle(sharedBaseStorage, ["b-1"], {
      mailboxId: "mailbox-b",
      mode: "shadow",
    }),
    [],
  );
  assert.equal(
    observeGoogle(sharedBaseStorage, ["a-2", "a-1"], {
      mailboxId: "mailbox-a",
      mode: "shadow",
    })[0]?.incomingLocator.provider,
    "google",
    "mailboxes sharing a workspace base key retain isolated continuity",
  );
  assert.equal(sharedBaseStorage.values.size, 2);

  const imapStorage = new MemoryStorage();
  const initialUidSet = Array.from({ length: 10 }, (_, index) =>
    String(index + 1),
  );
  assert.deepEqual(
    observeImap(imapStorage, initialUidSet, [imapMessage("10")], {
      mode: "shadow",
    }),
    [],
    "the full authoritative UID set seeds high-water beyond a partial page",
  );
  assert.deepEqual(
    observeImap(imapStorage, [...initialUidSet, "11"], [imapMessage("11")], {
      mode: "shadow",
    }),
    [
      {
        mailboxId: "mailbox-imap",
        incomingLocator: {
          provider: "custom_imap",
          providerFolder: "INBOX",
          uidValidity: "7",
          imapUid: "11",
        },
      },
    ],
  );
  assert.deepEqual(
    observeImap(imapStorage, [...initialUidSet, "11"], [imapMessage("11")], {
      mode: "shadow",
    }),
    [],
  );
  assert.deepEqual(
    observeImap(
      imapStorage,
      [...initialUidSet, "11", "12"],
      [{ ...imapMessage("12"), providerFolder: "Archive" }],
      { mode: "shadow" },
    ),
    [],
    "a non-Inbox message cannot become a candidate",
  );
  assert.deepEqual(
    observeImap(
      imapStorage,
      [...initialUidSet, "11", "12"],
      [imapMessage("12")],
      { mode: "shadow" },
    ),
    [],
    "fail-closed messages do not backfill after high-water advances",
  );
  assert.deepEqual(
    observeImap(imapStorage, [...initialUidSet, "11", "12", "13"], [
      imapMessage("13"),
    ]),
    [],
    "off still advances IMAP high-water",
  );
  assert.deepEqual(
    observeImap(
      imapStorage,
      [...initialUidSet, "11", "12", "13"],
      [imapMessage("13")],
      { mode: "shadow" },
    ),
    [],
  );
  assert.deepEqual(
    observeImap(imapStorage, ["1"], [imapMessage("1", "8")], {
      mode: "shadow",
      uidValidity: "8",
    }),
    [],
    "UIDVALIDITY changes reseed without historical calls",
  );
  assert.deepEqual(
    observeImap(imapStorage, [], [], {
      mode: "shadow",
      uidValidity: "8",
    }),
    [],
  );
  assert.deepEqual(
    observeImap(imapStorage, ["1"], [imapMessage("1", "8")], {
      mode: "shadow",
      uidValidity: "8",
    }),
    [],
    "high-water never moves backward when the current max UID disappears",
  );
  assert.equal(
    observeImap(imapStorage, ["1", "2"], [imapMessage("2", "8")], {
      mode: "shadow",
      uidValidity: "8",
    }).length,
    1,
  );

  const providerSwitchStorage = new MemoryStorage();
  observeGoogle(providerSwitchStorage, ["gmail-1"], { mode: "shadow" });
  assert.deepEqual(
    observeImap(providerSwitchStorage, ["50"], [imapMessage("50")], {
      mailboxId: "mailbox-1",
      connectionScope: "imap:account-one",
      mode: "shadow",
    }),
    [],
    "provider changes reseed even when the mailbox and base key are unchanged",
  );
}

function runEligibilityAndWireTests() {
  const eligible = {
    isAuthoritativeInbox: true,
    isExternal: true,
    isLowOrFiltered: false,
    isSpamTrashOrArchiveOnly: false,
    isNoise: false,
    isOrganizerExcluded: false,
    hasActiveOpenLoop: false,
    hasDeterministicPriority: false,
    isDuplicateOrOwnMessage: false,
  };
  assert.equal(isPrioritySemanticNewInboundEligible(eligible), true);
  for (const key of Object.keys(eligible) as Array<keyof typeof eligible>) {
    assert.equal(
      isPrioritySemanticNewInboundEligible({
        ...eligible,
        [key]: !eligible[key],
      }),
      false,
      `${key} is a hard candidate gate`,
    );
  }

  assert.equal(
    meetsPrioritySemanticNewInboundPromotionThreshold({
      state: "needs_user_action",
      confidence: 0.899,
    }),
    false,
  );
  assert.equal(
    meetsPrioritySemanticNewInboundPromotionThreshold({
      state: "needs_user_action",
      confidence: 0.9,
    }),
    true,
  );
  assert.equal(
    meetsPrioritySemanticNewInboundPromotionThreshold({
      state: "informational",
      confidence: 1,
    }),
    false,
  );
  assert.equal(
    meetsPrioritySemanticNewInboundPromotionThreshold({
      state: "needs_user_action",
      confidence: 1.01,
    }),
    false,
  );

  const googleRequest = {
    mailboxId: "mailbox-1",
    trigger: "new_inbound",
    incomingLocator: {
      provider: "google",
      providerMessageId: "gmail-3",
    },
  } as const;
  assert.deepEqual(
    buildPrioritySemanticNewInboundWireRequest(googleRequest),
    googleRequest,
  );
  assert.equal(
    buildPrioritySemanticNewInboundWireRequest({
      ...googleRequest,
      subject: "must never cross the wire boundary",
    }),
    null,
  );
  assert.equal(
    buildPrioritySemanticNewInboundWireRequest({
      mailboxId: "mailbox-1",
      trigger: "new_inbound",
      incomingLocator: {
        provider: "custom_imap",
        providerFolder: "Archive",
        uidValidity: "7",
        imapUid: "11",
      },
    }),
    null,
  );
  assert.deepEqual(
    buildPrioritySemanticNewInboundWireRequest({
      mailboxId: "mailbox-1",
      trigger: "new_inbound",
      incomingLocator: {
        provider: "custom_imap",
        providerFolder: "INBOX",
        uidValidity: "7",
        imapUid: "11",
      },
    }),
    {
      mailboxId: "mailbox-1",
      trigger: "new_inbound",
      incomingLocator: {
        provider: "custom_imap",
        providerFolder: "INBOX",
        uidValidity: "7",
        imapUid: "11",
      },
    },
  );
  assert.notEqual(
    buildPrioritySemanticNewInboundLocatorKey(googleRequest.incomingLocator),
    buildPrioritySemanticNewInboundLocatorKey({
      provider: "custom_imap",
      providerFolder: "INBOX",
      uidValidity: "7",
      imapUid: "11",
    }),
  );
}

function runResponseParserTests() {
  const identity = {
    mailboxId: "mailbox-1",
    conversationId: "thread:mailbox-1|gmail:thread-1",
    latestTurnId: "gmail-3",
    semanticVersion: SEMANTIC_SCHEMA_VERSION,
  } as const;
  const assessed = {
    ok: true,
    status: "assessed",
    semanticTrigger: "new_inbound",
    newInboundMode: "shadow",
    priorityEffect: "observe_only",
    assessment: {
      state: "needs_user_action",
      confidence: 0.94,
      reasonCode: "explicit_request",
    },
    effectiveSemanticState: "needs_user_action",
    identity,
    assessedAt: "2026-08-22T08:30:00.000Z",
  } as const;
  assert.deepEqual(parsePrioritySemanticNewInboundResponse(assessed), assessed);
  assert.equal(
    parsePrioritySemanticNewInboundResponse({
      ...assessed,
      semanticMode: "active",
    }),
    null,
    "legacy/open-loop policy fields are rejected on the separate response path",
  );
  assert.equal(
    parsePrioritySemanticNewInboundResponse({
      ...assessed,
      priorityEffect: "suppress_automatic_open_loop",
    }),
    null,
    "new inbound is always observe-only",
  );
  assert.equal(
    parsePrioritySemanticNewInboundResponse({
      ...assessed,
      assessment: {
        state: "informational",
        confidence: 0.99,
        reasonCode: "explicit_request",
      },
    }),
    null,
    "state/reason pairs remain strict",
  );
  assert.equal(
    parsePrioritySemanticNewInboundResponse({
      ...assessed,
      effectiveSemanticState: "uncertain",
    }),
    null,
    "assessed responses cannot forge an effective semantic state",
  );

  const disabled = {
    ok: true,
    status: "deferred",
    semanticTrigger: "new_inbound",
    newInboundMode: "off",
    priorityEffect: "observe_only",
    identity,
    retryAfterSeconds: 300,
  } as const;
  assert.deepEqual(parsePrioritySemanticNewInboundResponse(disabled), disabled);
  assert.equal(
    parsePrioritySemanticNewInboundResponse({
      ...disabled,
      status: "pending",
    }),
    null,
  );
  assert.deepEqual(
    parsePrioritySemanticNewInboundResponse({
      ok: false,
      error: { code: "not_authorized", message: "Not authorized." },
    }),
    {
      ok: false,
      error: { code: "not_authorized", message: "Not authorized." },
    },
  );
}

function runHydrationTests() {
  const request = {
    operation: "hydrate_new_inbound",
    mailboxId: "mailbox-1",
  } as const;
  assert.deepEqual(
    buildPrioritySemanticNewInboundHydrationWireRequest(request),
    request,
  );
  assert.equal(
    buildPrioritySemanticNewInboundHydrationWireRequest({
      ...request,
      state: "needs_user_action",
    }),
    null,
    "the browser cannot submit semantic policy fields during hydration",
  );
  assert.equal(
    buildPrioritySemanticNewInboundHydrationWireRequest({
      operation: "hydrate_new_inbound",
      mailboxId: " mailbox-1 ",
    }),
    null,
  );

  const record = {
    assessment: {
      state: "needs_user_action",
      confidence: 1,
      reasonCode: "explicit_request",
    },
    effectiveSemanticState: "needs_user_action",
    priorityEffect: "observe_only",
    identity: {
      mailboxId: "mailbox-1",
      conversationId: "thread:mailbox-1|gmail:mailbox-1:thread-new",
      latestTurnId: "gmail-new",
      semanticVersion: SEMANTIC_SCHEMA_VERSION,
    },
    assessedAt: "2026-08-22T10:00:00.000Z",
  } as const;
  const hydrated = {
    ok: true,
    status: "hydrated",
    semanticTrigger: "new_inbound",
    newInboundMode: "shadow",
    priorityEffect: "observe_only",
    records: [record],
  } as const;
  assert.deepEqual(
    parsePrioritySemanticNewInboundHydrationResponse(hydrated),
    hydrated,
  );
  assert.deepEqual(
    parsePrioritySemanticNewInboundHydrationResponse({
      ...hydrated,
      newInboundMode: "off",
      records: [],
    }),
    { ...hydrated, newInboundMode: "off", records: [] },
  );
  assert.equal(
    parsePrioritySemanticNewInboundHydrationResponse({
      ...hydrated,
      newInboundMode: "active",
    }),
    null,
    "Prep 1 remains shadow-only and active fails closed",
  );
  assert.equal(
    parsePrioritySemanticNewInboundHydrationResponse({
      ...hydrated,
      priorityEffect: "promote_new_inbound",
    }),
    null,
  );
  assert.equal(
    parsePrioritySemanticNewInboundHydrationResponse({
      ...hydrated,
      records: [{ ...record, priorityEffect: "promote_new_inbound" }],
    }),
    null,
    "even needs_user_action at 1.00 remains observe-only",
  );
  assert.equal(
    parsePrioritySemanticNewInboundHydrationResponse({
      ...hydrated,
      records: [
        {
          ...record,
          effectiveSemanticState: "uncertain",
        },
      ],
    }),
    null,
    "the effective state must match the strict local confidence projection",
  );
  assert.equal(
    parsePrioritySemanticNewInboundHydrationResponse({
      ...hydrated,
      records: Array.from(
        { length: PRIORITY_SEMANTIC_NEW_INBOUND_MAX_HYDRATION_RECORDS + 1 },
        () => record,
      ),
    }),
    null,
    "hydration is bounded to 64 records per mailbox",
  );
  assert.equal(
    parsePrioritySemanticNewInboundHydrationResponse({
      ...hydrated,
      records: [record, { ...record, assessedAt: "2026-08-22T10:01:00.000Z" }],
    }),
    null,
    "duplicate current records for one mailbox conversation fail closed",
  );
  assert.equal(
    parsePrioritySemanticNewInboundHydrationResponse({
      ...hydrated,
      records: [
        {
          ...record,
          identity: {
            ...record.identity,
            semanticVersion: "priority-semantic-v0",
          },
        },
      ],
    }),
    null,
    "incompatible semantic versions are discarded",
  );

  const liveIdentity = {
    ...record.identity,
    isExactCurrentAuthoritativeInboxRow: true,
    isLowOrFiltered: false,
    isSpamTrashOrArchiveOnly: false,
    isNoise: false,
    isOrganizerExcluded: false,
  };
  assert.equal(
    isPrioritySemanticNewInboundHydratedObservationCurrent({
      record,
      liveIdentity,
    }),
    true,
  );
  for (const mismatch of [
    { mailboxId: "mailbox-2" },
    { conversationId: `${record.identity.conversationId}:newer` },
    { latestTurnId: "gmail-newer" },
    { isExactCurrentAuthoritativeInboxRow: false },
    { isLowOrFiltered: true },
    { isSpamTrashOrArchiveOnly: true },
    { isNoise: true },
    { isOrganizerExcluded: true },
  ]) {
    assert.equal(
      isPrioritySemanticNewInboundHydratedObservationCurrent({
        record,
        liveIdentity: { ...liveIdentity, ...mismatch },
      }),
      false,
      `${Object.keys(mismatch)[0]} mismatch must discard hydration`,
    );
  }
}

function runDismissalTests() {
  const identity = {
    mailboxId: "mailbox-1",
    conversationId: "thread:mailbox-1|gmail:thread-new",
    latestTurnId: "message-new",
    semanticVersion: SEMANTIC_SCHEMA_VERSION,
  } as const;
  const request = {
    operation: "dismiss_new_inbound",
    mailboxId: identity.mailboxId,
    identity: {
      conversationId: identity.conversationId,
      latestTurnId: identity.latestTurnId,
      semanticVersion: identity.semanticVersion,
    },
  } as const;
  assert.deepEqual(
    buildPrioritySemanticNewInboundDismissalWireRequest(request),
    request,
  );
  assert.equal(
    buildPrioritySemanticNewInboundIdentityKey(identity),
    JSON.stringify([
      identity.mailboxId,
      identity.conversationId,
      identity.latestTurnId,
      identity.semanticVersion,
    ]),
  );
  assert.equal(
    isPrioritySemanticNewInboundDismissalTurnCurrent({
      dismissedIdentity: identity,
      currentIdentity: identity,
    }),
    true,
  );
  assert.equal(
    isPrioritySemanticNewInboundDismissalTurnCurrent({
      dismissedIdentity: identity,
      currentIdentity: { ...identity, latestTurnId: "message-newer" },
    }),
    false,
    "a same-connection newer turn cancels stale local Done/Remove completion",
  );
  assert.equal(
    isPrioritySemanticNewInboundDismissalTurnCurrent({
      dismissedIdentity: identity,
      currentIdentity: { ...identity, mailboxId: "mailbox-reconnected" },
    }),
    false,
    "a reconnect/account replacement cannot inherit stale local completion",
  );

  const dismissalFences = new Map<string, Set<string>>();
  for (
    let index = 0;
    index < PRIORITY_SEMANTIC_NEW_INBOUND_MAX_HYDRATION_RECORDS + 1;
    index += 1
  ) {
    rememberPrioritySemanticNewInboundDismissalFence(
      dismissalFences,
      "mailbox-a",
      `a-${index}`,
    );
  }
  for (
    let index = 0;
    index < PRIORITY_SEMANTIC_NEW_INBOUND_MAX_HYDRATION_RECORDS;
    index += 1
  ) {
    rememberPrioritySemanticNewInboundDismissalFence(
      dismissalFences,
      "mailbox-b",
      `b-${index}`,
    );
  }
  assert.equal(
    dismissalFences.get("mailbox-a")?.size,
    PRIORITY_SEMANTIC_NEW_INBOUND_MAX_HYDRATION_RECORDS,
  );
  assert.equal(
    dismissalFences.get("mailbox-b")?.size,
    PRIORITY_SEMANTIC_NEW_INBOUND_MAX_HYDRATION_RECORDS,
  );
  assert.equal(dismissalFences.get("mailbox-a")?.has("a-0"), false);
  assert.equal(
    dismissalFences
      .get("mailbox-a")
      ?.has(`a-${PRIORITY_SEMANTIC_NEW_INBOUND_MAX_HYDRATION_RECORDS}`),
    true,
  );
  assert.equal(
    dismissalFences.get("mailbox-b")?.has("b-0"),
    true,
    "a 65th confirmation in mailbox A cannot evict mailbox B's stale-hydration fence",
  );

  for (const forged of [
    { ...request, operation: "dismiss" },
    { ...request, mailboxId: " mailbox-1" },
    { ...request, identity: null },
    {
      ...request,
      identity: { ...request.identity, conversationId: "" },
    },
    { ...request, state: "needs_user_action" },
    { ...request, confidence: 1 },
    { ...request, priorityEffect: "observe_only" },
    { ...request, workspaceId: "workspace-2" },
    {
      ...request,
      identity: { ...request.identity, mailboxId: "mailbox-2" },
    },
    {
      ...request,
      identity: { ...request.identity, reasonCode: "explicit_request" },
    },
    {
      ...request,
      identity: {
        ...request.identity,
        semanticVersion: "priority-semantic-v0",
      },
    },
    { ...request, mailboxId: `m${"x".repeat(256)}` },
    {
      ...request,
      identity: {
        ...request.identity,
        conversationId: `c${"x".repeat(1_024)}`,
      },
    },
    {
      ...request,
      identity: {
        ...request.identity,
        latestTurnId: `t${"x".repeat(512)}`,
      },
    },
    {
      ...request,
      identity: { ...request.identity, latestTurnId: "message\nnew" },
    },
  ]) {
    assert.equal(
      buildPrioritySemanticNewInboundDismissalWireRequest(forged),
      null,
      "dismissal accepts only one bounded exact-turn identity and no policy fields",
    );
  }

  const dismissed = {
    ok: true,
    status: "dismissed",
    semanticTrigger: "new_inbound",
    newInboundMode: "shadow",
    priorityEffect: "observe_only",
    identity,
  } as const;
  assert.deepEqual(
    parsePrioritySemanticNewInboundDismissalResponse(dismissed),
    dismissed,
  );
  for (const invalid of [
    { ...dismissed, status: "hydrated" },
    { ...dismissed, newInboundMode: "active" },
    { ...dismissed, newInboundMode: "off" },
    { ...dismissed, priorityEffect: "promote_new_inbound" },
    { ...dismissed, dismissedAt: "2026-08-22T10:00:00.000Z" },
    {
      ...dismissed,
      identity: { ...identity, latestTurnId: "message-newer" },
      state: "needs_user_action",
    },
  ]) {
    assert.equal(
      parsePrioritySemanticNewInboundDismissalResponse(invalid),
      null,
      "dismiss success must stay shadow-only and use the exact response envelope",
    );
  }
  assert.deepEqual(
    parsePrioritySemanticNewInboundDismissalResponse({
      ok: false,
      error: {
        code: "new_inbound_identity_not_current",
        message: "The semantic new-inbound identity is no longer current.",
      },
    }),
    {
      ok: false,
      error: {
        code: "new_inbound_identity_not_current",
        message: "The semantic new-inbound identity is no longer current.",
      },
    },
  );
}

runBoundaryTests();
runEligibilityAndWireTests();
runResponseParserTests();
runHydrationTests();
runDismissalTests();

console.log("\n✓ Priority semantic new-inbound boundary tests passed.");
