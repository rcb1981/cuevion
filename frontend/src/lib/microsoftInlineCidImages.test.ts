import assert from "node:assert/strict";
import {
  discoverMicrosoftInlineCids, loadMicrosoftInlineCidImages,
  MAX_MICROSOFT_CID_IMAGE_BYTES,
} from "./microsoftInlineCidImages";
import type { MicrosoftAttachmentMetadata } from "./inboxConnectionApi";

const cid = "image001.png@msg";
const validHtml = '<table><tr><td><img alt="logo" data-microsoft-cid="' + cid + '"></td></tr></table>';
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==",
  "base64",
);
const attachment: MicrosoftAttachmentMetadata = {
  id: "AAMkAttachment.1/", name: "image001.png", size: png.length,
  contentType: "image/png", kind: "file", isInline: true, contentId: cid,
};
const request = {
  mailboxId: "microsoft-0123456789abcdefabcd12345678",
  providerMessageId: "AAMkExact.9/",
  folder: "Inbox" as const,
  attachments: [attachment],
  html: validHtml,
};
type Download = Parameters<typeof loadMicrosoftInlineCidImages>[2];

async function main() {
  assert.deepEqual(discoverMicrosoftInlineCids(validHtml), [cid]);
  assert.deepEqual(discoverMicrosoftInlineCids('<img src="https://tracker.invalid/">'), []);
  assert.deepEqual(discoverMicrosoftInlineCids('<img onerror="run()" data-microsoft-cid="ok">'), []);
  const calls: unknown[] = [];
  const good: Download = async (value) => {
    calls.push(value);
    return {
      ok: true, attachmentId: attachment.id, size: attachment.size,
      base64: png.toString("base64"), fileName: attachment.name,
    };
  };
  const data = await loadMicrosoftInlineCidImages(request, () => true, good);
  assert.deepEqual(Object.keys(data), [cid]);
  assert.equal(data[cid], "data:image/png;base64," + png.toString("base64"));
  assert.deepEqual(calls, [{
    mailboxId: request.mailboxId, providerMessageId: request.providerMessageId,
    attachmentId: attachment.id, folder: "Inbox",
  }]);

  calls.length = 0;
  assert.deepEqual(Object.keys(await loadMicrosoftInlineCidImages(
    {...request, attachments: [{...attachment, contentId: "other@msg"}]}, () => true, good,
  )), []);
  assert.equal(calls.length, 0);
  assert.deepEqual(Object.keys(await loadMicrosoftInlineCidImages(
    {...request, attachments: [attachment, {...attachment, id: "AAMkOther/"}]}, () => true, good,
  )), []); // ambiguous CID cannot select one
  assert.deepEqual(Object.keys(await loadMicrosoftInlineCidImages(
    {...request, attachments: [{...attachment, contentType: "image/svg+xml"}]}, () => true, good,
  )), []);
  assert.deepEqual(Object.keys(await loadMicrosoftInlineCidImages(
    {...request, attachments: [{...attachment, isInline: false}]}, () => true, good,
  )), []);
  assert.deepEqual(Object.keys(await loadMicrosoftInlineCidImages(
    {...request, attachments: [{...attachment, size: MAX_MICROSOFT_CID_IMAGE_BYTES + 1}]},
    () => true, good,
  )), []);
  calls.length = 0;
  assert.deepEqual(Object.keys(await loadMicrosoftInlineCidImages(
    {...request, folder: "Junk" as "Inbox"}, () => true, good,
  )), []);
  assert.equal(calls.length, 0);
  for (const folder of ["Sent", "Archive", "Drafts", "Deleted"] as const) {
    const extra = await loadMicrosoftInlineCidImages({...request, folder}, () => true, good);
    assert.equal(extra[cid], "data:image/png;base64," + png.toString("base64"));
    assert.deepEqual((calls[calls.length - 1] as {folder: string}).folder, folder);
  }
  assert.deepEqual(Object.keys(await loadMicrosoftInlineCidImages(request, () => false, good)), []);

  const invalid: Download = async () => ({
    ok: true, attachmentId: attachment.id, size: attachment.size,
    base64: Buffer.alloc(attachment.size, 0).toString("base64"),
    fileName: attachment.name,
  });
  assert.deepEqual(Object.keys(await loadMicrosoftInlineCidImages(request, () => true, invalid)), []);
  const mismatch: Download = async () => ({
    ok: true, attachmentId: "another", size: attachment.size,
    base64: png.toString("base64"), fileName: attachment.name,
  });
  assert.deepEqual(Object.keys(await loadMicrosoftInlineCidImages(request, () => true, mismatch)), []);

  let currentlyActive = true;
  const stale: Download = async () => {
    currentlyActive = false;
    return {ok: true, attachmentId: attachment.id, size: attachment.size,
      base64: png.toString("base64"), fileName: attachment.name};
  };
  assert.deepEqual(Object.keys(await loadMicrosoftInlineCidImages(
    request, () => currentlyActive, stale,
  )), []); // image cannot cross a message change

  console.log("Microsoft exact CID image ownership, MIME, size, and stale-request tests passed.");
}
void main().catch((error) => {console.error(error); process.exitCode = 1;});
