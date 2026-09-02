declare const process: { exitCode?: number };

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  __resetCollaborationOwnerReadApiForTests,
  type CollaborationOwnerReadDto,
} from "./collaborationOwnerReadApi";
import {
  createCollaborationWithGuestForOwner,
  issueGuestInvitationForOwner,
  parseCollaborationOwnerGuestInvitationMetadata,
  revokeGuestInvitationForOwner,
} from "./collaborationOwnerWriteApi";
import {
  deriveCollaborationOwnerSourceLocator,
  type CollaborationOwnerSourceLocator,
} from "./collaborationOwnerSourceLocator";

type FetchCall = { input: RequestInfo | URL; init?: RequestInit };

const COLLABORATION_ID = "A".repeat(22);
const OTHER_COLLABORATION_ID = "B".repeat(22);
const INVITE_ID = "I".repeat(22);
const OTHER_INVITE_ID = "J".repeat(22);
const OWNER_USER_ID = `usr_${"A".repeat(22)}`;
const TOKEN = "T".repeat(43);
const EMAIL = "reviewer@example.test";
const NOW_MS = 1_800_000_000_000;
const EXPIRES_AT = 1_900_000_000;

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

function csrfResponse() {
  return response(200, {
    ok: true,
    data: { csrfToken: "csrf-token", expiresAt: NOW_MS / 1000 + 300 },
  });
}

function installFetch(outcomes: Response[], calls: FetchCall[]) {
  globalThis.fetch = (async (input, init) => {
    calls.push({ input, init });
    const next = outcomes.shift();
    assert.ok(next, `Unexpected request to ${String(input)}`);
    return next;
  }) as typeof fetch;
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

function invitationMetadata(
  collaborationId = COLLABORATION_ID,
  invitedEmail: string | null | undefined = EMAIL,
) {
  return {
    inviteId: INVITE_ID,
    collaborationId,
    allowedActions: ["read", "reply"],
    identityAssurance: "link_possession",
    expiresAt: EXPIRES_AT,
    status: "active",
    ...(typeof invitedEmail === "string" ? { invitedEmail } : {}),
  } as const;
}

function externalGuest(
  status: "pending" | "active" | "logged_out" | "revoked" | "expired" =
    "pending",
  inviteId = INVITE_ID,
  invitedEmail: string | null | undefined = EMAIL,
) {
  return {
    inviteId,
    status,
    expiresAt: EXPIRES_AT,
    ...(typeof invitedEmail === "string" ? { invitedEmail } : {}),
  } as const;
}

function ownerCollaboration({
  collaborationId = COLLABORATION_ID,
  mailboxId = "mailbox-google",
  viewerAccess = "owner",
  guest = externalGuest(),
}: {
  collaborationId?: string;
  mailboxId?: string;
  viewerAccess?: "owner" | "participant";
  guest?: ReturnType<typeof externalGuest>;
} = {}): CollaborationOwnerReadDto {
  const base = {
    collaborationId,
    mailboxId,
    state: "needs_review" as const,
    createdAt: NOW_MS - 2_000,
    updatedAt: NOW_MS - 1_000,
    source: {
      subject: "Review this",
      senderDisplay: "Sender",
      fromDisplay: "sender@example.test",
      timestamp: "2027-01-15T08:00:00.000Z",
      bodyText: "Source body",
    },
    messages: [],
    participants: [
      {
        userId: OWNER_USER_ID,
        displayName: "Owner",
        access: "owner" as const,
      },
    ],
  };
  return viewerAccess === "owner"
    ? { ...base, viewerAccess, externalGuests: [guest] }
    : { ...base, viewerAccess };
}

function createSuccess({
  created = true,
  invitationCreated = true,
  collaboration = ownerCollaboration(),
  invitation = invitationMetadata(),
  token = TOKEN,
  omitToken = false,
  extraData = {},
}: {
  created?: boolean;
  invitationCreated?: boolean;
  collaboration?: unknown;
  invitation?: unknown;
  token?: unknown;
  omitToken?: boolean;
  extraData?: Record<string, unknown>;
} = {}) {
  return response(created ? 201 : 200, {
    ok: true,
    data: {
      created,
      invitationCreated,
      collaboration,
      invitation,
      ...(invitationCreated && !omitToken ? { token } : {}),
      ...extraData,
    },
  });
}

function issueSuccess({
  invitationCreated = true,
  collaboration = ownerCollaboration(),
  invitation = invitationMetadata(),
  token = TOKEN,
  forceToken = false,
}: {
  invitationCreated?: boolean;
  collaboration?: unknown;
  invitation?: unknown;
  token?: unknown;
  forceToken?: boolean;
} = {}) {
  return response(invitationCreated ? 201 : 200, {
    ok: true,
    data: {
      invitationCreated,
      collaboration,
      invitation,
      ...(invitationCreated || forceToken ? { token } : {}),
    },
  });
}

function revokeSuccess({
  collaboration = ownerCollaboration({
    guest: externalGuest("revoked"),
  }),
  invitation = externalGuest("revoked"),
}: {
  collaboration?: unknown;
  invitation?: unknown;
} = {}) {
  return response(200, {
    ok: true,
    data: { collaboration, invitation },
  });
}

function assertExactOperationRequest(
  call: FetchCall,
  body: Record<string, unknown>,
) {
  assert.equal(call.input, "/api/collaboration/owner");
  assert.equal(call.init?.method, "POST");
  assert.equal(call.init?.credentials, "same-origin");
  assert.equal(call.init?.cache, "no-store");
  assert.deepEqual(JSON.parse(String(call.init?.body)), body);
  assert.deepEqual(call.init?.headers, {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Cuevion-CSRF": "csrf-token",
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
  Date.now = () => NOW_MS;
  try {
    await test("creates with an external guest using exact email-less and email contracts", async () => {
      for (const invitedEmail of [undefined, EMAIL]) {
        __resetCollaborationOwnerReadApiForTests();
        const calls: FetchCall[] = [];
        const responseEmail = invitedEmail ?? null;
        const invitation = invitationMetadata(COLLABORATION_ID, responseEmail);
        const guest = externalGuest("pending", INVITE_ID, responseEmail);
        installFetch(
          [
            csrfResponse(),
            createSuccess({
              invitation,
              collaboration: ownerCollaboration({ guest }),
            }),
          ],
          calls,
        );
        const result = await createCollaborationWithGuestForOwner(
          googleLocator(),
          "needs_review",
          invitedEmail,
        );
        assert.equal(
          result.status,
          "success",
          `create variant ${invitedEmail ?? "without-email"}`,
        );
        assert.equal(result.status === "success" && result.invitationCreated, true);
        assert.equal(result.status === "success" && result.token, TOKEN);
        assertExactOperationRequest(calls[1], {
          operation: "create_with_guest",
          mailboxId: "mailbox-google",
          sourceRef: { providerMessageId: "gmail-provider-message-id" },
          state: "needs_review",
          ...(invitedEmail === undefined ? {} : { invitedEmail }),
        });
        const body = JSON.parse(String(calls[1].init?.body));
        for (const forbidden of [
          "participantUserId",
          "ownerEmail",
          "workspaceId",
          "collaborationId",
          "displayName",
          "inviteId",
          "token",
        ]) {
          assert.equal(Object.prototype.hasOwnProperty.call(body, forbidden), false);
        }
      }
    });

    await test("rejects invalid create inputs before HTTP", async () => {
      const calls: FetchCall[] = [];
      installFetch([], calls);
      const locator = googleLocator();
      assert.deepEqual(
        await createCollaborationWithGuestForOwner(
          { ...locator } as CollaborationOwnerSourceLocator,
          "needs_review",
        ),
        { status: "invalid_source_locator" },
      );
      assert.deepEqual(
        await createCollaborationWithGuestForOwner(
          locator,
          "resolved" as "needs_review",
        ),
        { status: "invalid_state" },
      );
      assert.deepEqual(
        await createCollaborationWithGuestForOwner(
          locator,
          "needs_review",
          "Reviewer@Example.test",
        ),
        { status: "invalid_invited_email" },
      );
      assert.equal(calls.length, 0);
    });

    await test("enforces create owner DTO, token, secrecy, and binding invariants", async () => {
      const malformedResponses = [
        createSuccess({ collaboration: ownerCollaboration({ viewerAccess: "participant" }) }),
        createSuccess({ omitToken: true }),
        createSuccess({ invitationCreated: false, extraData: { token: TOKEN } }),
        createSuccess({ token: "malformed" }),
        createSuccess({
          invitation: { ...invitationMetadata(), tokenHash: "secret" },
        }),
        createSuccess({
          invitation: invitationMetadata(OTHER_COLLABORATION_ID),
        }),
        createSuccess({
          collaboration: ownerCollaboration({ mailboxId: "other-mailbox" }),
        }),
        createSuccess({
          invitation: invitationMetadata(COLLABORATION_ID, "other@example.test"),
          collaboration: ownerCollaboration({
            guest: externalGuest("pending", INVITE_ID, "other@example.test"),
          }),
        }),
      ];
      for (const malformed of malformedResponses) {
        __resetCollaborationOwnerReadApiForTests();
        const calls: FetchCall[] = [];
        installFetch([csrfResponse(), malformed], calls);
        assert.deepEqual(
          await createCollaborationWithGuestForOwner(
            googleLocator(),
            "needs_review",
            EMAIL,
          ),
          { status: "invalid_response" },
        );
      }
    });

    await test("issues new and duplicate guest invitations with exact token discrimination", async () => {
      for (const invitedEmail of [undefined, EMAIL]) {
        __resetCollaborationOwnerReadApiForTests();
        const calls: FetchCall[] = [];
        const responseEmail = invitedEmail ?? null;
        const invitation = invitationMetadata(COLLABORATION_ID, responseEmail);
        const guest = externalGuest("pending", INVITE_ID, responseEmail);
        installFetch(
          [
            csrfResponse(),
            issueSuccess({
              invitation,
              collaboration: ownerCollaboration({ guest }),
            }),
          ],
          calls,
        );
        const result = await issueGuestInvitationForOwner(
          COLLABORATION_ID,
          invitedEmail,
        );
        assert.equal(result.status, "success");
        assert.equal(result.status === "success" && result.invitationCreated, true);
        assert.equal(result.status === "success" && result.token, TOKEN);
        assertExactOperationRequest(calls[1], {
          operation: "issue_guest_invite",
          collaborationId: COLLABORATION_ID,
          ...(invitedEmail === undefined ? {} : { invitedEmail }),
        });
      }

      __resetCollaborationOwnerReadApiForTests();
      const duplicateCalls: FetchCall[] = [];
      installFetch(
        [csrfResponse(), issueSuccess({ invitationCreated: false })],
        duplicateCalls,
      );
      assert.deepEqual(await issueGuestInvitationForOwner(COLLABORATION_ID), {
        status: "success",
        invitationCreated: false,
        collaboration: ownerCollaboration(),
        invitation: invitationMetadata(),
      });
    });

    await test("rejects invalid issue inputs before HTTP", async () => {
      const calls: FetchCall[] = [];
      installFetch([], calls);
      assert.deepEqual(await issueGuestInvitationForOwner("short"), {
        status: "invalid_collaboration_id",
      });
      assert.deepEqual(
        await issueGuestInvitationForOwner(
          COLLABORATION_ID,
          "Reviewer@Example.test",
        ),
        { status: "invalid_invited_email" },
      );
      assert.equal(calls.length, 0);
    });

    await test("rejects malformed issue successes and preserves transport failures", async () => {
      const malformedResponses = [
        issueSuccess({ invitationCreated: false, forceToken: true }),
        issueSuccess({ token: "malformed" }),
        issueSuccess({ collaboration: ownerCollaboration({ viewerAccess: "participant" }) }),
        issueSuccess({ invitation: invitationMetadata(OTHER_COLLABORATION_ID) }),
        issueSuccess({ collaboration: ownerCollaboration({ collaborationId: OTHER_COLLABORATION_ID }) }),
        issueSuccess({ invitation: { ...invitationMetadata(), sessionHash: "secret" } }),
      ];
      for (const malformed of malformedResponses) {
        __resetCollaborationOwnerReadApiForTests();
        const calls: FetchCall[] = [];
        installFetch([csrfResponse(), malformed], calls);
        assert.deepEqual(await issueGuestInvitationForOwner(COLLABORATION_ID, EMAIL), {
          status: "invalid_response",
        });
      }

      __resetCollaborationOwnerReadApiForTests();
      const failureCalls: FetchCall[] = [];
      installFetch(
        [csrfResponse(), response(409, { ok: false, error: { code: "conflict" } })],
        failureCalls,
      );
      assert.deepEqual(await issueGuestInvitationForOwner(COLLABORATION_ID), {
        status: "conflict",
      });
    });

    await test("revokes the exact collaboration invitation binding", async () => {
      const calls: FetchCall[] = [];
      installFetch([csrfResponse(), revokeSuccess()], calls);
      const result = await revokeGuestInvitationForOwner(
        COLLABORATION_ID,
        INVITE_ID,
      );
      assert.deepEqual(result, {
        status: "success",
        collaboration: ownerCollaboration({ guest: externalGuest("revoked") }),
        invitation: externalGuest("revoked"),
      });
      assertExactOperationRequest(calls[1], {
        operation: "revoke_guest_invite",
        collaborationId: COLLABORATION_ID,
        inviteId: INVITE_ID,
      });
      assert.equal(
        result.status === "success" && result.collaboration.externalGuests.length,
        1,
      );
    });

    await test("rejects invalid revoke IDs before HTTP", async () => {
      const calls: FetchCall[] = [];
      installFetch([], calls);
      assert.deepEqual(await revokeGuestInvitationForOwner("short", INVITE_ID), {
        status: "invalid_collaboration_id",
      });
      assert.deepEqual(
        await revokeGuestInvitationForOwner(COLLABORATION_ID, "short"),
        { status: "invalid_invite_id" },
      );
      assert.equal(calls.length, 0);
    });

    await test("rejects malformed revoke successes and preserves lifecycle failures", async () => {
      const malformedResponses = [
        revokeSuccess({
          collaboration: ownerCollaboration({ viewerAccess: "participant" }),
        }),
        revokeSuccess({
          collaboration: ownerCollaboration({ collaborationId: OTHER_COLLABORATION_ID }),
        }),
        revokeSuccess({ invitation: externalGuest("revoked", OTHER_INVITE_ID) }),
        revokeSuccess({ invitation: externalGuest("active") }),
        revokeSuccess({
          invitation: { ...externalGuest("revoked"), csrfTokenHash: "secret" },
        }),
      ];
      for (const malformed of malformedResponses) {
        __resetCollaborationOwnerReadApiForTests();
        const calls: FetchCall[] = [];
        installFetch([csrfResponse(), malformed], calls);
        assert.deepEqual(
          await revokeGuestInvitationForOwner(COLLABORATION_ID, INVITE_ID),
          { status: "invalid_response" },
        );
      }

      __resetCollaborationOwnerReadApiForTests();
      const failureCalls: FetchCall[] = [];
      installFetch(
        [csrfResponse(), response(404, { ok: false, error: { code: "not_found" } })],
        failureCalls,
      );
      assert.deepEqual(
        await revokeGuestInvitationForOwner(COLLABORATION_ID, INVITE_ID),
        { status: "not_found" },
      );
    });

    const secretFields = [
      "tokenHash",
      "sessionId",
      "sessionHash",
      "csrfToken",
      "csrfTokenHash",
      "ownerEmail",
      "workspaceId",
      "mailboxId",
      "redisKey",
      "createdBy",
    ];
    for (const secretField of secretFields) {
      assert.equal(
        parseCollaborationOwnerGuestInvitationMetadata({
          ...invitationMetadata(),
          [secretField]: "secret",
        }),
        null,
      );
    }

    const source = readFileSync(
      resolve(process.cwd(), "src/lib/collaborationOwnerWriteApi.ts"),
      "utf8",
    );
    for (const forbiddenPersistence of [
      "localStorage",
      "sessionStorage",
      "indexedDB",
      "document.cookie",
      "console.log",
      "buildCollaborationGuestInviteLink",
    ]) {
      assert.equal(
        source.includes(forbiddenPersistence),
        false,
        `owner transport must not contain ${forbiddenPersistence}`,
      );
    }
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalDateNow;
  }
}

void run();
