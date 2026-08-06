import assert from "node:assert/strict";
import {
  applyGmailProviderTrashFolderReadback,
  createProviderTrashCoordinator,
  hasPendingProviderTrashForMailbox,
  replaceGmailProviderInboxAndTrashReadback,
  resolveExactGmailTrashMutationTarget,
  type ProviderTrashMutation,
  type ProviderTrashMutationRequest,
  type ProviderTrashMutationResponse,
} from "./providerTrashAction";

type Test = {
  name: string;
  run: () => void | Promise<void>;
};

type TestMessage = {
  id: string;
  serverMailboxId?: string | null;
  providerFolder?: string | null;
  providerMessageId?: string | null;
  providerThreadId?: string | null;
  threadId?: string | null;
  rfcMessageId?: string | null;
  imapUid?: string | null;
};

const tests: Test[] = [];

function test(name: string, run: Test["run"]) {
  tests.push({ name, run });
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function waitFor(predicate: () => boolean) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (predicate()) return;
    await Promise.resolve();
  }
  assert.fail("timed out waiting for the expected asynchronous state");
}

function managedMailbox(
  overrides: Record<string, unknown> = {},
) {
  return {
    id: "server-mailbox-1",
    provider: "google",
    connected: true,
    connectionStatus: "connected",
    ...overrides,
  };
}

function sourceMessage(
  overrides: Partial<TestMessage> = {},
): TestMessage {
  return {
    id: "ui-message-1",
    serverMailboxId: "server-mailbox-1",
    providerFolder: "Inbox",
    providerMessageId: "18f-provider-message-1",
    providerThreadId: "18f-thread-with-two-messages",
    threadId: "ui-thread-with-two-messages",
    rfcMessageId: "message-1@example.test",
    imapUid: "42",
    ...overrides,
  };
}

type ResolveOverrides = {
  isLiveMailbox?: unknown;
  selectedMessageIds?: readonly string[];
  sourceFolder?: unknown;
  sourceManagedMailbox?: ReturnType<typeof managedMailbox> | null;
  sourceMessages?: readonly TestMessage[];
};

function resolveTarget(overrides: ResolveOverrides = {}) {
  return resolveExactGmailTrashMutationTarget({
    isLiveMailbox: true,
    selectedMessageIds: ["ui-message-1"],
    sourceFolder: "Inbox",
    sourceManagedMailbox: managedMailbox(),
    sourceMessages: [sourceMessage()],
    ...overrides,
  });
}

function exactTarget() {
  const resolution = resolveTarget();
  assert.ok(resolution, "expected an exact Gmail Trash target");
  return resolution.target;
}

function successResponse(
  request: ProviderTrashMutationRequest,
): ProviderTrashMutationResponse {
  return {
    ok: true,
    action: "trash",
    provider: "gmail",
    mailboxId: request.mailboxId,
    providerMessageId: request.providerMessageId,
    sourceFolder: "INBOX",
    destinationFolder: "TRASH",
    readback: {
      inSource: false,
      inTrash: true,
    },
  };
}

function uncertainResponse(
  request: ProviderTrashMutationRequest,
): ProviderTrashMutationResponse {
  return {
    ok: false,
    status: "mutation_unconfirmed",
    action: "trash",
    provider: "gmail",
    mailboxId: request.mailboxId,
    providerMessageId: request.providerMessageId,
    sourceFolder: "INBOX",
    destinationFolder: "TRASH",
    error: {
      code: "trash_mutation_unconfirmed",
      message:
        "Trash may have completed, but provider confirmation was not definitive.",
    },
  };
}

test("one selected Gmail message in a two-message thread resolves only its concrete provider identity", () => {
  const selected = sourceMessage();
  const sibling = sourceMessage({
    id: "ui-message-2",
    providerMessageId: "18f-provider-message-2",
    rfcMessageId: "message-2@example.test",
  });
  const resolution = resolveTarget({ sourceMessages: [selected, sibling] });

  assert.ok(resolution);
  assert.strictEqual(resolution.sourceMessage, selected);
  assert.deepEqual(resolution.target.request, {
    mailboxId: "server-mailbox-1",
    action: "trash",
    providerMessageId: "18f-provider-message-1",
    sourceFolder: "INBOX",
  });
  assert.deepEqual(Object.keys(resolution.target.request).sort(), [
    "action",
    "mailboxId",
    "providerMessageId",
    "sourceFolder",
  ]);
  assert.deepEqual(JSON.parse(resolution.target.inFlightKey), [
    "trash",
    "google",
    "server-mailbox-1",
    "INBOX",
    "18f-provider-message-1",
  ]);
  assert.equal(resolution.target.inFlightKey.includes(selected.id), false);
  assert.equal(
    resolution.target.inFlightKey.includes(selected.providerThreadId ?? ""),
    false,
  );
  assert.equal(
    resolution.target.inFlightKey.includes(selected.rfcMessageId ?? ""),
    false,
  );
  assert.equal(
    resolution.target.inFlightKey.includes(sibling.providerMessageId ?? ""),
    false,
  );
});

test("non-live, disconnected, unmanaged, non-Gmail, non-Inbox, and multi-selection contexts fail closed", () => {
  const invalidContexts: Array<[string, ResolveOverrides]> = [
    ["not live", { isLiveMailbox: false }],
    ["missing managed mailbox", { sourceManagedMailbox: null }],
    [
      "disconnected",
      { sourceManagedMailbox: managedMailbox({ connected: false }) },
    ],
    [
      "not connection-ready",
      {
        sourceManagedMailbox: managedMailbox({
          connectionStatus: "reconnect_required",
        }),
      },
    ],
    [
      "custom IMAP",
      { sourceManagedMailbox: managedMailbox({ provider: "custom_imap" }) },
    ],
    ["Archive UI folder", { sourceFolder: "Archive" }],
    ["provider INBOX spelling", { sourceMessages: [sourceMessage({ providerFolder: "INBOX" })] }],
    ["no selection", { selectedMessageIds: [] }],
    [
      "multi-selection",
      { selectedMessageIds: ["ui-message-1", "ui-message-2"] },
    ],
    ["unknown UI selection", { selectedMessageIds: ["ui-message-missing"] }],
    [
      "duplicate UI identity",
      { sourceMessages: [sourceMessage(), sourceMessage()] },
    ],
    [
      "mailbox mismatch",
      {
        sourceMessages: [
          sourceMessage({ serverMailboxId: "different-server-mailbox" }),
        ],
      },
    ],
  ];

  for (const [name, overrides] of invalidContexts) {
    assert.equal(resolveTarget(overrides), null, name);
  }
});

test("UI, RFC, thread, and IMAP identities never substitute for a concrete Gmail provider message id", () => {
  for (const providerMessageId of [
    undefined,
    null,
    "",
    " 18f-provider-message-1 ",
    "thread-provider-id",
    "rfc-message@example.test",
    "<rfc-message@example.test>",
    "imap-uid-42",
    "message\nidentity",
    "x".repeat(257),
  ]) {
    const message = sourceMessage({ providerMessageId });
    assert.equal(
      resolveTarget({ sourceMessages: [message] }),
      null,
      JSON.stringify(providerMessageId),
    );
  }

  const fallbackOnly = sourceMessage({ providerMessageId: undefined });
  fallbackOnly.id = "ui-fallback-must-not-be-used";
  fallbackOnly.providerThreadId = "provider-thread-fallback";
  fallbackOnly.threadId = "ui-thread-fallback";
  fallbackOnly.rfcMessageId = "rfc-fallback@example.test";
  fallbackOnly.imapUid = "900";
  assert.equal(
    resolveTarget({
      selectedMessageIds: [fallbackOnly.id],
      sourceMessages: [fallbackOnly],
    }),
    null,
  );
});

test("pending execution performs no optimistic apply, blocks the same identity, reconciles once, and unlocks finally", async () => {
  const target = exactTarget();
  const mutation = deferred<ProviderTrashMutationResponse>();
  const reconciliation = deferred<void>();
  const pendingKeys = new Set<string>();
  const mailboxState = {
    Inbox: [{ id: "ui-message-1" }],
    Trash: [{ id: "existing-trash-message" }],
  };
  const originalState = mailboxState;
  let mutationCalls = 0;
  const reconciliations: unknown[] = [];

  const mutationHandler: ProviderTrashMutation = (request) => {
    mutationCalls += 1;
    assert.deepEqual(request, target.request);
    return mutation.promise;
  };
  const coordinator = createProviderTrashCoordinator({
    pendingKeys,
    mutate: mutationHandler,
    reconcileReadOnly: async (request) => {
      reconciliations.push(request);
      await reconciliation.promise;
    },
  });

  const first = coordinator.trash(target);
  assert.equal(mutationCalls, 1);
  assert.strictEqual(mailboxState, originalState);
  assert.deepEqual(mailboxState.Inbox, [{ id: "ui-message-1" }]);
  assert.deepEqual(mailboxState.Trash, [{ id: "existing-trash-message" }]);
  assert.equal(reconciliations.length, 0);
  assert.equal(pendingKeys.has(target.inFlightKey), true);
  assert.equal(
    hasPendingProviderTrashForMailbox(pendingKeys, "server-mailbox-1"),
    true,
  );

  const duplicate = await coordinator.trash(target);
  assert.deepEqual(duplicate, {
    classification: "blocked",
    reason: "already_pending",
    inFlightKey: target.inFlightKey,
    request: target.request,
    reconciliationAttempted: false,
    reconciled: false,
  });
  assert.equal(mutationCalls, 1);

  mutation.resolve(successResponse(target.request));
  await waitFor(() => reconciliations.length === 1);
  assert.deepEqual(reconciliations, [
    {
      mailboxId: "server-mailbox-1",
      providerMessageId: "18f-provider-message-1",
      sourceFolder: "INBOX",
      cause: "confirmed_success",
    },
  ]);
  assert.strictEqual(mailboxState, originalState);
  assert.equal(pendingKeys.has(target.inFlightKey), true);

  reconciliation.resolve();
  const result = await first;
  assert.equal(result.classification, "success");
  assert.equal(result.reconciliationAttempted, true);
  assert.equal(result.reconciled, true);
  assert.equal(mutationCalls, 1);
  assert.equal(reconciliations.length, 1);
  assert.strictEqual(mailboxState, originalState);
  assert.equal(pendingKeys.size, 0);
});

test("the coordinator default delegates once to the strict Trash client", async () => {
  const target = exactTarget();
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  const reconciliations: unknown[] = [];

  globalThis.fetch = (async (input, init) => {
    fetchCalls += 1;
    assert.equal(input, "/api/inboxes/message-action");
    assert.equal(init?.method, "POST");
    assert.equal(init?.credentials, "include");
    assert.equal(init?.cache, "no-store");
    assert.deepEqual(JSON.parse(String(init?.body)), target.request);
    return new Response(JSON.stringify(successResponse(target.request)), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  try {
    const result = await createProviderTrashCoordinator({
      reconcileReadOnly: (request) => {
        reconciliations.push(request);
      },
    }).trash(target);

    assert.equal(result.classification, "success");
    assert.equal(fetchCalls, 1);
    assert.deepEqual(reconciliations, [
      {
        mailboxId: target.request.mailboxId,
        providerMessageId: target.request.providerMessageId,
        sourceFolder: "INBOX",
        cause: "confirmed_success",
      },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ordinary and malformed failures preserve state and never reconcile or retry", async () => {
  const target = exactTarget();
  const state = {
    Inbox: [{ id: "keep-inbox" }],
    Trash: [{ id: "keep-trash" }],
  };

  for (const response of [
    {
      ok: false,
      error: {
        code: "gmail_permission_denied",
        message: "Could not complete this Trash request safely.",
      },
    },
    {
      ...successResponse(target.request),
      status: "unexpected-extra-success-field",
    },
    {
      ...successResponse(target.request),
      providerMessageId: "different-provider-message",
    },
  ] as ProviderTrashMutationResponse[]) {
    const originalState = state;
    let mutationCalls = 0;
    let reconciliationCalls = 0;
    const coordinator = createProviderTrashCoordinator({
      mutate: async () => {
        mutationCalls += 1;
        return response;
      },
      reconcileReadOnly: () => {
        reconciliationCalls += 1;
      },
    });

    const result = await coordinator.trash(target);
    assert.equal(result.classification, "ordinary_failure");
    assert.equal(result.reconciliationAttempted, false);
    assert.equal(result.reconciled, false);
    assert.equal(mutationCalls, 1);
    assert.equal(reconciliationCalls, 0);
    assert.strictEqual(state, originalState);
  }
});

test("mutation uncertainty performs exactly one read-only reconciliation and never retries mutation", async () => {
  const target = exactTarget();
  const pendingKeys = new Set<string>();
  let mutationCalls = 0;
  const reconciliations: unknown[] = [];
  const coordinator = createProviderTrashCoordinator({
    pendingKeys,
    mutate: async (request) => {
      mutationCalls += 1;
      return uncertainResponse(request);
    },
    reconcileReadOnly: (request) => {
      reconciliations.push(request);
    },
  });

  const result = await coordinator.trash(target);
  assert.equal(result.classification, "uncertain");
  assert.equal(result.mutationClassification, "uncertain");
  assert.equal(result.reconciliationAttempted, true);
  assert.equal(result.reconciled, true);
  assert.equal(mutationCalls, 1);
  assert.deepEqual(reconciliations, [
    {
      mailboxId: "server-mailbox-1",
      providerMessageId: "18f-provider-message-1",
      sourceFolder: "INBOX",
      cause: "mutation_unconfirmed",
    },
  ]);
  assert.equal(pendingKeys.size, 0);
});

test("a thrown mutation is treated as uncertain without leaking or retrying", async () => {
  const target = exactTarget();
  let mutationCalls = 0;
  let reconciliationCalls = 0;
  const result = await createProviderTrashCoordinator({
    mutate: async () => {
      mutationCalls += 1;
      throw new Error("provider secret must not escape");
    },
    reconcileReadOnly: () => {
      reconciliationCalls += 1;
    },
  }).trash(target);

  assert.equal(result.classification, "uncertain");
  assert.equal(mutationCalls, 1);
  assert.equal(reconciliationCalls, 1);
  assert.doesNotMatch(JSON.stringify(result), /provider secret/);
});

test("failed read-only reconciliation returns a UI-safe reconciliation_failed result and unlocks", async () => {
  const target = exactTarget();

  for (const [name, response, expectedMutationClassification] of [
    ["confirmed", successResponse(target.request), "success"],
    ["uncertain", uncertainResponse(target.request), "uncertain"],
  ] as const) {
    const pendingKeys = new Set<string>();
    let mutationCalls = 0;
    let reconciliationCalls = 0;
    const result = await createProviderTrashCoordinator({
      pendingKeys,
      mutate: async () => {
        mutationCalls += 1;
        return response;
      },
      reconcileReadOnly: () => {
        reconciliationCalls += 1;
        throw new Error("readback secret must not escape");
      },
    }).trash(target);

    assert.equal(result.classification, "reconciliation_failed", name);
    assert.equal(
      result.mutationClassification,
      expectedMutationClassification,
      name,
    );
    assert.equal(result.reconciliationAttempted, true, name);
    assert.equal(result.reconciled, false, name);
    assert.equal(mutationCalls, 1, name);
    assert.equal(reconciliationCalls, 1, name);
    assert.equal(pendingKeys.size, 0, name);
    assert.doesNotMatch(JSON.stringify(result), /readback secret/, name);
  }
});

test("a forged target cannot bypass exact request and stable-key validation", async () => {
  const target = exactTarget();
  let mutationCalls = 0;
  const result = await createProviderTrashCoordinator({
    mutate: async (request) => {
      mutationCalls += 1;
      return successResponse(request);
    },
    reconcileReadOnly: () => undefined,
  }).trash({
    ...target,
    inFlightKey: `${target.inFlightKey}:forged`,
  });

  assert.deepEqual(result, {
    classification: "blocked",
    reason: "invalid_target",
    reconciliationAttempted: false,
    reconciled: false,
  });
  assert.equal(mutationCalls, 0);
});

test("provider Trash folder readback removes only exact mailbox and provider identities", () => {
  const selected = {
    ...sourceMessage(),
    unread: true,
    flagged: true,
  };
  const sameThreadSibling = {
    ...sourceMessage({
      id: "ui-message-2",
      providerMessageId: "18f-provider-message-2",
    }),
    unread: false,
    flagged: false,
  };
  const sameProviderIdOtherMailbox = sourceMessage({
    id: "ui-message-other-mailbox",
    serverMailboxId: "server-mailbox-2",
  });
  const trashSelected = {
    ...selected,
    providerFolder: "Trash",
  };
  const current = {
    Inbox: [selected, sameThreadSibling, sameProviderIdOtherMailbox],
    Trash: [],
    Archive: [{ id: "archive-unchanged" }],
  };

  const result = applyGmailProviderTrashFolderReadback(current, {
    mailboxId: "server-mailbox-1",
    Trash: [trashSelected],
  });
  assert.equal(result.applied, true);
  assert.notStrictEqual(result.state, current);
  assert.deepEqual(result.state.Inbox, [
    sameThreadSibling,
    sameProviderIdOtherMailbox,
  ]);
  assert.deepEqual(result.state.Trash, [trashSelected]);
  assert.strictEqual(result.state.Trash[0], trashSelected);
  assert.strictEqual(result.state.Archive, current.Archive);
  assert.equal(result.state.Inbox.includes(sameThreadSibling), true);
});

test("atomic Inbox and Trash reconciliation requires one unambiguous target side and rejects overlap", () => {
  const selectedInbox = sourceMessage();
  const siblingInbox = sourceMessage({
    id: "ui-message-2",
    providerMessageId: "18f-provider-message-2",
  });
  const selectedTrash = {
    ...selectedInbox,
    providerFolder: "Trash",
    unread: true,
    flagged: true,
  };
  const current = {
    Inbox: [selectedInbox, siblingInbox],
    Trash: [],
    Archive: [{ id: "archive-unchanged" }],
  };

  const confirmed = replaceGmailProviderInboxAndTrashReadback(current, {
    mailboxId: "server-mailbox-1",
    targetProviderMessageId: "18f-provider-message-1",
    mutationConfirmed: true,
    Inbox: [siblingInbox],
    Trash: [selectedTrash],
  });
  assert.equal(confirmed.applied, true);
  assert.deepEqual(confirmed.state.Inbox, [siblingInbox]);
  assert.deepEqual(confirmed.state.Trash, [selectedTrash]);
  assert.strictEqual(confirmed.state.Trash[0], selectedTrash);
  assert.strictEqual(confirmed.state.Archive, current.Archive);

  const uncertainStillInInbox = replaceGmailProviderInboxAndTrashReadback(
    current,
    {
      mailboxId: "server-mailbox-1",
      targetProviderMessageId: "18f-provider-message-1",
      mutationConfirmed: false,
      Inbox: [selectedInbox, siblingInbox],
      Trash: [],
    },
  );
  assert.equal(uncertainStillInInbox.applied, true);
  assert.strictEqual(uncertainStillInInbox.state.Inbox[0], selectedInbox);

  for (const invalidReadback of [
    {
      Inbox: [selectedInbox, siblingInbox],
      Trash: [selectedTrash],
    },
    {
      Inbox: [siblingInbox],
      Trash: [],
    },
    {
      Inbox: [siblingInbox, siblingInbox],
      Trash: [selectedTrash],
    },
    {
      Inbox: [siblingInbox],
      Trash: [{ ...selectedTrash, serverMailboxId: "server-mailbox-2" }],
    },
  ]) {
    const rejected = replaceGmailProviderInboxAndTrashReadback(current, {
      mailboxId: "server-mailbox-1",
      targetProviderMessageId: "18f-provider-message-1",
      mutationConfirmed: true,
      ...invalidReadback,
    });
    assert.equal(rejected.applied, false);
    assert.strictEqual(rejected.state, current);
  }
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

  if (failed > 0) {
    process.exitCode = 1;
  }
}

void run();
