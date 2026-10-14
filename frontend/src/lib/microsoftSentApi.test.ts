import assert from "node:assert/strict";
import { fetchMicrosoftSent, fetchMicrosoftSentMessage } from "./inboxConnectionApi";

const BOX = "microsoft-0123456789abcdefabcd12345678";
const ID = "AAMkAGVm.987/";
const ROW = {
  id: ID,
  providerMessageId: ID,
  providerThreadId: "AAQkABc123",
  serverMailboxId: BOX,
  providerFolder: "SENT",
  labelIds: ["SENT"],
  sender: "To: Manager <manager@example.com>",
  subject: "Confirmed release",
  snippet: "Thank you!",
  from: "DJ <dj@example.com>",
  to: "Manager <manager@example.com>",
  cc: "",
  timestamp: "October 9 at 15:00",
  createdAt: "2026-10-09T15:00:00+00:00",
  body: [] as string[],
  unread: false,
  flagged: false,
  attachments: [],
};
const LIST = {
  ok: true, messages: [ROW],
  sentMessageIds: [ID], hasMore: false,
};
const EXACT = {
  ok: true,
  message: { ...ROW, body: ["Sent message content"] },
};

async function run() {
  const original = globalThis.fetch;
  let calls: Array<{ url: string; init?: RequestInit }> = [];
  const fake = (data: unknown, status = 200) => {
    calls = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => data,
      } as Response;
    }) as typeof fetch;
  };
  try {
    fake(LIST);
    const sent = await fetchMicrosoftSent({ mailboxId: BOX, limit: 5 });
    assert.equal(sent.ok, true);
    assert.equal(sent.messages?.[0]?.providerFolder, "SENT");
    assert.equal(sent.messages?.[0]?.sender, "To: Manager <manager@example.com>");
    assert.equal(sent.hasMore, false);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft-sent");
    assert.equal(calls[0].init?.credentials, "include");
    assert.equal(calls[0].init?.method, "POST");
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { mailboxId: BOX, limit: 5 });

    fake(LIST);
    const olderSent = await fetchMicrosoftSent({ mailboxId: BOX, limit: 5, page: 3 });
    assert.equal(olderSent.ok, true);
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      mailboxId: BOX, limit: 5, page: 3,
    });
    for (const page of [0, 5, 2.2, Number.NaN, Number.POSITIVE_INFINITY]) {
      fake(LIST);
      const rejected = await fetchMicrosoftSent({ mailboxId: BOX, page });
      assert.equal(rejected.ok, false);
      assert.equal(calls.length, 0);
    }

    fake(EXACT);
    const exact = await fetchMicrosoftSentMessage({ mailboxId: BOX, providerMessageId: ID });
    assert.equal(exact.ok, true);
    assert.deepEqual(exact.message?.body, ["Sent message content"]);
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft-sent-message");

    for (const data of (
      [
        { ...LIST, messages: [{ ...ROW, providerFolder: "INBOX" }] },
        { ...LIST, messages: [{ ...ROW, labelIds: ["INBOX"] }] },
        { ...LIST, messages: [{ ...ROW, unread: true }] },
        { ...LIST, messages: [{ ...ROW, id: "bad?#", providerMessageId: "bad?#" }], sentMessageIds: ["bad?#"] },
        { ...LIST, messages: [{ ...ROW, body: ["unproven preview"] }] },
        { ...LIST, messages: [{ ...ROW, access_token: "secret" }] },
        { ...LIST, sentMessageIds: ["swapped-id"] },
        { ...LIST, hasMore: "false" },
        { ...LIST, messages: [ROW, ROW], sentMessageIds: [ID, ID] },
      ]
    )) {
      fake(data);
      assert.equal((await fetchMicrosoftSent({ mailboxId: BOX })).ok, false);
    }

    for (const data of (
      [
        { ...EXACT, message: { ...EXACT.message, id: "another" } },
        { ...EXACT, message: { ...EXACT.message, body: [] } },
        { ...EXACT, message: { ...EXACT.message, unread: true } },
        { ...EXACT, message: { ...EXACT.message, providerFolder: "INBOX" } },
        { ...EXACT, message: { ...EXACT.message, bodyHtml: "<img src='https://track'>" } },
      ]
    )) {
      fake(data);
      assert.equal((await fetchMicrosoftSentMessage({ mailboxId: BOX, providerMessageId: ID })).ok, false);
    }

    fake({ ok: false, error: { code: "microsoft_reconnect_required", message: "token=private" } }, 409);
    const reconnect = await fetchMicrosoftSent({ mailboxId: BOX });
    assert.equal(reconnect.ok, false);
    assert.equal(reconnect.error?.code, "microsoft_reconnect_required");
    assert.doesNotMatch(JSON.stringify(reconnect), /token=private/);

    fake(LIST);
    for (const limit of [0, -1, 26, "5" as unknown as number, true as unknown as number]) {
      assert.equal((await fetchMicrosoftSent({ mailboxId: BOX, limit })).ok, false);
    }
    assert.equal((await fetchMicrosoftSent({ mailboxId: "../other" })).ok, false);
    assert.equal(calls.length, 0);

    fake(EXACT);
    for (const id of ["", ".", "..", "../inbox", "bad?#fragment", "bad space", "x".repeat(513)]) {
      assert.equal((await fetchMicrosoftSentMessage({ mailboxId: BOX, providerMessageId: id })).ok, false);
    }
    assert.equal(calls.length, 0);

    globalThis.fetch = (async () => { throw new Error("private transport data"); }) as typeof fetch;
    const outage = await fetchMicrosoftSent({ mailboxId: BOX });
    assert.equal(outage.ok, false);
    assert.doesNotMatch(JSON.stringify(outage), /private transport data/);

    console.log("✓ Microsoft Sent client security and folder isolation tests passed.");
  } finally {
    globalThis.fetch = original;
  }
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
