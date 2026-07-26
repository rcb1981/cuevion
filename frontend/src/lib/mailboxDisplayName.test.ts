/**
 * Run with:
 *   cd frontend && node -e "require('./node_modules/sucrase/register/ts.js'); require('./src/lib/mailboxDisplayName.test.ts')"
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  buildCanonicalWorkspaceMailboxPresentations,
  buildWorkspaceMailboxPresentationLabels,
  resolveWorkspaceMailboxDisplayName,
  type WorkspaceMailboxDisplayRecord,
} from "./mailboxDisplayName";

let passed = 0;
let failed = 0;

function test(name: string, fn: () => void) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed += 1;
  } catch (error) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${(error as Error).message}`);
    failed += 1;
  }
}

const onboardingCustomInboxes = [
  {
    id: "custom:inbox-2",
    name: "Inbox 2",
  },
];

function connectedCustomMailbox(
  overrides: Partial<WorkspaceMailboxDisplayRecord> = {},
): WorkspaceMailboxDisplayRecord {
  return {
    id: "imap-server-generated",
    onboardingInboxId: "custom:inbox-2",
    provider: "custom_imap",
    title: "Inbox 2",
    email: "promo@example.com",
    connected: true,
    connectionMethod: "imap",
    connectionStatus: "connected",
    ...overrides,
  };
}

function authoritativeCustomMailbox(
  overrides: Partial<WorkspaceMailboxDisplayRecord> = {},
): WorkspaceMailboxDisplayRecord {
  return {
    ...connectedCustomMailbox(),
    title: "promo@example.com",
    ...overrides,
  };
}

function buildPresentedMailboxes({
  managedInboxes = [connectedCustomMailbox()],
  authoritativeManagedInboxes = [authoritativeCustomMailbox()],
  mailboxTitleOverrides = {},
  customInboxes = onboardingCustomInboxes,
}: {
  managedInboxes?: WorkspaceMailboxDisplayRecord[];
  authoritativeManagedInboxes?: WorkspaceMailboxDisplayRecord[];
  mailboxTitleOverrides?: Record<string, string | undefined>;
  customInboxes?: Array<{ id: string; name: string }>;
} = {}) {
  return buildCanonicalWorkspaceMailboxPresentations({
    mailboxes: managedInboxes.map((mailbox) => ({
      id: mailbox.id,
      title: mailbox.title,
      email: mailbox.email,
      detail: "Connected custom inbox",
      state: "CONNECTED",
    })),
    managedInboxes,
    authoritativeManagedInboxes,
    customInboxes,
    mailboxTitleOverrides,
  });
}

console.log("\nmailboxDisplayName");

test("connected custom mailbox uses its authoritative title", () => {
  assert.equal(buildPresentedMailboxes()[0].title, "promo@example.com");
});

test("missing authoritative title falls back to authoritative email", () => {
  const presented = buildPresentedMailboxes({
    authoritativeManagedInboxes: [
      authoritativeCustomMailbox({ title: "" }),
    ],
  });

  assert.equal(presented[0].title, "promo@example.com");
});

test("whitespace-only authoritative title is ignored", () => {
  const presented = buildPresentedMailboxes({
    authoritativeManagedInboxes: [
      authoritativeCustomMailbox({ title: "   " }),
    ],
  });

  assert.equal(presented[0].title, "promo@example.com");
});

test("manual title override has highest priority and is trimmed", () => {
  const presented = buildPresentedMailboxes({
    mailboxTitleOverrides: {
      "imap-server-generated": "  Hysteria Promo  ",
    },
  });

  assert.equal(presented[0].title, "Hysteria Promo");
});

test("missing authoritative match keeps the onboarding name", () => {
  const presented = buildPresentedMailboxes({
    authoritativeManagedInboxes: [
      authoritativeCustomMailbox({ id: "different-server-mailbox" }),
    ],
  });

  assert.equal(presented[0].title, "Inbox 2");
});

test("unconnected custom draft keeps the onboarding name", () => {
  const draft = connectedCustomMailbox({
    id: "custom:inbox-2",
    connected: false,
    connectionStatus: "not_connected",
    email: "",
  });
  const presented = buildPresentedMailboxes({
    managedInboxes: [draft],
  });

  assert.equal(presented[0].title, "Inbox 2");
});

test("ambiguous authoritative matches do not choose a title", () => {
  const authoritative = authoritativeCustomMailbox();
  const presented = buildPresentedMailboxes({
    authoritativeManagedInboxes: [
      authoritative,
      { ...authoritative },
    ],
  });

  assert.equal(presented[0].title, "Inbox 2");
});

test("duplicate server identity fails safe even when only one record matches the position", () => {
  const presented = buildPresentedMailboxes({
    authoritativeManagedInboxes: [
      authoritativeCustomMailbox(),
      authoritativeCustomMailbox({
        onboardingInboxId: "custom:inbox-3",
        email: "other@example.com",
      }),
    ],
  });

  assert.equal(presented[0].title, "Inbox 2");
});

test("duplicate normalized email fails safe even when server IDs differ", () => {
  const presented = buildPresentedMailboxes({
    authoritativeManagedInboxes: [
      authoritativeCustomMailbox(),
      authoritativeCustomMailbox({
        id: "imap-server-other",
        onboardingInboxId: "custom:inbox-3",
      }),
    ],
  });

  assert.equal(presented[0].title, "Inbox 2");
});

test("presentation leaves server mailbox ID unchanged", () => {
  const presented = buildPresentedMailboxes();

  assert.equal(presented[0].id, "imap-server-generated");
});

test("presentation leaves onboarding position unchanged", () => {
  const managed = connectedCustomMailbox();
  const before = managed.onboardingInboxId;

  buildPresentedMailboxes({ managedInboxes: [managed] });

  assert.equal(managed.onboardingInboxId, before);
});

test("name resolution is pure and does not mutate config-shaped inputs", () => {
  const managed = connectedCustomMailbox();
  const authoritative = authoritativeCustomMailbox();
  const before = JSON.stringify({ managed, authoritative });

  resolveWorkspaceMailboxDisplayName({
    mailbox: managed,
    authoritativeManagedInboxes: [authoritative],
    onboardingName: "Inbox 2",
    fallbackTitle: "Inbox 2",
  });

  assert.equal(JSON.stringify({ managed, authoritative }), before);
});

test("navigation, mailbox header, and message-list heading share one canonical name", () => {
  const canonicalName = buildPresentedMailboxes()[0].title;
  const labels = buildWorkspaceMailboxPresentationLabels(canonicalName);

  assert.deepEqual(labels, {
    navigationName: "promo@example.com",
    mailboxHeader: "promo@example.com Inbox",
    messageListHeading: "promo@example.com Inbox",
  });
});

test("refresh and hydration reproduce the same canonical name", () => {
  const first = buildPresentedMailboxes()[0].title;
  const hydrated = buildPresentedMailboxes({
    managedInboxes: [structuredClone(connectedCustomMailbox())],
    authoritativeManagedInboxes: [
      structuredClone(authoritativeCustomMailbox()),
    ],
  })[0].title;

  assert.equal(hydrated, first);
});

test("Gmail presentation remains unchanged", () => {
  const gmailMailbox = {
    id: "gmail-server-generated",
    onboardingInboxId: "main",
    provider: "google",
    title: "Carltricksmusic",
    email: "owner@example.com",
    connected: true,
    connectionMethod: "oauth",
    connectionStatus: "connected",
  };
  const presented = buildPresentedMailboxes({
    managedInboxes: [gmailMailbox],
    authoritativeManagedInboxes: [
      {
        ...gmailMailbox,
        title: "owner@example.com",
      },
    ],
    customInboxes: [],
  });

  assert.equal(presented[0].title, "Carltricksmusic");
});

test("multiple custom mailboxes each resolve their own authoritative name", () => {
  const secondManaged = connectedCustomMailbox({
    id: "imap-server-second",
    onboardingInboxId: "custom:inbox-3",
    title: "Inbox 3",
    email: "bookings@example.com",
  });
  const presented = buildPresentedMailboxes({
    managedInboxes: [connectedCustomMailbox(), secondManaged],
    authoritativeManagedInboxes: [
      authoritativeCustomMailbox(),
      authoritativeCustomMailbox({
        id: "imap-server-second",
        onboardingInboxId: "custom:inbox-3",
        title: "Bookings",
        email: "bookings@example.com",
      }),
    ],
    customInboxes: [
      ...onboardingCustomInboxes,
      { id: "custom:inbox-3", name: "Inbox 3" },
    ],
  });

  assert.deepEqual(
    presented.map((mailbox) => mailbox.title),
    ["promo@example.com", "Bookings"],
  );
});

test("equal titles do not merge distinct mailbox identities or positions", () => {
  const secondManaged = connectedCustomMailbox({
    id: "imap-server-second",
    onboardingInboxId: "custom:inbox-3",
    title: "Inbox 3",
    email: "second@example.com",
  });
  const presented = buildPresentedMailboxes({
    managedInboxes: [connectedCustomMailbox(), secondManaged],
    authoritativeManagedInboxes: [
      authoritativeCustomMailbox({ title: "Shared label" }),
      authoritativeCustomMailbox({
        id: "imap-server-second",
        onboardingInboxId: "custom:inbox-3",
        title: "Shared label",
        email: "second@example.com",
      }),
    ],
    customInboxes: [
      ...onboardingCustomInboxes,
      { id: "custom:inbox-3", name: "Inbox 3" },
    ],
  });

  assert.deepEqual(
    presented.map(({ id, title }) => ({ id, title })),
    [
      { id: "imap-server-generated", title: "Shared label" },
      { id: "imap-server-second", title: "Shared label" },
    ],
  );
});

test("normalized email participates in the exact identity match", () => {
  const presented = buildPresentedMailboxes({
    authoritativeManagedInboxes: [
      authoritativeCustomMailbox({ email: "  PROMO@EXAMPLE.COM  " }),
    ],
  });

  assert.equal(presented[0].title, "promo@example.com");
});

test("a mismatched normalized email fails safe to the onboarding name", () => {
  const presented = buildPresentedMailboxes({
    authoritativeManagedInboxes: [
      authoritativeCustomMailbox({ email: "other@example.com" }),
    ],
  });

  assert.equal(presented[0].title, "Inbox 2");
});

test("an onboarding position is never accepted as a server mailbox ID", () => {
  const invalidIdentity = connectedCustomMailbox({
    id: "custom:inbox-2",
  });
  const name = resolveWorkspaceMailboxDisplayName({
    mailbox: invalidIdentity,
    authoritativeManagedInboxes: [
      authoritativeCustomMailbox({ id: "custom:inbox-2" }),
    ],
    onboardingName: "Inbox 2",
    fallbackTitle: "Inbox 2",
  });

  assert.equal(name, "Inbox 2");
});

test("long authoritative email stays intact while workspace surfaces retain truncation", () => {
  const longEmail =
    "an-extremely-long-custom-mailbox-address-for-layout-regression@example.com";
  const presented = buildPresentedMailboxes({
    managedInboxes: [connectedCustomMailbox({ email: longEmail })],
    authoritativeManagedInboxes: [
      authoritativeCustomMailbox({ title: "", email: longEmail }),
    ],
  });
  const workspaceSource = fs.readFileSync(
    path.resolve(__dirname, "../components/workspace/WorkspaceShell.tsx"),
    "utf8",
  );
  const mobileSource = fs.readFileSync(
    path.resolve(
      __dirname,
      "../components/workspace/mobile/MobileWorkspaceShell.tsx",
    ),
    "utf8",
  );

  assert.equal(presented[0].title, longEmail);
  assert.match(
    workspaceSource,
    /max-w-\[min\(70vw,32rem\)\][^"]*[\s\S]{0,500}truncate/,
  );
  assert.equal(
    workspaceSource.includes(
      'className="block min-w-0 truncate text-[0.8rem] font-medium uppercase',
    ),
    true,
  );
  assert.match(mobileSource, /min-w-0 flex-1 truncate text-center/);
});

if (failed > 0) {
  process.exitCode = 1;
}

console.log(`${passed} mailbox display-name tests passed`);
