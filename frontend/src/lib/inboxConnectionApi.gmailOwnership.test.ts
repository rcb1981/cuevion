import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  downloadAttachment,
  fetchGmailInbox,
  mutateInboxMessageAction,
  sendGmailMessage,
} from "./inboxConnectionApi";

type CapturedRequest = { url: string; init: Record<string, any> };

function response(payload: unknown) {
  const serialized = JSON.stringify(payload);
  return {
    ok: true,
    status: 200,
    json: async () => payload,
    text: async () => serialized,
    blob: async () => ({ kind: "mock-blob" }),
  };
}

async function run() {
  await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));
  const captured: CapturedRequest[] = [];
  const originalFetch = globalThis.fetch;
  const originalWindow = (globalThis as any).window;
  (globalThis as any).window = {
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
  };
  const canonicalGmailMessage = {
    id: "message-1",
    sender: "Sender",
    subject: "Subject",
    snippet: "Snippet",
    from: "sender@example.com",
    to: "owner@example.com",
    timestamp: "July 13 at 10:00",
    createdAt: "2026-07-13T08:00:00.000Z",
    body: ["Body"],
    noiseDisposition: "unsolicited_low_value" as const,
    noiseConfidence: "high" as const,
    noiseReasons: ["cold_sales_outreach"] as const,
  };
  let gmailInboxMessages: unknown[] = [canonicalGmailMessage];
  (globalThis as any).fetch = async (url: string, init: Record<string, any>) => {
    captured.push({ url, init });
    if (url.endsWith("/fetch-gmail")) {
      return response({ ok: true, messages: gmailInboxMessages });
    }
    return response({ ok: true, action: "star", messages: [] });
  };

  const last = () => captured[captured.length - 1];
  const body = () => JSON.parse(String(last().init.body));
  const assertCredentialed = () => assert.equal(last().init.credentials, "include");

  try {
    const validGmailInboxResponse = await fetchGmailInbox({
      mailboxId: "gmail-1",
      focusPreferences: { promo: "low" } as any,
      limit: 25,
    });
    assert.deepEqual(body(), {
      mailboxId: "gmail-1",
      focusPreferences: { promo: "low" },
      limit: 25,
    });
    assertCredentialed();
    assert.deepEqual(
      {
        noiseDisposition:
          validGmailInboxResponse.messages?.[0]?.noiseDisposition,
        noiseConfidence:
          validGmailInboxResponse.messages?.[0]?.noiseConfidence,
        noiseReasons: validGmailInboxResponse.messages?.[0]?.noiseReasons,
      },
      {
        noiseDisposition: "unsolicited_low_value",
        noiseConfidence: "high",
        noiseReasons: ["cold_sales_outreach"],
      },
    );

    gmailInboxMessages = [{
      ...canonicalGmailMessage,
      noiseReasons: undefined,
    }];
    const partialAssessmentResponse = await fetchGmailInbox({
      mailboxId: "gmail-1",
    });
    const partialAssessmentMessage = partialAssessmentResponse.messages?.[0];
    assert.ok(partialAssessmentMessage);
    for (const key of [
      "noiseDisposition",
      "noiseConfidence",
      "noiseReasons",
    ]) {
      assert.equal(
        Object.prototype.hasOwnProperty.call(partialAssessmentMessage, key),
        false,
      );
    }

    gmailInboxMessages = [{
      ...canonicalGmailMessage,
      noiseConfidence: "provider-controlled-confidence",
      noiseReasons: ["provider-controlled-reason"],
    }];
    const invalidAssessmentResponse = await fetchGmailInbox({
      mailboxId: "gmail-1",
    });
    const invalidAssessmentMessage = invalidAssessmentResponse.messages?.[0];
    assert.ok(invalidAssessmentMessage);
    for (const key of [
      "noiseDisposition",
      "noiseConfidence",
      "noiseReasons",
    ]) {
      assert.equal(
        Object.prototype.hasOwnProperty.call(invalidAssessmentMessage, key),
        false,
      );
    }

    const legacyGmailMessage = {
      ...canonicalGmailMessage,
    } as Partial<typeof canonicalGmailMessage>;
    delete legacyGmailMessage.noiseDisposition;
    delete legacyGmailMessage.noiseConfidence;
    delete legacyGmailMessage.noiseReasons;
    gmailInboxMessages = [legacyGmailMessage];
    const legacyAssessmentResponse = await fetchGmailInbox({
      mailboxId: "gmail-1",
    });
    const legacyAssessmentMessage = legacyAssessmentResponse.messages?.[0];
    assert.ok(legacyAssessmentMessage);
    for (const key of [
      "noiseDisposition",
      "noiseConfidence",
      "noiseReasons",
    ]) {
      assert.equal(
        Object.prototype.hasOwnProperty.call(legacyAssessmentMessage, key),
        false,
      );
    }

    await mutateInboxMessageAction({
      mailboxId: "gmail-1",
      messageId: "message-1",
      action: "star",
    });
    assert.deepEqual(body(), {
      mailboxId: "gmail-1",
      messageId: "message-1",
      action: "star",
    });
    assertCredentialed();

    await sendGmailMessage({
      mailboxId: "gmail-1",
      to: "to@example.com",
      cc: "cc@example.com",
      subject: "Subject",
      bodyHtml: "<p>Body</p>",
      bodyText: "Body",
      provider: "google",
      email: "victim@gmail.com",
      authMode: "oauth",
      username: "victim",
      password: "secret",
      from: "victim@gmail.com",
      smtpHost: "evil.example.com",
      accessToken: "token",
      refreshToken: "refresh",
    } as any);
    assert.deepEqual(body(), {
      mailboxId: "gmail-1",
      to: "to@example.com",
      cc: "cc@example.com",
      subject: "Subject",
      bodyHtml: "<p>Body</p>",
      bodyText: "Body",
    });
    assertCredentialed();
    for (const forbidden of [
      "provider", "email", "authMode", "username", "password", "from",
      "smtpHost", "smtpPort", "accessToken", "refreshToken",
    ]) {
      assert.equal(forbidden in body(), false);
    }

    await downloadAttachment({
      mailboxId: "gmail-1",
      messageId: "message-1",
      attachmentId: "attachment-1",
    });
    assert.deepEqual(body(), {
      mailboxId: "gmail-1",
      messageId: "message-1",
      attachmentId: "attachment-1",
    });
    assertCredentialed();

    const workspaceSource = fs.readFileSync(
      path.resolve(__dirname, "../components/workspace/WorkspaceShell.tsx"),
      "utf8",
    );
    assert.doesNotMatch(workspaceSource, /fetchGmailThread\s*\(/);
  } finally {
    (globalThis as any).fetch = originalFetch;
    (globalThis as any).window = originalWindow;
  }

  console.log("inboxConnectionApi Gmail ownership DTO tests passed");
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
