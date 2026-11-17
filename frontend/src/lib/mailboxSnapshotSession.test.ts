import assert from "node:assert/strict";
import { createElement, useEffect, useState } from "react";
import { act, create } from "react-test-renderer";
import { MailboxSnapshotBoundary, useMailboxSnapshots } from "./MailboxSnapshotContext";
import { loadStartupSession, logoutAuth0Session } from "./authApi";
import { MUSIC_CLASSIFIER_VERSION, readLiveInboxSnapshots, saveLiveInboxSnapshot, type LiveInboxSnapshot } from "./liveInboxSnapshots";
import {
  activateMailboxSnapshotSession, clearMailboxSnapshotSession, getMailboxSnapshotSession,
  LEGACY_MAILBOX_SNAPSHOT_KEY, MAILBOX_SESSION_KEY, MAILBOX_SNAPSHOT_KEY, MAILBOX_FOLDER_SNAPSHOT_KEY,
  readMailboxFolderSnapshot, saveMailboxFolderSnapshot,
  type MailboxSnapshotOwner,
} from "./mailboxSnapshotSession";

const ownerA = { userId: "usr_test_account_a", workspaceId: "wsp_test_workspace_a" };
const ownerB = { userId: "usr_test_account_b", workspaceId: "wsp_test_workspace_b" };
const contexts = { inbox: { mailboxId: "inbox", provider: "custom_imap" as const, folder: "INBOX" } };
const snapshot: LiveInboxSnapshot = {
  inboxId: "inbox", provider: "custom_imap", folder: "INBOX", uidValidity: "900",
  classifierVersion: MUSIC_CLASSIFIER_VERSION, email: "fixture@example.test", fetchedAt: "2026-11-17T00:00:00Z",
  messages: [{ id: "fixture", sender: "Fixture", subject: "Synthetic fixture", snippet: "Synthetic fixture",
    body: ["Synthetic fixture"], from: "fixture@example.test", to: "fixture@example.test",
    timestamp: "fixture", createdAt: "2026-11-17T00:00:00Z", ui_signal: "NEW",
    serverMailboxId: "inbox", providerFolder: "INBOX", uidValidity: "900", imapUid: "1" }],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

const stored = new Map<string, string>();
const events = new EventTarget();
let storageUnavailable = false;
const browserStorage = {
  get length() { return stored.size; },
  key(index: number) { return [...stored.keys()][index] ?? null; },
  getItem(key: string) { if (storageUnavailable) throw new Error("unavailable"); return stored.get(key) ?? null; },
  setItem(key: string, value: string) { if (storageUnavailable) throw new Error("unavailable"); stored.set(key, value); },
  removeItem(key: string) { if (storageUnavailable) throw new Error("unavailable"); stored.delete(key); },
};
const priorWindow = (globalThis as { window?: unknown }).window;
(globalThis as { window?: unknown }).window = {
  localStorage: browserStorage,
  addEventListener: events.addEventListener.bind(events),
};

function session(owner = ownerA) {
  activateMailboxSnapshotSession(owner);
  const result = getMailboxSnapshotSession();
  assert.ok(result);
  return result;
}
function reset() { storageUnavailable = false; clearMailboxSnapshotSession(); stored.clear(); }
function authResponse(owner = ownerA) {
  return new Response(JSON.stringify({ authenticated: true, authSource: "auth0", ...owner,
    email: "fixture@example.test", name: "Fixture", userType: "member", workspaceRole: "owner" }), { status: 200 });
}

async function run() {
  reset();
  const first = session();
  saveLiveInboxSnapshot(snapshot, first);
  assert.equal(readLiveInboxSnapshots(contexts, first).inbox.messages.length, 1);
  assert.deepEqual(readLiveInboxSnapshots({}, first), {}); // A cache cannot authorize a mailbox absent from server config.
  const saved = stored.get(MAILBOX_SNAPSHOT_KEY)!;
  const savedMarker = stored.get(MAILBOX_SESSION_KEY)!;
  const encoded = JSON.parse(saved);
  assert.deepEqual(encoded.owner, ownerA);
  assert.equal(encoded.epoch, first.epoch);
  // Simulate the persisted browser state surviving a page reload.
  clearMailboxSnapshotSession();
  stored.set(MAILBOX_SESSION_KEY, savedMarker);
  stored.set(MAILBOX_SNAPSHOT_KEY, saved);
  const restored = session();
  assert.notStrictEqual(restored, first);
  assert.equal(readLiveInboxSnapshots(contexts, restored).inbox.messages.length, 1);
  saveLiveInboxSnapshot({ ...snapshot, messages: [] }, first);
  assert.equal(readLiveInboxSnapshots(contexts, restored).inbox.messages.length, 1);

  // Same inbox ID is insufficient, including when only the workspace changes.
  for (const owner of [ownerB, { ...ownerA, workspaceId: ownerB.workspaceId }]) {
    const other = session(owner);
    assert.equal(stored.has(MAILBOX_SNAPSHOT_KEY), false);
    stored.set(MAILBOX_SNAPSHOT_KEY, saved);
    assert.deepEqual(readLiveInboxSnapshots(contexts, other), {});
    stored.set(MAILBOX_SNAPSHOT_KEY, JSON.stringify({ ...encoded, epoch: other.epoch }));
    assert.deepEqual(readLiveInboxSnapshots(contexts, other), {});
  }

  reset();
  const folderKeys = ["sent", "trash", "spam", "archive"].map((folder) => `cuevion-${folder}-messages:fixture`);
  for (const key of folderKeys) stored.set(key, JSON.stringify({ inbox: snapshot.messages }));
  stored.set(LEGACY_MAILBOX_SNAPSHOT_KEY, JSON.stringify({ inbox: snapshot }));
  let legacySession = session();
  assert.equal(stored.has(LEGACY_MAILBOX_SNAPSHOT_KEY), false);
  for (const key of folderKeys) {
    assert.equal(stored.has(key), false);
    assert.equal(readMailboxFolderSnapshot(key, legacySession), null);
    saveMailboxFolderSnapshot(key, JSON.stringify({ inbox: snapshot.messages }), legacySession);
    assert.equal(JSON.parse(readMailboxFolderSnapshot(key, legacySession)!).inbox.length, 1);
  }
  const savedFolders = stored.get(MAILBOX_FOLDER_SNAPSHOT_KEY)!;
  const differentFolderOwner = session(ownerB);
  assert.equal(stored.has(MAILBOX_FOLDER_SNAPSHOT_KEY), false);
  stored.set(MAILBOX_FOLDER_SNAPSHOT_KEY, savedFolders);
  for (const key of folderKeys) assert.equal(readMailboxFolderSnapshot(key, differentFolderOwner), null);
  saveMailboxFolderSnapshot(folderKeys[0], "{}", legacySession);
  assert.equal(stored.get(MAILBOX_FOLDER_SNAPSHOT_KEY), savedFolders);
  legacySession = session();
  assert.deepEqual(readLiveInboxSnapshots(contexts, legacySession), {});
  for (const raw of [JSON.stringify({ inbox: snapshot }), "invalid-json", "[]", "null"]) {
    stored.set(MAILBOX_SNAPSHOT_KEY, raw);
    assert.deepEqual(readLiveInboxSnapshots(contexts, legacySession), {});
  }
  assert.deepEqual(readLiveInboxSnapshots(contexts, null), {});
  const beforeUnownedWrite = stored.get(MAILBOX_SNAPSHOT_KEY);
  saveLiveInboxSnapshot(snapshot, null);
  assert.equal(stored.get(MAILBOX_SNAPSHOT_KEY), beforeUnownedWrite);

  reset();
  const requestSession = session();
  const mailboxResponse = deferred<LiveInboxSnapshot>();
  const pendingWrite = mailboxResponse.promise.then((result) => saveLiveInboxSnapshot(result, requestSession));
  session(ownerB);
  const returnedToA = session();
  mailboxResponse.resolve(snapshot);
  await pendingWrite;
  assert.deepEqual(readLiveInboxSnapshots(contexts, returnedToA), {});
  assert.equal(requestSession.signal.aborted, true);

  // A cross-tab marker invalidates writes even before its storage event runs.
  saveLiveInboxSnapshot(snapshot, returnedToA);
  stored.set(MAILBOX_SESSION_KEY, JSON.stringify({ owner: ownerB, epoch: "other-tab" }));
  const beforeLateWrite = stored.get(MAILBOX_SNAPSHOT_KEY);
  saveLiveInboxSnapshot({ ...snapshot, messages: [] }, returnedToA);
  assert.equal(stored.get(MAILBOX_SNAPSHOT_KEY), beforeLateWrite);
  assert.deepEqual(readLiveInboxSnapshots(contexts, returnedToA), {});
  const changed = new Event("storage");
  Object.defineProperty(changed, "key", { value: MAILBOX_SESSION_KEY });
  events.dispatchEvent(changed);
  assert.equal(getMailboxSnapshotSession(), null);
  assert.equal(returnedToA.signal.aborted, true);

  reset();
  const logoutSession = session();
  saveLiveInboxSnapshot(snapshot, logoutSession);
  const authReply = deferred<Response>();
  const oldAuthentication = loadStartupSession((() => authReply.promise) as typeof fetch);
  const logoutReply = deferred<Response>();
  const logout = logoutAuth0Session(((url: string) => {
    assert.equal(url, "/api/auth/logout");
    assert.equal(getMailboxSnapshotSession(), null); // Cleared before the request.
    return logoutReply.promise;
  }) as typeof fetch);
  assert.equal(stored.has(MAILBOX_SNAPSHOT_KEY), false);
  saveLiveInboxSnapshot(snapshot, logoutSession);
  assert.equal(stored.has(MAILBOX_SNAPSHOT_KEY), false);
  authReply.resolve(authResponse());
  assert.equal((await oldAuthentication).status, "unavailable");
  assert.equal(getMailboxSnapshotSession(), null);
  logoutReply.resolve(new Response("{}", { status: 503 }));
  assert.equal((await logout).ok, false);
  assert.equal(getMailboxSnapshotSession(), null); // A failed logout cannot revive private data.

  reset();
  const beforeExpiry = session();
  saveLiveInboxSnapshot(snapshot, beforeExpiry);
  const expired = await loadStartupSession((async () => new Response("{}", { status: 401 })) as typeof fetch);
  assert.equal(expired.status, "unauthenticated");
  assert.equal(beforeExpiry.signal.aborted, true);
  assert.equal(stored.has(MAILBOX_SNAPSHOT_KEY), false);

  reset();
  storageUnavailable = true;
  assert.doesNotThrow(() => session());
  const withoutStorage = getMailboxSnapshotSession()!;
  assert.equal(withoutStorage.isActive(), true); // Provider-driven UI still works.
  assert.doesNotThrow(() => saveLiveInboxSnapshot(snapshot, withoutStorage));
  assert.deepEqual(readLiveInboxSnapshots(contexts, withoutStorage), {});
  assert.doesNotThrow(clearMailboxSnapshotSession);
  storageUnavailable = false;

  // Exercise the actual React boundary, not a model of component cleanup.
  reset();
  let api!: ReturnType<typeof useMailboxSnapshots>;
  let setPrivateState!: (value: string) => void;
  let unmounts = 0;
  function PrivateView() {
    api = useMailboxSnapshots();
    const [privateState, setState] = useState("empty");
    setPrivateState = setState;
    useEffect(() => () => { unmounts += 1; }, []);
    return createElement("span", null, privateState);
  }
  const view = (owner: MailboxSnapshotOwner | null) => createElement(MailboxSnapshotBoundary, {
    owner, fallback: createElement("span", null, "signed-out"), children: createElement(PrivateView),
  });
  session();
  let renderer!: ReturnType<typeof create>;
  act(() => { renderer = create(view(ownerA)); });
  act(() => { setPrivateState("populated"); });
  const oldSetState = setPrivateState;
  const oldApi = api;
  act(() => { session(ownerB); });
  assert.equal(unmounts, 1);
  assert.deepEqual(renderer.toJSON()?.children, ["signed-out"]);
  act(() => { renderer.update(view(ownerB)); });
  assert.deepEqual(renderer.toJSON()?.children, ["empty"]);
  act(() => { oldSetState("late-result"); oldApi.saveLiveInboxSnapshot(snapshot); oldApi.clear(); });
  assert.deepEqual(renderer.toJSON()?.children, ["empty"]);
  assert.deepEqual(api.readLiveInboxSnapshots(contexts), {});
  act(() => {
    setPrivateState("populated"); api.saveLiveInboxSnapshot(snapshot);
    api.saveFolderSnapshot(folderKeys[0], JSON.stringify({ inbox: snapshot.messages }));
  });
  act(() => { clearMailboxSnapshotSession(); });
  assert.equal(unmounts, 2);
  assert.deepEqual(renderer.toJSON()?.children, ["signed-out"]);
  oldApi.saveFolderSnapshot(folderKeys[0], "{}");
  assert.equal(stored.has(MAILBOX_SNAPSHOT_KEY), false);
  assert.equal(stored.has(MAILBOX_FOLDER_SNAPSHOT_KEY), false);
  act(() => { renderer.unmount(); });
  console.log("Mailbox snapshot isolation tests passed (ownership, restoration, legacy, logout, expiry, cross-tab, async and React cleanup).");
}

run().finally(() => {
  reset();
  (globalThis as { window?: unknown }).window = priorWindow;
}).catch((error) => { console.error(error); process.exitCode = 1; });
