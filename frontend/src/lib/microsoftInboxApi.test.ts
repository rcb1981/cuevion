import assert from "node:assert/strict";
import {
  fetchMicrosoftInbox,
  fetchMicrosoftMessage,
} from "./inboxConnectionApi";

const BOX = "microsoft-0123456789abcdefabcd12345678";
const ID = "AAMkAGVm.987/";
const ENTRY = {
  id: ID,
  providerMessageId: ID,
  providerThreadId: "AAQkABc123",
  serverMailboxId: BOX,
  providerFolder: "INBOX",
  labelIds: ["INBOX", "UNREAD"],
  sender: "Artist",
  subject: "New track",
  snippet: "Here is the link",
  from: "Artist <artist@example.com>",
  to: "",
  cc: "",
  timestamp: "October 9 at 09:15",
  createdAt: "2026-10-09T09:15:00+00:00",
  body: [] as string[],
  unread: true,
  flagged: false,
  attachments: [],
};
const LIST = {
  ok: true,
  messages: [ENTRY],
  inboxUidSet: [ID],
  uidValidity: "microsoft-graph-immutable",
  hasMore: false,
};
const EXACT = {
  ok: true,
  message: { ...ENTRY, body: ["Full verified text."] },
};

type StubFetch = (url: string, init?: RequestInit) => Promise<Response>;

async function run() {
  const original = globalThis.fetch;
  let calls: Array<{url: string; init?: RequestInit}> = [];
  const setFetch = (payload: unknown, { status = 200 } = {}) => {
    calls = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({url, init});
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => payload,
      } as Response;
    }) as typeof fetch;
  };
  try {
    setFetch(LIST);
    const list = await fetchMicrosoftInbox({mailboxId: BOX, limit: 5});
    assert.equal(list.ok, true);
    assert.equal(list.messages?.[0]?.providerMessageId, ID);
    assert.equal(list.hasMore, false);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft");
    assert.equal(calls[0].init?.credentials, "include");
    assert.equal(calls[0].init?.method, "POST");
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {mailboxId: BOX, limit: 5});

    setFetch(EXACT);
    const exact = await fetchMicrosoftMessage({mailboxId: BOX, providerMessageId: ID});
    assert.equal(exact.ok, true);
    assert.deepEqual(exact.message?.body, ["Full verified text."]);
    assert.equal(calls[0].url, "/api/inboxes/fetch-microsoft-message");

    setFetch({...LIST, messages: [{...ENTRY, access_token: "secret"}]});
    assert.equal((await fetchMicrosoftInbox({mailboxId: BOX})).ok, false);

    setFetch({...LIST, messages: [{...ENTRY, serverMailboxId: "different"}]});
    const spoofed = await fetchMicrosoftInbox({mailboxId: BOX});
    assert.equal(spoofed.ok, false);

    setFetch({...LIST, messages: [{...ENTRY, body: ["unproven preview body"]}]});
    assert.equal((await fetchMicrosoftInbox({mailboxId: BOX})).ok, false);

    setFetch({...LIST, inboxUidSet: ["different"]});
    assert.equal((await fetchMicrosoftInbox({mailboxId: BOX})).ok, false);

    setFetch({...LIST, messages: Array.from({length: 26}, () => ENTRY)});
    assert.equal((await fetchMicrosoftInbox({mailboxId: BOX, limit: 25})).ok, false);

    setFetch({...EXACT, message: {...EXACT.message, bodyHtml: "<img src='https://tracker'>"}});
    assert.equal((await fetchMicrosoftMessage({mailboxId: BOX, providerMessageId: ID})).ok, false);

    setFetch({...EXACT, message: {...EXACT.message, providerMessageId: "different"}});
    assert.equal((await fetchMicrosoftMessage({mailboxId: BOX, providerMessageId: ID})).ok, false);

    setFetch({ok:false,error:{code:"microsoft_reconnect_required", message:"Reconnect"}}, {status:409});
    const notConnected = await fetchMicrosoftInbox({mailboxId:BOX});
    assert.equal(notConnected.ok, false);
    assert.equal(notConnected.error?.code, "microsoft_reconnect_required");

    setFetch({ok:false,error:{code:"microsoft_reconnect_required", message:"token=private"}}, {status:409});
    const sanitized = await fetchMicrosoftInbox({mailboxId:BOX});
    assert.equal(sanitized.error?.code, "microsoft_reconnect_required");
    assert.equal(JSON.stringify(sanitized).includes("token=private"), false);

    setFetch({ok:false,error:{code:"private-secret-here", message:"token=private"}}, {status:503});
    const privateError = await fetchMicrosoftInbox({mailboxId:BOX});
    assert.equal(privateError.ok, false);
    assert.notEqual(privateError.error?.code, "private-secret-here");
    assert.equal(JSON.stringify(privateError).includes("token=private"), false);

    setFetch(LIST);
    for (const req of (
      [{mailboxId:"../../bad",limit:5},{mailboxId:BOX,limit:0},{mailboxId:BOX,limit:26},{mailboxId:BOX,limit:NaN}]
    )) {
      const result = await fetchMicrosoftInbox(req);
      assert.equal(result.ok, false);
    }
    assert.equal(calls.length, 0, "invalid list requests must not access network");

    setFetch(EXACT);
    for (const req of (
      [{mailboxId:BOX,providerMessageId:""},{mailboxId:BOX,providerMessageId:"bad?q=x"},
       {mailboxId:"../wrong",providerMessageId:ID},
       {mailboxId:BOX,providerMessageId:"../account"}]
    )) {
      const result = await fetchMicrosoftMessage(req);
      assert.equal(result.ok, false);
    }
    assert.equal(calls.length, 0, "invalid exact requests must not access network");

    globalThis.fetch = (async () => {throw new Error("Private network token details");}) as typeof fetch;
    const failed = await fetchMicrosoftInbox({mailboxId:BOX});
    assert.equal(failed.ok, false);
    assert.equal(JSON.stringify(failed).includes("Private"), false);
    console.log("✓ Microsoft Inbox client request/response tests passed.");
  } finally {
    globalThis.fetch = original;
  }
}
void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
