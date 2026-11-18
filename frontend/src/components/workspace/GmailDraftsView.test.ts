import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { transform } from "sucrase";
import React from "react";
import { act, create } from "react-test-renderer";
import { GmailDraftsView } from "./GmailDraftsView";
import { useGmailDrafts } from "../../lib/useGmailDrafts";
import { MailboxSnapshotContext } from "../../lib/MailboxSnapshotContext";
import { activateMailboxSnapshotSession, clearMailboxSnapshotSession, getMailboxSnapshotSession, MAILBOX_SESSION_KEY } from "../../lib/mailboxSnapshotSession";
(globalThis as any).React = React;
const previousWindow = (globalThis as any).window; const previousFetch = globalThis.fetch;
const values = new Map<string, string>(); const events = new EventTarget();
(globalThis as any).window = { localStorage: { getItem: (key: string) => values.get(key) ?? null,
  setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key), length: 0, key: () => null },
  addEventListener: events.addEventListener.bind(events), removeEventListener: events.removeEventListener.bind(events), dispatchEvent: events.dispatchEvent.bind(events) };
const scope = "a".repeat(64);
const draft = (id: string) => ({ draftId: id, messageId: `message-${id}`, threadId: "same-thread", from: "author@example.test",
  to: "to@example.test", cc: "cc@example.test", bcc: "private@example.test", subject: id, internalDate: null, headers: [] });
const full = (id: string) => ({ ...draft(id), previewStatus: "available", unavailableReason: null, bodyText: "Plain draft body", bodyHtml: "<p>HTML draft body</p>",
  inventoryComplete: true, attachments: [], mimeParts: [] });
const local = [{ id: "local", subject: "My temporary copy", to: "local@example.test", body: ["Keep this local preview"],
  bodyHtml: '<p><strong>Keep this local preview</strong></p><p data-compose-signature="true">Synthetic signature</p>' }];
const unchangedLocal = JSON.stringify(local);
let mailbox = "box"; let connection = "connected-1"; let visible = true; let mobile = false;
let latest!: ReturnType<typeof useGmailDrafts>; let root: ReturnType<typeof create>;
let firstPage = [draft("first"), draft("middle"), draft("last")];
let pending: Promise<Response> | null = null; let responseMode = "normal";
const calls: Array<{ mailboxId: string; pageToken?: string; draftId?: string }> = [];
globalThis.fetch = (async (url, init) => {
  assert.equal(url, "/api/inboxes/fetch-gmail-drafts"); assert.equal(init?.method, "POST");
  const request = JSON.parse(String(init?.body)); calls.push(request);
  if (pending) { const result = pending; pending = null; return result; }
  if (responseMode === "error") return new Response("private", { status: 502 });
  const draftDetail = responseMode === "unsupported" ? { ...full(request.draftId), previewStatus: "unavailable", unavailableReason: "unsupported_mime", bodyText: null, bodyHtml: null } : full(request.draftId);
  return new Response(JSON.stringify({ ok: true, mailboxId: request.mailboxId, scope,
    ...(request.draftId ? { draft: draftDetail } : { drafts: request.pageToken ? [draft("last"), draft("older")] : firstPage, nextPageToken: request.pageToken ? null : "next" }) }));
}) as typeof fetch;
function Harness() {
  latest = useGmailDrafts(mailbox, connection, visible);
  return React.createElement(GmailDraftsView, { drafts: latest, localPreviews: local, connected: !!connection, mobile,
    renderHtml: (html, origin) => React.createElement("iframe", { title: "Synthetic HTML preview", sandbox: "", srcDoc: html, "data-html-origin": origin }) });
}
const tree = () => React.createElement(MailboxSnapshotContext.Provider, { value: getMailboxSnapshotSession() }, React.createElement(Harness));
const settle = (action: () => void) => act(async () => { action(); await new Promise(done => setImmediate(done)); });
const click = (id: string) => settle(() => root.root.findByProps({ "data-gmail-draft-id": id }).props.onClick());
async function run() {
  try {
    activateMailboxSnapshotSession({ userId: "user-a", workspaceId: "workspace-a" });
    await settle(() => { root = create(tree()); });
    assert.equal(latest.drafts.length, 3);
    await click("middle"); assert.equal(latest.selectedId, "middle");
    assert.equal(root.root.findByProps({ "data-gmail-draft-bcc": true }).children.join(""), "Bcc: private@example.test");
    assert.equal(root.root.findByType("iframe").props.srcDoc, "<p>HTML draft body</p>");
    assert.equal(root.root.findByType("iframe").props["data-html-origin"], "provider");
    await settle(() => root.root.findAllByType("button").find(b => b.children.join("") === "Show plain text")!.props.onClick());
    assert.equal(root.root.findAllByType("iframe").length, 0);
    assert(JSON.stringify(root.toJSON()).includes("Plain draft body"));
    await settle(() => { void latest.refresh(); });
    assert.equal(latest.selectedId, "middle");
    await settle(() => { void latest.loadMore(); });
    assert.equal(latest.selectedId, "middle"); assert.equal(latest.drafts.length, 4);
    const beforeLocal = calls.length;
    await settle(() => root.root.findByProps({ "data-local-draft-id": "local" }).props.onClick());
    assert.equal(calls.length, beforeLocal); assert(JSON.stringify(root.toJSON()).includes("Not saved to Gmail"));
    assert(JSON.stringify(root.toJSON()).includes("Keep this local preview"));
    assert.equal(root.root.findByType("iframe").props["data-html-origin"], "local");
    assert.equal(root.root.findByType("iframe").props.srcDoc, local[0].bodyHtml);
    mobile = true; await settle(() => root.update(tree())); await click("first");
    assert.equal(root.root.findByProps({ "data-gmail-drafts-list": true }).props.className, "hidden");
    await settle(() => root.root.findAllByType("button").find(b => b.children.join("") === "Back to drafts")!.props.onClick());
    assert.equal(root.root.findByProps({ "data-gmail-drafts-reader": true }).props.className, "hidden");
    responseMode = "unsupported"; await click("first");
    assert(JSON.stringify(root.toJSON()).includes("Preview unavailable"));
    assert.equal(root.root.findAllByType("iframe").length, 0); responseMode = "normal";
    assert(!root.root.findAllByType("button").some(b => /^(Send|Save|Discard|Delete|Reply|Forward)$/.test(b.children.join(""))));
    let complete!: (response: Response) => void;
    pending = new Promise(done => { complete = done; });
    await settle(() => { void latest.select("middle"); });
    const oldWriter = latest.select;
    connection = "reconnected-2"; firstPage = [draft("new-connection")]; await settle(() => root.update(tree()));
    await settle(() => complete(new Response(JSON.stringify({ ok: true, mailboxId: "box", scope, draft: full("middle") }))));
    assert.deepEqual(latest.drafts.map(d => d.draftId), ["new-connection"]); assert.equal(latest.detail, null);
    const beforeOld = calls.length; await oldWriter("middle"); assert.equal(calls.length, beforeOld);
    mailbox = "other-box"; await settle(() => root.update(tree())); assert.equal(calls.at(-1)!.mailboxId, "other-box");
    for (const owner of [{ userId: "user-b", workspaceId: "workspace-a" }, { userId: "user-b", workspaceId: "workspace-b" }]) {
      await settle(() => { activateMailboxSnapshotSession(owner); root.update(tree()); });
      assert.equal(latest.detail, null); assert.equal(latest.selectedId, null);
    }
    await click("new-connection");
    await settle(() => {
      values.set(MAILBOX_SESSION_KEY, JSON.stringify({ owner: { userId: "other", workspaceId: "other" }, epoch: "other-tab" }));
      const event = new Event("storage"); Object.defineProperty(event, "key", { value: MAILBOX_SESSION_KEY }); events.dispatchEvent(event);
    });
    assert.equal(latest.detail, null); assert.deepEqual(latest.drafts, []);
    await settle(() => { clearMailboxSnapshotSession(); root.update(tree()); }); assert.equal(latest.detail, null);
    assert.equal(JSON.stringify(local), unchangedLocal);
    const source = readFileSync(resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"), "utf8");
    assert.match(source, /const gmailDrafts = useGmailDrafts\(mailbox.id, gmailSentConnectionKey, isGmailDraftsView\)/);
    assert.match(source, /if \(isGmailDraftsView\) return;/);
    assert.match(source, /isGmailDraftsView \? <MailToolbarIconButton[\s\S]*?onClick=\{\(\) => \{ void gmailDrafts.refresh\(\); \}/);
    assert.match(source, /Drafts: mailboxCollections.Drafts/);
    assert.match(source, /localPreviews=\{mailboxCollections.Drafts\}/);
    assert.match(source, /sanitizeMessageBodyHtml\(html, \{ allowRemoteImages: false, themeMode, attachments: \[\] \}\)/);
    // Exercise the actual Drafts render callback and existing stage document builder.
    // The sanitizer is an identity fixture here; its production implementation and tests remain unchanged.
    const callbackStart = source.indexOf("renderHtml={(html, origin) => {");
    assert(callbackStart >= 0);
    const callback = source.slice(callbackStart + "renderHtml={".length, source.indexOf("\n              }}", callbackStart)) + "}";
    const buildSource = source.slice(source.indexOf("function buildEmailStageDocument("), source.indexOf("type RgbColor ="));
    const quoteSource = source.slice(source.indexOf("function buildReliableQuoteVisibilityStyle("), source.indexOf("type DesktopThreadDisclosureState ="));
    const buildDocument = new Function(transform(quoteSource + buildSource, { transforms: ["typescript"] }).code + "; return buildEmailStageDocument;")();
    const createRender = (theme: "light" | "dark") => new Function("React", "sanitizeMessageBodyHtml", "EmailHtmlStage", "themeMode",
      transform("return (" + callback + ");", { transforms: ["typescript", "jsx"] }).code)(React,
      (html: string, options: { allowRemoteImages: boolean }) => { assert.equal(options.allowRemoteImages, false); return { html, emailStyles: "" }; },
      "stage-fixture", theme);
    const external = '<table style="background:#ffffff;color:#111111"><tbody><tr><td>Intentional external design</td></tr></tbody></table>';
    for (const theme of ["light", "dark"] as const) {
      const render = createRender(theme);
      const localStage = render(local[0].bodyHtml, "local").props;
      assert.equal(localStage.html, local[0].bodyHtml, "local content must remain byte-identical");
      assert.equal(localStage.isExternalHtml, false);
      assert.equal(localStage.isProviderHtml, false);
      const localDocument = buildDocument(localStage.html, theme, { emailStyles: localStage.emailStyles, isExternalHtml: localStage.isExternalHtml, isProviderHtmlEmail: localStage.isProviderHtml });
      assert(localDocument.includes(`:root { color-scheme: ${theme}; background: transparent; }`), "iframe canvas must match the host theme");
      assert(localDocument.includes(`class="email-stage email-stage--${theme}"`));
      assert(!localDocument.includes("data-cuevion-provider-email-html"), "local previews must not acquire the forced white provider wrapper");
      assert(localDocument.includes("background: transparent;"));
      assert(localDocument.includes(theme === "dark" ? "rgba(229, 236, 230, 0.96)" : "rgba(34, 38, 36, 0.99)"));
      assert(localDocument.includes(local[0].bodyHtml), "formatting and signature must remain in the preview");
      const providerStage = render(external, "provider").props;
      assert.equal(providerStage.html, external);
      assert.equal(providerStage.isExternalHtml, true);
      assert.equal(providerStage.isProviderHtml, true);
      assert.equal(providerStage.emailStyles, "", "provider styles must not acquire local theme overrides");
      const providerDocument = buildDocument(external, theme, { isExternalHtml: true, isProviderHtmlEmail: true });
      assert(providerDocument.includes('class="email-stage email-stage--light"'));
      assert(providerDocument.includes(external), "intentional external colours must remain intact");
    }
    assert.equal(JSON.stringify(local), unchangedLocal);
    console.log("Draft preview visual isolation: local light/dark themes, unchanged stored HTML/signature, unchanged external presentation and remote-image policy passed");
    for (const file of ["gmailDraftsState.ts", "useGmailDrafts.ts", "gmailDraftsApi.ts"]) {
      const code = readFileSync(resolve(process.cwd(), "src/lib", file), "utf8");
      assert.doesNotMatch(code, /localStorage|sessionStorage|indexedDB|setMailboxStore|classify|sendGmail|priorityRuntime/i);
    }
    console.log("Mounted Gmail Drafts: desktop/mobile reader, local-copy retention, stable selection, reconnect/account/workspace/logout/cross-tab isolation and Priority boundaries passed");
  } finally { if (root!) await settle(() => root.unmount()); clearMailboxSnapshotSession(); globalThis.fetch = previousFetch; (globalThis as any).window = previousWindow; }
}
void run().catch(error => { console.error(error); process.exitCode = 1; });
