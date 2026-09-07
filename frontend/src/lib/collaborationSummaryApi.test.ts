declare const process: { exitCode?: number };
import assert from "node:assert/strict";
import { isActiveCollaborationSummary, listCollaborationSummaries, parseCollaborationSummary, parseCollaborationSummaryPage } from "./collaborationSummaryApi";
import { __resetCollaborationOwnerApiTransportForTests } from "./collaborationOwnerApiTransport";
const workspaceId = `wsp_${"W".repeat(22)}`;
const summary = {
  collaborationId: "A".repeat(22), workspaceId, mailboxId: "google-mailbox",
  sourceRef: { provider: "google" as const, providerMessageId: "Exact-google-ID_1" },
  state: "needs_review" as const, updatedAt: 1_800_000_000_000, viewerAccess: "owner" as const,
};
const page = { v: 1, workspaceId, summaries: [summary], nextCursor: null };
async function run() {
  assert.deepEqual(parseCollaborationSummary(summary, workspaceId), summary);
  const imap = { ...summary, sourceRef: { provider: "custom_imap", folder: "INBOX", uidValidity: "18446744073709551615", imapUid: "42" } };
  assert.deepEqual(parseCollaborationSummary(imap, workspaceId), imap);
  assert.deepEqual(parseCollaborationSummaryPage(page, workspaceId), page);
  for (const state of ["needs_review", "needs_action", "note_only", "resolved"] as const)
    assert.equal(isActiveCollaborationSummary({ ...summary, state }), state !== "resolved");
  const bad = [
    { collaborationId: "short" }, { collaborationId: `${summary.collaborationId}\n` },
    { state: "active" }, { state: null }, { updatedAt: "1800000000000" }, { updatedAt: true },
    { updatedAt: 1.5 }, { updatedAt: Number.NaN }, { updatedAt: Number.MAX_SAFE_INTEGER },
    { mailboxId: "Wrong-Mailbox" }, { workspaceId: `wsp_${"X".repeat(22)}` }, { viewerAccess: "guest" },
    { messages: [] }, { sourceMessage: {} }, { token: "private" },
    { sourceRef: { provider: "google", providerMessageId: " value " } },
    { sourceRef: { provider: "google", providerMessageId: "x\u0000y" } },
    { sourceRef: { provider: "google", providerMessageId: "x".repeat(513) } },
    { sourceRef: { provider: "google", providerMessageId: "中文" } },
    { sourceRef: { provider: "google", providerMessageId: "x", unexpected: "y" } },
    ...["Trash", "inbox"].map(folder => ({ sourceRef: { provider: "custom_imap", folder, uidValidity: "1", imapUid: "2" } })),
    ...["0", "01", "-1", 1, "1".repeat(21)].map(uidValidity => ({ sourceRef: { provider: "custom_imap", folder: "INBOX", uidValidity, imapUid: "2" } })),
  ];
  for (const change of bad) assert.equal(parseCollaborationSummary({ ...summary, ...change }, workspaceId), null, JSON.stringify(change));
  assert.equal(parseCollaborationSummary(summary, "email@example.com"), null);
  for (const change of [
    { v: "1" }, { v: 2 }, { v: true }, { extra: true }, { workspaceId: `wsp_${"X".repeat(22)}` },
    { summaries: [summary, summary] }, { summaries: [summary, { ...summary, collaborationId: "0".repeat(22) }] },
    { summaries: Array(51).fill(summary) }, { nextCursor: "bad" }, { nextCursor: "0".repeat(22) },
  ]) assert.equal(parseCollaborationSummaryPage({ ...page, ...change }, workspaceId), null);
  assert.equal(parseCollaborationSummaryPage(page, workspaceId, summary.collaborationId), null);
  const filtered = { ...page, summaries: [], nextCursor: summary.collaborationId };
  assert.deepEqual(parseCollaborationSummaryPage(filtered, workspaceId), filtered);
  console.log("PASS summary: exact sources, lifecycle, schema, bounds, ordering and cursors");
  const originalFetch = globalThis.fetch;
  const calls: Array<{ body: Record<string, unknown>; init?: RequestInit }> = [];
  let responsePayload: unknown = { ok: true, data: page };
  let responseStatus = 200;
  globalThis.fetch = async (_input, init) => {
    const body = JSON.parse(String(init?.body)); calls.push({ body, init });
    return new Response(JSON.stringify(body.operation === "csrf"
      ? { ok: true, data: { csrfToken: "csrf-token", expiresAt: Math.floor(Date.now() / 1000) + 300 } }
      : responsePayload), { status: responseStatus, headers: { "Content-Type": "application/json" } });
  };
  try {
    __resetCollaborationOwnerApiTransportForTests();
    assert.equal((await listCollaborationSummaries("wrong-workspace")).status, "invalid_request");
    assert.equal((await listCollaborationSummaries(workspaceId, "bad")).status, "invalid_request");
    assert.equal(calls.length, 0);
    assert.deepEqual(await listCollaborationSummaries(workspaceId), { status: "success", page });
    assert.deepEqual(calls.map(call => call.body.operation), ["csrf", "list_summaries"]);
    assert.deepEqual(calls[1].body, { operation: "list_summaries", cursor: null });
    assert.equal(calls[1].init?.credentials, "same-origin");
    responsePayload = { ok: true, data: { ...page, summaries: [], nextCursor: null } };
    assert.equal((await listCollaborationSummaries(workspaceId, summary.collaborationId)).status, "success");
    assert.equal(calls.length, 3);
    for (const payload of [page, { ok: true, data: { ...page, unexpected: true } },
      { ok: true, data: { ...page, workspaceId: `wsp_${"X".repeat(22)}` } }]) {
      responsePayload = payload;
      assert.equal((await listCollaborationSummaries(workspaceId)).status, "invalid_response");
    }
    responseStatus = 503;
    assert.equal((await listCollaborationSummaries(workspaceId)).status, "service_unavailable");
    console.log("PASS summary: authenticated bounded fetch, strict envelope, cursor and failures");
  } finally {
    globalThis.fetch = originalFetch;
    __resetCollaborationOwnerApiTransportForTests();
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
