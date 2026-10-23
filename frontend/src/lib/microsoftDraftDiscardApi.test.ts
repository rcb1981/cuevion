import assert from "node:assert/strict";
import { discardMicrosoftDraft } from "./inboxConnectionApi";

const BOX = "microsoft-0123456789abcdefabcd12345678";
const ID = "AAMkDraftMessage.1/";

async function test() {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  let response: unknown = { ok: true, providerMessageId: ID, discarded: true };
  let httpSuccess = true;
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return { ok: httpSuccess, status: httpSuccess ? 200 : 403, json: async () => response } as Response;
  }) as typeof fetch;

  try {
    assert.deepEqual(await discardMicrosoftDraft({ mailboxId: BOX, providerMessageId: ID }),
      { ok: true, providerMessageId: ID, discarded: true });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/api/inboxes/discard-microsoft-draft");
    assert.equal(calls[0].init?.method, "POST");
    assert.equal(calls[0].init?.credentials, "include");
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { mailboxId: BOX, providerMessageId: ID });
    for (const bad of ["", "../other", "?token", "two words", ".", "ab#c"]) {
      calls.length = 0;
      const result = await discardMicrosoftDraft({ mailboxId: BOX, providerMessageId: bad });
      assert.equal(result.ok, false);
      if (!result.ok) assert.equal(result.outcome, "not_performed");
      assert.equal(calls.length, 0);
    }
    for (const bad of [
      { ok: true, providerMessageId: ID, discarded: false },
      { ok: true, providerMessageId: "wrong", discarded: true },
      { ok: true, providerMessageId: ID, discarded: true, user: "another" },
      { ok: false, providerMessageId: ID, discarded: true },
    ]) {
      calls.length = 0;
      response = bad;
      const result = await discardMicrosoftDraft({ mailboxId: BOX, providerMessageId: ID });
      assert.equal(result.ok, false);
      if (!result.ok) assert.equal(result.outcome, "uncertain");
      assert.equal(calls.length, 1, "Only one Graph-oriented HTTP attempt is allowed");
    }
    calls.length = 0;
    httpSuccess = false;
    response = { ok: false, error: { code: "microsoft_pilot_read_only", message: "Not approved" } };
    const blocked = await discardMicrosoftDraft({ mailboxId: BOX, providerMessageId: ID });
    assert.equal(blocked.ok, false);
    if (!blocked.ok) assert.equal(blocked.outcome, "not_performed");
    assert.equal(calls.length, 1);
    for (const [code, status, outcome] of [
      ["microsoft_draft_discard_not_performed", 409, "not_performed"],
      ["microsoft_graph_unavailable", 503, "uncertain"],
      ["microsoft_mutation_unconfirmed", 503, "uncertain"],
      ["microsoft_reconnect_required", 503, "uncertain"],
      ["unknown_code", 409, "uncertain"],
    ] as const) {
      calls.length = 0;
      globalThis.fetch = (async () => {
        calls.push({ url: "/api/inboxes/discard-microsoft-draft" });
        return { ok: false, status, json: async () => ({ ok: false, error: { code, message: "untrusted" } }) } as Response;
      }) as typeof fetch;
      const result = await discardMicrosoftDraft({ mailboxId: BOX, providerMessageId: ID });
      assert.equal(result.ok, false);
      if (!result.ok) {
        assert.equal(result.outcome, outcome);
        assert.doesNotMatch(result.error.message, /untrusted/);
      }
      assert.equal(calls.length, 1);
    }
    for (const failure of ["network", "json"]) {
      calls.length = 0;
      globalThis.fetch = (async () => {
        calls.push({ url: "/api/inboxes/discard-microsoft-draft" });
        if (failure === "network") throw new TypeError("Lost HTTP response");
        return { ok: true, status: 200, json: async () => { throw new SyntaxError("Truncated JSON"); } } as unknown as Response;
      }) as typeof fetch;
      const result = await discardMicrosoftDraft({ mailboxId: BOX, providerMessageId: ID });
      assert.equal(result.ok, false);
      if (!result.ok) assert.equal(result.outcome, "uncertain");
      assert.equal(calls.length, 1);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
  console.log("Microsoft Drafts discard exact client/no retry tests: PASS");
}
void test();
