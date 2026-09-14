import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createWorkspaceNotificationStore } from "../../lib/workspaceNotificationStore";
import { createNotificationNavigator } from "../../lib/notificationNavigation";
import type { NotificationsApi, ServerNotification } from "../../lib/notificationsApi";
import { notification, page, scope, time, success, deferred, tick, exactResponse, collaboration, google, imap } from "../../lib/notificationsTestFixtures.test";

// Match the existing focused renderer suite's automatic JSX runtime.
const sucrase = require("sucrase");
require.extensions[".tsx"] = (module: NodeModule, filename: string) =>
  (module as NodeModule & { _compile: (code: string, filename: string) => void })._compile(
    sucrase.transform(fs.readFileSync(filename, "utf8"), { transforms: ["typescript", "jsx", "imports"], jsxRuntime: "automatic" }).code, filename,
  );
const { ServerNotifications }: typeof import("./ServerNotifications") = require("./ServerNotifications");

async function setup(rows: ServerNotification[], unreadCount = 87) {
  const calls: string[] = [];
  const api: NotificationsApi = {
    summary: async () => { calls.push("summary"); return success({ v: 1, unreadCount }); },
    list: async () => { calls.push("list"); return success(page(rows, unreadCount)); },
    markRead: async (_workspaceId, id) => {
      calls.push("mark_read");
      return success({ v: 1, unreadCount: unreadCount - 1, notification: { ...rows.find(row => row.notificationId === id)!, readAt: time } });
    },
  };
  const store = createWorkspaceNotificationStore(scope, api);
  await store.ensureFirstPage("workbench");
  const render = (preview = false) => renderToStaticMarkup(createElement(ServerNotifications, {
    store, state: store.getSnapshot(), onOpen: () => {}, preview,
  }));
  return { store, render, calls };
}

const occurrences = (html: string, text: string) => html.split(text).length - 1;
const copy = {
  shared_message: "Alex added a shared message",
  internal_note: "Alex added an internal note",
  collaboration_started: "Alex started a collaboration",
  participant_added: "Alex added you to a collaboration",
} as const;

for (const preview of [false, true]) {
  for (const kind of Object.keys(copy) as (keyof typeof copy)[]) {
    test(`historical/generic ${kind} keeps primary copy and no attention label in preview=${preview}`, async () => {
      const { render } = await setup([notification(1, { kind })]);
      const html = render(preview);
      assert.ok(html.includes(copy[kind]));
      assert.equal(occurrences(html, "Mentioned you"), 0);
      assert.equal(occurrences(html, "data-notification-id="), 1);
      assert.ok(html.includes("Unread</span>"));
    });
  }
  for (const kind of ["shared_message", "internal_note"] as const) {
    for (const readAt of [null, time]) {
      test(`${kind} mention retains actor, copy, timestamp and read state ${readAt} in preview=${preview}`, async () => {
        const row = notification(1, { kind, attention: "mention", readAt });
        const { render, store, calls } = await setup([row]);
        const before = store.getSnapshot();
        const html = render(preview);
        assert.ok(html.includes(copy[kind]));
        assert.equal(occurrences(html, ">Mentioned you</span>"), 1);
        assert.equal(occurrences(html, "data-notification-id="), 1);
        assert.equal(occurrences(html, "Unread</span>"), readAt === null ? 1 : 0);
        if (readAt === null) assert.ok(html.includes('class="ml-2 inline-block rounded border border-current px-1.5 text-xs font-semibold">Unread'));
        assert.ok(html.includes(`dateTime="${new Date(row.createdAt).toISOString()}"`));
        assert.strictEqual(store.getSnapshot(), before);
        assert.equal(before.unreadCount, 87);
        assert.deepEqual(calls, ["list"]);
      });
    }
  }
}

test("mention text is accessible and adds no interactive element or tab stop", async () => {
  const generic = await setup([notification()]);
  const mentioned = await setup([notification(1, { attention: "mention" })]);
  const plain = generic.render(), html = mentioned.render();
  assert.ok(html.includes('<span class="mt-1 block text-xs font-medium text-[var(--workspace-accent-text)]">Mentioned you</span>'));
  assert.equal(occurrences(html, "<button"), occurrences(plain, "<button"));
  assert.equal(occurrences(html, 'type="button"'), occurrences(plain, 'type="button"'));
  assert.equal(occurrences(html, "tabindex="), occurrences(plain, "tabindex="));
  assert.ok(html.includes("focus-visible:outline focus-visible:outline-2"));
  assert.equal(html.includes("aria-hidden="), false);
});

test("plain @text cannot create attention and rendering never reads activity or body", async () => {
  const activity = { text: "@Rutger Bäumer", visibility: "internal" };
  const row = notification(1, { actor: { type: "cuevion_user", userId: scope.accountId, displayName: "@Rutger Bäumer" } });
  for (const key of ["body", "text", "activity", "mentions", "participants"]) {
    Object.defineProperty(row, key, { get() { throw new Error(`Presentation must not read ${key}`); } });
  }
  const { render, calls } = await setup([row]);
  assert.equal(occurrences(render(), "Mentioned you"), 0);
  assert.equal(render().includes(activity.text), false);
  assert.deepEqual(calls, ["list"]);
});

test("one ID stays one item across generic-to-mention refresh; ordering and durable count stay unchanged", async () => {
  const rows = [notification(1), notification(2, { readAt: time }), notification(3)];
  const { store, render } = await setup(rows);
  const ids = [...store.getSnapshot().orderedIds];
  rows[1] = { ...rows[1], attention: "mention" };
  await store.refresh();
  const html = render();
  assert.equal(store.getSnapshot().byId.size, 3);
  assert.deepEqual(store.getSnapshot().orderedIds, ids);
  assert.equal(store.getSnapshot().unreadCount, 87);
  assert.equal(occurrences(html, "Mentioned you"), 1);
  assert.equal(occurrences(html, "data-notification-id="), 3);
  for (const id of ids) assert.equal(occurrences(html, `data-notification-id="${id}"`), 1);
  assert.ok(html.indexOf(ids[0]) < html.indexOf(ids[1]) && html.indexOf(ids[1]) < html.indexOf(ids[2]));
});

test("no durable notification produces no item or mention indicator", async () => {
  const { render } = await setup([], 0);
  assert.equal(occurrences(render(), "data-notification-id="), 0);
  assert.equal(occurrences(render(), "Mentioned you"), 0);
  assert.ok(render().includes("No notifications yet."));
});

for (const sourceRef of [google, imap]) {
  for (const kind of ["shared_message", "internal_note"] as const) {
    for (const displayed of [true, false]) {
      test(`mention ${kind}/${sourceRef.provider}: exact resolved route and ack=${displayed} preserve read timing`, async () => {
        const row = notification(1, { sourceRef, kind, attention: "mention" });
        const { store, render, calls } = await setup([row]);
        const ack = deferred<boolean>();
        const target = { message: exactResponse(sourceRef).message, folder: "Inbox" as const };
        let displays = 0;
        const navigator = createNotificationNavigator({
          getScope: () => scope,
          getMailbox: id => { assert.equal(id, row.mailboxId); return { provider: sourceRef.provider, configRevision: 1 }; },
          lookup: (mailboxId, source) => { assert.equal(mailboxId, row.mailboxId); assert.deepEqual(source, row.sourceRef); return target; },
          augment: () => { throw new Error("Loaded exact source must not be replaced"); },
          fetchExact: async () => { throw new Error("No extra fetch for mention"); },
          readCollaboration: async id => { assert.equal(id, row.collaborationId); return { status: "success", collaboration: collaboration(row) }; },
          display: async request => {
            displays++;
            assert.deepEqual(request.ticket.scope, scope);
            assert.deepEqual(request.ticket.notification, row);
            assert.equal(request.ticket.notification.activityId, row.activityId);
            assert.deepEqual(request.target, target);
            if (!request.collaboration) return true;
            assert.equal(request.collaboration.state, "resolved");
            assert.equal(request.collaboration.messages[0].id, row.activityId);
            return ack.promise;
          },
          markRead: (id, current, signal) => store.markRead(id, current, signal),
          feedback: () => {},
        });
        const opened = navigator.open(row);
        await tick();
        assert.equal(displays, 2);
        assert.deepEqual(calls, ["list"]);
        assert.equal(store.getSnapshot().byId.get(row.notificationId)?.readAt, null);
        assert.equal(store.getSnapshot().unreadCount, 87);
        ack.resolve(displayed);
        assert.equal(await opened, displayed);
        assert.deepEqual(calls, displayed ? ["list", "mark_read"] : ["list"]);
        assert.equal(store.getSnapshot().byId.get(row.notificationId)?.readAt, displayed ? time : null);
        assert.equal(store.getSnapshot().unreadCount, displayed ? 86 : 87);
        assert.equal(occurrences(render(), "Mentioned you"), 1);
        navigator.cancel();
      });
    }
  }
}
