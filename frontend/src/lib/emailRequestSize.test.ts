import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { MAX_EMAIL_REQUEST_BYTES as LIMIT, isEmailRequestTooLarge, EMAIL_REQUEST_TOO_LARGE_MESSAGE } from "./emailRequestSize";
import { sendGmailMessage } from "./inboxConnectionApi";
import * as policy from "./microsoftDraftAttachmentPolicy";

const attachment = (bytes: number, name = "synthetic.bin") => ({
  name, mimeType: "application/octet-stream", contentBase64: Buffer.alloc(bytes, 120).toString("base64"),
});
const base = { mailboxId: "synthetic-mailbox", to: "to@example.com", cc: "cc@example.com", bcc: "hidden@example.com",
  subject: "Subject", bodyHtml: "<p>Body and signature</p>", bodyText: "Body and signature" };
const size = (value: unknown) => Buffer.byteLength(JSON.stringify(value), "utf8");

async function run() {
  assert.match(readFileSync("api/inboxes/email_request_size.py", "utf8"), /MAX_EMAIL_REQUEST_BYTES = 4_000_000/);
  assert.equal(LIMIT, 4_000_000);
  assert.equal(isEmailRequestTooLarge("x".repeat(LIMIT)), false);
  assert.equal(isEmailRequestTooLarge("x".repeat(LIMIT + 1)), true);
  assert.equal(isEmailRequestTooLarge("é".repeat(LIMIT / 2 + 1)), true);
  assert.equal(isEmailRequestTooLarge(JSON.stringify("\n".repeat(LIMIT / 2))), true, "JSON escapes count");
  for (const n of [1, 2, 3, 4, 1_000_001]) assert.equal(attachment(n).contentBase64.length, 4 * Math.ceil(n / 3));

  const savedFetch = globalThis.fetch, savedWindow = (globalThis as any).window;
  let calls = 0;
  (globalThis as any).window = { setTimeout: () => 1, clearTimeout() {} };
  globalThis.fetch = (async (_url: unknown, init: RequestInit) => {
    calls += 1;
    assert(Buffer.byteLength(String(init.body), "utf8") <= LIMIT);
    return { ok: true, status: 200, text: async () => '{"ok":true}' } as Response;
  }) as typeof fetch;
  const apiPath = require.resolve("./microsoftDraftAttachmentApi"), policyPath = require.resolve("./microsoftDraftAttachmentPolicy");
  const savedPolicy = require.cache[policyPath]!.exports;
  try {
    const near = { ...base, attachments: [attachment(1_400_000), attachment(1_400_001, 'résumé".pdf')], bodyText: "" };
    near.bodyText = "x".repeat(LIMIT - size(near));
    assert.equal(size(near), LIMIT);
    for (const request of [{ ...base, attachments: [attachment(3)] }, { ...base, attachments: [attachment(2), attachment(4)] }, near]) {
      const before = calls;
      assert.equal((await sendGmailMessage(request)).ok, true);
      assert.equal(calls, before + 1);
    }
    for (const request of [
      { ...near, bodyText: near.bodyText + "x" },
      { ...near, subject: "é".repeat(base.subject.length) },
      { ...base, attachments: [attachment(1_500_000), attachment(1_500_000)] },
      { ...base, bodyText: "é".repeat(2_000_000) },
    ]) {
      const original = JSON.stringify(request), before = calls;
      const rejected = await sendGmailMessage(request);
      assert.deepEqual(rejected, { ok: false, sendOutcome: "not_sent", error: { code: "request_too_large", message: EMAIL_REQUEST_TOO_LARGE_MESSAGE } });
      assert.equal(calls, before, "Reject before any fetch, including session probes");
      assert.equal(JSON.stringify(request), original);
    }

    const msRequest = { mailboxId: "microsoft-box", providerMessageId: "draft-1", requestId: "o".repeat(43), snapshotHash: "a".repeat(64), action: "add" as const,
      files: [1, 2].map(n => ({ name: `${n}.bin`, contentType: "application/octet-stream", base64: attachment(1_500_000).contentBase64 })) };
    const disabled = require(apiPath);
    assert.equal(policy.MICROSOFT_DRAFT_ATTACHMENTS_RELEASED, false);
    const before = calls;
    assert.equal((await disabled.mutateMicrosoftDraftAttachments(msRequest)).error.code, "microsoft_attachment_disabled");
    assert.equal(calls, before);
    require.cache[policyPath]!.exports = { ...savedPolicy, MICROSOFT_DRAFT_ATTACHMENTS_RELEASED: true };
    delete require.cache[apiPath];
    const api = require(apiPath);
    const original = JSON.stringify(msRequest);
    const rejected = await api.mutateMicrosoftDraftAttachments(msRequest);
    assert.equal(rejected.error.code, "request_too_large");
    assert.equal(rejected.outcome, "confirmed_not_performed");
    assert.equal(rejected.blocked, false);
    assert.equal(calls, before);
    assert.equal(JSON.stringify(msRequest), original);

    // The same exact serialized budget is used for gated Microsoft uploads.
    const msNear = { ...msRequest, files: msRequest.files.map(f => ({ ...f, base64: attachment(1_499_000).contentBase64 })) };
    globalThis.fetch = (async (_url: unknown, init: RequestInit) => {
      calls += 1;
      assert(Buffer.byteLength(String(init.body), "utf8") <= LIMIT);
      return { ok: true, status: 200, json: async () => ({ ok: true, mailboxId: msNear.mailboxId, providerMessageId: msNear.providerMessageId,
        requestId: msNear.requestId, outcome: "confirmed_performed", count: 2, completed: 2, blocked: false }) } as Response;
    }) as typeof fetch;
    assert.equal((await api.mutateMicrosoftDraftAttachments(msNear)).ok, true);
    assert.equal(calls, before + 1);
    globalThis.fetch = (async () => ({ ok: false, status: 413, json: async () => ({ ok: false,
      error: { code: "request_too_large", message: "untrusted" }, outcome: "confirmed_not_performed", blocked: false }) })) as typeof fetch;
    assert.equal((await api.mutateMicrosoftDraftAttachments(msNear)).error.message, EMAIL_REQUEST_TOO_LARGE_MESSAGE);
    globalThis.fetch = (async () => ({ ok: false, status: 500, json: async () => ({ ok: false,
      error: { code: "request_too_large" } }) })) as typeof fetch;
    assert.equal((await api.mutateMicrosoftDraftAttachments(msNear)).outcome, "uncertain", "Unproven failures stay blocked");
    console.log("Email payload byte limits, encoding, preservation and gated Microsoft upload checks passed");
  } finally {
    globalThis.fetch = savedFetch; (globalThis as any).window = savedWindow;
    require.cache[policyPath]!.exports = savedPolicy; delete require.cache[apiPath];
  }
}
void run().catch(error => { console.error(error); process.exitCode = 1; });
