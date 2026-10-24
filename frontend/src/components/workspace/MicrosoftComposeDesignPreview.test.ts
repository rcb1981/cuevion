import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "sucrase/register/tsx.js";

const { MicrosoftComposeDesignPreview } = require(
  resolve(process.cwd(), "src/components/workspace/MicrosoftComposeDesignPreview.tsx"),
) as typeof import("./MicrosoftComposeDesignPreview");

const html = renderToStaticMarkup(createElement(MicrosoftComposeDesignPreview, {
  mailboxEmail: "owner@example.com",
  onClose: () => {},
}));

assert.match(html, /data-microsoft-compose-design-preview="true"/);
assert.match(html, /data-theme="light"/);
assert.match(html, /role="dialog"/);
assert.match(html, /aria-modal="true"/);
assert.match(html, /New message/);
assert.match(html, /owner@example\.com/);
assert.match(html, /Recipients/);
assert.match(html, /Subject/);
assert.match(html, /Write your message/);
assert.match(html, /Design preview only/);
assert.match(html, /Nothing is sent or saved/);
assert.match(html, /Close compose design preview/);
assert.match(html, /Discard compose design preview/);
assert.match(html, /Formatting options \(preview only\)/);
assert.match(html, /Attach file \(preview only\)/);
assert.match(html, /Insert link \(preview only\)/);
assert.match(html, /Insert emoji \(preview only\)/);
assert.match(html, /aria-expanded="false"[^>]*>Cc<\/button>/);
assert.match(html, /aria-expanded="false"[^>]*>Bcc<\/button>/);
assert.match(html, /disabled=""[^>]*>.*?Send<\/button>/);
assert.doesNotMatch(html, /type="submit"|<form|mailto:|href=/);

const src = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftComposeDesignPreview.tsx"),
  "utf8",
);
assert.match(src, /createPortal\(preview, document\.body\)/);
assert.match(src, /previouslyFocused\?\.focus\(\)/);
assert.match(src, /event\.key === "Escape"/);
assert.match(src, /type="button"\s+disabled/);
assert.doesNotMatch(
  src,
  /\b(?:fetch|sendMicrosoftMessage|checkMicrosoftSentStatus|setMicrosoftMessageUnread|localStorage|sessionStorage|FormData|XMLHttpRequest)\s*(?:\(|\[|\.)/,
);
assert.doesNotMatch(src, /onSubmit|navigator\.sendBeacon|navigator\.share|<input[^>]*type="file"/);

const view = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMailboxReadOnlyView.tsx"),
  "utf8",
);
assert.match(view, /MICROSOFT_READ_ONLY_PILOT = true/);
assert.match(view, /setComposeDesignPreviewOpen\(true\)/);
assert.match(view, /MICROSOFT_READ_ONLY_PILOT && connected && composeDesignPreviewOpen && \(/);
assert.match(view, /<MicrosoftComposeDesignPreview/);
assert.match(view, /aria-label=\{MICROSOFT_READ_ONLY_PILOT && !MICROSOFT_COMPOSE_DRAFTS_RELEASED \? "Compose message \(design preview only\)"/);
assert.match(view, /onClick=\{\(\) => \{\s*if \(MICROSOFT_COMPOSE_DRAFTS_RELEASED\)/);
console.log("Microsoft compose design: visual-only, never sends/saves, keyboard safe PASS");
