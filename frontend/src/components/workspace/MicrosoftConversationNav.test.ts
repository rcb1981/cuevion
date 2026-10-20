import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "sucrase/register/tsx.js";
const { MicrosoftConversationNav } = require(
  resolve(process.cwd(),"src/components/workspace/MicrosoftConversationNav.tsx"),
) as typeof import("./MicrosoftConversationNav");
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";

const make = (id:string) => ({providerMessageId:id,createdAt:"2026-10-20T12:00:00Z",sender:id,subject:"Test",snippet:"Visible preview"}) as LiveInboxMessageSnapshot;
assert.equal(renderToStaticMarkup(React.createElement(MicrosoftConversationNav,{
  messages:[make("a")],selectedId:"a",disabled:false,onSelect:()=>{},
})), "");
const html=renderToStaticMarkup(React.createElement(MicrosoftConversationNav,{
  messages:[make("a"),make("b")],selectedId:"a",disabled:false,onSelect:()=>{},
}));
assert.match(html,/2 messages in this conversation/);
assert.match(html,/currently loaded page/);
assert.match(html,/aria-current="true"/);
assert.match(html,/disabled=""/);
assert.match(html,/Visible preview/);
const view=readFileSync(resolve(process.cwd(),"src/components/workspace/MicrosoftMailboxReadOnlyView.tsx"),"utf8");
assert.match(view,/getMicrosoftConversationRows\(folderRows, selectedId, mailbox\.id\)/);
assert.match(view,/conversationRows\.some\(\(row\) => row\.providerMessageId === id\)/);
assert.match(view,/onSelectConversationMessage/);
const modal=readFileSync(resolve(process.cwd(),"src/components/workspace/MicrosoftFullMessageModal.tsx"),"utf8");
assert.match(modal,/<MicrosoftConversationNav/);
assert.doesNotMatch(modal,/allow-same-origin|allow-scripts/);
console.log("Microsoft shared conversation navigator: list/modal render and guarded selection PASS");
