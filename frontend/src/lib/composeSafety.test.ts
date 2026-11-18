import assert from "node:assert/strict";
import { buildGmailReplyContext, buildImapReplyContext } from "./inboxConnectionApi";
import {
  captureDraftComposition, createComposeSafety, mobileComposeBodyHtml,
  type DraftCompositionContent,
} from "./composeSafety";
import {
  activateMailboxSnapshotSession, clearMailboxSnapshotSession, getMailboxSnapshotSession, MAILBOX_SESSION_KEY,
} from "./mailboxSnapshotSession";

const stored = new Map<string, string>();
const events = new EventTarget();
let storageFails = false;
const originalWindow = globalThis.window;
const originalFetch = globalThis.fetch;
let providerCalls = 0;
globalThis.fetch = (async () => { providerCalls += 1; throw Error("Provider calls forbidden"); }) as typeof fetch;
(globalThis as any).window = {
  localStorage: {
    get length() { return stored.size; }, key: (i: number) => [...stored.keys()][i] ?? null,
    getItem: (key: string) => { if (storageFails) throw Error("storage unavailable"); return stored.get(key) ?? null; },
    setItem: (key: string, value: string) => { if (storageFails) throw Error("quota"); stored.set(key, value); },
    removeItem: (key: string) => { if (storageFails) throw Error("denied"); stored.delete(key); },
  }, addEventListener: events.addEventListener.bind(events),
};
const owner = { userId: "synthetic-owner", workspaceId: "synthetic-workspace" };
const file = new File(["synthetic bytes"], "test.txt", { type: "text/plain" });
const source = { id: "source", from: "source@example.test", to: "me@example.test", cc: "cc@example.test",
  subject: "Thread", providerFolder: "INBOX", imapUid: "7", uidValidity: "42", rfcMessageId: "<fixture@example.test>" };
const content: DraftCompositionContent = {
  to: " to@example.test ", cc: "cc@example.test", bcc: "private@example.test", subject: "",
  bodyHtml: '<p>Last keystroke!</p><div data-compose-signature="true">Signature</div><blockquote>Quote</blockquote>',
  bodyText: "Last keystroke!\nSignature\nQuote", baseBodyHtml: "<p>Signature</p>", mobileBodyText: "Mobile <text> & last keystroke!",
  signatureSelection: "mailbox", signature: { html: "Signature", useByDefault: true, logoImageUrl: null, layout: "text-only", showDivider: false },
  mode: "reply_all", source, replyAnchor: source,
  attachments: [
    { id: "file", name: file.name, file, size: file.size, mimeType: file.type },
    { id: "received", name: "received.pdf", receivedSource: { mailboxId: "mailbox", messageId: "source", messageUid: "7", providerFolder: "INBOX", uidValidity: "42" } },
    { id: "missing", name: "missing.bin" },
  ],
};

async function run() {
  activateMailboxSnapshotSession(owner);
  const session = getMailboxSnapshotSession()!;
  let connection = "connection-1";
  const safety = createComposeSafety(session, () => connection);
  const snapshot = captureDraftComposition(safety.identity(), safety.scope("mailbox")!, content);
  assert.equal(snapshot.to, content.to);
  assert.equal(snapshot.cc, content.cc);
  assert.equal(snapshot.bcc, content.bcc);
  assert.equal(snapshot.subject, "");
  assert.equal(snapshot.bodyHtml, content.bodyHtml);
  assert.equal(snapshot.bodyText, content.bodyText);
  assert.deepEqual(snapshot.signature, content.signature);
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.source)), source);
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.replyAnchor)), source);
  assert.equal(snapshot.attachments[0].file, file);
  assert.equal(await snapshot.attachments[0].file!.text(), "synthetic bytes");
  assert.deepEqual(snapshot.attachments.map(a => a.availability), ["in_memory", "reference_unverified", "unavailable"]);
  assert.deepEqual(snapshot.attachments[1].receivedSource, content.attachments[1].receivedSource);
  assert(Object.isFrozen(snapshot) && Object.isFrozen(snapshot.attachments[1].receivedSource));
  assert.notEqual(snapshot.source, source);
  const gmailSource = { ...source, serverMailboxId: "mailbox", providerMessageId: "gmail-message", providerThreadId: "gmail-thread",
    threadIdentityAuthority: "gmail" as const,
    threadIdentityContext: { provider: "google" as const, mailboxId: "mailbox", folder: "INBOX", uidValidity: "gmail-api" } };
  const gmailSnapshot = captureDraftComposition(safety.identity(), safety.scope("mailbox")!, { ...content, source: gmailSource, replyAnchor: gmailSource });
  assert.deepEqual(buildGmailReplyContext({ sendProvider: "google", composeMode: "reply_all", mailboxId: "mailbox", sourceMessage: gmailSnapshot.source }), { sourceProviderMessageId: "gmail-message" });
  const imapSource = { ...source, serverMailboxId: "mailbox", threadIdentityAuthority: "rfc" as const,
    threadIdentityContext: { provider: "custom_imap" as const, mailboxId: "mailbox", folder: "INBOX", uidValidity: "42" } };
  const imapSnapshot = captureDraftComposition(safety.identity(), safety.scope("mailbox")!, { ...content, replyAnchor: imapSource });
  assert.deepEqual(buildImapReplyContext({ sendProvider: "custom_imap", composeMode: "reply", mailboxId: "mailbox", sourceMessage: imapSnapshot.replyAnchor }), { sourceProviderFolder: "INBOX", sourceImapUid: "7", sourceUidValidity: "42" });
  assert.equal(buildGmailReplyContext({ sendProvider: "google", composeMode: "forward", mailboxId: "mailbox", sourceMessage: gmailSnapshot.source }), undefined);
  assert.equal(snapshot.saveStatus, "unsaved");
  assert.equal(snapshot.persistence, "memory_only");

  for (const mode of ["new", "reply", "reply_all", "forward"] as const) {
    const copy = captureDraftComposition(safety.identity(), safety.scope("mailbox")!, { ...content, mode, to: "", cc: "", bcc: "", subject: "", bodyHtml: "", bodyText: "" });
    assert.deepEqual([copy.to, copy.cc, copy.bcc, copy.subject, copy.bodyHtml, copy.bodyText], ["", "", "", "", "", ""]);
    assert.equal(copy.mode, mode);
    assert.deepEqual(JSON.parse(JSON.stringify(copy.replyAnchor)), source);
  }
  assert.equal(mobileComposeBodyHtml("<&>\nlast!", "<p>Signature</p>"), "<p>&lt;&amp;&gt;</p><p>last!</p><p>Signature</p>");

  safety.edit();
  assert.equal(safety.begin("local_copy", snapshot), null, "Previous revision cannot be saved");
  const latest = captureDraftComposition(safety.identity(), safety.scope("mailbox")!, content);
  const op = safety.begin("local_copy", latest)!;
  assert(op);
  assert.equal(safety.edit(), false);
  assert.equal(safety.startComposition(), false);
  assert.equal(safety.begin("send", latest), null);
  assert.equal(safety.keepLocalCopy(op, latest), true);
  assert.equal(safety.readLocalCopy()!.bcc, content.bcc);
  assert.equal(safety.readLocalCopy()!.saveStatus, "unsaved", "Local memory must never claim provider persistence");
  assert.equal(safety.finish(op), true);
  assert.equal(safety.finish(op), false);
  assert.equal(safety.readLocalCopy()!.attachments[0].file, file);

  // A second tab has its own private editor, even for the same authenticated owner.
  const otherTab = createComposeSafety(session, () => connection);
  assert.equal(otherTab.readLocalCopy(), null);
  assert.equal(otherTab.begin("send", latest), null);
  assert.equal(otherTab.isCurrent(op), false);
  assert.equal(stored.size, 1, "Only existing account marker may be written, never draft content");
  assert.equal([...stored.values()].some(v => /private@example|Signature|synthetic bytes/.test(v)), false);

  const sending = safety.begin("send", latest)!;
  connection = "connection-2";
  assert.equal(safety.isCurrent(sending), false);
  assert.equal(safety.readLocalCopy(), null);
  assert.equal(safety.scope("mailbox")!.connectionIdentity, "connection-2");
  safety.invalidate();
  assert.equal(safety.readLocalCopy(), null);
  safety.activate();
  safety.startComposition();
  assert.notEqual(safety.identity().compositionId, latest.compositionId);
  assert.equal(safety.finish(sending), false);

  for (const nextOwner of [{ ...owner, workspaceId: "other-workspace" }, { userId: "other-owner", workspaceId: "other-workspace" }]) {
    const beforeSwitch = createComposeSafety(getMailboxSnapshotSession(), () => "connection");
    const before = captureDraftComposition(beforeSwitch.identity(), beforeSwitch.scope("mailbox")!, content);
    const pending = beforeSwitch.begin("local_copy", before)!;
    beforeSwitch.keepLocalCopy(pending, before);
    activateMailboxSnapshotSession(nextOwner);
    assert.equal(beforeSwitch.readLocalCopy(), null);
    assert.equal(beforeSwitch.isCurrent(pending), false);
    assert.equal(beforeSwitch.scope("mailbox"), null);
  }
  activateMailboxSnapshotSession(owner);
  const tab = createComposeSafety(getMailboxSnapshotSession(), () => "connection");
  const tabSnapshot = captureDraftComposition(tab.identity(), tab.scope("mailbox")!, content);
  const tabOperation = tab.begin("send", tabSnapshot)!;
  stored.set(MAILBOX_SESSION_KEY, JSON.stringify({ owner: { userId: "other", workspaceId: "other" }, epoch: "other-tab" }));
  assert.equal(tab.isCurrent(tabOperation), false, "Cross-tab account switch is rejected before its storage event");
  const changed = new Event("storage");
  Object.defineProperty(changed, "key", { value: MAILBOX_SESSION_KEY });
  events.dispatchEvent(changed);
  assert.equal(tab.scope("mailbox"), null);

  activateMailboxSnapshotSession(owner);
  const logout = createComposeSafety(getMailboxSnapshotSession(), () => "connection");
  const beforeLogout = captureDraftComposition(logout.identity(), logout.scope("mailbox")!, content);
  const logoutOp = logout.begin("local_copy", beforeLogout)!;
  logout.keepLocalCopy(logoutOp, beforeLogout);
  clearMailboxSnapshotSession();
  assert.equal(logout.readLocalCopy(), null);
  assert.equal(logout.isCurrent(logoutOp), false);

  storageFails = true;
  activateMailboxSnapshotSession(owner);
  const memoryOnly = createComposeSafety(getMailboxSnapshotSession(), () => "connection");
  const memorySnapshot = captureDraftComposition(memoryOnly.identity(), memoryOnly.scope("mailbox")!, content);
  const localOp = memoryOnly.begin("local_copy", memorySnapshot)!;
  assert.equal(memoryOnly.keepLocalCopy(localOp, memorySnapshot), true);
  assert.equal(memoryOnly.readLocalCopy()!.persistence, "memory_only");
  clearMailboxSnapshotSession();
  assert.equal(memoryOnly.readLocalCopy(), null);
  assert.equal(providerCalls, 0);
  console.log("Compose snapshots, revisions, attachment bytes, account/logout/two-tab isolation and unavailable storage passed");
}
void run().finally(() => {
  storageFails = false; clearMailboxSnapshotSession();
  (globalThis as any).window = originalWindow; globalThis.fetch = originalFetch;
}).catch(error => { console.error(error); process.exitCode = 1; });
