import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createWorkspaceNotificationStore } from "../../lib/workspaceNotificationStore";
import { notification, page, scope, success } from "../../lib/notificationsTestFixtures.test";
const sucrase = require("sucrase");
require.extensions[".tsx"] = (module: any, filename: string) => module._compile(sucrase.transform(fs.readFileSync(filename, "utf8"), { transforms: ["typescript", "jsx", "imports"], jsxRuntime: "automatic" }).code, filename);
const { ServerNotifications } = require("./ServerNotifications");
async function render(changes: any = {}, preview = false) {
  const store = createWorkspaceNotificationStore(scope, { summary: async () => success({ v: 1, unreadCount: 87 }), list: async () => success(page(Array.from({ length: 8 }, (_, i) => notification(i + 1)), 87, "more")), markRead: async () => { throw Error("render must never mark"); } });
  await store.refresh();
  return renderToStaticMarkup(React.createElement(ServerNotifications, { store, state: { ...store.getSnapshot(), ...changes }, preview, onOpen: () => {} }));
}
test("rows are native keyboard buttons and unread has visible text", async () => { const html = await render(); assert.equal((html.match(/data-notification-id=/g) ?? []).length, 8); assert.equal((html.match(/>Unread</g) ?? []).length, 8); assert.ok(html.includes('type="button"')); });
test("Dashboard preview shows only five authoritative records", async () => { const html = await render({}, true); assert.equal((html.match(/data-notification-id=/g) ?? []).length, 5); assert.ok(html.includes('data-server-notifications="dashboard"')); assert.ok(!html.includes("Load more")); });
test("workbench renders authoritative records", async () => assert.ok((await render()).includes('data-server-notifications="workbench"')));
test("Load more only with server cursor", async () => { assert.ok((await render()).includes("Load more")); assert.ok(!(await render({ nextCursor: null })).includes("Load more")); });
test("list error has safe retry and retains rows", async () => { const html = await render({ listError: true }); assert.ok(html.includes('role="alert"')); assert.ok(html.includes("Retry")); assert.ok(html.includes("data-notification-id")); });
test("server empty state has no synthesized fallback", async () => { const html = await render({ orderedIds: [], byId: new Map(), nextCursor: null }); assert.ok(html.includes("No notifications yet.")); assert.ok(!html.includes("data-notification-id")); });
test("loading is announced", async () => assert.ok((await render({ listLoading: true })).includes('role="status"')));
test("load-more error retains rows and offers explicit click", async () => { const html = await render({ loadMoreError: true }); assert.ok(html.includes("More notifications could not be loaded")); assert.ok(html.includes("Load more")); assert.ok(html.includes("data-notification-id")); });
test("rows show no message/note content or actor email", async () => { const html = await render(); for (const forbidden of ["Private note", "Body", "@example", "userId", "sourceRef"]) assert.ok(!html.includes(forbidden)); });
test("responsive row wraps and text can break without overflow", () => { const source = fs.readFileSync(path.join(__dirname, "ServerNotifications.tsx"), "utf8"); assert.ok(source.includes("flex-wrap")); assert.ok(source.includes("break-words")); assert.ok(source.includes("min-w-0")); });
