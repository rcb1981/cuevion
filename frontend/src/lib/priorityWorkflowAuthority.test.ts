import assert from "node:assert/strict";
import {
  PriorityWorkflowWriteCoordinator,
  resolvePriorityWorkflowTarget,
  type PriorityWorkflowOperation,
  type PriorityWorkflowTarget,
} from "./priorityWorkflowAuthority";
import type {
  PriorityWorkflowAuthorityError,
  PriorityWorkflowAuthorityResult,
  PriorityWorkflowIdentity,
  PriorityWorkflowRecord,
} from "./priorityWorkflowAuthorityApi";

let passed = 0;
let failed = 0;

async function test(name: string, fn: () => void | Promise<void>) {
  try {
    await fn();
    console.log(`  ✓ ${name}`);
    passed += 1;
  } catch (error) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${(error as Error).message}`);
    failed += 1;
  }
}

const gmailIdentity = {
  provider: "google" as const,
  providerMessageId: "gmail-provider-message-1",
};
const imapIdentity = {
  provider: "custom_imap" as const,
  providerFolder: "INBOX",
  uidValidity: "77",
  imapUid: "102",
};

function record(
  identity: PriorityWorkflowIdentity,
  version: number,
  overrides: Partial<PriorityWorkflowRecord> = {},
): PriorityWorkflowRecord {
  return {
    mailboxId: "mailbox-1",
    identity,
    manualPriority: "none",
    cleared: "active",
    waiting: "absent",
    version,
    updatedAt: 1_777_000_000_000 + version,
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

type WriteResult = PriorityWorkflowAuthorityResult<PriorityWorkflowRecord>;
type ReadResult = PriorityWorkflowAuthorityResult<PriorityWorkflowRecord[]>;

function fakeClient(input: {
  write: (
    operation: PriorityWorkflowOperation["operation"],
  ) => Promise<WriteResult>;
  read?: () => Promise<ReadResult>;
}) {
  return {
    setManualPriority: () => input.write("set_manual_priority"),
    setCleared: () => input.write("set_cleared"),
    setWaiting: () => input.write("set_waiting"),
    read: () =>
      input.read?.() ??
      Promise.resolve({
        ok: false as const,
        error: {
          code: "unexpected_read",
          message: "unexpected",
          ambiguous: false,
        },
      }),
  };
}

const gmailTarget: PriorityWorkflowTarget = {
  mailboxId: "mailbox-1",
  identity: gmailIdentity,
  recordKey: JSON.stringify([
    "mailbox-1",
    "google",
    "gmail-provider-message-1",
  ]),
};

async function main() {
console.log("\npriorityWorkflowAuthority");

await test("strict Gmail locator uses providerMessageId, not UI id", () => {
  const result = resolvePriorityWorkflowTarget({
    serverAuthorityEnabled: true,
    mailbox: {
      id: "mailbox-1",
      provider: "google",
      connected: true,
      connectionStatus: "connected",
    },
    message: {
      serverMailboxId: "mailbox-1",
      providerMessageId: "gmail-provider-message-1",
      threadIdentityContext: {
        mailboxId: "mailbox-1",
        provider: "google",
      },
    },
  });
  assert.equal(result.status, "canonical");
  if (result.status === "canonical") {
    assert.deepEqual(result.target.identity, gmailIdentity);
    assert.doesNotMatch(result.target.recordKey, /row|subject|sender/);
  }
});

await test("strict IMAP locator requires exact folder, UIDVALIDITY, and UID", () => {
  const result = resolvePriorityWorkflowTarget({
    serverAuthorityEnabled: true,
    mailbox: {
      id: "mailbox-1",
      provider: "custom_imap",
      connected: true,
      connectionStatus: "connected",
    },
    message: {
      serverMailboxId: "mailbox-1",
      providerFolder: "INBOX",
      uidValidity: "77",
      imapUid: "102",
      threadIdentityContext: {
        mailboxId: "mailbox-1",
        provider: "custom_imap",
        folder: "INBOX",
        uidValidity: "77",
      },
    },
  });
  assert.equal(result.status, "canonical");
  if (result.status === "canonical") {
    assert.deepEqual(result.target.identity, imapIdentity);
  }
});

await test("canonical malformed, mismatched, and unsupported messages fail closed", () => {
  const mailbox = {
    id: "mailbox-1",
    provider: "custom_imap",
    connected: true,
    connectionStatus: "connected",
  };
  assert.equal(
    resolvePriorityWorkflowTarget({
      serverAuthorityEnabled: true,
      mailbox,
      message: {
        serverMailboxId: "mailbox-1",
        providerFolder: "INBOX",
        uidValidity: "77",
        imapUid: "0",
      },
    }).status,
    "invalid",
  );
  assert.equal(
    resolvePriorityWorkflowTarget({
      serverAuthorityEnabled: true,
      mailbox,
      message: {
        serverMailboxId: "mailbox-2",
        providerFolder: "INBOX",
        uidValidity: "77",
        imapUid: "102",
      },
    }).status,
    "invalid",
  );
  assert.equal(
    resolvePriorityWorkflowTarget({
      serverAuthorityEnabled: true,
      mailbox: { ...mailbox, provider: "microsoft" },
      message: { serverMailboxId: "mailbox-1" },
    }).status,
    "invalid",
  );
});

await test("demo/non-authoritative context is explicitly isolated as local-only", () => {
  assert.deepEqual(
    resolvePriorityWorkflowTarget({
      serverAuthorityEnabled: false,
      mailbox: null,
      message: {},
    }),
    { status: "local_only", reason: "non_authoritative_workspace" },
  );
});

await test("local commit waits for canonical server success", async () => {
  const pending = deferred<WriteResult>();
  const commits: PriorityWorkflowRecord[] = [];
  const coordinator = new PriorityWorkflowWriteCoordinator(
    fakeClient({ write: () => pending.promise }),
    "scope-1",
  );
  const write = coordinator.write({
    scopeKey: "scope-1",
    target: gmailTarget,
    operation: { operation: "set_manual_priority", value: "priority" },
    commit: (value) => commits.push(value),
  });
  await Promise.resolve();
  assert.deepEqual(commits, []);
  const canonical = record(gmailIdentity, 1, { manualPriority: "priority" });
  pending.resolve({ ok: true, value: canonical });
  assert.equal((await write).status, "applied");
  assert.deepEqual(commits, [canonical]);
});

await test("failure preserves the prior mirror and has no local fallback", async () => {
  const commits: PriorityWorkflowRecord[] = [];
  const error: PriorityWorkflowAuthorityError = {
    code: "workflow_request_failed",
    message: "Could not save.",
    ambiguous: false,
  };
  const coordinator = new PriorityWorkflowWriteCoordinator(
    fakeClient({ write: async () => ({ ok: false, error }) }),
    "scope-1",
  );
  const outcome = await coordinator.write({
    scopeKey: "scope-1",
    target: gmailTarget,
    operation: { operation: "set_cleared", value: "cleared" },
    commit: (value) => commits.push(value),
  });
  assert.equal(outcome.status, "failed");
  assert.deepEqual(commits, []);
});

await test("double-click coalesces one action and one write", async () => {
  const pending = deferred<WriteResult>();
  let writes = 0;
  let actions = 0;
  const coordinator = new PriorityWorkflowWriteCoordinator(
    fakeClient({
      write: () => {
        writes += 1;
        return pending.promise;
      },
    }),
    "scope-1",
  );
  const task = () =>
    coordinator.runAction("scope-1", "manual-priority", async () => {
      actions += 1;
      return coordinator.write({
        scopeKey: "scope-1",
        target: gmailTarget,
        operation: { operation: "set_manual_priority", value: "priority" },
        commit: () => undefined,
      });
    });
  const first = task();
  const second = task();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(actions, 1);
  pending.resolve({
    ok: true,
    value: record(gmailIdentity, 1, { manualPriority: "priority" }),
  });
  assert.equal((await first).status, "applied");
  assert.equal(writes, 1);
  assert.equal(await second, await first);
});

await test("lower/equal versions never regress a newer accepted record", async () => {
  const versions = [2, 1];
  const commits: number[] = [];
  const coordinator = new PriorityWorkflowWriteCoordinator(
    fakeClient({
      write: async (operation) => ({
        ok: true,
        value: record(gmailIdentity, versions.shift()!, {
          manualPriority:
            operation === "set_manual_priority" ? "priority" : "none",
          waiting:
            operation === "set_waiting" ? "waiting_on_other" : "absent",
        }),
      }),
    }),
    "scope-1",
  );
  const first = await coordinator.write({
    scopeKey: "scope-1",
    target: gmailTarget,
    operation: { operation: "set_manual_priority", value: "priority" },
    commit: (value) => commits.push(value.version),
  });
  const second = await coordinator.write({
    scopeKey: "scope-1",
    target: gmailTarget,
    operation: { operation: "set_waiting", value: "waiting_on_other" },
    commit: (value) => commits.push(value.version),
  });
  assert.equal(first.status, "applied");
  assert.equal(second.status, "stale");
  assert.deepEqual(commits, [2]);
});

await test("manual/waiting/cleared writes serialize and accept later versions", async () => {
  const calls: string[] = [];
  let version = 0;
  const commits: Array<{ version: number; waiting: string; cleared: string }> = [];
  const coordinator = new PriorityWorkflowWriteCoordinator(
    fakeClient({
      write: async (operation) => {
        calls.push(operation);
        version += 1;
        return {
          ok: true,
          value: record(gmailIdentity, version, {
            manualPriority: "priority",
            waiting:
              operation === "set_waiting" ? "waiting_on_other" : "absent",
            cleared: operation === "set_cleared" ? "cleared" : "active",
          }),
        };
      },
    }),
    "scope-1",
  );
  const operations = [
    { operation: "set_manual_priority", value: "priority" },
    { operation: "set_waiting", value: "waiting_on_other" },
    { operation: "set_cleared", value: "cleared" },
  ] as const;
  const outcomes = await Promise.all(
    operations.map((operation) =>
      coordinator.write({
        scopeKey: "scope-1",
        target: gmailTarget,
        operation,
        commit: (value) =>
          commits.push({
            version: value.version,
            waiting: value.waiting,
            cleared: value.cleared,
          }),
      }),
    ),
  );
  assert.deepEqual(calls, [
    "set_manual_priority",
    "set_waiting",
    "set_cleared",
  ]);
  assert.deepEqual(
    outcomes.map((outcome) => outcome.status),
    ["applied", "applied", "applied"],
  );
  assert.deepEqual(
    commits.map((value) => value.version),
    [1, 2, 3],
  );
});

await test("mailbox/session scope switch prevents projection into the new view", async () => {
  const pending = deferred<WriteResult>();
  let commits = 0;
  const coordinator = new PriorityWorkflowWriteCoordinator(
    fakeClient({ write: () => pending.promise }),
    "scope-1",
  );
  const write = coordinator.write({
    scopeKey: "scope-1",
    target: gmailTarget,
    operation: { operation: "set_manual_priority", value: "removed" },
    commit: () => {
      commits += 1;
    },
  });
  await Promise.resolve();
  coordinator.activateScope("scope-2");
  pending.resolve({
    ok: true,
    value: record(gmailIdentity, 1, { manualPriority: "removed" }),
  });
  assert.equal((await write).status, "superseded");
  assert.equal(commits, 0);
});

await test("ambiguous write performs one read reconciliation and no blind retry", async () => {
  let writes = 0;
  let reads = 0;
  const commits: number[] = [];
  const ambiguous: PriorityWorkflowAuthorityError = {
    code: "workflow_network_error",
    message: "Could not confirm.",
    ambiguous: true,
  };
  const coordinator = new PriorityWorkflowWriteCoordinator(
    fakeClient({
      write: async () => {
        writes += 1;
        return { ok: false, error: ambiguous };
      },
      read: async () => {
        reads += 1;
        return {
          ok: true,
          value: [
            record(gmailIdentity, 7, { waiting: "returned_reply" }),
          ],
        };
      },
    }),
    "scope-1",
  );
  const outcome = await coordinator.write({
    scopeKey: "scope-1",
    target: gmailTarget,
    operation: { operation: "set_waiting", value: "returned_reply" },
    commit: (value) => commits.push(value.version),
  });
  assert.equal(outcome.status, "reconciled");
  assert.equal(writes, 1);
  assert.equal(reads, 1);
  assert.deepEqual(commits, [7]);
});

if (failed > 0) {
  console.error(`\n${failed} priority workflow authority test(s) failed`);
  process.exit(1);
}
console.log(`\n${passed} priority workflow authority tests passed`);
}

void main().catch((error) => {
  console.error(error);
  process.exit(1);
});
