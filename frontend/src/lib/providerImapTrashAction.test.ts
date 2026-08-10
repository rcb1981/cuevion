import assert from "node:assert/strict";
import {
  applyConfirmedImapTrashSourceRemoval,
  buildProviderImapTrashInFlightKey,
  buildProviderImapTrashMutationTarget,
  createMailboxRefreshTailSequencer,
  createProviderImapTrashCoordinator,
  fetchProviderImapTrash,
  hasPendingProviderImapTrashForMailbox,
  isProviderImapTrashMutationSuccessResponse,
  mutateProviderImapTrashMessage,
  replaceCustomImapTrashFolderReadback,
  resolveExactCustomImapTrashMutationTarget,
  validateProviderImapTrashSnapshot,
  type ProviderImapTrashConfirmedMutation,
  type ProviderImapTrashMutationRequest,
  type ProviderImapTrashSnapshot,
} from "./providerImapTrashAction";

type Test = { name: string; run: () => void | Promise<void> };
const tests: Test[] = [];
function test(name: string, run: Test["run"]) {
  tests.push({ name, run });
}

const request: ProviderImapTrashMutationRequest = {
  mailboxId: "mailbox-1",
  action: "trash",
  sourceFolder: "INBOX",
  imapUid: "42",
  uidValidity: "1234",
};

const confirmed: ProviderImapTrashConfirmedMutation = {
  ok: true,
  status: "ok",
  action: "trash",
  provider: "custom_imap",
  mailboxId: "mailbox-1",
  sourceFolder: "INBOX",
  sourceImapUid: "42",
  sourceUidValidity: "1234",
  targetFolder: "Deleted Items",
  targetImapUid: "900",
  targetUidValidity: "5678",
  confirmation: "source_removed_target_bound",
};

function target() {
  const value = buildProviderImapTrashMutationTarget({
    provider: "custom_imap",
    mailboxId: request.mailboxId,
    sourceFolder: request.sourceFolder,
    imapUid: request.imapUid,
    uidValidity: request.uidValidity,
  });
  assert.equal(value.ok, true);
  if (!value.ok) throw new Error("expected target");
  return value;
}

function message(imapUid: string, extras: Record<string, unknown> = {}) {
  return {
    id: `message-${imapUid}`,
    serverMailboxId: "mailbox-1",
    providerFolder: "Deleted Items",
    imapUid,
    uidValidity: "5678",
    threadId: `thread-${imapUid}`,
    rfcMessageId: `<${imapUid}@example.test>`,
    sender: "Sender",
    subject: "Subject",
    snippet: "Snippet",
    from: "sender@example.test",
    to: "recipient@example.test",
    timestamp: "2026-08-10T10:00:00.000Z",
    createdAt: "2026-08-10T10:00:00.000Z",
    body: ["Body"],
    unread: false,
    flagged: false,
    ...extras,
  };
}

function snapshot(): ProviderImapTrashSnapshot {
  return {
    serverMailboxId: "mailbox-1",
    providerFolder: "Deleted Items",
    uidValidity: "5678",
    imapUidSet: ["4", "7"],
    messages: [message("7"), message("4")],
  };
}

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function withFetch(
  fetcher: typeof fetch,
  run: () => Promise<void>,
) {
  const original = globalThis.fetch;
  globalThis.fetch = fetcher;
  try {
    await run();
  } finally {
    globalThis.fetch = original;
  }
}

function deferred<Value>() {
  let resolve!: (value: Value | PromiseLike<Value>) => void;
  const promise = new Promise<Value>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

test("resolves one live custom-IMAP Inbox identity without leaking config", () => {
  const source = {
    id: "ui-42",
    serverMailboxId: "mailbox-1",
    providerFolder: "INBOX",
    imapUid: "42",
    uidValidity: "1234",
    sender: "Sender",
  };
  const resolution = resolveExactCustomImapTrashMutationTarget({
    isLiveMailbox: true,
    selectedMessageIds: ["ui-42"],
    sourceFolder: "Inbox",
    sourceManagedMailbox: {
      id: "mailbox-1",
      provider: "custom_imap",
      connected: true,
      connectionStatus: "connected",
      customImap: {
        host: "imap.example.test",
        port: "993",
        username: "private@example.test",
      },
    },
    sourceMessages: [source],
  });
  assert.ok(resolution);
  assert.deepEqual(resolution.target.request, request);
  assert.deepEqual(Object.keys(resolution.target.request).sort(), [
    "action",
    "imapUid",
    "mailboxId",
    "sourceFolder",
    "uidValidity",
  ]);
});

test("resolver rejects non-live, mixed-provider, duplicate, secret and stale rows", () => {
  const base = {
    id: "ui-42",
    serverMailboxId: "mailbox-1",
    providerFolder: "INBOX",
    imapUid: "42",
    uidValidity: "1234",
  };
  const managed = {
    id: "mailbox-1",
    provider: "custom_imap",
    connected: true,
    connectionStatus: "connected",
  };
  const resolve = (sourceMessages: Array<Record<string, unknown>>, overrides = {}) =>
    resolveExactCustomImapTrashMutationTarget({
      isLiveMailbox: true,
      selectedMessageIds: ["ui-42"],
      sourceFolder: "Inbox",
      sourceManagedMailbox: managed,
      sourceMessages: sourceMessages as Array<typeof base>,
      ...overrides,
    });

  assert.equal(resolve([base], { isLiveMailbox: false }), null);
  assert.equal(resolve([base, { ...base }]), null);
  assert.equal(resolve([{ ...base, providerMessageId: "gmail-id" }]), null);
  assert.equal(resolve([{ ...base, labelIds: ["TRASH"] }]), null);
  assert.equal(resolve([{ ...base, authToken: "do-not-leak" }]), null);
  assert.equal(resolve([{ ...base, providerFolder: "Inbox" }]), null);
  assert.equal(resolve([{ ...base, uidValidity: "01234" }]), null);
  assert.equal(resolve([{ ...base, imapUid: "0" }]), null);
});

test("single-flight identity is exactly mailbox, UIDVALIDITY and source UID", () => {
  const key = buildProviderImapTrashInFlightKey({
    mailboxId: "mailbox-1",
    sourceUidValidity: "1234",
    sourceImapUid: "42",
  });
  assert.deepEqual(JSON.parse(key), [
    "trash",
    "custom_imap",
    "mailbox-1",
    "1234",
    "42",
  ]);
  assert.equal(
    hasPendingProviderImapTrashForMailbox(new Set([key]), "mailbox-1"),
    true,
  );
  assert.equal(
    hasPendingProviderImapTrashForMailbox(new Set([key]), "mailbox-2"),
    false,
  );
});

test("mutation client sends the exact custom-IMAP request and accepts only exact success", async () => {
  let invalidRequestFetchCalled = false;
  await withFetch(
    (async () => {
      invalidRequestFetchCalled = true;
      return jsonResponse(confirmed);
    }) as typeof fetch,
    async () => {
      const invalid = await mutateProviderImapTrashMessage({
        ...request,
        imapUid: undefined,
      } as unknown as ProviderImapTrashMutationRequest);
      assert.equal(invalid.ok, false);
      assert.equal(invalid.error.code, "invalid_trash_request");
    },
  );
  assert.equal(invalidRequestFetchCalled, false);

  await withFetch(
    (async (url, init) => {
      assert.equal(url, "/api/inboxes/message-action");
      assert.equal(init?.method, "POST");
      assert.equal(init?.credentials, "include");
      assert.equal(init?.cache, "no-store");
      assert.deepEqual(JSON.parse(String(init?.body)), request);
      return jsonResponse(confirmed);
    }) as typeof fetch,
    async () => {
      assert.deepEqual(await mutateProviderImapTrashMessage(request), confirmed);
    },
  );

  const { targetImapUid: _missingTargetImapUid, ...missingTargetImapUid } =
    confirmed;
  for (const unsafe of [
    { ...confirmed, provider: "gmail" },
    { ...confirmed, providerMessageId: "gmail-id" },
    { ...confirmed, providerThreadId: "gmail-thread" },
    { ...confirmed, password: "secret" },
    { ...confirmed, mailboxId: "mailbox-2" },
    { ...confirmed, sourceImapUid: "43" },
    { ...confirmed, sourceUidValidity: "1235" },
    { ...confirmed, targetFolder: "INBOX" },
    { ...confirmed, targetFolder: "Deleted\nItems" },
    { ...confirmed, targetFolder: "Deleted\u0085Items" },
    { ...confirmed, targetFolder: "Deleted\ud800Items" },
    { ...confirmed, targetImapUid: "0" },
    { ...confirmed, targetImapUid: " 900" },
    { ...confirmed, targetUidValidity: "0" },
    missingTargetImapUid,
  ]) {
    assert.equal(isProviderImapTrashMutationSuccessResponse(unsafe, request), false);
  }
  assert.equal(
    isProviderImapTrashMutationSuccessResponse(
      { ...confirmed, targetFolder: "Verwijderd 📨" },
      request,
    ),
    true,
  );
});

test("delayed mutation is non-optimistic and confirmed removal precedes delayed Trash refresh", async () => {
  const mutation = deferred<unknown>();
  const refreshStarted = deferred<void>();
  const refresh = deferred<boolean>();
  const chosen = {
    id: "chosen",
    serverMailboxId: "mailbox-1",
    providerFolder: "INBOX",
    imapUid: "42",
    uidValidity: "1234",
    subject: "Same",
    sender: "same@example.test",
    timestamp: "same-time",
    rfcMessageId: "<same@example.test>",
  };
  const sibling = { ...chosen, id: "sibling", imapUid: "43" };
  const oldTrash = { id: "old-trash" };
  const oldArchive = { id: "old-archive" };
  let collections = {
    Inbox: [chosen, sibling],
    Trash: [oldTrash],
    Archive: [oldArchive],
  };
  let selectedMessageId = chosen.id;
  let mutationCount = 0;
  const pending = new Set<string>();
  const coordinator = createProviderImapTrashCoordinator({
    pendingKeys: pending,
    mutate: async () => {
      mutationCount += 1;
      return mutation.promise;
    },
    applyConfirmedSourceRemoval: (response) => {
      const result = applyConfirmedImapTrashSourceRemoval(
        collections,
        response,
      );
      if (result.applied) {
        collections = result.state;
        selectedMessageId = sibling.id;
      }
      return result.applied;
    },
    refreshProviderTrashReadOnly: async () => {
      refreshStarted.resolve();
      return refresh.promise;
    },
  });

  const action = coordinator.trash(target());
  await Promise.resolve();
  assert.deepEqual(collections.Inbox, [chosen, sibling]);
  assert.deepEqual(collections.Trash, [oldTrash]);
  assert.deepEqual(collections.Archive, [oldArchive]);
  assert.equal(selectedMessageId, chosen.id);
  assert.equal(pending.size, 1);

  mutation.resolve(confirmed);
  await refreshStarted.promise;
  assert.deepEqual(collections.Inbox, [sibling]);
  assert.deepEqual(collections.Trash, [oldTrash]);
  assert.deepEqual(collections.Archive, [oldArchive]);
  assert.equal(selectedMessageId, sibling.id);
  assert.equal(mutationCount, 1);
  assert.equal(pending.size, 0);

  refresh.resolve(true);
  const result = await action;
  assert.equal(result.classification, "success");
  assert.equal(result.refreshed, true);
});

test("mutation client treats empty, non-JSON and malformed 2xx atomically as uncertain", async () => {
  for (const response of [
    new Response("", { status: 200 }),
    new Response("not-json", { status: 200 }),
    jsonResponse({ ok: true, provider: "custom_imap" }),
  ]) {
    await withFetch(
      (async () => response.clone()) as typeof fetch,
      async () => {
        const result = await mutateProviderImapTrashMessage(request);
        assert.equal(result.ok, false);
        assert.equal("status" in result ? result.status : null, "mutation_unconfirmed");
      },
    );
  }
});

test("mutation client bounds a chunked response while streaming", async () => {
  let cancelled = false;
  let chunksSent = 0;
  const oversizedResponse = new Response(
    new ReadableStream<Uint8Array>({
      pull(controller) {
        chunksSent += 1;
        controller.enqueue(new Uint8Array(32 * 1024).fill(32));
      },
      cancel() {
        cancelled = true;
      },
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );

  await withFetch(
    (async () => oversizedResponse) as typeof fetch,
    async () => {
      const result = await mutateProviderImapTrashMessage(request);
      assert.equal(result.ok, false);
      assert.equal("status" in result ? result.status : null, "mutation_unconfirmed");
    },
  );

  assert.equal(cancelled, true);
  assert.ok(chunksSent <= 4, "stream must be cancelled near the 64 KiB limit");
});

test("coordinator applies confirmed removal before release and then refreshes once", async () => {
  const pending = new Set<string>();
  const events: string[] = [];
  const coordinator = createProviderImapTrashCoordinator({
    pendingKeys: pending,
    mutate: async () => {
      events.push("mutate");
      return confirmed;
    },
    onPendingKeysChange: () => events.push(`pending:${pending.size}`),
    applyConfirmedSourceRemoval: () => {
      assert.equal(pending.size, 1);
      events.push("apply");
      return true;
    },
    refreshProviderTrashReadOnly: async (refresh) => {
      assert.equal(pending.size, 0);
      assert.equal(refresh.cause, "confirmed_success");
      assert.deepEqual(refresh.confirmedTarget, {
        targetFolder: "Deleted Items",
        targetImapUid: "900",
        targetUidValidity: "5678",
      });
      events.push("refresh");
      return true;
    },
  });
  const result = await coordinator.trash(target());
  assert.equal(result.classification, "success");
  assert.deepEqual(events, ["pending:1", "mutate", "apply", "pending:0", "refresh"]);
});

test("uncertain mutation stays single-flight until deferred read-only refresh settles", async () => {
  const pending = new Set<string>();
  let mutateCount = 0;
  let startRefresh!: () => void;
  const refreshStarted = new Promise<void>((resolve) => { startRefresh = resolve; });
  let finishRefresh!: () => void;
  const refreshGate = new Promise<void>((resolve) => { finishRefresh = resolve; });
  const coordinator = createProviderImapTrashCoordinator({
    pendingKeys: pending,
    mutate: async () => {
      mutateCount += 1;
      throw new Error("network response lost");
    },
    applyConfirmedSourceRemoval: () => {
      assert.fail("unconfirmed mutations must not remove Inbox state");
    },
    refreshProviderTrashReadOnly: async ({ cause }) => {
      assert.equal(cause, "mutation_unconfirmed");
      startRefresh();
      await refreshGate;
      return true;
    },
  });

  const first = coordinator.trash(target());
  await refreshStarted;
  assert.equal(pending.size, 1);
  const second = await coordinator.trash(target());
  assert.equal(second.classification, "blocked");
  if (second.classification === "blocked") {
    assert.equal(second.reason, "already_pending");
  }
  assert.equal(mutateCount, 1);
  finishRefresh();
  const settled = await first;
  assert.equal(settled.classification, "uncertain");
  assert.equal(pending.size, 0);
});

test("all capability failures are classified without source mutation or refresh", async () => {
  for (const code of [
    "trash_folder_unavailable",
    "trash_folder_ambiguous",
    "trash_move_unsupported",
    "trash_uidplus_unsupported",
  ]) {
    let callbacks = 0;
    let mutationCount = 0;
    const result = await createProviderImapTrashCoordinator({
      mutate: async () => {
        mutationCount += 1;
        return {
          ok: false,
          error: { code, message: "details" },
        };
      },
      applyConfirmedSourceRemoval: () => { callbacks += 1; return true; },
      refreshProviderTrashReadOnly: () => { callbacks += 1; },
    }).trash(target());
    assert.equal(result.classification, "capability_unavailable");
    assert.equal(callbacks, 0);
    assert.equal(mutationCount, 1);
    if (result.classification === "capability_unavailable") {
      assert.equal(result.response.error.message.includes("details"), false);
    }
  }
});

test("definitive mutation failure neither changes folders nor retries", async () => {
  const Inbox = [{ id: "inbox" }];
  const Trash = [{ id: "trash" }];
  let mutationCount = 0;
  let callbacks = 0;
  const result = await createProviderImapTrashCoordinator({
    mutate: async () => {
      mutationCount += 1;
      return {
        ok: false,
        error: { code: "trash_move_failed", message: "provider rejected" },
      };
    },
    applyConfirmedSourceRemoval: () => { callbacks += 1; return true; },
    refreshProviderTrashReadOnly: () => { callbacks += 1; },
  }).trash(target());
  assert.equal(result.classification, "ordinary_failure");
  assert.equal(mutationCount, 1);
  assert.equal(callbacks, 0);
  assert.deepEqual({ Inbox, Trash }, { Inbox: [{ id: "inbox" }], Trash: [{ id: "trash" }] });
});

test("confirmed read-only refresh failure never restores Inbox or retries mutation", async () => {
  let mutationCount = 0;
  let collections = {
    Inbox: [{
      id: "exact",
      serverMailboxId: "mailbox-1",
      providerFolder: "INBOX",
      imapUid: "42",
      uidValidity: "1234",
    }],
    Trash: [] as Array<{ id: string }>,
  };
  const result = await createProviderImapTrashCoordinator({
    mutate: async () => {
      mutationCount += 1;
      return confirmed;
    },
    applyConfirmedSourceRemoval: (response) => {
      const removal = applyConfirmedImapTrashSourceRemoval(
        collections,
        response,
      );
      if (removal.applied) collections = removal.state;
      return removal.applied;
    },
    refreshProviderTrashReadOnly: async () => false,
  }).trash(target());

  assert.equal(result.classification, "success");
  assert.equal(result.refreshed, false);
  assert.equal(mutationCount, 1);
  assert.deepEqual(collections.Inbox, []);
  assert.deepEqual(collections.Trash, []);
});

test("confirmed Trash readback atomically publishes the bound target exactly once", async () => {
  const source = {
    id: "source",
    serverMailboxId: "mailbox-1",
    providerFolder: "INBOX",
    imapUid: "42",
    uidValidity: "1234",
  };
  const sibling = { ...source, id: "sibling", imapUid: "43" };
  const Archive = [{ id: "archive" }];
  let mutationCount = 0;
  let collections = {
    Inbox: [source, sibling],
    Trash: [{ id: "stale-local-trash" }] as Array<Record<string, unknown>>,
    Archive,
  };
  const targetSnapshot: ProviderImapTrashSnapshot = {
    serverMailboxId: "mailbox-1",
    providerFolder: confirmed.targetFolder,
    uidValidity: confirmed.targetUidValidity,
    imapUidSet: [confirmed.targetImapUid],
    messages: [message(confirmed.targetImapUid)],
  };

  const result = await createProviderImapTrashCoordinator({
    mutate: async () => {
      mutationCount += 1;
      return confirmed;
    },
    applyConfirmedSourceRemoval: (response) => {
      const removal = applyConfirmedImapTrashSourceRemoval(
        collections,
        response,
      );
      if (removal.applied) collections = removal.state;
      return removal.applied;
    },
    refreshProviderTrashReadOnly: async () => {
      const readback = replaceCustomImapTrashFolderReadback(
        collections,
        targetSnapshot,
      );
      if (readback.applied) collections = readback.state;
      return readback.applied;
    },
  }).trash(target());

  assert.equal(result.classification, "success");
  assert.equal(mutationCount, 1);
  assert.deepEqual(collections.Inbox, [sibling]);
  assert.strictEqual(collections.Archive, Archive);
  assert.equal(collections.Trash.length, 1);
  assert.equal(collections.Trash[0].imapUid, confirmed.targetImapUid);
  assert.equal(collections.Trash[0].uidValidity, confirmed.targetUidValidity);
  assert.equal(
    collections.Trash.filter(
      (row) =>
        row.imapUid === confirmed.targetImapUid &&
        row.uidValidity === confirmed.targetUidValidity,
    ).length,
    1,
  );
});

test("confirmed removal requires exactly one full source identity and preserves siblings", () => {
  const exact = {
    id: "exact",
    serverMailboxId: "mailbox-1",
    providerFolder: "INBOX",
    imapUid: "42",
    uidValidity: "1234",
  };
  const sibling = { ...exact, id: "sibling", imapUid: "43" };
  const archive = [{ id: "archive" }];
  const uidCollisionInTrash = {
    id: "trash-uid-collision",
    serverMailboxId: "mailbox-1",
    providerFolder: "Deleted Items",
    imapUid: "42",
    uidValidity: "5678",
  };
  const current = {
    Inbox: [exact, sibling],
    Archive: archive,
    Trash: [uidCollisionInTrash],
  };
  const removed = applyConfirmedImapTrashSourceRemoval(current, confirmed);
  assert.equal(removed.applied, true);
  assert.deepEqual(removed.state.Inbox, [sibling]);
  assert.strictEqual(removed.state.Archive, archive);
  assert.strictEqual(removed.state.Trash, current.Trash);

  const duplicate = { ...current, Inbox: [exact, { ...exact }] };
  const rejected = applyConfirmedImapTrashSourceRemoval(duplicate, confirmed);
  assert.equal(rejected.applied, false);
  assert.strictEqual(rejected.state, duplicate);

  for (const unsafe of [
    {
      ...current,
      Inbox: [{ ...exact, uidValidity: "9999" }, sibling],
    },
    {
      ...current,
      Inbox: [{ ...exact, serverMailboxId: "mailbox-2" }, sibling],
    },
    {
      ...current,
      Inbox: [sibling],
      Archive: [exact],
    },
    {
      ...current,
      Inbox: [sibling],
      Trash: [exact],
    },
  ]) {
    const failClosed = applyConfirmedImapTrashSourceRemoval(unsafe, confirmed);
    assert.equal(failClosed.applied, false);
    assert.strictEqual(failClosed.state, unsafe);
  }
});

test("strict Trash snapshot binds folder, UIDVALIDITY, complete UID order and provider rows", () => {
  const valid = snapshot();
  assert.equal(validateProviderImapTrashSnapshot(valid, "mailbox-1"), true);
  for (const invalid of [
    { ...valid, serverMailboxId: "mailbox-2" },
    { ...valid, providerFolder: "INBOX" },
    { ...valid, imapUidSet: ["7", "4"] },
    { ...valid, imapUidSet: ["4", "4"] },
    { ...valid, messages: [message("7")] },
    { ...valid, messages: [message("4"), message("7")] },
    { ...valid, messages: [message("7"), message("4", { uidValidity: "9" })] },
    { ...valid, messages: [message("7", { providerMessageId: "gmail" }), message("4")] },
    { ...valid, messages: [message("7", { authToken: "secret" }), message("4")] },
  ]) {
    assert.equal(validateProviderImapTrashSnapshot(invalid, "mailbox-1"), false);
  }
});

test("fetch client accepts the exact custom-IMAP envelope and exact request only", async () => {
  const payload = {
    ok: true,
    status: "ok",
    provider: "custom_imap",
    mailboxId: "mailbox-1",
    folder: snapshot(),
  };
  await withFetch(
    (async (url, init) => {
      assert.equal(url, "/api/inboxes/fetch-trash");
      assert.equal(init?.method, "POST");
      assert.deepEqual(JSON.parse(String(init?.body)), { mailboxId: "mailbox-1" });
      return jsonResponse(payload);
    }) as typeof fetch,
    async () => {
      assert.deepEqual(await fetchProviderImapTrash({ mailboxId: "mailbox-1" }), payload);
    },
  );
});

test("fetch rejects empty, non-JSON and malformed snapshots atomically", async () => {
  const malformed = {
    ok: true,
    status: "ok",
    provider: "custom_imap",
    mailboxId: "mailbox-1",
    folder: { ...snapshot(), messages: [message("7")] },
  };
  for (const response of [
    new Response("", { status: 200 }),
    new Response("not-json", { status: 200 }),
    jsonResponse(malformed),
  ]) {
    await withFetch(
      (async () => response.clone()) as typeof fetch,
      async () => {
        const result = await fetchProviderImapTrash({ mailboxId: "mailbox-1" });
        assert.deepEqual(result, {
          ok: false,
          error: {
            code: "trash_snapshot_invalid",
            message: "Could not refresh this Trash folder safely.",
          },
        });
        assert.equal("folder" in result, false);
      },
    );
  }
});

test("Trash readback replacement is atomic and does not mutate Inbox or Archive", () => {
  const Inbox = [{ id: "inbox" }];
  const Archive = [{ id: "archive" }];
  const current = { Inbox, Archive, Trash: [{ id: "old" }] };
  const replaced = replaceCustomImapTrashFolderReadback(current, snapshot());
  assert.equal(replaced.applied, true);
  assert.strictEqual(replaced.state.Inbox, Inbox);
  assert.strictEqual(replaced.state.Archive, Archive);
  assert.deepEqual(replaced.state.Trash, snapshot().messages);

  const invalid = { ...snapshot(), messages: [message("7")] };
  const rejected = replaceCustomImapTrashFolderReadback(
    current,
    invalid as ProviderImapTrashSnapshot,
  );
  assert.equal(rejected.applied, false);
  assert.strictEqual(rejected.state, current);
});

test("mailbox refresh tails serialize a fresh readback while ordinary overlap preserves the active readback", async () => {
  type Result = "applied" | "skipped";
  const sequencer = createMailboxRefreshTailSequencer<Result>();
  let publicationEpoch = 1;
  const starts: Array<{ label: string; publicationEpoch: number }> = [];
  const firstCompletion = deferred<void>();
  const secondCompletion = deferred<void>();
  const perform =
    (label: string, completion: Promise<void>) => async (): Promise<Result> => {
      const epochAtStart = publicationEpoch;
      starts.push({ label, publicationEpoch: epochAtStart });
      await completion;
      return epochAtStart === publicationEpoch ? "applied" : "skipped";
    };

  const firstReadback = sequencer.run("mailbox-success-overlap", {
    queueAfterActive: true,
    perform: perform("A", firstCompletion.promise),
  });
  await Promise.resolve();
  assert.deepEqual(starts, [{ label: "A", publicationEpoch: 1 }]);
  assert.equal(
    await sequencer.run("mailbox-success-overlap", {
      queueAfterActive: false,
      perform: async () => {
        throw new Error("folder-open must not run while A is active");
      },
    }),
    "skipped",
  );

  publicationEpoch = 2;
  const secondReadback = sequencer.run("mailbox-success-overlap", {
    queueAfterActive: true,
    perform: perform("B", secondCompletion.promise),
  });
  await Promise.resolve();
  assert.deepEqual(
    starts,
    [{ label: "A", publicationEpoch: 1 }],
    "B must wait for A's stale readback to settle",
  );

  firstCompletion.resolve();
  assert.equal(await firstReadback, "skipped");
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(starts, [
    { label: "A", publicationEpoch: 1 },
    { label: "B", publicationEpoch: 2 },
  ]);
  secondCompletion.resolve();
  assert.equal(await secondReadback, "applied");

  const ordinaryCompletion = deferred<void>();
  const ordinaryStarts: number[] = [];
  publicationEpoch = 3;
  const readbackBeforeOrdinaryFailure = sequencer.run(
    "mailbox-ordinary-overlap",
    {
      queueAfterActive: true,
      perform: async () => {
        const epochAtStart = publicationEpoch;
        ordinaryStarts.push(epochAtStart);
        await ordinaryCompletion.promise;
        return epochAtStart === publicationEpoch ? "applied" : "skipped";
      },
    },
  );
  await Promise.resolve();
  // A definitive/capability outcome neither advances the publication epoch nor
  // queues a fallback readback, so the already-active provider readback stays valid.
  ordinaryCompletion.resolve();
  assert.equal(await readbackBeforeOrdinaryFailure, "applied");
  assert.deepEqual(ordinaryStarts, [3]);

  const resetSequencer = createMailboxRefreshTailSequencer<Result>();
  const activeBeforeReset = deferred<Result>();
  let queuedAfterResetPerformCount = 0;
  const activeReadback = resetSequencer.run("mailbox-reset", {
    queueAfterActive: true,
    perform: () => activeBeforeReset.promise,
  });
  await Promise.resolve();
  const queuedBeforeReset = resetSequencer.run("mailbox-reset", {
    queueAfterActive: true,
    perform: async () => {
      queuedAfterResetPerformCount += 1;
      return "applied";
    },
  });
  resetSequencer.reset("mailbox-reset");
  activeBeforeReset.resolve("skipped");
  assert.equal(await activeReadback, "skipped");
  assert.equal(await queuedBeforeReset, "skipped");
  assert.equal(queuedAfterResetPerformCount, 0);
});

async function run() {
  let failed = 0;
  for (const current of tests) {
    try {
      await current.run();
      console.log(`✓ ${current.name}`);
    } catch (error) {
      failed += 1;
      console.error(`✗ ${current.name}`);
      console.error(error);
    }
  }
  if (failed > 0) process.exitCode = 1;
}

void run();
