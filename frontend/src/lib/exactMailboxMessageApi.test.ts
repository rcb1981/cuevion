declare const process: { exitCode?: number };
import assert from "node:assert/strict";
import {
  EXACT_MAILBOX_MESSAGE_ENDPOINT,
  EXACT_MAILBOX_MESSAGE_ERROR,
  fetchExactMailboxMessage,
  isExactMailboxSourceRef,
  parseExactMailboxMessageResponse,
  type ExactMailboxSourceRef,
} from "./exactMailboxMessageApi";

const googleSource: ExactMailboxSourceRef = { provider: "google", providerMessageId: "provider-1" };
const imapSource: ExactMailboxSourceRef = { provider: "custom_imap", folder: "INBOX", uidValidity: "23", imapUid: "42" };
const base = {
  id: "gmail-provider-1", serverMailboxId: "main", providerFolder: "Inbox", sender: "Sender",
  subject: "Subject", snippet: "Body", from: "sender@example.test", to: "owner@example.test", cc: "",
  timestamp: "2026-09-09T09:00:00Z", createdAt: "2026-09-09T09:00:00Z", body: ["Body"], bodyHtml: "<p>Body</p>",
  attachments: [{ id: "attachment-1", name: "text.txt", mimeType: "text/plain", size: 3 }], unread: true, flagged: false,
};
function payload(sourceRef: ExactMailboxSourceRef = googleSource) {
  return {
    v: 1, mailboxId: "main", sourceRef,
    message: sourceRef.provider === "google"
      ? { ...base, providerMessageId: sourceRef.providerMessageId, providerThreadId: "thread-1", labelIds: ["INBOX", "UNREAD"] }
      : { ...base, id: "imap-uid-42", providerFolder: "INBOX", uidValidity: sourceRef.uidValidity, imapUid: sourceRef.imapUid, threadId: "imap:uid:main:INBOX:23:42" },
  };
}
function http(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}
let tests = 0;
async function test(name: string, run: () => void | Promise<void>) { await run(); tests++; console.log(`PASS ${name}`); }

async function main() {
  await test("strict Google canonical message parser", () => {
    const parsed = parseExactMailboxMessageResponse(payload(), "main", googleSource);
    assert.ok(parsed);
    assert.equal(parsed.message.providerMessageId, "provider-1");
    assert.deepEqual(parsed.message.body, ["Body"]);
  });
  await test("strict IMAP canonical identity parser", () => {
    const parsed = parseExactMailboxMessageResponse(payload(imapSource), "main", imapSource);
    assert.ok(parsed);
    assert.equal(parsed.message.uidValidity, "23");
    assert.equal(parsed.message.imapUid, "42");
    assert.equal(parsed.message.providerFolder, "INBOX");
  });
  await test("Google thread ID is not required", () => {
    const value = payload();
    delete (value.message as { providerThreadId?: string }).providerThreadId;
    assert.ok(parseExactMailboxMessageResponse(value, "main", googleSource));
  });
  await test("Google returned folder must match canonical provider labels", () => {
    const value = payload();
    value.message.providerFolder = "Archive";
    assert.equal(parseExactMailboxMessageResponse(value, "main", googleSource), null);
  });
  await test("Google label precedence determines canonical folder", () => {
    const cases = [
      [["TRASH", "SPAM", "DRAFT", "INBOX", "SENT"], "Trash"],
      [["SPAM", "DRAFT", "INBOX", "SENT"], "Spam"],
      [["DRAFT", "INBOX", "SENT"], "Drafts"],
      [["INBOX", "SENT"], "Inbox"],
      [["SENT"], "Sent"],
      [["UNREAD"], "Archive"],
      [[], "Archive"],
    ] as const;
    for (const [labels, folder] of cases) {
      const value = payload();
      (value.message as { labelIds: string[] }).labelIds = [...labels];
      value.message.providerFolder = folder;
      assert.ok(parseExactMailboxMessageResponse(value, "main", googleSource));
    }
  });
  await test("Google invalid labels rejected without normalization", () => {
    for (const label of ["", " INBOX", "INBOX ", "INBOX\n", "unicode-é", "x".repeat(513)]) {
      const value = payload();
      (value.message as { labelIds: string[] }).labelIds = ["INBOX", label];
      assert.equal(parseExactMailboxMessageResponse(value, "main", googleSource), null);
    }
    const duplicate = payload();
    (duplicate.message as { labelIds: string[] }).labelIds = ["INBOX", "INBOX"];
    assert.equal(parseExactMailboxMessageResponse(duplicate, "main", googleSource), null);
  });
  await test("Google invalid provider thread ID rejected without normalization", () => {
    for (const providerThreadId of ["", " leading", "trailing ", "unicode-é", "x\ny", "x".repeat(513)]) {
      const value = payload();
      (value.message as { providerThreadId: string }).providerThreadId = providerThreadId;
      assert.equal(parseExactMailboxMessageResponse(value, "main", googleSource), null);
    }
  });
  for (const [name, mutate] of [
    ["wrong envelope mailbox", (value: any) => { value.mailboxId = "other"; }],
    ["wrong nested mailbox", (value: any) => { value.message.serverMailboxId = "other"; }],
    ["wrong envelope source", (value: any) => { value.sourceRef = { provider: "google", providerMessageId: "other" }; }],
    ["wrong provider message ID", (value: any) => { value.message.providerMessageId = "other"; }],
    ["extra envelope field", (value: any) => { value.workspaceId = "wsp_other"; }],
    ["provider token field", (value: any) => { value.message.accessToken = "secret"; }],
    ["raw provider response", (value: any) => { value.message.payload = {}; }],
    ["malformed body", (value: any) => { value.message.body = [false]; }],
    ["numeric unread", (value: any) => { value.message.unread = 1; }],
    ["missing display field", (value: any) => { delete value.message.subject; }],
    ["attachment extra field", (value: any) => { value.message.attachments = [{ id: "x", name: "file", password: "secret" }]; }],
    ["negative attachment size", (value: any) => { value.message.attachments = [{ id: "x", name: "file", size: -1 }]; }],
    ["provider identity cross-contamination", (value: any) => { value.message.imapUid = "42"; }],
  ] as const) {
    await test(`${name} rejected`, () => {
      const value = payload(); mutate(value);
      assert.equal(parseExactMailboxMessageResponse(value, "main", googleSource), null);
    });
  }
  await test("IMAP UIDVALIDITY mismatch rejected", () => {
    const value = payload(imapSource);
    (value.message as { uidValidity: string }).uidValidity = "24";
    assert.equal(parseExactMailboxMessageResponse(value, "main", imapSource), null);
  });
  await test("IMAP UID mismatch rejected", () => {
    const value = payload(imapSource);
    (value.message as { imapUid: string }).imapUid = "43";
    assert.equal(parseExactMailboxMessageResponse(value, "main", imapSource), null);
  });
  await test("source contract follows C3C Google identifiers and bounded IMAP UID", () => {
    assert.equal(isExactMailboxSourceRef({ provider: "google", providerMessageId: "broad source:id" }), true);
    for (const providerMessageId of ["", " leading", "trailing ", "unicode-é", "x\ny", "x".repeat(513)]) {
      assert.equal(isExactMailboxSourceRef({ provider: "google", providerMessageId }), false);
    }
    for (const change of [{ folder: "Archive" }, { uidValidity: "0" }, { uidValidity: "01" }, { imapUid: "4294967296" }, { imapUid: "1:42" }, { userId: "caller" }]) {
      assert.equal(isExactMailboxSourceRef({ ...imapSource, ...change }), false);
    }
  });
  await test("one exact POST uses JSON, include credentials, no-store and caller signal", async () => {
    const controller = new AbortController();
    let calls = 0;
    const result = await fetchExactMailboxMessage("main", googleSource, {
      signal: controller.signal,
      fetchImplementation: (async (input, init) => {
        calls++;
        assert.equal(input, EXACT_MAILBOX_MESSAGE_ENDPOINT);
        assert.equal(init?.method, "POST"); assert.equal(init?.credentials, "include"); assert.equal(init?.cache, "no-store");
        assert.equal(init?.signal, controller.signal);
        assert.deepEqual(JSON.parse(init?.body as string), { v: 1, mailboxId: "main", sourceRef: googleSource });
        assert.deepEqual(init?.headers, { Accept: "application/json", "Content-Type": "application/json" });
        return http(payload());
      }) as typeof fetch,
    });
    assert.equal(result.status, "success"); assert.equal(calls, 1);
  });
  await test("fixed source_changed failure is preserved without provider detail", async () => {
    const result = await fetchExactMailboxMessage("main", imapSource, {
      fetchImplementation: (async () => http({ error: { code: "source_changed", message: EXACT_MAILBOX_MESSAGE_ERROR } }, 409)) as typeof fetch,
    });
    assert.deepEqual(result, { status: "source_changed" });
  });
  await test("raw provider error fails closed", async () => {
    const result = await fetchExactMailboxMessage("main", googleSource, {
      fetchImplementation: (async () => http({ error: { code: "service_unavailable", message: "private IMAP host and credentials" } }, 503)) as typeof fetch,
    });
    assert.deepEqual(result, { status: "invalid_response" });
  });
  await test("network failure performs no retries", async () => {
    let calls = 0;
    const result = await fetchExactMailboxMessage("main", googleSource, {
      fetchImplementation: (async () => { calls++; throw new Error("private provider exception"); }) as typeof fetch,
    });
    assert.deepEqual(result, { status: "service_unavailable" }); assert.equal(calls, 1);
  });
  await test("pre-aborted request never calls fetch", async () => {
    const controller = new AbortController(); controller.abort();
    const result = await fetchExactMailboxMessage("main", googleSource, { signal: controller.signal, fetchImplementation: (async () => { throw new Error("should not fetch"); }) as typeof fetch });
    assert.deepEqual(result, { status: "aborted" });
  });
  await test("abort while fetch resolves prevents success publication", async () => {
    const controller = new AbortController();
    const result = await fetchExactMailboxMessage("main", googleSource, {
      signal: controller.signal,
      fetchImplementation: (async () => { controller.abort(); return http(payload()); }) as typeof fetch,
    });
    assert.deepEqual(result, { status: "aborted" });
  });
  await test("abort during JSON parse prevents success publication", async () => {
    const controller = new AbortController();
    const result = await fetchExactMailboxMessage("main", googleSource, {
      signal: controller.signal,
      fetchImplementation: (async () => ({ ok: true, json: async () => { controller.abort(); return payload(); } })) as typeof fetch,
    });
    assert.deepEqual(result, { status: "aborted" });
  });
  await test("malformed JSON returns invalid_response", async () => {
    const result = await fetchExactMailboxMessage("main", googleSource, {
      fetchImplementation: (async () => ({ ok: true, json: async () => { throw new Error("invalid"); } })) as typeof fetch,
    });
    assert.deepEqual(result, { status: "invalid_response" });
  });
  await test("request source is captured before caller mutation", async () => {
    const source = { provider: "google" as const, providerMessageId: "provider-1" };
    const result = await fetchExactMailboxMessage("main", source, {
      fetchImplementation: (async () => { source.providerMessageId = "other"; return http(payload()); }) as typeof fetch,
    });
    assert.equal(result.status, "success");
  });
  console.log(`${tests} exact mailbox API tests passed`);
}
void main().catch((error) => { console.error(error); process.exitCode = 1; });
