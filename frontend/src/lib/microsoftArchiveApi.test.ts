import assert from "node:assert/strict";
import { fetchMicrosoftArchive, fetchMicrosoftArchiveMessage } from "./inboxConnectionApi";

const BOX = "microsoft-0123456789abcdefabcd12345678";
const ID = "AAMkArchiveMessage.1/";
const ENTRY = {
  id: ID,
  providerMessageId: ID,
  providerThreadId: "AAQkThread",
  serverMailboxId: BOX,
  providerFolder: "ARCHIVE",
  labelIds: ["ARCHIVE", "UNREAD"],
  sender: "Archive Sender",
  subject: "Older agreement",
  snippet: "Here is the agreement",
  from: "Archive Sender <sender@example.com>",
  to: "",
  cc: "",
  timestamp: "October 14 at 19:00",
  createdAt: "2026-10-14T19:00:00+00:00",
  body: [] as string[],
  unread: true,
  flagged: false,
  attachments: [],
  hasAttachments: true,
};
const LIST = {
  ok: true, messages: [ENTRY], archiveMessageIds: [ID], hasMore: false,
};
const EXACT = {
  ok: true, message: { ...ENTRY, body: ["Archived full message."] },
};

async function run() {
  const original = globalThis.fetch;
  let calls: Array<{ url: string; init?: RequestInit }> = [];
  const stub = (payload: unknown, code = 200) => {
    calls = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return {
        ok: code >= 200 && code < 300, status: code,
        json: async () => payload,
      } as Response;
    }) as typeof fetch;
  };

  try {
    stub(LIST);
    const listing = await fetchMicrosoftArchive({ mailboxId: BOX, limit: 5, page: 2 });
    assert.equal(listing.ok, true);
    assert.equal(listing.messages?.[0]?.providerFolder, "ARCHIVE");
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft-archive");
    assert.equal(calls[0].init?.method, "POST");
    assert.equal(calls[0].init?.credentials, "include");
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      mailboxId: BOX, limit: 5, page: 2,
    });

    stub(EXACT);
    const exact = await fetchMicrosoftArchiveMessage({
      mailboxId: BOX, providerMessageId: ID,
    });
    assert.equal(exact.ok, true);
    assert.equal(exact.message?.body[0], "Archived full message.");
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft-archive-message");

    for (const bad of [
      { ...LIST, messages: [{ ...ENTRY, providerFolder: "INBOX" }] },
      { ...LIST, messages: [{ ...ENTRY, labelIds: ["INBOX"] }] },
      { ...LIST, messages: [{ ...ENTRY, serverMailboxId: "other-owner" }] },
      { ...LIST, archiveMessageIds: ["wrong"] },
      { ...LIST, nextLink: "https://graph.microsoft.com/private" },
    ]) {
      stub(bad);
      assert.equal((await fetchMicrosoftArchive({ mailboxId: BOX })).ok, false);
    }

    for (const bad of [
      { ...EXACT, message: { ...ENTRY, providerFolder: "INBOX", body: ["Bad"] } },
      { ...EXACT, message: { ...ENTRY, body: [] } },
      { ...EXACT, message: { ...ENTRY, id: "wrong", body: ["Bad"] } },
    ]) {
      stub(bad);
      assert.equal((await fetchMicrosoftArchiveMessage({
        mailboxId: BOX, providerMessageId: ID,
      })).ok, false);
    }

    for (const page of [0, 5, -1, 1.4, Number.NaN]) {
      stub(LIST);
      assert.equal((await fetchMicrosoftArchive({ mailboxId: BOX, page })).ok, false);
      assert.equal(calls.length, 0);
    }
    for (const providerMessageId of ["", "bad?#", "../parent"]) {
      stub(EXACT);
      assert.equal((await fetchMicrosoftArchiveMessage({
        mailboxId: BOX, providerMessageId,
      })).ok, false);
      assert.equal(calls.length, 0);
    }
  } finally {
    globalThis.fetch = original;
  }
  console.log("Microsoft Archive client isolation and pagination tests: PASS");
}

void run();
