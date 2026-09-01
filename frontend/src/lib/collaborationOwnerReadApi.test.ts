declare const process: { exitCode?: number };

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  __resetCollaborationOwnerReadApiForTests,
  COLLABORATION_OWNER_READ_ENDPOINT,
  isValidCollaborationOwnerReadId,
  lookupCollaborationForOwner,
  readCollaborationForOwner,
} from "./collaborationOwnerReadApi";

type FetchCall = { input: RequestInfo | URL; init?: RequestInit };

const COLLABORATION_ID = "A".repeat(22);
const MESSAGE_ID = "B".repeat(22);
const NOW_MS = 1_800_000_000_000;
const googleLocator = {
  mailboxId: "mailbox-1",
  sourceRef: { providerMessageId: "provider-message-1" },
} as const;

const collaboration = {
  collaborationId: COLLABORATION_ID,
  mailboxId: "mailbox-1",
  state: "needs_review",
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
      id: MESSAGE_ID,
      authorDisplayName: "Workspace Owner",
      authorRole: "Cuevion user",
      text: "Please review",
      visibility: "shared",
      timestamp: NOW_MS - 1_000,
    },
  ],
} as const;

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
        Object.entries(headers).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1] ??
        null,
    },
    json: async () => payload,
  } as Response;
}

function csrfResponse(token: string, expiresAt = NOW_MS / 1000 + 300) {
  return response(200, {
    ok: true,
    data: { csrfToken: token, expiresAt },
  });
}

function readResponse(value: unknown = collaboration) {
  return response(200, {
    ok: true,
    data: { collaboration: value },
  });
}

function lookupResponse(collaborationId: unknown = COLLABORATION_ID) {
  return response(200, {
    ok: true,
    data: { collaborationId },
  });
}

function installFetch(responses: Array<Response | Promise<Response>>, calls: FetchCall[]) {
  globalThis.fetch = (async (input, init) => {
    calls.push({ input, init });
    const next = responses.shift();
    assert.ok(next, `Unexpected fetch call to ${String(input)}`);
    return await next;
  }) as typeof fetch;
}

function assertExactRequest(call: FetchCall, body: unknown, csrfToken?: string) {
  assert.equal(call.input, COLLABORATION_OWNER_READ_ENDPOINT);
  assert.equal(call.init?.method, "POST");
  assert.equal(call.init?.credentials, "same-origin");
  assert.equal(call.init?.cache, "no-store");
  assert.deepEqual(JSON.parse(String(call.init?.body)), body);
  assert.deepEqual(call.init?.headers, {
    Accept: "application/json",
    "Content-Type": "application/json",
    ...(csrfToken ? { "X-Cuevion-CSRF": csrfToken } : {}),
  });
}

async function test(name: string, callback: () => Promise<void>) {
  __resetCollaborationOwnerReadApiForTests();
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
  let nowMs = NOW_MS;
  Date.now = () => nowMs;

  try {
    await test("uses the exact CSRF and owner-read POST contracts", async () => {
      const calls: FetchCall[] = [];
      installFetch([csrfResponse("csrf-secret"), readResponse()], calls);

      assert.deepEqual(await readCollaborationForOwner(COLLABORATION_ID), {
        status: "success",
        collaboration,
      });
      assert.equal(calls.length, 2);
      assertExactRequest(calls[0], { operation: "csrf" });
      assertExactRequest(
        calls[1],
        { operation: "read", collaborationId: COLLABORATION_ID },
        "csrf-secret",
      );
    });

    await test("uses the exact lookup POST contract without browser-supplied authority", async () => {
      const calls: FetchCall[] = [];
      installFetch([csrfResponse("csrf-secret"), lookupResponse()], calls);

      assert.deepEqual(await lookupCollaborationForOwner(googleLocator), {
        status: "success",
        collaborationId: COLLABORATION_ID,
      });
      assert.equal(calls.length, 2);
      assertExactRequest(calls[0], { operation: "csrf" });
      assertExactRequest(
        calls[1],
        {
          operation: "lookup",
          mailboxId: "mailbox-1",
          sourceRef: { providerMessageId: "provider-message-1" },
        },
        "csrf-secret",
      );
      const body = JSON.parse(String(calls[1].init?.body)) as Record<string, unknown>;
      for (const forbiddenField of ["provider", "ownerEmail", "workspaceId", "collaborationId"]) {
        assert.equal(Object.prototype.hasOwnProperty.call(body, forbiddenField), false);
      }
    });

    await test("rejects invalid source locators before any request", async () => {
      const calls: FetchCall[] = [];
      installFetch([], calls);
      const invalidLocators = [
        { ...googleLocator, mailboxId: " mailbox-1" },
        { ...googleLocator, sourceRef: { providerMessageId: "provider id" } },
        { ...googleLocator, sourceRef: { providerMessageId: "provider-id", provider: "google" } },
        { mailboxId: "mailbox-1", sourceRef: { folder: "Archive", uidValidity: "1", imapUid: "2" } },
        { mailboxId: "mailbox-1", sourceRef: { folder: "INBOX", uidValidity: "01", imapUid: "2" } },
      ];
      for (const locator of invalidLocators) {
        assert.deepEqual(
          await lookupCollaborationForOwner(locator as typeof googleLocator),
          { status: "invalid_source_locator" },
        );
      }
      assert.equal(calls.length, 0);
    });

    await test("accepts only the exact opaque lookup success envelope", async () => {
      const malformedPayloads = [
        { ok: true, data: { collaborationId: "short" } },
        { ok: true, data: { collaborationId: COLLABORATION_ID, provider: "google" } },
        { ok: true, data: { collaborationId: COLLABORATION_ID }, extra: true },
        { ok: true, collaborationId: COLLABORATION_ID },
      ];
      for (const payload of malformedPayloads) {
        __resetCollaborationOwnerReadApiForTests();
        const calls: FetchCall[] = [];
        installFetch([csrfResponse("token"), response(200, payload)], calls);
        assert.deepEqual(await lookupCollaborationForOwner(googleLocator), {
          status: "invalid_response",
        });
      }
    });

    await test("shares one memory-only CSRF cache across lookup and read", async () => {
      const calls: FetchCall[] = [];
      installFetch([csrfResponse("shared-token"), lookupResponse(), readResponse()], calls);
      assert.equal((await lookupCollaborationForOwner(googleLocator)).status, "success");
      assert.equal((await readCollaborationForOwner(COLLABORATION_ID)).status, "success");
      assert.equal(calls.length, 3);
      assert.equal(
        calls.filter((call) => JSON.parse(String(call.init?.body)).operation === "csrf").length,
        1,
      );
    });

    await test("deduplicates concurrent lookup/read CSRF bootstrap", async () => {
      const calls: FetchCall[] = [];
      let resolveBootstrap!: (value: Response) => void;
      const pendingBootstrap = new Promise<Response>((resolve) => {
        resolveBootstrap = resolve;
      });
      installFetch([pendingBootstrap, lookupResponse(), readResponse()], calls);
      const lookup = lookupCollaborationForOwner(googleLocator);
      const read = readCollaborationForOwner(COLLABORATION_ID);
      await Promise.resolve();
      assert.equal(calls.length, 1);
      resolveBootstrap(csrfResponse("shared-token"));
      await Promise.all([lookup, read]);
      assert.equal(calls.length, 3);
    });

    await test("refreshes and retries lookup after one 403, then stops", async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [
          csrfResponse("stale-token"),
          response(403, {}),
          csrfResponse("fresh-token"),
          response(403, {}),
        ],
        calls,
      );
      assert.deepEqual(await lookupCollaborationForOwner(googleLocator), {
        status: "forbidden",
      });
      assert.equal(calls.length, 4);
      assert.equal(
        calls.filter((call) => JSON.parse(String(call.init?.body)).operation === "lookup").length,
        2,
      );
    });

    await test("classifies lookup access, absence, conflict, retry, and service failures", async () => {
      const cases = [
        { response: response(401, {}), expected: { status: "unauthorized" } },
        { response: response(404, {}), expected: { status: "not_found" } },
        { response: response(409, {}), expected: { status: "conflict" } },
        {
          response: response(429, {}, { "Retry-After": "60" }),
          expected: { status: "rate_limited", retryAfterSeconds: 60 },
        },
        { response: response(503, {}), expected: { status: "service_unavailable" } },
        { response: response(500, {}), expected: { status: "internal_error" } },
      ];
      for (const testCase of cases) {
        __resetCollaborationOwnerReadApiForTests();
        const calls: FetchCall[] = [];
        installFetch([csrfResponse("token"), testCase.response], calls);
        assert.deepEqual(await lookupCollaborationForOwner(googleLocator), testCase.expected);
      }
    });

    await test("rejects non-opaque collaboration IDs before any request", async () => {
      const calls: FetchCall[] = [];
      installFetch([], calls);
      for (const value of [
        "",
        "A".repeat(21),
        "A".repeat(129),
        `${"A".repeat(22)} `,
        "mailbox-1/message-1",
        "contains.dot.invalid-id",
      ]) {
        assert.equal(isValidCollaborationOwnerReadId(value), false);
        assert.deepEqual(await readCollaborationForOwner(value), {
          status: "invalid_collaboration_id",
        });
      }
      assert.equal(isValidCollaborationOwnerReadId(`${"A".repeat(20)}_-`), true);
      assert.equal(calls.length, 0);
    });

    await test("parses only the exact v2 owner DTO", async () => {
      const calls: FetchCall[] = [];
      installFetch([csrfResponse("token"), readResponse()], calls);
      const result = await readCollaborationForOwner(COLLABORATION_ID);
      assert.equal(result.status, "success");
      if (result.status === "success") {
        assert.deepEqual(Object.keys(result.collaboration).sort(), [
          "collaborationId",
          "createdAt",
          "mailboxId",
          "messages",
          "source",
          "state",
          "updatedAt",
        ]);
        const serialized = JSON.stringify(result.collaboration);
        for (const legacyField of ["participants", "requester", "mentions", "preview"])
          assert.equal(serialized.includes(`\"${legacyField}\"`), false);
      }
    });

    await test("fails closed for partial, malformed, or legacy-extended success DTOs", async () => {
      const malformedValues = [
        { ...collaboration, mailboxId: undefined },
        { ...collaboration, state: "open" },
        { ...collaboration, participants: [] },
        { ...collaboration, source: { ...collaboration.source, bodyText: 42 } },
        { ...collaboration, messages: [{ ...collaboration.messages[0], visibility: "public" }] },
      ];

      for (const malformed of malformedValues) {
        __resetCollaborationOwnerReadApiForTests();
        const calls: FetchCall[] = [];
        installFetch([csrfResponse("token"), readResponse(malformed)], calls);
        assert.deepEqual(await readCollaborationForOwner(COLLABORATION_ID), {
          status: "invalid_response",
        });
      }
    });

    await test("reuses its memory-only token while it is fresh", async () => {
      const calls: FetchCall[] = [];
      installFetch([csrfResponse("one-token"), readResponse(), readResponse()], calls);
      await readCollaborationForOwner(COLLABORATION_ID);
      await readCollaborationForOwner(COLLABORATION_ID);
      assert.equal(calls.length, 3);
      assert.equal(
        calls.filter((call) => JSON.parse(String(call.init?.body)).operation === "csrf").length,
        1,
      );
    });

    await test("refreshes a token inside the bounded early-expiry margin", async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [csrfResponse("old-token", NOW_MS / 1000 + 20), readResponse(), csrfResponse("new-token", NOW_MS / 1000 + 400), readResponse()],
        calls,
      );
      await readCollaborationForOwner(COLLABORATION_ID);
      nowMs += 10_000;
      await readCollaborationForOwner(COLLABORATION_ID);
      assert.equal(calls.length, 4);
      assertExactRequest(calls[3], { operation: "read", collaborationId: COLLABORATION_ID }, "new-token");
    });

    await test("deduplicates simultaneous CSRF bootstrap requests", async () => {
      const calls: FetchCall[] = [];
      let resolveBootstrap!: (value: Response) => void;
      const pendingBootstrap = new Promise<Response>((resolve) => {
        resolveBootstrap = resolve;
      });
      installFetch([pendingBootstrap, readResponse(), readResponse()], calls);

      const first = readCollaborationForOwner(COLLABORATION_ID);
      const second = readCollaborationForOwner("C".repeat(22));
      await Promise.resolve();
      assert.equal(calls.length, 1);
      resolveBootstrap(csrfResponse("shared-token"));
      await Promise.all([first, second]);
      assert.equal(calls.length, 3);
      assert.equal(
        calls.filter((call) => JSON.parse(String(call.init?.body)).operation === "csrf").length,
        1,
      );
    });

    await test("invalidates CSRF and retries a forbidden read exactly once", async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [
          csrfResponse("stale-token"),
          response(403, { ok: false, error: { code: "forbidden" } }),
          csrfResponse("fresh-token"),
          response(403, { ok: false, error: { code: "forbidden" } }),
        ],
        calls,
      );
      assert.deepEqual(await readCollaborationForOwner(COLLABORATION_ID), {
        status: "forbidden",
      });
      assert.equal(calls.length, 4);
      assert.equal(
        calls.filter((call) => JSON.parse(String(call.init?.body)).operation === "read").length,
        2,
      );
    });

    await test("clears cached CSRF state on 401", async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [
          csrfResponse("rejected-token"),
          response(401, { ok: false, error: { code: "unauthorized" } }),
          csrfResponse("replacement-token"),
          readResponse(),
        ],
        calls,
      );
      assert.deepEqual(await readCollaborationForOwner(COLLABORATION_ID), {
        status: "unauthorized",
      });
      assert.equal((await readCollaborationForOwner(COLLABORATION_ID)).status, "success");
      assert.equal(calls.length, 4);
      assertExactRequest(calls[3], { operation: "read", collaborationId: COLLABORATION_ID }, "replacement-token");
    });

    await test("classifies bounded read failures without collapsing their semantics", async () => {
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
          response: response(429, {}, { "Retry-After": " 5" }),
          expected: { status: "rate_limited" },
        },
        { response: response(503, {}), expected: { status: "service_unavailable" } },
        { response: response(500, {}), expected: { status: "internal_error" } },
      ];

      for (const testCase of cases) {
        __resetCollaborationOwnerReadApiForTests();
        const calls: FetchCall[] = [];
        installFetch([csrfResponse("token"), testCase.response], calls);
        assert.deepEqual(await readCollaborationForOwner(COLLABORATION_ID), testCase.expected);
      }
    });

    await test("keeps network and invalid-response failures bounded", async () => {
      const networkCalls: FetchCall[] = [];
      installFetch([Promise.reject(new Error("private network detail"))], networkCalls);
      assert.deepEqual(await readCollaborationForOwner(COLLABORATION_ID), {
        status: "network_failure",
      });

      __resetCollaborationOwnerReadApiForTests();
      const invalidCalls: FetchCall[] = [];
      installFetch(
        [
          csrfResponse("token"),
          response(200, {
            ok: false,
            error: { message: "private response detail" },
          }),
        ],
        invalidCalls,
      );
      const result = await readCollaborationForOwner(COLLABORATION_ID);
      assert.deepEqual(result, { status: "invalid_response" });
      assert.equal(JSON.stringify(result).includes("private response detail"), false);
    });

    await test("introduces no owner write operation", async () => {
      const calls: FetchCall[] = [];
      installFetch(
        [csrfResponse("token"), lookupResponse(), readResponse()],
        calls,
      );
      await lookupCollaborationForOwner(googleLocator);
      await readCollaborationForOwner(COLLABORATION_ID);
      const operations = calls.map(
        (call) =>
          (JSON.parse(String(call.init?.body)) as { operation: string }).operation,
      );
      assert.deepEqual(operations, ["csrf", "lookup", "read"]);
      for (const forbiddenOperation of [
        "create",
        "append_shared",
        "append_internal",
        "resolve",
        "reopen",
      ]) {
        assert.equal(operations.includes(forbiddenOperation), false);
      }
    });

    await test("never uses browser persistence for CSRF", async () => {
      const sources = [
        "./collaborationOwnerReadApi.ts",
        "./collaborationOwnerApiTransport.ts",
      ].map((filename) =>
        fs.readFileSync(path.resolve(__dirname, filename), "utf8"),
      );
      for (const source of sources) {
        assert.equal(source.includes("localStorage"), false);
        assert.equal(source.includes("sessionStorage"), false);
        assert.equal(source.includes("indexedDB"), false);
      }
    });
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalDateNow;
    __resetCollaborationOwnerReadApiForTests();
  }
}

void run();
