import assert from "node:assert/strict";
import {
  createCollaborationSummaryStore, indexCollaborationSummaries,
  lookupActiveCollaborationSummary, loadWorkspaceCollaborationSummaries,
  isCurrentCollaborationOpenBinding,
} from "./collaborationSummaryStore";
import type { CollaborationSummary, CollaborationSummaryResult } from "./collaborationSummaryApi";

const workspaceId = `wsp_${"W".repeat(22)}`;
const summary: CollaborationSummary = {
  collaborationId: "A".repeat(22), workspaceId, mailboxId: "mailbox-a",
  sourceRef: { provider: "google", providerMessageId: "gmail-exact" },
  state: "needs_review", updatedAt: 1_800_000_000_000, viewerAccess: "owner",
};
const input = {
  workspaceDataMode: "live", hasAuthenticatedMemberAuthority: true,
  managedMailbox: { id: "mailbox-a", provider: "google", connected: true, connectionStatus: "connected" },
  sourceMailboxId: "mailbox-a", trustedFolder: "INBOX",
  message: { id: "local-id", providerMessageId: "gmail-exact", serverMailboxId: "mailbox-a" },
};
const page = (summaries = [summary], nextCursor: string | null = null): CollaborationSummaryResult =>
  ({ status: "success", page: { v: 1, workspaceId, summaries, nextCursor } });
const defer = <T>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; };

async function run() {
  for (const state of ["needs_review", "needs_action", "note_only", "resolved", "unknown"]) {
    const index = indexCollaborationSummaries(workspaceId, [{ ...summary, state }]);
    assert.equal(Boolean(lookupActiveCollaborationSummary(index, workspaceId, input)), !["resolved", "unknown"].includes(state), state);
  }
  const index = indexCollaborationSummaries(workspaceId, [summary, summary]);
  assert.equal(index.size, 1);
  for (const value of [null, {}, { ...summary, extra: true }, { ...summary, workspaceId: `wsp_${"X".repeat(22)}` }]) {
    assert.equal(indexCollaborationSummaries(workspaceId, [value]).size, 0);
  }
  assert.equal(indexCollaborationSummaries(workspaceId, [summary, { ...summary, collaborationId: "B".repeat(22) }]).size, 0, "ambiguous sources fail closed");
  for (const change of [
    { sourceMailboxId: "mailbox-b" }, { workspaceDataMode: "demo" }, { hasAuthenticatedMemberAuthority: false },
    { managedMailbox: { ...input.managedMailbox, connected: false } },
    { message: { ...input.message, serverMailboxId: "mailbox-b" } },
    { message: { ...input.message, providerMessageId: "other" } },
    { message: { id: "gmail-exact", subject: "same subject" } },
  ]) assert.equal(lookupActiveCollaborationSummary(index, workspaceId, { ...input, ...change }), null);
  assert.equal(lookupActiveCollaborationSummary(index, `wsp_${"X".repeat(22)}`, input), null);
  const imapSummary: CollaborationSummary = { ...summary, sourceRef: { provider: "custom_imap", folder: "INBOX", uidValidity: "9001", imapUid: "42" } };
  const imapIndex = indexCollaborationSummaries(workspaceId, [imapSummary]);
  const imapInput = { ...input, managedMailbox: { ...input.managedMailbox, provider: "custom_imap" },
    message: { id: "imap-local", imapUid: "42", uidValidity: "9001", providerFolder: "INBOX" } };
  assert.equal(lookupActiveCollaborationSummary(imapIndex, workspaceId, imapInput)?.collaborationId, summary.collaborationId);
  for (const change of [{ trustedFolder: "Archive" }, { message: { ...imapInput.message, uidValidity: "9002" } },
    { message: { ...imapInput.message, imapUid: "43" } }, { message: { ...imapInput.message, uidValidity: undefined } }]) {
    assert.equal(lookupActiveCollaborationSummary(imapIndex, workspaceId, { ...imapInput, ...change }), null);
  }
  const binding = { scopeKey: "account-a", selectionKey: "mailbox-a:local-id", summary };
  assert.equal(isCurrentCollaborationOpenBinding(binding, binding), true);
  for (const current of [null, { ...binding, scopeKey: "account-b" }, { ...binding, selectionKey: "another-mail" },
    { ...binding, summary: { ...summary, state: "resolved" as const } },
    { ...binding, summary: { ...summary, collaborationId: "B".repeat(22) } }]) {
    assert.equal(isCurrentCollaborationOpenBinding(binding, current), false);
  }

  const cursors: (string | null)[] = [];
  const second = { ...summary, collaborationId: "C".repeat(22) };
  const loaded = await loadWorkspaceCollaborationSummaries(workspaceId, async (_, cursor) => {
    cursors.push(cursor);
    return cursor === null ? page([summary], summary.collaborationId) :
      cursor === summary.collaborationId ? page([], "B".repeat(22)) : page([second]);
  });
  assert.deepEqual(loaded, [summary, second]);
  assert.deepEqual(cursors, [null, summary.collaborationId, "B".repeat(22)]);
  let calls = 0;
  assert.equal(await loadWorkspaceCollaborationSummaries(workspaceId, async () => { calls++; return page([], summary.collaborationId); }), null);
  assert.equal(calls, 2, "repeated cursor stops immediately");
  calls = 0;
  assert.equal(await loadWorkspaceCollaborationSummaries(workspaceId, async () => page([], String(++calls).padStart(22, "0"))), null);
  assert.equal(calls, 20, "empty advancing pages remain bounded");
  calls = 0;
  const thousand = await loadWorkspaceCollaborationSummaries(workspaceId, async () => {
    const entries = Array.from({ length: 50 }, (_, i) => ({ ...summary, collaborationId: String(calls * 50 + i + 1).padStart(22, "0") }));
    calls++;
    return page(entries, calls === 20 ? null : entries[49].collaborationId);
  });
  assert.equal(thousand?.length, 1000);
  assert.equal(calls, 20);
  assert.equal(await loadWorkspaceCollaborationSummaries(workspaceId, async () => page(Array(51).fill(summary))), null);
  assert.equal(await loadWorkspaceCollaborationSummaries(workspaceId, async () => { throw Error("offline"); }), null);

  let outcome: CollaborationSummaryResult = page();
  calls = 0;
  const store = createCollaborationSummaryStore(workspaceId, async () => { calls++; return outcome; });
  assert.equal(store.getSnapshot().size, 0);
  await Promise.all([store.refresh(), store.refresh()]);
  assert.equal(calls, 1, "hydration is deduplicated");
  const valid = store.getSnapshot();
  outcome = { status: "network_failure" };
  await store.refresh();
  assert.equal(store.getSnapshot(), valid, "same-scope refresh failure retains valid authority");
  const emptyStore = createCollaborationSummaryStore(workspaceId, async () => outcome);
  await emptyStore.refresh();
  assert.equal(emptyStore.getSnapshot().size, 0);
  const pending = defer<CollaborationSummaryResult>();
  const oldStore = createCollaborationSummaryStore(workspaceId, () => pending.promise);
  const oldLoad = oldStore.refresh();
  oldStore.cancel();
  const newStore = createCollaborationSummaryStore(workspaceId);
  assert.equal(newStore.getSnapshot().size, 0, "new account in same workspace clears immediately");
  pending.resolve(page()); await oldLoad;
  assert.equal(oldStore.getSnapshot().size, 0, "cancelled request cannot publish");
  assert.equal(newStore.getSnapshot().size, 0);

  const beforeMutationCalls = calls;
  const dto = { collaborationId: summary.collaborationId, mailboxId: summary.mailboxId,
    state: "resolved", updatedAt: summary.updatedAt + 1, viewerAccess: "owner" } as any;
  await store.acceptMutation(dto);
  assert.equal(lookupActiveCollaborationSummary(store.getSnapshot(), workspaceId, input), null);
  await store.acceptMutation({ ...dto, state: "note_only", updatedAt: dto.updatedAt + 1 });
  assert.equal(lookupActiveCollaborationSummary(store.getSnapshot(), workspaceId, input)?.state, "note_only");
  await store.acceptMutation(dto);
  assert.equal(lookupActiveCollaborationSummary(store.getSnapshot(), workspaceId, input)?.state, "note_only", "older mutation cannot undo newer authority");
  assert.equal(calls, beforeMutationCalls, "bound lifecycle success uses projection, no redundant refresh");
  outcome = page([summary, { ...second, sourceRef: { provider: "google", providerMessageId: "new" } }]);
  await store.acceptMutation({ ...dto, collaborationId: second.collaborationId, state: "note_only" });
  assert.equal(calls, beforeMutationCalls + 1, "create without routing refreshes exactly once");
  assert.equal(store.getSnapshot().size, 2);
  const staleList = defer<CollaborationSummaryResult>();
  let first = true;
  const raceStore = createCollaborationSummaryStore(workspaceId, async () => first ? (first = false, page()) : staleList.promise);
  await raceStore.refresh(); const refresh = raceStore.refresh();
  await raceStore.acceptMutation(dto); staleList.resolve(page()); await refresh;
  assert.equal(lookupActiveCollaborationSummary(raceStore.getSnapshot(), workspaceId, input), null, "stale list cannot undo resolve");
  for (let row = 0; row < 10000; row++) lookupActiveCollaborationSummary(index, workspaceId, input);
  assert.equal(calls, beforeMutationCalls + 1, "row lookups perform no requests");
  console.log("PASS Collaboration summary store: authority, exact sources, bounded hydration, races, mutations, CTA bindings, O(1) lookups");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
