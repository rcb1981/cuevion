import assert from "node:assert/strict";
import {
  PRIORITY_SEMANTIC_ASSESSMENT_TIMEOUT_MS,
  requestPrioritySemanticAssessment,
  sendGmailMessage,
} from "./inboxConnectionApi";
import {
  SEMANTIC_SCHEMA_VERSION,
  type PrioritySemanticAssessmentRequest,
} from "./prioritySemanticState";

const originalFetch = globalThis.fetch;
const originalWindow = (globalThis as { window?: unknown }).window;
const fetchCalls: Array<{ url: string; init?: RequestInit }> = [];

function response(payload: unknown, options?: { ok?: boolean; status?: number }) {
  return {
    ok: options?.ok ?? true,
    status: options?.status ?? 200,
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
