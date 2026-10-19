import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "sucrase/register/tsx.js";

const { MicrosoftMessageListControls } = require(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMessageListControls.tsx"),
) as typeof import("./MicrosoftMessageListControls");

const defaults = {
  mailboxId: "microsoft-0123456789abcdefabcd12345678",
  searchQuery: "",
  onSearchChange: () => {},
  unreadOnly: false,
  onUnreadOnlyChange: () => {},
  canFilterUnread: true,
  sortOrder: "newest" as const,
  onSortChange: () => {},
  refreshBusy: false,
  refreshDisabled: false,
  onRefresh: () => {},
};

const html = renderToStaticMarkup(createElement(MicrosoftMessageListControls, defaults));
assert.match(html, /Microsoft loaded message controls/);
assert.match(html, /Search mail/);
assert.match(html, /Search loaded Microsoft messages/);
assert.match(html, /aria-label="Filter loaded messages"/);
assert.match(html, /aria-pressed="true"[^>]*>All<\/button>/);
assert.match(html, /aria-pressed="false"[^>]*>Unread<\/button>/);
assert.match(html, /disabled=""[^>]*aria-pressed="false"[^>]*>Priority<\/button>/);
assert.match(html, /Priority filtering for Microsoft is not available yet/);
assert.match(html, /Refresh Microsoft messages/);
assert.match(html, /Newest first/);
assert.match(html, /Oldest first/);
assert.match(html, /microsoft-list-search-microsoft-0123456789abcdefabcd12345678/);
assert.doesNotMatch(html, /type="submit"|href=|mailto:/);

const unreadMarkup = renderToStaticMarkup(createElement(
  MicrosoftMessageListControls, { ...defaults, unreadOnly: true, sortOrder: "oldest" },
));
assert.match(unreadMarkup, /aria-pressed="true"[^>]*>Unread<\/button>/);
assert.match(unreadMarkup, /aria-pressed="false"[^>]*>All<\/button>/);
assert.match(unreadMarkup, /value="oldest"/);

const sentMarkup = renderToStaticMarkup(createElement(
  MicrosoftMessageListControls, { ...defaults, canFilterUnread: false, refreshBusy: true, refreshDisabled: true },
));
assert.match(sentMarkup, /disabled=""[^>]*aria-pressed="false"[^>]*>Unread<\/button>/);
assert.match(sentMarkup, /aria-label="Refreshing Microsoft messages"/);
assert.match(sentMarkup, /animate-spin/);

const src = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMessageListControls.tsx"), "utf8",
);
assert.match(src, /onUnreadOnlyChange\(false\)/);
assert.match(src, /onUnreadOnlyChange\(true\)/);
assert.match(src, /onSortChange\(event.target.value as MicrosoftMessageSortOrder\)/);
assert.match(src, /onClick=\{onRefresh\}/);
assert.doesNotMatch(src, /\bfetch\(|sendMicrosoft|setMicrosoftMessageUnread|localStorage|sessionStorage|Mail.Send|Mail.ReadWrite/);

console.log("Microsoft list actions: real All/Unread/sort/read-only refresh, Priority preview disabled PASS");
