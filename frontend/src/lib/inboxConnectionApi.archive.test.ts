import assert from "node:assert/strict";
import {
  fetchProviderArchive,
  mutateInboxMessageAction,
  mutateProviderArchiveMessage,
  type GmailArchiveMutationRequest,
  type ImapArchiveMutationRequest,
} from "./inboxConnectionApi";

type CapturedRequest = {
  url: string;
  init: RequestInit;
};

const MAILBOX_ID = "server-mailbox-1";
const GMAIL_MESSAGE_ID = "provider-message-1";
const GMAIL_THREAD_ID = "provider-thread-1";
const IMAP_SOURCE_UID = "123";
const IMAP_SOURCE_UID_VALIDITY = "456";
const IMAP_ARCHIVE_UID = "900";
const IMAP_ARCHIVE_UID_VALIDITY = "789";
const IMAP_ARCHIVE_FOLDER = "Stored Archive";

function gmailMessage(
  providerFolder: "Inbox" | "Archive",
  providerMessageId = GMAIL_MESSAGE_ID,
) {
  return {
    id: "rfc-message@example.test",
    sender: "Sender",
    subject: "Provider message",
    snippet: "Provider body",
    from: "sender@example.test",
    to: "owner@example.test",
    timestamp: "July 27 at 10:00",
    createdAt: "2026-07-27T08:00:00.000Z",
    body: ["Provider body"],
    serverMailboxId: MAILBOX_ID,
    providerFolder,
    providerMessageId,
    providerThreadId: GMAIL_THREAD_ID,
    rfcMessageId: "rfc-message@example.test",
    labelIds: providerFolder === "Inbox" ? ["INBOX"] : ["STARRED"],
  };
}

function gmailSnapshot(
  providerFolder: "Inbox" | "Archive",
  messages: ReturnType<typeof gmailMessage>[] = [],
) {
  return {
    serverMailboxId: MAILBOX_ID,
    providerFolder,
    uidValidity: "gmail-api",
    messages,
  };
}

function gmailMutationSuccess() {
  return {
    ok: true,
    status: "ok",
    action: "archive",
    mailboxId: MAILBOX_ID,
    archivedMessageIdentity: {
      serverMailboxId: MAILBOX_ID,
      providerMessageId: GMAIL_MESSAGE_ID,
      providerThreadId: GMAIL_THREAD_ID,
      providerFolder: "Archive",
      rfcMessageId: "rfc-message@example.test",
    },
    delta: {
      Inbox: {
        removeProviderMessageId: GMAIL_MESSAGE_ID,
      },
      Archive: {
        upsertMessage: gmailMessage("Archive"),
      },
    },
  };
}

function oldGmailFullSnapshotMutationSuccess() {
  return {
    ok: true,
    status: "ok",
    action: "archive",
    mailboxId: MAILBOX_ID,
    archivedMessageIdentity: {
      serverMailboxId: MAILBOX_ID,
      providerMessageId: GMAIL_MESSAGE_ID,
      providerThreadId: GMAIL_THREAD_ID,
      providerFolder: "Archive",
      rfcMessageId: "rfc-message@example.test",
    },
    folders: {
      Inbox: gmailSnapshot("Inbox"),
      Archive: gmailSnapshot("Archive", [gmailMessage("Archive")]),
    },
  };
}

function imapMessage(
  providerFolder: string,
  uidValidity: string,
  imapUid: string,
) {
  return {
    id: "rfc-message@example.test",
    sender: "Sender",
    subject: "Provider message",
    snippet: "Provider body",
    from: "sender@example.test",
    to: "owner@example.test",
    timestamp: "July 27 at 10:00",
    createdAt: "2026-07-27T08:00:00.000Z",
    body: ["Provider body"],
    serverMailboxId: MAILBOX_ID,
    providerFolder,
    uidValidity,
    imapUid,
    threadId: `imap:uid:${MAILBOX_ID}:${providerFolder}:${uidValidity}:${imapUid}`,
    rfcMessageId: "rfc-message@example.test",
  };
}

function imapSnapshot(
  providerFolder: string,
  uidValidity: string,
  imapUidSet: string[],
  messages: ReturnType<typeof imapMessage>[] = [],
) {
  return {
    serverMailboxId: MAILBOX_ID,
    providerFolder,
    uidValidity,
    imapUidSet,
    messages,
  };
}

function imapMutationSuccess() {
  return {
    ok: true,
    status: "ok",
    action: "archive",
    mailboxId: MAILBOX_ID,
    archivedMessageIdentity: {
      serverMailboxId: MAILBOX_ID,
      sourceProviderFolder: "INBOX",
      sourceImapUid: IMAP_SOURCE_UID,
      sourceUidValidity: IMAP_SOURCE_UID_VALIDITY,
      providerFolder: IMAP_ARCHIVE_FOLDER,
      imapUid: IMAP_ARCHIVE_UID,
      uidValidity: IMAP_ARCHIVE_UID_VALIDITY,
      rfcMessageId: "rfc-message@example.test",
    },
    folders: {
      Inbox: imapSnapshot("INBOX", IMAP_SOURCE_UID_VALIDITY, []),
      Archive: imapSnapshot(
        IMAP_ARCHIVE_FOLDER,
        IMAP_ARCHIVE_UID_VALIDITY,
        [IMAP_ARCHIVE_UID],
        [
          imapMessage(
            IMAP_ARCHIVE_FOLDER,
            IMAP_ARCHIVE_UID_VALIDITY,
            IMAP_ARCHIVE_UID,
          ),
        ],
      ),
    },
  };
}

function response(
  status: number,
  payload: unknown,
  rawPayload = JSON.stringify(payload),
): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => rawPayload,
  } as Response;
}

async function run() {
  // Other focused API-client suites own global fetch while their promise
  // chains run. Yield once so this standalone contract test cannot race them
  // when a broader harness requires both files in one process.
  await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));
  const originalFetch = globalThis.fetch;
  const captured: CapturedRequest[] = [];
  let nextResponse = response(200, gmailMutationSuccess());
  let networkFailure: Error | null = null;

  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    captured.push({ url, init: init ?? {} });
    if (networkFailure) throw networkFailure;
    return nextResponse;
  }) as typeof fetch;

  const lastRequest = () => captured[captured.length - 1];
  const lastBody = () =>
    JSON.parse(String(lastRequest().init.body)) as Record<string, unknown>;
  const assertArchiveTransport = (url: string) => {
    assert.equal(lastRequest().url, url);
    assert.equal(lastRequest().init.method, "POST");
    assert.equal(lastRequest().init.credentials, "include");
    assert.equal(lastRequest().init.cache, "no-store");
    assert.deepEqual(lastRequest().init.headers, {
      "Content-Type": "application/json",
    });
  };

  try {
    const gmailRequest: GmailArchiveMutationRequest = {
      mailboxId: MAILBOX_ID,
      messageId: GMAIL_MESSAGE_ID,
      action: "archive",
    };
    const gmailSuccessPayload = gmailMutationSuccess();
    nextResponse = response(200, gmailSuccessPayload);
    assert.deepEqual(
      await mutateProviderArchiveMessage(gmailRequest),
      gmailSuccessPayload,
    );
    assertArchiveTransport("/api/inboxes/message-action");
    assert.deepEqual(lastBody(), gmailRequest);

    nextResponse = response(200, oldGmailFullSnapshotMutationSuccess());
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "the former full folders response is no longer a valid Gmail success",
    );

    const imapRequest: ImapArchiveMutationRequest = {
      mailboxId: MAILBOX_ID,
      folder: "INBOX",
      uid: IMAP_SOURCE_UID,
      uidValidity: IMAP_SOURCE_UID_VALIDITY,
      action: "archive",
    };
    const imapSuccessPayload = imapMutationSuccess();
    nextResponse = response(200, imapSuccessPayload);
    assert.deepEqual(
      await mutateProviderArchiveMessage(imapRequest),
      imapSuccessPayload,
    );
    assertArchiveTransport("/api/inboxes/message-action");
    assert.deepEqual(lastBody(), imapRequest);

    const callsBeforeForgedRequest = captured.length;
    assert.deepEqual(
      await mutateProviderArchiveMessage({
        ...gmailRequest,
        accessToken: "client-forged-token",
      } as GmailArchiveMutationRequest),
      {
        ok: false,
        error: {
          code: "invalid_archive_request",
          message: "Archive requires one valid provider message identity.",
        },
      },
    );
    assert.equal(
      captured.length,
      callsBeforeForgedRequest,
      "unknown authority fields must fail before a request is sent",
    );

    const uncertainPayload = {
      ok: false,
      status: "mutation_confirmed_readback_failed",
      action: "archive",
      mailboxId: MAILBOX_ID,
      archivedMessageIdentity: {
        serverMailboxId: MAILBOX_ID,
        providerMessageId: GMAIL_MESSAGE_ID,
        providerFolder: "Archive",
      },
      error: {
        code: "archive_readback_failed",
        message:
          "Archive was confirmed, but the latest mailbox state could not be verified.",
      },
    };
    nextResponse = response(502, uncertainPayload);
    assert.deepEqual(
      await mutateProviderArchiveMessage(gmailRequest),
      uncertainPayload,
      "confirmed-mutation uncertainty must survive HTTP 502",
    );

    nextResponse = response(502, {
      ...uncertainPayload,
      archivedMessageIdentity: null,
      error: {
        code: "archive_readback_failed",
        message: "raw provider access-token-canary",
      },
    });
    const identitySafeUncertain =
      await mutateProviderArchiveMessage(gmailRequest);
    assert.deepEqual(identitySafeUncertain, uncertainPayload);
    assert.doesNotMatch(
      JSON.stringify(identitySafeUncertain),
      /access-token-canary/,
      "a malformed readback identity must remain uncertain using only trusted request identity",
    );

    nextResponse = response(502, {
      ...uncertainPayload,
      accessToken: "must-never-escape",
      error: {
        ...uncertainPayload.error,
        providerError: "raw-provider-error",
      },
    });
    const sanitizedUncertain = await mutateProviderArchiveMessage(gmailRequest);
    assert.equal(sanitizedUncertain.ok, false);
    assert.equal(
      "status" in sanitizedUncertain ? sanitizedUncertain.status : undefined,
      "mutation_confirmed_readback_failed",
    );
    assert.equal(
      "error" in sanitizedUncertain
        ? sanitizedUncertain.error.code
        : undefined,
      "archive_readback_failed",
    );
    assert.doesNotMatch(
      JSON.stringify(sanitizedUncertain),
      /must-never-escape|providerError|raw-provider-error/,
    );

    const unsafeSuccess = gmailMutationSuccess();
    (
      unsafeSuccess.delta.Archive.upsertMessage as Record<string, unknown>
    ).fingerprint = "internal-provider-fingerprint";
    nextResponse = response(200, unsafeSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
    );

    const clientStateSuccess = gmailMutationSuccess();
    (
      clientStateSuccess.delta.Archive.upsertMessage as Record<string, unknown>
    ).clientState = "client-selected-preview";
    nextResponse = response(200, clientStateSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "the server preview may not carry client-selected state",
    );

    const extraTopLevelSuccess = gmailMutationSuccess();
    (extraTopLevelSuccess as Record<string, unknown>).metadata = {};
    nextResponse = response(200, extraTopLevelSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "the Gmail delta success envelope must have exact keys",
    );

    const extraDeltaSuccess = gmailMutationSuccess();
    (extraDeltaSuccess.delta as Record<string, unknown>).Trash = {};
    nextResponse = response(200, extraDeltaSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "the Gmail delta may contain only Inbox and Archive",
    );

    const extraInboxDeltaSuccess = gmailMutationSuccess();
    (
      extraInboxDeltaSuccess.delta.Inbox as Record<string, unknown>
    ).clientState = "keep";
    nextResponse = response(200, extraInboxDeltaSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "the Gmail Inbox delta branch must have exact keys",
    );

    const extraArchiveDeltaSuccess = gmailMutationSuccess();
    (
      extraArchiveDeltaSuccess.delta.Archive as Record<string, unknown>
    ).clientState = "replace";
    nextResponse = response(200, extraArchiveDeltaSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "the Gmail Archive delta branch must have exact keys",
    );

    const missingArchiveDeltaSuccess = gmailMutationSuccess();
    delete (
      missingArchiveDeltaSuccess.delta as Partial<
        ReturnType<typeof gmailMutationSuccess>["delta"]
      >
    ).Archive;
    nextResponse = response(200, missingArchiveDeltaSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "both exact Gmail delta branches are required",
    );

    const mismatchedRemovalSuccess = gmailMutationSuccess();
    mismatchedRemovalSuccess.delta.Inbox.removeProviderMessageId =
      "other-provider-message";
    nextResponse = response(200, mismatchedRemovalSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "the Inbox removal identity must match the request",
    );

    const mismatchedIdentitySuccess = gmailMutationSuccess();
    mismatchedIdentitySuccess.archivedMessageIdentity.providerMessageId =
      "other-provider-message";
    nextResponse = response(200, mismatchedIdentitySuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "the archived identity must match the requested provider message",
    );

    const mismatchedUpsertMailboxSuccess = gmailMutationSuccess();
    mismatchedUpsertMailboxSuccess.delta.Archive.upsertMessage.serverMailboxId =
      "other-mailbox";
    nextResponse = response(200, mismatchedUpsertMailboxSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "the Archive upsert mailbox must match the request",
    );

    const mismatchedUpsertProviderIdSuccess = gmailMutationSuccess();
    mismatchedUpsertProviderIdSuccess.delta.Archive.upsertMessage.providerMessageId =
      "other-provider-message";
    nextResponse = response(200, mismatchedUpsertProviderIdSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "the Archive upsert provider id must match the request",
    );

    const mismatchedUpsertThreadSuccess = gmailMutationSuccess();
    mismatchedUpsertThreadSuccess.delta.Archive.upsertMessage.providerThreadId =
      "other-provider-thread";
    nextResponse = response(200, mismatchedUpsertThreadSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "the Archive upsert thread must match the archived identity",
    );

    for (const excludedLabel of [
      "INBOX",
      "TRASH",
      "SPAM",
      "DRAFT",
      "SENT",
    ]) {
      const excludedLabelSuccess = gmailMutationSuccess();
      excludedLabelSuccess.delta.Archive.upsertMessage.labelIds = [
        excludedLabel,
      ];
      nextResponse = response(200, excludedLabelSuccess);
      assert.equal(
        (await mutateProviderArchiveMessage(gmailRequest)).error.code,
        "archive_response_invalid",
        `${excludedLabel} is not valid on a Gmail Archive upsert`,
      );
    }

    const duplicateLabelsSuccess = gmailMutationSuccess();
    duplicateLabelsSuccess.delta.Archive.upsertMessage.labelIds = [
      "STARRED",
      "STARRED",
    ];
    nextResponse = response(200, duplicateLabelsSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "Gmail Archive labels must be unique",
    );

    const mismatchedImapSuccess = imapMutationSuccess();
    mismatchedImapSuccess.folders.Archive.uidValidity = "790";
    nextResponse = response(200, mismatchedImapSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(imapRequest)).error.code,
      "archive_response_invalid",
      "the returned IMAP identity must match the Archive snapshot",
    );

    nextResponse = response(201, gmailMutationSuccess());
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_mutation_failed",
      "a valid-looking payload on HTTP 201 is not success",
    );

    nextResponse = response(200, null, "");
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "an empty HTTP 200 body is not synthetic success",
    );

    nextResponse = response(200, null, "{not-json");
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "invalid JSON is not success",
    );

    for (const malformedPayload of [[], "ok", { ok: true }]) {
      nextResponse = response(200, malformedPayload);
      assert.equal(
        (await mutateProviderArchiveMessage(gmailRequest)).error.code,
        "archive_response_invalid",
        "non-object and incomplete payloads fail closed",
      );
    }

    const mismatchedMailboxSuccess = gmailMutationSuccess();
    mismatchedMailboxSuccess.mailboxId = "other-mailbox";
    nextResponse = response(200, mismatchedMailboxSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "the response mailbox must match the request",
    );

    const mismatchedFolderSuccess = gmailMutationSuccess();
    mismatchedFolderSuccess.delta.Archive.upsertMessage.providerFolder =
      "Inbox";
    nextResponse = response(200, mismatchedFolderSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "folder namespaces may not be relabeled by the client",
    );

    const malformedMessageSuccess = gmailMutationSuccess();
    delete (
      malformedMessageSuccess.delta.Archive.upsertMessage as Partial<
        ReturnType<typeof gmailMessage>
      >
    ).subject;
    nextResponse = response(200, malformedMessageSuccess);
    assert.equal(
      (await mutateProviderArchiveMessage(gmailRequest)).error.code,
      "archive_response_invalid",
      "messages without the provider preview contract are malformed",
    );

    const gmailFetchPayload = {
      ok: true,
      status: "ok",
      mailboxId: MAILBOX_ID,
      folder: gmailSnapshot("Archive"),
    };
    nextResponse = response(200, gmailFetchPayload);
    const gmailFetchResult = await fetchProviderArchive(MAILBOX_ID);
    assert.deepEqual(gmailFetchResult, gmailFetchPayload);
    assert.equal(
      gmailFetchResult.ok ? gmailFetchResult.folder.serverMailboxId : null,
      MAILBOX_ID,
    );
    assert.equal(
      gmailFetchResult.ok
        ? gmailFetchResult.folder.messages.length
        : null,
      0,
      "an empty valid Archive remains empty without synthesizing identities",
    );
    assertArchiveTransport("/api/inboxes/fetch-archive");
    assert.deepEqual(lastBody(), { mailboxId: MAILBOX_ID });

    const imapFetchPayload = {
      ok: true,
      status: "ok",
      mailboxId: MAILBOX_ID,
      folder: imapSnapshot(
        IMAP_ARCHIVE_FOLDER,
        IMAP_ARCHIVE_UID_VALIDITY,
        [IMAP_ARCHIVE_UID],
        [
          imapMessage(
            IMAP_ARCHIVE_FOLDER,
            IMAP_ARCHIVE_UID_VALIDITY,
            IMAP_ARCHIVE_UID,
          ),
        ],
      ),
    };
    nextResponse = response(200, imapFetchPayload);
    const imapFetchResult = await fetchProviderArchive(MAILBOX_ID);
    assert.deepEqual(imapFetchResult, imapFetchPayload);
    assert.equal(
      imapFetchResult.ok
        ? imapFetchResult.folder.messages[0]?.serverMailboxId
        : null,
      MAILBOX_ID,
    );
    assert.equal(
      imapFetchResult.ok && "imapUidSet" in imapFetchResult.folder
        ? imapFetchResult.folder.messages[0]?.imapUid
        : null,
      IMAP_ARCHIVE_UID,
      "provider identities survive validation unchanged",
    );

    nextResponse = response(200, {
      ...gmailFetchPayload,
      mailboxId: "other-mailbox",
    });
    assert.equal(
      (await fetchProviderArchive(MAILBOX_ID)).error.code,
      "archive_response_invalid",
    );

    nextResponse = response(200, {
      ...gmailFetchPayload,
      folder: {
        ...gmailFetchPayload.folder,
        identities: {
          [GMAIL_MESSAGE_ID]: {
            fingerprint: "private",
          },
        },
      },
    });
    assert.equal(
      (await fetchProviderArchive(MAILBOX_ID)).error.code,
      "archive_response_invalid",
    );

    nextResponse = response(201, gmailFetchPayload);
    assert.equal(
      (await fetchProviderArchive(MAILBOX_ID)).error.code,
      "archive_fetch_failed",
      "Archive fetch also requires exact HTTP 200",
    );

    nextResponse = response(403, {
      ok: false,
      error: {
        code: "gmail_permission_denied",
        message: "raw provider access-token-canary",
      },
    });
    assert.deepEqual(await fetchProviderArchive(MAILBOX_ID), {
      ok: false,
      error: {
        code: "gmail_permission_denied",
        message: "Could not complete this Archive request safely.",
      },
    });

    nextResponse = response(409, {
      ok: false,
      error: {
        code: "archive_folder_unavailable",
        message: "raw provider folder discovery detail",
      },
    });
    const attemptsBeforeUnavailableArchive = captured.length;
    assert.deepEqual(await fetchProviderArchive(MAILBOX_ID), {
      ok: false,
      error: {
        code: "archive_folder_unavailable",
        message: "Could not complete this Archive request safely.",
      },
    });
    assert.equal(
      captured.length,
      attemptsBeforeUnavailableArchive + 1,
      "an unavailable Archive capability is returned without a client retry",
    );
    assertArchiveTransport("/api/inboxes/fetch-archive");

    nextResponse = response(403, {
      ok: false,
      error: {
        code: "access_token_canary",
        message: "refresh-token-canary",
      },
    });
    const unknownUnsafeFailure = await fetchProviderArchive(MAILBOX_ID);
    assert.deepEqual(unknownUnsafeFailure, {
      ok: false,
      error: {
        code: "archive_fetch_failed",
        message: "Could not load Archive from the connected mailbox.",
      },
    });
    assert.doesNotMatch(
      JSON.stringify(unknownUnsafeFailure),
      /access_token_canary|refresh-token-canary/,
    );

    nextResponse = response(403, {
      ok: false,
      error: {
        code: "gmail_modify_scope_required",
        message: "raw provider scope detail must not escape",
      },
    });
    assert.deepEqual(await mutateProviderArchiveMessage(gmailRequest), {
      ok: false,
      error: {
        code: "gmail_modify_scope_required",
        message: "Could not complete this Archive request safely.",
      },
    });

    networkFailure = new Error("access-token-in-network-error");
    const attemptsBeforeNetworkFailure = captured.length;
    assert.deepEqual(await mutateProviderArchiveMessage(gmailRequest), {
      ok: false,
      error: {
        code: "archive_mutation_failed",
        message: "Could not archive this message in the connected mailbox.",
      },
    });
    assert.equal(
      captured.length,
      attemptsBeforeNetworkFailure + 1,
      "the Archive client must not retry a mutation",
    );
    networkFailure = null;

    nextResponse = response(200, { ok: true, action: "star" });
    assert.deepEqual(
      await mutateInboxMessageAction({
        mailboxId: MAILBOX_ID,
        messageId: GMAIL_MESSAGE_ID,
        action: "star",
      }),
      { ok: true, action: "star" },
      "the existing read/star action client remains unchanged",
    );
    assert.deepEqual(lastBody(), {
      mailboxId: MAILBOX_ID,
      messageId: GMAIL_MESSAGE_ID,
      action: "star",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  console.log("inboxConnectionApi Archive client tests passed");
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
