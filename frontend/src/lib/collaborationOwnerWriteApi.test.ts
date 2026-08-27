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

function appendedMessage(
  visibility: "internal" | "shared",
  text: string,
) {
  return {
    id: "N".repeat(22),
    authorDisplayName: "Owner",
    authorRole: "Cuevion user",
    text,
    timestamp: NOW_MS,
    visibility,
  } as const;
}

function appendResponse(
  visibility: "internal" | "shared",
  text: string,
) {
  return response(200, {
    ok: true,
    data: {
      message: appendedMessage(visibility, text),
      updatedAt: NOW_MS,
    },
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
  idempotencyKey?: string,
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
    ...(idempotencyKey
      ? { "X-Cuevion-Idempotency-Key": idempotencyKey }
      : {}),
  });
}

function idempotencyKeyFrom(call: FetchCall): string {
  const headers = call.init?.headers as Record<string, string>;
  const key = headers["X-Cuevion-Idempotency-Key"];
  assert.equal(typeof key, "string");
  return key;
}

function assertCanonicalIdempotencyKey(key: string) {
  assert.equal(key.length, 43);
  assert.equal(/^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/.test(key), true);
  const decoded = atob(
    key.replace(/-/g, "+").replace(/_/g, "/") + "=",
  );
  assert.equal(decoded.length, 32);
}

function requireReadyOperation(
  result: WriteApi.CollaborationOwnerAppendPreparationResult,
): WriteApi.CollaborationOwnerAppendOperation {
  assert.equal(result.status, "ready");
  if (result.status !== "ready") {
    throw new Error("Expected a prepared append operation");
  }
  return result.operation;
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

    await test("sends the exact append_internal contract with a hidden canonical key", reset, async () => {
      const calls: FetchCall[] = [];
      const text = "Private\nowner note\tkept";
      installFetch(
        [csrfResponse("csrf-token"), appendResponse("internal", text)],
        calls,
      );
      const operation = requireReadyOperation(
        writeApi.prepareInternalCollaborationMessageForOwner(
          COLLABORATION_ID,
          text,
        ),
      );
      assert.deepEqual(Reflect.ownKeys(operation), ["execute"]);
      assert.equal(JSON.stringify(operation), "{}");
      assert.deepEqual(await operation.execute(), {
        status: "success",
        message: appendedMessage("internal", text),
        updatedAt: NOW_MS,
      });

      assert.equal(calls.length, 2);
      assertExactRequest(calls[0], { operation: "csrf" });
      const key = idempotencyKeyFrom(calls[1]);
      assertCanonicalIdempotencyKey(key);
      assertExactRequest(
        calls[1],
        {
          operation: "append_internal",
          collaborationId: COLLABORATION_ID,
          text,
        },
        "csrf-token",
        key,
      );
    });

    await test("sends the exact append_shared contract", reset, async () => {
      const calls: FetchCall[] = [];
      const text = "Visible reply";
      installFetch(
        [csrfResponse("csrf-token"), appendResponse("shared", text)],
        calls,
      );
      const operation = requireReadyOperation(
        writeApi.prepareSharedCollaborationMessageForOwner(
          COLLABORATION_ID,
          text,
        ),
      );
      assert.equal((await operation.execute()).status, "success");
      const key = idempotencyKeyFrom(calls[1]);
      assertCanonicalIdempotencyKey(key);
      assertExactRequest(
        calls[1],
        {
          operation: "append_shared",
          collaborationId: COLLABORATION_ID,
          text,
        },
        "csrf-token",
        key,
      );
    });

    await test("reuses one key for the same logical append and payload", reset, async () => {
      const calls: FetchCall[] = [];
      const text = "Retry-safe note";
      installFetch(
        [
          csrfResponse("csrf-token"),
          appendResponse("internal", text),
          appendResponse("internal", text),
        ],
        calls,
      );
      const operation = requireReadyOperation(
        writeApi.prepareInternalCollaborationMessageForOwner(
          COLLABORATION_ID,
          text,
        ),
      );
      assert.equal((await operation.execute()).status, "success");
      assert.equal((await operation.execute()).status, "success");
      assert.deepEqual(calls[1].init?.body, calls[2].init?.body);
      assert.equal(idempotencyKeyFrom(calls[1]), idempotencyKeyFrom(calls[2]));
    });

    await test("keeps the append key across the automatic CSRF retry", reset, async () => {
      const calls: FetchCall[] = [];
      const text = "CSRF retry note";
      installFetch(
        [
          csrfResponse("stale-token"),
          response(403, {}),
          csrfResponse("fresh-token"),
          appendResponse("internal", text),
        ],
        calls,
      );
      const operation = requireReadyOperation(
        writeApi.prepareInternalCollaborationMessageForOwner(
          COLLABORATION_ID,
          text,
        ),
      );
      assert.equal((await operation.execute()).status, "success");
      assert.equal(calls.length, 4);
      assert.equal(idempotencyKeyFrom(calls[1]), idempotencyKeyFrom(calls[3]));
      assert.equal(
        (calls[1].init?.headers as Record<string, string>)["X-Cuevion-CSRF"],
        "stale-token",
      );
      assert.equal(
        (calls[3].init?.headers as Record<string, string>)["X-Cuevion-CSRF"],
        "fresh-token",
      );
    });

    await test("does not auto-retry lost append responses and supports explicit same-key retry", reset, async () => {
      const calls: FetchCall[] = [];
      const text = "Lost response note";
      installFetch(
        [
          csrfResponse("csrf-token"),
          async () => {
            throw new Error("synthetic ambiguous append failure");
          },
          appendResponse("internal", text),
        ],
        calls,
      );
      const operation = requireReadyOperation(
        writeApi.prepareInternalCollaborationMessageForOwner(
          COLLABORATION_ID,
          text,
        ),
      );
      assert.deepEqual(await operation.execute(), { status: "network_failure" });
      assert.equal(calls.length, 2);

      assert.equal((await operation.execute()).status, "success");
      assert.equal(calls.length, 3);
      assert.deepEqual(calls[1].init?.body, calls[2].init?.body);
      assert.equal(idempotencyKeyFrom(calls[1]), idempotencyKeyFrom(calls[2]));
    });

    await test("gives concurrent logical appends independent keys", reset, async () => {
      const calls: FetchCall[] = [];
      const text = "Independent note";
      installFetch(
        [
          csrfResponse("shared-token"),
          appendResponse("internal", text),
          appendResponse("internal", text),
        ],
        calls,
      );
      const first = requireReadyOperation(
        writeApi.prepareInternalCollaborationMessageForOwner(
          COLLABORATION_ID,
          text,
        ),
      );
      const second = requireReadyOperation(
        writeApi.prepareInternalCollaborationMessageForOwner(
          COLLABORATION_ID,
          text,
        ),
      );
      const results = await Promise.all([first.execute(), second.execute()]);
      assert.deepEqual(
        results.map((result) => result.status),
        ["success", "success"],
      );
      assert.equal(calls.length, 3);
      assert.notEqual(idempotencyKeyFrom(calls[1]), idempotencyKeyFrom(calls[2]));
    });

    await test("validates append IDs, controls, empty text, and UTF-8 byte limits", reset, async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [csrfResponse("csrf-token"), appendResponse("internal", "")],
        calls,
      );
      for (const invalidId of ["", "A".repeat(21), "A".repeat(129), "A A"]) {
        assert.deepEqual(
          writeApi.prepareInternalCollaborationMessageForOwner(invalidId, "note"),
          { status: "invalid_collaboration_id" },
        );
      }

      for (const invalidText of [
        "hidden\u0000control",
        "hidden\u200bformat",
        `surrogate${String.fromCharCode(0xd800)}`,
        "😀".repeat(4096) + "a",
      ]) {
        assert.deepEqual(
          writeApi.prepareInternalCollaborationMessageForOwner(
            COLLABORATION_ID,
            invalidText,
          ),
          { status: "invalid_text" },
        );
      }

      assert.equal(
        writeApi.prepareInternalCollaborationMessageForOwner(
          COLLABORATION_ID,
          "😀".repeat(4096),
        ).status,
        "ready",
      );
      assert.equal(
        writeApi.prepareInternalCollaborationMessageForOwner(
          COLLABORATION_ID,
          "line one\r\nline two\t",
        ).status,
        "ready",
      );
      const empty = requireReadyOperation(
        writeApi.prepareInternalCollaborationMessageForOwner(
          COLLABORATION_ID,
          "",
        ),
      );
      assert.equal((await empty.execute()).status, "success");
      assert.equal(JSON.parse(String(calls[1].init?.body)).text, "");
      assert.equal(calls.length, 2);
    });

    await test("rejects malformed and contradictory append success DTOs", reset, async () => {
      const text = "Strict response";
      const message = appendedMessage("internal", text);
      const malformed = [
        response(201, { ok: true, data: { message, updatedAt: NOW_MS } }),
        response(200, { ok: true, data: { message, updatedAt: NOW_MS, extra: 1 } }),
        response(200, { ok: true, data: { updatedAt: NOW_MS } }),
        response(200, {
          ok: true,
          data: { message: { ...message, extra: 1 }, updatedAt: NOW_MS },
        }),
        response(200, {
          ok: true,
          data: { message: { ...message, id: "bad" }, updatedAt: NOW_MS },
        }),
        response(200, {
          ok: true,
          data: {
            message: { ...message, authorDisplayName: "" },
            updatedAt: NOW_MS,
          },
        }),
        response(200, {
          ok: true,
          data: {
            message: { ...message, authorRole: "Guest reviewer" },
            updatedAt: NOW_MS,
          },
        }),
        response(200, {
          ok: true,
          data: { message: { ...message, text: "changed" }, updatedAt: NOW_MS },
        }),
        response(200, {
          ok: true,
          data: {
            message: { ...message, visibility: "shared" },
            updatedAt: NOW_MS,
          },
        }),
        response(200, {
          ok: true,
          data: {
            message: { ...message, timestamp: "not-a-timestamp" },
            updatedAt: NOW_MS,
          },
        }),
        response(200, {
          ok: true,
          data: { message, updatedAt: "not-a-timestamp" },
        }),
        response(200, {
          ok: true,
          data: { message, updatedAt: NOW_MS + 1 },
        }),
      ];

      for (const malformedResponse of malformed) {
        reset();
        const calls: FetchCall[] = [];
        installFetch([csrfResponse("token"), malformedResponse], calls);
        const operation = requireReadyOperation(
          writeApi.prepareInternalCollaborationMessageForOwner(
            COLLABORATION_ID,
            text,
          ),
        );
        assert.deepEqual(await operation.execute(), {
          status: "invalid_response",
        });
      }
    });

    await test("preserves every append failure class without backend details", reset, async () => {
      const cases: Array<{
        outcomes: FetchOutcome[];
        expected: WriteApi.CollaborationOwnerAppendResult;
      }> = [
        {
          outcomes: [csrfResponse("token"), response(401, {})],
          expected: { status: "unauthorized" },
        },
        {
          outcomes: [
            csrfResponse("stale"),
            response(403, {}),
            csrfResponse("fresh"),
            response(403, {}),
          ],
          expected: { status: "forbidden" },
        },
        {
          outcomes: [csrfResponse("token"), response(404, {})],
          expected: { status: "not_found" },
        },
        {
          outcomes: [csrfResponse("token"), response(409, {})],
          expected: { status: "conflict" },
        },
        {
          outcomes: [
            csrfResponse("token"),
            response(429, {}, { "Retry-After": "30" }),
          ],
          expected: { status: "rate_limited", retryAfterSeconds: 30 },
        },
        {
          outcomes: [csrfResponse("token"), response(503, {})],
          expected: { status: "service_unavailable" },
        },
        {
          outcomes: [csrfResponse("token"), response(500, {})],
          expected: { status: "internal_error" },
        },
        {
          outcomes: [
            csrfResponse("token"),
            async () => {
              throw new Error("synthetic network failure");
            },
          ],
          expected: { status: "network_failure" },
        },
        {
          outcomes: [csrfResponse("token"), response(200, { ok: true })],
          expected: { status: "invalid_response" },
        },
      ];

      for (const testCase of cases) {
        reset();
        const calls: FetchCall[] = [];
        installFetch([...testCase.outcomes], calls);
        const operation = requireReadyOperation(
          writeApi.prepareSharedCollaborationMessageForOwner(
            COLLABORATION_ID,
            "Failure case",
          ),
        );
        assert.deepEqual(await operation.execute(), testCase.expected);
      }
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
      assert.equal(
        workspaceSource.includes(
          "prepareInternalCollaborationMessageForOwner",
        ),
        false,
      );
      assert.equal(
        workspaceSource.includes("prepareSharedCollaborationMessageForOwner"),
        false,
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
