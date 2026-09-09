import assert from "node:assert/strict";
import { test } from "node:test";
import { readCollaborationForOwner, __resetCollaborationOwnerReadApiForTests } from "./collaborationOwnerReadApi";
import { collaboration, notification, deferred, tick } from "./notificationsTestFixtures.test";
const response = (body: unknown, status = 200) => ({ ok: status === 200, status, json: async () => body, headers: new Headers() }) as Response;
const csrf = () => response({ ok: true, data: { csrfToken: "csrf", expiresAt: Math.ceil(Date.now() / 1000) + 1000 } });
test("notification owner read uses exact ID and does not retry forbidden", async () => {
  __resetCollaborationOwnerReadApiForTests(); const original = globalThis.fetch; const calls: any[] = [];
  globalThis.fetch = (async (_url, init) => { const body = JSON.parse(init!.body as string); calls.push(body); return body.operation === "csrf" ? csrf() : response({}, 403); }) as typeof fetch;
  try { assert.equal((await readCollaborationForOwner(notification().collaborationId, { retryForbidden: false })).status, "forbidden"); assert.deepEqual(calls, [{ operation: "csrf" }, { operation: "read", collaborationId: notification().collaborationId }]); }
  finally { globalThis.fetch = original; __resetCollaborationOwnerReadApiForTests(); }
});
test("navigation fencing at CSRF async boundary prevents stale owner HTTP", async () => {
  __resetCollaborationOwnerReadApiForTests(); const original = globalThis.fetch, pending = deferred(); let calls = 0, current = true;
  globalThis.fetch = (async () => { calls++; return pending.promise; }) as typeof fetch;
  try { const result = readCollaborationForOwner(notification().collaborationId, { retryForbidden: false, isCurrent: () => current }); await tick(); current = false; pending.resolve(csrf()); assert.equal((await result).status, "network_failure"); assert.equal(calls, 1); }
  finally { globalThis.fetch = original; __resetCollaborationOwnerReadApiForTests(); }
});
test("notification owner read passes abort signal and returns canonical projection", async () => {
  __resetCollaborationOwnerReadApiForTests(); const original = globalThis.fetch, controller = new AbortController();
  globalThis.fetch = (async (_url, init) => { const body = JSON.parse(init!.body as string); if (body.operation === "csrf") return csrf(); assert.equal(init?.signal, controller.signal); return response({ ok: true, data: { collaboration: collaboration() } }); }) as typeof fetch;
  try { assert.equal((await readCollaborationForOwner(notification().collaborationId, { retryForbidden: false, signal: controller.signal })).status, "success"); }
  finally { globalThis.fetch = original; __resetCollaborationOwnerReadApiForTests(); }
});
