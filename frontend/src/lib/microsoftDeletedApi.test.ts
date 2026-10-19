import assert from "node:assert/strict";
import {fetchMicrosoftDeleted, fetchMicrosoftDeletedMessage} from "./inboxConnectionApi";

const BOX = "microsoft-0123456789abcdefabcd12345678";
const ID = "AAMkDeletedMessage.1/";
const ENTRY = {
  id: ID,
  providerMessageId: ID,
  providerThreadId: "AAQkThread",
  serverMailboxId: BOX,
  providerFolder: "DELETED",
  labelIds: ["DELETED", "UNREAD"],
  sender: "Artist <artist@example.com>",
  subject: "Old demo",
  snippet: "Please review.",
  from: "Artist <artist@example.com>",
  to: "Manager <manager@example.com>",
  cc: "",
  timestamp: "October 14 at 22:00",
  createdAt: "2026-10-14T22:00:00+00:00",
  body: [] as string[],
  unread: true,
  flagged: false,
  attachments: [],
  hasAttachments: true,
};
const LIST = {
  ok: true, messages: [ENTRY], deletedMessageIds: [ID], hasMore: false,
};
const EXACT = {ok: true, message: {...ENTRY, body: ["Deleted message content"]}};

async function test() {
  const original = globalThis.fetch;
  let calls: Array<{url: string; init?: RequestInit}> = [];
  const mock = (response: unknown, status = 200) => {
    calls = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({url, init});
      return {ok: status >= 200 && status < 300, status, json: async () => response} as Response;
    }) as typeof fetch;
  };
  try {
    mock(LIST);
    const page = await fetchMicrosoftDeleted({mailboxId: BOX, limit: 5, page: 2});
    assert.equal(page.ok, true);
    assert.equal(page.messages?.[0].providerFolder, "DELETED");
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft-deleted");
    assert.equal(calls[0].init?.method, "POST");
    assert.equal(calls[0].init?.credentials, "include");
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      mailboxId: BOX, limit: 5, page: 2,
    });

    mock(EXACT);
    const exact = await fetchMicrosoftDeletedMessage({mailboxId: BOX, providerMessageId: ID});
    assert.equal(exact.ok, true);
    assert.equal(exact.message?.body[0], "Deleted message content");
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft-deleted-message");
    mock({...EXACT, message: {...EXACT.message, bodyHtml: "<table><tr><td>Safe HTML</td></tr></table>"}});
    assert.equal((await fetchMicrosoftDeletedMessage({mailboxId: BOX, providerMessageId: ID})).message?.bodyHtml?.includes("Safe HTML"), true);
    mock({...EXACT, message: {...EXACT.message, bodyHtml: '<img src="https://tracker.invalid/pixel">'}});
    assert.equal((await fetchMicrosoftDeletedMessage({mailboxId: BOX, providerMessageId: ID})).ok, false);
    mock({...LIST, messages: [{...ENTRY, bodyHtml: "<strong>unexpected</strong>"}]});
    assert.equal((await fetchMicrosoftDeleted({mailboxId: BOX})).ok, false);


    const outgoing = {
      ...ENTRY, sender: "To: Manager <manager@example.com>", from: "",
      unread: false, labelIds: ["DELETED"],
    };
    mock({...LIST, messages: [outgoing]});
    assert.equal((await fetchMicrosoftDeleted({mailboxId: BOX})).ok, true);

    for (const bad of [
      {...LIST, messages: [{...ENTRY, providerFolder: "INBOX"}]},
      {...LIST, messages: [{...ENTRY, labelIds: ["INBOX", "UNREAD"]}]},
      {...LIST, messages: [{...ENTRY, labelIds: ["DELETED", "INBOX"]}]},
      {...LIST, messages: [{...ENTRY, unread: false}]},
      {...LIST, messages: [{...ENTRY, flagged: true}]},
      {...LIST, messages: [{...ENTRY, serverMailboxId: "other-owner"}]},
      {...LIST, messages: [{...outgoing, sender: "Fake sender"}]},
      {...LIST, deletedMessageIds: ["different"]},
      {...LIST, nextLink: "https://graph.microsoft.com/private"},
    ]) {
      mock(bad);
      assert.equal((await fetchMicrosoftDeleted({mailboxId: BOX})).ok, false);
    }

    for (const bad of [
      {...EXACT, message: {...ENTRY, providerFolder: "ARCHIVE", body: ["Wrong"]}},
      {...EXACT, message: {...ENTRY, body: []}},
      {...EXACT, message: {...ENTRY, id: "wrong", body: ["Wrong"]}},
    ]) {
      mock(bad);
      assert.equal((await fetchMicrosoftDeletedMessage({
        mailboxId: BOX, providerMessageId: ID,
      })).ok, false);
    }
    for (const pageNumber of [0, 5, -1, 1.2, Number.NaN]) {
      mock(LIST);
      assert.equal((await fetchMicrosoftDeleted({mailboxId: BOX, page: pageNumber})).ok, false);
      assert.equal(calls.length, 0);
    }
    for (const id of ["", "../other", "bad?#"]) {
      mock(EXACT);
      assert.equal((await fetchMicrosoftDeletedMessage({
        mailboxId: BOX, providerMessageId: id,
      })).ok, false);
      assert.equal(calls.length, 0);
    }
  } finally {
    globalThis.fetch = original;
  }
  console.log("Microsoft Deleted Items client: owner, folder, paging and response validation PASS");
}
void test();
