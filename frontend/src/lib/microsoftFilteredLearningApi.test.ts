import assert from "node:assert/strict";
import { requestMicrosoftFilteredLearning } from "./microsoftFilteredLearningApi";

const BOX = "microsoft-test-01";
const PAYLOAD = { ok: true, exists: true, placements: { "artist@example.com": "keep" },
  rules: { "dj@example.com": { priority: "important", label: "Demo" } },
  messageRules: { "AAMk-message-one": { priority: "important", label: "Promo" } } };

async function run() {
  const original = globalThis.fetch;
  const requests: Array<{url: string; options: RequestInit}> = [];
  try {
    globalThis.fetch = (async (url: string, options: RequestInit) => {
      requests.push({ url, options });
      return { ok: true, json: async () => PAYLOAD } as Response;
    }) as typeof fetch;
    const read = await requestMicrosoftFilteredLearning({ mailboxId: BOX, action: "read" });
    assert.equal(read?.placements["artist@example.com"], "keep");
    assert.equal(read?.rules["dj@example.com"]?.label, "Demo");
    assert.equal(read?.messageRules["AAMk-message-one"]?.priority, "important");
    assert.equal(requests[0].url, "/api/inboxes/microsoft-filtered-learning");
    assert.equal(requests[0].options.method, "POST");
    assert.equal(requests[0].options.credentials, "include");
    assert.deepEqual(JSON.parse(String(requests[0].options.body)), { mailboxId: BOX, action: "read" });

    await requestMicrosoftFilteredLearning({ mailboxId: BOX, action: "setPlacement",
      sender: "artist@example.com", placement: "show_less" });
    assert.equal(JSON.parse(String(requests[1].options.body)).placement, "show_less");

    await requestMicrosoftFilteredLearning({ mailboxId: BOX, action: "setMessageRule",
      messageId: "AAMk-message-two", rule: { placement: "inbox" } });
    assert.deepEqual(JSON.parse(String(requests[2].options.body)), {
      mailboxId: BOX, action: "setMessageRule", messageId: "AAMk-message-two",
      rule: { placement: "inbox" },
    });

    globalThis.fetch = (async () => ({ ok: true,
      json: async () => ({ ...PAYLOAD, rules: { "invalid sender": { priority: "important", label: "Demo" } } }),
    } as Response)) as typeof fetch;
    assert.equal(await requestMicrosoftFilteredLearning({ mailboxId: BOX, action: "read" }), null);
    globalThis.fetch = (async () => ({ ok: false, json: async () => ({}) } as Response)) as typeof fetch;
    assert.equal(await requestMicrosoftFilteredLearning({ mailboxId: BOX, action: "read" }), null);

    const count = requests.length;
    assert.equal(await requestMicrosoftFilteredLearning({ mailboxId: "../other", action: "read" }), null);
    assert.equal(requests.length, count);
  } finally {
    globalThis.fetch = original;
  }
}

run().then(() => console.log("Microsoft durable Learning API tests passed"))
  .catch((error) => { console.error(error); process.exitCode = 1; });
