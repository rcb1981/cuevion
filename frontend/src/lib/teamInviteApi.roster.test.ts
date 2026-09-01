declare const process: { exitCode?: number };

import assert from "node:assert/strict";
import { fetchTeamMembers } from "./teamInviteApi";

type FetchCall = {
  input: RequestInfo | URL;
  init?: RequestInit;
};

function response(status: number, payload: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => payload,
  } as Response;
}

async function run() {
  const originalFetch = globalThis.fetch;
  const firstMemberUserId = "usr_AAAAAAAAAAAAAAAAAAAAAA";
  const secondMemberUserId = "usr_BBBBBBBBBBBBBBBBBBBBBQ";

  try {
    const calls: FetchCall[] = [];
    globalThis.fetch = (async (input, init) => {
      calls.push({ input, init });
      return response(200, {
        ok: true,
        members: [
          {
            memberUserId: firstMemberUserId,
            email: "member@example.test",
            displayName: "Member",
            accessLevel: "Shared",
            status: "active",
            inviteToken: "raw-token-must-be-discarded",
            mailboxCredential: "credential-must-be-discarded",
          },
        ],
      });
    }) as typeof fetch;

    assert.deepEqual(await fetchTeamMembers(), {
      ok: true,
      members: [
        {
          memberUserId: firstMemberUserId,
          email: "member@example.test",
          displayName: "Member",
          accessLevel: "Shared",
          status: "active",
        },
      ],
    });
    assert.deepEqual(
      [String(calls[0]?.input), calls[0]?.init?.method, calls[0]?.init?.credentials, calls[0]?.init?.cache],
      ["/api/team/members?op=list", "GET", "include", "no-store"],
    );
    assert.equal(
      String(calls[0]?.input).includes("workspaceId"),
      false,
      "the client must not send workspace authority",
    );
    assert.equal(calls.length, 1, "the authoritative roster uses one request");

    globalThis.fetch = (async () =>
      response(200, {
        ok: true,
        members: [
          {
            memberUserId: secondMemberUserId,
            email: "second@example.test",
            displayName: "Second",
            accessLevel: "Limited",
            status: "active",
          },
          {
            memberUserId: firstMemberUserId,
            email: "first@example.test",
            displayName: "First",
            accessLevel: "Shared",
            status: "active",
          },
        ],
      })) as typeof fetch;
    assert.deepEqual(await fetchTeamMembers(), {
      ok: true,
      members: [
        {
          memberUserId: secondMemberUserId,
          email: "second@example.test",
          displayName: "Second",
          accessLevel: "Limited",
          status: "active",
        },
        {
          memberUserId: firstMemberUserId,
          email: "first@example.test",
          displayName: "First",
          accessLevel: "Shared",
          status: "active",
        },
      ],
    });

    const invalidMemberUserIds: unknown[] = [
      undefined,
      "",
      " member-user ",
      "member@example.test",
      "usr_AAAAAAAAAAAAAAAAAAAAAB",
      "usr_AAAAAAAAAAAAAAAAAAAAAA\n",
    ];
    for (const memberUserId of invalidMemberUserIds) {
      globalThis.fetch = (async () =>
        response(200, {
          ok: true,
          members: [
            {
              ...(memberUserId === undefined ? {} : { memberUserId }),
              email: "member@example.test",
              displayName: "Member",
              accessLevel: "Shared",
              status: "active",
            },
          ],
        })) as typeof fetch;
      assert.deepEqual(await fetchTeamMembers(), {
        ok: false,
        status: "unavailable",
        error: {
          code: "unavailable",
          message: "Could not load team members.",
        },
      });
    }

    globalThis.fetch = (async () => response(200, { ok: true, members: [] })) as typeof fetch;
    assert.deepEqual(await fetchTeamMembers(), { ok: true, members: [] });

    for (const [httpStatus, expectedStatus] of [
      [401, "unauthorized"],
      [403, "forbidden"],
      [503, "unavailable"],
    ] as const) {
      globalThis.fetch = (async () =>
        response(httpStatus, {
          ok: false,
          error: { code: expectedStatus, message: expectedStatus },
        })) as typeof fetch;
      const result = await fetchTeamMembers();
      assert.equal(result.ok, false);
      if (!result.ok) {
        assert.equal(result.status, expectedStatus);
      }
    }

    globalThis.fetch = (async () => {
      throw new TypeError("network unavailable");
    }) as typeof fetch;
    assert.deepEqual(await fetchTeamMembers(), {
      ok: false,
      status: "unavailable",
      error: {
        code: "unavailable",
        message: "Could not load team members.",
      },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
