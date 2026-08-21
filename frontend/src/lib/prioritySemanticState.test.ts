import assert from "node:assert/strict";
import { applyLiveThreadIdentity, resolveCanonicalConversationIdentity } from "./inboxEngine";
import { shouldAllowNormalPriority } from "./normalPriorityGate";
import { resolvePrioritySource } from "./prioritySource";
import {
  PRIORITY_SEMANTIC_AUTHORED_TEXT_MAX_CODE_POINTS,
  PRIORITY_SEMANTIC_PENDING_RETURNED_TRIGGER_MAX_RECORDS,
  PRIORITY_SEMANTIC_REQUESTED_TRIGGER_MAX_KEYS,
  PRIORITY_SEMANTIC_SHADOW_OBSERVATION_MAX_RECORDS,
  SEMANTIC_SCHEMA_VERSION,
  addPrioritySemanticShadowObservation,
  buildPrioritySemanticAssessmentWireRequest,
  findPrioritySemanticReturnedReplyTriggers,
  normalizePrioritySemanticActiveEventRefStore,
  normalizePrioritySemanticAuthoredText,
  parsePrioritySemanticAssessmentResponse,
  persistPrioritySemanticActiveEventRefStore,
  projectPrioritySemanticShadowObservation,
  readPrioritySemanticActiveEventRefStore,
  recordPrioritySemanticActiveEventRef,
  rememberPrioritySemanticPendingReturnedReplyTrigger,
  rememberPrioritySemanticRequestedTriggerKey,
  resolvePrioritySemanticActiveEventRef,
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
      { status: "pending", isShadow: true },
    );
  }
  assert.equal(
    Object.keys(observations).length,
    PRIORITY_SEMANTIC_SHADOW_OBSERVATION_MAX_RECORDS,
  );
  assert.equal("observation-0" in observations, false);

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
