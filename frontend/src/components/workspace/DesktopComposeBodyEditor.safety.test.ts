import assert from "node:assert/strict";
import React, { createElement, createRef } from "react";
// Sucrase uses the classic JSX runtime in this repository's node tests.
(globalThis as any).React = React;
import { act, create } from "react-test-renderer";
import { DesktopComposeBodyEditor, type DesktopComposeBodyEditorHandle } from "./DesktopComposeBodyEditor";
import { MailboxSnapshotContext } from "../../lib/MailboxSnapshotContext";
import { useComposeField, useComposeSafety } from "../../lib/useComposeSafety";
import { captureDraftComposition, type ComposeSafety } from "../../lib/composeSafety";

const abort = new AbortController();
const session = { owner: { userId: "test-owner", workspaceId: "test-workspace" }, epoch: "test-session", persistent: false,
  signal: abort.signal, isActive: () => !abort.signal.aborted };
const connection = () => "connection";
const handle = createRef<DesktopComposeBodyEditorHandle>();
const node = { innerHTML: "", querySelector: () => null };
let renderCount = 0;
let field = "";
let setField: (value: string) => void;
let safety: ComposeSafety;
function Editor() {
  renderCount++;
  safety = useComposeSafety(connection);
  [field, setField] = useComposeField(safety, "");
  return createElement(DesktopComposeBodyEditor, { ref: handle, bodyHtml: "<p>Signature</p>", className: "editor",
    quoteExpanded: false, setQuoteExpanded: () => {}, showQuoteDisclosure: false,
    signatureSelection: "none", setSignatureSelection: () => {}, onBeforeBodyChange: safety.edit });
}
let mounted: ReturnType<typeof create>;
act(() => {
  mounted = create(createElement(MailboxSnapshotContext.Provider, { value: session }, createElement(Editor)), {
    createNodeMock: element => element.type === "div" ? node : null,
  });
});
const initialRenders = renderCount;
node.innerHTML = "<p>Last keystroke!</p><p>Signature</p>";
// Intentionally no onInput first: the action boundary must see the exact DOM.
assert.equal(handle.current!.getBodyHtml(), node.innerHTML);
assert.equal(safety!.identity().revision, 1);
assert.equal(renderCount, initialRenders, "Typing revision tracking must not rerender the mailbox");
act(() => setField!("bcc@example.test"));
assert.equal(field, "bcc@example.test");
const beforePendingRevision = safety!.identity().revision;
const snapshot = captureDraftComposition(safety!.identity(), safety!.scope("mailbox")!, {
  to: "", cc: "", bcc: field, subject: "", bodyHtml: handle.current!.getBodyHtml(), bodyText: "Last keystroke! Signature",
  mobileBodyText: "", baseBodyHtml: "<p>Signature</p>", signatureSelection: "none", signature: null,
  mode: "new", source: null, replyAnchor: null, attachments: [],
});
const operation = safety!.begin("send", snapshot)!;
assert(operation);
act(() => setField!("must-not-replace@example.test"));
assert.equal(field, "bcc@example.test");
node.innerHTML = "<p>Must not replace pending snapshot</p>";
act(() => mounted!.root.findByProps({ role: "textbox" }).props.onInput());
assert.equal(node.innerHTML, snapshot.bodyHtml, "Synchronous fence also protects the pre-render pending interval");
assert.equal(handle.current!.applyBodyTransform(() => "replacement"), snapshot.bodyHtml);
assert.equal(safety!.identity().revision, beforePendingRevision);
assert.equal(safety!.finish(operation), true);
node.innerHTML = "<p>Next revision</p>";
act(() => mounted!.root.findByProps({ role: "textbox" }).props.onInput());
assert.equal(handle.current!.getBodyHtml(), node.innerHTML);
assert(safety!.identity().revision > beforePendingRevision);
assert.equal(snapshot.bodyHtml, "<p>Last keystroke!</p><p>Signature</p>", "Captured operation remains immutable");
act(() => abort.abort());
assert.equal(safety!.scope("mailbox"), null);
assert.equal(safety!.readLocalCopy(), null);
assert.equal(safety!.edit(), false);
act(() => mounted!.unmount());
assert.equal(safety!.isCurrent(operation), false);
console.log("Mounted editor: last keystroke, revision containment, pending field/DOM fences and logout cleanup passed");
