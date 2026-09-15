import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test } from "node:test";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { transform } from "sucrase";
import { createMailboxSyncSuccessTimers, resolveMailboxSyncPresentation } from "../../lib/mailboxRefreshSemantics";

// Execute production presentation and effect lifecycle with a deterministic clock.
const source = readFileSync(resolve(__dirname, "WorkspaceShell.tsx"), "utf8");
const parsed = ts.createSourceFile("WorkspaceShell.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const declarations = new Map<string, string>();
let setup = "";
function visit(node: ts.Node) {
  if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
    declarations.set(node.name.text, `const ${node.name.text} = ${node.initializer.getText(parsed)};`);
  }
  if (ts.isFunctionDeclaration(node) && node.name) {
    declarations.set(node.name.text, node.getText(parsed).replace(/^export /, ""));
  }
  if (ts.isCallExpression(node) && node.expression.getText(parsed) === "useEffect") {
    const callback = node.arguments[0]?.getText(parsed) ?? "";
    if (callback.includes("const timers = createMailboxSyncSuccessTimers")) setup = callback;
  }
  ts.forEachChild(node, visit);
}
visit(parsed);
function evaluate(code: string, dependencies: Record<string, unknown>) {
  return new Function(...Object.keys(dependencies), transform(code, { transforms: ["typescript", "jsx"] }).code)(...Object.values(dependencies));
}
function select(names: string[]) {
  return names.map((name) => {
    assert.ok(declarations.has(name), `Production declaration ${name} exists`);
    return declarations.get(name);
  }).join("\n");
}
function harness(run: (h: any) => void) {
  let now = 0;
  let nextId = 1;
  let revision = 0;
  const pending = new Map<number, { due: number; callback: () => void }>();
  const callbacks: Array<() => void> = [];
  const realSetTimeout = globalThis.setTimeout;
  const realClearTimeout = globalThis.clearTimeout;
  globalThis.setTimeout = ((callback: () => void, delay: number) => {
    const id = nextId++;
    pending.set(id, { due: now + delay, callback });
    callbacks.push(callback);
    return id;
  }) as any;
  globalThis.clearTimeout = ((id: number) => pending.delete(id)) as any;
  let readiness: Record<string, string> = {};
  const deps: Record<string, any> = {
    createMailboxSyncSuccessTimers, resolveMailboxSyncPresentation,
    mailboxSyncSuccessTimersRef: { current: null },
    setMailboxSyncActivityRevision: (update: (previous: number) => number) => { revision = update(revision); },
    updateMailboxInboxReadiness: (update: (previous: unknown) => any) => { readiness = update(readiness); },
    learningStorageKey: "member:one", hasAuthenticatedMemberAuthority: true,
    orderedMailboxes: [{ id: "a" }, { id: "b" }],
    providerArchiveCurrentConnectionKeysRef: { current: { a: "connection:a", b: "connection:b" } },
    providerArchiveConnectionEpochsRef: { current: {} },
    syncingMailboxIdsRef: { current: new Set() },
    providerArchiveFetchMailboxIdsRef: { current: new Set() },
    providerTrashFetchMailboxIdsRef: { current: new Set() },
    providerArchiveFolderStatusMessages: {}, providerTrashFolderStatusMessages: {},
  };
  assert.ok(setup, "Production timer setup and cleanup exist");
  const mount = () => evaluate(`return (${setup})();`, deps);
  let cleanup = mount();
  const scope = (id: string) => evaluate(`${select(["readMailboxSyncPresentationScope"])} return readMailboxSyncPresentationScope(${JSON.stringify(id)});`, deps);
  const set = (id: string, value: string, fallback = false) => {
    evaluate(`${select(["setMailboxInboxReadiness"])} return setMailboxInboxReadiness;`, deps)(scope(id), value, fallback);
  };
  const render = () => {
    const rendered = evaluate(`${select(["readMailboxSyncPresentationScope", "mailboxSyncPresentation"])} return { mailboxSyncPresentation };`, { ...deps, mailboxInboxReadiness: readiness });
    return rendered.mailboxSyncPresentation;
  };
  try {
    run({
      deps, set, scope, render, callbacks,
      get pending() { return pending.size; },
      get revision() { return revision; },
      advance(ms: number) {
        now += ms;
        for (const [id, timer] of [...pending]) {
          if (timer.due <= now && pending.has(id)) { pending.delete(id); timer.callback(); }
        }
      },
      unmount() { cleanup(); },
      remount() { cleanup(); cleanup = mount(); },
    });
  } finally {
    cleanup();
    globalThis.setTimeout = realSetTimeout;
    globalThis.clearTimeout = realClearTimeout;
    assert.equal(pending.size, 0, "Unmount leaves no success timers");
  }
}

test("idle is silent, active Inbox says Syncing…, publication says Updated for exactly 2 seconds", () => harness((h) => {
  assert.equal(h.render().a.message, null);
  h.deps.syncingMailboxIdsRef.current.add("a");
  h.set("a", "refreshing");
  assert.equal(h.render().a.message, "Syncing…");
  h.set("a", "updated");
  assert.equal(h.render().a.message, "Updated");
  assert.equal(h.render().a.inboxRefreshing, false);
  h.advance(1999);
  assert.equal(h.render().a.message, "Updated");
  h.advance(1);
  assert.equal(h.render().a.message, null);
  assert.equal(h.revision, 1, "Expiry requests a React render");
  assert.equal(h.render().a.inboxUpdated, true, "Timer cannot revoke Inbox readiness");
  assert.equal(h.render().a.operationInFlight, true, "Timer cannot release provider lock");
}));

test("background activity and late success cannot start or extend a success message", () => harness((h) => {
  h.deps.providerArchiveFetchMailboxIdsRef.current.add("a");
  assert.equal(h.render().a.message, null);
  h.set("a", "updated");
  h.advance(1500);
  h.set("a", "not_updated", true);
  h.deps.providerArchiveFetchMailboxIdsRef.current.delete("a");
  h.advance(500);
  assert.equal(h.render().a.message, null);
  assert.equal(h.render().a.inboxUpdated, true);
  h.deps.providerTrashFetchMailboxIdsRef.current.add("a");
  h.deps.providerTrashFetchMailboxIdsRef.current.delete("a");
  assert.equal(h.render().a.message, null);
  assert.equal(h.pending, 0);
}));

test("new sync removes success immediately; cancelled callback cannot expire replacement", () => harness((h) => {
  h.set("a", "updated");
  const stale = h.callbacks[0];
  h.advance(1000);
  h.deps.syncingMailboxIdsRef.current.add("a");
  h.set("a", "refreshing");
  assert.equal(h.pending, 0);
  assert.equal(h.render().a.message, "Syncing…");
  h.set("a", "updated");
  stale();
  h.advance(1000);
  assert.equal(h.render().a.message, "Updated");
  h.advance(1000);
  assert.equal(h.render().a.message, null);
}));

test("Inbox failure and partial Inbox failure cancel success", () => harness((h) => {
  h.set("a", "updated");
  h.set("a", "not_updated");
  assert.equal(h.pending, 0);
  assert.equal(h.render().a.message, "Couldn’t update inbox");
  h.set("a", "updated");
  h.set("a", "partial");
  assert.equal(h.pending, 0);
  assert.equal(h.render().a.message, "Couldn’t fully update inbox");
  assert.equal(h.render().a.inboxUpdated, true);
}));

for (const folders of [["Archive"], ["Trash"], ["Archive", "Trash"]]) {
  test(`${folders.join(" + ")} diagnostics stay internal and do not interrupt Inbox success or idle`, () => harness((h) => {
    const diagnostics = folders.map((folder) => ({
      state: h.deps[`provider${folder}FolderStatusMessages`],
      message: `${folder} provider discovery/snapshot warning`,
    }));
    const publishDiagnostics = () => {
      for (const { state, message } of diagnostics) state.a = message;
    };
    const assertDiagnostics = () => {
      for (const { state, message } of diagnostics) assert.equal(state.a, message);
    };
    publishDiagnostics();
    assert.equal(h.render().a.message, null, "Background errors alone leave idle silent");
    h.deps.syncingMailboxIdsRef.current.add("a");
    h.set("a", "refreshing");
    assert.equal(h.render().a.message, "Syncing…");
    h.set("a", "updated");
    h.advance(1000);
    publishDiagnostics();
    assert.equal(h.render().a.message, "Updated", "Background errors cannot replace success");
    assert.equal(h.pending, 1, "Background errors cannot cancel success timer");
    assertDiagnostics();
    h.advance(999);
    assert.equal(h.render().a.message, "Updated");
    h.advance(1);
    assert.equal(h.render().a.message, null);
    assert.equal(h.render().a.inboxUpdated, true);
    assert.equal(h.render().a.operationInFlight, true, "Provider lock is unchanged");
    publishDiagnostics();
    assert.equal(h.render().a.message, null, "Late background errors cannot narrate idle");
    assertDiagnostics();
    h.set("a", "not_updated");
    assert.equal(h.render().a.message, "Couldn’t update inbox");
    h.set("a", "partial");
    assert.equal(h.render().a.message, "Couldn’t fully update inbox");
    assertDiagnostics();
  }));
}

test("concurrent mailbox timers and switching selection cannot leak or expire each other", () => harness((h) => {
  h.set("a", "updated");
  assert.equal(h.render().b.message, null, "A completion while B is selected stays scoped to A");
  h.advance(1000);
  h.set("b", "updated");
  h.advance(1000);
  assert.equal(h.render().a.message, null);
  assert.equal(h.render().b.message, "Updated");
  h.advance(1000);
  assert.equal(h.render().b.message, null);
}));

test("connection identity, incarnation and MEMBER scope fence old success and late callbacks", () => harness((h) => {
  h.set("a", "updated");
  const originalScope = h.scope("a");
  h.deps.providerArchiveCurrentConnectionKeysRef.current.a = "replacement:a";
  assert.equal(h.render().a.message, null);
  h.deps.providerArchiveCurrentConnectionKeysRef.current.a = "connection:a";
  h.deps.providerArchiveConnectionEpochsRef.current.a = 1;
  assert.equal(h.render().a.message, null);
  h.deps.learningStorageKey = "member:two";
  assert.equal(h.render().a.message, null);
  evaluate(`${select(["setMailboxInboxReadiness"])} return setMailboxInboxReadiness;`, h.deps)(originalScope, "updated");
  assert.equal(h.render().a.message, null);
  h.set("a", "updated");
  h.advance(2000);
  assert.equal(h.render().a.message, null);
  h.deps.hasAuthenticatedMemberAuthority = false;
  assert.deepEqual(h.render(), {});
}));

test("actual unmount cancels all timers and ignores callbacks or late publication", () => harness((h) => {
  h.set("a", "updated"); h.set("b", "updated");
  assert.equal(h.pending, 2);
  const callbacks = [...h.callbacks];
  h.unmount();
  assert.equal(h.pending, 0);
  for (const callback of callbacks) callback();
  h.set("a", "updated");
  assert.equal(h.pending, 0);
  assert.equal(h.revision, 0);
  h.remount();
  h.set("a", "updated");
  assert.equal(h.pending, 1);
  callbacks[0]();
  assert.equal(h.render().a.message, "Updated");
}));

test("desktop renders actual Sync control and temporary status without changing the lock", () => harness((h) => {
  const start = source.indexOf('<MailToolbarIconButton\n            label={mailboxSyncPresentation.inboxRefreshing');
  const end = source.indexOf('<MailToolbarIconButton', start + 1);
  assert.ok(start >= 0 && end > start, "Production Sync control and status found");
  const render = () => renderToStaticMarkup(evaluate(
    `${select(["MailToolbarIconButton"])} return <>${source.slice(start, end)}</>;`,
    {
      React, mailboxSecondaryActionButtonClass: "toolbar",
      mailboxSyncPresentation: h.render().a,
      isSyncingMailbox: h.deps.syncingMailboxIdsRef.current.has("a"),
      onSyncMailbox: () => {},
    },
  ));
  assert.doesNotMatch(render(), /role="status"|animate-spin|disabled=""/);
  h.deps.syncingMailboxIdsRef.current.add("a");
  h.set("a", "refreshing");
  assert.match(render(), /role="status"[^>]*>Syncing…<\/span>/);
  assert.match(render(), /animate-spin/);
  h.set("a", "updated");
  h.deps.providerArchiveFolderStatusMessages.a = "Archive snapshot warning";
  h.deps.providerTrashFolderStatusMessages.a = "Trash snapshot warning";
  assert.match(render(), /role="status"[^>]*>Updated<\/span>/);
  assert.doesNotMatch(render(), /animate-spin/);
  assert.match(render(), /disabled=""/);
  h.advance(2000);
  assert.doesNotMatch(render(), /role="status"|Updated|Syncing…|animate-spin|Some folders|snapshot warning/);
  assert.match(render(), /disabled=""/);
  h.deps.syncingMailboxIdsRef.current.delete("a");
  assert.doesNotMatch(render(), /disabled=""/);
}));
