import assert from "node:assert/strict";
import {
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
  } finally {
    globalThis.fetch = originalFetch;
  }

  console.log("inboxConnectionApi Trash client tests passed");
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
