import assert from "node:assert/strict";
import {
  listImapTrashFolders,
  saveImapTrashFolderMapping,
  type ImapTrashFolderInventorySuccess,
} from "./imapFolderMappingApi";

type CapturedRequest = {
  url: string;
  init: RequestInit;
};

const MAILBOX_ID = "server-mailbox-1";

function rawResponse(status: number, rawPayload: string): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    text: async () => rawPayload,
  } as Response;
}

function response(status: number, payload: unknown): Response {
  return rawResponse(status, JSON.stringify(payload));
}

function inventory({
  mode,
  currentFolder,
  folders,
}: {
  mode: "automatic" | "needs_mapping" | "configured";
  currentFolder: string | null;
  folders: string[];
}): ImapTrashFolderInventorySuccess {
  return {
    ok: true,
    mailboxId: MAILBOX_ID,
    trash: { mode, currentFolder },
    folders: folders.map((providerFolder) => ({ providerFolder })),
  };
}

async function run() {
  const originalFetch = globalThis.fetch;
  const captured: CapturedRequest[] = [];
  let nextResponse = response(
    200,
    inventory({
      mode: "automatic",
      currentFolder: "Trash",
      folders: ["Trash", "Sent"],
    }),
  );
  let networkFailure: Error | null = null;
  let passedCases = 0;

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
  const expectInvalidListResponse = async () => {
    assert.deepEqual(await listImapTrashFolders({ mailboxId: MAILBOX_ID }), {
      ok: false,
      error: {
        code: "imap_folder_list_response_invalid",
        message: "The IMAP folder list could not be verified safely.",
      },
    });
  };

  try {
    assert.deepEqual(
      await listImapTrashFolders({ mailboxId: MAILBOX_ID }),
      inventory({
        mode: "automatic",
        currentFolder: "Trash",
        folders: ["Trash", "Sent"],
      }),
    );
    assert.equal(lastRequest().url, "/api/inboxes/list-imap-folders");
    assert.equal(lastRequest().init.method, "POST");
    assert.equal(lastRequest().init.credentials, "include");
    assert.equal(lastRequest().init.cache, "no-store");
    assert.deepEqual(lastRequest().init.headers, {
      "Content-Type": "application/json",
    });
    assert.deepEqual(lastBody(), { mailboxId: MAILBOX_ID });
    passedCases += 1;

    const opaqueFolders = [
      "Archive/Deleted",
      "Projects/\"Done\"\\2026",
      "&AMk-",
      "Prullenbak 🗑",
    ];
    nextResponse = response(
      200,
      inventory({
        mode: "needs_mapping",
        currentFolder: null,
        folders: opaqueFolders,
      }),
    );
    assert.deepEqual(
      await listImapTrashFolders({ mailboxId: MAILBOX_ID }),
      inventory({
        mode: "needs_mapping",
        currentFolder: null,
        folders: opaqueFolders,
      }),
      "opaque modified UTF-7, Unicode, delimiter-like text, quotes, and backslashes stay exact",
    );
    passedCases += 1;

    const configured = inventory({
      mode: "configured",
      currentFolder: opaqueFolders[1],
      folders: opaqueFolders,
    });
    nextResponse = response(200, configured);
    assert.deepEqual(
      await saveImapTrashFolderMapping({
        mailboxId: MAILBOX_ID,
        role: "trash",
        selectedFolder: opaqueFolders[1],
      }),
      configured,
    );
    assert.equal(
      lastRequest().url,
      "/api/inboxes/save-imap-folder-mapping",
    );
    assert.deepEqual(lastBody(), {
      mailboxId: MAILBOX_ID,
      role: "trash",
      selectedFolder: opaqueFolders[1],
    });
    passedCases += 1;

    nextResponse = response(
      200,
      inventory({
        mode: "automatic",
        currentFolder: "Provider Trash",
        folders: ["Provider Trash", ...opaqueFolders],
      }),
    );
    assert.equal(
      (
        await saveImapTrashFolderMapping({
          mailboxId: MAILBOX_ID,
          role: "trash",
          selectedFolder: opaqueFolders[0],
        })
      ).ok,
      true,
      "a save may authoritatively resolve to automatic when SPECIAL-USE appears",
    );
    passedCases += 1;

    nextResponse = response(
      200,
      inventory({
        mode: "needs_mapping",
        currentFolder: null,
        folders: opaqueFolders,
      }),
    );
    assert.deepEqual(
      await saveImapTrashFolderMapping({
        mailboxId: MAILBOX_ID,
        role: "trash",
        selectedFolder: opaqueFolders[0],
      }),
      {
        ok: false,
        error: {
          code: "imap_folder_mapping_response_invalid",
          message:
            "The saved IMAP folder mapping could not be verified safely.",
        },
      },
      "save must never claim success while authoritative state still needs mapping",
    );
    passedCases += 1;

    const requestsBeforeInvalidList = captured.length;
    for (const mailboxId of [
      "",
      " padded",
      "padded ",
      "mailbox\ridentity",
      "mailbox\nidentity",
      `mailbox${String.fromCharCode(0x80)}identity`,
      `mailbox${String.fromCharCode(0x9f)}identity`,
      `mailbox${String.fromCharCode(0xd800)}identity`,
      `mailbox${String.fromCharCode(0xdc00)}identity`,
      `mailbox${String.fromCharCode(0)}identity`,
      "x".repeat(513),
    ]) {
      assert.deepEqual(await listImapTrashFolders({ mailboxId }), {
        ok: false,
        error: {
          code: "invalid_imap_folder_list_request",
          message: "A valid connected IMAP mailbox is required.",
        },
      });
    }
    assert.equal(captured.length, requestsBeforeInvalidList);
    passedCases += 1;

    const validSave = {
      mailboxId: MAILBOX_ID,
      role: "trash" as const,
      selectedFolder: "Provider Deleted",
    };
    const requestsBeforeInvalidSave = captured.length;
    for (const invalidSave of [
      { ...validSave, role: "archive" },
      { ...validSave, selectedFolder: "" },
      { ...validSave, selectedFolder: " Deleted" },
      { ...validSave, selectedFolder: "Deleted " },
      { ...validSave, selectedFolder: "INBOX" },
      { ...validSave, selectedFolder: "Inbox" },
      { ...validSave, selectedFolder: "Deleted\rItems" },
      { ...validSave, selectedFolder: "Deleted\nItems" },
      {
        ...validSave,
        selectedFolder: `Deleted${String.fromCharCode(0x80)}Items`,
      },
      {
        ...validSave,
        selectedFolder: `Deleted${String.fromCharCode(0x9f)}Items`,
      },
      {
        ...validSave,
        selectedFolder: `Deleted${String.fromCharCode(0xd800)}Items`,
      },
      {
        ...validSave,
        selectedFolder: `Deleted${String.fromCharCode(0xdc00)}Items`,
      },
      {
        ...validSave,
        selectedFolder: `Deleted${String.fromCharCode(0)}Items`,
      },
      { ...validSave, selectedFolder: "x".repeat(16_385) },
      { ...validSave, archiveFolder: "browser-authority" },
      { ...validSave, host: "must-not-be-sent.example" },
    ]) {
      assert.deepEqual(
        await saveImapTrashFolderMapping(
          invalidSave as typeof validSave,
        ),
        {
          ok: false,
          error: {
            code: "invalid_imap_folder_mapping_request",
            message: "Choose one current provider folder for this IMAP mailbox.",
          },
        },
      );
    }
    assert.equal(captured.length, requestsBeforeInvalidSave);
    passedCases += 1;

    const safeBase = inventory({
      mode: "needs_mapping",
      currentFolder: null,
      folders: ["Provider Deleted"],
    });
    for (const malformedSuccess of [
      { ...safeBase, mailboxId: "other-mailbox" },
      { ...safeBase, host: "secret.example" },
      { ...safeBase, password: "must-not-escape" },
      { ...safeBase, credentialVersion: 7 },
      { ...safeBase, token: "must-not-escape" },
      { ...safeBase, trash: { ...safeBase.trash, rawAttributes: ["\\Trash"] } },
      {
        ...safeBase,
        folders: [{ providerFolder: "Provider Deleted", selectable: true }],
      },
    ]) {
      nextResponse = response(200, malformedSuccess);
      await expectInvalidListResponse();
    }
    passedCases += 1;

    for (const invalidTrashState of [
      { mode: "needs_mapping", currentFolder: "Provider Deleted" },
      { mode: "automatic", currentFolder: null },
      { mode: "configured", currentFolder: null },
      { mode: "heuristic", currentFolder: "Trash" },
      { mode: "automatic", currentFolder: " Trash" },
    ]) {
      nextResponse = response(200, { ...safeBase, trash: invalidTrashState });
      await expectInvalidListResponse();
    }
    passedCases += 1;

    for (const invalidFolders of [
      [{ providerFolder: "Provider Deleted" }, { providerFolder: "Provider Deleted" }],
      [{ providerFolder: " Deleted" }],
      [{ providerFolder: "Deleted " }],
      [{ providerFolder: "INBOX" }],
      [{ providerFolder: "inbox" }],
      [{ providerFolder: "Deleted\rItems" }],
      [{ providerFolder: "Deleted\nItems" }],
      [{ providerFolder: `Deleted${String.fromCharCode(0x80)}Items` }],
      [{ providerFolder: `Deleted${String.fromCharCode(0x9f)}Items` }],
      [{ providerFolder: `Deleted${String.fromCharCode(0xd800)}Items` }],
      [{ providerFolder: `Deleted${String.fromCharCode(0xdc00)}Items` }],
      [{ providerFolder: `Deleted${String.fromCharCode(0)}Items` }],
      [{ providerFolder: "x".repeat(16_385) }],
      [{ mailbox: "Provider Deleted" }],
    ]) {
      nextResponse = response(200, { ...safeBase, folders: invalidFolders });
      await expectInvalidListResponse();
    }
    passedCases += 1;

    nextResponse = response(
      200,
      inventory({
        mode: "configured",
        currentFolder: "Stale Deleted",
        folders: ["Current Deleted"],
      }),
    );
    await expectInvalidListResponse();
    passedCases += 1;

    nextResponse = response(403, {
      ok: false,
      error: {
        code: "mailbox_not_owned",
        message: "This mailbox is not available.",
      },
    });
    assert.deepEqual(await listImapTrashFolders({ mailboxId: MAILBOX_ID }), {
      ok: false,
      error: {
        code: "mailbox_not_owned",
        message: "This mailbox is not available.",
      },
    });
    passedCases += 1;

    for (const unsafeFailure of [
      {
        ok: false,
        error: { code: "imap_failed", message: "safe" },
        providerError: "raw provider failure",
      },
      {
        ok: false,
        error: {
          code: "imap_failed",
          message: "raw\rprovider failure",
        },
      },
      {
        ok: false,
        error: {
          code: "imap_failed",
          message: "safe",
          host: "secret.example",
        },
      },
    ]) {
      nextResponse = response(502, unsafeFailure);
      const result = await listImapTrashFolders({ mailboxId: MAILBOX_ID });
      assert.deepEqual(result, {
        ok: false,
        error: {
          code: "imap_folder_list_response_invalid",
          message: "The IMAP folder list could not be verified safely.",
        },
      });
      assert.doesNotMatch(
        JSON.stringify(result),
        /raw provider failure|secret\.example/,
      );
    }
    passedCases += 1;

    nextResponse = response(201, safeBase);
    await expectInvalidListResponse();
    nextResponse = rawResponse(200, "not-json");
    await expectInvalidListResponse();
    nextResponse = rawResponse(200, "x".repeat(4 * 1_024 * 1_024 + 1));
    await expectInvalidListResponse();
    passedCases += 1;

    networkFailure = new Error("raw host and password must never escape");
    const result = await listImapTrashFolders({ mailboxId: MAILBOX_ID });
    assert.deepEqual(result, {
      ok: false,
      error: {
        code: "imap_folder_list_failed",
        message: "Could not load IMAP folders safely.",
      },
    });
    assert.doesNotMatch(JSON.stringify(result), /raw host|password/);
    passedCases += 1;
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(passedCases, 15);
  console.log(`IMAP folder mapping API client tests passed (${passedCases} cases)`);
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
