import assert from "node:assert/strict";
import { checkMicrosoftSentStatus } from "./inboxConnectionApi";

const MAILBOX = "microsoft-0123456789abcdefabcd12345678";
const ATTEMPT = "a".repeat(43);
const INPUT = { mailboxId: MAILBOX, attemptId: ATTEMPT };

async function run() {
  const original = globalThis.fetch;
  let requests: Array<{ url: string; init?: RequestInit }> = [];
  const simulate = (payload: unknown, status = 200) => {
    requests = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      requests.push({url, init});
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => payload,
      } as Response;
    }) as typeof fetch;
  };

  const confirmed = {
    ok: true, attemptId: ATTEMPT,
    status: "sent_copy_confirmed", sentCopyConfirmed: true,
    deliveryConfirmed: false,
  };
  try {
    simulate(confirmed);
    const result = await checkMicrosoftSentStatus(INPUT);
    assert.deepEqual(result, confirmed);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "/api/inboxes/check-microsoft-sent-status");
    assert.equal(requests[0].init?.method, "POST");
    assert.equal(requests[0].init?.credentials, "include");
    assert.deepEqual(JSON.parse(String(requests[0].init?.body)), INPUT);
    assert.equal(JSON.stringify(result).includes("draftId"), false);

    for (const status of (["sent_copy_pending", "send_not_started"] as const)) {
      simulate({...confirmed, status, sentCopyConfirmed:false});
      const reply = await checkMicrosoftSentStatus(INPUT);
      assert.equal(reply.ok, true);
      if (reply.ok) assert.equal(reply.sentCopyConfirmed, false);
    }

    for (const corrupted of [
      {...confirmed, attemptId: "swapped"},
      {...confirmed, status: "sent_copy_pending"},
      {...confirmed, status: "delivered"},
      {...confirmed, sentCopyConfirmed: "true"},
      {...confirmed, deliveryConfirmed: true},
      {...confirmed, ok: false},
      null,
    ]) {
      simulate(corrupted);
      const reply = await checkMicrosoftSentStatus(INPUT);
      assert.equal(reply.ok, false);
      assert.equal(reply.error?.code, "microsoft_sent_copy_unavailable");
    }

    simulate({ok:false, error:{
      code: "microsoft_reconnect_required", message: "secret=private",
    }}, 409);
    const revoked = await checkMicrosoftSentStatus(INPUT);
    assert.equal(revoked.ok, false);
    assert.equal(revoked.error?.code, "microsoft_reconnect_required");
    assert.doesNotMatch(JSON.stringify(revoked), /secret=private/);

    simulate({ok:false, error:{
      code: "microsoft_sent_copy_unavailable", message: "private header token",
    }}, 503);
    const unavailable = await checkMicrosoftSentStatus(INPUT);
    assert.equal(unavailable.error?.code, "microsoft_sent_copy_unavailable");
    assert.doesNotMatch(JSON.stringify(unavailable), /private header/);

    simulate(confirmed);
    for (const invalid of [
      {mailboxId:"../outside", attemptId: ATTEMPT},
      {mailboxId:MAILBOX, attemptId:"short"},
      {mailboxId:MAILBOX, attemptId:"a".repeat(44)},
      {mailboxId:MAILBOX, attemptId:"a".repeat(43)+"?"},
    ]) {
      const reply = await checkMicrosoftSentStatus(invalid);
      assert.equal(reply.ok, false);
    }
    assert.equal(requests.length, 0);

    requests=[];
    globalThis.fetch = (async () => {
      requests.push({url:"requested"});
      throw Error("private network failure");
    }) as typeof fetch;
    const lost = await checkMicrosoftSentStatus(INPUT);
    assert.equal(lost.ok, false);
    assert.equal(lost.error?.code, "microsoft_sent_copy_unavailable");
    assert.equal(requests.length, 1, "status check is one read request, not a retry loop");
    assert.doesNotMatch(JSON.stringify(lost), /private network/);

    console.log("✓ Microsoft read-only Sent-copy status client tests passed.");
  } finally {
    globalThis.fetch = original;
  }
}

void run().catch(error => {console.error(error); process.exitCode=1;});
