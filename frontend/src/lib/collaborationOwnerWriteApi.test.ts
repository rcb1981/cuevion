declare const process: { exitCode?: number };
declare function require(name: string): unknown;

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  deriveCollaborationOwnerSourceLocator,
  type CollaborationOwnerSourceLocator,
} from "./collaborationOwnerSourceLocator";
import type * as ReadApi from "./collaborationOwnerReadApi";
import type * as WriteApi from "./collaborationOwnerWriteApi";

type FetchCall = { input: RequestInfo | URL; init?: RequestInit };
type FetchOutcome = Response | (() => Promise<Response>);

const COLLABORATION_ID = "A".repeat(22);
const NOW_MS = 1_800_000_000_000;

function response(
  status: number,
  payload: unknown,
  headers: Record<string, string> = {},
): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: {
      get: (name: string) =>
        Object.entries(headers).find(
          ([key]) => key.toLowerCase() === name.toLowerCase(),
        )?.[1] ?? null,
    },
    json: async () => payload,
  } as Response;
}

function csrfResponse(token: string) {
  return response(200, {
    ok: true,
    data: { csrfToken: token, expiresAt: NOW_MS / 1000 + 300 },
  });
}

function collaboration(
  mailboxId: string,
  state: "needs_review" | "needs_action" | "note_only" | "resolved" =
    "needs_review",
) {
  return {
    collaborationId: COLLABORATION_ID,
    mailboxId,
    state,
    createdAt: NOW_MS - 2_000,
    updatedAt: NOW_MS - 1_000,
    source: {
      subject: "Review this",
      senderDisplay: "Sender",
      fromDisplay: "sender@example.test",
      timestamp: "2027-01-15T08:00:00.000Z",
      bodyText: "Source body",
    },
    messages: [
      {
        id: "M".repeat(22),
        authorDisplayName: "Owner",
        authorRole: "Cuevion user",
        text: "Please review",
        visibility: "internal",
        timestamp: NOW_MS - 1_500,
      },
    ],
  } as const;
}

function createResponse(
  mailboxId: string,
  created: boolean,
  state: "needs_review" | "needs_action" | "note_only" | "resolved" =
    "needs_review",
) {
  return response(created ? 201 : 200, {
    ok: true,
    data: { created, collaboration: collaboration(mailboxId, state) },
  });
}

function readResponse(mailboxId: string) {
  return response(200, {
    ok: true,
    data: { collaboration: collaboration(mailboxId) },
  });
}

function installFetch(outcomes: FetchOutcome[], calls: FetchCall[]) {
  globalThis.fetch = (async (input, init) => {
    calls.push({ input, init });
    const next = outcomes.shift();
    assert.ok(next, `Unexpected fetch call to ${String(input)}`);
    return typeof next === "function" ? await next() : next;
  }) as typeof fetch;
}

function assertExactRequest(
  call: FetchCall,
  body: unknown,
  csrfToken?: string,
) {
  assert.equal(call.input, "/api/collaboration/owner");
  assert.equal(call.init?.method, "POST");
  assert.equal(call.init?.credentials, "same-origin");
  assert.equal(call.init?.cache, "no-store");
  assert.deepEqual(JSON.parse(String(call.init?.body)), body);
  assert.deepEqual(call.init?.headers, {
    Accept: "application/json",
    "Content-Type": "application/json",
    ...(csrfToken ? { "X-Cuevion-CSRF": csrfToken } : {}),
  });
  assert.equal(
    Object.keys(call.init?.headers as Record<string, string>).some(
      (name) => name.toLowerCase() === "x-cuevion-idempotency-key",
    ),
    false,
  );
}

function googleLocator(): CollaborationOwnerSourceLocator {
  const locator = deriveCollaborationOwnerSourceLocator({
    workspaceDataMode: "live",
    hasAuthenticatedMemberAuthority: true,
    managedMailbox: {
      id: "mailbox-google",
      provider: "google",
      connected: true,
      connectionStatus: "connected",
    },
    sourceMailboxId: "mailbox-google",
    trustedFolder: "INBOX",
    message: {
      id: "local-message-id-must-not-be-used",
      serverMailboxId: "mailbox-google",
      providerMessageId: "gmail-provider-message-id",
      providerFolder: "INBOX",
      threadIdentityContext: {
        mailboxId: "mailbox-google",
        provider: "google",
        folder: "INBOX",
      },
    },
  });
  assert.notEqual(locator, null);
  return locator as CollaborationOwnerSourceLocator;
}

function imapLocator(): CollaborationOwnerSourceLocator {
  const locator = deriveCollaborationOwnerSourceLocator({
    workspaceDataMode: "live",
    hasAuthenticatedMemberAuthority: true,
    managedMailbox: {
      id: "mailbox-imap",
      provider: "custom_imap",
      connected: true,
      connectionStatus: "connected",
    },
    sourceMailboxId: "mailbox-imap",
    trustedFolder: "INBOX",
    trustedUidValidity: "9001",
    message: {
      id: "local-imap-message-id",
      serverMailboxId: "mailbox-imap",
      providerFolder: "INBOX",
      uidValidity: "9001",
      imapUid: "42",
      threadIdentityContext: {
        mailboxId: "mailbox-imap",
        provider: "custom_imap",
        folder: "INBOX",
        uidValidity: "9001",
      },
    },
  });
  assert.notEqual(locator, null);
  return locator as CollaborationOwnerSourceLocator;
}

async function test(
  name: string,
  reset: () => void,
  callback: () => Promise<void>,
) {
  reset();
  try {
    await callback();
  } catch (error) {
    process.exitCode = 1;
    console.error(`FAIL: ${name}`);
    console.error(error);
  }
}

async function run() {
  const originalFetch = globalThis.fetch;
  const originalDateNow = Date.now;
  Date.now = () => NOW_MS;

  const importCalls: FetchCall[] = [];
  installFetch([], importCalls);
  const writeApi = require("./collaborationOwnerWriteApi") as typeof WriteApi;
  const readApi = require("./collaborationOwnerReadApi") as typeof ReadApi;
  assert.equal(importCalls.length, 0, "import must perform zero requests");
  const reset = readApi.__resetCollaborationOwnerReadApiForTests;

  try {
    await test("sends the exact Gmail create contract without idempotency", reset, async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [csrfResponse("csrf-token"), createResponse("mailbox-google", true)],
        calls,
      );
      assert.deepEqual(
        await writeApi.createCollaborationForOwner(
          googleLocator(),
          "needs_review",
        ),
        {
          status: "success",
          created: true,
          collaboration: collaboration("mailbox-google"),
        },
      );
      assert.equal(calls.length, 2);
      assertExactRequest(calls[0], { operation: "csrf" });
      assertExactRequest(
        calls[1],
        {
          operation: "create",
          mailboxId: "mailbox-google",
          sourceRef: { providerMessageId: "gmail-provider-message-id" },
          state: "needs_review",
        },
        "csrf-token",
      );
    });

    await test("sends the exact canonical IMAP Inbox create contract", reset, async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [csrfResponse("csrf-token"), createResponse("mailbox-imap", true, "needs_action")],
        calls,
      );
      assert.equal(
        (
          await writeApi.createCollaborationForOwner(
            imapLocator(),
            "needs_action",
          )
        ).status,
        "success",
      );
      assertExactRequest(
        calls[1],
        {
          operation: "create",
          mailboxId: "mailbox-imap",
          sourceRef: {
            folder: "INBOX",
            uidValidity: "9001",
            imapUid: "42",
          },
          state: "needs_action",
        },
        "csrf-token",
      );
    });

    await test("accepts note_only as the third exact initial state", reset, async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [
          csrfResponse("csrf-token"),
          createResponse("mailbox-google", true, "note_only"),
        ],
        calls,
      );
      assert.equal(
        (
          await writeApi.createCollaborationForOwner(
            googleLocator(),
            "note_only",
          )
        ).status,
        "success",
      );
      assert.equal(
        JSON.parse(String(calls[1].init?.body)).state,
        "note_only",
      );
    });

    await test("rejects forged, demo, and MailMessage.id-only locators before fetch", reset, async () => {
      const calls: FetchCall[] = [];
      installFetch([], calls);
      const forged = {
        mailboxId: "arbitrary-mailbox",
        sourceRef: { providerMessageId: "local-message-id" },
      } as CollaborationOwnerSourceLocator;
      assert.deepEqual(
        await writeApi.createCollaborationForOwner(forged, "needs_review"),
        { status: "invalid_source_locator" },
      );

      const idOnly = deriveCollaborationOwnerSourceLocator({
        workspaceDataMode: "live",
        hasAuthenticatedMemberAuthority: true,
        managedMailbox: {
          id: "mailbox-google",
          provider: "google",
          connected: true,
          connectionStatus: "connected",
        },
        sourceMailboxId: "mailbox-google",
        trustedFolder: "INBOX",
        message: {
          id: "must-not-substitute-for-provider-id",
          serverMailboxId: "mailbox-google",
        },
      });
      assert.equal(idOnly, null);
      assert.deepEqual(
        await writeApi.createCollaborationForOwner(
          idOnly as unknown as CollaborationOwnerSourceLocator,
          "needs_review",
        ),
        { status: "invalid_source_locator" },
      );

      const demo = deriveCollaborationOwnerSourceLocator({
        workspaceDataMode: "demo",
        hasAuthenticatedMemberAuthority: true,
        managedMailbox: {
          id: "mailbox-google",
          provider: "google",
          connected: true,
          connectionStatus: "connected",
        },
        sourceMailboxId: "mailbox-google",
        trustedFolder: "INBOX",
        message: {
          serverMailboxId: "mailbox-google",
          providerMessageId: "provider-id",
        },
      });
      assert.equal(demo, null);
      assert.equal(calls.length, 0);
    });

    await test("rejects resolved and malformed initial state before fetch", reset, async () => {
      const calls: FetchCall[] = [];
      installFetch([], calls);
      for (const state of ["resolved", "open", " needs_review"] as const) {
        assert.deepEqual(
          await writeApi.createCollaborationForOwner(
            googleLocator(),
            state as "needs_review",
          ),
          { status: "invalid_state" },
        );
      }
      assert.equal(calls.length, 0);
    });

    await test("reuses one cached CSRF token for new and duplicate create", reset, async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [
          csrfResponse("shared-token"),
          createResponse("mailbox-google", true),
          createResponse("mailbox-google", false, "resolved"),
        ],
        calls,
      );
      const locator = googleLocator();
      const first = await writeApi.createCollaborationForOwner(locator, "needs_review");
      const duplicate = await writeApi.createCollaborationForOwner(locator, "needs_review");
      assert.equal(first.status, "success");
      assert.deepEqual(duplicate, {
        status: "success",
        created: false,
        collaboration: collaboration("mailbox-google", "resolved"),
      });
      assert.equal(calls.length, 3);
      assert.equal(
        calls.filter(
          (call) => JSON.parse(String(call.init?.body)).operation === "csrf",
        ).length,
        1,
      );
    });

    await test("shares the same in-memory CSRF cache with owner read", reset, async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [
          csrfResponse("shared-token"),
          createResponse("mailbox-google", true),
          readResponse("mailbox-google"),
        ],
        calls,
      );
      await writeApi.createCollaborationForOwner(googleLocator(), "needs_review");
      assert.equal(
        (await readApi.readCollaborationForOwner(COLLABORATION_ID)).status,
        "success",
      );
      assert.equal(calls.length, 3);
      assert.equal(
        calls.filter(
          (call) => JSON.parse(String(call.init?.body)).operation === "csrf",
        ).length,
        1,
      );
    });

    await test("refreshes once after 403 and never retries a second 403", reset, async () => {
      const locator = googleLocator();
      const successCalls: FetchCall[] = [];
      installFetch(
        [
          csrfResponse("stale-token"),
          response(403, {}),
          csrfResponse("fresh-token"),
          createResponse("mailbox-google", true),
        ],
        successCalls,
      );
      assert.equal(
        (await writeApi.createCollaborationForOwner(locator, "needs_review")).status,
        "success",
      );
      assert.equal(successCalls.length, 4);
      assertExactRequest(
        successCalls[3],
        {
          operation: "create",
          mailboxId: "mailbox-google",
          sourceRef: { providerMessageId: "gmail-provider-message-id" },
          state: "needs_review",
        },
        "fresh-token",
      );

      reset();
      const deniedCalls: FetchCall[] = [];
      installFetch(
        [
          csrfResponse("stale-token"),
          response(403, {}),
          csrfResponse("fresh-token"),
          response(403, {}),
        ],
        deniedCalls,
      );
      assert.deepEqual(
        await writeApi.createCollaborationForOwner(locator, "needs_review"),
        { status: "forbidden" },
      );
      assert.equal(
        deniedCalls.filter(
          (call) => JSON.parse(String(call.init?.body)).operation === "create",
        ).length,
        2,
      );
    });

    await test("invalidates cached CSRF after 401", reset, async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [
          csrfResponse("rejected-token"),
          response(401, {}),
          csrfResponse("replacement-token"),
          createResponse("mailbox-google", true),
        ],
        calls,
      );
      const locator = googleLocator();
      assert.deepEqual(
        await writeApi.createCollaborationForOwner(locator, "needs_review"),
        { status: "unauthorized" },
      );
      assert.equal(
        (await writeApi.createCollaborationForOwner(locator, "needs_review")).status,
        "success",
      );
      assert.equal(calls.length, 4);
      assertExactRequest(
        calls[3],
        {
          operation: "create",
          mailboxId: "mailbox-google",
          sourceRef: { providerMessageId: "gmail-provider-message-id" },
          state: "needs_review",
        },
        "replacement-token",
      );
    });

    await test("preserves bounded Retry-After and exact public failure classes", reset, async () => {
      const cases = [
        { response: response(404, {}), expected: { status: "not_found" } },
        { response: response(409, {}), expected: { status: "conflict" } },
        {
          response: response(429, {}, { "Retry-After": "60" }),
          expected: { status: "rate_limited", retryAfterSeconds: 60 },
        },
        {
          response: response(429, {}, { "Retry-After": "61" }),
          expected: { status: "rate_limited" },
        },
        {
          response: response(503, {}),
          expected: { status: "service_unavailable" },
        },
        { response: response(500, {}), expected: { status: "internal_error" } },
      ];
      for (const testCase of cases) {
        reset();
        const calls: FetchCall[] = [];
        installFetch([csrfResponse("token"), testCase.response], calls);
        assert.deepEqual(
          await writeApi.createCollaborationForOwner(
            googleLocator(),
            "needs_review",
          ),
          testCase.expected,
        );
        assert.equal(calls.length, 2);
      }
    });

    await test("rejects malformed envelopes, DTOs, and status/created mismatches", reset, async () => {
      const malformed = [
        response(201, { ok: true, data: { created: true } }),
        response(201, {
          ok: true,
          data: {
            created: "true",
            collaboration: collaboration("mailbox-google"),
          },
        }),
        response(201, {
          ok: true,
          data: {
            created: true,
            collaboration: {
              ...collaboration("mailbox-google"),
              state: "open",
            },
          },
        }),
        response(201, {
          ok: true,
          data: {
            created: true,
            collaboration: collaboration("mailbox-google", "needs_action"),
          },
        }),
        response(201, {
          ok: true,
          data: {
            created: true,
            collaboration: {
              ...collaboration("mailbox-google"),
              updatedAt: "not-a-timestamp",
            },
          },
        }),
        response(201, {
          ok: true,
          data: {
            created: true,
            collaboration: {
              ...collaboration("mailbox-google"),
              source: {
                ...collaboration("mailbox-google").source,
                bodyText: 42,
              },
            },
          },
        }),
        response(201, {
          ok: true,
          data: {
            created: true,
            collaboration: {
              ...collaboration("mailbox-google"),
              messages: [
                {
                  ...collaboration("mailbox-google").messages[0],
                  visibility: "private",
                },
              ],
            },
          },
        }),
        response(201, {
          ok: true,
          data: {
            created: true,
            collaboration: {
              ...collaboration("mailbox-google"),
              revision: 1,
            },
          },
        }),
        response(201, {
          ok: true,
          data: {
            created: true,
            collaboration: collaboration("other-mailbox"),
          },
        }),
        response(200, {
          ok: true,
          data: {
            created: true,
            collaboration: collaboration("mailbox-google"),
          },
        }),
        response(201, {
          ok: true,
          data: {
            created: false,
            collaboration: collaboration("mailbox-google"),
          },
        }),
        response(202, {
          ok: true,
          data: {
            created: true,
            collaboration: collaboration("mailbox-google"),
          },
        }),
      ];

      for (const malformedResponse of malformed) {
        reset();
        const calls: FetchCall[] = [];
        installFetch([csrfResponse("token"), malformedResponse], calls);
        assert.deepEqual(
          await writeApi.createCollaborationForOwner(
            googleLocator(),
            "needs_review",
          ),
          { status: "invalid_response" },
        );
      }
    });

    await test("does not auto-retry an ambiguous network failure", reset, async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [
          csrfResponse("token"),
          async () => {
            throw new Error("synthetic lost response");
          },
          createResponse("mailbox-google", false),
        ],
        calls,
      );
      const locator = googleLocator();
      assert.deepEqual(
        await writeApi.createCollaborationForOwner(locator, "needs_review"),
        { status: "network_failure" },
      );
      assert.equal(calls.length, 2);

      const callerRetry = await writeApi.createCollaborationForOwner(
        locator,
        "needs_review",
      );
      assert.deepEqual(callerRetry, {
        status: "success",
        created: false,
        collaboration: collaboration("mailbox-google"),
      });
      assert.equal(calls.length, 3);
      assert.equal(
        calls.filter(
          (call) => JSON.parse(String(call.init?.body)).operation === "create",
        ).length,
        2,
      );
    });

    await test("is isolated to the visible WorkspaceShell start path", reset, async () => {
      const workspaceSource = fs.readFileSync(
        path.resolve(
          __dirname,
          "../components/workspace/WorkspaceShell.tsx",
        ),
        "utf8",
      );
      const startIndex = workspaceSource.indexOf(
        "const createMessageCollaboration = () =>",
      );
      const endIndex = workspaceSource.indexOf(
        "const sendCollaborationReply = (",
        startIndex,
      );
      assert.notEqual(startIndex, -1);
      assert.notEqual(endIndex, -1);
      const visibleStartRegion = workspaceSource.slice(startIndex, endIndex);

      assert.equal(workspaceSource.includes("collaborationOwnerWriteApi"), true);
      assert.equal(
        (workspaceSource.match(/createCollaborationForOwner\(/g) ?? []).length,
        1,
      );
      assert.equal(visibleStartRegion.includes("createCollaborationForOwner("), true);
      assert.equal(visibleStartRegion.includes("createCollaborationThread("), false);
      assert.equal(visibleStartRegion.includes("result.created"), false);
      assert.equal(
        visibleStartRegion.includes("collaboration: result.collaboration"),
        true,
      );

      const writeSource = fs.readFileSync(
        path.resolve(__dirname, "./collaborationOwnerWriteApi.ts"),
        "utf8",
      );
      const transportSource = fs.readFileSync(
        path.resolve(__dirname, "./collaborationOwnerApiTransport.ts"),
        "utf8",
      );
      for (const source of [writeSource, transportSource]) {
        for (const forbidden of [
          "localStorage",
          "sessionStorage",
          "indexedDB",
          "setInterval(",
          "setTimeout(",
        ]) {
          assert.equal(source.includes(forbidden), false);
        }
      }
    });
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalDateNow;
    reset();
  }
}

void run();
