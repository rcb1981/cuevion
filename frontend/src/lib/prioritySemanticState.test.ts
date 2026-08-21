import assert from "node:assert/strict";
import { applyLiveThreadIdentity, resolveCanonicalConversationIdentity } from "./inboxEngine";
import { shouldAllowNormalPriority } from "./normalPriorityGate";
import { resolvePrioritySource } from "./prioritySource";
import {
  PRIORITY_SEMANTIC_AUTHORED_TEXT_MAX_CODE_POINTS,
  PRIORITY_SEMANTIC_CURRENT_LOOKUP_TRIGGER_MAX_RECORDS,
  PRIORITY_SEMANTIC_PENDING_RETURNED_TRIGGER_MAX_RECORDS,
  PRIORITY_SEMANTIC_REQUESTED_TRIGGER_MAX_KEYS,
  PRIORITY_SEMANTIC_SHADOW_OBSERVATION_MAX_RECORDS,
  SEMANTIC_SCHEMA_VERSION,
  addPrioritySemanticObservation,
  addPrioritySemanticShadowObservation,
  buildPrioritySemanticAssessmentWireRequest,
  buildPrioritySemanticCurrentLookupWireRequest,
  buildPrioritySemanticObservationKey,
  findPrioritySemanticCurrentLookupTriggers,
  findPrioritySemanticReturnedReplyTriggers,
  normalizePrioritySemanticActiveEventRefStore,
  normalizePrioritySemanticAuthoredText,
  parsePrioritySemanticAssessmentResponse,
  persistPrioritySemanticActiveEventRefStore,
  projectPrioritySemanticObservation,
  projectPrioritySemanticShadowObservation,
  readPrioritySemanticActiveEventRefStore,
  recordPrioritySemanticActiveEventRef,
  rememberPrioritySemanticPendingReturnedReplyTrigger,
  rememberPrioritySemanticRequestedTriggerKey,
  resolvePrioritySemanticActiveEventRef,
  shouldSuppressAutomaticOpenLoopPriority,
  type PrioritySemanticAssessmentSuccess,
} from "./prioritySemanticState";
import type { WaitingOnOtherStore } from "./waitingOnOther";

let passed = 0;
let failed = 0;

function test(name: string, fn: () => void) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed += 1;
  } catch (error) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${(error as Error).message}`);
    failed += 1;
  }
}

const semanticIdentity = {
  mailboxId: "mailbox-1",
  conversationId: "thread:mailbox-1|gmail:mailbox-1:thread-1",
  latestTurnId: "message-2",
  semanticVersion: SEMANTIC_SCHEMA_VERSION,
} as const;

function assessedResponse(
  overrides: Partial<PrioritySemanticAssessmentSuccess> = {},
): PrioritySemanticAssessmentSuccess {
  return {
    ok: true,
    status: "assessed",
    semanticMode: "shadow",
    priorityEffect: "observe_only",
    assessment: {
      state: "resolved",
      confidence: 0.99,
      reasonCode: "completed_confirmation",
    },
    effectiveSemanticState: "resolved",
    identity: semanticIdentity,
    activeEventRef: "pse1.ticket.signature",
    assessedAt: "2026-08-21T08:30:00.000Z",
    ...overrides,
  };
}

console.log("\nprioritySemanticState");

test("canonicalizes exact outgoing text and rejects an oversized body", () => {
  assert.equal(
    normalizePrioritySemanticAuthoredText("  Cafe\u0301\r\nDone.  "),
    "Café\nDone.",
  );
  const oversized = normalizePrioritySemanticAuthoredText(
    "🙂".repeat(PRIORITY_SEMANTIC_AUTHORED_TEXT_MAX_CODE_POINTS + 5),
  );
  assert.equal(oversized, "");
});

test("outgoing request admits only the signed ticket and bounded authored text", () => {
  assert.deepEqual(
    buildPrioritySemanticAssessmentWireRequest({
      mailboxId: "mailbox-1",
      trigger: "outgoing_reply",
      eventRef: "pse1.ticket.signature",
      authoredText: "  Thanks.\r\nDone.  ",
    }),
    {
      mailboxId: "mailbox-1",
      trigger: "outgoing_reply",
      eventRef: "pse1.ticket.signature",
      authoredText: "Thanks.\nDone.",
    },
  );
  assert.equal(
    buildPrioritySemanticAssessmentWireRequest({
      mailboxId: "mailbox-1",
      trigger: "outgoing_reply",
      eventRef: "pse1.ticket.signature",
      authoredText: "Done.",
      workspaceId: "browser-asserted-authority",
    }),
    null,
  );
  assert.equal(
    buildPrioritySemanticAssessmentWireRequest({
      mailboxId: "mailbox-1",
      trigger: "forward",
      eventRef: "pse1.ticket.signature",
      authoredText: "FYI",
    }),
    null,
  );
});

test("incoming request admits locators but rejects message text and extra authority", () => {
  assert.deepEqual(
    buildPrioritySemanticAssessmentWireRequest({
      mailboxId: "mailbox-1",
      trigger: "incoming_reply",
      activeEventRef: "pse1.ticket.signature",
      incomingLocator: {
        provider: "google",
        providerMessageId: "message-2",
      },
    }),
    {
      mailboxId: "mailbox-1",
      trigger: "incoming_reply",
      activeEventRef: "pse1.ticket.signature",
      incomingLocator: {
        provider: "google",
        providerMessageId: "message-2",
      },
    },
  );
  assert.equal(
    buildPrioritySemanticAssessmentWireRequest({
      mailboxId: "mailbox-1",
      trigger: "incoming_reply",
      activeEventRef: "pse1.ticket.signature",
      incomingLocator: {
        provider: "google",
        providerMessageId: "message-2",
      },
      text: "browser must not send incoming content",
    }),
    null,
  );
  assert.equal(
    buildPrioritySemanticAssessmentWireRequest({
      mailboxId: "mailbox-1",
      trigger: "incoming_reply",
      incomingLocator: {
        provider: "custom_imap",
        providerFolder: "INBOX",
        uidValidity: "0",
        imapUid: "1",
      },
    }),
    null,
  );
  assert.equal(
    buildPrioritySemanticAssessmentWireRequest({
      mailboxId: "mailbox-1",
      trigger: "incoming_reply",
      incomingLocator: {
        provider: "custom_imap",
        providerFolder: "INBOX",
        uidValidity: "4294967296",
        imapUid: "1",
      },
    }),
    null,
  );
  assert.deepEqual(
    buildPrioritySemanticAssessmentWireRequest({
      mailboxId: "mailbox-1",
      trigger: "incoming_reply",
      incomingLocator: {
        provider: "custom_imap",
        providerFolder: "INBOX",
        uidValidity: "4294967295",
        imapUid: "4294967295",
      },
    }),
    {
      mailboxId: "mailbox-1",
      trigger: "incoming_reply",
      incomingLocator: {
        provider: "custom_imap",
        providerFolder: "INBOX",
        uidValidity: "4294967295",
        imapUid: "4294967295",
      },
    },
  );
  assert.equal(
    buildPrioritySemanticAssessmentWireRequest({
      mailboxId: "mailbox-1",
      trigger: "incoming_reply",
      activeEventRef: "legacy-custom-ref",
      incomingLocator: {
        provider: "custom_imap",
        providerFolder: "INBOX",
        uidValidity: "7",
        imapUid: "8",
      },
    }),
    null,
    "custom IMAP authority must be ref-less",
  );
  assert.equal(
    buildPrioritySemanticAssessmentWireRequest({
      mailboxId: "mailbox-1",
      trigger: "incoming_reply",
      incomingLocator: {
        provider: "custom_imap",
        providerFolder: "INBOX",
        uidValidity: "7",
        imapUid: "8",
      },
      conversationRoot: "browser-supplied-root",
    }),
    null,
  );
});

test("lookup_current wire requests are exact, lookup-only, and provider bounded", () => {
  const gmailOutgoing = {
    operation: "lookup_current",
    mailboxId: "mailbox-1",
    trigger: "outgoing_reply",
    eventRef: "pse1.ticket.signature",
  } as const;
  assert.deepEqual(
    buildPrioritySemanticCurrentLookupWireRequest(gmailOutgoing),
    gmailOutgoing,
  );
  assert.equal(
    buildPrioritySemanticCurrentLookupWireRequest({
      ...gmailOutgoing,
      authoredText: "must never enter a cache lookup",
    }),
    null,
  );

  const gmailIncoming = {
    operation: "lookup_current",
    mailboxId: "mailbox-1",
    trigger: "incoming_reply",
    activeEventRef: "pse1.ticket.signature",
    incomingLocator: {
      provider: "google",
      providerMessageId: "message-2",
    },
  } as const;
  assert.deepEqual(
    buildPrioritySemanticCurrentLookupWireRequest(gmailIncoming),
    gmailIncoming,
  );
  assert.equal(
    buildPrioritySemanticCurrentLookupWireRequest({
      ...gmailIncoming,
      incomingLocator: {
        provider: "google",
        providerMessageId: "message-2",
        subject: "browser authority is forbidden",
      },
    }),
    null,
  );

  const customImapIncoming = {
    operation: "lookup_current",
    mailboxId: "mailbox-1",
    trigger: "incoming_reply",
    incomingLocator: {
      provider: "custom_imap",
      providerFolder: "INBOX",
      uidValidity: "77",
      imapUid: "8",
    },
  } as const;
  assert.deepEqual(
    buildPrioritySemanticCurrentLookupWireRequest(customImapIncoming),
    customImapIncoming,
  );
  assert.equal(
    buildPrioritySemanticCurrentLookupWireRequest({
      ...customImapIncoming,
      activeEventRef: "custom-smtp-must-not-gain-semantic-authority",
    }),
    null,
  );
});

test("strict response parser rejects incompatible state/reason and extra authority", () => {
  assert.deepEqual(
    parsePrioritySemanticAssessmentResponse(assessedResponse()),
    assessedResponse(),
  );
  assert.equal(
    parsePrioritySemanticAssessmentResponse(
      assessedResponse({
        assessment: {
          state: "resolved",
          confidence: 0.99,
          reasonCode: "explicit_request",
        },
      }),
    ),
    null,
  );
  assert.equal(
    parsePrioritySemanticAssessmentResponse({
      ...assessedResponse(),
      workspaceId: "browser-asserted-authority",
    }),
    null,
  );
  assert.equal(
    parsePrioritySemanticAssessmentResponse({
      ...assessedResponse(),
      identity: { ...semanticIdentity, tenantId: "unexpected" },
    }),
    null,
  );
});

test("strict response parser requires bounded server policy and rejects invalid combinations", () => {
  const activeBoundary = assessedResponse({
    semanticMode: "active",
    priorityEffect: "suppress_automatic_open_loop",
    assessment: {
      state: "resolved",
      confidence: 0.97,
      reasonCode: "closing_acknowledgement",
    },
    effectiveSemanticState: "resolved",
  });
  assert.deepEqual(
    parsePrioritySemanticAssessmentResponse(activeBoundary),
    activeBoundary,
  );
  const activeObserveOnly = {
    ...activeBoundary,
    priorityEffect: "observe_only",
  } as const;
  assert.deepEqual(
    parsePrioritySemanticAssessmentResponse(activeObserveOnly),
    activeObserveOnly,
  );
  const shadowObserveOnly = assessedResponse();
  assert.deepEqual(
    parsePrioritySemanticAssessmentResponse(shadowObserveOnly),
    shadowObserveOnly,
  );

  assert.equal(
    parsePrioritySemanticAssessmentResponse({
      ...activeBoundary,
      assessment: { ...activeBoundary.assessment, confidence: 0.969 },
    }),
    null,
    "resolved below the exact threshold cannot carry suppression",
  );
  assert.equal(
    parsePrioritySemanticAssessmentResponse({
      ...activeBoundary,
      assessment: {
        state: "needs_user_action",
        confidence: 0.99,
        reasonCode: "explicit_request",
      },
      effectiveSemanticState: "needs_user_action",
    }),
    null,
  );
  for (const invalidAssessment of [
    {
      state: "informational",
      confidence: 1,
      reasonCode: "informational_update",
    },
    {
      state: "uncertain",
      confidence: 1,
      reasonCode: "ambiguous_context",
    },
  ] as const) {
    assert.equal(
      parsePrioritySemanticAssessmentResponse({
        ...activeBoundary,
        assessment: invalidAssessment,
        effectiveSemanticState: invalidAssessment.state,
      }),
      null,
      `${invalidAssessment.state} can never carry suppression`,
    );
  }
  assert.equal(
    parsePrioritySemanticAssessmentResponse({
      ...activeBoundary,
      effectiveSemanticState: "uncertain",
    }),
    null,
  );
  assert.equal(
    parsePrioritySemanticAssessmentResponse({
      ...activeBoundary,
      semanticMode: "shadow",
    }),
    null,
    "shadow can never suppress",
  );
  assert.equal(
    parsePrioritySemanticAssessmentResponse({
      ...assessedResponse(),
      semanticMode: "unexpected",
    }),
    null,
  );
  assert.equal(
    parsePrioritySemanticAssessmentResponse({
      ...assessedResponse(),
      priorityEffect: "unexpected",
    }),
    null,
  );
  const missingPolicy = { ...assessedResponse() } as Record<string, unknown>;
  delete missingPolicy.priorityEffect;
  assert.equal(parsePrioritySemanticAssessmentResponse(missingPolicy), null);

  const pending = {
    ok: true,
    status: "pending",
    semanticMode: "active",
    priorityEffect: "observe_only",
    identity: semanticIdentity,
    retryAfterSeconds: 10,
  } as const;
  assert.deepEqual(parsePrioritySemanticAssessmentResponse(pending), pending);
  assert.equal(
    parsePrioritySemanticAssessmentResponse({
      ...pending,
      priorityEffect: "suppress_automatic_open_loop",
    }),
    null,
    "pending and deferred results are always observe-only",
  );
});

test("client projection is conservative and requires exact live identity", () => {
  const lowResolved = assessedResponse({
    assessment: {
      state: "resolved",
      confidence: 0.96,
      reasonCode: "closing_acknowledgement",
    },
    effectiveSemanticState: "uncertain",
  });
  assert.equal(
    projectPrioritySemanticShadowObservation(lowResolved, semanticIdentity)
      ?.effectiveSemanticState,
    "uncertain",
  );

  const disagreeingServerProjection = assessedResponse({
    effectiveSemanticState: "informational",
  });
  assert.equal(
    projectPrioritySemanticShadowObservation(
      disagreeingServerProjection,
      semanticIdentity,
    )?.effectiveSemanticState,
    "uncertain",
  );
  assert.equal(
    projectPrioritySemanticShadowObservation(assessedResponse(), {
      ...semanticIdentity,
      latestTurnId: "different-live-turn",
    }),
    null,
  );
});

test("automatic open-loop suppression requires exact current active server authority", () => {
  const activeObservation = projectPrioritySemanticObservation(
    assessedResponse({
      semanticMode: "active",
      priorityEffect: "suppress_automatic_open_loop",
    }),
    semanticIdentity,
  );
  assert.ok(activeObservation);
  const baseInput = {
    observation: activeObservation,
    currentIdentity: semanticIdentity,
    currentActiveEventRef: "pse1.ticket.signature",
    hasAutomaticOpenLoopEvidence: true,
    hasIndependentPriorityAuthority: false,
  } as const;
  assert.equal(shouldSuppressAutomaticOpenLoopPriority(baseInput), true);
  assert.equal(
    shouldSuppressAutomaticOpenLoopPriority({
      ...baseInput,
      hasIndependentPriorityAuthority: true,
    }),
    false,
    "manual, collaboration, assigned-review, or other explicit authority wins",
  );
  assert.equal(
    shouldSuppressAutomaticOpenLoopPriority({
      ...baseInput,
      hasAutomaticOpenLoopEvidence: false,
    }),
    false,
  );
  assert.equal(
    shouldSuppressAutomaticOpenLoopPriority({
      ...baseInput,
      currentIdentity: { ...semanticIdentity, latestTurnId: "newer-message" },
    }),
    false,
    "a newer turn makes the old observation ineligible immediately",
  );
  assert.equal(
    shouldSuppressAutomaticOpenLoopPriority({
      ...baseInput,
      currentActiveEventRef: "pse1.superseding.signature",
    }),
    false,
    "a superseded signed event cannot suppress the current open loop",
  );
  assert.equal(
    shouldSuppressAutomaticOpenLoopPriority({
      ...baseInput,
      observation: {
        ...activeObservation!,
        confidence: 0.969,
      },
    }),
    false,
  );
  assert.equal(
    shouldSuppressAutomaticOpenLoopPriority({
      ...baseInput,
      observation: {
        ...activeObservation!,
        semanticMode: "shadow",
        priorityEffect: "observe_only",
        isShadow: true,
      },
    }),
    false,
  );
  assert.equal(
    buildPrioritySemanticObservationKey(activeObservation!.identity),
    buildPrioritySemanticObservationKey(semanticIdentity),
  );

  const observationKey = buildPrioritySemanticObservationKey(semanticIdentity);
  const shadowRollback = projectPrioritySemanticObservation(
    assessedResponse(),
    semanticIdentity,
  );
  assert.ok(shadowRollback);
  const rolledBackObservations = addPrioritySemanticObservation(
    addPrioritySemanticObservation({}, observationKey, activeObservation!),
    observationKey,
    shadowRollback!,
  );
  assert.equal(
    shouldSuppressAutomaticOpenLoopPriority({
      ...baseInput,
      observation: rolledBackObservations[observationKey],
    }),
    false,
    "a current shadow response replaces active policy without mutating evidence",
  );
});

test("resolved 0.99 remains a shadow observation and changes no deterministic Priority result", () => {
  const deterministicInput = {
    hasReturnedReplyEvidence: true,
    message: { priorityScore: "medium" as const },
  };
  const before = resolvePrioritySource(deterministicInput);
  const beforeGate = shouldAllowNormalPriority({
    prioritySource: before,
    returnedReplyEvidence: { hasEvidence: true, confidence: "high" },
  });
  const observation = projectPrioritySemanticShadowObservation(
    assessedResponse(),
    semanticIdentity,
  );
  const after = resolvePrioritySource(deterministicInput);
  const afterGate = shouldAllowNormalPriority({
    prioritySource: after,
    returnedReplyEvidence: { hasEvidence: true, confidence: "high" },
  });

  assert.equal(observation?.state, "resolved");
  assert.equal(observation?.confidence, 0.99);
  assert.equal(observation?.isShadow, true);
  assert.deepEqual(after, before);
  assert.equal(beforeGate, true);
  assert.equal(afterGate, true);
});

test("waiting Priority, failure, uncertain, and count remain deterministic-only", () => {
  const deterministicInput = {
    hasWaitingOnOtherEvidence: true,
    message: { priorityScore: "low" as const },
  };
  const before = resolvePrioritySource(deterministicInput);
  const beforeGate = shouldAllowNormalPriority({ prioritySource: before });
  const priorityCollection = ["waiting-conversation"];

  const resolvedShadow = projectPrioritySemanticShadowObservation(
    assessedResponse(),
    semanticIdentity,
  );
  const uncertainShadow = projectPrioritySemanticShadowObservation(
    assessedResponse({
      assessment: {
        state: "uncertain",
        confidence: 0.42,
        reasonCode: "ambiguous_context",
      },
      effectiveSemanticState: "uncertain",
    }),
    semanticIdentity,
  );
  const failedShadow = projectPrioritySemanticShadowObservation(
    {
      ok: false,
      error: {
        code: "semantic_unavailable",
        message: "Semantic analysis is unavailable.",
      },
    },
    semanticIdentity,
  );

  const after = resolvePrioritySource(deterministicInput);
  const afterGate = shouldAllowNormalPriority({ prioritySource: after });
  assert.equal(resolvedShadow?.state, "resolved");
  assert.equal(uncertainShadow?.effectiveSemanticState, "uncertain");
  assert.equal(failedShadow, null);
  assert.equal(before.source, "waiting_on_other");
  assert.deepEqual(after, before);
  assert.equal(beforeGate, true);
  assert.equal(afterGate, true);
  assert.equal(priorityCollection.length, 1);
});

test("opaque event-ref transport survives refresh without storing assessment authority", () => {
  const nowMs = new Date("2026-08-21T08:30:00.000Z").getTime();
  const store = recordPrioritySemanticActiveEventRef(
    {},
    {
      mailboxId: "mailbox-1",
      conversationId: semanticIdentity.conversationId,
      activeEventRef: "pse1.ticket.signature",
      recordedAt: "2026-08-21T08:00:00.000Z",
    },
    nowMs,
  );
  const persisted: Record<string, string> = {};
  persistPrioritySemanticActiveEventRefStore(
    { setItem: (key, value) => (persisted[key] = value) },
    "semantic-shadow-key",
    store,
    nowMs,
  );
  const hydrated = readPrioritySemanticActiveEventRefStore(
    { getItem: (key) => persisted[key] ?? null },
    "semantic-shadow-key",
    nowMs,
  );
  assert.equal(
    resolvePrioritySemanticActiveEventRef(
      hydrated,
      "mailbox-1",
      semanticIdentity.conversationId,
      nowMs,
    )?.activeEventRef,
    "pse1.ticket.signature",
  );
  assert.doesNotMatch(JSON.stringify(hydrated), /resolved|confidence|reasonCode/);
});

test("semantic storage failures are swallowed and preserve the in-memory record", () => {
  const nowMs = new Date("2026-08-21T08:30:00.000Z").getTime();
  const store = recordPrioritySemanticActiveEventRef(
    {},
    {
      mailboxId: "mailbox-1",
      conversationId: semanticIdentity.conversationId,
      activeEventRef: "pse1.ticket.signature",
      recordedAt: "2026-08-21T08:00:00.000Z",
    },
    nowMs,
  );
  assert.deepEqual(
    readPrioritySemanticActiveEventRefStore(
      { getItem: () => { throw new Error("SecurityError"); } },
      "semantic-shadow-key",
      nowMs,
    ),
    {},
  );
  const retained = persistPrioritySemanticActiveEventRefStore(
    { setItem: () => { throw new Error("QuotaExceededError"); } },
    "semantic-shadow-key",
    store,
    nowMs,
  );
  assert.deepEqual(retained, normalizePrioritySemanticActiveEventRefStore(store, nowMs));
});

test("long-lived shadow observation, trigger, and pending stores remain bounded", () => {
  let observations = {};
  for (
    let index = 0;
    index < PRIORITY_SEMANTIC_SHADOW_OBSERVATION_MAX_RECORDS + 20;
    index += 1
  ) {
    observations = addPrioritySemanticShadowObservation(
      observations,
      `observation-${index}`,
      {
        status: "pending",
        identity: {
          ...semanticIdentity,
          conversationId: `conversation-${index}`,
          latestTurnId: `message-${index}`,
        },
        semanticMode: "shadow",
        priorityEffect: "observe_only",
        isShadow: true,
      },
    );
  }
  assert.equal(
    Object.keys(observations).length,
    PRIORITY_SEMANTIC_SHADOW_OBSERVATION_MAX_RECORDS,
  );
  assert.equal("observation-0" in observations, false);

  const replacementIdentity = {
    ...semanticIdentity,
    latestTurnId: "replacement-turn",
  };
  const withOldConversationTurn = addPrioritySemanticShadowObservation(
    observations,
    "old-conversation-turn",
    {
      status: "cached",
      identity: semanticIdentity,
      semanticMode: "active",
      priorityEffect: "observe_only",
      activeEventRef: "pse1.old.signature",
      isShadow: false,
    },
  );
  const withReplacement = addPrioritySemanticShadowObservation(
    withOldConversationTurn,
    buildPrioritySemanticObservationKey(replacementIdentity),
    {
      status: "cached",
      identity: replacementIdentity,
      semanticMode: "active",
      priorityEffect: "observe_only",
      activeEventRef: "pse1.new.signature",
      isShadow: false,
    },
  );
  assert.equal("old-conversation-turn" in withReplacement, false);
  assert.equal(
    withReplacement[buildPrioritySemanticObservationKey(replacementIdentity)]
      ?.activeEventRef,
    "pse1.new.signature",
  );

  const requestedKeys = new Set<string>();
  for (
    let index = 0;
    index < PRIORITY_SEMANTIC_REQUESTED_TRIGGER_MAX_KEYS + 20;
    index += 1
  ) {
    assert.equal(
      rememberPrioritySemanticRequestedTriggerKey(
        requestedKeys,
        `trigger-${index}`,
      ),
      true,
    );
  }
  assert.equal(
    requestedKeys.size,
    PRIORITY_SEMANTIC_REQUESTED_TRIGGER_MAX_KEYS,
  );
  assert.equal(requestedKeys.has("trigger-0"), false);
  assert.equal(
    rememberPrioritySemanticRequestedTriggerKey(
      requestedKeys,
      `trigger-${PRIORITY_SEMANTIC_REQUESTED_TRIGGER_MAX_KEYS + 19}`,
    ),
    false,
  );

  const pending = new Map();
  for (
    let index = 0;
    index < PRIORITY_SEMANTIC_PENDING_RETURNED_TRIGGER_MAX_RECORDS + 20;
    index += 1
  ) {
    rememberPrioritySemanticPendingReturnedReplyTrigger(
      pending,
      `pending-${index}`,
      {
        mailboxId: "mailbox-1",
        conversationId: `conversation-${index}`,
        latestTurnId: `message-${index}`,
        returnedMessageKey: `returned-${index}`,
        incomingLocator: {
          provider: "google",
          providerMessageId: `message-${index}`,
        },
      },
    );
  }
  assert.equal(
    pending.size,
    PRIORITY_SEMANTIC_PENDING_RETURNED_TRIGGER_MAX_RECORDS,
  );
  assert.equal(pending.has("pending-0"), false);
});

test("lookup discovery rehydrates only exact current Gmail waiting authority", () => {
  const nowMs = new Date("2026-08-21T10:00:00.000Z").getTime();
  const sourceInbound = applyLiveThreadIdentity(
    {
      id: "source-ui",
      providerMessageId: "source-1",
      providerThreadId: "thread-lookup",
      subject: "Contract",
      from: "external@example.com",
      to: "user@example.com",
      createdAt: "2026-08-21T07:50:00.000Z",
      timestamp: "2026-08-21T07:50:00.000Z",
    },
    {
      mailboxId: "mailbox-1",
      provider: "google",
      folder: "INBOX",
      uidValidity: "gmail-api",
    },
  );
  const sent = applyLiveThreadIdentity(
    {
      id: "sent-ui",
      providerMessageId: "sent-1",
      providerThreadId: "thread-lookup",
      subject: "Re: Contract",
      from: "user@example.com",
      to: "external@example.com",
      createdAt: "2026-08-21T08:00:00.000Z",
      timestamp: "2026-08-21T08:00:00.000Z",
    },
    {
      mailboxId: "mailbox-1",
      provider: "google",
      folder: "SENT",
      uidValidity: "gmail-api",
    },
  );
  const conversationId = resolveCanonicalConversationIdentity(
    sourceInbound,
    "mailbox-1",
  ).key;
  const waitingStore: WaitingOnOtherStore = {
    waiting: {
      state: "waiting_on_other",
      mailboxId: "mailbox-1",
      conversationKey: conversationId,
      transitionedAt: "2026-08-21T08:00:00.000Z",
    },
  };
  const persistedSliceOneEvent = recordPrioritySemanticActiveEventRef(
    {},
    {
      mailboxId: "mailbox-1",
      conversationId,
      activeEventRef: "pse1.ticket.signature",
      recordedAt: "2026-08-21T08:00:00.000Z",
    },
    nowMs,
  );
  const discovered = findPrioritySemanticCurrentLookupTriggers({
    waitingStore,
    messageEntries: [
      { mailboxId: "mailbox-1", message: sourceInbound },
      { mailboxId: "mailbox-1", message: sent },
    ],
    activeEventRefStore: persistedSliceOneEvent,
    ownEmailAddresses: ["user@example.com"],
    nowMs,
  });
  assert.deepEqual(discovered, [
    {
      request: {
        operation: "lookup_current",
        mailboxId: "mailbox-1",
        trigger: "outgoing_reply",
        eventRef: "pse1.ticket.signature",
      },
      expectedIdentity: {
        mailboxId: "mailbox-1",
        conversationId,
        latestTurnId: "sent-1",
        semanticVersion: SEMANTIC_SCHEMA_VERSION,
      },
      observationKey: buildPrioritySemanticObservationKey({
        mailboxId: "mailbox-1",
        conversationId,
        latestTurnId: "sent-1",
        semanticVersion: SEMANTIC_SCHEMA_VERSION,
      }),
      deterministicState: "waiting_on_other",
    },
  ]);
  assert.ok(
    discovered.length <= PRIORITY_SEMANTIC_CURRENT_LOOKUP_TRIGGER_MAX_RECORDS,
  );

  assert.deepEqual(
    findPrioritySemanticCurrentLookupTriggers({
      waitingStore,
      messageEntries: [{ mailboxId: "mailbox-1", message: sourceInbound }],
      activeEventRefStore: persistedSliceOneEvent,
      ownEmailAddresses: ["user@example.com"],
      nowMs,
    }),
    [],
    "a legacy event ref without latestTurnId needs one unique exact Sent entry",
  );

  const newerInbound = applyLiveThreadIdentity(
    {
      ...sourceInbound,
      id: "newer-ui",
      providerMessageId: "newer-2",
      createdAt: "2026-08-21T08:10:00.000Z",
      timestamp: "2026-08-21T08:10:00.000Z",
    },
    sourceInbound.threadIdentityContext,
  );
  assert.deepEqual(
    findPrioritySemanticCurrentLookupTriggers({
      waitingStore,
      messageEntries: [
        { mailboxId: "mailbox-1", message: sourceInbound },
        { mailboxId: "mailbox-1", message: sent },
        { mailboxId: "mailbox-1", message: newerInbound },
      ],
      activeEventRefStore: persistedSliceOneEvent,
      ownEmailAddresses: ["user@example.com"],
      nowMs,
    }),
    [],
    "a newer authoritative turn breaks old outgoing suppression immediately",
  );
});

test("lookup discovery supports exact returned Gmail and IMAP but never custom SMTP waiting", () => {
  const nowMs = new Date("2026-08-21T11:00:00.000Z").getTime();
  const gmailInbound = applyLiveThreadIdentity(
    {
      id: "gmail-returned-ui",
      providerMessageId: "gmail-returned-2",
      providerThreadId: "gmail-thread-returned",
      subject: "Re: Contract",
      from: "external@example.com",
      to: "user@example.com",
      createdAt: "2026-08-21T10:10:00.000Z",
      timestamp: "2026-08-21T10:10:00.000Z",
    },
    {
      mailboxId: "gmail-mailbox",
      provider: "google",
      folder: "INBOX",
      uidValidity: "gmail-api",
    },
  );
  const gmailConversationId = resolveCanonicalConversationIdentity(
    gmailInbound,
    "gmail-mailbox",
  ).key;
  const gmailStore: WaitingOnOtherStore = {
    returned: {
      state: "returned_reply",
      mailboxId: "gmail-mailbox",
      conversationKey: gmailConversationId,
      transitionedAt: "2026-08-21T10:00:00.000Z",
      returnedMessageKey:
        "returned-message:v1:gmail-mailbox:google:gmail-returned-2",
      returnedReplyAt: "2026-08-21T10:10:00.000Z",
    },
  };
  const gmailActiveEvent = recordPrioritySemanticActiveEventRef(
    {},
    {
      mailboxId: "gmail-mailbox",
      conversationId: gmailConversationId,
      activeEventRef: "pse1.gmail.signature",
      latestTurnId: "gmail-sent-1",
      recordedAt: "2026-08-21T10:00:00.000Z",
    },
    nowMs,
  );
  const gmailTriggers = findPrioritySemanticCurrentLookupTriggers({
    waitingStore: gmailStore,
    messageEntries: [{ mailboxId: "gmail-mailbox", message: gmailInbound }],
    activeEventRefStore: gmailActiveEvent,
    ownEmailAddresses: ["user@example.com"],
    nowMs,
  });
  assert.equal(gmailTriggers.length, 1);
  assert.deepEqual(gmailTriggers[0].request, {
    operation: "lookup_current",
    mailboxId: "gmail-mailbox",
    trigger: "incoming_reply",
    activeEventRef: "pse1.gmail.signature",
    incomingLocator: {
      provider: "google",
      providerMessageId: "gmail-returned-2",
    },
  });

  const imapInbound = applyLiveThreadIdentity(
    {
      id: "imap-returned-ui",
      threadId: "imap:rfc:imap-mailbox:root%40example.com",
      imapUid: "8",
      rfcMessageId: "<reply-8@example.com>",
      subject: "Re: Artwork",
      from: "external@example.com",
      to: "user@example.com",
      createdAt: "2026-08-21T10:40:00.000Z",
      timestamp: "2026-08-21T10:40:00.000Z",
      providerFolder: "INBOX",
      uidValidity: "77",
    },
    {
      mailboxId: "imap-mailbox",
      provider: "custom_imap",
      folder: "INBOX",
      uidValidity: "77",
    },
  );
  const imapConversationId = resolveCanonicalConversationIdentity(
    imapInbound,
    "imap-mailbox",
  ).key;
  const imapReturnedMessageKey = [
    "returned-message:v1",
    encodeURIComponent("imap-mailbox"),
    "custom_imap",
    encodeURIComponent("<reply-8@example.com>"),
  ].join(":");
  const imapReturnedStore: WaitingOnOtherStore = {
    returned: {
      state: "returned_reply",
      mailboxId: "imap-mailbox",
      conversationKey: imapConversationId,
      transitionedAt: "2026-08-21T10:30:00.000Z",
      returnedMessageKey: imapReturnedMessageKey,
      returnedReplyAt: "2026-08-21T10:40:00.000Z",
    },
  };
  const imapTriggers = findPrioritySemanticCurrentLookupTriggers({
    waitingStore: imapReturnedStore,
    messageEntries: [{ mailboxId: "imap-mailbox", message: imapInbound }],
    activeEventRefStore: {},
    ownEmailAddresses: ["user@example.com"],
    nowMs,
  });
  assert.equal(imapTriggers.length, 1);
  assert.deepEqual(imapTriggers[0].request, {
    operation: "lookup_current",
    mailboxId: "imap-mailbox",
    trigger: "incoming_reply",
    incomingLocator: {
      provider: "custom_imap",
      providerFolder: "INBOX",
      uidValidity: "77",
      imapUid: "8",
    },
  });

  const customSmtpWaitingStore: WaitingOnOtherStore = {
    waiting: {
      state: "waiting_on_other",
      mailboxId: "imap-mailbox",
      conversationKey: imapConversationId,
      transitionedAt: "2026-08-21T10:30:00.000Z",
    },
  };
  assert.deepEqual(
    findPrioritySemanticCurrentLookupTriggers({
      waitingStore: customSmtpWaitingStore,
      messageEntries: [{ mailboxId: "imap-mailbox", message: imapInbound }],
      activeEventRefStore: {},
      ownEmailAddresses: ["user@example.com"],
      nowMs,
    }),
    [],
  );
});

test("Trigger B exists only for a newly reconciled exact Gmail returned reply", () => {
  const inbound = applyLiveThreadIdentity(
    {
      id: "ui-message-2",
      providerMessageId: "message-2",
      providerThreadId: "thread-1",
      subject: "Re: Contract",
      from: "external@example.com",
      to: "user@example.com",
      createdAt: "2026-08-21T08:10:00.000Z",
      timestamp: "2026-08-21T08:10:00.000Z",
    },
    {
      mailboxId: "mailbox-1",
      provider: "google",
      folder: "INBOX",
      uidValidity: "gmail-api",
    },
  );
  const conversationId = resolveCanonicalConversationIdentity(
    inbound,
    "mailbox-1",
  ).key;
  const previous: WaitingOnOtherStore = {
    prior: {
      state: "waiting_on_other",
      mailboxId: "mailbox-1",
      conversationKey: conversationId,
      transitionedAt: "2026-08-21T08:00:00.000Z",
    },
  };
  const reconciled: WaitingOnOtherStore = {
    next: {
      state: "returned_reply",
      mailboxId: "mailbox-1",
      conversationKey: conversationId,
      transitionedAt: "2026-08-21T08:00:00.000Z",
      returnedMessageKey: "returned-message:v1:mailbox-1:google:message-2",
      returnedReplyAt: "2026-08-21T08:10:00.000Z",
    },
  };
  assert.deepEqual(
    findPrioritySemanticReturnedReplyTriggers(previous, reconciled, [
      { mailboxId: "mailbox-1", message: inbound },
    ]),
    [
      {
        mailboxId: "mailbox-1",
        conversationId,
        latestTurnId: "message-2",
        returnedMessageKey: "returned-message:v1:mailbox-1:google:message-2",
        incomingLocator: {
          provider: "google",
          providerMessageId: "message-2",
        },
      },
    ],
  );
  assert.deepEqual(
    findPrioritySemanticReturnedReplyTriggers(reconciled, reconciled, [
      { mailboxId: "mailbox-1", message: inbound },
    ]),
    [],
    "hydration or ordinary refresh of an unchanged returned reply must not trigger",
  );
  assert.deepEqual(
    findPrioritySemanticReturnedReplyTriggers({}, reconciled, [
      { mailboxId: "mailbox-1", message: inbound },
    ]),
    [],
    "a returned record without a prior active deterministic state must not trigger",
  );
  assert.deepEqual(
    findPrioritySemanticReturnedReplyTriggers(previous, reconciled, [
      {
        mailboxId: "mailbox-1",
        message: {
          ...inbound,
          threadIdentityContext: {
            ...inbound.threadIdentityContext,
            mailboxId: "mailbox-2",
          },
        },
      },
    ]),
    [],
    "a conflicting attached mailbox context must fail closed",
  );
});

test("Trigger B emits the exact IMAP locator and normalized RFC latest-turn identity", () => {
  const inbound = applyLiveThreadIdentity(
    {
      id: "imap-ui-8",
      threadId: "imap:rfc:mailbox-1:root%40example.com",
      imapUid: "8",
      rfcMessageId: "<reply-8@example.com>",
      subject: "Re: Artwork",
      from: "external@example.com",
      to: "user@example.com",
      createdAt: "2026-08-21T09:10:00.000Z",
      timestamp: "2026-08-21T09:10:00.000Z",
      providerFolder: "INBOX",
      uidValidity: "77",
    },
    {
      mailboxId: "mailbox-1",
      provider: "custom_imap",
      folder: "INBOX",
      uidValidity: "77",
    },
  );
  const conversationId = resolveCanonicalConversationIdentity(
    inbound,
    "mailbox-1",
  ).key;
  const previous: WaitingOnOtherStore = {
    prior: {
      state: "waiting_on_other",
      mailboxId: "mailbox-1",
      conversationKey: conversationId,
      transitionedAt: "2026-08-21T09:00:00.000Z",
    },
  };
  const reconciled: WaitingOnOtherStore = {
    next: {
      state: "returned_reply",
      mailboxId: "mailbox-1",
      conversationKey: conversationId,
      transitionedAt: "2026-08-21T09:00:00.000Z",
      returnedMessageKey: "returned-message:v1:mailbox-1:custom_imap:8",
      returnedReplyAt: "2026-08-21T09:10:00.000Z",
    },
  };
  assert.deepEqual(
    findPrioritySemanticReturnedReplyTriggers(previous, reconciled, [
      { mailboxId: "mailbox-1", message: inbound },
    ])[0],
    {
      mailboxId: "mailbox-1",
      conversationId,
      latestTurnId: "reply-8@example.com",
      returnedMessageKey: "returned-message:v1:mailbox-1:custom_imap:8",
      incomingLocator: {
        provider: "custom_imap",
        providerFolder: "INBOX",
        uidValidity: "77",
        imapUid: "8",
      },
    },
  );
  assert.deepEqual(
    findPrioritySemanticReturnedReplyTriggers(previous, reconciled, [
      {
        mailboxId: "mailbox-1",
        message: { ...inbound, providerFolder: "Archive" },
      },
    ]),
    [],
    "conflicting IMAP folder identity must fail closed",
  );
});

if (failed > 0) {
  console.error(`\n${failed} priority semantic state test${failed === 1 ? "" : "s"} failed.`);
  process.exit(1);
}

console.log(`\n${passed} priority semantic state tests passed.`);
