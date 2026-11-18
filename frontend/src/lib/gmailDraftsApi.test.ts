import assert from "node:assert/strict";
import { fetchGmailDrafts } from "./gmailDraftsApi";
const original = globalThis.fetch;
const scope = "a".repeat(64);
const summary = { draftId: "draft-a", messageId: "message-a", threadId: "thread-a", from: "from@example.test", to: "to@example.test",
  cc: "cc@example.test", bcc: "private@example.test", subject: "External draft", internalDate: "1700000000000", headers: [] };
const detail = { ...summary, previewStatus: "available", unavailableReason: null, bodyText: "text", bodyHtml: "<p>HTML</p>",
  inventoryComplete: true, mimeParts: [], attachments: [] };
let response: Response; const requests: Array<{url: unknown; init: RequestInit}> = [];
globalThis.fetch = (async (url, init) => { requests.push({ url, init: init! }); return response; }) as typeof fetch;
async function run() {
  try {
    response = new Response(JSON.stringify({ ok: true, mailboxId: "box", scope, drafts: [summary], nextPageToken: "next+/=" }));
    const result = await fetchGmailDrafts({ mailboxId: "box" });
    assert(result.ok && "drafts" in result); assert.equal(result.drafts[0].bcc, "private@example.test");
    assert.notEqual(result.drafts[0].draftId, result.drafts[0].messageId);
    assert.equal(requests[0].url, "/api/inboxes/fetch-gmail-drafts");
    assert.equal(requests[0].init.method, "POST"); assert.equal(requests[0].init.credentials, "include");
    assert.equal(requests[0].init.cache, "no-store"); assert.deepEqual(JSON.parse(String(requests[0].init.body)), { mailboxId: "box" });
    response = new Response(JSON.stringify({ ok: true, mailboxId: "box", scope, draft: detail }));
    const loaded = await fetchGmailDrafts({ mailboxId: "box", scope, draftId: "draft-a" });
    assert(loaded.ok && "draft" in loaded); assert.equal(loaded.draft.bodyHtml, "<p>HTML</p>"); assert.equal(loaded.draft.bodyText, "text");
    for (const bad of [null, {}, { ok: true, mailboxId: "other", scope, draft: detail },
      { ok: true, mailboxId: "box", scope: "b".repeat(64), draft: detail },
      { ok: true, mailboxId: "box", scope, draft: { ...detail, draftId: "different" } },
      { ok: true, mailboxId: "box", scope, draft: { ...detail, bcc: ["unvalidated"] } },
      { ok: true, mailboxId: "box", scope, draft: { ...detail, previewStatus: "unavailable", unavailableReason: "too_large" } },
      { ok: true, mailboxId: "box", scope, draft: { ...detail, attachments: [{ filename: "bad" }] } }]) {
      response = new Response(JSON.stringify(bad));
      assert.equal((await fetchGmailDrafts({ mailboxId: "box", scope, draftId: "draft-a" })).ok, false);
    }
    for (const bad of ["", "\n", "x".repeat(4097)]) {
      const before = requests.length;
      assert.equal((await fetchGmailDrafts({ mailboxId: "box", pageToken: bad, scope })).ok, false);
      assert.equal(requests.length, before);
    }
    for (const status of [401,403,404,409,429,500]) {
      response = new Response("secret provider error", { status });
      const error = await fetchGmailDrafts({ mailboxId: "box" });
      assert(!error.ok); assert(!error.message.includes("secret"));
    }
    response = new Response("bad JSON"); assert.equal((await fetchGmailDrafts({ mailboxId: "box" })).ok, false);
    let cancelled = false;
    response = new Response(new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(10*1024*1024+1)); }, cancel() { cancelled = true; } }));
    assert.equal((await fetchGmailDrafts({ mailboxId: "box" })).ok, false); assert(cancelled);
    const abort = new AbortController(); abort.abort();
    globalThis.fetch = (async (_url, init) => { assert.equal(init?.signal, abort.signal); throw new DOMException("Aborted", "AbortError"); }) as typeof fetch;
    assert.equal((await fetchGmailDrafts({ mailboxId: "box" }, abort.signal)).ok, false);
    console.log("Gmail Drafts API: identities, Bcc, HTML/plain text, strict DTOs, bounded responses, safe errors and abort passed");
  } finally { globalThis.fetch = original; }
}
void run().catch(error => { console.error(error); process.exitCode = 1; });
