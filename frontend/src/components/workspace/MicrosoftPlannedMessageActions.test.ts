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
assert.match(markup, /border-\[var\(--workspace-accent-border\)\]/);
assert.match(markup, /bg-\[linear-gradient/);
assert.match(markup, /disabled=""/);
assert.match(markup, /Preview only/);
assert.doesNotMatch(markup, /href=|contentEditable|mailto:/);
assert.doesNotMatch(markup, /\bArchive\b|>Delete</);

const source = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftPlannedMessageActions.tsx"), "utf8",
);
assert.match(source, /setMoreOpen\(!moreOpen\)/);
assert.match(source, /"Archive", "Delete", unread \? "Mark as read" : "Mark as unread"/);
assert.match(source, /type="button"\s+disabled/);
assert.doesNotMatch(source, /fetch\(|sendMicrosoft|setMicrosoftMessageUnread\(/);

const view = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMailboxReadOnlyView.tsx"), "utf8",
);
assert.match(view, /MICROSOFT_READ_ONLY_PILOT && \(/);
assert.match(view, /<MicrosoftPlannedMessageActions unread=/);
assert.match(view, /shouldAutoMarkMicrosoftOpenedMessageRead\(/);
assert.match(view, /readWriteEnabled: MICROSOFT_AUTO_MARK_READ_RELEASED/);
assert.match(view, /flagTicket !== mutationTicket\.current/);
assert.match(view, /selectedIdRef\.current !== providerMessageId/);
assert.match(view, /applyConfirmedMicrosoftUnread\(item, providerMessageId, false\)/);
assert.match(view, /setMicrosoftMessageUnread\(\{/);
console.log("Microsoft future action bar and safeguarded auto-read wiring tests: PASS");
