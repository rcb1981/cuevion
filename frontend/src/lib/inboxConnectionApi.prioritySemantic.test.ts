import assert from "node:assert/strict";
import {
  PRIORITY_SEMANTIC_ASSESSMENT_TIMEOUT_MS,
  connectInboxWithImap,
  fetchGmailInbox,
  requestPrioritySemanticAssessment,
  requestPrioritySemanticNewInboundAssessment,
  requestPrioritySemanticNewInboundHydration,
  sendGmailMessage,
} from "./inboxConnectionApi";
import {
  SEMANTIC_SCHEMA_VERSION,
  type PrioritySemanticAssessmentRequest,
  type PrioritySemanticCurrentLookupRequest,
} from "./prioritySemanticState";

const originalFetch = globalThis.fetch;
const originalWindow = (globalThis as { window?: unknown }).window;
const fetchCalls: Array<{ url: string; init?: RequestInit }> = [];

function response(payload: unknown, options?: { ok?: boolean; status?: number }) {
  return {
    ok: options?.ok ?? true,
    status: options?.status ?? 200,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  } as Response;
}

async function run() {
  assert.equal(PRIORITY_SEMANTIC_ASSESSMENT_TIMEOUT_MS, 60_000);
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    fetchCalls.push({ url, init });
    return response({
      ok: true,
      status: "assessed",
      semanticMode: "active",
      priorityEffect: "suppress_automatic_open_loop",
      assessment: {
        state: "resolved",
        confidence: 0.99,
        reasonCode: "completed_confirmation",
      },
      effectiveSemanticState: "resolved",
      identity: {
        mailboxId: "mailbox-1",
        conversationId: "thread:mailbox-1|gmail:mailbox-1:thread-1",
        latestTurnId: "message-2",
        semanticVersion: SEMANTIC_SCHEMA_VERSION,
      },
      activeEventRef: "pse1.ticket.signature",
      assessedAt: "2026-08-21T08:30:00.000Z",
    });
  }) as typeof fetch;

  const outgoingRequest = {
    mailboxId: "mailbox-1",
    trigger: "outgoing_reply",
    eventRef: "pse1.ticket.signature",
    authoredText: "  Done.\r\nThanks.  ",
  } as const;
  const outgoingResponse = await requestPrioritySemanticAssessment(
    outgoingRequest,
  );
  assert.equal(outgoingResponse.ok, true);
  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].url, "/api/priority/semantic-assessment");
  assert.equal(fetchCalls[0].init?.method, "POST");
  assert.equal(fetchCalls[0].init?.credentials, "include");
  assert.equal(fetchCalls[0].init?.cache, "no-store");
  assert.deepEqual(fetchCalls[0].init?.headers, {
    "Content-Type": "application/json",
  });
  assert.equal(
    fetchCalls[0].init?.body,
    JSON.stringify({
      ...outgoingRequest,
      authoredText: "Done.\nThanks.",
    }),
  );

  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    fetchCalls.push({ url, init });
    return response({
      ok: true,
      status: "deferred",
      semanticMode: "active",
      priorityEffect: "observe_only",
      identity: {
        mailboxId: "mailbox-1",
        conversationId: "thread:mailbox-1|gmail:mailbox-1:thread-1",
        latestTurnId: "message-3",
        semanticVersion: SEMANTIC_SCHEMA_VERSION,
      },
      retryAfterSeconds: 300,
    }, { status: 202 });
  }) as typeof fetch;
  const incomingRequest = {
    mailboxId: "mailbox-1",
    trigger: "incoming_reply",
    activeEventRef: "pse1.ticket.signature",
    incomingLocator: {
      provider: "google",
      providerMessageId: "message-3",
    },
  } as const;
  assert.equal(
    (await requestPrioritySemanticAssessment(incomingRequest)).ok,
    true,
  );
  assert.equal(fetchCalls.length, 2);
  assert.equal(fetchCalls[1].init?.body, JSON.stringify(incomingRequest));
  assert.doesNotMatch(String(fetchCalls[1].init?.body), /text|subject|workspace/i);

  const customIncomingRequest = {
    mailboxId: "mailbox-1",
    trigger: "incoming_reply",
    incomingLocator: {
      provider: "custom_imap",
      providerFolder: "INBOX",
      uidValidity: "7",
      imapUid: "9",
    },
  } as const;
  assert.equal(
    (await requestPrioritySemanticAssessment(customIncomingRequest)).ok,
    true,
  );
  assert.equal(fetchCalls.length, 3);
  assert.equal(fetchCalls[2].init?.body, JSON.stringify(customIncomingRequest));
  assert.doesNotMatch(
    String(fetchCalls[2].init?.body),
    /activeEventRef|text|subject|workspace/i,
  );

  const lookupRequest: PrioritySemanticCurrentLookupRequest = {
    operation: "lookup_current",
    mailboxId: "mailbox-1",
    trigger: "outgoing_reply",
    eventRef: "pse1.ticket.signature",
  };
  assert.equal(
    (await requestPrioritySemanticAssessment(lookupRequest)).ok,
    true,
  );
  assert.equal(fetchCalls.length, 4);
  assert.equal(fetchCalls[3].init?.body, JSON.stringify(lookupRequest));
  assert.doesNotMatch(
    String(fetchCalls[3].init?.body),
    /authoredText|body|subject|workspace/i,
    "cache-only rehydration must not carry message content",
  );

  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    fetchCalls.push({ url, init });
    return response({
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
      identity: {
        mailboxId: "mailbox-1",
        conversationId: "thread:mailbox-1|gmail:thread-new",
        latestTurnId: "message-new",
        semanticVersion: SEMANTIC_SCHEMA_VERSION,
      },
      assessedAt: "2026-08-22T08:30:00.000Z",
    });
  }) as typeof fetch;
  const newInboundRequest = {
    mailboxId: "mailbox-1",
    trigger: "new_inbound",
    incomingLocator: {
      provider: "google",
      providerMessageId: "message-new",
    },
  } as const;
  assert.equal(
    (await requestPrioritySemanticNewInboundAssessment(newInboundRequest)).ok,
    true,
  );
  assert.equal(fetchCalls.length, 5);
  assert.equal(fetchCalls[4].url, "/api/priority/semantic-assessment");
  assert.equal(fetchCalls[4].init?.method, "POST");
  assert.equal(fetchCalls[4].init?.credentials, "include");
  assert.equal(fetchCalls[4].init?.cache, "no-store");
  assert.equal(fetchCalls[4].init?.body, JSON.stringify(newInboundRequest));
  assert.doesNotMatch(
    String(fetchCalls[4].init?.body),
    /text|subject|snippet|workspace|tenant|semanticMode/i,
  );

  const newInboundCallsBeforeInvalid = fetchCalls.length;
  assert.deepEqual(
    await requestPrioritySemanticNewInboundAssessment({
      ...newInboundRequest,
      subject: "must not cross the client boundary",
    } as unknown as typeof newInboundRequest),
    {
      ok: false,
      error: {
        code: "invalid_semantic_request",
        message: "Semantic assessment request is invalid.",
      },
    },
  );
  assert.equal(fetchCalls.length, newInboundCallsBeforeInvalid);

  const hydrationRecord = {
    assessment: {
      state: "needs_user_action",
      confidence: 1,
      reasonCode: "explicit_request",
    },
    effectiveSemanticState: "needs_user_action",
    priorityEffect: "observe_only",
    identity: {
      mailboxId: "mailbox-1",
      conversationId: "thread:mailbox-1|gmail:thread-new",
      latestTurnId: "message-new",
      semanticVersion: SEMANTIC_SCHEMA_VERSION,
    },
    assessedAt: "2026-08-22T08:30:00.000Z",
  } as const;
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    fetchCalls.push({ url, init });
    return response({
      ok: true,
      status: "hydrated",
      semanticTrigger: "new_inbound",
      newInboundMode: "shadow",
      priorityEffect: "observe_only",
      records: [hydrationRecord],
    });
  }) as typeof fetch;
  const hydrationRequest = {
    operation: "hydrate_new_inbound",
    mailboxId: "mailbox-1",
  } as const;
  const hydrationResponse =
    await requestPrioritySemanticNewInboundHydration(hydrationRequest);
  assert.equal(hydrationResponse.ok, true);
  assert.equal(fetchCalls.length, newInboundCallsBeforeInvalid + 1);
  assert.equal(
    fetchCalls.at(-1)?.url,
    "/api/priority/semantic-assessment",
  );
  assert.equal(
    fetchCalls.at(-1)?.init?.body,
    JSON.stringify(hydrationRequest),
  );
  assert.doesNotMatch(
    String(fetchCalls.at(-1)?.init?.body),
    /state|confidence|reason|priorityEffect|conversation|messageId|subject|body/i,
    "hydration sends only its exact mailbox-scoped cache projection request",
  );

  const hydrationCallsBeforeInvalid = fetchCalls.length;
  assert.deepEqual(
    await requestPrioritySemanticNewInboundHydration({
      ...hydrationRequest,
      state: "needs_user_action",
    } as unknown as typeof hydrationRequest),
    {
      ok: false,
      error: {
        code: "invalid_semantic_request",
        message: "Semantic hydration request is invalid.",
      },
    },
  );
  assert.equal(fetchCalls.length, hydrationCallsBeforeInvalid);

  globalThis.fetch = (async () =>
    response({
      ok: true,
      status: "hydrated",
      semanticTrigger: "new_inbound",
      newInboundMode: "shadow",
      priorityEffect: "observe_only",
      records: [
        {
          ...hydrationRecord,
          identity: {
            ...hydrationRecord.identity,
            mailboxId: "mailbox-2",
          },
        },
      ],
    })) as typeof fetch;
  assert.deepEqual(
    await requestPrioritySemanticNewInboundHydration(hydrationRequest),
    {
      ok: false,
      error: {
        code: "invalid_semantic_response",
        message: "Semantic hydration returned an invalid response.",
      },
    },
    "a cross-mailbox record is discarded before reaching runtime state",
  );

  globalThis.fetch = (async () =>
    response({
      ok: true,
      messages: [],
      uidValidity: "7",
      inboxUidSet: [],
      prioritySemanticNewInboundMode: "active",
    })) as typeof fetch;
  assert.equal(
    (
      await connectInboxWithImap({
        mode: "refresh",
        mailboxId: "mailbox-1",
      })
    ).prioritySemanticNewInboundMode,
    "off",
    "IMAP provider refreshes must normalize active/unknown modes to off",
  );

  globalThis.fetch = (async () =>
    response({
      ok: true,
      messages: [],
      prioritySemanticNewInboundMode: "shadow",
    })) as typeof fetch;
  assert.equal(
    (await fetchGmailInbox({ mailboxId: "mailbox-1" }))
      .prioritySemanticNewInboundMode,
    "shadow",
  );

  globalThis.fetch = (async () =>
    response({ ok: true, messages: [] })) as typeof fetch;
  assert.equal(
    (await fetchGmailInbox({ mailboxId: "mailbox-1" }))
      .prioritySemanticNewInboundMode,
    "off",
    "missing Gmail capability metadata must fail closed",
  );

  const callsBeforeInvalidRequest = fetchCalls.length;
  const invalidRequest = {
    ...incomingRequest,
    incomingText: "must not cross the client boundary",
  } as unknown as PrioritySemanticAssessmentRequest;
  assert.deepEqual(await requestPrioritySemanticAssessment(invalidRequest), {
    ok: false,
    error: {
      code: "invalid_semantic_request",
      message: "Semantic assessment request is invalid.",
    },
  });
  assert.equal(fetchCalls.length, callsBeforeInvalidRequest);

  globalThis.fetch = (async () => {
    throw new Error("raw private network detail");
  }) as typeof fetch;
  assert.deepEqual(
    await requestPrioritySemanticAssessment(outgoingRequest),
    {
      ok: false,
      error: {
        code: "semantic_request_failed",
        message: "Semantic assessment could not be reached.",
      },
    },
    "network exceptions must be replaced with a fixed client error",
  );

  (globalThis as { window?: unknown }).window = globalThis;
  globalThis.fetch = (async () =>
    response({
      ok: true,
      providerMessageId: "sent-1",
      providerThreadId: "thread-1",
      threadContinuityConfirmed: true,
      semanticEventRef: "pse1.ticket.signature",
    })) as typeof fetch;
  assert.deepEqual(
    await sendGmailMessage({
      mailboxId: "mailbox-1",
      to: "external@example.com",
      subject: "Reply",
      bodyHtml: "<div>Done.</div>",
      bodyText: "Done.",
      replyContext: { sourceProviderMessageId: "source-1" },
    }),
    {
      ok: true,
      providerMessageId: "sent-1",
      providerThreadId: "thread-1",
      threadContinuityConfirmed: true,
      semanticEventRef: "pse1.ticket.signature",
    },
    "the confirmed send response must retain the server-issued semantic ticket",
  );

  globalThis.fetch = (async () =>
    response({
      ok: true,
      providerMessageId: "hostile-custom-id",
      providerThreadId: "hostile-custom-thread",
      providerIdentityConfirmed: true,
      threadContinuityConfirmed: true,
      semanticEventRef: "pse1.legacy-custom.signature",
    })) as typeof fetch;
  assert.deepEqual(
    await sendGmailMessage({
      mailboxId: "mailbox-1",
      to: "external@example.com",
      subject: "Reply",
      bodyHtml: "<div>Done.</div>",
      bodyText: "Done.",
      imapReplyContext: {
        sourceProviderFolder: "INBOX",
        sourceImapUid: "8",
        sourceUidValidity: "7",
      },
    }),
    { ok: true },
    "custom SMTP must discard hostile or legacy semantic and Gmail identity fields",
  );

  globalThis.fetch = (async () =>
    response({
      ok: true,
      providerIdentityConfirmed: false,
      threadContinuityConfirmed: false,
      semanticEventRef: "pse1.ticket.signature",
    })) as typeof fetch;
  assert.deepEqual(
    await sendGmailMessage({
      mailboxId: "mailbox-1",
      to: "external@example.com",
      subject: "Reply",
      bodyHtml: "<div>Done.</div>",
      bodyText: "Done.",
      replyContext: { sourceProviderMessageId: "source-1" },
    }),
    {
      ok: true,
      providerIdentityConfirmed: false,
      threadContinuityConfirmed: false,
      warning: {
        code: "send_identity_unconfirmed",
        message: "The message was sent, but its provider identity could not be confirmed.",
      },
    },
    "identity-unconfirmed Gmail responses must discard semantic tickets",
  );
}

run()
  .then(() =>
    console.log("\n✓ inboxConnectionApi Priority semantic shadow tests passed."),
  )
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(() => {
    globalThis.fetch = originalFetch;
    if (originalWindow === undefined) {
      delete (globalThis as { window?: unknown }).window;
    } else {
      (globalThis as { window?: unknown }).window = originalWindow;
    }
  });
