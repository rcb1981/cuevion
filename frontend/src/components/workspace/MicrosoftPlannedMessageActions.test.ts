import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "sucrase/register/tsx.js";

const { MicrosoftPlannedMessageActions } = require(
  resolve(process.cwd(), "src/components/workspace/MicrosoftPlannedMessageActions.tsx"),
) as typeof import("./MicrosoftPlannedMessageActions");

const markup = renderToStaticMarkup(createElement(MicrosoftPlannedMessageActions, {unread: true}));
assert.match(markup, /Microsoft message actions preview/);
for (const name of ["Reply", "Reply All", "Forward"]) {
  assert.match(markup, new RegExp(name));
}
assert.match(markup, /aria-expanded="false"/);
assert.match(markup, /\bMore\b/);
assert.match(markup, /h-9 w-9/);
assert.match(markup, /rounded-full/);
assert.match(markup, /disabled=""/);
for (const action of ["Reply", "Reply All", "Forward", "Archive", "Attachments", "Delete", "Bookmark", "More"]) {
  assert.match(markup, new RegExp('aria-label="' + action + '"'), action + " must be visible");
}
const withoutMessage = renderToStaticMarkup(createElement(
  MicrosoftPlannedMessageActions, { unread: false, hasMessage: false },
));
assert.match(withoutMessage, /aria-label="More"[^>]*disabled=""/,
  "More must be disabled until a message is selected");
assert.match(markup, /Preview only/);
assert.doesNotMatch(markup, /href=|contentEditable|mailto:/);
for (const name of ["Archive", "Attachments", "Delete", "Bookmark"]) {
  assert.match(markup, new RegExp('aria-label="' + name + '"'));
}
assert.doesNotMatch(markup, /onClick=|type="submit"/);

const source = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftPlannedMessageActions.tsx"), "utf8",
);
assert.match(source, /setMoreOpen\(\(open\) => !open\)/);
assert.match(source, /unread \? "Mark as read" : "Mark as unread"/);
assert.match(source, /<ActionGlyph name=\{name\} \/>/);
assert.match(source, /type="button"\s+disabled/);
assert.doesNotMatch(source, /fetch\(|sendMicrosoft|setMicrosoftMessageUnread\(/);

const view = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMailboxReadOnlyView.tsx"), "utf8",
);
assert.match(view, /MICROSOFT_READ_ONLY_PILOT && \(/);
assert.match(view, /<MicrosoftPlannedMessageActions\s+unread=/);
assert.match(view, /shouldAutoMarkMicrosoftOpenedMessageRead\(/);
assert.match(view, /readWriteEnabled: MICROSOFT_AUTO_MARK_READ_RELEASED/);
assert.match(view, /flagTicket !== mutationTicket\.current/);
assert.match(view, /selectedIdRef\.current !== providerMessageId/);
assert.match(view, /applyConfirmedMicrosoftUnread\(item, providerMessageId, false\)/);
assert.match(view, /setMicrosoftMessageUnread\(\{/);
console.log("Microsoft future action bar and safeguarded auto-read wiring tests: PASS");
