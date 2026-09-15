import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { transform } from "sucrase";
import { pruneInboxSnapshot } from "../../lib/inboxEngine";
import {
  createCustomImapInboxAuthority,
  createMailboxSyncSuccessTimers,
  createGmailInboxAuthority,
  createGmailUnreadIntentAuthority,
  resolveMailboxRefreshPlan,
  resolveMailboxSyncPresentation,
  resolveSuccessfulInboxRefreshPresentation,
  startIndependentMailboxFetches,
} from "../../lib/mailboxRefreshSemantics";

const workspaceSource = readFileSync(resolve(__dirname, "WorkspaceShell.tsx"), "utf8");
const parsedSource = ts.createSourceFile(
  "WorkspaceShell.tsx", workspaceSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX,
);
const declarations = new Map<string, string>();
function collectDeclarations(node: ts.Node) {
  if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
    declarations.set(node.name.text, `const ${node.name.text} = ${node.initializer.getText(parsedSource)};`);
  } else if (ts.isFunctionDeclaration(node) && node.name) {
    declarations.set(node.name.text, node.getText(parsedSource).replace(/^export /, ""));
  }
  ts.forEachChild(node, collectDeclarations);
}
collectDeclarations(parsedSource);
function declaration(name: string) {
  const source = declarations.get(name);
  assert.ok(source, `Actual WorkspaceShell declaration exists: ${name}`);
  return source;
}
function evaluate(names: string[], dependencies: Record<string, unknown>) {
  const code = transform(names.map(declaration).join("\n"), {
    transforms: ["typescript", "jsx"], production: true,
  }).code;
  return new Function(...Object.keys(dependencies), `${code}\nreturn {${names.join(",")}};`)(
    ...Object.values(dependencies),
  );
}

type Message = {
  id: string;
  subject: string;
  serverMailboxId: string;
  providerFolder: string;
  body: string[];
  unread: boolean;
  timestamp: string;
  providerMessageId?: string;
  labelIds?: string[];
  imapUid?: string;
  uidValidity?: string;
};
type Response = {
  ok: boolean;
  messages?: Message[];
  inboxUidSet?: string[];
  uidValidity?: string;
  error?: { code: string; message?: string; stage?: string };
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}
// Flush only promise continuations; no timers, real network, or machine-speed assumptions.
async function flush() {
  for (let step = 0; step < 12; step += 1) await Promise.resolve();
}
function message(mailboxId: string, id: string, imap = false): Message {
  return {
    id, subject: id, serverMailboxId: mailboxId, providerFolder: "INBOX",
    body: [`Body for ${id}`], unread: true, timestamp: new Date().toISOString(),
    ...(imap
      ? { imapUid: id, uidValidity: "11" }
      : { providerMessageId: id, labelIds: ["INBOX"] }),
  };
}

// Run the real refresh orchestration, state publishers, error handling, merge,
// request plan and mutation authorities. Only provider I/O, React setters, and
// peripheral persistence/health integrations are replaced by observable boundaries.
function createHarness(
  mailboxes: Array<{ id: string; imap?: boolean }> = [{ id: "a" }],
  options: { authenticated?: boolean } = {},
) {
  const events: string[] = [];
  const syncingMailboxIdsRef = { current: new Set<string>() };
  const providerArchiveFetchMailboxIdsRef = { current: new Set<string>() };
  const providerTrashFetchMailboxIdsRef = { current: new Set<string>() };
  const providerArchiveCurrentConnectionKeysRef = { current: Object.fromEntries(mailboxes.map(({ id }) => [id, `connection:${id}`])) };
  const providerArchiveConnectionEpochsRef = { current: {} as Record<string, number> };
  const providerImapTrashInboxMutationPublicationEpochsRef = { current: {} as Record<string, number> };
  const gmailInboxAuthorityRef = { current: createGmailInboxAuthority() };
  const customImapInboxAuthorityRef = { current: createCustomImapInboxAuthority() };
  const gmailUnreadIntentAuthorityRef = { current: createGmailUnreadIntentAuthority() };
  const mailboxStore = Object.fromEntries(mailboxes.map(({ id, imap }) => [id, { Inbox: [message(id, "cached", imap)] }]));
  const snapshots = Object.fromEntries(mailboxes.map(({ id }) => [id, {
    messages: [...mailboxStore[id].Inbox], uidValidity: "11",
  }]));
  const inbox = Object.fromEntries(mailboxes.map(({ id }) => [id, deferred<Response>()]));
  const archive = Object.fromEntries(mailboxes.map(({ id }) => [id, deferred<boolean>()]));
  const trash = Object.fromEntries(mailboxes.map(({ id }) => [id, deferred<boolean>()]));
  const imapRequests: Array<{ mailboxId: string; limit?: number }> = [];
  const inboxRequestCounts: Record<string, number> = {};
  const retries: Record<string, Response> = {};
  const errors: Record<string, string> = {};
  const folderErrors: Record<string, string> = {};
  const lastRefreshDiagnosticRef = { current: {} as Record<string, any> };
  const publications: Array<{ mailboxId: string; messages: Message[]; evicted?: Set<string>; options: any }> = [];
  let scopedReadiness: Record<string, any> = {};
  let activityRevision = 0;
  let memberScope = "member:one";
  let hasMemberAuthority = options.authenticated !== false;
  let scalarSyncingMailboxId: string | null = null;
  let persistenceFailure: Error | null = null;
  let runtime: any;
  const update = (previous: any, next: any) => typeof next === "function" ? next(previous) : next;
  const publishActivity = (id: string) => runtime.publishMailboxSyncActivity(id);
  const startInbox = ({ mailboxId, limit }: { mailboxId: string; limit?: number }, imap = false) => {
    events.push(`inbox:${mailboxId}:${limit ?? "default"}`);
    inboxRequestCounts[mailboxId] = (inboxRequestCounts[mailboxId] ?? 0) + 1;
    if (imap) imapRequests.push({ mailboxId, limit });
    return imap && inboxRequestCounts[mailboxId] > 1
      ? Promise.resolve(retries[mailboxId])
      : inbox[mailboxId].promise;
  };
  const dependencies = {
    hasAuthenticatedMemberAuthority: hasMemberAuthority,
    learningStorageKey: memberScope,
    orderedMailboxes: mailboxes,
    hasPendingProviderArchiveForMailbox: () => false,
    hasPendingProviderImapTrashForMailbox: () => false,
    hasPendingProviderTrashForMailbox: () => false,
    providerArchivePendingKeys: new Set(), providerImapTrashPendingKeys: new Set(), providerTrashPendingKeys: new Set(),
    savedManagedInboxes: mailboxes.map(({ id, imap }) => ({
      id, connected: true, connectionStatus: "connected", provider: imap ? "custom_imap" : "google",
      email: `${id}@example.test`, customImap: { host: "imap.example.test", port: "993", ssl: true },
    })),
    cloneManagedWorkspaceInbox: (value: unknown) => structuredClone(value),
    isImapCredentialsProvider: (provider: string) => provider === "custom_imap",
    readCurrentProviderArchiveKnowledge: () => ({ hasSnapshot: true, capability: "available" }),
    resolveMailboxRefreshPlan, startIndependentMailboxFetches, resolveSuccessfulInboxRefreshPresentation,
    syncingMailboxIdsRef, providerArchiveFetchMailboxIdsRef, providerTrashFetchMailboxIdsRef,
    syncingMailboxId: null,
    setSyncingMailboxId: (next: any) => { scalarSyncingMailboxId = update(scalarSyncingMailboxId, next); },
    mailboxSyncSuccessTimersRef: { current: createMailboxSyncSuccessTimers(() => {}) },
    updateMailboxInboxReadiness: (next: any) => { scopedReadiness = update(scopedReadiness, next); },
    setMailboxSyncActivityRevision: (next: any) => { activityRevision = update(activityRevision, next); },
    beginMailboxHealthCheck: (id: string) => ({ id }), completeMailboxHealthCheck: () => {}, cancelMailboxHealthCheck: () => {},
    drainGmailArchiveReconciliation: () => {},
    clearMailboxSyncError: (id: string) => { delete errors[id]; },
    setMailboxSyncError: (id: string, error: string) => { errors[id] = error; },
    setMailboxSyncFeedbackMessage: () => {}, setSavedManagedInboxes: () => {},
    effectiveFocusPreferencesByMailbox: {}, buildRefreshInboxRequest: (value: unknown) => value,
    fetchGmailInbox: (request: { mailboxId: string }) => startInbox(request),
    connectInboxWithImap: (request: { mailboxId: string; limit?: number }) => startInbox(request, true),
    refreshProviderArchiveById: async (id: string) => {
      events.push(`archive:${id}`);
      providerArchiveFetchMailboxIdsRef.current.add(id);
      publishActivity(id);
      try { if (!(await archive[id].promise)) folderErrors[id] = "Archive refresh failed"; }
      finally { providerArchiveFetchMailboxIdsRef.current.delete(id); publishActivity(id); }
    },
    refreshProviderTrashById: async (id: string) => {
      events.push(`trash:${id}`);
      providerTrashFetchMailboxIdsRef.current.add(id);
      publishActivity(id);
      try { if (!(await trash[id].promise)) folderErrors[id] = "Trash refresh failed"; }
      finally { providerTrashFetchMailboxIdsRef.current.delete(id); publishActivity(id); }
    },
    gmailInboxAuthorityRef, customImapInboxAuthorityRef, gmailUnreadIntentAuthorityRef,
    providerArchiveCurrentConnectionKeysRef, providerArchiveConnectionEpochsRef,
    providerImapTrashInboxMutationPublicationEpochsRef,
    lastRefreshDiagnosticRef,
    applyCachedLiveInboxSnapshotToMailboxStore: (id: string) => snapshots[id].messages.length,
    buildTrustedLiveInboxSnapshotContexts: () => ({}), readLiveInboxSnapshots: () => snapshots,
    mailboxStore,
    saveLiveInboxSnapshot: (snapshot: any) => {
      events.push(`persist:${snapshot.inboxId}`);
      if (persistenceFailure) throw persistenceFailure;
      snapshots[snapshot.inboxId] = snapshot;
    },
    applyLiveInboxMessagesToMailboxStore: (id: string, messages: Message[], evicted: Set<string> | undefined, options: any) => {
      events.push(`publish:${id}`);
      publications.push({ mailboxId: id, messages, evicted, options });
      mailboxStore[id].Inbox = messages;
    },
    prioritySemanticNewInboundAcceptedRefreshAuthorityRef: { current: {} },
    setPrioritySemanticNewInboundHydrationRefreshEpoch: () => {},
    isCanonicalPrioritySemanticNewInboundImapInteger: (value: unknown) => typeof value === "string" && /^[1-9]\d*$/.test(value),
    clearUnreadOverridesForProviderMessages: () => {},
    pruneInboxSnapshot, isWorkspaceMessageSpamSuppressed: () => false,
  };
  runtime = evaluate([
    "buildStablePreviewIdentity", "getCanonicalMessageIdentityKeys", "findMatchingMessageByIdentity", "buildMessageIdentityIndexes",
    "hasRenderableMessagePayload", "mergePersistedLiveInboxSnapshotMessages", "buildLiveThreadIdentityContext",
    "isQuotaRefreshIssue", "resolveMailboxRefreshWarningMessage", "resolveMailboxRefreshErrorMessage",
    "publishMailboxSyncActivity", "readMailboxSyncPresentationScope", "setMailboxInboxReadiness", "refreshMailboxById",
  ], dependencies);
  const renderPresentation = () => evaluate([
    "readMailboxSyncPresentationScope", "mailboxSyncPresentation",
  ], {
    ...dependencies, learningStorageKey: memberScope, hasAuthenticatedMemberAuthority: hasMemberAuthority,
    mailboxInboxReadiness: scopedReadiness, resolveMailboxSyncPresentation,
    providerArchiveFolderStatusMessages: folderErrors, providerTrashFolderStatusMessages: {},
  });
  return {
    refresh: runtime.refreshMailboxById, events, inbox, archive, trash, mailboxStore, snapshots, errors, folderErrors,
    publications, inboxRequestCounts, imapRequests, retries, lastRefreshDiagnosticRef,
    syncingMailboxIdsRef, providerArchiveFetchMailboxIdsRef, providerTrashFetchMailboxIdsRef,
    gmailInboxAuthorityRef, customImapInboxAuthorityRef, gmailUnreadIntentAuthorityRef,
    providerArchiveCurrentConnectionKeysRef, providerImapTrashInboxMutationPublicationEpochsRef,
    get readiness() {
      const rendered = renderPresentation();
      return Object.fromEntries(mailboxes.map(({ id }) => [id, scopedReadiness[rendered.readMailboxSyncPresentationScope(id)]]));
    },
    get scopedReadiness() { return scopedReadiness; },
    get activityRevision() { return activityRevision; },
    get activity() {
      return Object.fromEntries(mailboxes.map(({ id }) => [id, {
        inbox: syncingMailboxIdsRef.current.has(id), archive: providerArchiveFetchMailboxIdsRef.current.has(id),
        trash: providerTrashFetchMailboxIdsRef.current.has(id),
      }]));
    },
    get scalarSyncingMailboxId() { return scalarSyncingMailboxId; },
    failPersistence() { persistenceFailure = new Error("Snapshot write failed"); },
    switchMember(scope: string) { memberScope = scope; },
    revokeMemberAuthority() { hasMemberAuthority = false; },
    presentation(id: string) { return renderPresentation().mailboxSyncPresentation[id]; },
  };
}

let testCount = 0;
async function test(name: string, run: () => void | Promise<void>) {
  await run();
  testCount += 1;
  console.log(`PASS Inbox readiness: ${name}`);
}
async function main() {
  await test("fresh Gmail Inbox publishes before slow Trash, with background work and locks retained", async () => {
    const h = createHarness();
    const operation = h.refresh("a", { reason: "manual" });
    assert.deepEqual(h.events, ["inbox:a:default", "archive:a"]);
    assert.equal(h.readiness.a, "refreshing");
    assert.equal(h.activityRevision, 2, "Inbox and independent Archive both request a render");
    assert.equal(h.mailboxStore.a.Inbox[0].id, "cached");
    assert.equal(h.presentation("a").inboxRefreshing, true);
    assert.equal(h.presentation("a").inboxUpdated, false);
    h.inbox.a.resolve({ ok: true, messages: [message("a", "fresh")] });
    await flush();
    assert.deepEqual(h.events, ["inbox:a:default", "archive:a", "persist:a", "publish:a", "trash:a"]);
    assert.equal(h.readiness.a, "updated");
    assert.equal(h.mailboxStore.a.Inbox[0].id, "fresh");
    assert.equal(h.presentation("a").inboxRefreshing, false);
    assert.equal(h.presentation("a").inboxUpdated, true);
    assert.equal(h.presentation("a").message, "Updated");
    assert.equal(h.syncingMailboxIdsRef.current.has("a"), true);
    assert.equal(h.activity.a.trash, true);
    for (const reason of ["manual", "interval", "mailbox_open"]) {
      assert.equal(await h.refresh("a", { reason }), "skipped");
    }
    assert.equal(h.inboxRequestCounts.a, 1);
    assert.equal(h.events.filter((event) => event === "archive:a").length, 1);
    h.trash.a.resolve(true);
    assert.equal(await operation, "synced");
    assert.equal(h.syncingMailboxIdsRef.current.has("a"), false);
    assert.equal(h.activity.a.archive, true);
    assert.equal(h.presentation("a").message, "Updated");
    h.archive.a.resolve(true);
    await flush();
    assert.equal(h.presentation("a").message, "Updated");
    assert.equal(h.activityRevision, 6, "Every started/finished provider activity requests a render");
  });

  await test("Trash failure preserves published Inbox and reports folder attention", async () => {
    const h = createHarness();
    h.archive.a.resolve(true);
    const operation = h.refresh("a", { reason: "manual" });
    h.inbox.a.resolve({ ok: true, messages: [message("a", "fresh")] });
    await flush();
    h.trash.a.resolve(false);
    assert.equal(await operation, "synced");
    assert.equal(h.readiness.a, "updated");
    assert.equal(h.mailboxStore.a.Inbox[0].id, "fresh");
    assert.equal(h.presentation("a").message, "Some folders couldn’t update");
  });

  await test("Inbox failure never labels cached rows fresh, including during slow Trash", async () => {
    const h = createHarness();
    const operation = h.refresh("a", { reason: "mailbox_open" });
    h.inbox.a.resolve({ ok: false, error: { code: "provider_failed", message: "Refresh failed" } });
    await flush();
    assert.equal(h.readiness.a, "not_updated");
    assert.equal(h.presentation("a").inboxUpdated, false);
    assert.equal(h.mailboxStore.a.Inbox[0].id, "cached");
    assert.equal(h.publications.length, 0);
    assert.equal(h.syncingMailboxIdsRef.current.has("a"), true);
    assert.equal(h.presentation("a").message, "Couldn’t update inbox");
    h.trash.a.resolve(true);
    assert.equal(await operation, "failed");
    assert.equal(h.readiness.a, "not_updated");
  });

  await test("independent slow Archive outlives completed Inbox operation", async () => {
    const h = createHarness();
    h.trash.a.resolve(true);
    const operation = h.refresh("a", { reason: "manual" });
    h.inbox.a.resolve({ ok: true, messages: [message("a", "fresh")] });
    assert.equal(await operation, "synced");
    assert.equal(h.readiness.a, "updated");
    assert.equal(h.activity.a.inbox, false);
    assert.equal(h.activity.a.archive, true);
    assert.equal(h.presentation("a").inboxRefreshing, false);
    h.archive.a.resolve(true);
    await flush();
    assert.equal(h.activity.a.archive, false);
  });

  await test("simultaneous mailbox statuses survive scalar syncing id replacement", async () => {
    const h = createHarness([{ id: "a" }, { id: "b" }]);
    const first = h.refresh("a", { reason: "mailbox_open" });
    const second = h.refresh("b", { reason: "mailbox_open" });
    assert.equal(h.scalarSyncingMailboxId, "b");
    assert.equal(h.activity.a.inbox, true);
    assert.equal(h.activity.b.inbox, true);
    h.inbox.a.resolve({ ok: true, messages: [message("a", "fresh-a")] });
    await flush();
    assert.equal(h.readiness.a, "updated");
    assert.equal(h.readiness.b, "refreshing");
    assert.equal(h.presentation("a").inboxRefreshing, false);
    assert.equal(h.presentation("b").inboxRefreshing, true);
    h.inbox.b.resolve({ ok: false, error: { code: "provider_failed" } });
    h.trash.b.resolve(true);
    assert.equal(await second, "failed");
    assert.equal(h.scalarSyncingMailboxId, null);
    assert.equal(h.activity.a.inbox, true);
    assert.equal(await h.refresh("a", { reason: "interval" }), "skipped");
    assert.equal(h.readiness.a, "updated");
    assert.equal(h.readiness.b, "not_updated");
    h.trash.a.resolve(true);
    assert.equal(await first, "synced");
  });

  await test("stale Gmail mutation generation cannot publish or claim readiness", async () => {
    const h = createHarness();
    const operation = h.refresh("a", { reason: "mailbox_open" });
    h.gmailInboxAuthorityRef.current.confirmArchive("a", "fresh");
    h.inbox.a.resolve({ ok: true, messages: [message("a", "fresh")] });
    assert.equal(await operation, "skipped");
    assert.equal(h.readiness.a, "not_updated");
    assert.equal(h.publications.length, 0);
    assert.deepEqual(h.events, ["inbox:a:default"]);
    assert.equal(h.mailboxStore.a.Inbox[0].id, "cached");
  });

  await test("changed connection identity rejects stale Inbox readiness", async () => {
    const h = createHarness();
    const operation = h.refresh("a", { reason: "mailbox_open" });
    h.providerArchiveCurrentConnectionKeysRef.current.a = "replacement-connection";
    h.inbox.a.resolve({ ok: true, messages: [message("a", "fresh")] });
    assert.equal(await operation, "skipped");
    assert.equal(h.readiness.a, undefined);
    assert.equal(Object.values(h.scopedReadiness).includes("not_updated"), true);
    assert.equal(h.publications.length, 0);
  });

  await test("late completion cannot attach Inbox freshness to another member scope", async () => {
    const h = createHarness();
    h.trash.a.resolve(true);
    const operation = h.refresh("a", { reason: "mailbox_open" });
    h.switchMember("member:two");
    h.inbox.a.resolve({ ok: true, messages: [message("a", "fresh")] });
    assert.equal(await operation, "synced");
    assert.equal(h.readiness.a, undefined);
    assert.equal(h.presentation("a").message, null);
    h.switchMember("member:one");
    assert.equal(h.readiness.a, "updated");
    h.revokeMemberAuthority();
    assert.equal(h.presentation("a"), undefined);
  });

  await test("missing authenticated member authority starts no refresh or presentation", async () => {
    const h = createHarness([{ id: "a" }], { authenticated: false });
    assert.equal(await h.refresh("a", { reason: "manual" }), "skipped");
    assert.deepEqual(h.events, []);
    assert.equal(h.readiness.a, undefined);
    assert.equal(h.presentation("a"), undefined);
    assert.equal(h.activityRevision, 0);
  });

  await test("reconciliation keeps its existing lock until paired Archive settles", async () => {
    const h = createHarness();
    const operation = h.refresh("a", { reason: "reconcile" });
    h.inbox.a.resolve({ ok: true, messages: [message("a", "fresh")] });
    await flush();
    assert.equal(h.readiness.a, "updated");
    assert.equal(h.syncingMailboxIdsRef.current.has("a"), true);
    assert.equal(h.events.includes("trash:a"), false);
    assert.equal(await h.refresh("a", { reason: "reconcile" }), "skipped");
    h.archive.a.resolve(true);
    assert.equal(await operation, "synced");
    assert.equal(h.syncingMailboxIdsRef.current.has("a"), false);
  });

  await test("Gmail unread intents remain authoritative at fresh publication", async () => {
    const h = createHarness();
    h.trash.a.resolve(true);
    const operation = h.refresh("a", { reason: "mailbox_open" });
    h.gmailUnreadIntentAuthorityRef.current.beginIntent("a", "fresh", false);
    h.inbox.a.resolve({ ok: true, messages: [message("a", "fresh")] });
    assert.equal(await operation, "synced");
    assert.equal(h.mailboxStore.a.Inbox[0].unread, false);
    assert.equal(h.readiness.a, "updated");
  });

  await test("IMAP quota retry preserves cached merge and partial readiness", async () => {
    const h = createHarness([{ id: "a", imap: true }]);
    h.retries.a = { ok: true, messages: [message("a", "12", true)], uidValidity: "11" };
    const operation = h.refresh("a", { reason: "startup" });
    assert.deepEqual(h.imapRequests, [{ mailboxId: "a", limit: 20 }]);
    assert.equal(h.mailboxStore.a.Inbox[0].id, "cached");
    h.inbox.a.resolve({ ok: false, error: { code: "quota_exceeded", stage: "fetch" } });
    assert.equal(await operation, "partial");
    assert.deepEqual(h.imapRequests, [{ mailboxId: "a", limit: 20 }, { mailboxId: "a", limit: 5 }]);
    assert.equal(h.readiness.a, "partial");
    assert.deepEqual(h.mailboxStore.a.Inbox.map(({ id }) => id), ["cached", "12"]);
    assert.equal(h.publications[0].options.replaceInbox, false);
    assert.equal(h.publications[0].evicted, undefined);
    assert.equal(h.lastRefreshDiagnosticRef.current.a.retried, true);
    assert.equal(h.events.includes("trash:a"), false);
    h.archive.a.resolve(true);
    await flush();
  });

  await test("IMAP UID metadata retains authoritative eviction", async () => {
    const h = createHarness([{ id: "a", imap: true }]);
    const operation = h.refresh("a", { reason: "mailbox_open" });
    h.inbox.a.resolve({ ok: true, messages: [message("a", "12", true)], uidValidity: "11", inboxUidSet: ["12"] });
    assert.equal(await operation, "synced");
    assert.equal(h.readiness.a, "updated");
    assert.deepEqual(h.mailboxStore.a.Inbox.map(({ id }) => id), ["12"]);
    assert.deepEqual(h.publications[0].evicted, new Set(["cached"]));
  });

  await test("IMAP mutation publication epoch fences stale responses", async () => {
    const h = createHarness([{ id: "a", imap: true }]);
    const operation = h.refresh("a", { reason: "mailbox_open" });
    h.providerImapTrashInboxMutationPublicationEpochsRef.current.a = 1;
    h.inbox.a.resolve({ ok: true, messages: [message("a", "12", true)], uidValidity: "11" });
    assert.equal(await operation, "skipped");
    assert.equal(h.readiness.a, "not_updated");
    assert.equal(h.publications.length, 0);
    assert.equal(h.mailboxStore.a.Inbox[0].id, "cached");
  });

  await test("required IMAP persistence failure cannot claim published Inbox", async () => {
    const h = createHarness([{ id: "a", imap: true }]);
    h.failPersistence();
    const operation = h.refresh("a", { reason: "mailbox_open" });
    h.inbox.a.resolve({ ok: true, messages: [message("a", "12", true)], uidValidity: "11" });
    await assert.rejects(operation, /Snapshot write failed/);
    assert.equal(h.readiness.a, "not_updated");
    assert.equal(h.publications.length, 0);
    assert.equal(h.syncingMailboxIdsRef.current.has("a"), false);
  });

  await test("failed IMAP quota retry keeps cached data without claiming fresh publication", async () => {
    const h = createHarness([{ id: "a", imap: true }]);
    h.retries.a = { ok: false, error: { code: "quota_exceeded" } };
    const operation = h.refresh("a", { reason: "mailbox_open" });
    h.inbox.a.resolve({ ok: false, error: { code: "quota_exceeded" } });
    assert.equal(await operation, "failed");
    assert.deepEqual(h.imapRequests, [{ mailboxId: "a", limit: undefined }, { mailboxId: "a", limit: 5 }]);
    assert.equal(h.readiness.a, "not_updated");
    assert.equal(h.mailboxStore.a.Inbox[0].id, "cached");
    assert.equal(h.publications.length, 0);
    assert.match(h.errors.a, /showing messages from the last successful sync/);
    assert.equal(h.lastRefreshDiagnosticRef.current.a.cacheRestored, 1);
  });

  await test("unexpected Inbox rejection clears operation activity without marking cached data updated", async () => {
    const h = createHarness();
    const operation = h.refresh("a", { reason: "mailbox_open" });
    h.inbox.a.reject(new Error("Provider request rejected"));
    await assert.rejects(operation, /Provider request rejected/);
    assert.equal(h.readiness.a, "not_updated");
    assert.equal(h.syncingMailboxIdsRef.current.has("a"), false);
    assert.equal(h.activityRevision, 2);
    assert.equal(h.publications.length, 0);
    assert.equal(h.events.includes("trash:a"), false);
  });

  await test("all existing provider lock boundaries publish presentation activity", () => {
    const refreshSources = [
      "refreshMailboxById", "refreshProviderArchiveById", "refreshProviderTrashById",
      "performProviderImapTrashRefreshById", "reconcileProviderTrashById",
    ].map(declaration).join("\n");
    for (const lock of ["syncingMailboxIdsRef", "providerArchiveFetchMailboxIdsRef", "providerTrashFetchMailboxIdsRef"]) {
      const mutations = [...refreshSources.matchAll(new RegExp(`${lock}\\.current\\.(add|delete)\\(mailboxId\\);`, "g"))];
      assert.ok(mutations.length >= 2, `${lock} has observed acquire/release boundaries`);
      for (const mutation of mutations) {
        const tail = refreshSources.slice(mutation.index! + mutation[0].length);
        // The Inbox finally block performs its existing scalar cleanup and
        // reconciliation drain before requesting the presentation rerender.
        assert.match(tail, /^(?:\s|setSyncingMailboxId\([^\n]+\);|drainGmailArchiveReconciliation\(mailboxId\);)*publishMailboxSyncActivity\(\);/,
          `${lock}.${mutation[1]} must request a React presentation update`);
      }
    }
  });

  console.log(`WorkspaceShell Inbox readiness tests passed (${testCount} tests)`);
}
void main().catch((error) => { console.error(error); process.exitCode = 1; });
