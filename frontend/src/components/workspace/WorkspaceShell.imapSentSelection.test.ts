import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as React from "react";
import { act, create } from "react-test-renderer";
import ts from "typescript";
import { applyLiveThreadIdentity, buildRenderedConversationRows } from "../../lib/inboxEngine";
import { mergeImapSentDisplay } from "../../lib/imapSentState";
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
    const isGmailSentView = false;
    const isImapSentView = props.provider === "custom_imap" && activeFolder === "Sent" && props.connected !== false;
    const mailboxStore = props.store;
    const mailboxCollections = mailboxStore[mailbox.id];
    const folderMessages = isImapSentView
      ? mergeImapSentDisplay(mailbox.id, props.messages, mailboxStore[mailbox.id].Sent)
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
  mergeImapSentDisplay,
  buildRenderedConversationRows,
  reduceFullMessageModalInteraction,
};
const Harness = new Function(...Object.keys(helpers), ts.transpileModule(componentSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
}).outputText)(...Object.values(helpers));

const folders = ["Inbox", "Drafts", "Sent", "Archive", "Filtered", "Spam", "Trash"];
const emptyCollections = () => Object.fromEntries(folders.map((folder) => [folder, []]));
function message(uid: string, order: number, mailboxId = "box", thread = uid, scope = "connection-a", uv = "101") {
  return applyLiveThreadIdentity({
    id: `imap-sent:${scope}:Sent%20Messages:${uv}:${uid}`, serverMailboxId: mailboxId, providerFolder: "Sent Messages",
    imapUid: uid, uidValidity: uv, threadId: `imap:rfc:${mailboxId}:${thread}`,
    sender: "You", from: "self@example.test", to: "other@example.test", subject: "Same subject",
    snippet: `Body ${uid}`, body: [`Body ${uid}`], createdAt: `2026-11-18T${String(order).padStart(2, "0")}:00:00Z`,
    timestamp: "2026-11-18", time: "fixture", category: "Primary", categorySource: "system", categoryConfidence: "low", priorityScore: "low", signal: "Sent",
    attachments: [{ id: "attachment-2", name: "synthetic.bin", receivedSource: { mailboxId, providerFolder: "Sent Messages", messageUid: uid, uidValidity: uv, folder: "Sent" } }],
  }, { mailboxId, provider: "custom_imap", folder: "Sent Messages", uidValidity: uv });
}

async function run() {
  const first = message("30", 12), middle = message("20", 11), last = message("10", 10);
  const store = { box: emptyCollections(), other: emptyCollections() };
  const untouched = JSON.stringify(store);
  let props = { provider: "custom_imap", mailboxId: "box", connected: true, messages: [first, middle, last], store };
  let root!: ReturnType<typeof create>;
  await act(async () => { root = create(React.createElement(Harness, props)); });
  const probe = () => root.root.findByType("selection-probe").props;
  const update = async (change: any) => { props = { ...props, ...change }; await act(async () => root.update(React.createElement(Harness, props))); };
  const select = async (row: any) => {
    await act(async () => probe().click(row.id, { openFull: true }));
    assert.equal(probe().state.selected[0].messageId, row.id);
    assert.equal(probe().pane.id, row.id); assert.equal(probe().modal.id, row.id);
    assert.deepEqual(probe().pane.attachments, row.attachments);
  };
  for (const row of [middle, first, last, middle]) await select(row);
  const before = probe().state;
  await update({ messages: props.messages.map(m => ({ ...m })) });
  assert.equal(probe().state, before);
  const older = message("5", 9);
  await update({ messages: [...props.messages, older] });
  assert.equal(probe().pane.id, middle.id);
  const reply = message("40", 13, "box", "20");
  await update({ messages: [...props.messages, reply] });
  assert.equal(probe().rows.length, 4);
  assert.equal(probe().pane.id, middle.id, "representative changes preserve exact selected message");
  await update({ messages: props.messages.filter(m => m.id !== middle.id) });
  assert.equal(probe().state.primaryKey, null);
  await select(last);
  await update({ messages: [message("10", 10, "box", "10", "connection-b")] });
  assert.equal(probe().state.primaryKey, null, "reconnect identity cannot alias the previous connection");
  await select(props.messages[0]);
  await update({ messages: [message("10", 10, "box", "10", "connection-b", "102")] });
  assert.equal(probe().state.primaryKey, null, "UIDVALIDITY invalidates old selection");
  await select(props.messages[0]);
  await update({ mailboxId: "other", messages: [message("10", 10, "other")] });
  assert.equal(probe().state.primaryKey, null);
  await select(props.messages[0]);
  await update({ connected: false });
  assert.equal(probe().pane, null);
  assert.equal(JSON.stringify(store), untouched, "provider display leaves all shared folder collections unchanged");
  const forwarded = subject.resolveInitialComposeAttachments("forward", first, { mailboxId: "box", folder: "Sent" });
  assert.equal(forwarded[0].receivedSource.providerFolder, "Sent Messages");
  assert.equal(forwarded[0].receivedSource.messageUid, "30");
  assert.equal(forwarded[0].receivedSource.uidValidity, "101");
  await act(async () => root.unmount());
  console.log("IMAP Sent component selection, conversations, reconnect, UIDVALIDITY and attachments passed");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
