import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  applyProviderArchiveFolderReadback,
  buildProviderArchiveStateIdentity,
  buildProviderArchiveMutationTarget,
  createProviderArchiveCoordinator,
  executeProviderArchiveAction,
  filterLegacyArchiveHydration,
  hasPendingProviderArchiveForMailbox,
  mergeLegacyArchiveStorage,
  replaceProviderArchiveReadback,
  type ProviderArchiveCandidate,
  type ProviderArchiveMutationRequest,
  type ProviderArchiveMutationResponse,
} from "./providerArchiveAction";

type Test = {
  name: string;
  run: () => void | Promise<void>;
};

const tests: Test[] = [];

function test(name: string, run: Test["run"]) {
  tests.push({ name, run });
}

function gmailCandidate(
  overrides: Partial<ProviderArchiveCandidate> = {},
): ProviderArchiveCandidate {
  return {
    provider: "google",
    mailboxId: "mailbox-1",
    folder: "Inbox",
    providerMessageId: "gmail-provider-message-1",
    ...overrides,
  };
}

function imapCandidate(
  overrides: Partial<ProviderArchiveCandidate> = {},
): ProviderArchiveCandidate {
  return {
    provider: "custom_imap",
    mailboxId: "mailbox-1",
    folder: "INBOX",
    imapUid: "42",
    uidValidity: "900",
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

function successResponse(
  request: ProviderArchiveMutationRequest,
): ProviderArchiveMutationResponse {
  const preview = {
    id: "rfc-message@example.test",
    sender: "Sender",
    subject: "Provider message",
    snippet: "Provider body",
    from: "sender@example.test",
    to: "owner@example.test",
    timestamp: "July 27 at 10:00",
    createdAt: "2026-07-27T08:00:00.000Z",
    body: ["Provider body"],
  };
  if ("messageId" in request) {
    return {
      ok: true,
      status: "ok",
      action: "archive",
      mailboxId: request.mailboxId,
      archivedMessageIdentity: {
        serverMailboxId: request.mailboxId,
        providerMessageId: request.messageId,
        providerThreadId: "gmail-thread-1",
        providerFolder: "Archive",
        rfcMessageId: "rfc-message@example.test",
      },
      folders: {
        Inbox: {
          serverMailboxId: request.mailboxId,
          providerFolder: "Inbox",
          uidValidity: "gmail-api",
          messages: [],
        },
        Archive: {
          serverMailboxId: request.mailboxId,
          providerFolder: "Archive",
          uidValidity: "gmail-api",
          messages: [
            {
              ...preview,
              serverMailboxId: request.mailboxId,
              providerFolder: "Archive",
              providerMessageId: request.messageId,
              providerThreadId: "gmail-thread-1",
              rfcMessageId: "rfc-message@example.test",
              labelIds: ["STARRED"],
            },
          ],
        },
      },
    };
  }

  const archiveUid = "900";
  const archiveUidValidity = "901";
  const archiveFolder = "Archive";
  return {
    ok: true,
    status: "ok",
    action: "archive",
    mailboxId: request.mailboxId,
    archivedMessageIdentity: {
      serverMailboxId: request.mailboxId,
      sourceProviderFolder: request.folder,
      sourceImapUid: request.uid,
      sourceUidValidity: request.uidValidity,
      providerFolder: archiveFolder,
      imapUid: archiveUid,
      uidValidity: archiveUidValidity,
      rfcMessageId: "rfc-message@example.test",
    },
    folders: {
      Inbox: {
        serverMailboxId: request.mailboxId,
        providerFolder: "INBOX",
        uidValidity: request.uidValidity,
        imapUidSet: [],
        messages: [],
      },
      Archive: {
        serverMailboxId: request.mailboxId,
        providerFolder: archiveFolder,
        uidValidity: archiveUidValidity,
        imapUidSet: [archiveUid],
        messages: [
          {
            ...preview,
            serverMailboxId: request.mailboxId,
            providerFolder: archiveFolder,
            imapUid: archiveUid,
            uidValidity: archiveUidValidity,
            threadId: `imap:uid:${request.mailboxId}:${archiveFolder}:${archiveUidValidity}:${archiveUid}`,
            rfcMessageId: "rfc-message@example.test",
          },
        ],
      },
    },
  };
}

test("Gmail uses only the concrete provider message id in an exact request", () => {
  const target = buildProviderArchiveMutationTarget({
    ...gmailCandidate(),
    id: "rfc-or-ui-id-must-not-be-used",
    providerThreadId: "thread-id-must-not-be-used",
    imapUid: "legacy-id-must-not-be-used",
  } as ProviderArchiveCandidate);

  assert.equal(target.ok, true);
  if (!target.ok) return;
  assert.deepEqual(target.request, {
    mailboxId: "mailbox-1",
    messageId: "gmail-provider-message-1",
    action: "archive",
  });
  assert.deepEqual(Object.keys(target.request).sort(), [
    "action",
    "mailboxId",
    "messageId",
  ]);
  assert.equal(target.inFlightKey.includes("gmail-provider-message-1"), true);
  assert.equal(target.inFlightKey.includes("thread-id-must-not-be-used"), false);
});

test("Gmail never falls back to UI, RFC, thread, or legacy IMAP ids", () => {
  const target = buildProviderArchiveMutationTarget({
    provider: "google",
    mailboxId: "mailbox-1",
    folder: "Inbox",
    providerMessageId: null,
    id: "ui-id",
    rfcMessageId: "rfc@example.com",
    providerThreadId: "provider-thread",
    imapUid: "42",
  } as ProviderArchiveCandidate);

  assert.deepEqual(target, {
    ok: false,
    classification: "blocked",
    reason: "missing_gmail_provider_message_id",
  });
});

test("Gmail and mailbox identifiers must be exact, concrete, and bounded", () => {
  for (const value of [
    "",
    " message",
    "message ",
    "message\n",
    "rfc@example.test",
    "<rfc-message>",
    "imap-uid-42",
    "thread-42",
    "x".repeat(257),
  ]) {
    const target = buildProviderArchiveMutationTarget(
      gmailCandidate({ providerMessageId: value }),
    );
    assert.equal(target.ok, false, `expected provider id ${JSON.stringify(value)} to fail`);
  }

  assert.deepEqual(
    buildProviderArchiveMutationTarget(gmailCandidate({ mailboxId: " mailbox-1" })),
    {
      ok: false,
      classification: "blocked",
      reason: "invalid_mailbox_id",
    },
  );
});

test("Gmail rejects every source folder outside its canonical Inbox namespace", () => {
  for (const folder of ["INBOX", "Archive", "Spam", " Inbox", "Inbox ", null]) {
    assert.deepEqual(
      buildProviderArchiveMutationTarget(gmailCandidate({ folder })),
      {
        ok: false,
        classification: "blocked",
        reason: "invalid_gmail_source_folder",
      },
    );
  }
});

test("custom IMAP builds only canonical Inbox UID identity fields", () => {
  const target = buildProviderArchiveMutationTarget({
    ...imapCandidate(),
    providerMessageId: "must-not-be-used",
    targetFolder: "Archive",
  } as ProviderArchiveCandidate);

  assert.equal(target.ok, true);
  if (!target.ok) return;
  assert.deepEqual(target.request, {
    mailboxId: "mailbox-1",
    folder: "INBOX",
    uid: "42",
    uidValidity: "900",
    action: "archive",
  });
  assert.deepEqual(Object.keys(target.request).sort(), [
    "action",
    "folder",
    "mailboxId",
    "uid",
    "uidValidity",
  ]);
  assert.equal(target.inFlightKey.includes("Archive"), false);
});

test("custom IMAP rejects any source folder other than exact INBOX", () => {
  for (const folder of ["Inbox", "inbox", " INBOX", "INBOX ", "Archive", null]) {
    const target = buildProviderArchiveMutationTarget(imapCandidate({ folder }));
    assert.deepEqual(target, {
      ok: false,
      classification: "blocked",
      reason: "invalid_imap_source_folder",
    });
  }
});

test("custom IMAP rejects non-canonical UID and UIDVALIDITY values", () => {
  for (const imapUid of ["", "0", "01", "1:2", "4294967296", " 42", "42 "]) {
    const target = buildProviderArchiveMutationTarget(imapCandidate({ imapUid }));
    assert.deepEqual(target, {
      ok: false,
      classification: "blocked",
      reason: "invalid_imap_uid",
    });
  }

  for (const uidValidity of ["", "0", "0900", "9".repeat(21), " 900", "900 "]) {
    const target = buildProviderArchiveMutationTarget(
      imapCandidate({ uidValidity }),
    );
    assert.deepEqual(target, {
      ok: false,
      classification: "blocked",
      reason: "invalid_imap_uid_validity",
    });
  }
});

test("in-flight keys are deterministic and identity scoped", () => {
  const first = buildProviderArchiveMutationTarget(imapCandidate());
  const same = buildProviderArchiveMutationTarget(imapCandidate());
  const nextUid = buildProviderArchiveMutationTarget(imapCandidate({ imapUid: "43" }));
  const nextEpoch = buildProviderArchiveMutationTarget(
    imapCandidate({ uidValidity: "901" }),
  );
  const nextMailbox = buildProviderArchiveMutationTarget(
    imapCandidate({ mailboxId: "mailbox-2" }),
  );

  assert.equal(first.ok, true);
  assert.equal(same.ok, true);
  assert.equal(nextUid.ok, true);
  assert.equal(nextEpoch.ok, true);
  assert.equal(nextMailbox.ok, true);
  if (!first.ok || !same.ok || !nextUid.ok || !nextEpoch.ok || !nextMailbox.ok) {
    return;
  }
  assert.equal(first.inFlightKey, same.inFlightKey);
  assert.notEqual(first.inFlightKey, nextUid.inFlightKey);
  assert.notEqual(first.inFlightKey, nextEpoch.inFlightKey);
  assert.notEqual(first.inFlightKey, nextMailbox.inFlightKey);
});

test("state identities remain scoped by mailbox, provider folder, and UID epoch", () => {
  const baseImap = {
    serverMailboxId: "mailbox-1",
    providerFolder: "INBOX",
    imapUid: "42",
    uidValidity: "900",
  };
  const baseIdentity = buildProviderArchiveStateIdentity(baseImap);
  assert.notEqual(
    baseIdentity,
    buildProviderArchiveStateIdentity({
      ...baseImap,
      serverMailboxId: "mailbox-2",
    }),
  );
  assert.notEqual(
    baseIdentity,
    buildProviderArchiveStateIdentity({
      ...baseImap,
      providerFolder: "Archive",
    }),
  );
  assert.notEqual(
    baseIdentity,
    buildProviderArchiveStateIdentity({
      ...baseImap,
      uidValidity: "901",
    }),
  );

  assert.notEqual(
    buildProviderArchiveStateIdentity({
      serverMailboxId: "mailbox-1",
      providerFolder: "Archive",
      providerMessageId: "gmail-message-1",
    }),
    buildProviderArchiveStateIdentity({
      serverMailboxId: "mailbox-1",
      providerFolder: "Archive",
      providerMessageId: "gmail-message-2",
    }),
  );
});

test("pending Archive mutations are discoverable by exact mailbox scope", () => {
  const first = buildProviderArchiveMutationTarget(gmailCandidate());
  const second = buildProviderArchiveMutationTarget(
    imapCandidate({ mailboxId: "mailbox-2" }),
  );
  assert.equal(first.ok, true);
  assert.equal(second.ok, true);
  if (!first.ok || !second.ok) return;

  const pending = new Set([first.inFlightKey, second.inFlightKey, "not-json"]);
  assert.equal(
    hasPendingProviderArchiveForMailbox(pending, "mailbox-1"),
    true,
  );
  assert.equal(
    hasPendingProviderArchiveForMailbox(pending, "mailbox-2"),
    true,
  );
  assert.equal(
    hasPendingProviderArchiveForMailbox(pending, "mailbox-3"),
    false,
  );

  const differentIdentitySameMailbox = buildProviderArchiveMutationTarget(
    gmailCandidate({ providerMessageId: "gmail-provider-message-2" }),
  );
  assert.equal(differentIdentitySameMailbox.ok, true);
  assert.equal(
    hasPendingProviderArchiveForMailbox(
      new Set([first.inFlightKey]),
      "mailbox-1",
    ),
    true,
    "a different identity in the same mailbox must still see the mailbox lock",
  );
});

test("coordinator classifies a provider-confirmed mutation as success", async () => {
  const calls: ProviderArchiveMutationRequest[] = [];
  const pendingKeys = new Set<string>();
  const coordinator = createProviderArchiveCoordinator({
    pendingKeys,
    mutate: async (request) => {
      calls.push(request);
      return successResponse(request);
    },
  });

  const result = await coordinator.archive(gmailCandidate());
  assert.equal(result.classification, "success");
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], {
    mailboxId: "mailbox-1",
    messageId: "gmail-provider-message-1",
    action: "archive",
  });
  assert.equal(pendingKeys.size, 0);
});

test("ordinary provider failures are classified without retry", async () => {
  let calls = 0;
  const coordinator = createProviderArchiveCoordinator({
    mutate: async () => {
      calls += 1;
      return {
        ok: false,
        error: {
          code: "access_token_canary",
          message: "raw provider refresh-token-canary",
        },
      };
    },
  });

  const result = await coordinator.archive(gmailCandidate());
  assert.equal(result.classification, "ordinary_failure");
  assert.equal(calls, 1);
  assert.doesNotMatch(
    JSON.stringify(result),
    /access_token_canary|refresh-token-canary/,
  );
});

test("an ok-shaped mutation without the strict success status is not success", async () => {
  const coordinator = createProviderArchiveCoordinator({
    mutate: async () => ({ ok: true }),
  });

  assert.equal(
    (await coordinator.archive(gmailCandidate())).classification,
    "ordinary_failure",
  );
});

test("deeply malformed server snapshots never classify as success", async () => {
  let applyCalls = 0;
  const requestTarget = buildProviderArchiveMutationTarget(gmailCandidate());
  assert.equal(requestTarget.ok, true);
  if (!requestTarget.ok) return;
  const malformed = {
    ...successResponse(requestTarget.request),
    folders: {
      Inbox: {},
      Archive: {},
    },
  };
  const result = await executeProviderArchiveAction({
    coordinator: createProviderArchiveCoordinator({
      mutate: async () => malformed,
    }),
    candidate: gmailCandidate(),
    applySuccess: () => {
      applyCalls += 1;
      return true;
    },
  });

  assert.equal(result.classification, "ordinary_failure");
  assert.equal(result.applied, false);
  assert.equal(applyCalls, 0);
});

test("confirmed readback failure stays uncertain while incomplete markers do not", async () => {
  const responses: ProviderArchiveMutationResponse[] = [
    {
      ok: false,
      status: "mutation_confirmed_readback_failed",
      action: "archive",
      mailboxId: "mailbox-1",
      archivedMessageIdentity: {
        malformed: "must-not-be-published",
      },
      error: {
        code: "archive_readback_failed",
        message: "raw provider access-token-canary",
      },
    },
    {
      ok: false,
      action: "archive",
      mailboxId: "mailbox-1",
      archivedMessageIdentity: {
        serverMailboxId: "mailbox-1",
        providerMessageId: "gmail-provider-message-2",
        providerFolder: "Archive",
      },
      error: { code: "archive_readback_failed" },
    },
  ];
  let calls = 0;
  const coordinator = createProviderArchiveCoordinator({
    mutate: async () => {
      const response = responses[calls];
      calls += 1;
      assert.ok(response);
      return response;
    },
  });

  const uncertain = await coordinator.archive(gmailCandidate());
  assert.equal(uncertain.classification, "uncertain");
  assert.deepEqual(
    uncertain.classification === "uncertain"
      ? uncertain.response.archivedMessageIdentity
      : null,
    {
      serverMailboxId: "mailbox-1",
      providerMessageId: "gmail-provider-message-1",
      providerFolder: "Archive",
    },
  );
  assert.doesNotMatch(JSON.stringify(uncertain), /access-token-canary|malformed/);
  assert.equal(
    (
      await coordinator.archive(
        gmailCandidate({ providerMessageId: "gmail-provider-message-2" }),
      )
    ).classification,
    "ordinary_failure",
  );
  assert.equal(calls, 2);
});

test("validation blocks before mutation and never touches the pending set", async () => {
  let calls = 0;
  const pendingKeys = new Set<string>();
  const coordinator = createProviderArchiveCoordinator({
    pendingKeys,
    mutate: async () => {
      calls += 1;
      return { ok: true };
    },
  });

  const result = await coordinator.archive(
    gmailCandidate({ providerMessageId: undefined }),
  );
  assert.deepEqual(result, {
    classification: "blocked",
    reason: "missing_gmail_provider_message_id",
  });
  assert.equal(calls, 0);
  assert.equal(pendingKeys.size, 0);
});

test("same-key duplicate is blocked while the first request is pending", async () => {
  const pendingResponse = deferred<ProviderArchiveMutationResponse>();
  const pendingKeys = new Set<string>();
  let calls = 0;
  const coordinator = createProviderArchiveCoordinator({
    pendingKeys,
    mutate: async () => {
      calls += 1;
      return pendingResponse.promise;
    },
  });

  const firstResult = coordinator.archive(imapCandidate());
  assert.equal(calls, 1);
  assert.equal(pendingKeys.size, 1);

  const duplicateResult = await coordinator.archive(imapCandidate());
  assert.equal(duplicateResult.classification, "blocked");
  assert.equal(
    duplicateResult.classification === "blocked"
      ? duplicateResult.reason
      : null,
    "already_pending",
  );
  assert.equal(calls, 1);
  assert.equal(pendingKeys.size, 1);

  const pendingRequest = buildProviderArchiveMutationTarget(imapCandidate());
  assert.equal(pendingRequest.ok, true);
  if (!pendingRequest.ok) return;
  pendingResponse.resolve(successResponse(pendingRequest.request));
  assert.equal((await firstResult).classification, "success");
  assert.equal(pendingKeys.size, 0);
});

test("execution applies one validated readback only after the provider resolves", async () => {
  const pendingResponse = deferred<ProviderArchiveMutationResponse>();
  const pendingKeys = new Set<string>();
  let calls = 0;
  let applyCalls = 0;
  const initialState = {
    Inbox: [{ id: "stale-inbox" }],
    Archive: [{ id: "stale-archive" }],
    Trash: [{ id: "trash-unchanged" }],
  };
  let state = initialState;
  const coordinator = createProviderArchiveCoordinator({
    pendingKeys,
    mutate: async () => {
      calls += 1;
      return pendingResponse.promise;
    },
  });

  const execution = executeProviderArchiveAction({
    coordinator,
    candidate: gmailCandidate(),
    applySuccess: (response) => {
      applyCalls += 1;
      assert.equal(
        hasPendingProviderArchiveForMailbox(pendingKeys, "mailbox-1"),
        true,
        "mailbox lock must remain held while server state is applied",
      );
      const folders = response.folders as {
        Inbox: { messages: Array<{ id: string }> };
        Archive: { messages: Array<{ id: string }> };
      };
      state = replaceProviderArchiveReadback(state, {
        Inbox: folders.Inbox.messages,
        Archive: folders.Archive.messages,
      });
      return true;
    },
  });

  assert.equal(calls, 1);
  assert.equal(applyCalls, 0);
  assert.equal(state, initialState, "state must remain exact while mutation is pending");

  const target = buildProviderArchiveMutationTarget(gmailCandidate());
  assert.equal(target.ok, true);
  if (!target.ok) return;
  pendingResponse.resolve(successResponse(target.request));
  const result = await execution;

  assert.equal(result.classification, "success");
  assert.equal(result.applied, true);
  assert.equal(applyCalls, 1);
  assert.deepEqual(state.Inbox, []);
  assert.deepEqual(state.Archive.map((message) => message.id), [
    "rfc-message@example.test",
  ]);
  assert.deepEqual(state.Trash, [{ id: "trash-unchanged" }]);
  assert.equal(pendingKeys.size, 0);
});

test("ordinary and uncertain execution failures preserve exact state without retry", async () => {
  const failures: Array<{
    expected: "ordinary_failure" | "uncertain";
    response: ProviderArchiveMutationResponse;
  }> = [
    {
      expected: "ordinary_failure",
      response: {
        ok: false,
        error: {
          code: "gmail_archive_failed",
          message: "Could not archive this message.",
        },
      },
    },
    {
      expected: "uncertain",
      response: {
        ok: false,
        status: "mutation_confirmed_readback_failed",
        action: "archive",
        mailboxId: "mailbox-1",
        archivedMessageIdentity: {
          serverMailboxId: "mailbox-1",
          providerMessageId: "gmail-provider-message-1",
          providerFolder: "Archive",
        },
        error: {
          code: "archive_readback_failed",
          message: "Archive readback failed.",
        },
      },
    },
  ];

  for (const failure of failures) {
    const initialState = {
      Inbox: [{ id: "original-inbox" }],
      Archive: [{ id: "original-archive" }],
    };
    let state = initialState;
    let calls = 0;
    let applyCalls = 0;
    const result = await executeProviderArchiveAction({
      coordinator: createProviderArchiveCoordinator({
        mutate: async () => {
          calls += 1;
          return failure.response;
        },
      }),
      candidate: gmailCandidate(),
      applySuccess: () => {
        applyCalls += 1;
        state = {
          Inbox: [],
          Archive: [],
        };
        return true;
      },
    });

    assert.equal(result.classification, failure.expected);
    assert.equal(result.applied, false);
    assert.equal(calls, 1);
    assert.equal(applyCalls, 0);
    assert.equal(state, initialState);
  }
});

test("two executions for the same pending identity mutate and apply exactly once", async () => {
  const pendingResponse = deferred<ProviderArchiveMutationResponse>();
  const pendingKeys = new Set<string>();
  let calls = 0;
  let applyCalls = 0;
  const coordinator = createProviderArchiveCoordinator({
    pendingKeys,
    mutate: async () => {
      calls += 1;
      return pendingResponse.promise;
    },
  });
  const execute = () =>
    executeProviderArchiveAction({
      coordinator,
      candidate: gmailCandidate(),
      applySuccess: () => {
        applyCalls += 1;
        return true;
      },
    });

  const first = execute();
  const duplicate = await execute();
  assert.equal(duplicate.classification, "blocked");
  assert.equal(duplicate.applied, false);
  assert.equal(calls, 1);
  assert.equal(applyCalls, 0);

  const target = buildProviderArchiveMutationTarget(gmailCandidate());
  assert.equal(target.ok, true);
  if (!target.ok) return;
  pendingResponse.resolve(successResponse(target.request));
  const firstResult = await first;
  assert.equal(firstResult.classification, "success");
  assert.equal(firstResult.applied, true);
  assert.equal(calls, 1);
  assert.equal(applyCalls, 1);
});

test("a mutation exception is ordinary, is not retried, and releases its key", async () => {
  const pendingKeys = new Set<string>();
  let calls = 0;
  const coordinator = createProviderArchiveCoordinator({
    pendingKeys,
    mutate: async () => {
      calls += 1;
      throw new Error("provider detail must not escape");
    },
  });

  const result = await coordinator.archive(gmailCandidate());
  assert.equal(result.classification, "ordinary_failure");
  assert.equal(calls, 1);
  assert.equal(pendingKeys.size, 0);
  assert.equal(JSON.stringify(result).includes("provider detail must not escape"), false);
});

test("server readback replaces both provider folders and preserves unrelated collections", () => {
  const current = {
    Inbox: [{ id: "stale-inbox" }],
    Archive: [{ id: "stale-archive" }],
    Trash: [{ id: "trash-unchanged" }],
  };
  const next = replaceProviderArchiveReadback(current, {
    Inbox: [{ id: "server-inbox" }],
    Archive: [{ id: "server-archive" }],
  });

  assert.deepEqual(next, {
    Inbox: [{ id: "server-inbox" }],
    Archive: [{ id: "server-archive" }],
    Trash: [{ id: "trash-unchanged" }],
  });
  assert.deepEqual(current, {
    Inbox: [{ id: "stale-inbox" }],
    Archive: [{ id: "stale-archive" }],
    Trash: [{ id: "trash-unchanged" }],
  });
});

test("valid cold Archive readback replaces only Archive", () => {
  const current = {
    Inbox: [{ id: "inbox-unchanged" }],
    Archive: [{ id: "stale-archive" }],
    Trash: [{ id: "trash-unchanged" }],
  };

  assert.deepEqual(
    applyProviderArchiveFolderReadback(current, [{ id: "server-archive" }]),
    {
      applied: true,
      state: {
        Inbox: [{ id: "inbox-unchanged" }],
        Archive: [{ id: "server-archive" }],
        Trash: [{ id: "trash-unchanged" }],
      },
    },
  );
});

test("invalid or failed cold Archive readback preserves the exact current state", () => {
  const current = {
    Inbox: [{ id: "inbox-unchanged" }],
    Archive: [{ id: "archive-must-remain" }],
    Trash: [{ id: "trash-unchanged" }],
  };

  for (const missingReadback of [null, undefined]) {
    const result = applyProviderArchiveFolderReadback(
      current,
      missingReadback,
    );
    assert.deepEqual(result, {
      applied: false,
      state: current,
    });
    assert.equal(result.state, current);
  }
  assert.deepEqual(current, {
      Inbox: [{ id: "inbox-unchanged" }],
      Archive: [{ id: "archive-must-remain" }],
      Trash: [{ id: "trash-unchanged" }],
  });
});

test("legacy Archive hydration excludes provider-authoritative mailboxes", () => {
  const hydrated = filterLegacyArchiveHydration<{ id: string }>(
    {
      gmail: [{ id: "legacy-gmail" }],
      imap: [{ id: "legacy-imap" }],
      local: [{ id: "local-archive" }],
    },
    ["gmail", "imap", "local"],
    new Set(["gmail", "imap"]),
  );

  assert.deepEqual(hydrated, {
    local: [{ id: "local-archive" }],
  });
});

test("legacy Archive writes preserve old provider entries without publishing live provider state", () => {
  const merged = mergeLegacyArchiveStorage(
    {
      gmail: [{ id: "old-provider-cache" }],
      imap: [{ id: "old-imap-cache" }],
      local: [{ id: "old-local" }],
    },
    {
      gmail: [{ id: "live-provider-state-must-not-be-written" }],
      imap: [{ id: "live-imap-state-must-not-be-written" }],
      local: [{ id: "new-local" }],
    },
    new Set(["gmail", "imap"]),
  );

  assert.deepEqual(merged, {
    gmail: [{ id: "old-provider-cache" }],
    imap: [{ id: "old-imap-cache" }],
    local: [{ id: "new-local" }],
  });
});

test("module remains pure and owns no transport, React, or persistence", () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, "providerArchiveAction.ts"),
    "utf8",
  );
  for (const forbidden of [
    "localStorage",
    "sessionStorage",
    "useState",
    "useEffect",
    "react",
    "fetch(",
  ]) {
    assert.equal(source.includes(forbidden), false, forbidden);
  }
});

async function run() {
  let failures = 0;
  for (const entry of tests) {
    try {
      await entry.run();
      console.log(`  ✓ ${entry.name}`);
    } catch (error) {
      failures += 1;
      console.error(`  ✗ ${entry.name}`);
      console.error(`    ${(error as Error).message}`);
    }
  }

  if (failures > 0) {
    process.exitCode = 1;
    return;
  }
  console.log(`providerArchiveAction: ${tests.length} tests passed`);
}

void run();
