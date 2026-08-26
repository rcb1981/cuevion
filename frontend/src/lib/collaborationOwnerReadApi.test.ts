declare const process: { exitCode?: number };

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  __resetCollaborationOwnerReadApiForTests,
  COLLABORATION_OWNER_READ_ENDPOINT,
  isValidCollaborationOwnerReadId,
  readCollaborationForOwner,
} from "./collaborationOwnerReadApi";

type FetchCall = { input: RequestInfo | URL; init?: RequestInit };

const COLLABORATION_ID = "A".repeat(22);
const MESSAGE_ID = "B".repeat(22);
const NOW_MS = 1_800_000_000_000;

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

    await test("classifies 404, bounded 429 Retry-After, invalid Retry-After, and 503", async () => {
      const cases = [
        { response: response(404, {}), expected: { status: "not_found" } },
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
        { response: response(503, {}), expected: { status: "unavailable" } },
      ];

      for (const testCase of cases) {
        __resetCollaborationOwnerReadApiForTests();
        const calls: FetchCall[] = [];
        installFetch([csrfResponse("token"), testCase.response], calls);
        assert.deepEqual(await readCollaborationForOwner(COLLABORATION_ID), testCase.expected);
      }
    });

    await test("never uses browser persistence for CSRF", async () => {
      const source = fs.readFileSync(
        path.resolve(__dirname, "./collaborationOwnerReadApi.ts"),
        "utf8",
      );
      assert.equal(source.includes("localStorage"), false);
      assert.equal(source.includes("sessionStorage"), false);
      assert.equal(source.includes("indexedDB"), false);
    });
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalDateNow;
    __resetCollaborationOwnerReadApiForTests();
  }
}

void run();
