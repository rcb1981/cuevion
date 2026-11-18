import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { transform } from "sucrase";
import { captureDraftComposition, createComposeSafety, mobileComposeBodyHtml } from "../../lib/composeSafety";

const source = readFileSync(resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"), "utf8");
function callback(name: string, end: string, scope: any) {
  const start = source.indexOf(`  const ${name} =`);
  const stop = source.indexOf(`  const ${end} =`, start);
  assert(start > 0 && stop > start);
  const code = transform(source.slice(start, stop), { transforms: ["typescript"] }).code;
  return new Function("scope", `with(scope) { ${code}; return ${name}; }`)(scope);
}
function harness(mobile = false) {
  const effects: Array<[string, unknown]> = [];
  const capture = (name: string) => (value?: unknown) => effects.push([name, value]);
  const file = new File(["private file bytes"], "file.txt");
  const scope: any = {
    composeSafety: createComposeSafety({ owner: { userId: "owner", workspaceId: "workspace" }, epoch: "epoch",
      persistent: false, signal: new AbortController().signal, isActive: () => true }, () => "connection"),
    composeSendAttemptRef: { current: "idle" }, composeBodyEditorRef: { current: { getBodyHtml: () => "<p>last keystroke!</p>" } },
    isMobileViewport: mobile, mobileReplyBodyText: "Mobile last!", composeBody: "<p>Signature and quote</p>",
    composeTo: "", composeCc: " cc@example.test ", composeBcc: "private@example.test", composeSubject: "",
    composeSignatureSelection: "none", composeMode: "forward", composeSourceMessage: { id: "source", from: "a@example.test", to: "b@example.test", subject: "original" },
    composeReplyAnchorMessage: { id: "anchor", from: "b@example.test", to: "a@example.test", subject: "original", imapUid: "7", uidValidity: "42" },
    composeAttachments: [{ id: "file", name: file.name, file }], activeComposeMailbox: { id: "mailbox", email: "me@example.test" },
    composePresentation: "workspace", mailbox: { id: "mailbox" }, inboxSignatures: {},
    extractComposePlainText: (value: string) => value.replace(/<[^>]*>/g, ""), extractComposeParagraphs: (value: string) => [value],
    captureDraftComposition, mobileComposeBodyHtml,
    setComposeLocalCopyNotice: capture("notice"), setIsCloseModalOpen: capture("closeConfirmation"),
    setIsComposeOpen: capture("composeOpen"), resetComposeState: capture("reset"),
    setComposePresentation: capture("presentation"), setModalComposeSourceSelection: capture("sourceSelection"),
    onComposeOpenChange: capture("mobileOpen"),
  };
  let store: any = { mailbox: { Drafts: [] } };
  scope.setMailboxStore = (updater: any) => { store = updater(store); };
  scope.getCurrentComposeBodyHtml = callback("getCurrentComposeBodyHtml", "captureCurrentComposition", scope);
  scope.captureCurrentComposition = callback("captureCurrentComposition", "saveDraftAndClose", scope);
  return { scope, effects, file, store: () => store, save: callback("saveDraftAndClose", "discardCompose", scope),
    discard: callback("discardCompose", "sendMessage", scope), close: callback("requestComposeClose", "resetComposeState", scope) };
}

const originalFetch = globalThis.fetch;
let calls = 0;
globalThis.fetch = (async () => { calls += 1; throw Error("All provider calls forbidden"); }) as typeof fetch;
try {
  for (const mobile of [false, true]) {
    const h = harness(mobile);
    h.save();
    const saved = h.scope.composeSafety.readLocalCopy();
    assert.equal(saved.bcc, "private@example.test");
    assert.equal(saved.cc, " cc@example.test ");
    assert.equal(saved.to, "");
    assert.equal(saved.subject, "");
    assert.equal(saved.mode, "forward");
    assert.equal(saved.replyAnchor.imapUid, "7");
    assert.equal(saved.attachments[0].file, h.file);
    assert.equal(saved.bodyHtml, mobile ? "<p>Mobile last!</p><p>Signature and quote</p>" : "<p>last keystroke!</p>");
    assert.equal(saved.saveStatus, "unsaved");
    assert.equal(h.effects.some(([name]) => name === "reset" || name === "composeOpen"), false);
    assert.equal(h.store().mailbox.Drafts[0].to, "");
    assert.equal(h.store().mailbox.Drafts[0].subject, "");
    assert.equal("bcc" in h.store().mailbox.Drafts[0], false, "Private Bcc must not enter shared mailbox projections");
    h.save();
    assert.equal(h.store().mailbox.Drafts.length, 1, "Repeated local copy replaces its own preview");
    h.close();
    assert.deepEqual(h.effects.at(-1), ["closeConfirmation", true]);
    assert.equal(h.scope.mobileReplyBodyText, "Mobile last!");

    const before = [...h.effects];
    const operation = h.scope.composeSafety.begin("send", h.scope.captureCurrentComposition());
    h.scope.composeSendAttemptRef.current = "sending";
    h.close(); h.save(); h.discard();
    assert.deepEqual(h.effects, before, "All close/save/discard gestures are fenced before React renders pending state");
    h.scope.composeSafety.finish(operation);
    h.scope.composeSendAttemptRef.current = "idle";
    h.discard();
    assert(h.effects.some(([name]) => name === "reset"));
  }
  const failed = harness();
  failed.scope.setMailboxStore = () => { throw Error("Synthetic local update failure"); };
  failed.save();
  assert(failed.effects.some(([name, value]) => name === "notice" && String(value).includes("Could not keep a local copy")));
  assert.equal(failed.effects.some(([name]) => name === "reset" || name === "composeOpen"), false);
  assert.equal(failed.scope.composeSafety.isPending(), false);
  assert.equal(failed.scope.composeAttachments[0].file, failed.file);

  const localSaveSource = source.slice(source.indexOf("  const saveDraftAndClose ="), source.indexOf("  const discardCompose ="));
  assert.doesNotMatch(localSaveSource, /normalizeMailMessage|sendGmailMessage|fetch\(|onPriority|Learning|Filtered|localStorage|sessionStorage|indexedDB/);
  const mobileSource = source.slice(source.indexOf("{/* ── Mobile compose overlay"));
  assert.match(mobileSource, /onClick=\{requestComposeClose\}/);
  assert.equal(calls, 0);
  console.log("Actual Compose callbacks: local save/failure, empty fields, Bcc, mobile retention and pending close/discard passed");
} finally { globalThis.fetch = originalFetch; }
