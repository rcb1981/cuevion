import assert from "node:assert/strict";
import { updateMicrosoftEditableDraft, validMicrosoftDraftEdit } from "./inboxConnectionApi";

async function run() {
  const originalFetch = globalThis.fetch;
  let fetches = 0;
  globalThis.fetch = (async () => {
    fetches++;
    throw Error("test should not contact network");
  }) as typeof globalThis.fetch;
  try {
    const base = {
      action: "save" as const, mailboxId: "microsoft-12345", providerMessageId: "AAMkDraft01",
      etag: 'W/"CQAAABYAAA=="',
      edit: { subject: "Demo", to: ["artist@example.com"], cc: [], body: "Hi" },
    };
    assert.equal(validMicrosoftDraftEdit(base.edit), true);
    for (const edit of [
      { ...base.edit, to: null }, { ...base.edit, cc: null },
      { ...base.edit, to: "artist@example.com" },
      { ...base.edit, to: ["BAD@EXAMPLE.COM"] },
      { ...base.edit, to: ["artist@example.com"], cc: ["artist@example.com"] },
      { ...base.edit, subject: "Bad\r\nSubject" },
      { ...base.edit, body: "A\0B" },
    ]) {
      assert.equal(validMicrosoftDraftEdit(edit), false);
      const r = await updateMicrosoftEditableDraft({ ...base, edit } as any);
      assert.equal(r.ok, false);
      if (!r.ok) assert.equal(r.error.code, "invalid_request");
    }
    const invalidSources = ["../messages", "bad?#", ".", "two words", ""];
    for (const providerMessageId of invalidSources) {
      const r = await updateMicrosoftEditableDraft({ ...base, providerMessageId });
      assert.equal(r.ok, false);
    }
    assert.equal(fetches, 0, "invalid draft edits must never reach Graph or HTTP");
    console.log("Microsoft Drafts client no-network tests passed");
  } finally { globalThis.fetch = originalFetch; }
}
void run().catch(error => { console.error(error); process.exitCode = 1; });
