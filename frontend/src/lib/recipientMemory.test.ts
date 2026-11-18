import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import React from "react";
import { act, create } from "react-test-renderer";
import { transform } from "sucrase";
import { loadStartupSession, logoutAuth0Session } from "./authApi";
import { MailboxSnapshotContext } from "./MailboxSnapshotContext";
import {
  activateMailboxSnapshotSession, clearMailboxSnapshotSession, getMailboxSnapshotSession,
  MAILBOX_SESSION_KEY, type MailboxSnapshotOwner,
} from "./mailboxSnapshotSession";
import {
  createRecipientMemory, getRecipientMemory, recipientMemoryKey, LEGACY_RECIPIENT_MEMORY_KEY,
  extractRecipientEmails, normalizeRememberedRecipient,
} from "./recipientMemory";
import { useRecipientMemory } from "./useRecipientMemory";

const ownerA = { userId: "usr_synthetic_a", workspaceId: "wsp_synthetic_a" };
const ownerB = { userId: "usr_synthetic_b", workspaceId: "wsp_synthetic_a" };
const stored = new Map<string, string>();
const events = new EventTarget();
const reads: string[] = [];
let denied = false;
let rejectRemove = false;
let rejectWrite = false;
let afterWrite: (() => void) | null = null;
const browserStorage = {
  getItem(key: string) { reads.push(key); if (denied) throw Error("unavailable"); return stored.get(key) ?? null; },
  setItem(key: string, value: string) {
    if (denied || rejectWrite) throw Error("quota");
    stored.set(key, value);
    if (key !== MAILBOX_SESSION_KEY) { const callback = afterWrite; afterWrite = null; callback?.(); }
  },
  removeItem(key: string) { if (denied || rejectRemove) throw Error("removal denied"); stored.delete(key); },
};
const priorWindow = (globalThis as any).window;
const priorFetch = globalThis.fetch;
(globalThis as any).window = {
  get localStorage() { if (denied) throw Error("storage getter denied"); return browserStorage; },
  addEventListener: events.addEventListener.bind(events),
  removeEventListener: events.removeEventListener.bind(events),
};
globalThis.fetch = (async () => { throw Error("Real network access forbidden"); }) as typeof fetch;

function reset() {
  denied = rejectRemove = rejectWrite = false; afterWrite = null;
  clearMailboxSnapshotSession(); stored.clear(); reads.length = 0;
}
function session(owner: MailboxSnapshotOwner = ownerA) {
  activateMailboxSnapshotSession(owner);
  const current = getMailboxSnapshotSession();
  assert.ok(current);
  return current;
}
function memory(owner: MailboxSnapshotOwner = ownerA) {
  const current = session(owner);
  const store = getRecipientMemory(current);
  store.start();
  return { current, store, key: recipientMemoryKey(current) };
}
function storageEvent(key: string | null) {
  const event = new Event("storage"); Object.defineProperty(event, "key", { value: key }); events.dispatchEvent(event);
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
function authResponse(owner = ownerA) {
  // Deliberately the same email for every identity: email is not authority.
  return new Response(JSON.stringify({ authenticated: true, authSource: "auth0", ...owner,
    email: "same@example.test", name: "Synthetic", userType: "member", workspaceRole: "owner" }));
}

const cases: Array<[string, () => void | Promise<void>]> = [
  ["No history before server authentication; legacy data is never read", async () => {
    stored.set(LEGACY_RECIPIENT_MEMORY_KEY, JSON.stringify(["private@example.test"]));
    stored.set(MAILBOX_SESSION_KEY, JSON.stringify({ owner: ownerA, epoch: "untrusted-browser-marker" }));
    const unauthenticated = createRecipientMemory(null);
    unauthenticated.start(); unauthenticated.remember(["new@example.test"]);
    assert.deepEqual(unauthenticated.getRecipients(), []);
    assert.equal(getMailboxSnapshotSession(), null);
    assert.equal(stored.has(LEGACY_RECIPIENT_MEMORY_KEY), false);
    assert.equal(reads.includes(LEGACY_RECIPIENT_MEMORY_KEY), false);
    const response = deferred<Response>();
    const pending = loadStartupSession((() => response.promise) as typeof fetch);
    assert.equal(getMailboxSnapshotSession(), null);
    response.resolve(authResponse()); await pending;
    const h = memory(); h.store.remember(["authorized@example.test"]);
    assert.deepEqual(h.store.getRecipients(), ["authorized@example.test"]);
  }],
  ["Same-account reload restores To, Cc and Bcc in the authorized epoch", async () => {
    const h = memory();
    h.store.remember([" To@Example.test ; second@example.test ", "cc@example.test", "bcc@example.test"]);
    const saved = stored.get(h.key)!; const marker = stored.get(MAILBOX_SESSION_KEY)!;
    const payload = JSON.parse(saved);
    assert.deepEqual(payload.owner, ownerA); assert.equal(payload.epoch, h.current.epoch);
    assert.deepEqual(payload.recipients, ["to@example.test", "second@example.test", "cc@example.test", "bcc@example.test"]);
    // Restore browser bytes into a fresh session, as on a page reload (not a login after logout).
    clearMailboxSnapshotSession(); stored.set(h.key, saved); stored.set(MAILBOX_SESSION_KEY, marker);
    await loadStartupSession((async () => authResponse()) as typeof fetch);
    // Reload resets the recipient module's memory, including revocation tombstones.
    const modulePath = require.resolve("./recipientMemory");
    const loadedModule = require.cache[modulePath];
    delete require.cache[modulePath];
    let freshModule: typeof import("./recipientMemory");
    try { freshModule = require(modulePath); } finally { require.cache[modulePath] = loadedModule; }
    const reloadedSession = getMailboxSnapshotSession()!;
    const restored = freshModule!.getRecipientMemory(reloadedSession); restored.start();
    assert.notStrictEqual(reloadedSession, h.current);
    assert.deepEqual(restored.getRecipients(), payload.recipients);
    h.store.remember(["late@example.test"]);
    assert.equal(stored.get(h.key), saved);
  }],
  ["Different user or workspace rejects copied records, including Bcc", () => {
    const h = memory(); h.store.remember(["bcc-secret@example.test"]);
    const payload = JSON.parse(stored.get(h.key)!);
    for (const owner of [ownerB, { ...ownerA, workspaceId: "wsp_synthetic_b" }]) {
      const current = session(owner); const key = recipientMemoryKey(current);
      assert.notEqual(key, h.key);
      stored.set(key, JSON.stringify({ ...payload, epoch: current.epoch }));
      const store = getRecipientMemory(current); store.start();
      assert.deepEqual(store.getRecipients(), []);
      store.remember(["own@example.test"]);
      assert.deepEqual(store.getRecipients(), ["own@example.test"]);
      assert.deepEqual(JSON.parse(stored.get(key)!).owner, owner);
    }
  }],
  ["Malformed, unowned and stale-epoch records fail closed", () => {
    const current = session(); const key = recipientMemoryKey(current);
    for (const payload of [null, [], {}, { version: 1, owner: ownerA, epoch: "old", recipients: ["private@example.test"] },
      { version: 1, owner: ownerA, epoch: current.epoch, recipients: [7] },
      { version: 1, owner: ownerA, epoch: current.epoch, recipients: ["<script>not an address</script>"] },
      { version: 1, owner: ownerA, epoch: current.epoch, recipients: Array(51).fill("a@example.test") }]) {
      stored.set(key, JSON.stringify(payload));
      const store = createRecipientMemory(current); store.start(); assert.deepEqual(store.getRecipients(), []);
    }
    stored.set(key, "invalid JSON");
    const store = createRecipientMemory(current); store.start(); assert.deepEqual(store.getRecipients(), []);
  }],
  ["Logout invalidates immediately before its network response; login starts empty", async () => {
    const h = memory(); h.store.remember(["private@example.test"]);
    const response = deferred<Response>();
    const pending = logoutAuth0Session((() => response.promise) as typeof fetch);
    assert.deepEqual(h.store.getRecipients(), []); assert.equal(stored.has(h.key), false);
    h.store.remember(["late@example.test"]); assert.equal(stored.has(h.key), false);
    response.resolve(new Response(JSON.stringify({ logoutUrl: null }))); await pending;
    await loadStartupSession((async () => authResponse()) as typeof fetch);
    const returned = memory();
    assert.notEqual(returned.current.epoch, h.current.epoch);
    assert.deepEqual(returned.store.getRecipients(), []);
  }],
  ["Server session revocation and late send completion cannot restore history", async () => {
    const h = memory(); h.store.remember(["private@example.test"]);
    const sent = deferred<string[]>(); const write = sent.promise.then(h.store.remember);
    await loadStartupSession((async () => new Response(JSON.stringify({ authenticated: false }), { status: 401 })) as typeof fetch);
    assert.deepEqual(h.store.getRecipients(), []);
    const other = memory(ownerB); other.store.remember(["other@example.test"]);
    const before = stored.get(other.key);
    sent.resolve(["late-bcc@example.test"]); await write;
    assert.equal(stored.get(other.key), before); assert.equal(stored.has(h.key), false);
  }],
  ["Cross-tab switch fences reads and writes even before the storage event", () => {
    const h = memory(); h.store.remember(["private@example.test"]);
    const otherMarker = JSON.stringify({ owner: ownerB, epoch: "other-tab-epoch" });
    stored.set(MAILBOX_SESSION_KEY, otherMarker);
    const otherKey = recipientMemoryKey({ ...h.current, owner: ownerB, epoch: "other-tab-epoch" });
    stored.set(otherKey, "new-account-copy");
    assert.deepEqual(h.store.getRecipients(), []);
    h.store.remember(["late@example.test"]);
    storageEvent(MAILBOX_SESSION_KEY);
    assert.equal(h.current.signal.aborted, true);
    assert.equal(stored.has(h.key), false); assert.equal(stored.get(otherKey), "new-account-copy");
    assert.equal(stored.get(MAILBOX_SESSION_KEY), otherMarker);
  }],
  ["Same-session cross-tab updates restore suggestions; storage.clear revokes", () => {
    const h = memory(); h.store.remember(["to@example.test"]);
    const payload = JSON.parse(stored.get(h.key)!);
    stored.set(h.key, JSON.stringify({ ...payload, recipients: ["bcc@example.test"] })); storageEvent(h.key);
    assert.deepEqual(h.store.getRecipients(), ["bcc@example.test"]);
    stored.set(h.key, JSON.stringify({ ...payload, recipients: ["cc@example.test"] }));
    h.store.remember(["next@example.test"]);
    assert.deepEqual(h.store.getRecipients(), ["next@example.test", "cc@example.test", "bcc@example.test"]);
    stored.clear(); assert.deepEqual(h.store.getRecipients(), []); storageEvent(null);
    assert.equal(h.current.signal.aborted, true);
  }],
  ["Cross-tab history removal prevents late recreation even with an unchanged marker", () => {
    const h = memory(); h.store.remember(["private@example.test"]);
    stored.delete(h.key); storageEvent(h.key);
    assert.equal(h.current.isActive(), true, "simulate a failed cross-tab logout marker write");
    h.store.remember(["late@example.test"]);
    assert.deepEqual(h.store.getRecipients(), []); assert.equal(stored.has(h.key), false);
  }],
  ["Failed logout cleanup cannot revive the same epoch in this page", () => {
    const h = memory(); h.store.remember(["private@example.test"]);
    rejectWrite = rejectRemove = true; clearMailboxSnapshotSession();
    assert.equal(stored.has(h.key), true, "physical cleanup failed");
    assert.deepEqual(h.store.getRecipients(), []);
    rejectWrite = rejectRemove = false;
    const returned = memory();
    assert.equal(returned.current.epoch, h.current.epoch, "shared mailbox marker could not change");
    assert.deepEqual(returned.store.getRecipients(), []);
    returned.store.remember(["new@example.test"]);
    assert.deepEqual(returned.store.getRecipients(), []);
  }],
  ["Storage failures do not throw; failed legacy removal leaves no authorized history", () => {
    const current = session(); const key = recipientMemoryKey(current);
    stored.set("unrelated-cuevion-data", "preserve");
    stored.set(LEGACY_RECIPIENT_MEMORY_KEY, JSON.stringify(["unowned@example.test"]));
    stored.set(key, JSON.stringify({ version: 1, owner: ownerA, epoch: current.epoch, recipients: ["owned@example.test"] }));
    rejectRemove = true;
    const blocked = getRecipientMemory(current);
    assert.doesNotThrow(() => { blocked.start(); blocked.remember(["new@example.test"]); });
    assert.deepEqual(blocked.getRecipients(), []);
    assert.equal(reads.includes(LEGACY_RECIPIENT_MEMORY_KEY), false);
    assert.equal(stored.get("unrelated-cuevion-data"), "preserve");
    rejectRemove = false; clearMailboxSnapshotSession();
    const h = memory(); h.store.remember(["prior@example.test"]); rejectWrite = true;
    assert.doesNotThrow(() => h.store.remember(["quota@example.test"]));
    assert.deepEqual(h.store.getRecipients(), []);
    rejectWrite = false; denied = true;
    assert.doesNotThrow(() => { h.store.remember(["denied@example.test"]); clearMailboxSnapshotSession(); });
    const unavailable = memory();
    assert.equal(unavailable.current.persistent, false);
    assert.deepEqual(unavailable.store.getRecipients(), []);
  }],
  ["Legacy reintroduction from an old tab is removed without migration", () => {
    const h = memory(); h.store.remember(["own@example.test"]);
    stored.set(LEGACY_RECIPIENT_MEMORY_KEY, '["other@example.test"]'); storageEvent(LEGACY_RECIPIENT_MEMORY_KEY);
    assert.equal(stored.has(LEGACY_RECIPIENT_MEMORY_KEY), false);
    assert.deepEqual(h.store.getRecipients(), ["own@example.test"]);
    stored.set(LEGACY_RECIPIENT_MEMORY_KEY, '["other@example.test"]'); rejectRemove = true;
    storageEvent(LEGACY_RECIPIENT_MEMORY_KEY);
    assert.deepEqual(h.store.getRecipients(), []);
    assert.equal(reads.includes(LEGACY_RECIPIENT_MEMORY_KEY), false);
  }],
  ["Account transition during a write removes only the old epoch's result", () => {
    const h = memory();
    afterWrite = () => { stored.set(MAILBOX_SESSION_KEY, JSON.stringify({ owner: ownerB, epoch: "new-epoch" })); };
    h.store.remember(["private@example.test"]);
    assert.deepEqual(h.store.getRecipients(), []); assert.equal(stored.has(h.key), false);
  }],
  ["Mounted hook clears visible history, retains captured writers and cleans up after unmount", () => {
    let latest!: ReturnType<typeof useRecipientMemory>;
    function View() { latest = useRecipientMemory(); return React.createElement("div", null, latest.getRecipients().join(",")); }
    const h = memory(); h.store.remember(["private@example.test"]);
    let tree: ReturnType<typeof create>;
    act(() => { tree = create(React.createElement(View)); });
    assert.equal(tree!.root.findByType("div").children.join(""), "");
    act(() => { tree!.update(React.createElement(MailboxSnapshotContext.Provider, { value: h.current }, React.createElement(View))); });
    assert.equal(tree!.root.findByType("div").children.join(""), "private@example.test");
    const oldWriter = latest.remember;
    act(() => {
      stored.set(MAILBOX_SESSION_KEY, JSON.stringify({ owner: ownerB, epoch: "cross-tab" }));
      storageEvent(MAILBOX_SESSION_KEY);
    });
    assert.equal(tree!.root.findByType("div").children.join(""), "");
    const other = memory(ownerB);
    act(() => { tree!.update(React.createElement(MailboxSnapshotContext.Provider, { value: other.current }, React.createElement(View))); });
    act(() => latest.remember(["own@example.test"]));
    oldWriter(["late@example.test"]);
    assert.equal(tree!.root.findByType("div").children.join(""), "own@example.test");
    act(() => tree!.unmount());
    assert.equal(stored.has(other.key), true, "ordinary navigation preserves authorized history");
    clearMailboxSnapshotSession();
    assert.equal(stored.has(other.key), false, "revocation cleanup outlives the mounted Compose");
  }],
  ["Actual To/Cc/Bcc suggestion callbacks preserve matching and reject stale selection", () => {
    const h = memory(); h.store.remember(["alice@example.test", "brenda@example.test", "charlie@example.test"]);
    const source = readFileSync(resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"), "utf8");
    const start = source.indexOf("  const getComposeRecipientValue =");
    const end = source.indexOf("  const handleComposeRecipientBlur =", start);
    const code = transform(source.slice(start, end), { transforms: ["typescript"] }).code;
    const scope: any = { composeTo: "", composeCc: "", composeBcc: "", replyModeSessionRef: { current: null },
      getRememberedRecipients: h.store.getRecipients, extractRecipientEmails, normalizeRememberedRecipient,
      setActiveRecipientSuggestionField: () => {},
    };
    for (const field of ["To", "Cc", "Bcc"]) scope[`setCompose${field}`] = (value: string) => { scope[`compose${field}`] = value; };
    const callbacks = new Function("scope", `with (scope) { ${code}; return { getRecipientSuggestions, applyRecipientSuggestion }; }`)(scope);
    for (const field of ["to", "cc", "bcc"]) {
      const name = field === "to" ? "alice" : field === "cc" ? "brenda" : "charlie";
      const state = `compose${field === "to" ? "To" : field === "cc" ? "Cc" : "Bcc"}`;
      scope[state] = "existing@example.test; " + name.toUpperCase();
      assert.deepEqual(callbacks.getRecipientSuggestions(field), [`${name}@example.test`]);
      callbacks.applyRecipientSuggestion(field, `${name}@example.test`);
      assert.equal(scope[state], `existing@example.test; ${name}@example.test`);
      assert.deepEqual(callbacks.getRecipientSuggestions(field), []);
    }
    scope.composeTo = "example";
    assert.equal(callbacks.getRecipientSuggestions("to").length, 3, "substring matching is retained");
    h.store.remember(Array.from({ length: 60 }, (_, index) => `recipient${index}@example.test`));
    assert.equal(h.store.getRecipients().length, 50);
    assert.equal(callbacks.getRecipientSuggestions("to").length, 6);
    stored.set(MAILBOX_SESSION_KEY, JSON.stringify({ owner: ownerB, epoch: "next" }));
    assert.deepEqual(callbacks.getRecipientSuggestions("to"), []);
    callbacks.applyRecipientSuggestion("to", "recipient0@example.test");
    assert.equal(scope.composeTo, "example", "stale suggestion DOM cannot insert a revoked address");
  }],
];

async function run() {
  try {
    for (const [name, check] of cases) { reset(); await check(); console.log(`PASS ${name}`); }
    console.log(`Recipient memory: ${cases.length} synthetic isolation and Compose scenarios passed`);
  } finally { reset(); (globalThis as any).window = priorWindow; globalThis.fetch = priorFetch; }
}
void run().catch((error) => { console.error(error); process.exitCode = 1; });
