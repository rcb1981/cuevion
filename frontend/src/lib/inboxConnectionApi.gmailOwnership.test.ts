import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  buildGmailReplyContext,
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
  let sendGmailPayload: unknown = { ok: true };
  let sendGmailTextFailure = false;
  (globalThis as any).fetch = async (url: string, init: Record<string, any>) => {
    captured.push({ url, init });
    if (url.endsWith("/fetch-gmail")) {
      return response({ ok: true, messages: gmailInboxMessages });
    }
    if (url.endsWith("/send-gmail")) {
      if (sendGmailTextFailure) {
        return {
          ok: true,
          status: 200,
          text: async () => {
            throw new Error("response body unavailable after send");
          },
        };
      }
      return response(sendGmailPayload);
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

    const authoritativeReplySource = {
      providerMessageId: "source-provider-message-1",
      threadIdentityAuthority: "gmail",
      threadIdentityContext: {
        provider: "google",
        mailboxId: "gmail-1",
      },
    };
    for (const composeMode of ["reply", "reply_all"] as const) {
      assert.deepEqual(
        buildGmailReplyContext({
          sendProvider: "google",
          composeMode,
          mailboxId: "gmail-1",
          sourceMessage: authoritativeReplySource,
        }),
        { sourceProviderMessageId: "source-provider-message-1" },
      );
    }
    for (const composeMode of ["new", "forward"] as const) {
      assert.equal(
        buildGmailReplyContext({
          sendProvider: "google",
          composeMode,
          mailboxId: "gmail-1",
          sourceMessage: authoritativeReplySource,
        }),
        undefined,
      );
    }
    for (const invalidOptions of [
      { sendProvider: "custom_imap" },
      {
        sourceMessage: {
          ...authoritativeReplySource,
          threadIdentityAuthority: "unique_message",
        },
      },
      {
        sourceMessage: {
          ...authoritativeReplySource,
          threadIdentityContext: {
            provider: "custom_imap",
            mailboxId: "gmail-1",
          },
        },
      },
      { mailboxId: "gmail-2" },
      {
        sourceMessage: {
          ...authoritativeReplySource,
          providerMessageId: " source-provider-message-1 ",
        },
      },
    ] as const) {
      assert.equal(
        buildGmailReplyContext({
          sendProvider: "google",
          composeMode: "reply",
          mailboxId: "gmail-1",
          sourceMessage: authoritativeReplySource,
          ...invalidOptions,
        }),
        undefined,
      );
    }

    sendGmailPayload = {
      ok: true,
      providerMessageId: "sent-provider-message-1",
      providerThreadId: "sent-provider-thread-1",
      labelIds: ["SENT"],
      threadContinuityConfirmed: false,
    };
    const replySendResponse = await sendGmailMessage({
      mailboxId: "gmail-1",
      to: "to@example.com",
      subject: "Re: Subject",
      bodyHtml: "<p>Reply</p>",
      bodyText: "Reply",
      replyContext: {
        sourceProviderMessageId: "source-provider-message-1",
        providerThreadId: "client-controlled-thread",
        threadId: "gmail:gmail-1:client-controlled-thread",
        rfcMessageId: "client-controlled@example.com",
        References: "<client-controlled@example.com>",
        "In-Reply-To": "<client-controlled@example.com>",
      },
      providerThreadId: "top-level-client-controlled-thread",
      threadId: "gmail:gmail-1:top-level-client-controlled-thread",
      rfcMessageId: "top-level-client-controlled@example.com",
      References: "<top-level-client-controlled@example.com>",
      "In-Reply-To": "<top-level-client-controlled@example.com>",
      composeMode: "reply",
      composeSourceMessage: { id: "must-not-cross-the-wire" },
    } as any);
    assert.deepEqual(body(), {
      mailboxId: "gmail-1",
      to: "to@example.com",
      subject: "Re: Subject",
      bodyHtml: "<p>Reply</p>",
      bodyText: "Reply",
      replyContext: {
        sourceProviderMessageId: "source-provider-message-1",
      },
    });
    assertCredentialed();
    for (const forbidden of [
      "providerThreadId",
      "threadId",
      "rfcMessageId",
      "References",
      "In-Reply-To",
      "composeMode",
      "composeSourceMessage",
    ]) {
      assert.equal(forbidden in body(), false);
    }
    assert.deepEqual(replySendResponse, sendGmailPayload);
    assert.equal(replySendResponse.providerMessageId, "sent-provider-message-1");
    assert.equal(replySendResponse.providerThreadId, "sent-provider-thread-1");
    assert.deepEqual(replySendResponse.labelIds, ["SENT"]);
    assert.equal(replySendResponse.threadContinuityConfirmed, false);

    const requestCountBeforeInvalidReply = captured.length;
    const invalidReplyResponse = await sendGmailMessage({
      mailboxId: "gmail-1",
      to: "to@example.com",
      subject: "Re: Subject",
      bodyHtml: "<p>Reply</p>",
      bodyText: "Reply",
      replyContext: { sourceProviderMessageId: " " },
    });
    assert.equal(invalidReplyResponse.ok, false);
    assert.equal(invalidReplyResponse.error?.code, "invalid_reply_context");
    assert.equal(
      captured.length,
      requestCountBeforeInvalidReply,
      "an explicit malformed reply context must fail before any send request",
    );

    sendGmailPayload = {
      ok: true,
      providerMessageId: "sent-provider-message-2",
      providerThreadId: "\ud800",
    };
    const malformedIdentityResponse = await sendGmailMessage({
      mailboxId: "gmail-1",
      to: "to@example.com",
      subject: "Subject",
      bodyHtml: "<p>Message</p>",
      bodyText: "Message",
    });
    assert.equal(malformedIdentityResponse.ok, true);
    assert.equal(malformedIdentityResponse.providerIdentityConfirmed, false);
    assert.equal(malformedIdentityResponse.providerMessageId, undefined);
    assert.equal(malformedIdentityResponse.providerThreadId, undefined);

    sendGmailPayload = {
      ok: true,
      providerIdentityConfirmed: true,
      threadContinuityConfirmed: true,
    };
    const missingConfirmedIdentityResponse = await sendGmailMessage({
      mailboxId: "gmail-1",
      to: "to@example.com",
      subject: "Subject",
      bodyHtml: "<p>Message</p>",
      bodyText: "Message",
    });
    assert.equal(missingConfirmedIdentityResponse.ok, true);
    assert.equal(missingConfirmedIdentityResponse.providerIdentityConfirmed, false);
    assert.equal(missingConfirmedIdentityResponse.threadContinuityConfirmed, false);

    sendGmailTextFailure = true;
    const unreadableSuccessResponse = await sendGmailMessage({
      mailboxId: "gmail-1",
      to: "to@example.com",
      subject: "Subject",
      bodyHtml: "<p>Message</p>",
      bodyText: "Message",
    });
    sendGmailTextFailure = false;
    assert.equal(unreadableSuccessResponse.ok, true);
    assert.equal(unreadableSuccessResponse.providerIdentityConfirmed, false);
    assert.equal(unreadableSuccessResponse.threadContinuityConfirmed, false);
    assert.equal(unreadableSuccessResponse.error, undefined);

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
