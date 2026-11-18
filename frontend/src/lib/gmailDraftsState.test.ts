import assert from "node:assert/strict";
import { createGmailDraftsController } from "./gmailDraftsState";
import type { GmailDraftSummary, GmailDraftDetail, GmailDraftsResult, GmailDraftsRequest } from "./gmailDraftsApi";
const scope = "a".repeat(64);
const draft = (draftId: string, messageId = `message-${draftId}`): GmailDraftSummary => ({ draftId, messageId, threadId: "same-thread",
  from: "from@example.test", to: "to@example.test", cc: "cc@example.test", bcc: "private@example.test", subject: draftId, internalDate: null, headers: [] });
const detail = (id: string, messageId?: string): GmailDraftDetail => ({ ...draft(id, messageId), previewStatus: "available", unavailableReason: null,
  bodyText: id, bodyHtml: null, inventoryComplete: true, attachments: [], mimeParts: [] });
function deferred() { let resolve!: (v: GmailDraftsResult) => void; const promise = new Promise<GmailDraftsResult>(done => { resolve = done; }); return { promise, resolve }; }
async function run() {
  let active = true; let page: GmailDraftsResult = { ok: true, scope, drafts: [draft("first"), draft("middle"), draft("last")], nextPageToken: "page2" };
  const calls: GmailDraftsRequest[] = [];
  let full = detail("middle");
  const controller = createGmailDraftsController("box", () => active, async request => {
    calls.push(request); return request.draftId ? { ok: true, scope, draft: { ...full, draftId: request.draftId } } : page;
  });
  await controller.refresh(); assert.equal(controller.getSnapshot().drafts.length, 3, "same-thread drafts must not group");
  await controller.select("middle"); assert.equal(controller.getSnapshot().selectedId, "middle");
  full = detail("middle", "replaced-message");
  await controller.refresh(); await Promise.resolve();
  assert.equal(controller.getSnapshot().selectedId, "middle"); assert.equal(controller.getSnapshot().detail?.messageId, "replaced-message");
  page = { ok: true, scope, drafts: [draft("last"), draft("older")], nextPageToken: null };
  await controller.loadMore(); assert.deepEqual(controller.getSnapshot().drafts.map(d => d.draftId), ["first", "middle", "last", "older"]);
  assert.equal(controller.getSnapshot().selectedId, "middle"); assert(calls.some(c => c.pageToken === "page2" && c.scope === scope));
  page = { ok: true, scope, drafts: [], nextPageToken: null }; await controller.refresh();
  assert.equal(controller.getSnapshot().selectedId, null); assert.equal(controller.getSnapshot().detail, null);
  page = { ok: true, scope, drafts: [draft("first")], nextPageToken: "loop" }; await controller.refresh();
  await controller.loadMore(); assert.match(controller.getSnapshot().error!, /pagination expired/);
  page = { ok: false, code: "scope_changed", message: "Changed" }; await controller.refresh();
  assert.equal(controller.getSnapshot().drafts.length, 0); assert.equal(controller.getSnapshot().detail, null);
  const pending = deferred();
  const old = createGmailDraftsController("box", () => active, () => pending.promise);
  const work = old.refresh(); old.clear(); pending.resolve({ ok: true, scope, drafts: [draft("private-old")], nextPageToken: null }); await work;
  assert.deepEqual(old.getSnapshot().drafts, []);
  const pendingDetail = deferred();
  const stale = createGmailDraftsController("box", () => active, async r => r.draftId ? pendingDetail.promise : { ok: true, scope, drafts: [draft("first")], nextPageToken: null });
  await stale.refresh(); const loading = stale.select("first"); active = false;
  assert.deepEqual(stale.getSnapshot().drafts, []);
  pendingDetail.resolve({ ok: true, scope, draft: detail("first") }); await loading;
  assert.equal(stale.getSnapshot().detail, null);
  active = true; stale.clear(); assert.deepEqual(stale.getSnapshot().drafts, []);
  const removedDetail = deferred();
  let removedPage: GmailDraftsResult = { ok: true, scope, drafts: [draft("removed")], nextPageToken: null };
  const removed = createGmailDraftsController("box", () => active, async r => r.draftId ? removedDetail.promise : removedPage);
  await removed.refresh(); const selected = removed.select("removed");
  removedPage = { ok: true, scope, drafts: [], nextPageToken: "last-page" };
  await removed.refresh(); assert.equal(removed.getSnapshot().selectedId, "removed");
  assert.equal(removed.getSnapshot().detailLoading, true);
  removedPage = { ok: true, scope, drafts: [], nextPageToken: null };
  await removed.loadMore(); assert.equal(removed.getSnapshot().selectedId, null);
  assert.equal(removed.getSnapshot().detailLoading, false, "removed selection cannot leave a permanent loading pane");
  removedDetail.resolve({ ok: true, scope, draft: detail("removed") }); await selected; await Promise.resolve();
  assert.equal(removed.getSnapshot().detail, null, "late detail cannot resurrect an externally removed draft");
  console.log("Gmail Drafts state: pagination/dedup, stable draft selection, message replacement, removed drafts and late responses passed");
}
void run().catch(error => { console.error(error); process.exitCode = 1; });
