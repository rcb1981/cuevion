import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createImapSentController, mergeImapSentDisplay } from "./imapSentState";
import { fetchImapSent, parseImapSentResponse, type ImapSentResponse } from "./imapSentApi";
import { buildPriorityRuntimeSignalsForCandidates } from "./priorityRuntimeSignals";

export const scope = "a".repeat(64);
export const row = (uid: string, mailbox = "box", uv = "101", folder = "Sent Messages") => ({
  id: uid, imapUid: uid, uidValidity: uv, serverMailboxId: mailbox, providerFolder: folder,
  threadId: `imap:rfc:${mailbox}:same-root`, sender: "You", from: "self@example.test", to: "other@example.test", cc: "",
  subject: "Same subject", snippet: "Synthetic", body: ["Synthetic"], createdAt: "2026-11-18T10:00:00Z", timestamp: "2026-11-18T10:00:00Z",
  unread: true, flagged: false, attachments: [],
});
export const page = (ids: string[], nextPageToken: string | null = null, scanLow = 1, scanHigh = 100): ImapSentResponse => ({
  ok: true, mailboxId: "box", scope, discovery: { mode: "ready", providerFolder: "Sent Messages", folders: [{ providerFolder: "Sent Messages", displayName: "Sent Messages", suggested: true }] },
  page: { serverMailboxId: "box", scope, providerFolder: "Sent Messages", uidValidity: "101", messages: ids.map(id => row(id)), scanLow, scanHigh, nextPageToken },
});
function deferred<T>() { let resolve!: (v: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }

async function run() {
  const local = [{ id: "legacy", subject: "Same subject" }, { ...row("30"), id: "unproven-local" }];
  const shared = { Sent: local, Inbox: [row("inbound")], Filtered: [row("filtered")], learning: { sender: "unchanged" } };
  const untouched = JSON.stringify(shared);
  const signals = { candidateMessages: [], sentMessagesByMailboxId: { box: shared.Sent } } as any;
  const before = buildPriorityRuntimeSignalsForCandidates(signals);
  const displayed = mergeImapSentDisplay("box", [row("30"), row("30"), row("30", "other"), row("30", "box", "102"), row("30", "box", "101", "Other Sent")], local);
  assert.equal(displayed.length, 5, "provider duplicates collapse only within the full UID identity; local history is preserved");
  assert.equal(mergeImapSentDisplay("box", [{ ...row("30"), id: "imap-sent:scope-a:Sent:101:30" }, { ...row("30"), id: "imap-sent:scope-b:Sent:101:30" }], []).length, 2, "connection scopes cannot deduplicate each other");
  assert.equal(JSON.stringify(shared), untouched);
  assert.deepEqual(buildPriorityRuntimeSignalsForCandidates(signals), before);

  const requests: Array<{ token?: string; choice?: object; signal?: AbortSignal; result: ReturnType<typeof deferred<ImapSentResponse>> }> = [];
  let active = true;
  const controller = createImapSentController("box", () => active, async (_mailbox, token, choice, signal) => {
    const result = deferred<ImapSentResponse>(); requests.push({ token, choice, signal, result }); return result.promise;
  });
  let pending = controller.refresh(); requests[0].result.resolve(page(["30", "20"], "p1", 20, 30)); await pending;
  pending = controller.loadMore(); assert.deepEqual(requests[1].choice, { providerFolder: "Sent Messages", scope }, "automatic choice is pinned for pagination without another count scan"); requests[1].result.resolve(page(["20", "10"], "p2", 10, 20)); await pending;
  assert.deepEqual(controller.getSnapshot().messages.map(m => m.imapUid), ["30", "20", "10"]);
  pending = controller.refresh(); requests[2].result.resolve(page(["40", "30"], "p3", 30, 40)); await pending;
  assert.deepEqual(controller.getSnapshot().messages.map(m => m.imapUid), ["40", "30", "20", "10"], "refresh cannot infer deletion below its scan range");
  const stale = controller.loadMore(); const current = controller.refresh();
  assert.equal(requests[3].signal?.aborted, true);
  requests[4].result.resolve(page(["40"], "p4", 30, 40)); await current;
  requests[3].result.resolve(page(["5"], null)); await stale;
  assert.deepEqual(controller.getSnapshot().messages.map(m => m.imapUid), ["40", "20", "10"]);
  pending = controller.loadMore(); requests[5].result.resolve(page([], "p5", 21, 29)); await pending;
  assert.equal(controller.getSnapshot().nextPageToken, "p5", "empty scan does not end pagination");
  pending = controller.loadMore(); requests[6].result.resolve(page(["1"], "p4", 1, 20)); await pending;
  assert.match(controller.getSnapshot().error!, /expired/);
  assert.deepEqual(controller.getSnapshot().messages.map(m => m.imapUid), ["40", "20", "10"]);
  pending = controller.refresh(); requests[7].result.resolve({ ok: false, error: { code: "uid_validity_changed", message: "Changed" } }); await pending;
  assert.equal(controller.getSnapshot().messages.length, 0);
  pending = controller.refresh(); active = false; requests[8].result.resolve(page(["99"])); await pending;
  assert.equal(controller.getSnapshot().messages.length, 0);
  active = true;
  pending = controller.refresh(); requests[9].result.resolve({ ok: true, mailboxId: "box", scope, discovery: { mode: "choose", providerFolder: null, folders: [{ providerFolder: "Sent Messages", displayName: "Sent Messages", suggested: true }] }, page: null }); await pending;
  pending = controller.choose("Sent Messages");
  assert.deepEqual(requests[10].choice, { providerFolder: "Sent Messages", scope });
  controller.reset(); requests[10].result.resolve(page(["99"])); await pending;
  assert.equal(controller.getSnapshot().messages.length, 0);

  const warningController = createImapSentController("box", () => true, async () => ({ ...page(["1"]), preferenceSaved: false } as ImapSentResponse));
  await warningController.refresh();
  assert.equal(warningController.getSnapshot().messages.length, 1);
  assert.match(warningController.getSnapshot().error!, /could not be saved/);
  assert.equal(parseImapSentResponse({ ...page(["1"]), preferenceSaved: false }, "box").ok, true);
  assert.equal(parseImapSentResponse({ ...page(["1"]), preferenceSaved: "yes" }, "box").ok, false);
  assert.equal(parseImapSentResponse(page(["1"]), "box").ok, true);
  for (const mutate of [
    (x: any) => { x.mailboxId = "other"; }, (x: any) => { x.scope = "invalid"; },
    (x: any) => { x.page.messages[0].imapUid = "0"; }, (x: any) => { x.page.messages[0].uidValidity = "102"; },
    (x: any) => { x.page.messages[0].providerFolder = "INBOX"; }, (x: any) => { x.page.messages.push(x.page.messages[0]); },
    (x: any) => { x.page.messages[0].password = "secret"; }, (x: any) => { x.page.messages[0].attachments = [{ id: "a", name: "file", inlineSrc: "https://tracker.invalid" }]; },
    (x: any) => { x.page.scanLow = 2; }, (x: any) => { x.discovery.providerFolder = "Other"; },
  ]) { const value = page(["1"]); mutate(value); assert.equal(parseImapSentResponse(value, "box").ok, false); }
  assert.equal(parseImapSentResponse(page(["1"], "same"), "box", "same").ok, false);
  assert.deepEqual(parseImapSentResponse(page(["1"], "same"), "box", "same"), { ok: false, error: { code: "invalid_page_token", message: "Sent pagination expired. Refresh Sent." } });
  const originalFetch = globalThis.fetch;
  let transportCalls = 0;
  globalThis.fetch = async (url, init) => {
    transportCalls++; assert.equal(url, "/api/inboxes/fetch-imap-sent"); assert.equal(init?.cache, "no-store");
    assert.equal(init?.credentials, "include"); assert.equal(init?.method, "POST");
    assert.deepEqual(JSON.parse(init!.body as string), { mailboxId: "box", limit: 20 });
    return new Response(JSON.stringify(page(["1"])));
  };
  assert.equal((await fetchImapSent("box")).ok, true);
  assert.equal((await fetchImapSent("box", "bad token")).ok, false); assert.equal(transportCalls, 1);
  globalThis.fetch = async () => new Response(JSON.stringify({ ok: false, error: { code: "uid_validity_changed" } }), { status: 409 });
  assert.deepEqual(await fetchImapSent("box"), { ok: false, error: { code: "uid_validity_changed", message: "Sent changed. Refresh Sent or choose its folder again." } });
  for (const status of [403, 404]) {
    globalThis.fetch = async () => new Response(null, { status });
    assert.deepEqual(await fetchImapSent("box"), { ok: false, error: { code: "mailbox_connection_changed", message: "This mailbox is no longer available. Reconnect it to load Sent." } });
  }
  globalThis.fetch = originalFetch;

  const source = readFileSync(resolve(__dirname, "../components/workspace/WorkspaceShell.tsx"), "utf8");
  const mapping = source.slice(source.indexOf('const imapSentMessages'), source.indexOf('const [revealedRemoteImageMessageIds'));
  assert.ok(!/normalizeMailMessage|setMailboxStore|senderCategoryLearning|priorityRuntime/.test(mapping));
  assert.ok(source.includes('mergeImapSentDisplay(mailbox.id, imapSentMessages, mailboxCollections.Sent)'));
  assert.ok(source.includes('Sent: [sentMessage, ...currentStore[activeComposeMailbox.id].Sent]'));
  assert.ok(source.includes('(activeFolder !== "Inbox" && activeFolder !== "Filtered")'));
  console.log("IMAP Sent API, UID pagination, local coexistence, request races and Priority isolation passed");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
