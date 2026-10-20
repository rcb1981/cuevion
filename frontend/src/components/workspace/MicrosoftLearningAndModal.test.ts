import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "sucrase/register/tsx.js";
const { MicrosoftLearningControls } = require(
  resolve(process.cwd(), "src/components/workspace/MicrosoftLearningControls.tsx"),
) as typeof import("./MicrosoftLearningControls");
import {
  classifyMicrosoftFilteredMessage,
  microsoftVisibleFolderRows,
} from "./microsoftVirtualFiltered";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";

const markup = renderToStaticMarkup(React.createElement(MicrosoftLearningControls, {
  from: "Paul <paul@example.com>",
  disabled: false,
  canShowLess: true,
  onSave: () => true,
}));
assert.match(markup, /Learning/);
assert.match(markup, /aria-haspopup="dialog"/);
assert.doesNotMatch(markup, /Show less from this sender/);
const disabledMarkup = renderToStaticMarkup(React.createElement(MicrosoftLearningControls, {
  from: "invalid", disabled: false, canShowLess: true, onSave: () => true,
}));
assert.match(disabledMarkup, /disabled=""/);
const item = {
  providerFolder: "INBOX", providerMessageId: "graph-1", from: "Paul <paul@example.com>",
  categoryConfidence: "medium", signal: "Normal",
} as LiveInboxMessageSnapshot;
const showLess = { "paul@example.com": { priority: "show_less" as const, label: "Promo" as const } };
assert.equal(classifyMicrosoftFilteredMessage(item, {}, {}, showLess), "Filtered");
assert.equal(microsoftVisibleFolderRows([item], "Filtered", {}, {}, true, showLess).length, 1);
assert.equal(classifyMicrosoftFilteredMessage({ ...item, signal: "Priority" }, {}, {}, showLess), "Inbox");
assert.equal(classifyMicrosoftFilteredMessage(item, {}, { "paul@example.com": "show_less" },
  { "paul@example.com": { priority: "important", label: "Demo" } }), "Inbox");

const view = readFileSync(resolve(process.cwd(), "src/components/workspace/MicrosoftMailboxReadOnlyView.tsx"), "utf8");
const row = readFileSync(resolve(process.cwd(), "src/components/workspace/MicrosoftMessageListRow.tsx"), "utf8");
const popup = readFileSync(resolve(process.cwd(), "src/components/workspace/MicrosoftFullMessageModal.tsx"), "utf8");
assert.match(view, /<MicrosoftLearningControls/);
assert.match(view, /onSave=\{saveSenderLearning\}/);
assert.match(view, /setFullMessageTargetId\(null\)/);
assert.match(view, /selectedMessage\?\.providerMessageId === fullMessageTargetId/);
assert.match(view, /<MicrosoftFullMessageModal/);
assert.match(view, /attachments=\{attachmentRows\}/);
assert.match(row, /onDoubleClick=\{\(\) => onOpenFullMessage\?\.\(id\)\}/);
assert.match(popup, /aria-modal="true"/);
assert.match(popup, /onKey/);
assert.match(popup, /<MicrosoftInertHtmlStage/);
assert.doesNotMatch(popup, /sendMicrosoftMessage|deleteMicrosoftMessage|allow-same-origin|allow-scripts/);
console.log("Microsoft Learning UI, protected rerouting and guarded full-message popup PASS");
