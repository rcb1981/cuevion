import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  buildConnectInboxRequest,
  buildRefreshInboxRequest,
  connectInboxWithImap,
  downloadAttachment,
  mutateInboxMessageAction,
  sendGmailMessage,
} from "./inboxConnectionApi";

type CapturedRequest = { url: string; init: Record<string, unknown> };

function response(payload: unknown, ok = true, status = 200) {
  const serialized = JSON.stringify(payload);
  return {
    ok,
    status,
    json: async () => payload,
    text: async () => serialized,
    blob: async () => ({ kind: "mock-blob" }),
  };
}

function requestBody(request: CapturedRequest) {
  return JSON.parse(String(request.init.body ?? "{}")) as Record<string, unknown>;
}

function lastCaptured(requests: CapturedRequest[]) {
  return requests[requests.length - 1];
}

function collectProductionSources(directory: string): string {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      return collectProductionSources(target);
    }
    if (!entry.name.endsWith(".ts") && !entry.name.endsWith(".tsx")) {
      return [];
    }
    if (entry.name.includes(".test.")) {
      return [];
    }
    return fs.readFileSync(target, "utf8");
  }).join("\n");
}

async function run() {
  // The existing Gmail client regression file also owns global fetch while its
  // promise chain runs. Wait one event-loop turn so these focused DTO checks do
  // not race that unchanged suite.
  await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));
  const captured: CapturedRequest[] = [];
  const originalFetch = globalThis.fetch;
  const originalWindow = (globalThis as any).window;
  (globalThis as any).window = {
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
  };
  (globalThis as any).fetch = async (url: string, init: Record<string, unknown>) => {
    captured.push({ url, init });
    if (url.endsWith("/download-attachment")) {
      return response(null);
    }
    if (url.endsWith("/connect-imap")) {
      return response({
        ok: true,
        messages: [{
          id: "message-1",
          imapUid: "42",
          threadId: "imap:rfc:stable-mailbox:INBOX:root%40example.com",
          sender: "Sender",
          subject: "Subject",
          snippet: "Body",
          from: "sender@example.com",
          to: "owner@example.com",
          timestamp: "July 13 at 10:00",
          createdAt: "2026-07-13T08:00:00.000Z",
          body: ["Body"],
        }],
        uidValidity: "900",
      });
    }
    return response({ ok: true, action: requestBody(lastCaptured(captured)).action });
  };

  try {
    const refresh = buildRefreshInboxRequest({
      mailboxId: "stable-mailbox",
      limit: 25,
      focusPreferences: { newsletters: "low" } as any,
    });
    const refreshResponse = await connectInboxWithImap(refresh);
    assert.equal(
      refreshResponse.messages?.[0]?.threadId,
      "imap:rfc:stable-mailbox:INBOX:root%40example.com",
    );
    assert.equal(refreshResponse.uidValidity, "900");
    assert.deepEqual(Object.keys(requestBody(lastCaptured(captured))).sort(), [
      "focusPreferences",
      "limit",
      "mailboxId",
      "mode",
    ]);
    assert.equal(refresh.mode, "refresh");

    const connection = {
      provider: "custom_imap" as const,
      email: "owner@example.com",
      customImap: {
        host: "imap.example.com",
        port: "993",
        ssl: true,
        username: "owner@example.com",
        password: "one-time-imap",
      },
      customSmtp: {
        host: "smtp.example.com",
        port: "587",
        security: "starttls" as const,
        username: "owner@example.com",
        password: "one-time-smtp",
        useSameCredentials: false,
      },
    };
    for (const mode of ["initial", "reconnect"] as const) {
      const request = buildConnectInboxRequest({
        mode,
        mailboxId: "stable-mailbox",
        ...connection,
      });
      await connectInboxWithImap(request);
      const body = requestBody(lastCaptured(captured));
      assert.equal(body.mode, mode);
      assert.equal(body.mailboxId, "stable-mailbox");
      assert.equal(
        (body.connection as any).imap.password,
        "one-time-imap",
      );
      assert.equal(
        (body.connection as any).smtp.password,
        "one-time-smtp",
      );
    }

    for (const action of ["mark_read", "mark_unread", "star", "unstar"] as const) {
      await mutateInboxMessageAction({
        mailboxId: "stable-mailbox",
        folder: "INBOX",
        uid: "42",
        uidValidity: "9",
        action,
      });
      assert.deepEqual(Object.keys(requestBody(lastCaptured(captured))).sort(), [
        "action",
        "folder",
        "mailboxId",
        "uid",
        "uidValidity",
      ]);
    }

    await downloadAttachment({
      mailboxId: "stable-mailbox",
      folder: "INBOX",
      uid: "42",
      uidValidity: "9",
      attachmentId: "part-2",
    });
    assert.deepEqual(Object.keys(requestBody(lastCaptured(captured))).sort(), [
      "attachmentId",
      "folder",
      "mailboxId",
      "uid",
      "uidValidity",
    ]);

    await sendGmailMessage({
      provider: "custom_imap",
      mailboxId: "stable-mailbox",
      authMode: "smtp",
      useSameCredentials: false,
      email: "spoof@example.com",
      username: "spoof-user",
      password: "must-not-send",
      smtpHost: "evil.example.com",
      smtpPort: "465",
      smtpSecurity: "ssl",
      from: "spoof@example.com",
      to: "to@example.com",
      cc: "cc@example.com",
      bcc: "bcc@example.com",
      subject: "Subject",
      bodyHtml: "<p>Body</p>",
      bodyText: "Body",
      attachments: [{
        name: "note.txt",
        mimeType: "text/plain",
        contentBase64: "bm90ZQ==",
      }],
    } as any);
    assert.deepEqual(Object.keys(requestBody(lastCaptured(captured))).sort(), [
      "attachments",
      "bcc",
      "bodyHtml",
      "bodyText",
      "cc",
      "mailboxId",
      "subject",
      "to",
    ]);

    const productionSources = collectProductionSources(path.resolve(__dirname, ".."));
    assert.equal(productionSources.includes("saveMailboxCredentials"), false);
    assert.doesNotMatch(
      productionSources,
      /fetch\(["']\/api\/inboxes\/credentials["'][\s\S]{0,300}method:\s*["']POST["']/,
    );
    const credentialsRoute = fs.readFileSync(
      path.resolve(__dirname, "../../api/inboxes/credentials.py"),
      "utf8",
    );
    assert.match(
      credentialsRoute,
      /def do_POST\(self\):[\s\S]*?405[\s\S]*?method_not_allowed/,
    );
  } finally {
    (globalThis as any).fetch = originalFetch;
    (globalThis as any).window = originalWindow;
  }

  console.log("inboxConnectionApi IMAP hardening DTO tests passed");
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
