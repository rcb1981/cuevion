import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { MailboxSyncPresentation } from "../../../lib/mailboxRefreshSemantics";
import type { MobileWorkspaceMailbox } from "./MobileWorkspaceShell";

// Render the production component with the same automatic JSX runtime as Vite.
const removeTsxHook = require("sucrase/dist/register").addHook(".tsx", {
  transforms: ["typescript", "jsx", "imports"],
  jsxRuntime: "automatic",
});
const { MobileWorkspaceShell } = require("./MobileWorkspaceShell");
removeTsxHook();

const mailbox = (id: string): MobileWorkspaceMailbox => ({
  id,
  title: `Mailbox ${id}`,
  email: `${id}@example.com`,
  detail: "Connected",
  connected: true,
  refreshStatus: "↻ Refresh requested…",
  messages: [{
    id: `message-${id}`,
    mailboxId: id,
    mailboxTitle: `Mailbox ${id}`,
    sender: "Sender",
    from: "sender@example.com",
    subject: `Available message ${id}`,
    snippet: "Saved message content",
    time: "09:00",
    timestamp: "2026-09-15T09:00:00Z",
    body: ["Saved message content"],
  }],
});

function render(
  presentation: Partial<Record<string, MailboxSyncPresentation>>,
  mailboxId: string | null = "a",
  options: {
    refreshStatus?: string;
    syncFeedbackMessage?: string;
    tab?: "inboxes" | "settings";
  } = {},
) {
  return renderToStaticMarkup(createElement(MobileWorkspaceShell, {
    themeMode: "dark",
    accountName: "Owner",
    accountEmail: "owner@example.com",
    connectedInboxCount: 2,
    syncFeedbackMessage: options.syncFeedbackMessage,
    mailboxSyncPresentation: presentation,
    mailboxes: [mailbox("a"), mailbox("b")].map((entry) => ({
      ...entry,
      refreshStatus: options.refreshStatus ?? entry.refreshStatus,
    })),
    priorityMessages: [],
    mobileNavRestoreContext: {
      tab: options.tab ?? "inboxes",
      mailboxId: mailboxId ?? undefined,
    },
    onLogoutClick: () => {},
    onSyncMailbox: () => {},
    onComposeMailbox: () => {},
  }));
}

const refreshing: MailboxSyncPresentation = {
  operationInFlight: true,
  inboxRefreshing: true,
  inboxUpdated: false,
  message: "Syncing…",
};
const background: MailboxSyncPresentation = {
  operationInFlight: true,
  inboxRefreshing: false,
  inboxUpdated: true,
  message: "Updated",
};

function syncButton(html: string, label: string) {
  const button = html.match(new RegExp(`<button[^>]*aria-label="${label}"[^>]*>`));
  assert.ok(button, `missing Sync button: ${label}`);
  return button[0];
}

function assertMessageAvailable(html: string) {
  const button = html.match(
    /<button\b([^>]*)>(?:(?!<\/button>)[\s\S])*Available message a(?:(?!<\/button>)[\s\S])*<\/button>/,
  );
  assert.ok(button, "the message remains an interactive row");
  assert.doesNotMatch(button[1], /disabled=/);
}

test("mobile cached messages stay available while Inbox refresh spins", () => {
  const html = render({ a: refreshing });
  assert.match(syncButton(html, refreshing.message!), /disabled=""/);
  assert.match(html, /animate-spin/);
  assertMessageAvailable(html);
  assert.doesNotMatch(html, /Updated/);
});

test("mobile fresh Inbox stops spinning while the provider operation stays locked", () => {
  const html = render({ a: background });
  assert.match(syncButton(html, "Sync mailbox"), /disabled=""/);
  assert.doesNotMatch(html, /animate-spin/);
  assertMessageAvailable(html);
  assert.doesNotMatch(html, /Refresh requested|Refresh complete|Everything is synced/);
});

test("mobile background failure preserves fresh Inbox presentation and rows", () => {
  const html = render({ a: {
    operationInFlight: false,
    inboxRefreshing: false,
    inboxUpdated: true,
    message: "Some folders couldn’t update",
  } });
  assert.match(html, /Some folders couldn’t update/);
  assertMessageAvailable(html);
  assert.doesNotMatch(syncButton(html, "Sync mailbox"), /disabled=/);
  assert.doesNotMatch(html, /animate-spin|Refresh complete/);
});

test("mobile failed Inbox refresh keeps rows without claiming an update", () => {
  const html = render({ a: {
    operationInFlight: false,
    inboxRefreshing: false,
    inboxUpdated: false,
    message: "Couldn’t update inbox",
  } });
  assert.match(html, /Couldn’t update inbox/);
  assertMessageAvailable(html);
  assert.doesNotMatch(html, /Updated|Refresh complete/);
});

test("mobile concurrent mailboxes render their own readiness and lock", () => {
  const presentation = { a: background, b: refreshing };
  const first = render(presentation, "a");
  const second = render(presentation, "b");
  assert.match(syncButton(first, "Sync mailbox"), /disabled=""/);
  assert.doesNotMatch(first, /animate-spin/);
  assert.match(syncButton(second, refreshing.message!), /disabled=""/);
  assert.match(second, /animate-spin/);
  assert.doesNotMatch(second, /Updated/);
});

test("mobile inbox cards show both mailboxes' current presentation", () => {
  const html = render({ a: background, b: refreshing }, null);
  assert.match(html, /Updated/);
  assert.match(html, /Syncing…/);
  assert.doesNotMatch(html, /Refresh requested/);
});

test("mobile completed provider operation restores manual Sync", () => {
  const html = render({ a: {
    operationInFlight: false,
    inboxRefreshing: false,
    inboxUpdated: true,
    message: "Updated",
  } });
  assert.doesNotMatch(syncButton(html, "Sync mailbox"), /disabled=/);
  assert.doesNotMatch(html, /animate-spin/);
});

test("mobile idle does not fall back to stale manual diagnostics", () => {
  const html = render({});
  assert.doesNotMatch(html, /Refresh requested/);
  assert.doesNotMatch(syncButton(html, "Sync mailbox"), /disabled=/);
});

const timeoutStatus =
  "Refresh is taking longer than expected — please wait or close and re-open.";

test("mobile active sync keeps concise copy even after a manual timeout", () => {
  const html = render({ a: refreshing }, "a", { refreshStatus: timeoutStatus });
  assert.match(html, /Syncing…/);
  assert.doesNotMatch(html, /Refresh is taking longer than expected/);
  assert.match(syncButton(html, refreshing.message!), /disabled=""/);
  assertMessageAvailable(html);
});

test("mobile fresh Inbox supersedes stale manual requested and timeout statuses", () => {
  for (const refreshStatus of ["↻ Refresh requested…", timeoutStatus]) {
    const html = render({ a: background }, "a", { refreshStatus });
    assert.match(html, /Updated/);
    assert.doesNotMatch(html, /Refresh requested|Refresh is taking longer/);
  }
});

test("mobile failed refresh hides raw diagnostics behind concise Inbox copy", () => {
  const html = render({ a: {
    operationInFlight: false,
    inboxRefreshing: false,
    inboxUpdated: false,
    message: "Couldn’t update inbox",
  } }, "a", { refreshStatus: "Refresh failed — reconnect this inbox in Settings." });
  assert.match(html, /Couldn’t update inbox/);
  assert.doesNotMatch(html, /reconnect this inbox in Settings/);
  assertMessageAvailable(html);
});

test("mobile queued and skipped diagnostics do not narrate background activity", () => {
  for (const refreshStatus of [
    "⚠ Skipped — already syncing",
    "⏳ Queued — startup sync in progress",
  ]) {
    const html = render({ a: background }, "a", { refreshStatus });
    assert.match(html, /Updated/);
    assert.ok(!html.includes(refreshStatus));
  }
});

test("mobile active Inbox phase suppresses stale unscoped sync feedback", () => {
  const html = render({ a: background }, "a", {
    syncFeedbackMessage: "Syncing unrelated mailbox b",
  });
  assert.match(html, /Updated/);
  assert.doesNotMatch(html, /Syncing unrelated mailbox b/);
});

test("mobile Settings retains global sync feedback", () => {
  const html = render({ a: background }, null, {
    tab: "settings",
    syncFeedbackMessage: "Syncing mailbox b",
  });
  assert.match(html, /Syncing mailbox b/);
});

const idle: MailboxSyncPresentation = {
  operationInFlight: false, inboxRefreshing: false, inboxUpdated: false, message: null,
};

test("mobile expired success stays silent despite late manual completion or global feedback", () => {
  for (const refreshStatus of ["✓ Synced — 42 messages", "⚠ Partial refresh — quota limit (42 cached)"]) {
    const html = render({ a: { ...idle, inboxUpdated: true } }, "a", {
      refreshStatus, syncFeedbackMessage: "Inbox refresh complete",
    });
    assert.doesNotMatch(html, /Updated|Synced|quota|Inbox refresh complete/);
    assertMessageAvailable(html);
    assert.doesNotMatch(syncButton(html, "Sync mailbox"), /disabled=/);
  }
});

test("mobile switch to idle B cannot show A's success or unscoped feedback", () => {
  const html = render({ a: background, b: idle }, "b", { syncFeedbackMessage: "Updated mailbox a" });
  assert.doesNotMatch(html, /Updated|Syncing…|animate-spin/);
  assert.doesNotMatch(syncButton(html, "Sync mailbox"), /disabled=/);
});

test("mobile silent background work keeps the provider button locked and rows usable", () => {
  const html = render({ a: { ...background, message: null } });
  assert.match(syncButton(html, "Sync mailbox"), /disabled=""/);
  assert.doesNotMatch(html, /Updated|Syncing…|animate-spin|Refresh requested/);
  assertMessageAvailable(html);
});
