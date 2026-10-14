import assert from "node:assert/strict";
import { downloadMicrosoftAttachmentFile } from "./inboxConnectionApi";

const BOX = "microsoft-0123456789abcdefabcd12345678";
const MESSAGE = "AAMkMessage.1/";
const ATTACHMENT = "AAMkAttachment.2/";
const file = {
  ok: true, attachmentId: ATTACHMENT, fileName: "demo.wav",
  size: 4, base64: "dGVzdA==",
};

async function test() {
  const original = globalThis.fetch;
  const calls: Array<{url: string; init?: RequestInit}> = [];
  function mock(value: unknown, status = 200) {
    calls.length = 0;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({url, init});
      return {
        ok: status >= 200 && status < 300,
        status, json: async () => value,
      } as Response;
    }) as typeof fetch;
  }
  const request = {
    mailboxId: BOX, providerMessageId: MESSAGE, attachmentId: ATTACHMENT,
    folder: "Inbox" as const,
  };
  try {
    mock(file);
    const ok = await downloadMicrosoftAttachmentFile(request);
    assert.equal(ok.ok, true);
    assert.equal(ok.base64, file.base64);
    assert.equal(ok.fileName, file.fileName);
    assert.deepEqual(JSON.parse(String(calls[0]?.init?.body)), request);
    assert.equal(calls[0]?.url, "/api/inboxes/download-microsoft-attachment");
    assert.equal(calls[0]?.init?.method, "POST");
    assert.equal(calls[0]?.init?.credentials, "include");

    for (const extra of [
      { ...file, token: "must-not-leak" },
      { ...file, attachmentId: "other" },
      { ...file, fileName: "../private.html" },
      { ...file, fileName: "evil\nname.pdf" },
      { ...file, size: 2 * 1024 * 1024 + 1 },
      { ...file, base64: "unsafe bytes!" },
      { ...file, base64: "dGVzdA=" },
    ]) {
      mock(extra);
      assert.equal((await downloadMicrosoftAttachmentFile(request)).ok, false);
    }

    mock({ok: true, ...file, size: 0, base64: ""});
    assert.equal((await downloadMicrosoftAttachmentFile(request)).ok, true);

    mock({ok: false, error: {code: "microsoft_attachment_too_large"}}, 413);
    const tooLarge = await downloadMicrosoftAttachmentFile(request);
    assert.equal(tooLarge.ok, false);
    assert.equal(tooLarge.error?.code, "microsoft_attachment_too_large");

    for (const invalid of [
      {...request, folder: "Junk" as "Inbox"},
      {...request, attachmentId: "bad?#"},
      {...request, providerMessageId: "../other"},
      {...request, mailboxId: "other mailbox"},
    ]) {
      mock(file);
      assert.equal((await downloadMicrosoftAttachmentFile(invalid)).ok, false);
      assert.equal(calls.length, 0);
    }
  } finally {
    globalThis.fetch = original;
  }
  console.log("Microsoft read-only file download client tests passed");
}

void test();
