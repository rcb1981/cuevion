declare const process: { exitCode?: number };

import assert from "node:assert/strict";
import {
  bootstrapGuestSession,
  COLLABORATION_GUEST_CSRF_HEADER,
  COLLABORATION_GUEST_ENDPOINT,
  createCollaborationGuestReplyIdempotencyKey,
  exchangeGuestInvitation,
  logoutGuestCollaboration,
  parseCollaborationGuestDto,
  readGuestCollaboration,
  replyToGuestCollaboration,
} from "./collaborationGuestApi";

type FetchCall = { input: RequestInfo | URL; init?: RequestInit };

const TOKEN = "A".repeat(43);
const CSRF = "C".repeat(43);
const COLLABORATION_ID = "D".repeat(22);
const MESSAGE_ID = "M".repeat(22);
const IDEMPOTENCY_KEY = "I".repeat(42) + "A";
const session = {
  collaborationId: COLLABORATION_ID,
  guestDisplayName: "External Reviewer",
  allowedActions: ["read", "reply"],
  identityAssurance: "link_possession",
  expiresAt: 1_900_000_000,
};
const collaboration = {
  collaborationId: COLLABORATION_ID,
  state: "needs_review",
  updatedAt: 1_800_000_000_000,
  allowedActions: ["read", "reply"],
  sharedSource: {
    subject: "Please review",
    senderDisplay: "Cuevion Sender",
    fromDisplay: "sender@example.test",
    timestamp: "2027-01-15T08:00:00.000Z",
    bodyText: "Shared source body",
  },
  messages: [
    {
      id: MESSAGE_ID,
      authorDisplayName: "Workspace Owner",
      authorRole: "Cuevion user",
      text: "Please take a look.",
      timestamp: 1_800_000_000_000,
    },
  ],
};

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

function success(data: Record<string, unknown>) {
  return response(200, { ok: true, data });
}

function installFetch(responses: Response[], calls: FetchCall[]) {
  globalThis.fetch = (async (input, init) => {
    calls.push({ input, init });
    const next = responses.shift();
    assert.ok(next, `Unexpected request to ${String(input)}`);
    return next;
  }) as typeof fetch;
}

function assertBaseRequest(call: FetchCall) {
  assert.equal(call.input, COLLABORATION_GUEST_ENDPOINT);
  assert.equal(String(call.input).includes("?"), false);
  assert.equal(call.init?.credentials, "include");
  assert.equal(call.init?.cache, "no-store");
}

function assertPost(
  call: FetchCall,
  body: Record<string, unknown>,
  csrf?: string,
) {
  assertBaseRequest(call);
  assert.equal(call.init?.method, "POST");
  assert.deepEqual(JSON.parse(String(call.init?.body)), body);
  assert.deepEqual(call.init?.headers, {
    Accept: "application/json",
    "Content-Type": "application/json",
    ...(csrf ? { [COLLABORATION_GUEST_CSRF_HEADER]: csrf } : {}),
  });
}

async function run() {
  const originalFetch = globalThis.fetch;
  try {
    {
      const calls: FetchCall[] = [];
      installFetch(
        [
          success({ session, csrfToken: CSRF }),
          success({ session, csrfToken: CSRF }),
          success({ collaboration }),
          success({ collaboration }),
          success({ loggedOut: true }),
        ],
        calls,
      );

      assert.equal(
        (await exchangeGuestInvitation(TOKEN, "External Reviewer")).status,
        "success",
      );
      assert.equal((await bootstrapGuestSession()).status, "success");
      assert.deepEqual(await readGuestCollaboration(), {
        status: "success",
        collaboration,
      });
      assert.deepEqual(await replyToGuestCollaboration("My reply", CSRF, IDEMPOTENCY_KEY), {
        status: "success",
        collaboration,
      });
      assert.deepEqual(await logoutGuestCollaboration(CSRF), {
        status: "success",
        loggedOut: true,
      });

      assertPost(calls[0], {
        operation: "exchange",
        token: TOKEN,
        displayName: "External Reviewer",
      });
      assertPost(calls[1], { operation: "bootstrap" });
      assertBaseRequest(calls[2]);
      assert.equal(calls[2].init?.method, "GET");
      assert.equal(calls[2].init?.body, undefined);
      assert.deepEqual(calls[2].init?.headers, { Accept: "application/json" });
      assertPost(calls[3], {
        operation: "reply", text: "My reply", idempotencyKey: IDEMPOTENCY_KEY,
      }, CSRF);
      assertPost(calls[4], { operation: "logout" }, CSRF);

      assert.equal(String(calls[1].init?.body).includes(TOKEN), false);
      assert.equal(String(calls[2].input).includes(TOKEN), false);
      assert.equal(String(calls[3].init?.body).includes(TOKEN), false);
      assert.equal(JSON.stringify(calls[0].init?.headers).includes(CSRF), false);
      assert.equal(JSON.stringify(calls[1].init?.headers).includes(CSRF), false);
      assert.equal(JSON.stringify(calls[2].init?.headers).includes(CSRF), false);
    }

    {
      const lifecycleCases = [
        [404, "invitation_invalid", "invitation_invalid"],
        [410, "invitation_expired", "invitation_expired"],
        [410, "invitation_revoked", "invitation_revoked"],
        [409, "invitation_already_exchanged", "invitation_already_exchanged"],
        [401, "session_expired", "session_expired"],
        [401, "session_revoked", "session_revoked"],
        [503, "service_unavailable", "service_unavailable"],
        [404, "not_found", "service_unavailable"],
      ] as const;
      for (const [httpStatus, code, expectedStatus] of lifecycleCases) {
        const calls: FetchCall[] = [];
        installFetch(
          [response(httpStatus, { ok: false, error: { code } })],
          calls,
        );
        assert.deepEqual(await bootstrapGuestSession(), {
          status: expectedStatus,
        });
      }
    }

    {
      const calls: FetchCall[] = [];
      installFetch(
        [
          response(
            429,
            { ok: false, error: { code: "rate_limited" } },
            { "Retry-After": "17" },
          ),
        ],
        calls,
      );
      assert.deepEqual(await bootstrapGuestSession(), {
        status: "rate_limited",
        retryAfterSeconds: 17,
      });
    }

    {
      const calls: FetchCall[] = [];
      installFetch(
        [response(500, { ok: false, error: { code: "invitation_expired" } })],
        calls,
      );
      assert.deepEqual(await bootstrapGuestSession(), {
        status: "invalid_response",
      });
    }

    for (const leakedField of [
      "sessionId",
      "sessionHash",
      "tokenHash",
      "token",
      "ownerEmail",
      "workspaceId",
      "mailboxId",
      "participants",
      "externalGuests",
    ]) {
      const calls: FetchCall[] = [];
      installFetch(
        [success({ collaboration: { ...collaboration, [leakedField]: "leak" } })],
        calls,
      );
      assert.deepEqual(await readGuestCollaboration(), {
        status: "invalid_response",
      });
    }

    for (const malformed of [
      { ...collaboration, messages: [{ ...collaboration.messages[0], visibility: "shared" }] },
      { ...collaboration, participants: [] },
      { ...collaboration, sharedSource: { ...collaboration.sharedSource, mailboxId: "mailbox" } },
      { ...collaboration, allowedActions: ["read"] },
    ]) {
      assert.equal(parseCollaborationGuestDto(malformed), null);
    }

    {
      const calls: FetchCall[] = [];
      installFetch([], calls);
      assert.deepEqual(await exchangeGuestInvitation("bad", "Reviewer"), {
        status: "invalid_request",
      });
      assert.deepEqual(await replyToGuestCollaboration("", CSRF, IDEMPOTENCY_KEY), {
        status: "invalid_request",
      });
      assert.deepEqual(await logoutGuestCollaboration("bad"), {
        status: "invalid_request",
      });
      assert.equal(calls.length, 0);
    }

    {
      const calls: FetchCall[] = [];
      installFetch([], calls);
      for (const key of [
        "", "A".repeat(42), "A".repeat(44), "A".repeat(42) + "B",
        "A".repeat(42) + "=", " " + IDEMPOTENCY_KEY,
        undefined as unknown as string,
        { toString: () => IDEMPOTENCY_KEY } as unknown as string,
      ]) {
        assert.deepEqual(await replyToGuestCollaboration("Reply", CSRF, key), {
          status: "invalid_request",
        });
      }
      assert.equal(calls.length, 0, "malformed send identity never reaches the server");
    }

    {
      const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
      try {
        let randomCalls = 0;
        Object.defineProperty(globalThis, "crypto", {
          configurable: true,
          value: { getRandomValues: (bytes: Uint8Array) => {
            assert.ok(bytes instanceof Uint8Array);
            assert.equal(bytes.length, 32, "logical send uses 256 random bits");
            bytes.fill(++randomCalls);
            return bytes;
          } },
        });
        const first = createCollaborationGuestReplyIdempotencyKey();
        const second = createCollaborationGuestReplyIdempotencyKey();
        assert.match(first!, /^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/);
        assert.match(second!, /^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/);
        assert.notEqual(first, second);
        assert.equal(randomCalls, 2);
        Object.defineProperty(globalThis, "crypto", {
          configurable: true,
          value: { getRandomValues: () => { throw new Error("random unavailable"); } },
        });
        assert.equal(createCollaborationGuestReplyIdempotencyKey(), null);
      } finally {
        if (cryptoDescriptor) Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
        else Reflect.deleteProperty(globalThis, "crypto");
      }
    }

    {
      const calls: FetchCall[] = [];
      globalThis.fetch = (async (input, init) => {
        calls.push({ input, init });
        throw new Error("lost response");
      }) as typeof fetch;
      assert.deepEqual(await replyToGuestCollaboration("Retry", CSRF, IDEMPOTENCY_KEY), {
        status: "network_failure",
      });
      assert.equal(calls.length, 1, "transport must not retry automatically");
      installFetch([success({ collaboration })], calls);
      assert.equal((await replyToGuestCollaboration("Retry", CSRF, IDEMPOTENCY_KEY)).status, "success");
      assert.deepEqual(calls[0].init?.body, calls[1].init?.body);
    }

    {
      globalThis.fetch = (async () => {
        throw new Error("offline");
      }) as typeof fetch;
      assert.deepEqual(await bootstrapGuestSession(), {
        status: "network_failure",
      });
    }
  } catch (error) {
    process.exitCode = 1;
    console.error(error);
  } finally {
    globalThis.fetch = originalFetch;
  }
}

void run();
