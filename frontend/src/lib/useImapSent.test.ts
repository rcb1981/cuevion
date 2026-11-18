import assert from "node:assert/strict";
import { createElement } from "react";
import { act, create } from "react-test-renderer";
import { MailboxSnapshotContext } from "./MailboxSnapshotContext";
import { activateMailboxSnapshotSession, clearMailboxSnapshotSession, getMailboxSnapshotSession } from "./mailboxSnapshotSession";
import { useImapSent } from "./useImapSent";

async function run() {
  const original = globalThis.fetch;
  const requests: Array<{ resolve: (r: Response) => void; signal: AbortSignal; body: any }> = [];
  globalThis.fetch = (_url, init) => new Promise(resolve => requests.push({ resolve, signal: init!.signal as AbortSignal, body: JSON.parse(init!.body as string) }));
  const scope = "a".repeat(64);
  const response = () => new Response(JSON.stringify({ ok: true, mailboxId: "box", scope,
    discovery: { mode: "ready", providerFolder: "Sent", folders: [{ providerFolder: "Sent", displayName: "Sent", suggested: true }] },
    page: { serverMailboxId: "box", providerFolder: "Sent", uidValidity: "101", scope, messages: [], scanLow: 1, scanHigh: 0, nextPageToken: null } }));
  let latest!: ReturnType<typeof useImapSent>;
  function Probe({ connection, visible }: { connection: string | null; visible: boolean }) { latest = useImapSent("box", connection, visible); return null; }
  const tree = (connection: string | null, visible = true) => createElement(MailboxSnapshotContext.Provider, { value: getMailboxSnapshotSession() }, createElement(Probe, { connection, visible }));
  activateMailboxSnapshotSession({ userId: "u", workspaceId: "w" });
  let root!: ReturnType<typeof create>;
  await act(async () => { root = create(tree("a", false)); }); assert.equal(requests.length, 0);
  await act(async () => root.update(tree("a"))); assert.equal(requests.length, 1);
  await act(async () => root.update(tree(null))); assert.equal(requests[0].signal.aborted, true);
  await act(async () => root.update(tree("b")));
  await act(async () => requests[1].resolve(response())); assert.equal(latest.loaded, true);
  await act(async () => requests[0].resolve(response())); assert.equal(latest.loaded, true);
  await act(async () => root.update(tree("a"))); assert.equal(latest.loaded, false, "ABA reconnection gets fresh state");
  await act(async () => { clearMailboxSnapshotSession(); requests[2].resolve(response()); });
  assert.equal(latest.loaded, false); assert.equal(latest.scope, null); assert.equal(requests[2].signal.aborted, true);
  for (const owner of [{ userId: "u", workspaceId: "w" }, { userId: "other", workspaceId: "w" }, { userId: "other", workspaceId: "other-workspace" }]) {
    await act(async () => { activateMailboxSnapshotSession(owner); root.update(tree("a")); });
    assert.equal(latest.loaded, false);
    assert.deepEqual(requests.at(-1)!.body, { mailboxId: "box", limit: 20 }, "a new login restores through server authority, not a previous session choice");
    await act(async () => requests.at(-1)!.resolve(response())); assert.equal(latest.loaded, true);
  }
  await act(async () => { root.unmount(); clearMailboxSnapshotSession(); });
  globalThis.fetch = original;
  console.log("IMAP Sent hook: lazy loading, disconnect, ABA reconnect, logout and account/workspace isolation passed");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
