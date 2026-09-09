import assert from "node:assert/strict";
import {
  buildExactMailboxMessageSeed,
  captureExactMailboxMessageTicket,
  reduceExactMailboxMessagePublication,
  type ExactMailboxMessageProjection,
  type ExactMailboxMessageScope,
} from "./exactMailboxMessageStore";
import type { ExactMailboxMessageIdentity, ExactMailboxMessageResponse, ExactMailboxSourceRef } from "./exactMailboxMessageApi";
import type { normalizeMailMessage } from "../components/workspace/WorkspaceShell";

const googleSource: ExactMailboxSourceRef = { provider: "google", providerMessageId: "provider-message-1" };
const imapSource: ExactMailboxSourceRef = { provider: "custom_imap", folder: "INBOX", uidValidity: "23", imapUid: "42" };
const scope: ExactMailboxMessageScope = {
  accountId: "usr_AAAAAAAAAAAAAAAAAAAAAA", workspaceId: "wsp_AAAAAAAAAAAAAAAAAAAAAA",
  mailboxId: "main", provider: "google", configRevision: 4, generation: 8,
};
const message = {
  id: "gmail-provider-message-1", serverMailboxId: "main", providerFolder: "Inbox",
  providerMessageId: "provider-message-1", subject: "Same subject", body: ["Body"],
};
function state(provider: "google" | "custom_imap" = "google") {
  return {
    scope: { ...scope, provider },
    mailboxes: { main: { Inbox: [], Drafts: [], Sent: [], Archive: [], Filtered: [], Spam: [], Trash: [] } },
    selection: null,
  } as ExactMailboxMessageProjection<ExactMailboxMessageIdentity>;
}
function response(sourceRef = googleSource, value: unknown = message) {
  return { v: 1, mailboxId: "main", sourceRef, message: value } as ExactMailboxMessageResponse;
}
let tests = 0;
function test(name: string, run: () => void) { run(); tests++; console.log(`PASS ${name}`); }

test("cold item augments only its folder and selects the exact display message", () => {
  const initial = state();
  const unrelated = { ...message, id: "unrelated", providerMessageId: "other" };
  initial.mailboxes.main.Inbox = [unrelated];
  const next = reduceExactMailboxMessagePublication(initial, captureExactMailboxMessageTicket(scope, googleSource), response(), message);
  assert.deepEqual(next.mailboxes.main.Inbox, [unrelated, message]);
  assert.deepEqual(next.selection, { mailboxId: "main", messageId: message.id, folder: "Inbox" });
  assert.equal(next.mailboxes.main.Sent, initial.mailboxes.main.Sent);
  assert.deepEqual(initial.mailboxes.main.Inbox, [unrelated]);
});
test("loaded Google identity dedupes and preserves the current object and folder", () => {
  const initial = state();
  const loaded = { ...message, id: "existing-render-id", localCollaboration: "preserved" };
  initial.mailboxes.main.Archive = [loaded];
  const next = reduceExactMailboxMessagePublication(initial, captureExactMailboxMessageTicket(scope, googleSource), response(), message);
  assert.equal(next.mailboxes, initial.mailboxes);
  assert.equal(next.mailboxes.main.Archive[0], loaded);
  assert.equal(next.selection?.messageId, loaded.id);
  assert.equal(next.selection?.folder, "Archive");
});
test("duplicate exact Google identity across folders retains one canonical row", () => {
  const initial = state();
  initial.mailboxes.main.Inbox = [message, message];
  initial.mailboxes.main.Filtered = [{ ...message }];
  const next = reduceExactMailboxMessagePublication(initial, captureExactMailboxMessageTicket(scope, googleSource), response(), message);
  assert.equal(next.mailboxes.main.Inbox.length, 1);
  assert.equal(next.mailboxes.main.Filtered.length, 0);
});
test("loaded IMAP identity includes live UIDVALIDITY and UID", () => {
  const initial = state("custom_imap");
  const imapMessage = { ...message, providerMessageId: undefined, id: "imap-uid-42", providerFolder: "INBOX", uidValidity: "23", imapUid: "42" };
  initial.mailboxes.main.Inbox = [imapMessage];
  const next = reduceExactMailboxMessagePublication(initial, captureExactMailboxMessageTicket(initial.scope!, imapSource), response(imapSource, imapMessage), imapMessage);
  assert.equal(next.mailboxes.main.Inbox.length, 1);
  assert.equal(next.mailboxes.main.Inbox[0], imapMessage);
});
for (const [name, change] of [
  ["account switch", { accountId: "usr_BBBBBBBBBBBBBBBBBBBBBB" }],
  ["workspace switch", { workspaceId: "wsp_BBBBBBBBBBBBBBBBBBBBBB" }],
  ["mailbox switch", { mailboxId: "another" }],
  ["provider change", { provider: "custom_imap" as const }],
  ["mailbox config change", { configRevision: 5 }],
  ["scope changes away and back", { generation: 9 }],
] as const) {
  test(`${name} fences both insertion and selection`, () => {
    const initial = state();
    const ticket = captureExactMailboxMessageTicket(initial.scope!, googleSource);
    initial.scope = { ...initial.scope!, ...change };
    assert.equal(reduceExactMailboxMessagePublication(initial, ticket, response(), message), initial);
    assert.equal(initial.selection, null);
    assert.equal(initial.mailboxes.main.Inbox.length, 0);
  });
}
test("signed-out current scope fences publication", () => {
  const initial = state();
  const ticket = captureExactMailboxMessageTicket(initial.scope!, googleSource);
  initial.scope = null;
  assert.equal(reduceExactMailboxMessagePublication(initial, ticket, response(), message), initial);
});
test("aborted ticket prevents a provider result from publishing", () => {
  const initial = state();
  const controller = new AbortController();
  const ticket = captureExactMailboxMessageTicket(scope, googleSource, controller.signal);
  controller.abort();
  assert.equal(reduceExactMailboxMessagePublication(initial, ticket, response(), message), initial);
});
test("envelope source mismatch fails closed even when message identity matches", () => {
  const initial = state();
  assert.equal(reduceExactMailboxMessagePublication(initial, captureExactMailboxMessageTicket(scope, googleSource), response({ provider: "google", providerMessageId: "wrong" }), message), initial);
});
test("normalizer changing source identity cannot publish", () => {
  const initial = state();
  assert.equal(reduceExactMailboxMessagePublication(initial, captureExactMailboxMessageTicket(scope, googleSource), response(), { ...message, providerMessageId: "wrong" }), initial);
});
test("wrong mailbox response cannot publish", () => {
  const initial = state();
  assert.equal(reduceExactMailboxMessagePublication(initial, captureExactMailboxMessageTicket(scope, googleSource), { ...response(), mailboxId: "other" }, message), initial);
});
test("renderer ID collision with a different exact source fails closed", () => {
  const initial = state();
  initial.mailboxes.main.Inbox = [{ ...message, providerMessageId: "other-source" }];
  assert.equal(reduceExactMailboxMessagePublication(initial, captureExactMailboxMessageTicket(scope, googleSource), response(), message), initial);
});
test("existing exact row renderer ID collision also fails closed", () => {
  const initial = state();
  initial.mailboxes.main.Inbox = [
    { ...message, id: "existing-render-id" },
    { ...message, id: "existing-render-id", providerMessageId: "unrelated" },
  ];
  assert.equal(reduceExactMailboxMessagePublication(initial, captureExactMailboxMessageTicket(scope, googleSource), response(), message), initial);
});
test("canonical exact seed fits the existing WorkspaceShell renderer input", () => {
  const value = response();
  value.message.timestamp = "2026-09-09T09:00:00Z";
  const seed: Parameters<typeof normalizeMailMessage>[0] = buildExactMailboxMessageSeed(value);
  assert.equal(seed.time, value.message.timestamp);
  assert.equal(seed.threadIdentityContext?.mailboxId, "main");
  assert.equal(seed.threadIdentityContext?.provider, "google");
  assert.equal(seed.threadIdentityAuthority, "unique_message");
});
test("cold IMAP seed augments without replacing existing message list", () => {
  const initial = state("custom_imap");
  const imapMessage = { ...message, providerMessageId: undefined, id: "exact:imap:main:INBOX:23:42", providerFolder: "INBOX", uidValidity: "23", imapUid: "42" };
  const olderEpoch = { ...imapMessage, id: "exact:imap:main:INBOX:22:42", uidValidity: "22" };
  initial.mailboxes.main.Inbox = [olderEpoch];
  const value = response(imapSource, imapMessage);
  const seed: Parameters<typeof normalizeMailMessage>[0] = buildExactMailboxMessageSeed(value);
  assert.equal(seed.threadIdentityContext?.uidValidity, "23");
  assert.equal(seed.threadIdentityContext?.provider, "custom_imap");
  const next = reduceExactMailboxMessagePublication(initial, captureExactMailboxMessageTicket(initial.scope!, imapSource), value, seed);
  assert.equal(next.mailboxes.main.Inbox.length, 2);
  assert.equal(next.mailboxes.main.Inbox[0], olderEpoch);
  assert.equal(next.selection?.messageId, imapMessage.id);
});
test("same subject and sender never identify a target", () => {
  const initial = state();
  initial.mailboxes.main.Inbox = [{ ...message, id: "unrelated", providerMessageId: "other-source" }];
  const next = reduceExactMailboxMessagePublication(initial, captureExactMailboxMessageTicket(scope, googleSource), response(), message);
  assert.equal(next.mailboxes.main.Inbox.length, 2);
  assert.equal(next.selection?.messageId, message.id);
});
test("scope ticket snapshots caller state and cannot be changed", () => {
  const mutableScope = { ...scope };
  const ticket = captureExactMailboxMessageTicket(mutableScope, googleSource);
  mutableScope.configRevision++;
  assert.equal(ticket.scope.configRevision, 4);
  assert.equal(Object.isFrozen(ticket), true);
  assert.equal(Object.isFrozen(ticket.scope), true);
  assert.equal(Object.isFrozen(ticket.sourceRef), true);
});
console.log(`${tests} exact mailbox publication tests passed`);
