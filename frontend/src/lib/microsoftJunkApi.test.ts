import assert from "node:assert/strict";
import {
  fetchMicrosoftJunk, fetchMicrosoftJunkMessage,
  fetchMicrosoftAttachments, downloadMicrosoftAttachmentFile,
} from "./inboxConnectionApi";

const BOX = "microsoft-0123456789abcdefabcd12345678";
const ID = "AAMkJunkMessage.1/";
const ENTRY = {
  id: ID, providerMessageId: ID, providerThreadId: "AAQkThread",
  serverMailboxId: BOX, providerFolder: "JUNK",
  labelIds: ["JUNK", "UNREAD"],
  sender: "Untrusted Sender", subject: "Claim your prize",
  snippet: "Click here", from: "Untrusted Sender <sender@example.com>",
  to: "", cc: "", timestamp: "October 15 at 00:01",
  createdAt: "2026-10-15T00:01:00+00:00",
  body: [] as string[], unread: true, flagged: false,
  attachments: [], hasAttachments: true,
};
const LIST = {ok: true, messages: [ENTRY], junkMessageIds: [ID], hasMore: false};
const EXACT = {ok: true, message: {...ENTRY, body: ["Click https://example.com/"]}};

async function run() {
  const nativeFetch = globalThis.fetch;
  let calls: Array<{url: string; init?: RequestInit}> = [];
  function stub(response: unknown, status = 200) {
    calls = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({url, init});
      return {ok: status >= 200 && status < 300, status, json: async () => response} as Response;
    }) as typeof fetch;
  }
  try {
    stub(LIST);
    const listing = await fetchMicrosoftJunk({mailboxId: BOX, limit: 5, page: 2});
    assert.equal(listing.ok, true);
    assert.equal(listing.messages?.[0]?.providerFolder, "JUNK");
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft-junk");
    assert.equal(calls[0].init?.method, "POST");
    assert.equal(calls[0].init?.credentials, "include");
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      mailboxId: BOX, limit: 5, page: 2,
    });

    stub(EXACT);
    const exact = await fetchMicrosoftJunkMessage({
      mailboxId: BOX, providerMessageId: ID,
    });
    assert.equal(exact.ok, true);
    assert.equal(exact.message?.body[0], "Click https://example.com/");
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft-junk-message");

    for (const invalid of [
      {...LIST, messages: [{...ENTRY, providerFolder: "INBOX"}]},
      {...LIST, messages: [{...ENTRY, providerFolder: "DELETED"}]},
      {...LIST, messages: [{...ENTRY, labelIds: ["INBOX", "UNREAD"]}]},
      {...LIST, messages: [{...ENTRY, labelIds: ["JUNK", "INBOX"]}]},
      {...LIST, messages: [{...ENTRY, unread: false}]},
      {...LIST, messages: [{...ENTRY, flagged: true}]},
      {...LIST, messages: [{...ENTRY, serverMailboxId: "another-owner"}]},
      {...LIST, junkMessageIds: ["wrong"]},
      {...LIST, nextLink: "https://graph.microsoft.com/private"},
    ]) {
      stub(invalid);
      assert.equal((await fetchMicrosoftJunk({mailboxId: BOX})).ok, false);
    }
    for (const invalid of [
      {...EXACT, message: {...ENTRY, providerFolder: "ARCHIVE", body: ["Wrong"]}},
      {...EXACT, message: {...ENTRY, id: "wrong", body: ["Wrong"]}},
      {...EXACT, message: {...ENTRY, body: []}},
    ]) {
      stub(invalid);
      assert.equal((await fetchMicrosoftJunkMessage({
        mailboxId: BOX, providerMessageId: ID,
      })).ok, false);
    }
    for (const page of [-1, 0, 5, 1.2, Number.NaN]) {
      stub(LIST);
      assert.equal((await fetchMicrosoftJunk({mailboxId: BOX, page})).ok, false);
      assert.equal(calls.length, 0);
    }
    for (const providerMessageId of ["", "bad?#", "../other"]) {
      stub(EXACT);
      assert.equal((await fetchMicrosoftJunkMessage({
        mailboxId: BOX, providerMessageId,
      })).ok, false);
      assert.equal(calls.length, 0);
    }

    // Metadata is allowed, but even a crafted call cannot download a junk file.
    stub({ok: true, attachments: [], hasMore: false});
    assert.equal((await fetchMicrosoftAttachments({
      mailboxId: BOX, providerMessageId: ID, folder: "Junk",
    })).ok, true);
    stub({ok: true, attachmentId: "AAMkAttachment", fileName: "unsafe.exe", size: 0, base64: ""});
    assert.equal((await downloadMicrosoftAttachmentFile({
      mailboxId: BOX, providerMessageId: ID,
      attachmentId: "AAMkAttachment", folder: "Junk",
    })).ok, false);
    assert.equal(calls.length, 0);
  } finally {
    globalThis.fetch = nativeFetch;
  }
  console.log("Microsoft Junk read-only folder, owner and download restrictions: PASS");
}
void run();
