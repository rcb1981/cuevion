import assert from "node:assert/strict";
import { validateMicrosoftDraftFiles, prepareMicrosoftDraftFiles, validMicrosoftDraftFileMetadata } from "./microsoftDraftAttachmentPolicy";

async function test() {
  const small = { name: "same.txt", contentType: "text/plain", base64: "eA==" };
  const twoMiB = { ...small, base64: Buffer.alloc(2*1024*1024, 120).toString("base64") };
  assert.equal(validateMicrosoftDraftFiles([twoMiB]), true);
  assert.equal(validateMicrosoftDraftFiles([twoMiB, twoMiB, twoMiB, twoMiB]), true);
  assert.equal(validateMicrosoftDraftFiles(Array(10).fill(small)), true);
  assert.equal(validateMicrosoftDraftFiles(Array(11).fill(small)), false);
  assert.equal(validateMicrosoftDraftFiles([twoMiB, twoMiB, twoMiB, twoMiB, small]), false);
  assert.equal(validateMicrosoftDraftFiles([{ ...small, base64: Buffer.alloc(2*1024*1024+1).toString("base64") }]), false);
  assert.equal(validateMicrosoftDraftFiles([small, small]), true); // Duplicate names/content remain distinct intent slots.
  for (const files of [null, [], [{ ...small, base64: "eB==" }], [{ ...small, base64: "eA==\n" }], [{ ...small, extra: true }]]) {
    assert.equal(validateMicrosoftDraftFiles(files), false);
  }
  for (const name of ["../secret", "a\\b.txt", "x\r\n.txt", "\u202eevil.exe", "\u0085bad", "\ud800", "", ".."]) {
    assert.equal(validMicrosoftDraftFileMetadata(name, "text/plain"), false);
  }
  assert.equal(validMicrosoftDraftFileMetadata("résumé 🎵.pdf", "application/pdf"), true);
  let reads = 0;
  const file = (size: number, actual = size) => ({ name: "same.txt", type: "text/plain", size,
    arrayBuffer: async () => { reads++; return new Uint8Array(actual).buffer; } }) as File;
  const prepared = await prepareMicrosoftDraftFiles([file(4), file(4)]);
  assert.equal(prepared.length, 2); assert.equal(reads, 2);
  assert.equal(validateMicrosoftDraftFiles(prepared), true);
  reads = 0;
  await assert.rejects(prepareMicrosoftDraftFiles([file(2*1024*1024+1)]), /selection_invalid/);
  await assert.rejects(prepareMicrosoftDraftFiles(Array(11).fill(file(1))), /selection_invalid/);
  await assert.rejects(prepareMicrosoftDraftFiles(Array(5).fill(file(2*1024*1024))), /selection_invalid/);
  assert.equal(reads, 0);
  await assert.rejects(prepareMicrosoftDraftFiles([file(4, 5)]), /selection_invalid/);
}
void test().catch(error => { console.error(error); process.exitCode = 1; });
