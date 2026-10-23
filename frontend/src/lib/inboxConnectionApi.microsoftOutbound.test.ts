import assert from "node:assert/strict";
import { sendMicrosoftMessage } from "./inboxConnectionApi";

// Exercise the real HTTP client validation, not a copy of its implementation.
// Invalid Forward payloads may not throw and must never reach fetch/Graph.
async function run() {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (async () => {
    calls++;
    throw new Error("No network calls permitted by this regression test");
  }) as typeof globalThis.fetch;
  try {
    const standard = {
      mailboxId: "microsoft-1234567890",
      attemptId: "a".repeat(43),
      compose: {
        mode: "forward", sourceProviderMessageId: "source-123",
        to: ["recipient@example.com"], cc: [], body: "Please review.",
      },
    } as const;
    const invalid = [
      { ...standard.compose, to: undefined },
      { ...standard.compose, to: null },
      { ...standard.compose, to: "recipient@example.com" },
      { ...standard.compose, cc: "not-an-array" },
      { ...standard.compose, cc: null },
      { ...standard.compose, to: [] },
      { ...standard.compose, to: ["recipient@example.com"], cc: ["recipient@example.com"] },
      { ...standard.compose, to: ["recipient@example.com", "bad address"] },
    ];
    for (const compose of invalid) {
      const result = await sendMicrosoftMessage({ ...standard, compose } as any);
      assert.deepEqual(result, { ok: false, error: {
        code: "invalid_request", message: "Invalid Microsoft message request.",
      } });
    }
    for (const cc of [null, "not-an-array"]) {
      const result = await sendMicrosoftMessage({ ...standard, compose: {
        mode: "new", to: ["recipient@example.com"], cc,
        subject: "A subject", body: "Please review.",
      } } as any);
      assert.deepEqual(result, { ok: false, error: {
        code: "invalid_request", message: "Invalid Microsoft message request.",
      } });
    }
    assert.equal(calls, 0, "Invalid new/forward requests must never reach the network");
    console.log("Microsoft Forward HTTP client invalid-array no-network tests passed");
  } finally {
    globalThis.fetch = originalFetch;
  }
}
void run().catch((error) => { console.error(error); process.exitCode = 1; });
