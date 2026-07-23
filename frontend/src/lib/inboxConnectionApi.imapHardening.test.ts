import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  beginInboxConnection,
  beginOnboardingInboxConnection,
  buildConnectInboxRequest,
  buildOnboardingConnectInboxRequest,
  buildRefreshInboxRequest,
  connectInboxWithImap,
  downloadAttachment,
  mutateInboxMessageAction,
  projectManagedMailboxAccountConfigIdentity,
  resolveManagedMailboxIdentity,
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
    const firstManagedIdentity = resolveManagedMailboxIdentity({
      onboardingInboxId: "demo",
      serverMailboxId: "imap-server-a",
    });
    const secondManagedIdentity = resolveManagedMailboxIdentity({
      onboardingInboxId: "promo",
      serverMailboxId: "imap-server-b",
    });
    assert.deepEqual(firstManagedIdentity, {
      onboardingInboxId: "demo",
      mailboxId: "imap-server-a",
    });
    assert.deepEqual(secondManagedIdentity, {
      onboardingInboxId: "promo",
      mailboxId: "imap-server-b",
    });
    assert.notEqual(
      firstManagedIdentity.mailboxId,
      secondManagedIdentity.mailboxId,
    );
    assert.deepEqual(
      projectManagedMailboxAccountConfigIdentity({
        mailboxId: firstManagedIdentity.mailboxId,
        onboardingInboxId: firstManagedIdentity.onboardingInboxId,
      }),
      {
        id: "imap-server-a",
        onboardingInboxId: "demo",
      },
    );
    assert.deepEqual(
      [
        firstManagedIdentity,
        secondManagedIdentity,
      ].map((identity) =>
        projectManagedMailboxAccountConfigIdentity({
          mailboxId: identity.mailboxId,
          onboardingInboxId: identity.onboardingInboxId,
        }),
      ),
      [
        { id: "imap-server-a", onboardingInboxId: "demo" },
        { id: "imap-server-b", onboardingInboxId: "promo" },
      ],
    );
    const managedRefresh = buildRefreshInboxRequest({
      mailboxId: firstManagedIdentity.mailboxId,
    });
    assert.equal(managedRefresh.mode, "refresh");
    assert.equal(managedRefresh.mailboxId, "imap-server-a");
    const managedReconnect = buildConnectInboxRequest({
      mode: "reconnect",
      mailboxId: firstManagedIdentity.mailboxId,
      ...connection,
      customImap: {
        ...connection.customImap,
        password: "",
      },
      customSmtp: undefined,
    });
    assert.equal(managedReconnect.mailboxId, "imap-server-a");
    assert.equal("smtp" in managedReconnect.connection, false);

    for (const mode of ["initial", "reconnect"] as const) {
      const request = buildConnectInboxRequest({
        mode,
        mailboxId: "stable-mailbox",
        ...connection,
      });
      await connectInboxWithImap(request);
      const body = requestBody(lastCaptured(captured));
      assert.deepEqual(body, {
        mode,
        mailboxId: "stable-mailbox",
        connection: {
          provider: "custom_imap",
          email: "owner@example.com",
          imap: {
            host: "imap.example.com",
            port: "993",
            ssl: true,
            username: "owner@example.com",
            password: "one-time-imap",
          },
          smtp: {
            host: "smtp.example.com",
            port: "587",
            security: "starttls",
            username: "owner@example.com",
            useSameCredentials: false,
            password: "one-time-smtp",
          },
        },
      });
    }

    const invalidImapPasswords = [
      undefined,
      "",
      "   ",
      "********",
      "••••••••",
      "●●●●●●●●",
      "Stored securely",
      "Stored securely — leave blank to reuse",
    ];
    for (const password of invalidImapPasswords) {
      assert.throws(
        () =>
          buildConnectInboxRequest({
            mode: "initial",
            mailboxId: "stable-mailbox",
            provider: "custom_imap",
            email: connection.email,
            customImap: {
              ...connection.customImap,
              password,
            } as any,
          }),
        /IMAP password is required/,
      );

      const reconnect = buildConnectInboxRequest({
        mode: "reconnect",
        mailboxId: "stable-mailbox",
        provider: "custom_imap",
        email: connection.email,
        customImap: {
          ...connection.customImap,
          password,
        } as any,
      });
      assert.equal(
        Object.prototype.hasOwnProperty.call(reconnect.connection.imap, "password"),
        false,
      );
    }

    const tlsForcedRequest = buildConnectInboxRequest({
      mode: "initial",
      mailboxId: "stable-mailbox",
      provider: "custom_imap",
      email: connection.email,
      customImap: {
        ...connection.customImap,
        ssl: false,
      },
    });
    assert.equal(tlsForcedRequest.connection.imap.ssl, true);
    assert.equal("smtp" in tlsForcedRequest.connection, false);

    const emptySmtpRequest = buildConnectInboxRequest({
      mode: "initial",
      mailboxId: "stable-mailbox",
      provider: "custom_imap",
      email: connection.email,
      customImap: connection.customImap,
      customSmtp: {} as any,
    });
    assert.equal("smtp" in emptySmtpRequest.connection, false);

    const reconnectWithMaskedSmtp = buildConnectInboxRequest({
      mode: "reconnect",
      mailboxId: "stable-mailbox",
      provider: "custom_imap",
      email: connection.email,
      customImap: {
        ...connection.customImap,
        password: "********",
      },
      customSmtp: {
        ...connection.customSmtp,
        password: "••••••••",
        useSameCredentials: true,
        ignoredClientHint: "must-not-send",
      } as any,
    });
    assert.deepEqual(reconnectWithMaskedSmtp.connection.smtp, {
      host: "smtp.example.com",
      port: "587",
      security: "starttls",
      username: "",
      useSameCredentials: true,
    });
    assert.equal(
      Object.prototype.hasOwnProperty.call(
        reconnectWithMaskedSmtp.connection.imap,
        "password",
      ),
      false,
    );

    assert.throws(
      () =>
        buildConnectInboxRequest({
          mode: "initial",
          mailboxId: "stable-mailbox",
          provider: "custom_imap",
          email: connection.email,
          customImap: {
            ...connection.customImap,
            credentialVersion: "client-generation",
          } as any,
        }),
      /Credential generation is server-owned/,
    );

    const allowlistedReconnect = buildConnectInboxRequest({
      mode: "reconnect",
      mailboxId: "stable-mailbox",
      provider: "custom_imap",
      email: connection.email,
      customImap: {
        ...connection.customImap,
        password: "",
        ownerId: "must-not-send",
      } as any,
      ownerEmail: "must-not-send@example.com",
      workspaceId: "must-not-send",
    } as any);
    const serializedAllowlistedReconnect = JSON.stringify(allowlistedReconnect);
    for (const forbiddenValue of [
      "ownerId",
      "ownerEmail",
      "workspaceId",
      "must-not-send",
    ]) {
      assert.equal(serializedAllowlistedReconnect.includes(forbiddenValue), false);
    }

    const capturedBeforeInvalidInput = captured.length;
    const missingPasswordResult = await beginInboxConnection({
      imapMode: "initial",
      mailboxId: "stable-mailbox",
      provider: "custom_imap",
      email: connection.email,
      customImap: {
        ...connection.customImap,
        password: "",
      },
    });
    assert.equal(missingPasswordResult.ok, false);
    assert.equal(missingPasswordResult.error?.code, "imap_password_required");
    assert.equal(captured.length, capturedBeforeInvalidInput);

    const partialSmtpCases = [
      { host: "smtp.example.com" },
      {
        password: "one-time-smtp",
      },
      {
        port: "587",
        security: "starttls",
        username: "owner@example.com",
        password: "one-time-smtp",
        useSameCredentials: false,
      },
      {
        host: "smtp.example.com",
        port: "587",
        security: "starttls",
        useSameCredentials: false,
      },
      {
        host: "smtp.example.com",
        port: "587",
        username: "owner@example.com",
        useSameCredentials: false,
      },
      {
        host: "smtp.example.com",
        port: "",
        security: "starttls",
        username: "owner@example.com",
        password: "one-time-smtp",
        useSameCredentials: false,
      },
    ];
    for (const customSmtp of partialSmtpCases) {
      const capturedBeforePartialSmtp = captured.length;
      const partialResult = await beginInboxConnection({
        imapMode: "initial",
        mailboxId: "stable-mailbox",
        provider: "custom_imap",
        email: connection.email,
        customImap: connection.customImap,
        customSmtp: customSmtp as any,
      });
      assert.equal(partialResult.ok, false);
      assert.equal(
        partialResult.error?.code,
        "smtp_configuration_incomplete",
      );
      assert.equal(captured.length, capturedBeforePartialSmtp);
    }

    const capturedBeforeMaskedIndependentSmtp = captured.length;
    const maskedIndependentSmtpResult = await beginInboxConnection({
      imapMode: "reconnect",
      mailboxId: "stable-mailbox",
      provider: "custom_imap",
      email: connection.email,
      customImap: {
        ...connection.customImap,
        password: "",
      },
      customSmtp: {
        ...connection.customSmtp,
        password: "Stored securely",
      },
    });
    assert.equal(maskedIndependentSmtpResult.ok, false);
    assert.equal(
      maskedIndependentSmtpResult.error?.code,
      "smtp_password_required",
    );
    assert.equal(captured.length, capturedBeforeMaskedIndependentSmtp);

    const reconnectWithoutPasswordsResult = await beginInboxConnection({
      imapMode: "reconnect",
      mailboxId: "stable-mailbox",
      provider: "custom_imap",
      email: connection.email,
      customImap: {
        ...connection.customImap,
        password: "",
      },
    });
    assert.equal(reconnectWithoutPasswordsResult.ok, true);
    assert.equal(reconnectWithoutPasswordsResult.connected, false);
    assert.equal(reconnectWithoutPasswordsResult.connectionStatus, "not_connected");
    assert.deepEqual(requestBody(lastCaptured(captured)), {
      mode: "reconnect",
      mailboxId: "stable-mailbox",
      connection: {
        provider: "custom_imap",
        email: "owner@example.com",
        imap: {
          host: "imap.example.com",
          port: "993",
          ssl: true,
          username: "owner@example.com",
        },
      },
    });

    const ackResult = await beginInboxConnection({
      imapMode: "initial",
      mailboxId: "stable-mailbox",
      provider: "custom_imap",
      email: connection.email,
      customImap: connection.customImap,
    });
    assert.equal(ackResult.ok, true);
    assert.equal(ackResult.connected, false);
    assert.equal(ackResult.connectionStatus, "not_connected");
    const ackBody = requestBody(lastCaptured(captured));
    assert.equal("smtp" in (ackBody.connection as Record<string, unknown>), false);
    assert.equal(
      ((ackBody.connection as any).imap as Record<string, unknown>).ssl,
      true,
    );

    const successfulImapFetch = globalThis.fetch;
    globalThis.fetch = (async () =>
      response(
        {
          ok: false,
          error: {
            code: "invalid_credentials",
            message: "Rejected one-time-imap secret canary",
            stage: "one-time-imap",
          },
        },
        false,
        401,
      )) as unknown as typeof fetch;
    const failedInitialResult = await beginInboxConnection({
      imapMode: "initial",
      mailboxId: "stable-mailbox",
      provider: "custom_imap",
      email: connection.email,
      customImap: connection.customImap,
    });
    assert.equal(failedInitialResult.ok, false);
    assert.equal(failedInitialResult.error?.code, "invalid_credentials");
    assert.equal(
      JSON.stringify(failedInitialResult).includes("one-time-imap"),
      false,
    );
    globalThis.fetch = successfulImapFetch;

    const onboardingRequest = buildOnboardingConnectInboxRequest({
      onboardingInboxId: " custom:inbox-2 ",
      email: " owner@example.com ",
      customImap: connection.customImap,
    });
    assert.deepEqual(onboardingRequest, {
      mode: "onboarding",
      onboardingInboxId: "custom:inbox-2",
      connection: {
        provider: "custom_imap",
        email: "owner@example.com",
        imap: {
          host: "imap.example.com",
          port: "993",
          ssl: true,
          username: "owner@example.com",
          password: "one-time-imap",
        },
      },
    });

    const onboardingAbortController = new AbortController();
    const onboardingResult = await beginOnboardingInboxConnection(
      {
        onboardingInboxId: "custom:inbox-2",
        email: connection.email,
        customImap: connection.customImap,
      },
      onboardingAbortController.signal,
    );
    const onboardingBody = requestBody(lastCaptured(captured));
    assert.equal(lastCaptured(captured).url, "/api/inboxes/connect-imap");
    assert.equal(lastCaptured(captured).init.method, "POST");
    assert.equal(lastCaptured(captured).init.credentials, "include");
    assert.equal(
      lastCaptured(captured).init.signal,
      onboardingAbortController.signal,
    );
    assert.deepEqual(onboardingBody, onboardingRequest);
    assert.deepEqual(Object.keys(onboardingBody).sort(), [
      "connection",
      "mode",
      "onboardingInboxId",
    ]);
    assert.deepEqual(
      Object.keys(onboardingBody.connection as Record<string, unknown>).sort(),
      ["email", "imap", "provider"],
    );
    for (const forbiddenField of [
      "id",
      "mailboxId",
      "managedInboxId",
      "serverMailboxId",
      "credentialId",
      "userId",
      "workspaceId",
      "ownerId",
      "ownerEmail",
      "oauthOwnerEmail",
      "smtp",
      "internalRole",
      "focusPreferences",
      "selectedInboxes",
      "signal",
    ]) {
      assert.equal(
        JSON.stringify(onboardingBody).includes(`"${forbiddenField}"`),
        false,
      );
    }
    assert.equal(onboardingResult.ok, true);
    assert.equal(onboardingResult.connected, false);
    assert.equal(onboardingResult.connectionStatus, "not_connected");

    const capturedBeforePlaintextAttempt = captured.length;
    const plaintextResult = await beginOnboardingInboxConnection({
      onboardingInboxId: "custom:inbox-2",
      email: connection.email,
      customImap: { ...connection.customImap, ssl: false },
    });
    assert.equal(plaintextResult.ok, false);
    assert.equal(plaintextResult.error?.code, "tls_required");
    assert.equal(captured.length, capturedBeforePlaintextAttempt);

    const successfulFetch = globalThis.fetch;
    globalThis.fetch = (async () =>
      response(
        {
          ok: false,
          error: {
            code: "invalid_credentials",
            message: "Rejected one-time-imap credential detail",
          },
        },
        false,
        401,
      )) as unknown as typeof fetch;
    const rejectedOnboardingResult = await beginOnboardingInboxConnection({
      onboardingInboxId: "custom:inbox-2",
      email: connection.email,
      customImap: connection.customImap,
    });
    assert.equal(rejectedOnboardingResult.ok, false);
    assert.equal(rejectedOnboardingResult.error?.code, "invalid_credentials");
    assert.equal(
      JSON.stringify(rejectedOnboardingResult).includes("one-time-imap"),
      false,
    );
    globalThis.fetch = successfulFetch;

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
    const onboardingStepSource = fs.readFileSync(
      path.resolve(
        __dirname,
        "../components/onboarding/StepConnectInboxes.tsx",
      ),
      "utf8",
    );
    assert.equal(onboardingStepSource.includes("localStorage"), false);
    assert.equal(onboardingStepSource.includes("sessionStorage"), false);
    assert.equal(onboardingStepSource.includes("SMTP password"), false);
    assert.doesNotMatch(
      onboardingStepSource,
      /onCustomImapChange\([\s\S]{0,160}["']password["']/,
    );
    for (const field of ["host", "port", "username"]) {
      assert.match(
        onboardingStepSource,
        new RegExp(
          `clearImapPassword\\(inboxId\\);\\s*onCustomImapChange\\(\\s*inboxId,\\s*["']${field}["']`,
        ),
      );
    }
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
