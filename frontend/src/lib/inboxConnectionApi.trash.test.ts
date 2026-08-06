import assert from "node:assert/strict";
import {
  fetchGmailTrash,
  mutateProviderTrashMessage,
  type ProviderTrashMessageRequest,
} from "./inboxConnectionApi";

type CapturedRequest = {
  url: string;
  init: RequestInit;
};

const MAILBOX_ID = "server-mailbox-1";
const PROVIDER_MESSAGE_ID = "gmail-provider-message-1";

const request: ProviderTrashMessageRequest = {
  mailboxId: MAILBOX_ID,
  action: "trash",
  providerMessageId: PROVIDER_MESSAGE_ID,
  sourceFolder: "INBOX",
};

const successPayload = {
  ok: true,
  action: "trash",
  provider: "gmail",
  mailboxId: MAILBOX_ID,
  providerMessageId: PROVIDER_MESSAGE_ID,
  sourceFolder: "INBOX",
  destinationFolder: "TRASH",
  readback: {
    inSource: false,
    inTrash: true,
  },
} as const;

const trashMessageOne = {
  id: "gmail-trash-ui-1",
  sender: "First sender",
  subject: "First trashed message",
  snippet: "First Trash preview",
  from: "first@example.test",
  to: "owner@example.test",
  timestamp: "August 6 at 10:00",
  createdAt: "2026-08-06T08:00:00.000Z",
  body: ["First Trash body"],
  serverMailboxId: MAILBOX_ID,
  providerFolder: "Trash",
  providerMessageId: "gmail-trash-provider-message-1",
  providerThreadId: "thread-shared-by-two-messages",
  rfcMessageId: "first-trash@example.test",
  labelIds: ["TRASH", "UNREAD"],
} as const;

const trashMessageTwo = {
  ...trashMessageOne,
  id: "gmail-trash-ui-2",
  sender: "Second sender",
  subject: "Second trashed message",
  snippet: "Second Trash preview",
  from: "second@example.test",
  timestamp: "August 6 at 10:05",
  createdAt: "2026-08-06T08:05:00.000Z",
  body: ["Second Trash body"],
  providerMessageId: "gmail-trash-provider-message-2",
  // Multiple Gmail messages may legitimately belong to one provider thread.
  providerThreadId: trashMessageOne.providerThreadId,
  rfcMessageId: "second-trash@example.test",
  labelIds: ["TRASH"],
} as const;

const emptyTrashFetchPayload = {
  ok: true,
  status: "ok",
  mailboxId: MAILBOX_ID,
  folder: {
    serverMailboxId: MAILBOX_ID,
    providerFolder: "Trash",
    uidValidity: "gmail-api",
    messages: [],
  },
} as const;

const multiMessageTrashFetchPayload = {
  ...emptyTrashFetchPayload,
  folder: {
    ...emptyTrashFetchPayload.folder,
    messages: [trashMessageOne, trashMessageTwo],
  },
} as const;

function rawResponse(status: number, rawPayload: string): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => rawPayload,
  } as Response;
}

function response(status: number, payload: unknown): Response {
  return rawResponse(status, JSON.stringify(payload));
}

function assertInvalidResponse(result: Awaited<ReturnType<typeof mutateProviderTrashMessage>>) {
  assert.deepEqual(result, {
    ok: false,
    error: {
      code: "trash_response_invalid",
      message: "Trash did not return a valid provider-confirmed mailbox state.",
    },
  });
}

function assertInvalidTrashFetchResponse(
  result: Awaited<ReturnType<typeof fetchGmailTrash>>,
) {
  assert.deepEqual(result, {
    ok: false,
    error: {
      code: "gmail_trash_fetch_response_invalid",
      message: "Trash did not return a valid Gmail provider snapshot.",
    },
  });
}

async function run() {
  await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));
  const originalFetch = globalThis.fetch;
  const captured: CapturedRequest[] = [];
  let nextResponse = response(200, successPayload);
  let networkFailure: Error | null = null;

  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    captured.push({ url, init: init ?? {} });
    if (networkFailure) {
      throw networkFailure;
    }
    return nextResponse;
  }) as typeof fetch;

  const lastRequest = () => captured[captured.length - 1];
  const lastBody = () =>
    JSON.parse(String(lastRequest().init.body)) as Record<string, unknown>;

  try {
    assert.deepEqual(await mutateProviderTrashMessage(request), successPayload);
    assert.equal(lastRequest().url, "/api/inboxes/message-action");
    assert.equal(lastRequest().init.method, "POST");
    assert.equal(lastRequest().init.credentials, "include");
    assert.equal(lastRequest().init.cache, "no-store");
    assert.deepEqual(lastRequest().init.headers, {
      "Content-Type": "application/json",
    });
    assert.deepEqual(lastBody(), {
      mailboxId: MAILBOX_ID,
      action: "trash",
      providerMessageId: PROVIDER_MESSAGE_ID,
      sourceFolder: "INBOX",
    });
    assert.deepEqual(Object.keys(successPayload).sort(), [
      "action",
      "destinationFolder",
      "mailboxId",
      "ok",
      "provider",
      "providerMessageId",
      "readback",
      "sourceFolder",
    ]);

    const callsBeforeInvalidRequests = captured.length;
    for (const invalidRequest of [
      { ...request, accessToken: "forged-token" },
      { ...request, action: "archive" },
      { ...request, sourceFolder: "Archive" },
      { ...request, providerMessageId: "thread-provider-id" },
      { ...request, providerMessageId: "rfc@example.test" },
      { ...request, mailboxId: "" },
    ]) {
      assert.deepEqual(
        await mutateProviderTrashMessage(
          invalidRequest as ProviderTrashMessageRequest,
        ),
        {
          ok: false,
          error: {
            code: "invalid_trash_request",
            message: "Trash requires one valid Gmail provider message identity.",
          },
        },
      );
    }
    assert.equal(
      captured.length,
      callsBeforeInvalidRequests,
      "invalid or authority-bearing requests must fail before fetch",
    );

    for (const malformedSuccess of [
      { ...successPayload, status: "ok" },
      { ...successPayload, mailboxId: "other-mailbox" },
      { ...successPayload, providerMessageId: "other-message" },
      { ...successPayload, provider: "imap" },
      { ...successPayload, sourceFolder: "Archive" },
      { ...successPayload, destinationFolder: "BIN" },
      { ...successPayload, readback: { inSource: true, inTrash: true } },
      { ...successPayload, readback: { inSource: false, inTrash: false } },
      {
        ...successPayload,
        readback: { inSource: false, inTrash: true, targetUid: "42" },
      },
      { ...successPayload, error: null },
    ]) {
      nextResponse = response(200, malformedSuccess);
      assertInvalidResponse(await mutateProviderTrashMessage(request));
    }

    for (const invalidSuccessfulTransport of [
      rawResponse(200, ""),
      rawResponse(200, "not-json"),
      response(201, successPayload),
      response(204, successPayload),
    ]) {
      nextResponse = invalidSuccessfulTransport;
      assertInvalidResponse(await mutateProviderTrashMessage(request));
    }

    for (const code of [
      "gmail_connection_not_found",
      "gmail_connection_not_ready",
      "gmail_modify_scope_required",
      "gmail_permission_denied",
      "gmail_rate_limited",
      "gmail_refresh_unavailable",
      "gmail_token_store_unavailable",
      "gmail_trash_failed",
      "internal_error",
      "invalid_trash_request",
      "mailbox_ownership_unavailable",
      "reconnect_required",
      "trash_provider_not_supported",
      "trash_source_invalid",
      "trash_source_unconfirmed",
      "unauthorized",
      "unsupported_provider",
      "user_config_store_unavailable",
    ]) {
      nextResponse = response(403, {
        ok: false,
        error: {
          code,
          message: "raw provider detail must not escape",
        },
      });
      assert.deepEqual(await mutateProviderTrashMessage(request), {
        ok: false,
        error: {
          code,
          message: "Could not complete this Trash request safely.",
        },
      });
    }

    for (const malformedFailure of [
      {
        ok: false,
        error: { code: "unknown_provider_error", message: "raw detail" },
      },
      {
        ok: false,
        error: { code: "gmail_trash_failed", message: "raw detail" },
        providerError: "must not escape",
      },
      {
        ok: false,
        error: {
          code: "gmail_trash_failed",
          message: "raw detail",
          accessToken: "must not escape",
        },
      },
    ]) {
      nextResponse = response(403, malformedFailure);
      const result = await mutateProviderTrashMessage(request);
      assertInvalidResponse(result);
      assert.doesNotMatch(
        JSON.stringify(result),
        /raw detail|must not escape|unknown_provider_error/,
      );
    }

    const uncertainPayload = {
      ok: false,
      status: "mutation_unconfirmed",
      action: "trash",
      provider: "gmail",
      mailboxId: MAILBOX_ID,
      providerMessageId: PROVIDER_MESSAGE_ID,
      sourceFolder: "INBOX",
      destinationFolder: "TRASH",
      error: {
        code: "trash_mutation_unconfirmed",
        message: "raw provider uncertainty must not escape",
      },
    } as const;
    const callsBeforeUncertain = captured.length;
    nextResponse = response(502, uncertainPayload);
    assert.deepEqual(await mutateProviderTrashMessage(request), {
      ...uncertainPayload,
      error: {
        code: "trash_mutation_unconfirmed",
        message:
          "Trash may have completed, but provider confirmation was not definitive.",
      },
    });
    assert.equal(
      captured.length,
      callsBeforeUncertain + 1,
      "an uncertain mutation must not be retried",
    );

    for (const malformedUncertain of [
      { ...uncertainPayload, mailboxId: "other-mailbox" },
      { ...uncertainPayload, providerMessageId: "other-message" },
      { ...uncertainPayload, provider: "imap" },
      { ...uncertainPayload, destinationFolder: "BIN" },
      { ...uncertainPayload, readback: { inSource: false, inTrash: true } },
      {
        ...uncertainPayload,
        error: {
          ...uncertainPayload.error,
          providerError: "must not escape",
        },
      },
    ]) {
      nextResponse = response(502, malformedUncertain);
      assertInvalidResponse(await mutateProviderTrashMessage(request));
    }

    nextResponse = response(200, uncertainPayload);
    assertInvalidResponse(await mutateProviderTrashMessage(request));

    networkFailure = new Error("provider secret must not escape");
    const callsBeforeNetworkFailure = captured.length;
    assert.deepEqual(await mutateProviderTrashMessage(request), {
      ok: false,
      status: "mutation_unconfirmed",
      action: "trash",
      provider: "gmail",
      mailboxId: MAILBOX_ID,
      providerMessageId: PROVIDER_MESSAGE_ID,
      sourceFolder: "INBOX",
      destinationFolder: "TRASH",
      error: {
        code: "trash_mutation_unconfirmed",
        message:
          "Trash may have completed, but provider confirmation was not definitive.",
      },
    });
    assert.equal(captured.length, callsBeforeNetworkFailure + 1);

    networkFailure = null;
    nextResponse = response(200, emptyTrashFetchPayload);
    assert.deepEqual(await fetchGmailTrash(MAILBOX_ID), emptyTrashFetchPayload);
    assert.equal(lastRequest().url, "/api/inboxes/fetch-trash");
    assert.equal(lastRequest().init.method, "POST");
    assert.equal(lastRequest().init.credentials, "include");
    assert.equal(lastRequest().init.cache, "no-store");
    assert.deepEqual(lastRequest().init.headers, {
      "Content-Type": "application/json",
    });
    assert.deepEqual(lastBody(), { mailboxId: MAILBOX_ID });

    nextResponse = response(200, multiMessageTrashFetchPayload);
    assert.deepEqual(
      await fetchGmailTrash(MAILBOX_ID),
      multiMessageTrashFetchPayload,
      "distinct provider message ids may share one provider thread id",
    );

    for (const mismatchedSnapshot of [
      { ...multiMessageTrashFetchPayload, mailboxId: "other-mailbox" },
      {
        ...multiMessageTrashFetchPayload,
        folder: {
          ...multiMessageTrashFetchPayload.folder,
          serverMailboxId: "other-mailbox",
        },
      },
      {
        ...multiMessageTrashFetchPayload,
        folder: {
          ...multiMessageTrashFetchPayload.folder,
          providerFolder: "Archive",
        },
      },
      {
        ...multiMessageTrashFetchPayload,
        folder: {
          ...multiMessageTrashFetchPayload.folder,
          providerFolder: "TRASH",
        },
      },
      {
        ...multiMessageTrashFetchPayload,
        folder: {
          ...multiMessageTrashFetchPayload.folder,
          uidValidity: "other-provider-generation",
        },
      },
      {
        ...multiMessageTrashFetchPayload,
        folder: {
          ...multiMessageTrashFetchPayload.folder,
          messages: [
            { ...trashMessageOne, serverMailboxId: "other-mailbox" },
          ],
        },
      },
      {
        ...multiMessageTrashFetchPayload,
        folder: {
          ...multiMessageTrashFetchPayload.folder,
          messages: [{ ...trashMessageOne, providerFolder: "Inbox" }],
        },
      },
    ]) {
      nextResponse = response(200, mismatchedSnapshot);
      assertInvalidTrashFetchResponse(await fetchGmailTrash(MAILBOX_ID));
    }

    for (const invalidLabelIds of [
      [],
      ["STARRED"],
      ["INBOX"],
      ["TRASH", "INBOX"],
    ]) {
      nextResponse = response(200, {
        ...emptyTrashFetchPayload,
        folder: {
          ...emptyTrashFetchPayload.folder,
          messages: [{ ...trashMessageOne, labelIds: invalidLabelIds }],
        },
      });
      assertInvalidTrashFetchResponse(await fetchGmailTrash(MAILBOX_ID));
    }

    nextResponse = response(200, {
      ...multiMessageTrashFetchPayload,
      folder: {
        ...multiMessageTrashFetchPayload.folder,
        messages: [
          trashMessageOne,
          {
            ...trashMessageTwo,
            providerMessageId: trashMessageOne.providerMessageId,
            providerThreadId: "thread-different-from-first",
          },
        ],
      },
    });
    assertInvalidTrashFetchResponse(await fetchGmailTrash(MAILBOX_ID));

    for (const invalidIdentityMessage of [
      {
        ...trashMessageOne,
        providerMessageId: trashMessageOne.providerThreadId,
      },
      { ...trashMessageOne, providerThreadId: "" },
    ]) {
      nextResponse = response(200, {
        ...emptyTrashFetchPayload,
        folder: {
          ...emptyTrashFetchPayload.folder,
          messages: [invalidIdentityMessage],
        },
      });
      assertInvalidTrashFetchResponse(await fetchGmailTrash(MAILBOX_ID));
    }

    for (const malformedOrAuthorityBearingPayload of [
      { ...emptyTrashFetchPayload, unexpected: true },
      { ...emptyTrashFetchPayload, accessToken: "must-not-escape" },
      {
        ...emptyTrashFetchPayload,
        folder: {
          ...emptyTrashFetchPayload.folder,
          unexpected: true,
        },
      },
      {
        ...emptyTrashFetchPayload,
        folder: {
          ...emptyTrashFetchPayload.folder,
          rawProviderResponse: "must-not-escape",
        },
      },
      {
        ...emptyTrashFetchPayload,
        folder: {
          ...emptyTrashFetchPayload.folder,
          messages: [{ ...trashMessageOne, unexpected: true }],
        },
      },
      {
        ...emptyTrashFetchPayload,
        folder: {
          ...emptyTrashFetchPayload.folder,
          messages: [{ ...trashMessageOne, oauthToken: "must-not-escape" }],
        },
      },
      { ...emptyTrashFetchPayload, folder: null },
      {
        ...emptyTrashFetchPayload,
        folder: { ...emptyTrashFetchPayload.folder, messages: {} },
      },
      {
        ...emptyTrashFetchPayload,
        folder: {
          ...emptyTrashFetchPayload.folder,
          messages: [{ ...trashMessageOne, body: "not-an-array" }],
        },
      },
    ]) {
      nextResponse = response(200, malformedOrAuthorityBearingPayload);
      const result = await fetchGmailTrash(MAILBOX_ID);
      assertInvalidTrashFetchResponse(result);
      assert.doesNotMatch(
        JSON.stringify(result),
        /must-not-escape|rawProviderResponse|oauthToken|accessToken/,
      );
    }

    for (const emptyOrMalformedTransport of [
      rawResponse(200, ""),
      rawResponse(200, "   "),
      rawResponse(200, "not-json"),
      response(201, emptyTrashFetchPayload),
    ]) {
      nextResponse = emptyOrMalformedTransport;
      assertInvalidTrashFetchResponse(await fetchGmailTrash(MAILBOX_ID));
    }

    nextResponse = response(403, {
      ok: false,
      error: {
        code: "unsupported_provider",
        message: "raw wrong-provider detail must not escape",
      },
    });
    assert.deepEqual(await fetchGmailTrash(MAILBOX_ID), {
      ok: false,
      error: {
        code: "unsupported_provider",
        message: "Could not load Trash from this Gmail mailbox safely.",
      },
    });

    for (const malformedProviderFailure of [
      {
        ok: false,
        error: {
          code: "imap_fetch_failed",
          message: "raw wrong-provider detail must not escape",
        },
      },
      {
        ok: false,
        error: {
          code: "unsupported_provider",
          message: "raw wrong-provider detail must not escape",
          providerToken: "must-not-escape",
        },
      },
      {
        ok: false,
        error: {
          code: "unsupported_provider",
          message: "raw wrong-provider detail must not escape",
        },
        providerError: "must-not-escape",
      },
    ]) {
      nextResponse = response(403, malformedProviderFailure);
      const result = await fetchGmailTrash(MAILBOX_ID);
      assertInvalidTrashFetchResponse(result);
      assert.doesNotMatch(
        JSON.stringify(result),
        /raw wrong-provider detail|must-not-escape|imap_fetch_failed/,
      );
    }

    const callsBeforeInvalidTrashFetchRequest = captured.length;
    assert.deepEqual(await fetchGmailTrash(""), {
      ok: false,
      error: {
        code: "invalid_trash_fetch_request",
        message: "A valid Gmail mailbox identity is required.",
      },
    });
    assert.equal(captured.length, callsBeforeInvalidTrashFetchRequest);

    networkFailure = new Error("raw Gmail provider secret must not escape");
    const callsBeforeTrashFetchNetworkFailure = captured.length;
    assert.deepEqual(await fetchGmailTrash(MAILBOX_ID), {
      ok: false,
      error: {
        code: "gmail_trash_fetch_failed",
        message: "Could not load Trash from this Gmail mailbox.",
      },
    });
    assert.equal(captured.length, callsBeforeTrashFetchNetworkFailure + 1);
  } finally {
    globalThis.fetch = originalFetch;
  }

  console.log("inboxConnectionApi Trash mutation and fetch client tests passed");
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
