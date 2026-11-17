import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "sucrase/register/tsx.js";
import { mailboxFolderHeadingClass, mailboxFolderListClass, mailboxFolderSelectedClass } from "./mailboxFolderStyles";

const { MicrosoftMailboxFolderRail, microsoftVisualFolders } = require(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMailboxFolderRail.tsx"),
) as typeof import("./MicrosoftMailboxFolderRail");

const noop = () => {};
const rail = renderToStaticMarkup(createElement(MicrosoftMailboxFolderRail, {
  folder: "Filtered", disabled: false, onSelectFolder: noop,
}));
const compact = renderToStaticMarkup(createElement(MicrosoftMailboxFolderRail, {
  folder: "Inbox", disabled: false, onSelectFolder: noop, compact: true,
}));
assert.equal(microsoftVisualFolders.length, 7);
const withCompose = renderToStaticMarkup(createElement(MicrosoftMailboxFolderRail, {
  folder: "Inbox", disabled: false, onSelectFolder: noop,
  composeAction: createElement("button", {type: "button", "aria-label": "Compose preview"}, "Compose"),
}));
const compactWithCompose = renderToStaticMarkup(createElement(MicrosoftMailboxFolderRail, {
  folder: "Inbox", disabled: false, onSelectFolder: noop, compact: true,
  composeAction: createElement("button", {type: "button", "aria-label": "Compose preview"}, "Compose"),
}));
assert.match(withCompose, /Compose preview/);
assert.match(compactWithCompose, /Compose preview/);
assert.ok(withCompose.indexOf("Compose preview") < withCompose.indexOf("Folders"));
assert.match(rail, /Microsoft mail folders/);
assert.match(compact, /Microsoft mail folders \(compact\)/);
assert.match(rail, /aria-pressed="true"[^>]*><span[^>]*>Filtered<\/span><\/button>/);
assert.match(compact, /aria-pressed="true"[^>]*>Inbox<\/button>/);
for (const label of ["Inbox", "Filtered", "Sent", "Archive", "Drafts", "Trash", "Spam"]) {
  assert.ok(rail.includes(">" + label + "</span></button>"), label);
}
assert.deepEqual(microsoftVisualFolders.slice(-2).map(({ id }) => id), ["Deleted", "Junk"]);
assert.doesNotMatch(rail, /href=|type="submit"|mailto:/);
assert.match(rail, /xl:flex/);
assert.match(compact, /xl:hidden/);
assert.ok(rail.includes(mailboxFolderHeadingClass));
assert.ok(rail.includes(mailboxFolderListClass));
assert.ok(rail.includes(mailboxFolderSelectedClass));
assert.match(rail, /xl:pl-0/);
assert.doesNotMatch(compact, /xl:pl-0/);
const code = readFileSync(resolve(__dirname, "MicrosoftMailboxFolderRail.tsx"), "utf8");
assert.match(code, /onClick=\{\(\) => onSelectFolder\(item\.id\)\}/);
assert.doesNotMatch(code, /fetch\(|localStorage|sessionStorage|setMicrosoftMessageUnread|sendMicrosoft/);
console.log("Microsoft hybrid folder rail: no provider mutation, seven folders, responsive selection PASS");
