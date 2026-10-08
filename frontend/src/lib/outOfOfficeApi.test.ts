import assert from "node:assert/strict";
import {
  loadOutOfOfficeSettings,
  saveOutOfOfficeSettings,
} from "./outOfOfficeApi";

const originalFetch = globalThis.fetch;

const settings = {
  schemaVersion: 1 as const,
  enabled: true,
  startsAt: "2026-10-10T08:00:00Z",
  endsAt: "2026-10-20T16:00:00Z",
  activatedAt: "2026-10-08T10:00:00Z",
  subject: "Out of office",
  message: "Back soon.",
  updatedAt: "2026-10-08T10:00:00Z",
};

async function run() {
  const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];

  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ input, init });
    return new Response(
      JSON.stringify({ ok: true, mailboxId: "mailbox-1", settings }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    );
  }) as typeof fetch;

  const loaded = await loadOutOfOfficeSettings("mailbox-1");
  assert.deepEqual(loaded, settings);
  assert.equal(String(calls[0]?.input), "/api/out-of-office?mailboxId=mailbox-1");
  assert.equal(calls[0]?.init?.method, "GET");
  assert.equal(calls[0]?.init?.credentials, "same-origin");

  calls.length = 0;
  const saved = await saveOutOfOfficeSettings("mailbox-1", {
    enabled: true,
    startsAt: settings.startsAt,
    endsAt: settings.endsAt,
    subject: settings.subject,
    message: settings.message,
  });
  assert.deepEqual(saved, settings);
  assert.equal(String(calls[0]?.input), "/api/out-of-office");
  assert.equal(calls[0]?.init?.method, "POST");
  assert.equal(calls[0]?.init?.credentials, "same-origin");
  assert.deepEqual(JSON.parse(String(calls[0]?.init?.body)), {
    mailboxId: "mailbox-1",
    settings: {
      enabled: true,
      startsAt: settings.startsAt,
      endsAt: settings.endsAt,
      subject: settings.subject,
      message: settings.message,
    },
  });

  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        ok: false,
        error: { code: "invalid_settings", message: "message is required" },
      }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    )) as typeof fetch;

  await assert.rejects(
    () =>
      saveOutOfOfficeSettings("mailbox-1", {
        enabled: true,
        startsAt: null,
        endsAt: null,
        subject: "Away",
        message: "",
      }),
    /message is required/,
  );
}

run()
  .finally(() => {
    globalThis.fetch = originalFetch;
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
