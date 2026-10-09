import assert from "node:assert/strict";
import { setMicrosoftMessageUnread } from "./inboxConnectionApi";

const BOX = "microsoft-0123456789abcdefabcd12345678";
const ID = "AAMkAGVm.987/";

async function main() {
  const original = globalThis.fetch;
  let calls: Array<{ url: string; init?: RequestInit }> = [];
  const fakeFetch = (payload: unknown, status = 200) => {
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
    fakeFetch({ok: true, providerMessageId: ID, unread: false, changed: true});
    const result = await setMicrosoftMessageUnread({
      mailboxId: BOX, providerMessageId: ID, unread: false,
    });
    assert.deepEqual(result, {
      ok: true, providerMessageId: ID, unread: false, changed: true,
    });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/api/inboxes/mark-microsoft-read");
    assert.equal(calls[0].init?.credentials, "include");
    assert.equal(calls[0].init?.method, "POST");
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      mailboxId: BOX, providerMessageId: ID, unread: false,
    });

    fakeFetch({ok: true, providerMessageId: ID, unread: true, changed: false});
    const already = await setMicrosoftMessageUnread({
      mailboxId: BOX, providerMessageId: ID, unread: true,
    });
    assert.equal(already.ok, true);
    if (already.ok) assert.equal(already.changed, false);

    for (const payload of (
      [
        {ok: true, providerMessageId: "different", unread: false, changed: true},
        {ok: true, providerMessageId: ID, unread: true, changed: true},
        {ok: true, providerMessageId: ID, unread: false, changed: "true"},
        {ok: true, providerMessageId: ID, unread: false},
        null,
      ]
    )) {
      fakeFetch(payload);
      const response = await setMicrosoftMessageUnread({
        mailboxId: BOX, providerMessageId: ID, unread: false,
      });
      assert.equal(response.ok, false);
      assert.equal(response.error?.code, "microsoft_mutation_unconfirmed");
    }

    fakeFetch({ok:false, error:{
      code:"microsoft_write_permission_required",
      message:"secret access_token=private",
    }}, 403);
    const missing = await setMicrosoftMessageUnread({
      mailboxId: BOX, providerMessageId: ID, unread: false,
    });
    assert.equal(missing.ok, false);
    assert.equal(missing.error?.code, "microsoft_write_permission_required");
    assert.equal(JSON.stringify(missing).includes("access_token="), false);

    fakeFetch({ok:false,error:{
      code: "microsoft_mutation_unconfirmed",
      message: "Token secret leaked in error",
    }}, 503);
    const unconfirmed = await setMicrosoftMessageUnread({
      mailboxId:BOX, providerMessageId:ID, unread:true,
    });
    assert.equal(unconfirmed.ok, false);
    assert.equal(unconfirmed.error?.code, "microsoft_mutation_unconfirmed");
    assert.equal(JSON.stringify(unconfirmed).includes("secret"), false);

    fakeFetch({ok: true});
    for (const bad of (
      [
        {mailboxId: BOX, providerMessageId: "../private", unread:false},
        {mailboxId: BOX, providerMessageId: "bad?#", unread:true},
        {mailboxId: BOX, providerMessageId: "", unread:true},
        {mailboxId: "../../evil", providerMessageId: ID, unread:true},
        {mailboxId: BOX, providerMessageId: ID, unread:"false" as unknown as boolean},
        {mailboxId: BOX, providerMessageId: ID, unread:0 as unknown as boolean},
      ]
    )) {
      const reply = await setMicrosoftMessageUnread(bad);
      assert.equal(reply.ok, false);
    }
    assert.equal(calls.length, 0, "invalid requests never contact the provider");

    globalThis.fetch = (async () => {throw Error("private token network error");}) as typeof fetch;
    const network = await setMicrosoftMessageUnread({
      mailboxId: BOX, providerMessageId: ID, unread: false,
    });
    assert.equal(network.ok, false);
    assert.equal(network.error?.code, "microsoft_mutation_unconfirmed");
    assert.equal(JSON.stringify(network).includes("private token"), false);

    console.log("✓ Microsoft explicit read/unread client tests passed.");
  } finally {
    globalThis.fetch = original;
  }
}

void main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
