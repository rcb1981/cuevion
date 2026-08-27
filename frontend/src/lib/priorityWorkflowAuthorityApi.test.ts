import assert from "node:assert/strict";
import {
  PRIORITY_WORKFLOW_AUTHORITY_ENDPOINT,
  PriorityWorkflowAuthorityClient,
  type PriorityWorkflowRecord,
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

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
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
  overrides: Partial<PriorityWorkflowRecord> = {},
): PriorityWorkflowRecord {
  return {
    mailboxId: "mailbox-1",
    identity: gmailIdentity,
    manualPriority: "priority",
    cleared: "active",
    waiting: "absent",
    version: 1,
    updatedAt: 1_777_000_000_000,
    ...overrides,
  };
}

async function main() {
console.log("\npriorityWorkflowAuthorityApi");

await test("constructing/importing the client causes zero requests", () => {
  let requests = 0;
  new PriorityWorkflowAuthorityClient({
    fetch: async () => {
      requests += 1;
      return jsonResponse({});
    },
  });
  assert.equal(requests, 0);
});

await test("manual Priority uses the exact endpoint, session fetch, and body", async () => {
  const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
  const client = new PriorityWorkflowAuthorityClient({
    fetch: async (input, init) => {
      calls.push({ input, init });
      return jsonResponse({ ok: true, status: "updated", record: record() });
    },
  });
  const result = await client.setManualPriority({
    mailboxId: "mailbox-1",
    identity: gmailIdentity,
    value: "priority",
  });

  assert.equal(result.ok, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].input, PRIORITY_WORKFLOW_AUTHORITY_ENDPOINT);
  assert.equal(calls[0].init?.method, "POST");
  assert.equal(calls[0].init?.credentials, "include");
  assert.equal(calls[0].init?.cache, "no-store");
  assert.deepEqual(calls[0].init?.headers, {
    "Content-Type": "application/json",
  });
  const body = JSON.parse(String(calls[0].init?.body));
  assert.deepEqual(body, {
    operation: "set_manual_priority",
    mailboxId: "mailbox-1",
    identity: gmailIdentity,
    value: "priority",
  });
  assert.equal("workspaceId" in body, false);
  assert.equal("userId" in body, false);
});

await test("Remove Priority sends removed with exact Gmail identity", async () => {
  let body: unknown;
  const client = new PriorityWorkflowAuthorityClient({
    fetch: async (_input, init) => {
      body = JSON.parse(String(init?.body));
      return jsonResponse({
        ok: true,
        status: "updated",
        record: record({ manualPriority: "removed" }),
      });
    },
  });
  assert.equal(
    (
      await client.setManualPriority({
        mailboxId: "mailbox-1",
        identity: gmailIdentity,
        value: "removed",
      })
    ).ok,
    true,
  );
  assert.deepEqual(body, {
    operation: "set_manual_priority",
    mailboxId: "mailbox-1",
    identity: gmailIdentity,
    value: "removed",
  });
});

await test("Done and all waiting values use exact operations and IMAP identity", async () => {
  const bodies: unknown[] = [];
  let version = 0;
  const client = new PriorityWorkflowAuthorityClient({
    fetch: async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as {
        operation: string;
        value: string;
      };
      bodies.push(body);
      version += 1;
      return jsonResponse({
        ok: true,
        status: "updated",
        record: record({
          identity: imapIdentity,
          manualPriority: "none",
          cleared: body.operation === "set_cleared" ? "cleared" : "active",
          waiting:
            body.operation === "set_waiting"
              ? (body.value as PriorityWorkflowRecord["waiting"])
              : "absent",
          version,
        }),
      });
    },
  });
  await client.setCleared({
    mailboxId: "mailbox-1",
    identity: imapIdentity,
    value: "cleared",
  });
  for (const value of [
    "waiting_on_other",
    "returned_reply",
    "absent",
  ] as const) {
    await client.setWaiting({
      mailboxId: "mailbox-1",
      identity: imapIdentity,
      value,
    });
  }
  assert.deepEqual(
    bodies.map((body) => ({
      operation: (body as { operation: string }).operation,
      value: (body as { value: string }).value,
      identity: (body as { identity: unknown }).identity,
      mailboxId: (body as { mailboxId: string }).mailboxId,
    })),
    [
      { operation: "set_cleared", value: "cleared", identity: imapIdentity, mailboxId: "mailbox-1" },
      { operation: "set_waiting", value: "waiting_on_other", identity: imapIdentity, mailboxId: "mailbox-1" },
      { operation: "set_waiting", value: "returned_reply", identity: imapIdentity, mailboxId: "mailbox-1" },
      { operation: "set_waiting", value: "absent", identity: imapIdentity, mailboxId: "mailbox-1" },
    ],
  );
});

await test("malformed requests fail closed without a request", async () => {
  let requests = 0;
  const client = new PriorityWorkflowAuthorityClient({
    fetch: async () => {
      requests += 1;
      return jsonResponse({});
    },
  });
  const result = await client.setWaiting({
    mailboxId: "mailbox-1",
    identity: { ...imapIdentity, imapUid: "0" },
    value: "returned_reply",
  });
  assert.equal(result.ok, false);
  assert.equal(requests, 0);
});

await test("malformed and cross-mailbox success responses are rejected", async () => {
  const malformed = new PriorityWorkflowAuthorityClient({
    fetch: async () =>
      jsonResponse({ ok: true, status: "updated", record: { version: 1 } }),
  });
  const mismatched = new PriorityWorkflowAuthorityClient({
    fetch: async () =>
      jsonResponse({
        ok: true,
        status: "updated",
        record: record({ mailboxId: "mailbox-2" }),
      }),
  });
  assert.equal(
    (
      await malformed.setManualPriority({
        mailboxId: "mailbox-1",
        identity: gmailIdentity,
        value: "priority",
      })
    ).ok,
    false,
  );
  assert.equal(
    (
      await mismatched.setManualPriority({
        mailboxId: "mailbox-1",
        identity: gmailIdentity,
        value: "priority",
      })
    ).ok,
    false,
  );
});

await test("server failures use bounded client copy", async () => {
  const client = new PriorityWorkflowAuthorityClient({
    fetch: async () =>
      jsonResponse(
        {
          ok: false,
          error: {
            code: "mailbox_not_ready",
            message: "sensitive backend detail",
          },
        },
        409,
      ),
  });
  const result = await client.setCleared({
    mailboxId: "mailbox-1",
    identity: gmailIdentity,
    value: "cleared",
  });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.error.ambiguous, false);
    assert.doesNotMatch(result.error.message, /sensitive/);
    assert.match(result.error.message, /not ready/i);
  }
});

if (failed > 0) {
  console.error(`\n${failed} priority workflow API test(s) failed`);
  process.exit(1);
}
console.log(`\n${passed} priority workflow API tests passed`);
}

void main().catch((error) => {
  console.error(error);
  process.exit(1);
});
