import assert from "node:assert/strict";
import { fetchMicrosoftAttachments } from "./inboxConnectionApi";

const mailboxId = "microsoft-0123456789abcdefabcd12345678";
const providerMessageId = "AAMkMessage.1/";
const attachment = {
  id: "AAMkAttachment.2/",
  name: "master.wav",
  size: 234567,
  contentType: "audio/wav",
  isInline: false,
  kind: "file",
};

async function run() {
  const original = globalThis.fetch;
  const calls: Array<{url: string; init?: RequestInit}> = [];
  const mock = (body: unknown, ok = true) => {
    calls.length = 0;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({url, init});
      return {ok, json: async () => body} as Response;
    }) as typeof fetch;
  };
  try {
    mock({ok: true, attachments: [attachment], hasMore: false});
    const response = await fetchMicrosoftAttachments({mailboxId, providerMessageId, folder: "Inbox"});
    assert.equal(response.ok, true);
    assert.deepEqual(response.attachments, [attachment]);
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft-attachments");
    assert.equal(calls[0].init?.method, "POST");
    assert.equal(calls[0].init?.credentials, "include");
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      mailboxId, providerMessageId, folder: "Inbox",
    });

    mock({ok: true, attachments: [{...attachment, contentBytes: "private"}], hasMore: false});
    assert.equal((await fetchMicrosoftAttachments({mailboxId, providerMessageId, folder: "Sent"})).ok, false);

    mock({ok: true, attachments: [{...attachment, size: "123"}], hasMore: false});
    assert.equal((await fetchMicrosoftAttachments({mailboxId, providerMessageId, folder: "Inbox"})).ok, false);

    mock({ok: true, attachments: [attachment], hasMore: "no"});
    assert.equal((await fetchMicrosoftAttachments({mailboxId, providerMessageId, folder: "Inbox"})).ok, false);

    mock({ok: true, attachments: [attachment] , hasMore: false});
    assert.equal((await fetchMicrosoftAttachments({
      mailboxId, providerMessageId, folder: "Junk" as "Inbox",
    })).ok, false);
    assert.equal(calls.length, 0);
  } finally {
    globalThis.fetch = original;
  }
  console.log("Microsoft attachment metadata client tests passed.");
}

void run();
