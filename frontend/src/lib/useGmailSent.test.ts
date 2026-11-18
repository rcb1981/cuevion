import assert from "node:assert/strict";
import { createElement } from "react";
import { act, create } from "react-test-renderer";
import { MailboxSnapshotContext } from "./MailboxSnapshotContext";
import { useGmailSent } from "./useGmailSent";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

let latest: ReturnType<typeof useGmailSent>;
const requests: Array<{ resolve: (response: Response) => void; signal: AbortSignal }> = [];
const originalFetch = globalThis.fetch;
globalThis.fetch = (_url, init) => new Promise((resolve) => requests.push({ resolve, signal: init!.signal as AbortSignal }));
const controller = new AbortController();
let active = true;
const session = { owner: { userId: "u", workspaceId: "w" }, epoch: "e", persistent: false, signal: controller.signal, isActive: () => active };
const response = (id: string) => new Response(JSON.stringify({ ok: true, mailboxId: "box", folder: {
  serverMailboxId: "box", providerFolder: "Sent", uidValidity: "gmail-api", nextPageToken: null,
  messages: [{ id, providerMessageId: id, providerThreadId: "t", serverMailboxId: "box", providerFolder: "Sent", labelIds: ["SENT"],
    sender: "Synthetic", from: "self@example.test", to: "other@example.test", subject: "Synthetic", snippet: "Synthetic", body: ["Synthetic"], timestamp: "2026-11-17", createdAt: "2026-11-17" }],
} }));
function Probe({ connection, visible }: { connection: string | null; visible: boolean }) {
  latest = useGmailSent("box", connection, visible); return null;
}
const tree = (connection: string | null, visible = true) => createElement(MailboxSnapshotContext.Provider, { value: session }, createElement(Probe, { connection, visible }));

async function run() {
  let root!: ReturnType<typeof create>;
  await act(async () => { root = create(tree("first", false)); });
  assert.equal(requests.length, 0);
  await act(async () => { root.update(tree("first")); });
  assert.equal(requests.length, 1);
  await act(async () => { root.update(tree(null)); });
  assert.equal(requests[0].signal.aborted, true);
  await act(async () => { root.update(tree("second")); });
  await act(async () => { requests[1].resolve(response("second")); });
  await act(async () => { requests[0].resolve(response("old")); });
  assert.equal(latest!.messages[0].providerMessageId, "second");
  await act(async () => { root.update(tree("first")); }); // reconnect ABA gets a fresh controller
  assert.equal(latest!.messages.length, 0);
  await act(async () => { active = false; controller.abort(); requests[2].resolve(response("logout")); });
  assert.equal(requests[2].signal.aborted, true);
  assert.equal(latest!.messages.length, 0);
  await act(async () => { root.unmount(); });
  globalThis.fetch = originalFetch;

  // Wiring contracts accompany runtime tests: new data must stay in MailboxView.
  const source = readFileSync(resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"), "utf8");
  const view = source.slice(source.indexOf("function MailboxView("), source.indexOf("function normalizeMailboxFocusPreferenceOverridesStore("));
  const mapping = view.slice(view.indexOf("const gmailSentMessages"), view.indexOf("const [revealedRemoteImageMessageIds"));
  assert.ok(mapping.includes("message.providerMessageId"));
  assert.ok(!/normalizeMailMessage|setMailboxStore|senderCategoryLearning|priorityRuntime/.test(mapping));
  assert.ok(view.includes("? mergeGmailSentDisplay(mailbox.id, gmailSentMessages, mailboxCollections.Sent, gmailSent.observed)"));
  assert.ok(view.includes('onClick={isGmailSentView ? () => { void gmailSent.refresh(); } : isImapSentView ? () => { void imapSent.refresh(); } : onSyncMailbox}'));
  assert.ok(source.includes('return [mailboxId, collections.Sent];'));
  const readGuard = view.slice(view.indexOf("const markInboxMessageReadOnOpen"), view.indexOf("const markInboxMessageReadOnOpen") + 450);
  assert.ok(readGuard.includes('(activeFolder !== "Inbox" && activeFolder !== "Filtered")'));
  assert.ok(source.includes("Sent: [sentMessage, ...currentStore[activeComposeMailbox.id].Sent]"));
  const { resolveInitialComposeAttachments } = require("../components/workspace/WorkspaceShell.tsx");
  const syntheticSent = { id: "display", providerMessageId: "provider-id", providerFolder: "Sent",
    threadIdentityContext: { provider: "google", mailboxId: "box" }, attachments: [{ id: "attachment-1", name: "fixture.txt" }] };
  const forwarded = resolveInitialComposeAttachments("forward", syntheticSent, { mailboxId: "box", folder: "Sent" });
  assert.equal(forwarded[0].receivedSource.messageUid, "provider-id");
  assert.deepEqual(resolveInitialComposeAttachments("reply", syntheticSent, { mailboxId: "box", folder: "Sent" }), []);
  assert.deepEqual(resolveInitialComposeAttachments("reply_all", syntheticSent, { mailboxId: "box", folder: "Sent" }), []);
  console.log("Gmail Sent hook lifecycle and display isolation tests passed");
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
