import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "sucrase/register/tsx.js";

const { MicrosoftMessageListRow } = require(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMessageListRow.tsx"),
) as typeof import("./MicrosoftMessageListRow");

const base = {
  id: "graph-msg-1",
  providerMessageId: "graph-msg-1",
  sender: "Demo Sender",
  subject: "Track feedback",
  snippet: "Please take a listen.",
  createdAt: "2026-10-14T10:00:00Z",
  from: "demo@example.com",
  to: "owner@example.com",
  timestamp: "October 14",
  body: [],
  unread: true,
  hasAttachments: false,
};

function row(
  overrides: Partial<typeof base> = {},
  folder: "Inbox" | "Sent" = "Inbox",
  selected = false,
) {
  return renderToStaticMarkup(createElement(MicrosoftMessageListRow, {
    message: { ...base, ...overrides },
    folder,
    selected,
    disabled: false,
    dateLabel: "Oct 14, 12:00",
    onOpenMessage: () => {},
  }));
}

const unread = row();
assert.match(unread, /data-read-state="unread"/);
assert.match(unread, /aria-pressed="false"/);
assert.match(unread, /Unread message\./);
assert.match(unread, /bg-moss/);
assert.match(unread, /font-semibold/);
assert.match(unread, /Demo Sender/);
assert.match(unread, /Oct 14, 12:00/);

const withAttachment = row({ hasAttachments: true });
assert.match(withAttachment, /Has attachments/);
assert.match(withAttachment, />Attachment</);
assert.doesNotMatch(row({ hasAttachments: false }), /Has attachments/);

const read = row({ unread: false });
assert.match(read, /data-read-state="read"/);
assert.match(read, /Read message\./);
assert.doesNotMatch(read, /Unread message\./);
assert.match(read, /bg-transparent/);
assert.match(read, /border-l-transparent/);
assert.match(read, /font-normal/);

const selectedUnread = row({}, "Inbox", true);
assert.match(selectedUnread, /aria-pressed="true"/);
assert.match(selectedUnread, /data-read-state="unread"/);
assert.match(selectedUnread, /border-l-moss/);
assert.match(selectedUnread, /bg-moss/);

const selectedRead = row({ unread: false }, "Inbox", true);
assert.match(selectedRead, /aria-pressed="true"/);
assert.match(selectedRead, /data-read-state="read"/);
assert.match(selectedRead, /border-l-moss/);
assert.doesNotMatch(selectedRead, /Unread message\./);

const sent = row({ unread: true }, "Sent", true);
assert.match(sent, /data-read-state="sent"/);
assert.doesNotMatch(sent, /Unread message\.|Read message\./);
assert.match(sent, /bg-transparent/);

assert.equal(
  row({ providerMessageId: undefined as unknown as string }),
  "",
  "Missing opaque message IDs cannot create actionable rows",
);

const rowSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMessageListRow.tsx"),
  "utf8",
);
const viewerSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMailboxReadOnlyView.tsx"),
  "utf8",
);
const openMessageStart = viewerSource.indexOf("const openMessage = useCallback");
const openMessageEnd = viewerSource.indexOf("const changeReadState = useCallback");
assert.ok(openMessageStart >= 0 && openMessageEnd > openMessageStart);
const openMessageCode = viewerSource.slice(openMessageStart, openMessageEnd);
assert.doesNotMatch(openMessageCode, /setMicrosoftMessageUnread|applyConfirmedMicrosoftUnread/);
assert.match(rowSource, /onClick=\{\(\) => onOpenMessage\(id\)\}/);
assert.doesNotMatch(rowSource, /fetch\(|setMicrosoftMessageUnread/);
assert.match(viewerSource, /<MicrosoftMessageListRow/);

console.log("Microsoft read/unread row visual and no-mutation tests: PASS");
