import assert from "node:assert/strict";
import {fetchMicrosoftDrafts, fetchMicrosoftDraftMessage} from "./inboxConnectionApi";

const BOX = "microsoft-0123456789abcdefabcd12345678";
const ID = "AAMkDraftMessage.1/";
const draft = {
  id: ID, providerMessageId: ID, providerThreadId: "AAQkThread",
  serverMailboxId: BOX, providerFolder: "DRAFTS",
  labelIds: ["DRAFTS"], sender: "To: Artist <artist@example.com>",
  subject: "", snippet: "",
  from: "", to: "Artist <artist@example.com>", cc: "",
  timestamp: "October 14 at 21:00",
  createdAt: "2026-10-14T21:00:00+00:00",
  body: [] as string[], unread: false, flagged: false,
  attachments: [], hasAttachments: false,
};
const LIST = {ok: true, messages: [draft], draftMessageIds: [ID], hasMore: false};
const EXACT = {ok: true, message: {...draft, body: ["Unsent message."]}};

async function test() {
  const realFetch = globalThis.fetch;
  let calls: Array<{url: string; init?: RequestInit}> = [];
  const mock = (payload: unknown, status = 200) => {
    calls = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({url, init});
      return {ok: status >= 200 && status < 300, status, json: async () => payload} as Response;
    }) as typeof fetch;
  };
  try {
    mock(LIST);
    const first = await fetchMicrosoftDrafts({mailboxId: BOX, limit: 5, page: 2});
    assert.equal(first.ok, true);
    assert.equal(first.messages?.[0].providerFolder, "DRAFTS");
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft-drafts");
    assert.equal(calls[0].init?.credentials, "include");
    assert.equal(calls[0].init?.method, "POST");
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      mailboxId: BOX, limit: 5, page: 2,
    });
    mock(EXACT);
    const exact = await fetchMicrosoftDraftMessage({mailboxId: BOX, providerMessageId: ID});
    assert.equal(exact.ok, true);
    assert.equal(exact.message?.body[0], "Unsent message.");
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft-draft-message");

    for (const bad of [
      {...LIST, messages: [{...draft, providerFolder: "INBOX"}]},
      {...LIST, messages: [{...draft, labelIds: ["INBOX"]}]},
      {...LIST, messages: [{...draft, labelIds: ["DRAFTS", "INBOX"]}]},
      {...LIST, messages: [{...draft, unread: true}]},
      {...LIST, messages: [{...draft, flagged: true}]},
      {...LIST, messages: [{...draft, from: "other@example.com"}]},
      {...LIST, messages: [{...draft, serverMailboxId: "other-owner"}]},
      {...LIST, draftMessageIds: ["different"]},
      {...LIST, nextLink: "https://graph.microsoft.com/private"},
    ]) {
      mock(bad);
      assert.equal((await fetchMicrosoftDrafts({mailboxId: BOX})).ok, false);
    }
    for (const bad of [
      {...EXACT, message: {...draft, providerFolder: "SENT", body: ["Invalid"]}},
      {...EXACT, message: {...draft, body: []}},
      {...EXACT, message: {...draft, id: "different", body: ["Invalid"]}},
    ]) {
      mock(bad);
      assert.equal((await fetchMicrosoftDraftMessage({
        mailboxId: BOX, providerMessageId: ID,
      })).ok, false);
    }
    for (const page of [0, 5, -1, 1.2, Number.NaN]) {
      mock(LIST);
      assert.equal((await fetchMicrosoftDrafts({mailboxId: BOX, page})).ok, false);
      assert.equal(calls.length, 0);
    }
    for (const id of ["", "../other", "bad?#"]) {
      mock(EXACT);
      assert.equal((await fetchMicrosoftDraftMessage({
        mailboxId: BOX, providerMessageId: id,
      })).ok, false);
      assert.equal(calls.length, 0);
    }
  } finally {
    globalThis.fetch = realFetch;
  }
  console.log("Microsoft Drafts client strict identity and pagination tests: PASS");
}
void test();
