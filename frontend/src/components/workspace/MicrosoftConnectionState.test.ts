import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import "sucrase/register/tsx.js";
const {
  MicrosoftConnectionState, describeMicrosoftConnectionStatus,
} = require(resolve(process.cwd(), "src/components/workspace/MicrosoftConnectionState.tsx"));

for (const [status, label] of [
  ["checking", "CHECKING"],
  ["connected", "CONNECTED"],
  ["temporary_issue", "TEMPORARY ISSUE"],
  ["action_required", "ACTION REQUIRED"],
] as const) {
  const html = renderToStaticMarkup(
    createElement(MicrosoftConnectionState, {status})
  );
  assert.match(html, new RegExp(label));
  assert.match(html, /h-2\.5 w-2\.5/);
  assert.ok(describeMicrosoftConnectionStatus(status).length > 20);
  assert.ok(!html.includes("Microsoft mailbox not connected"));
}
assert.match(renderToStaticMarkup(createElement(MicrosoftConnectionState, {
  status:"connected",
})), /role="status"/);
assert.match(renderToStaticMarkup(createElement(MicrosoftConnectionState, {
  status:"action_required", onActionRequired:()=>{},
})), /<button type="button"/);

const view = readFileSync(resolve(process.cwd(),"src/components/workspace/MicrosoftMailboxReadOnlyView.tsx"),"utf8");
assert.match(view, /reportGraphStatus\("checking"\)/);
assert.match(view, /reportGraphStatus\("connected"\)/);
assert.match(view, /reportGraphStatus\("temporary_issue"\)/);
assert.match(view, /"action_required" : "temporary_issue"/);
assert.match(view, /ticket !== inboxTicket\.current/);
assert.match(view, /<MicrosoftConnectionState/);
assert.match(view, /status=\{connectionStatus\}/);
const shell=readFileSync(resolve(process.cwd(),"src/components/workspace/WorkspaceShell.tsx"),"utf8");
assert.match(shell, /microsoftConnectionStatuses=\{microsoftConnectionStatuses\}/);
assert.match(shell, /microsoftLinkedMailboxIds=\{microsoftLinkedMailboxIds\}/);
assert.match(shell, /Microsoft account linked\. Open the mailbox to verify its connection\./);
assert.match(shell, /getSessionViewerStorageKey/);
assert.match(shell, /existing\?\.viewerScope === auth0ViewerStorageScope/);
assert.match(shell, /<MicrosoftConnectionState status=\{microsoftConnectionStatuses\[inbox\.id\]\}/);
assert.match(shell, /onConnectionStatusChange=\{\(status\) => \{/);
console.log("Microsoft Graph-backed CHECKING/CONNECTED/ISSUE/RECOVERY header and overview parity PASS");
