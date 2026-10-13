import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import "sucrase/register/tsx.js";

const { MicrosoftComposePanel } = require(
  resolve(process.cwd(), "src/components/workspace/MicrosoftComposePanel.tsx"),
) as typeof import("./MicrosoftComposePanel");

const props = {
  mailboxId: "microsoft-0123456789abcdefabcd12345678",
  onClose: () => {},
  onOpenSent: () => {},
};
const newMarkup = renderToStaticMarkup(createElement(MicrosoftComposePanel, {
  ...props, mode: "new",
}));
assert.match(newMarkup, /New message/);
assert.match(newMarkup, />To</);
assert.match(newMarkup, /Cc \(optional\)/);
assert.match(newMarkup, /Subject/);
assert.match(newMarkup, /Message/);
assert.match(newMarkup, /Plain text only/);
assert.match(newMarkup, /Send/);
assert.match(newMarkup, /disabled=""/);
assert.doesNotMatch(newMarkup, /Reply to:/);

const replyMarkup = renderToStaticMarkup(createElement(MicrosoftComposePanel, {
  ...props, mode: "reply", sourceProviderMessageId: "source-id",
  sourceDisplay: "Artist <artist@example.com>",
}));
assert.match(replyMarkup, /Reply to:/);
assert.match(replyMarkup, /Artist &lt;artist@example.com&gt;/);
assert.match(replyMarkup, /Reply-To header/);
assert.doesNotMatch(replyMarkup, />Cc \(optional\)</);
assert.doesNotMatch(replyMarkup, />Subject</);

const source = readFileSync(resolve(__dirname,"MicrosoftComposePanel.tsx"),"utf8");
assert.match(source, /createMicrosoftSendAttemptId\(/);
assert.match(source, /pending\.current = true/);
assert.match(source, /attempted \|\| accepted/);
assert.match(source, /sendMicrosoftMessage\(/);
assert.match(source, /checkMicrosoftSentStatus\(/);
assert.match(source, /Check Sent status/);
assert.match(source, /statusInFlight\.current/);
assert.match(source, /operationId\.current/);
assert.match(source, /sent_copy_confirmed/);
assert.match(source, /sent_copy_pending/);
assert.match(source, /not yet visible in Sent Items/);
assert.match(source, /Delivery to the recipient is not confirmed/);
assert.match(source, /if \(result\.ok\)/);
assert.match(source, /Do not send this message again before checking Sent and Drafts/);
assert.match(source, /Delivery is not confirmed/);
assert.doesNotMatch(source, /setInterval\(|setTimeout\(/);
assert.doesNotMatch(source, /dangerouslySetInnerHTML/);
const view = readFileSync(resolve(__dirname,"MicrosoftMailboxReadOnlyView.tsx"),"utf8");
assert.match(view, /<MicrosoftComposePanel/);
assert.match(view, /onClick=\{\(\) => openComposer\("new"\)\}/);
assert.match(view, /onClick=\{\(\) => openComposer\("reply"\)\}/);
assert.match(view, /sourceProviderMessageId=\{composer\.sourceProviderMessageId\}/);
assert.match(view, /folder === "Inbox" &&/);
assert.match(view, /selectFolder\("Sent", true\)/);

console.log("✓ Microsoft compose/reply explicit action and SSR UI contracts passed.");
