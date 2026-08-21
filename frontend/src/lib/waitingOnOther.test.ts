import assert from "node:assert/strict";
import {
  clearConversationWaitingOnOther,
  normalizeWaitingOnOtherStore,
  reconcileWaitingOnOtherStore,
  resolveWaitingReturnedReplyEvidence,
  resolveWaitingOnOtherState,
  selectWaitingOnOtherRepresentatives,
  transitionWaitingOnOtherAfterSend,
  WAITING_ON_OTHER_MAX_INACTIVITY_MS,
  type WaitingOnOtherStore,
} from "./waitingOnOther";
import type {
  LiveThreadIdentityContext,
  RenderedConversationMessage,
  ThreadIdentityAuthority,
} from "./inboxEngine";

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

const transitionTime = "2026-08-21T10:00:00.000Z";
const transitionMs = new Date(transitionTime).getTime();
const ownEmailAddresses = ["me@example.com", "alias@example.com"];

function context(
  mailboxId: string,
  provider: "google" | "custom_imap",
): LiveThreadIdentityContext {
  return {
    mailboxId,
    provider,
    folder: "INBOX",
    uidValidity: provider === "google" ? "gmail-api" : "77",
  };
}

function message(
  overrides: Partial<RenderedConversationMessage> & {
    mailboxId?: string;
    provider?: "google" | "custom_imap";
    authority?: ThreadIdentityAuthority;
  } = {},
): RenderedConversationMessage {
  const mailboxId = overrides.mailboxId ?? "mail-a";
  const provider = overrides.provider ?? "google";
  const authority = overrides.authority ?? (provider === "google" ? "gmail" : "rfc");
  const threadId =
    overrides.threadId ??
    (provider === "google"
      ? `gmail:${mailboxId}:thread-1`
      : `imap:rfc:${mailboxId}:root%40example.com`);

  return {
    id: "incoming-1",
    threadId,
    threadIdentityAuthority: authority,
    threadIdentityContext: context(mailboxId, provider),
    subject: "Re: Licensing question",
    from: "partner@example.com",
    to: "me@example.com",
    sender: "Partner",
    createdAt: "2026-08-21T09:00:00.000Z",
    timestamp: "2026-08-21T09:00:00.000Z",
    ...overrides,
  };
}

function replyTransition(
  source = message(),
  store: WaitingOnOtherStore = {},
  composeMode: "reply" | "reply_all" = "reply",
) {
  return transitionWaitingOnOtherAfterSend(store, {
    mailboxId: source.threadIdentityContext?.mailboxId ?? "mail-a",
    message: source,
    composeMode,
    sendSucceeded: true,
    transitionedAt: transitionTime,
  });
}

console.log("\nwaitingOnOther");

test("Gmail incoming -> successful Reply -> waiting_on_other", () => {
  const source = message();
  const store = replyTransition(source);
  const state = resolveWaitingOnOtherState(store, "mail-a", source, transitionMs);

  assert.equal(state?.state, "waiting_on_other");
  assert.equal(state?.mailboxId, "mail-a");
  assert.match(state?.conversationKey ?? "", /gmail/);
});

test("custom IMAP RFC ancestry -> successful Reply -> waiting_on_other", () => {
  const source = message({ provider: "custom_imap", authority: "rfc" });
  const store = replyTransition(source);

  assert.equal(
    resolveWaitingOnOtherState(store, "mail-a", source, transitionMs)?.state,
    "waiting_on_other",
  );
});

test("unsafe heuristic identity fails closed", () => {
  const source = message({ threadId: "licensing question", authority: "heuristic" });
  assert.deepEqual(replyTransition(source), {});
});

test("conflicting mailbox context fails closed", () => {
  const source = message({ mailboxId: "mail-a" });
  assert.deepEqual(
    transitionWaitingOnOtherAfterSend({}, {
      mailboxId: "mail-b",
      message: source,
      composeMode: "reply",
      sendSucceeded: true,
      transitionedAt: transitionTime,
    }),
    {},
  );
});

test("same waiting thread is represented exactly once", () => {
  const source = message();
  const olderCopy = message({
    id: "incoming-older",
    createdAt: "2026-08-20T09:00:00.000Z",
  });
  const representatives = selectWaitingOnOtherRepresentatives(
    replyTransition(source),
    [
      { mailboxId: "mail-a", message: olderCopy },
      { mailboxId: "mail-a", message: source },
      { mailboxId: "mail-a", message: source },
    ],
    transitionMs,
  );

  assert.equal(representatives.length, 1);
  assert.equal(representatives[0].message.id, "incoming-1");
});

test("serialized state reconstructs after refresh", () => {
  const source = message();
  const stored = JSON.parse(JSON.stringify(replyTransition(source)));
  const reconstructed = normalizeWaitingOnOtherStore(stored, transitionMs);

  assert.equal(
    resolveWaitingOnOtherState(reconstructed, "mail-a", source, transitionMs)?.state,
    "waiting_on_other",
  );
});

test("newer external inbound supersedes waiting_on_other", () => {
  const source = message();
  const returnedReply = message({
    id: "incoming-2",
    createdAt: "2026-08-21T11:00:00.000Z",
    timestamp: "2026-08-21T11:00:00.000Z",
  });
  const reconciled = reconcileWaitingOnOtherStore(
    replyTransition(source),
    [{ mailboxId: "mail-a", message: returnedReply }],
    {
      ownEmailAddresses,
      nowMs: new Date("2026-08-21T11:01:00.000Z").getTime(),
    },
  );

  assert.equal(resolveWaitingOnOtherState(reconciled, "mail-a", returnedReply), null);
  assert.equal(
    resolveWaitingReturnedReplyEvidence(
      reconciled,
      "mail-a",
      returnedReply,
      ownEmailAddresses,
      new Date("2026-08-21T11:01:00.000Z").getTime(),
    )?.confidence,
    "high",
  );
});

test("older inbound does not clear the post-reply waiting state", () => {
  const source = message();
  const store = replyTransition(source);
  assert.deepEqual(
    reconcileWaitingOnOtherStore(
      store,
      [{ mailboxId: "mail-a", message: source }],
      { ownEmailAddresses, nowMs: transitionMs },
    ),
    store,
  );
});

test("Gmail returned-reply evidence survives refresh without local Sent history", () => {
  const source = message();
  const returnedReply = message({
    id: "gmail-returned-1",
    providerMessageId: "gmail-provider-message-1",
    createdAt: "2026-08-21T11:00:00.000Z",
    timestamp: "2026-08-21T11:00:00.000Z",
  });
  const reconciled = reconcileWaitingOnOtherStore(
    replyTransition(source),
    [{ mailboxId: "mail-a", message: returnedReply }],
    { ownEmailAddresses, nowMs: new Date(returnedReply.createdAt!).getTime() },
  );
  const refreshed = normalizeWaitingOnOtherStore(
    JSON.parse(JSON.stringify(reconciled)),
    new Date(returnedReply.createdAt!).getTime(),
  );

  const evidence = resolveWaitingReturnedReplyEvidence(
    refreshed,
    "mail-a",
    returnedReply,
    ownEmailAddresses,
    new Date(returnedReply.createdAt!).getTime(),
  );
  assert.equal(evidence?.hasEvidence, true);
  assert.equal(evidence?.lastUserReplyAt, transitionTime);
  assert.equal(evidence?.returnedReplyAt, returnedReply.createdAt);
});

test("custom IMAP RFC-root return becomes high-confidence evidence", () => {
  const source = message({ provider: "custom_imap", authority: "rfc" });
  const returnedReply = message({
    id: "imap-returned-1",
    imapUid: "102",
    provider: "custom_imap",
    authority: "rfc",
    createdAt: "2026-08-21T11:00:00.000Z",
    timestamp: "2026-08-21T11:00:00.000Z",
  });
  const reconciled = reconcileWaitingOnOtherStore(
    replyTransition(source),
    [{ mailboxId: "mail-a", message: returnedReply }],
    { ownEmailAddresses, nowMs: new Date(returnedReply.createdAt!).getTime() },
  );

  assert.equal(
    resolveWaitingReturnedReplyEvidence(
      reconciled,
      "mail-a",
      returnedReply,
      ownEmailAddresses,
      new Date(returnedReply.createdAt!).getTime(),
    )?.confidence,
    "high",
  );
});

for (const unsafeSource of [
  message({ threadId: "licensing question", authority: "heuristic" }),
  message({ threadId: undefined, authority: "heuristic", subject: "Re: Licensing question" }),
]) {
  test(`unsafe ${unsafeSource.threadId ? "subject-derived identity" : "Re: alone"} cannot transition to returned_reply`, () => {
    const waitingStore = replyTransition(unsafeSource);
    const newerInbound = {
      ...unsafeSource,
      id: "unsafe-returned",
      createdAt: "2026-08-21T11:00:00.000Z",
      timestamp: "2026-08-21T11:00:00.000Z",
    };

    assert.deepEqual(waitingStore, {});
    assert.deepEqual(
      reconcileWaitingOnOtherStore(
        waitingStore,
        [{ mailboxId: "mail-a", message: newerInbound }],
        { ownEmailAddresses, nowMs: new Date(newerInbound.createdAt).getTime() },
      ),
      {},
    );
  });
}

test("equal timestamp ambiguity cannot satisfy waiting", () => {
  const source = message();
  const equalTimestampInbound = message({
    id: "equal-time",
    createdAt: transitionTime,
    timestamp: transitionTime,
  });
  const store = replyTransition(source);

  assert.deepEqual(
    reconcileWaitingOnOtherStore(
      store,
      [{ mailboxId: "mail-a", message: equalTimestampInbound }],
      { ownEmailAddresses, nowMs: transitionMs },
    ),
    store,
  );
});

test("two distinct newest inbounds with equal timestamps fail conservatively", () => {
  const source = message();
  const firstInbound = message({
    id: "equal-newest-a",
    providerMessageId: "provider-a",
    createdAt: "2026-08-21T11:00:00.000Z",
    timestamp: "2026-08-21T11:00:00.000Z",
  });
  const secondInbound = message({
    id: "equal-newest-b",
    providerMessageId: "provider-b",
    createdAt: firstInbound.createdAt,
    timestamp: firstInbound.timestamp,
  });
  const store = replyTransition(source);

  assert.deepEqual(
    reconcileWaitingOnOtherStore(
      store,
      [
        { mailboxId: "mail-a", message: firstInbound },
        { mailboxId: "mail-a", message: secondInbound },
      ],
      {
        ownEmailAddresses,
        nowMs: new Date(firstInbound.createdAt!).getTime(),
      },
    ),
    store,
  );
});

for (const from of ["Me <me@example.com>", "alias@example.com"] as const) {
  test(`owned sender ${from} cannot satisfy waiting`, () => {
    const source = message();
    const ownCopy = message({
      id: `own-${from}`,
      from,
      createdAt: "2026-08-21T11:00:00.000Z",
      timestamp: "2026-08-21T11:00:00.000Z",
    });
    const store = replyTransition(source);

    assert.deepEqual(
      reconcileWaitingOnOtherStore(
        store,
        [{ mailboxId: "mail-a", message: ownCopy }],
        { ownEmailAddresses, nowMs: new Date(ownCopy.createdAt!).getTime() },
      ),
      store,
    );
  });
}

test("missing or malformed sender identity cannot satisfy waiting", () => {
  const source = message();
  const malformedInbound = message({
    id: "missing-sender",
    from: "not-an-email",
    sender: "",
    createdAt: "2026-08-21T11:00:00.000Z",
    timestamp: "2026-08-21T11:00:00.000Z",
  });
  const store = replyTransition(source);

  assert.deepEqual(
    reconcileWaitingOnOtherStore(
      store,
      [{ mailboxId: "mail-a", message: malformedInbound }],
      { ownEmailAddresses, nowMs: new Date(malformedInbound.createdAt!).getTime() },
    ),
    store,
  );
});

test("cross-mailbox authoritative thread collision cannot return another mailbox", () => {
  const waitingSource = message({ mailboxId: "mail-a", id: "collision" });
  const wrongMailboxInbound = message({
    mailboxId: "mail-b",
    id: "collision",
    threadId: "gmail:mail-a:thread-1",
    createdAt: "2026-08-21T11:00:00.000Z",
    timestamp: "2026-08-21T11:00:00.000Z",
  });
  const store = replyTransition(waitingSource);

  assert.deepEqual(
    reconcileWaitingOnOtherStore(
      store,
      [{ mailboxId: "mail-b", message: wrongMailboxInbound }],
      { ownEmailAddresses, nowMs: new Date(wrongMailboxInbound.createdAt!).getTime() },
    ),
    store,
  );
});

test("duplicate provider delivery is idempotent and retains one conversation record", () => {
  const source = message();
  const returnedReply = message({
    id: "gmail-returned-duplicate",
    providerMessageId: "provider-returned-duplicate",
    createdAt: "2026-08-21T11:00:00.000Z",
    timestamp: "2026-08-21T11:00:00.000Z",
  });
  const options = {
    ownEmailAddresses,
    nowMs: new Date(returnedReply.createdAt!).getTime(),
  };
  const once = reconcileWaitingOnOtherStore(
    replyTransition(source),
    [{ mailboxId: "mail-a", message: returnedReply }],
    options,
  );
  const duplicated = reconcileWaitingOnOtherStore(
    once,
    [
      { mailboxId: "mail-a", message: returnedReply },
      { mailboxId: "mail-a", message: { ...returnedReply } },
    ],
    options,
  );

  assert.deepEqual(duplicated, once);
  assert.equal(Object.keys(duplicated).length, 1);
});

test("only the persisted returned inbound receives transition evidence", () => {
  const source = message();
  const returnedReply = message({
    id: "returned-current",
    createdAt: "2026-08-21T11:00:00.000Z",
    timestamp: "2026-08-21T11:00:00.000Z",
  });
  const otherThreadMessage = message({
    id: "other-same-thread",
    createdAt: "2026-08-21T10:30:00.000Z",
    timestamp: "2026-08-21T10:30:00.000Z",
  });
  const nowMs = new Date(returnedReply.createdAt!).getTime();
  const reconciled = reconcileWaitingOnOtherStore(
    replyTransition(source),
    [{ mailboxId: "mail-a", message: returnedReply }],
    { ownEmailAddresses, nowMs },
  );

  assert.equal(
    resolveWaitingReturnedReplyEvidence(
      reconciled,
      "mail-a",
      returnedReply,
      ownEmailAddresses,
      nowMs,
    )?.hasEvidence,
    true,
  );
  assert.equal(
    resolveWaitingReturnedReplyEvidence(
      reconciled,
      "mail-a",
      otherThreadMessage,
      ownEmailAddresses,
      nowMs,
    ),
    null,
  );
});

test("reading a returned inbound does not remove its evidence", () => {
  const source = message();
  const returnedReply = message({
    id: "returned-read",
    createdAt: "2026-08-21T11:00:00.000Z",
    timestamp: "2026-08-21T11:00:00.000Z",
  });
  const nowMs = new Date(returnedReply.createdAt!).getTime();
  const reconciled = reconcileWaitingOnOtherStore(
    replyTransition(source),
    [{ mailboxId: "mail-a", message: returnedReply }],
    { ownEmailAddresses, nowMs },
  );

  assert.equal(
    resolveWaitingReturnedReplyEvidence(
      reconciled,
      "mail-a",
      { ...returnedReply, unread: false },
      ownEmailAddresses,
      nowMs,
    )?.hasEvidence,
    true,
  );
});

test("Done or Remove clears persisted returned-reply evidence", () => {
  const source = message();
  const returnedReply = message({
    id: "returned-cleared",
    createdAt: "2026-08-21T11:00:00.000Z",
    timestamp: "2026-08-21T11:00:00.000Z",
  });
  const nowMs = new Date(returnedReply.createdAt!).getTime();
  const reconciled = reconcileWaitingOnOtherStore(
    replyTransition(source),
    [{ mailboxId: "mail-a", message: returnedReply }],
    { ownEmailAddresses, nowMs },
  );

  assert.deepEqual(
    clearConversationWaitingOnOther(reconciled, {
      mailboxId: "mail-a",
      message: returnedReply,
    }),
    {},
  );
});

for (const action of ["Mark as done", "Remove from Priority"] as const) {
  test(`${action} clears the conversation state`, () => {
    const source = message();
    const cleared = clearConversationWaitingOnOther(replyTransition(source), {
      mailboxId: "mail-a",
      message: source,
    });
    assert.deepEqual(cleared, {});
  });
}

test("opening/reading the representative does not clear waiting", () => {
  const source = message();
  const readSource = { ...source, unread: false };
  assert.equal(
    resolveWaitingOnOtherState(replyTransition(source), "mail-a", readSource, transitionMs)
      ?.state,
    "waiting_on_other",
  );
});

for (const composeMode of ["new", "forward"] as const) {
  test(`${composeMode} send does not create waiting state`, () => {
    assert.deepEqual(
      transitionWaitingOnOtherAfterSend({}, {
        mailboxId: "mail-a",
        message: message(),
        composeMode,
        sendSucceeded: true,
        transitionedAt: transitionTime,
      }),
      {},
    );
  });
}

test("failed Reply does not create waiting state", () => {
  assert.deepEqual(
    transitionWaitingOnOtherAfterSend({}, {
      mailboxId: "mail-a",
      message: message(),
      composeMode: "reply",
      sendSucceeded: false,
      transitionedAt: transitionTime,
    }),
    {},
  );
});

test("Reply All creates the same waiting state as Reply", () => {
  assert.deepEqual(replyTransition(message(), {}, "reply_all"), replyTransition());
});

test("colliding raw message ids remain isolated by mailbox and provider thread", () => {
  const first = message({ mailboxId: "mail-a", id: "collision" });
  const second = message({ mailboxId: "mail-b", id: "collision" });
  const store = replyTransition(second, replyTransition(first));

  assert.equal(Object.keys(store).length, 2);
  assert.equal(
    resolveWaitingOnOtherState(store, "mail-a", first, transitionMs)?.mailboxId,
    "mail-a",
  );
  assert.equal(
    resolveWaitingOnOtherState(store, "mail-b", second, transitionMs)?.mailboxId,
    "mail-b",
  );
});

test("expired waiting state is not active Priority evidence", () => {
  const source = message();
  const store = replyTransition(source);
  const expiredNow = transitionMs + WAITING_ON_OTHER_MAX_INACTIVITY_MS + 1;

  assert.equal(resolveWaitingOnOtherState(store, "mail-a", source, expiredNow), null);
  assert.deepEqual(normalizeWaitingOnOtherStore(store, expiredNow), {});
});

test("ordinary Sent messages cannot create waiting state", () => {
  const sent = message({ id: "sent-ordinary", from: "me@example.com", signal: "Sent" });
  assert.deepEqual(
    transitionWaitingOnOtherAfterSend({}, {
      mailboxId: "mail-a",
      message: sent,
      composeMode: "new",
      sendSucceeded: true,
      transitionedAt: transitionTime,
    }),
    {},
  );
});

assert.equal(
  WAITING_ON_OTHER_MAX_INACTIVITY_MS,
  14 * 24 * 60 * 60 * 1000,
  "waiting_on_other must reuse the 14-day Priority inactivity contract",
);

if (failed > 0) {
  console.error(`\n${failed} waitingOnOther test${failed === 1 ? "" : "s"} failed.`);
  process.exit(1);
}

console.log(`\n${passed} waitingOnOther tests passed.`);
