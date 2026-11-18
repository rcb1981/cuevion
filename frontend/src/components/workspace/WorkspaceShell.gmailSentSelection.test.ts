import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as React from "react";
import { act, create } from "react-test-renderer";
import ts from "typescript";
import { applyLiveThreadIdentity, buildRenderedConversationRows } from "../../lib/inboxEngine";
import { createGmailSentController, mergeGmailSentDisplay } from "../../lib/gmailSentState";
import { MailboxSnapshotBoundary } from "../../lib/MailboxSnapshotContext";
import { activateMailboxSnapshotSession, clearMailboxSnapshotSession } from "../../lib/mailboxSnapshotSession";
import { reduceFullMessageModalInteraction } from "./fullMessageModalState";

// Execute the actual component's click handler, reconciliation effect, grouping,
// and reading-pane selection in React, without mounting unrelated workspace UI.
// The override runs this same regression against an unchanged baseline checkout.
const sourcePath = process.env.CUEVION_SELECTION_SOURCE ?? resolve(__dirname, "WorkspaceShell.tsx");
const source = readFileSync(sourcePath, "utf8");
const parsed = ts.createSourceFile(sourcePath, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const view = parsed.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "MailboxView") as ts.FunctionDeclaration;
assert.ok(view?.body);
const variable = (name: string, optional = false) => {
  const statement = view.body!.statements.find((node) => ts.isVariableStatement(node) &&
    node.declarationList.declarations.some((declaration) => declaration.name.getText(parsed) === name));
  if (!statement && optional) return "";
  assert.ok(statement, `MailboxView declaration: ${name}`);
  return statement.getText(parsed);
};
const topFunction = (name: string) => {
  const statement = parsed.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === name);
  assert.ok(statement, `Workspace function: ${name}`);
  return statement.getText(parsed);
};
const effects = view.body!.statements.filter((node) => ts.isExpressionStatement(node) &&
  ts.isCallExpression(node.expression) && node.expression.expression.getText(parsed) === "useEffect" &&
  node.getText(parsed).includes("const resolvedSelections =") &&
  node.getText(parsed).includes("setMailboxScopedSelectionState"));
assert.equal(effects.length, 1, "execute the selection-reconciliation effect, not a test copy");
const componentSource = `
  ${topFunction("resolveMailDateMs")}
  ${topFunction("dedupeMessagesForNormalAppRenderedRows")}
  function SelectionHarness(props) {
    const mailbox = { id: props.mailboxId ?? "box" };
    const activeFolder = props.folder ?? "Sent";
    const isImapSentView = false;
    const isGmailSentView = props.provider === "google" && activeFolder === "Sent" && props.connected !== false;
    const mailboxStore = props.store;
    const mailboxCollections = mailboxStore[mailbox.id];
    const folderMessages = isGmailSentView
      ? mergeGmailSentDisplay(mailbox.id, props.messages, mailboxStore[mailbox.id].Sent, props.observed ?? new Set())
      : mailboxStore[mailbox.id][activeFolder];
    const visibleMessages = folderMessages;
    const sortOrder = "desc";
    const currentMessageLocationById = Object.fromEntries(folderMessages.map(message => [message.id, { mailboxId: mailbox.id, folder: activeFolder }]));
    const currentMessageLocationByMessage = new WeakMap(folderMessages.map(message => [message, { mailboxId: mailbox.id, folder: activeFolder }]));
    const [mailboxScopedSelectionState, setMailboxScopedSelectionState] = useState({ selected: [], primaryKey: null, anchorKey: null });
    const [isFullMessageOpen, setIsFullMessageOpen] = useState(false);
    const resetFullMessageModalSize = () => {};
    const onRecordMessageOwnershipInteraction = () => {};
    ${[
      "selectedMessageSelections", "primaryMessageSelection", "selectedMessageId",
      "dedupeMessagesForRenderedRows", "threadDedupedMessages", "sortedMessages",
      "getCurrentMessageSourceMailboxId", "buildCurrentMessageSelection",
      "selectableMessageRows", "selectableMessageSelections", "matchesPrimarySelection",
      "selectedMessageFromFolder", "visibleSelectedMessageKeys", "visibleSelectedMessageIds",
      "selectedMessageFromVisiblePrimary", "selectedMessageFromVisibleSelection",
      "resolveGmailSentSelection", "resolveImapSentSelection",
      "selectedMessageFromCurrentId", "selectedMessageFallbackTop", "selectedMessage", "fullWidthMessage",
      "fullMessageModalInteractionState", "fullMessageModalResolvedMessage",
      "handleSelectMessage",
    ].map((name) => variable(name, name === "resolveGmailSentSelection")).join("\n")}
    ${effects[0].getText(parsed)}
    props.record?.({ selected: selectedMessageId, pane: selectedMessage?.id ?? null });
    return createElement("selection-probe", {
      state: mailboxScopedSelectionState, pane: selectedMessage, fullPane: fullWidthMessage,
      modal: fullMessageModalResolvedMessage, rows: sortedMessages,
      click: (id, options) => handleSelectMessage(activeFolder, id, options),
    });
  }
  return SelectionHarness;
`;
const subject = require(sourcePath) as typeof import("./WorkspaceShell");
const helpers = {
  buildMailboxScopedMessageSelection: subject.buildMailboxScopedMessageSelection,
  resolveMailboxScopedSelectionEntries: subject.resolveMailboxScopedSelectionEntries,
  resolveAuthoritativeMessageLocation: subject.resolveAuthoritativeMessageLocation,
  reduceMailboxScopedMessageSelection: subject.reduceMailboxScopedMessageSelection,
  useState: React.useState, useEffect: React.useEffect, createElement: React.createElement,
  mergeGmailSentDisplay,
  buildRenderedConversationRows,
  reduceFullMessageModalInteraction,
};
const Harness = new Function(...Object.keys(helpers), ts.transpileModule(componentSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
}).outputText)(...Object.values(helpers));

const folders = ["Inbox", "Drafts", "Sent", "Archive", "Filtered", "Spam", "Trash"];
const emptyCollections = () => Object.fromEntries(folders.map((folder) => [folder, []]));
function message(id: string, order: number, mailboxId = "box", thread = id, provider = "google") {
  return applyLiveThreadIdentity({
    id: `gmail-sent:${mailboxId}:${id}`, serverMailboxId: mailboxId, providerFolder: "Sent",
    providerMessageId: id, providerThreadId: thread, labelIds: ["SENT"],
    sender: "Synthetic sender", from: "sender@example.test", to: "recipient@example.test",
    subject: `Synthetic ${id}`, snippet: `Synthetic body ${id}`, body: [`Synthetic body ${id}`],
    createdAt: `2026-11-18T${String(order).padStart(2, "0")}:00:00Z`, timestamp: "2026-11-18", time: "fixture",
    category: "Primary", categorySource: "system", categoryConfidence: "low", priorityScore: "low", signal: "Sent",
  }, { mailboxId, provider, folder: "SENT", uidValidity: "gmail-api" });
}

async function run() {
  const first = message("first", 12), middle = message("middle", 11), last = message("last", 10);
  const store = { box: emptyCollections(), other: emptyCollections() };
  const untouchedStore = JSON.stringify(store);
  const renders: Array<{ selected: string | null; pane: string | null }> = [];
  let props = { provider: "google", messages: [first, middle, last], store, record: (entry: any) => renders.push(entry) };
  let root!: ReturnType<typeof create>;
  await act(async () => { root = create(React.createElement(Harness, props)); });
  const probe = () => root.root.findByType("selection-probe").props;
  const update = async (changes: any) => { props = { ...props, ...changes }; await act(async () => { root.update(React.createElement(Harness, props)); }); };
  const select = async (row: typeof first, options?: object) => {
    await act(async () => { probe().click(row.id, options); });
    assert.equal(probe().state.selected[0]?.messageId, row.id,
      `retain ${row.providerMessageId} after effects; transitions=${JSON.stringify(renders.slice(-3))}`);
    assert.equal(probe().pane?.id, row.id, "reading pane must show the clicked message");
    assert.deepEqual(probe().pane.body, row.body);
  };
  assert.equal(probe().pane.id, first.id, "initial folder entry still falls back to the first row");
  // Middle first makes the baseline failure show the exact reported transition:
  // clicked middle -> correct pane -> effect clears selection -> first pane.
  await select(middle);
  await select(first);
  await select(last, { openFull: true });
  assert.equal(probe().modal.id, last.id);
  for (const row of [middle, last, first, middle]) await select(row);
  await act(async () => { for (const row of [last, first, middle]) probe().click(row.id); });
  assert.equal(probe().pane.id, middle.id, "rapid clicks retain the final target");
  const retainedState = probe().state;
  await update({ messages: props.messages.map((row) => ({ ...row })) });
  assert.equal(probe().state, retainedState, "harmless React rerender does not replace selection state");
  assert.equal(probe().pane.id, middle.id);

  // Drive real refresh and Load more state updates through the Sent controller.
  let nextPage = { ok: true as const, page: { messages: [first, middle, last], nextPageToken: "page2" as string | null } };
  const controller = createGmailSentController("box", () => true, async () => nextPage);
  function ControllerHarness() {
    const state = React.useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
    return React.createElement(Harness, { ...props, messages: state.messages, observed: state.observed });
  }
  await act(async () => { root.unmount(); root = create(React.createElement(ControllerHarness)); await controller.refresh(); });
  await select(middle);
  nextPage = { ok: true, page: { messages: [first, { ...middle, body: ["Refreshed synthetic body"] }, last], nextPageToken: "page2" } };
  await act(async () => { await controller.refresh(); });
  assert.equal(probe().pane.id, middle.id);
  assert.deepEqual(probe().pane.body, ["Refreshed synthetic body"]);
  const older = message("older", 9);
  nextPage = { ok: true, page: { messages: [last, older], nextPageToken: null } };
  await act(async () => { await controller.loadMore(); });
  assert.equal(probe().pane.id, middle.id, "Load more preserves a valid selection");
  assert.equal(probe().rows.length, 4);

  // A newer member changes the rendered thread row, not the selected message.
  await select(first);
  await act(async () => { probe().click(middle.id, { isToggle: true }); });
  assert.equal(probe().state.selected.length, 2);
  const reply = message("reply", 13, "box", "middle");
  nextPage = { ok: true, page: { messages: [first, middle, last, reply], nextPageToken: null } };
  await act(async () => { await controller.refresh(); });
  assert.equal(probe().rows.length, 3, "existing conversation grouping is preserved");
  assert.ok(probe().rows.some((row: any) => row.id === reply.id));
  assert.ok(!probe().rows.some((row: any) => row.id === middle.id));
  assert.equal(probe().state.primaryKey, subject.buildMailboxScopedMessageSelection("box", middle, "Sent").key);
  assert.equal(probe().pane.id, middle.id, "retain the exact message when its thread gets a newer row");
  assert.equal(probe().modal.id, middle.id);
  await select(last);
  nextPage = { ok: true, page: { messages: [first, reply], nextPageToken: null } };
  await act(async () => { await controller.refresh(); });
  assert.equal(probe().state.primaryKey, null, "a genuinely removed selection is cleared");
  assert.equal(probe().pane.id, reply.id, "removed selection safely falls back to the current first row");
  nextPage = { ok: true, page: { messages: [], nextPageToken: null } };
  await act(async () => { await controller.refresh(); });
  assert.equal(probe().pane, null, "empty Sent has no stale reading-pane message");

  await act(async () => { root.unmount(); root = create(React.createElement(Harness, props)); });
  await select(middle);
  await update({ mailboxId: "other", messages: [message("middle", 11, "other")] });
  assert.equal(probe().state.primaryKey, null, "mailbox ownership is part of the selection key");
  assert.equal(probe().pane.serverMailboxId, "other");
  await update({ mailboxId: "box", messages: [first, middle, last] });
  await select(last);
  await update({ connected: false });
  assert.equal(probe().state.primaryKey, null);
  assert.equal(probe().pane, null, "disconnect removes provider-only selections");
  assert.equal(JSON.stringify(store), untouchedStore, "provider Sent never enters shared collections or Priority inputs");

  // A local send and its fetched provider copy can have different UI IDs but
  // the same exact mailbox/provider identity. Reconciliation updates the ID.
  const localCopy = { ...middle, id: "local-send-middle" };
  await update({ connected: true, messages: [first], store: { box: { ...emptyCollections(), Sent: [localCopy] } } });
  await select(first);
  await act(async () => { probe().click(localCopy.id, { isToggle: true }); });
  const transitionStart = renders.length;
  await update({ messages: [first, middle, last] });
  const providerSelection = subject.buildMailboxScopedMessageSelection("box", middle, "Sent");
  assert.equal(probe().state.primaryKey, providerSelection.key);
  assert.equal(probe().state.anchorKey, providerSelection.key);
  assert.equal(probe().state.selected[1].messageId, middle.id);
  assert.equal(probe().pane.id, middle.id);
  assert.ok(renders.slice(transitionStart).every((entry) => entry.pane === middle.id), "provider reconciliation never flashes the first message");

  // Other folders/providers still reconcile against their shared mailbox store.
  for (const provider of ["google", "custom_imap", "icloud", "microsoft"]) {
    for (const folder of ["Inbox", "Archive", "Trash", ...(provider === "google" ? [] : ["Sent"])]) {
      const local = [message("first", 12, "box", "first", provider), message("middle", 11, "box", "middle", provider)];
      const collections = { ...emptyCollections(), [folder]: local };
      await update({ mailboxId: "box", provider, connected: true, folder, store: { box: collections }, messages: [] });
      await select(local[1]);
      await update({ store: { box: { ...collections } } });
      assert.equal(probe().pane.id, local[1].id, `${provider} ${folder} selection remains unchanged`);
      await update({ store: { box: { ...collections, [folder]: [local[0]] } } });
      assert.equal(probe().state.primaryKey, null);
      assert.equal(probe().pane.id, local[0].id);
    }
  }

  let owner = { userId: "synthetic-user", workspaceId: "synthetic-workspace" };
  activateMailboxSnapshotSession(owner);
  props = { provider: "google", messages: [first, middle, last], store, record: props.record };
  const ownedTree = () => React.createElement(MailboxSnapshotBoundary, { owner, fallback: React.createElement("signed-out") }, React.createElement(Harness, props));
  await act(async () => { root.unmount(); root = create(ownedTree()); });
  await select(last);
  await act(async () => { clearMailboxSnapshotSession(); });
  assert.equal(root.root.findAllByType("selection-probe").length, 0, "logout unmounts private selections and message bodies");
  await act(async () => { activateMailboxSnapshotSession(owner); });
  assert.equal(probe().state.primaryKey, null, "a new session does not restore the old private selection");
  assert.equal(probe().pane.id, first.id);
  await select(last);
  owner = { userId: "other-user", workspaceId: "other-workspace" };
  props = { ...props, messages: [message("other-account", 13)] };
  await act(async () => { activateMailboxSnapshotSession(owner); root.update(ownedTree()); });
  assert.equal(probe().state.primaryKey, null, "account/workspace switches remount selection state");
  assert.equal(probe().pane.id, props.messages[0].id);
  await act(async () => { root.unmount(); clearMailboxSnapshotSession(); });
  console.log("Gmail Sent selection: clicks, React effects, refresh, pagination, threads, ownership and folder/provider regressions passed");
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
