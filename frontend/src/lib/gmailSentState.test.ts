import assert from "node:assert/strict";
import { buildPriorityRuntimeSignalsForCandidates } from "./priorityRuntimeSignals";
import { createGmailSentController, mergeGmailSentDisplay } from "./gmailSentState";
import { fetchGmailSent, type GmailSentResponse, type LiveInboxMessageSnapshot } from "./inboxConnectionApi";
import { activateMailboxSnapshotSession, clearMailboxSnapshotSession, getMailboxSnapshotSession, MAILBOX_SESSION_KEY } from "./mailboxSnapshotSession";

const row = (id: string, mailbox = "box"): LiveInboxMessageSnapshot => ({
  id, providerMessageId: id, providerThreadId: "same-thread", serverMailboxId: mailbox, providerFolder: "Sent",
  labelIds: ["SENT"], sender: "Synthetic", from: "self@example.test", to: "other@example.test", subject: "Same subject",
  snippet: "Synthetic", body: ["Synthetic"], timestamp: "2026-11-17T00:00:00Z", createdAt: "2026-11-17T00:00:00Z",
});
const page = (ids: string[], nextPageToken: string | null = null): GmailSentResponse => ({ ok: true, page: { messages: ids.map((id) => row(id)), nextPageToken } });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((r) => { resolve = r; }); return { promise, resolve }; }

async function run() {
  const local = [{ ...row("a"), id: "local-a" }, { ...row("legacy"), providerMessageId: undefined }, row("other", "other-box")];
  const shared = { Sent: local, Inbox: [row("inbound")], Filtered: [row("filtered")], learning: { sender: "unchanged" } };
  const priorityInput = { candidateMessages: [{ ...row("inbound"), mailboxId: "box" }], sentMessagesByMailboxId: { box: shared.Sent } };
  const priorityBefore = buildPriorityRuntimeSignalsForCandidates(priorityInput);
  const sharedBefore = JSON.stringify(shared);
  const before = JSON.stringify(local);
  const result = mergeGmailSentDisplay("box", [row("a"), row("b"), row("a")], local, new Set());
  assert.deepEqual(result.map((r) => r.id), ["a", "b", "legacy"]);
  assert.equal(JSON.stringify(local), before);
  assert.equal(JSON.stringify(shared), sharedBefore);
  assert.deepEqual(buildPriorityRuntimeSignalsForCandidates(priorityInput), priorityBefore);
  // A self-addressed message appears in Inbox and Sent, but once in the Sent conversation.
  assert.deepEqual(mergeGmailSentDisplay("box", [row("self")], [{ ...row("self"), id: "inbox-self" }], new Set()).map((m) => m.id), ["self"]);
  assert.equal(mergeGmailSentDisplay("box", [], local, new Set(["a"])).length, 1);
  assert.equal(mergeGmailSentDisplay("box", [], [{ ...row("other"), threadIdentityContext: { mailboxId: "other-box" } }], new Set()).length, 0);

  const requests: Array<{ token?: string; signal?: AbortSignal; response: ReturnType<typeof deferred<GmailSentResponse>> }> = [];
  let active = true;
  const controller = createGmailSentController("box", () => active, async (_mailbox, token, signal) => {
    const response = deferred<GmailSentResponse>(); requests.push({ token, signal, response }); return response.promise;
  });
  const first = controller.refresh(); requests[0].response.resolve(page(["external", "a"], "page2")); await first;
  const more = controller.loadMore(); requests[1].response.resolve(page(["a", "b"], "page3")); await more;
  assert.deepEqual(controller.getSnapshot().messages.map((m) => m.id), ["external", "a", "b"]);
  const staleMore = controller.loadMore();
  const refresh = controller.refresh();
  assert.equal(requests[2].signal?.aborted, true);
  requests[3].response.resolve(page(["new"], "fresh")); await refresh;
  requests[2].response.resolve(page(["stale"])); await staleMore;
  assert.deepEqual(controller.getSnapshot().messages.map((m) => m.id), ["new"]);
  assert.ok(controller.getSnapshot().observed.has("a")); // no local resurrection
  const failure = controller.loadMore(); requests[4].response.resolve({ ok: false, error: { code: "error", message: "Synthetic error" } }); await failure;
  assert.equal(controller.getSnapshot().error, "Synthetic error");
  assert.deepEqual(controller.getSnapshot().messages.map((m) => m.id), ["new"]);
  const cycle = controller.loadMore(); requests[5].response.resolve(page(["x"], "fresh")); await cycle;
  assert.match(controller.getSnapshot().error!, /pagination/);
  const disconnected = controller.refresh(); active = false; requests[6].response.resolve(page(["wrong-account"])); await disconnected;
  assert.deepEqual(controller.getSnapshot().messages.map((m) => m.id), ["new"]);
  active = true;
  const removed = controller.refresh(); controller.cancel(); requests[7].response.resolve(page(["removed"])); await removed;
  assert.deepEqual(controller.getSnapshot().messages.map((m) => m.id), ["new"]);

  // Real cache session authority, including workspace-only and cross-tab changes.
  const values = new Map<string, string>();
  (globalThis as any).window = { localStorage: { getItem: (k: string) => values.get(k) ?? null,
    setItem: (k: string, v: string) => values.set(k, v), removeItem: (k: string) => values.delete(k),
    get length() { return values.size; }, key: (i: number) => [...values.keys()][i] }, addEventListener() {}, dispatchEvent() {} };
  for (const change of ["logout", "account", "workspace", "cross-tab"]) {
    activateMailboxSnapshotSession({ userId: "a", workspaceId: "w" });
    const session = getMailboxSnapshotSession()!;
    const pending = deferred<GmailSentResponse>();
    const owned = createGmailSentController("box", session.isActive, () => pending.promise);
    const read = owned.refresh();
    if (change === "logout") clearMailboxSnapshotSession();
    if (change === "account") activateMailboxSnapshotSession({ userId: "b", workspaceId: "w" });
    if (change === "workspace") activateMailboxSnapshotSession({ userId: "a", workspaceId: "other" });
    if (change === "cross-tab") values.set(MAILBOX_SESSION_KEY, "revoked");
    pending.resolve(page(["private"])); await read;
    assert.equal(owned.getSnapshot().messages.length, 0, change);
  }

  const originalFetch = globalThis.fetch;
  let body: any;
  let payload: any = { ok: true, mailboxId: "box", folder: { providerFolder: "Sent", serverMailboxId: "box", uidValidity: "gmail-api", messages: [row("external")], nextPageToken: "next" } };
  globalThis.fetch = async (url, init) => {
    assert.equal(url, "/api/inboxes/fetch-gmail-sent"); assert.equal(init?.method, "POST"); assert.equal(init?.cache, "no-store");
    body = JSON.parse(init?.body as string); return new Response(JSON.stringify(payload));
  };
  assert.equal((await fetchGmailSent("box")).ok, true);
  assert.deepEqual(body, { mailboxId: "box", limit: 50 });
  payload.folder.messages[0].labelIds = ["SENT", "INBOX", "UNREAD"];
  assert.equal((await fetchGmailSent("box")).ok, true);
  for (const labels of [["INBOX"], ["SENT", "TRASH"], ["SENT", "SENT"]]) {
    payload.folder.messages[0].labelIds = labels;
    assert.equal((await fetchGmailSent("box")).ok, false);
  }
  payload.folder.messages = [row("a", "other")]; assert.equal((await fetchGmailSent("box")).ok, false);
  payload.folder.messages = [row("a"), row("a")]; assert.equal((await fetchGmailSent("box")).ok, false);
  payload.folder.messages = []; payload.folder.nextPageToken = "bad\n"; assert.equal((await fetchGmailSent("box")).ok, false);
  assert.equal((await fetchGmailSent("box", "bad\n")).ok, false);
  globalThis.fetch = originalFetch;
  clearMailboxSnapshotSession();
  console.log("Gmail Sent identity, pagination, isolation, and API tests passed");
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
