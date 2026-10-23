import assert from "node:assert/strict";
import { updateMicrosoftRichDraftMetadata, validMicrosoftRichDraftMetadata } from "./inboxConnectionApi";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import "sucrase/register/tsx.js";

const { MicrosoftRichDraftMetadataEditor } = require(
  resolve(process.cwd(), "src/components/workspace/MicrosoftRichDraftMetadataEditor.tsx"),
) as typeof import("../components/workspace/MicrosoftRichDraftMetadataEditor");

async function test() {
  const props = {
    action: "save_rich" as const, mailboxId: "microsoft-12345",
    providerMessageId: "AAMkDraft01", etag: 'W/"CQAAABYAAA=="',
    metadata: { subject: "Subject", to: ["artist@example.com"], cc: [] },
  };
  assert.equal(validMicrosoftRichDraftMetadata(props.metadata), true);
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (async () => { calls++; throw Error("Unexpected network"); }) as typeof fetch;
  try {
    for (const metadata of [
      { ...props.metadata, body: "Trying to write rich HTML" },
      { ...props.metadata, cc: null },
      { ...props.metadata, to: null },
      { ...props.metadata, to: ["artist@example.com"], cc: ["artist@example.com"] },
      { ...props.metadata, to: ["not an email"] },
      { ...props.metadata, subject: "Injected\r\nTo: evil@example.com" },
      { ...props.metadata, to: ["UPPER@EXAMPLE.COM"] },
    ]) {
      assert.equal(validMicrosoftRichDraftMetadata(metadata), false);
      const result = await updateMicrosoftRichDraftMetadata({ ...props, metadata } as any);
      assert.equal(result.ok, false);
      if (!result.ok) assert.equal(result.error.code, "invalid_request");
    }
    for (const providerMessageId of ["../secret", "A?#", "a b", "", "."]) {
      const result = await updateMicrosoftRichDraftMetadata({ ...props, providerMessageId });
      assert.equal(result.ok, false);
    }
    assert.equal(calls, 0);
  } finally { globalThis.fetch = originalFetch; }

  const markup = renderToStaticMarkup(createElement(MicrosoftRichDraftMetadataEditor, {
    mailboxId: props.mailboxId, providerMessageId: props.providerMessageId,
    onClose: () => {}, onSaved: () => {},
  }));
  assert.match(markup, /HTML formatting is preserved/);
  assert.match(markup, /Edit draft details/);
  assert.doesNotMatch(markup, /dangerouslySetInnerHTML/);
  const view = readFileSync(resolve(process.cwd(), "src/components/workspace/MicrosoftMailboxReadOnlyView.tsx"), "utf8");
  assert.match(view, /const MICROSOFT_DRAFT_EDIT_ACTIONS_RELEASED = false/);
  assert.match(view, /<MicrosoftRichDraftMetadataEditor/);
  assert.match(view, /selectedMessage\.hasAttachments/);
  assert.match(view, /selectedMessage\.bodyHtml/);
  console.log("Microsoft rich-HTML Draft metadata UI validation and release gate: PASS");
}
void test().catch(error => { console.error(error); process.exitCode = 1; });
