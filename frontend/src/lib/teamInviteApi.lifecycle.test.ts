declare const process: { exitCode?: number };

import assert from "node:assert/strict";
import * as currentTeamInviteApi from "./teamInviteApi";

type TeamLifecycleFailureStatus =
  | "unauthorized"
  | "forbidden"
  | "invalid"
  | "expired"
  | "used"
  | "conflict"
  | "unavailable";

type SafeInvitation = {
  invitationId: string;
  inviteeEmail: string;
  inviteeName: string;
  accessLevel: "Shared" | "Limited";
  status: "pending" | "accepted" | "declined" | "cancelled";
  expiresAt: number;
};

type SafePublicInvitation = Pick<
  SafeInvitation,
  "inviteeName" | "accessLevel" | "status" | "expiresAt"
>;

type SafeMember = {
  email: string;
  displayName: string;
  accessLevel: "Shared" | "Limited";
  status: "active";
};

type LifecycleResult<T> =
  | ({ ok: true } & T)
  | {
      ok: false;
      status: TeamLifecycleFailureStatus;
      error?: { code?: string; message?: string };
    };

type ExpectedTeamInviteApi = {
  issueTeamInvite(request: {
    inviteeEmail: string;
    inviteeName: string;
    accessLevel: "Shared" | "Limited";
  }): Promise<LifecycleResult<{ invite: SafeInvitation; inviteUrl: string }>>;
  fetchTeamInvite(
    token: string,
  ): Promise<LifecycleResult<{ invite: SafePublicInvitation }>>;
  mutateTeamInvite(request: {
    token: string;
    action: { type: "accept" | "decline" };
  }): Promise<LifecycleResult<{ invite: SafePublicInvitation }>>;
  fetchPendingTeamInvites(): Promise<
    LifecycleResult<{ invitations: SafeInvitation[] }>
  >;
  cancelTeamInvite(request: {
    invitationId: string;
  }): Promise<LifecycleResult<{ invitation: SafeInvitation }>>;
  removeTeamMember(request: {
    memberEmail: string;
  }): Promise<
    LifecycleResult<{
      member: { email: string; status: "removed"; removedAt?: number };
    }>
  >;
  changeTeamMemberAccess(request: {
    memberEmail: string;
    accessLevel: "Shared" | "Limited";
  }): Promise<LifecycleResult<{ member: SafeMember }>>;
};

const teamInviteApi = currentTeamInviteApi as unknown as ExpectedTeamInviteApi;

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

const safeInvitation: SafeInvitation = {
  invitationId: "tinv_test",
  inviteeEmail: "recipient@example.test",
  inviteeName: "Recipient Name",
  accessLevel: "Shared",
  status: "pending",
  expiresAt: 1_800_000_000_000,
};
const freshInviteToken = `${safeInvitation.invitationId}.${"A".repeat(43)}`;
const freshInviteUrl = `https://app.cuevion.example/?team_invite=${freshInviteToken}`;

const unsafeServerInvitation = {
  ...safeInvitation,
  token: "raw-bearer-token",
  tokenHash: "server-token-hash",
  workspaceId: "foreign-workspace",
  createdByUserId: "client-must-not-see-this",
  createdByUserName: "Workspace Owner",
  sessionData: "server-session-data",
};

const safePublicInvitation: SafePublicInvitation = {
  inviteeName: safeInvitation.inviteeName,
  accessLevel: safeInvitation.accessLevel,
  status: safeInvitation.status,
  expiresAt: safeInvitation.expiresAt,
};

async function test(name: string, callback: () => Promise<void>) {
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
  const originalWindow = globalThis.window;

  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      location: {
        origin: "https://app.cuevion.example",
      },
    },
  });

  try {
    await test("issue uses session authority and returns only a one-time URL plus redacted DTO", async () => {
      const calls: FetchCall[] = [];
      globalThis.fetch = (async (input, init) => {
        calls.push({ input, init });
        return response(201, {
          ok: true,
          invite: unsafeServerInvitation,
          inviteUrl: freshInviteUrl,
        });
      }) as typeof fetch;

      const result = await teamInviteApi.issueTeamInvite({
        inviteeEmail: safeInvitation.inviteeEmail,
        inviteeName: safeInvitation.inviteeName,
        accessLevel: safeInvitation.accessLevel,
      });

      assert.deepEqual(JSON.parse(String(calls[0]?.init?.body)), {
        inviteeEmail: safeInvitation.inviteeEmail,
        inviteeName: safeInvitation.inviteeName,
        accessLevel: safeInvitation.accessLevel,
      });
      assert.equal(calls[0]?.init?.credentials, "include");
      assert.equal(String(calls[0]?.input).includes("workspace"), false);
      assert.deepEqual(result, {
        ok: true,
        invite: safeInvitation,
        inviteUrl: freshInviteUrl,
      });
      if (result.ok) {
        assert.equal(JSON.stringify(result.invite).includes("raw-bearer-token"), false);
        assert.equal(JSON.stringify(result.invite).includes("server-token-hash"), false);
        assert.equal(JSON.stringify(result.invite).includes("foreign-workspace"), false);
      }
    });

    await test("public lookup is no-store and returns redacted presentation context", async () => {
      const calls: FetchCall[] = [];
      globalThis.fetch = (async (input, init) => {
        calls.push({ input, init });
        return response(200, {
          ok: true,
          invite: unsafeServerInvitation,
        });
      }) as typeof fetch;

      const result = await teamInviteApi.fetchTeamInvite("route-bearer-token");

      assert.equal(calls[0]?.init?.credentials, "include");
      assert.equal(calls[0]?.init?.cache, "no-store");
      assert.equal(String(calls[0]?.input).includes("workspaceId"), false);
      assert.deepEqual(result, {
        ok: true,
        invite: safePublicInvitation,
      });
    });

    await test("all lifecycle failures preserve typed classifications", async () => {
      const cases: Array<{
        httpStatus: number;
        code: string;
        expected: TeamLifecycleFailureStatus;
      }> = [
        { httpStatus: 401, code: "unauthorized", expected: "unauthorized" },
        { httpStatus: 403, code: "forbidden", expected: "forbidden" },
        { httpStatus: 400, code: "invalid", expected: "invalid" },
        { httpStatus: 410, code: "expired", expected: "expired" },
        { httpStatus: 409, code: "used", expected: "used" },
        { httpStatus: 409, code: "conflict", expected: "conflict" },
        { httpStatus: 503, code: "unavailable", expected: "unavailable" },
      ];

      for (const failure of cases) {
        globalThis.fetch = (async () =>
          response(failure.httpStatus, {
            ok: false,
            error: {
              code: failure.code,
              message: failure.code,
            },
          })) as typeof fetch;

        const result = await teamInviteApi.fetchTeamInvite("route-bearer-token");
        assert.equal(result.ok, false);
        if (!result.ok) {
          assert.equal(result.status, failure.expected);
        }
      }

      globalThis.fetch = (async () => {
        throw new TypeError("network unavailable");
      }) as typeof fetch;
      const unavailable = await teamInviteApi.fetchTeamInvite("route-bearer-token");
      assert.equal(unavailable.ok, false);
      if (!unavailable.ok) {
        assert.equal(unavailable.status, "unavailable");
      }
    });

    await test("accept and decline use the recipient session and never return the bearer", async () => {
      for (const actionType of ["accept", "decline"] as const) {
        const calls: FetchCall[] = [];
        globalThis.fetch = (async (input, init) => {
          calls.push({ input, init });
          return response(200, {
            ok: true,
            invite: {
              ...unsafeServerInvitation,
              status: actionType === "accept" ? "accepted" : "declined",
            },
          });
        }) as typeof fetch;

        const result = await teamInviteApi.mutateTeamInvite({
          token: "route-bearer-token",
          action: { type: actionType },
        });

        assert.equal(calls[0]?.init?.credentials, "include");
        assert.deepEqual(JSON.parse(String(calls[0]?.init?.body)), {
          action: { type: actionType },
        });
        assert.equal(String(calls[0]?.input).includes("workspaceId"), false);
        assert.equal(JSON.stringify(result).includes("raw-bearer-token"), false);
        assert.equal(JSON.stringify(result).includes("server-token-hash"), false);
      }
    });

    await test("lifecycle clients reject semantically mismatched successful responses", async () => {
      globalThis.fetch = (async () =>
        response(200, {
          ok: true,
          invite: {
            ...unsafeServerInvitation,
            status: "accepted",
          },
          inviteUrl: freshInviteUrl,
        })) as typeof fetch;
      const invalidIssue = await teamInviteApi.issueTeamInvite({
        inviteeEmail: safeInvitation.inviteeEmail,
        inviteeName: safeInvitation.inviteeName,
        accessLevel: safeInvitation.accessLevel,
      });
      assert.equal(invalidIssue.ok, false);
      if (!invalidIssue.ok) {
        assert.equal(invalidIssue.status, "unavailable");
      }

      for (const actionType of ["accept", "decline"] as const) {
        const mismatchedStatus = actionType === "accept" ? "declined" : "accepted";
        globalThis.fetch = (async () =>
          response(200, {
            ok: true,
            invite: {
              ...unsafeServerInvitation,
              status: mismatchedStatus,
            },
          })) as typeof fetch;
        const invalidMutation = await teamInviteApi.mutateTeamInvite({
          token: "route-bearer-token",
          action: { type: actionType },
        });
        assert.equal(invalidMutation.ok, false);
        if (!invalidMutation.ok) {
          assert.equal(invalidMutation.status, "unavailable");
        }
      }

      globalThis.fetch = (async () =>
        response(200, {
          ok: true,
          invitations: [
            {
              ...unsafeServerInvitation,
              status: "declined",
            },
          ],
        })) as typeof fetch;
      const invalidPending = await teamInviteApi.fetchPendingTeamInvites();
      assert.equal(invalidPending.ok, false);
      if (!invalidPending.ok) {
        assert.equal(invalidPending.status, "unavailable");
      }

      globalThis.fetch = (async () =>
        response(200, {
          ok: true,
          invitation: unsafeServerInvitation,
        })) as typeof fetch;
      const invalidCancel = await teamInviteApi.cancelTeamInvite({
        invitationId: safeInvitation.invitationId,
      });
      assert.equal(invalidCancel.ok, false);
      if (!invalidCancel.ok) {
        assert.equal(invalidCancel.status, "unavailable");
      }
    });

    await test("mutation clients reject successful responses for a different target", async () => {
      globalThis.fetch = (async () =>
        response(200, {
          ok: true,
          invite: {
            ...unsafeServerInvitation,
            inviteeEmail: "different@example.test",
            status: "pending",
          },
          inviteUrl: freshInviteUrl,
        })) as typeof fetch;
      const mismatchedIssue = await teamInviteApi.issueTeamInvite({
        inviteeEmail: safeInvitation.inviteeEmail,
        inviteeName: safeInvitation.inviteeName,
        accessLevel: safeInvitation.accessLevel,
      });
      assert.equal(mismatchedIssue.ok, false);

      for (const mismatchedUrl of [
        `https://attacker.example/?team_invite=${freshInviteToken}`,
        `https://app.cuevion.example/?team_invite=tinv_other.${"B".repeat(43)}`,
      ]) {
        globalThis.fetch = (async () =>
          response(200, {
            ok: true,
            invite: unsafeServerInvitation,
            inviteUrl: mismatchedUrl,
          })) as typeof fetch;
        const mismatchedLink = await teamInviteApi.issueTeamInvite({
          inviteeEmail: safeInvitation.inviteeEmail,
          inviteeName: safeInvitation.inviteeName,
          accessLevel: safeInvitation.accessLevel,
        });
        assert.equal(mismatchedLink.ok, false);
      }

      globalThis.fetch = (async () =>
        response(200, {
          ok: true,
          invitation: {
            ...unsafeServerInvitation,
            invitationId: "different-invitation",
            status: "cancelled",
          },
        })) as typeof fetch;
      const mismatchedCancel = await teamInviteApi.cancelTeamInvite({
        invitationId: safeInvitation.invitationId,
      });
      assert.equal(mismatchedCancel.ok, false);

      globalThis.fetch = (async () =>
        response(200, {
          ok: true,
          member: {
            email: "different@example.test",
            status: "removed",
          },
        })) as typeof fetch;
      const mismatchedRemove = await teamInviteApi.removeTeamMember({
        memberEmail: "member@example.test",
      });
      assert.equal(mismatchedRemove.ok, false);

      globalThis.fetch = (async () =>
        response(200, {
          ok: true,
          member: {
            email: "member@example.test",
            displayName: "Member",
            accessLevel: "Limited",
            status: "active",
          },
        })) as typeof fetch;
      const mismatchedAccess = await teamInviteApi.changeTeamMemberAccess({
        memberEmail: "member@example.test",
        accessLevel: "Shared",
      });
      assert.equal(mismatchedAccess.ok, false);
    });

    await test("pending projection is authenticated, no-store, and token-redacted", async () => {
      const calls: FetchCall[] = [];
      globalThis.fetch = (async (input, init) => {
        calls.push({ input, init });
        return response(200, {
          ok: true,
          invitations: [unsafeServerInvitation],
        });
      }) as typeof fetch;

      const result = await teamInviteApi.fetchPendingTeamInvites();

      assert.equal(calls[0]?.init?.method, "GET");
      assert.equal(calls[0]?.init?.credentials, "include");
      assert.equal(calls[0]?.init?.cache, "no-store");
      assert.equal(String(calls[0]?.input).includes("workspaceId"), false);
      assert.deepEqual(result, {
        ok: true,
        invitations: [safeInvitation],
      });
    });

    await test("cancel uses only a safe invitation id and authenticated session authority", async () => {
      const calls: FetchCall[] = [];
      globalThis.fetch = (async (input, init) => {
        calls.push({ input, init });
        return response(200, {
          ok: true,
          invitation: {
            ...unsafeServerInvitation,
            status: "cancelled",
          },
        });
      }) as typeof fetch;

      const result = await teamInviteApi.cancelTeamInvite({
        invitationId: safeInvitation.invitationId,
      });

      assert.equal(calls[0]?.init?.credentials, "include");
      assert.deepEqual(JSON.parse(String(calls[0]?.init?.body)), {
        invitationId: safeInvitation.invitationId,
      });
      assert.equal(String(calls[0]?.input).includes("raw-bearer-token"), false);
      assert.equal(JSON.stringify(result).includes("raw-bearer-token"), false);
      assert.equal(JSON.stringify(result).includes("server-token-hash"), false);
      assert.equal(JSON.stringify(result).includes("foreign-workspace"), false);
    });

    await test("member removal sends only email and redacts workspace fields", async () => {
      const calls: FetchCall[] = [];
      globalThis.fetch = (async (input, init) => {
        calls.push({ input, init });
        return response(200, {
          ok: true,
          member: {
            workspaceId: "foreign-workspace",
            email: "member@example.test",
            status: "removed",
            removedAt: 1_700_000_000_000,
            sessionData: "secret",
          },
        });
      }) as typeof fetch;

      const result = await teamInviteApi.removeTeamMember({
        memberEmail: "member@example.test",
      });

      assert.equal(calls[0]?.init?.credentials, "include");
      assert.deepEqual(JSON.parse(String(calls[0]?.init?.body)), {
        memberEmail: "member@example.test",
      });
      assert.deepEqual(result, {
        ok: true,
        member: {
          email: "member@example.test",
          status: "removed",
          removedAt: 1_700_000_000_000,
        },
      });
    });

    await test("access change sends email plus Shared or Limited only", async () => {
      for (const accessLevel of ["Shared", "Limited"] as const) {
        const calls: FetchCall[] = [];
        globalThis.fetch = (async (input, init) => {
          calls.push({ input, init });
          return response(200, {
            ok: true,
            member: {
              email: "member@example.test",
              displayName: "Member",
              accessLevel,
              status: "active",
              workspaceId: "foreign-workspace",
              tokenHash: "secret",
            },
          });
        }) as typeof fetch;

        const result = await teamInviteApi.changeTeamMemberAccess({
          memberEmail: "member@example.test",
          accessLevel,
        });

        assert.equal(calls[0]?.init?.credentials, "include");
        assert.deepEqual(JSON.parse(String(calls[0]?.init?.body)), {
          memberEmail: "member@example.test",
          accessLevel,
        });
        assert.deepEqual(result, {
          ok: true,
          member: {
            email: "member@example.test",
            displayName: "Member",
            accessLevel,
            status: "active",
          },
        });
      }

      let invalidRequestCount = 0;
      globalThis.fetch = (async () => {
        invalidRequestCount += 1;
        return response(500, { ok: false });
      }) as typeof fetch;

      const invalidResult = await (
        teamInviteApi.changeTeamMemberAccess as unknown as (request: {
          memberEmail: string;
          accessLevel: string;
        }) => Promise<LifecycleResult<{ member: SafeMember }>>
      )({
        memberEmail: "member@example.test",
        accessLevel: "Admin",
      });

      assert.equal(invalidRequestCount, 0);
      assert.equal(invalidResult.ok, false);
      if (!invalidResult.ok) {
        assert.equal(invalidResult.status, "invalid");
      }
    });
  } finally {
    globalThis.fetch = originalFetch;
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: originalWindow,
    });
  }
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
