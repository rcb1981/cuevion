import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import * as policy from "./microsoftDraftAttachmentPolicy";

async function test() {
  const apiPath = require.resolve("./microsoftDraftAttachmentApi");
  const policyPath = require.resolve("./microsoftDraftAttachmentPolicy");
  const originalFetch = globalThis.fetch;
  const originalExports = require.cache[policyPath]!.exports;
  const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const context = { mailboxId: "microsoft-box", providerMessageId: "draft-1" };
  const request = { ...context, requestId: "o".repeat(43), snapshotHash: "a".repeat(64), action: "add" as const,
    files: [{ name: "new.txt", contentType: "text/plain", base64: "bmV3IQ==" }] };
  const calls: { url: string; init: RequestInit }[] = [];
  let response: unknown = { ok: true, ...context, requestId: request.requestId, outcome: "confirmed_performed", count: 1, completed: 1, blocked: false };
  let status = 200;
  let failNetwork = false;
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    if (failNetwork) throw new Error("private network data");
    return { ok: status >= 200 && status < 300, status, json: async () => response } as Response;
  }) as typeof fetch;
  try {
    assert.equal(policy.MICROSOFT_DRAFT_ATTACHMENTS_RELEASED, false);
    const disabled = require(apiPath);
    assert.equal((await disabled.mutateMicrosoftDraftAttachments(request)).error.code, "microsoft_attachment_disabled");
    await disabled.loadMicrosoftDraftAttachments(context);
    await disabled.inspectMicrosoftDraftAttachmentRequest({ ...context, requestId: request.requestId });
    assert.equal(calls.length, 0);
    // Override only this isolated test process's module export, never source or env.
    require.cache[policyPath]!.exports = { ...originalExports, MICROSOFT_DRAFT_ATTACHMENTS_RELEASED: true };
    delete require.cache[apiPath];
    const api = require(apiPath);
    const result = await api.mutateMicrosoftDraftAttachments(request);
    assert.equal(result.outcome, "confirmed_performed");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/api/inboxes/mutate-microsoft-draft-attachments");
    assert.equal(calls[0].init.credentials, "include");
    assert.deepEqual(JSON.parse(String(calls[0].init.body)), request);
    const valid = response as Record<string, unknown>;
    for (const bad of [{ ...valid, mailboxId: "other" }, { ...valid, providerMessageId: "wrong" }, { ...valid, requestId: "p".repeat(43) },
      { ...valid, count: 2 }, { ...valid, blocked: true }, { ...valid, completed: 0 }, { ...valid, privateToken: "secret" }]) {
      response = bad;
      assert.equal((await api.mutateMicrosoftDraftAttachments(request)).outcome, "uncertain");
    }
    response = { ...valid, outcome: "uncertain", completed: 0, blocked: true };
    const before = calls.length;
    assert.equal((await api.mutateMicrosoftDraftAttachments(request)).blocked, true);
    assert.equal(calls.length, before+1); // No status polling or second mutation.
    failNetwork = true;
    const lostBefore = calls.length;
    const lost = await api.mutateMicrosoftDraftAttachments(request);
    assert.equal(lost.outcome, "uncertain"); assert.equal(lost.blocked, true);
    assert.equal(calls.length, lostBefore+1);
    assert.doesNotMatch(JSON.stringify(lost), /private network data/);
    failNetwork = false;
    status = 409; response = { ok: false, error: { code: "microsoft_attachment_draft_blocked", message: "private provider text" } };
    const rejected = await api.mutateMicrosoftDraftAttachments(request);
    assert.equal(rejected.error.code, "microsoft_attachment_draft_blocked");
    assert.doesNotMatch(JSON.stringify(rejected), /private provider text/);
    status = 200;
    const part = { id: "ordinary", name: "song.mp3", size: 100*1024*1024, contentType: "audio/mpeg", isInline: false, kind: "file", contentId: null, removable: true };
    response = { ok: true, ...context, snapshotHash: request.snapshotHash, attachments: [part], complete: true };
    assert.equal((await api.loadMicrosoftDraftAttachments(context)).ok, true);
    for (const attachments of [[{ ...part, isInline: true }], [part, part]]) {
      response = { ok: true, ...context, snapshotHash: request.snapshotHash, attachments, complete: true };
      assert.equal((await api.loadMicrosoftDraftAttachments(context)).ok, false);
    }
    response = { ...valid, outcome: "uncertain", completed: 0, blocked: true };
    const statusBefore = calls.length;
    assert.equal((await api.inspectMicrosoftDraftAttachmentRequest({ ...context, requestId: request.requestId })).outcome, "uncertain");
    assert.equal(JSON.parse(String(calls[statusBefore].init.body)).action, "status");
    for (const bad of ["", "../bad", "?token", "a b", "."]) {
      const n = calls.length;
      assert.equal((await api.mutateMicrosoftDraftAttachments({ ...request, providerMessageId: bad })).ok, false);
      assert.equal((await api.mutateMicrosoftDraftAttachments({ ...context, requestId: request.requestId, snapshotHash: request.snapshotHash, action: "remove", attachmentId: bad })).ok, false);
      assert.equal(calls.length, n);
    }
    Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
    const ids = new Set(Array.from({ length: 100 }, () => api.newMicrosoftAttachmentRequestId()));
    assert.equal(ids.size, 100);
    assert.ok([...ids].every(id => /^[A-Za-z0-9_-]{43}$/.test(String(id))));
  } finally {
    globalThis.fetch = originalFetch;
    require.cache[policyPath]!.exports = originalExports;
    delete require.cache[apiPath];
    if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto);
    else Reflect.deleteProperty(globalThis, "crypto");
  }
}
void test().catch(error => { console.error(error); process.exitCode = 1; });
