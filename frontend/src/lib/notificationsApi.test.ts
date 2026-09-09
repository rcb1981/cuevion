import assert from "node:assert/strict";
import { test } from "node:test";
import { createNotificationsApi, parseServerNotification, parseNotificationSummary, parseNotificationPage, parseNotificationRead, notificationCopy } from "./notificationsApi";
import { notification, page, scope, time, imap } from "./notificationsTestFixtures.test";

test("strict C3C contract accepts body-free Google and IMAP records", () => {
  assert.deepEqual(parseServerNotification(notification(), scope.workspaceId), notification());
  assert.ok(parseServerNotification(notification(1, { sourceRef: imap }), scope.workspaceId));
});
for (const [name, change] of Object.entries({ body: { body: "secret" }, note: { text: "secret" }, email: { email: "private" }, mention: { kind: "mention" }, workspace: { workspaceId: "wsp_" + "Z".repeat(22) }, mailbox: { mailboxId: " mail" }, activity: { activityId: null }, id: { notificationId: "ntf_bad" }, read: { readAt: time + 86400000 }, expiry: { expiresAt: time - 999999 }, source: { sourceRef: { provider: "google", providerMessageId: "other", threadId: "guess" } }, version: { v: true }, actorEmail: { actor: { type: "external_guest", displayName: "Guest", email: "hidden" } }, actorType: { actor: { type: "system", displayName: "System" } }, actorControl: { actor: { type: "external_guest", displayName: "bad\nname" } } })) {
  test(`strict DTO rejects ${name}`, () => assert.equal(parseServerNotification({ ...notification(), ...change }, scope.workspaceId), null));
}
for (const value of [-1, 1001, 1.5, "5", null, true]) test(`summary rejects invalid count ${String(value)}`, () => assert.equal(parseNotificationSummary({ v: 1, unreadCount: value }), null));
test("server count greater than fifty is preserved", () => assert.equal(parseNotificationSummary({ v: 1, unreadCount: 87 })?.unreadCount, 87));
test("page cursor is preserved byte for byte", () => assert.equal(parseNotificationPage(page([notification()], 87, "opaque+/= token"), scope.workspaceId, null)?.nextCursor, "opaque+/= token"));
for (const [name, payload] of [
  ["duplicate IDs", page([notification(), notification()])], ["over fifty", page(Array.from({ length: 51 }, (_, i) => notification(i + 1)))],
  ["wrong order", page([notification(2), notification(1)])], ["empty advancing cursor", page([], 0, "next")],
  ["malformed cursor", { ...page(), nextCursor: {} }], ["extra authority", { ...page(), userId: "hidden" }],
]) test(`strict list rejects ${name}`, () => assert.equal(parseNotificationPage(payload, scope.workspaceId, null), null));
test("non-advancing cursor rejected", () => assert.equal(parseNotificationPage(page([notification()], 87, "same"), scope.workspaceId, "same"), null));
test("mark-read requires exact ID, nonnull returned readAt and returned count", () => {
  const value = { v: 1, unreadCount: 86, notification: notification(1, { readAt: time }) };
  assert.deepEqual(parseNotificationRead(value, scope.workspaceId, notification().notificationId), value);
  assert.equal(parseNotificationRead(value, scope.workspaceId, notification(2).notificationId), null);
  assert.equal(parseNotificationRead({ ...value, notification: notification() }, scope.workspaceId, notification().notificationId), null);
});
for (const [kind, actor, expected] of [
  ["collaboration_started", "cuevion_user", "Alex started a collaboration"], ["participant_added", "cuevion_user", "Alex added you to a collaboration"],
  ["shared_message", "cuevion_user", "Alex added a shared message"], ["shared_message", "external_guest", "Alex replied to a collaboration"], ["internal_note", "cuevion_user", "Alex added an internal note"],
] as const) test(`copy ${kind} ${actor}`, () => assert.equal(notificationCopy(notification(1, { kind, actor: actor === "external_guest" ? { type: actor, displayName: "Alex" } : notification().actor })), expected));
test("actor email is never displayed as fallback", () => assert.equal(notificationCopy(notification(1, { actor: { type: "external_guest", displayName: "private@example.test" } })), "Someone replied to a collaboration"));
test("transport uses one neutral POST per operation and no authority fields", async () => {
  const calls: any[] = [];
  const api = createNotificationsApi((async (url, init) => {
    calls.push(JSON.parse(init!.body as string)); assert.equal(url, "/api/notifications");
    assert.equal(init?.method, "POST"); assert.equal(init?.credentials, "include"); assert.equal(init?.cache, "no-store");
    const operation = calls.at(-1).operation;
    return { ok: true, json: async () => operation === "summary" ? { v: 1, unreadCount: 87 } : operation === "list" ? page() : { v: 1, unreadCount: 86, notification: notification(1, { readAt: time }) } } as Response;
  }) as typeof fetch);
  await api.summary(); await api.list(scope.workspaceId, null); await api.markRead(scope.workspaceId, notification().notificationId);
  assert.deepEqual(calls, [{ operation: "summary" }, { operation: "list", limit: 50, cursor: null }, { operation: "mark_read", notificationId: notification().notificationId }]);
});
for (const status of [401, 403, 404, 503]) test(`HTTP ${status} safe failure without raw error or retry`, async () => {
  let count = 0;
  const api = createNotificationsApi((async () => { count++; return { ok: false, status, json: () => { throw Error("must not read raw failure"); } } as unknown as Response; }) as typeof fetch);
  assert.notEqual((await api.summary()).status, "success"); assert.equal(count, 1);
});
test("network failure does not retry", async () => { let count = 0; const api = createNotificationsApi((async () => { count++; throw Error("private backend details"); }) as typeof fetch); assert.deepEqual(await api.summary(), { status: "service_unavailable" }); assert.equal(count, 1); });
test("equal timestamp page order follows descending notification ID", () => { const a = notification(1), b = notification(2, { createdAt: a.createdAt }); assert.equal(parseNotificationPage(page([a, b]), scope.workspaceId, null), null); assert.ok(parseNotificationPage(page([b, a]), scope.workspaceId, null)); });
