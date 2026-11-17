import assert from "node:assert/strict";
import { sendGmailMessage, SEND_UNCERTAIN_MESSAGE } from "./inboxConnectionApi";

async function run() {
  const savedFetch = globalThis.fetch;
  const savedWindow = (globalThis as any).window;
  let onTimeout: () => void = () => {};
  let clearCount = 0;
  let calls = 0;
  let transport: (init: RequestInit) => Promise<any>;
  (globalThis as any).window = {
    setTimeout(callback: () => void, delay: number) {
      assert.equal(delay, 45000);
      onTimeout = callback;
      return 1;
    },
    clearTimeout() { clearCount += 1; },
  };
  const request = {
    mailboxId: "synthetic-mailbox", to: "to@example.com", cc: "cc@example.com", bcc: "hidden@example.com",
    subject: "Synthetic", bodyText: "Body and signature", bodyHtml: "<p>Body and signature</p>",
    attachments: [{ name: "sample.txt", mimeType: "text/plain", contentBase64: "c2FtcGxl" }],
  };
  globalThis.fetch = (async (_url: unknown, init: RequestInit) => {
    calls += 1;
    assert.equal(init.method, "POST");
    assert.deepEqual(JSON.parse(String(init.body)), request);
    return transport(init);
  }) as typeof fetch;
  const response = (status: number, body: unknown) => ({
    ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body),
  });
  const uncertain = (result: Awaited<ReturnType<typeof sendGmailMessage>>) => {
    assert.equal(result.ok, false);
    assert.equal(result.sendOutcome, "uncertain");
    assert.equal(result.error?.code, "send_uncertain");
    assert.equal(result.error?.message, SEND_UNCERTAIN_MESSAGE);
    assert.doesNotMatch(result.error!.message!, /try again|retry/i);
  };
  try {
    for (const accepted of [
      { ok: true },
      { ok: true, providerMessageId: "sent-id", providerThreadId: "thread-id" },
      { ok: true, providerIdentityConfirmed: false },
    ]) {
      transport = async () => response(200, accepted);
      assert.equal((await sendGmailMessage(request)).ok, true);
    }
    for (const status of [400, 403, 502]) {
      const error = { code: "send_failed", message: "Provider rejected the message." };
      transport = async () => response(status, { ok: false, sendOutcome: "not_sent", error });
      assert.deepEqual(await sendGmailMessage(request), { ok: false, sendOutcome: "not_sent", error });
    }
    const before = calls;
    transport = async (init) => new Promise((_resolve, reject) => {
      init.signal!.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    });
    const pending = sendGmailMessage(request);
    onTimeout();
    uncertain(await pending);
    assert.equal(calls, before + 1, "Timeout must not retry a potentially accepted POST");
    const cases = [
      async () => { throw new TypeError("Network interrupted after possible acceptance"); },
      async () => ({ ok: true, status: 200, text: async () => { throw new Error("Lost response body"); } }),
      async () => ({ ok: true, status: 200, text: async () => "<html>gateway response</html>" }),
      async () => response(200, { ok: false }),
      async () => response(200, null),
      async () => response(504, { error: { code: "gateway_timeout", message: "Try again" } }),
      async () => response(500, { error: { code: "internal_error", message: "Could not send" } }),
      async () => response(502, { ok: false, sendOutcome: "uncertain", error: { code: "send_uncertain", message: "unsafe retry text" } }),
    ];
    for (const next of cases) {
      transport = next;
      const count = calls;
      uncertain(await sendGmailMessage(request));
      assert.equal(calls, count + 1, "One outbound request per invocation");
    }
    assert.equal(clearCount, calls, "Timers are cleared on every transport outcome");
    console.log("Gmail/SMTP send outcome client checks passed");
  } finally {
    globalThis.fetch = savedFetch;
    (globalThis as any).window = savedWindow;
  }
}
void run().catch(error => { console.error(error); process.exitCode = 1; });
