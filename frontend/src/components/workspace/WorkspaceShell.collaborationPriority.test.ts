// Execute the actual integration expressions without loading the application or
// its unrelated provider transports. No protected source is needed by this suite.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as summaryStore from "../../lib/collaborationSummaryStore";
import * as composition from "../../lib/collaborationPriority";
import { dedupeLatestCanonicalConversationEntries, resolveCanonicalConversationIdentity } from "../../lib/inboxEngine";
import { resolveMessageNoisePolicy } from "../../lib/messageNoiseGate";
import { buildNormalPriorityGateInput } from "../../lib/normalPriorityGateAdapter";
import { shouldAllowNormalPriority } from "../../lib/normalPriorityGate";
import { resolvePrioritySource } from "../../lib/prioritySource";
import { deriveCollaborationOwnerSourceLocator } from "../../lib/collaborationOwnerSourceLocator";

const source = readFileSync("src/components/workspace/WorkspaceShell.tsx", "utf8");
const tree = ts.createSourceFile("WorkspaceShell.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const declarations = new Map<string, string>();
function visit(node: ts.Node) {
  if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) declarations.set(node.name.text, node.initializer.getText(tree));
  if (ts.isFunctionDeclaration(node) && node.name) declarations.set(node.name.text, node.getText(tree).replace(/^export /, ""));
  ts.forEachChild(node, visit);
}
visit(tree);
function evaluate(name: string, context: Record<string, any>) {
  assert.ok(declarations.has(name), name);
  const output = ts.transpileModule(`globalThis.result = (${declarations.get(name)});`, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React },
  }).outputText;
  return vm.runInNewContext(output, context);
}
const workspaceId = `wsp_${"W".repeat(22)}`;
const summary = { collaborationId: "A".repeat(22), workspaceId, mailboxId: "mailbox-a",
  sourceRef: { provider: "google", providerMessageId: "exact-google" }, state: "needs_review", updatedAt: 1_800_000_000_000, viewerAccess: "owner" };
const mail = { id: "old-mail", providerMessageId: "exact-google", providerThreadId: "gmail-thread",
  threadId: "gmail-thread", threadIdentityAuthority: "gmail", serverMailboxId: "mailbox-a",
  threadIdentityContext: { mailboxId: "mailbox-a", provider: "google", folder: "INBOX" },
  subject: "Identical subject is not authority", from: "sender@example.test", createdAt: "2000-01-01T00:00:00Z",
  timestamp: "2000-01-01T00:00:00Z", unread: false, priorityScore: "medium", final_visibility: "show_normal" };
const mailbox = { id: "mailbox-a", title: "Mailbox", provider: "google", connected: true, connectionStatus: "connected" };

function priority({ state = "needs_review", messages = [mail], removed = "", cleared = "", waiting = false,
  snapshots = [summary], workflowReady = true }: any = {}) {
  const context: Record<string, any> = {
    ...composition, ...summaryStore, dedupeLatestCanonicalConversationEntries, resolveCanonicalConversationIdentity,
    resolveMessageNoisePolicy, buildNormalPriorityGateInput, shouldAllowNormalPriority,
    workspaceDataMode: "live", hasAuthenticatedMemberAuthority: true, authenticatedUser: { workspaceId },
    collaborationSummaries: { index: summaryStore.indexCollaborationSummaries(workspaceId, snapshots.map((s: any) => ({ ...s, state }))) },
    collaborationManagedMailboxById: new Map([[mailbox.id, mailbox]]),
    orderedMailboxes: [mailbox], connectedOrderedMailboxes: [mailbox], mailboxStore: { [mailbox.id]: { Inbox: messages } },
    effectiveFocusPreferencesByMailbox: {}, activeFocusPreferences: {}, manualPriorityOverrides: {}, manualLabelOverrides: {},
    resolveManualPriorityOverride: (_: any, m: any) => m.id === removed ? "removed" : undefined,
    isPriorityMessageCleared: (_: any, m: any) => m.id === cleared,
    resolvePriorityWorkflowReadAuthority: () => ({ status: "canonical", authority: {
      status: workflowReady ? "ready" : "loading", record: { waiting: waiting ? "waiting_on_other" : "absent", cleared: "active" },
    } }),
    isWorkspaceMessageSpamSuppressed: (m: any) => m.spam === true,
    createEmptyMailboxCollections: () => ({ Inbox: [] }),
    // The actual candidate builder must add active source mail even if an early
    // focus/visibility gate has put it in LOW/Filtered.
    getMailboxReadyInboxMessagesForWorkspaceMailbox: (c: any) => c.Inbox.filter((m: any) => !m.filtered),
    isPromoMailboxContext: () => false,
    createNormalPriorityMessageKey: (id: string, m: any) => `${id}:${m.id}`,
    waitingOnOtherRepresentativeEntries: [], returnedReplyRepresentativeEntries: [],
    activeWorkspaceEmail: "owner@example.test", reviewController: { getReviewBySourceId: () => null },
    useMemo: (fn: () => any) => fn(), resolveManualLabelOverrideFromStore: () => null,
    isPrioritySemanticDeterministicStoreCommitted: false, prioritySemanticCurrentLookupTriggers: [],
    prioritySemanticObservations: {}, shouldSuppressAutomaticOpenLoopPriority: () => false,
    isPriorityPageVisiblePriorityMessage: (m: any) => m.final_visibility === "show_priority",
    resolveMailDateMs: (m: any) => Date.parse(m.createdAt), PRIORITY_QUEUE_RECENT_WINDOW_MS: 14 * 86400000,
  };
  context.isPriorityQueueEligibleMessage = evaluate("isPriorityQueueEligibleMessage", context);
  for (const name of ["getActiveCollaborationSummary", "collaborationPriorityLatestByConversation", "isCollaborationPriorityEntrySuppressed",
    "hasActiveCollaborationPriority", "normalPriorityGateCandidateEntries"]) context[name] = evaluate(name, context);
  context.priorityRuntimeSignalsForCandidates = Object.fromEntries(context.normalPriorityGateCandidateEntries.map(({ mailboxId, message }: any) => {
    const active = context.hasActiveCollaborationPriority(mailboxId, message);
    return [`${mailboxId}:${message.id}`, { prioritySource: composition.composeCollaborationPrioritySource(
      resolvePrioritySource({ message: { ...message, isShared: false, sharedContext: undefined }, hasWaitingOnOtherEvidence: waiting }), active),
    }];
  }));
  context.strictNormalPriorityAllowedMessageKeys = evaluate("strictNormalPriorityAllowedMessageKeys", context);
  context.broadLivePriorityInboxEntries = evaluate("broadLivePriorityInboxEntries", context);
  context.resolveExactPrioritySemanticNewInboundObservationForMessage = () => null;
  context.meetsPrioritySemanticNewInboundPromotionThreshold = () => false;
  context.prioritySemanticNewInboundHydratedObservations = {};
  context.mergePrioritySemanticNewInboundPromotionsIntoCanonicalPriorityEntries = evaluate("mergePrioritySemanticNewInboundPromotionsIntoCanonicalPriorityEntries", context);
  return { entries: evaluate("livePriorityInboxEntries", context), context };
}

async function run() {
  for (const state of ["needs_review", "needs_action", "note_only"]) {
    const { entries, context } = priority({ state });
    assert.equal(entries.length, 1, `${state}: old/read/Normal promotes before early gates`);
    assert.equal(entries[0].message.id, mail.id);
    assert.equal(context.priorityRuntimeSignalsForCandidates[`mailbox-a:${mail.id}`].prioritySource.source, "collaboration");
  }
  assert.equal(priority({ messages: [{ ...mail, filtered: true, priorityScore: "low" }] }).entries.length, 1);
  assert.equal(priority({ state: "resolved" }).entries.length, 0);
  assert.equal(priority({ state: "unknown" }).entries.length, 0);
  assert.equal(priority({ snapshots: [] }).entries.length, 0);
  assert.equal(priority({ state: "resolved", messages: [{ ...mail, isShared: true, sharedContext: {}, collaboration: { state: "needs_review" } }] }).entries.length, 0, "old local snapshots cannot restore resolved work");
  const waitingMail = { ...mail, createdAt: "2000-01-01", final_visibility: "show_normal" };
  assert.equal(priority({ state: "resolved", waiting: true, messages: [waitingMail] }).entries.length, 1, "waiting survives resolve");
  assert.equal(priority({ waiting: true }).context.priorityRuntimeSignalsForCandidates[`mailbox-a:${mail.id}`].prioritySource.source, "waiting_on_other");
  assert.equal(priority({ removed: mail.id }).entries.length, 0);
  assert.equal(priority({ cleared: mail.id }).entries.length, 0);
  assert.equal(priority({ workflowReady: false }).entries.length, 0, "wait for canonical suppression authority");
  for (const noiseDisposition of ["strong_spam", "unsolicited_low_value"]) {
    assert.equal(priority({ messages: [{ ...mail, noiseDisposition, noiseConfidence: "high", noiseReasons: [] }] }).entries.length, 0, noiseDisposition);
  }
  assert.equal(priority({ messages: [{ ...mail, spam: true }] }).entries.length, 0);
  assert.equal(priority({ snapshots: [summary, summary] }).entries.length, 1);
  assert.equal(priority({ messages: [{ ...mail, providerMessageId: "wrong", id: "different" }] }).entries.length, 0);
  const latest = { ...mail, id: "latest", providerMessageId: "different", createdAt: "2026-09-01", timestamp: "2026-09-01" };
  assert.equal(priority({ messages: [mail, latest] }).entries[0].message.id, mail.id, "old source remains exact, not replaced by latest thread mail");
  assert.equal(priority({ messages: [mail, latest], removed: latest.id }).entries.length, 0, "latest remove cannot backfill an older collaboration source");
  assert.equal(priority({ messages: [mail, { ...latest, filtered: true }], removed: latest.id }).entries.length, 0);
  assert.equal(priority({ messages: [mail, latest], cleared: latest.id }).entries.length, 0);

  const binding = { scopeKey: "scope-a", selectionKey: "mailbox-a:old-mail", summary };
  let activeBinding: any = binding;
  const opened: any[] = [];
  const currentRef = { current: activeBinding };
  const render = evaluate("renderMessageCollaboration", {
    React, workspaceDataMode: "live", getCollaborationOpenBinding: () => activeBinding,
    isFullMessageOpen: false, collaborationCompactPrimaryActionButtonClass: "existing-style",
    isCurrentCollaborationOpenBinding: summaryStore.isCurrentCollaborationOpenBinding,
    currentCollaborationOpenBindingRef: currentRef,
    openCollaborationOverlay: (...args: any[]) => opened.push(args),
  });
  const cue = render(mail);
  const html = renderToStaticMarkup(cue);
  assert.equal((html.match(/Open Collaboration/g) ?? []).length, 1);
  cue.props.onClick();
  assert.equal(opened[0][0], mail.id);
  assert.equal(opened[0][1].sourceMailboxId, summary.mailboxId);
  assert.equal(opened[0][1].expectedBinding.summary.collaborationId, summary.collaborationId);
  assert.equal(opened[0][1].loadOwnerProjection, true, "existing owner modal path reused");
  currentRef.current = { ...binding, selectionKey: "different-selected-mail" };
  cue.props.onClick(); assert.equal(opened.length, 1, "stale selection cannot open");
  currentRef.current = null; cue.props.onClick(); assert.equal(opened.length, 1, "removed binding cannot open");
  activeBinding = null; assert.equal(render(mail), null, "resolved/no binding hides active cue");
  activeBinding = binding; assert.equal(render(mail, "full"), null, "no duplicate hidden/full cue");
  for (const outcome of ["exact", "wrong-id", "binding-removed"]) {
    const projection: any[] = [];
    const requestRef: any = { current: null };
    const bindingRef: any = { current: binding };
    let reads = 0;
    const read = evaluate("beginCollaborationOwnerRead", {
      managedInboxes: [mailbox], workspaceDataMode: "live", hasAuthenticatedMemberAuthority: true,
      deriveCollaborationOwnerSourceLocator, fenceCollaborationOwnerProjection: () => {},
      collaborationOwnerProjectionRequestRef: requestRef, collaborationOwnerProjectionGenerationRef: { current: 0 },
      setCollaborationOwnerCreateState: () => {}, setCollaborationOwnerProjection: (value: any) => projection.push(value),
      isCurrentCollaborationOpenBinding: summaryStore.isCurrentCollaborationOpenBinding,
      currentCollaborationOpenBindingRef: bindingRef, isCollaborationOwnerReadFailureRetryable: () => false,
      lookupCollaborationForOwner: async (locator: any) => {
        assert.equal(locator.mailboxId, mailbox.id);
        assert.equal(locator.sourceRef.providerMessageId, mail.providerMessageId);
        if (outcome === "binding-removed") bindingRef.current = null;
        return { status: "success", collaborationId: outcome === "wrong-id" ? "B".repeat(22) : summary.collaborationId };
      },
      readCollaborationForOwner: async (id: string) => { reads++; assert.equal(id, summary.collaborationId); return { status: "success", collaboration: summary }; },
      onCanonicalCollaborationMutation: () => {},
      setPendingEndCollaborationMessageId: () => {}, setIsCollaborationParticipantPickerOpen: () => {},
      setIsCollaborationInviteComposerOpen: () => {}, setIsCollaborationActionsMenuOpen: () => {},
    });
    read(mail.id, mailbox.id, mail, "Inbox", binding);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(reads, outcome === "exact" ? 1 : 0, "read only the exact current summary ID");
    assert.equal(projection.at(-1).status, outcome === "exact" ? "success" : "non_retryable_failure");
    assert.equal(requestRef.current.inFlight, false, "invalidated binding must not leave a stuck read");
  }

  for (const provider of ["google", "custom_imap"] as const) {
    for (const outcome of ["repaired", "closed", "selection-changed", "wrong-id", "wrong-mailbox", "read-failed", "summary-absent"]) {
      const exactMailbox = { ...mailbox, provider };
      const exactMail = provider === "google" ? mail : {
        ...mail, providerMessageId: undefined, providerFolder: "INBOX", uidValidity: "9001", imapUid: "42",
        threadIdentityContext: { mailboxId: mailbox.id, provider: "custom_imap", folder: "INBOX", uidValidity: "9001" },
      };
      const exactSummary = { ...summary, sourceRef: provider === "google" ? summary.sourceRef : {
        provider: "custom_imap", folder: "INBOX", uidValidity: "9001", imapUid: "42",
      } };
      const unrelated = { ...summary, collaborationId: "B".repeat(22), sourceRef: { provider: "google", providerMessageId: "unrelated" } };
      let summaries: any[] = [unrelated];
      let refreshes = 0;
      const store = summaryStore.createCollaborationSummaryStore(workspaceId, async () => {
        refreshes++;
        return { status: "success", page: { v: 1, workspaceId, summaries, nextCursor: null } };
      });
      await store.refresh();
      const locatorInput = { workspaceDataMode: "live", hasAuthenticatedMemberAuthority: true,
        managedMailbox: exactMailbox, sourceMailboxId: mailbox.id, trustedFolder: "INBOX", message: exactMail };
      assert.equal(summaryStore.lookupActiveCollaborationSummary(store.getSnapshot(), workspaceId, locatorInput), null);
      const requestRef: any = { current: null };
      const projection: any[] = [];
      const publications: Promise<void>[] = [];
      const read = evaluate("beginCollaborationOwnerRead", {
        managedInboxes: [exactMailbox], workspaceDataMode: "live", hasAuthenticatedMemberAuthority: true,
        deriveCollaborationOwnerSourceLocator, fenceCollaborationOwnerProjection: () => {},
        collaborationOwnerProjectionRequestRef: requestRef, collaborationOwnerProjectionGenerationRef: { current: 0 },
        setCollaborationOwnerCreateState: () => {}, setCollaborationOwnerProjection: (value: any) => projection.push(value),
        isCollaborationOwnerReadFailureRetryable: () => false,
        lookupCollaborationForOwner: async () => ({ status: "success", collaborationId: summary.collaborationId }),
        readCollaborationForOwner: async () => {
          if (outcome === "closed") requestRef.current = null;
          if (outcome === "selection-changed") requestRef.current = { identityKey: "another-selection", requestId: 2 };
          if (outcome === "read-failed") return { status: "service_unavailable" };
          if (outcome !== "summary-absent") summaries = [exactSummary, unrelated];
          return { status: "success", collaboration: {
            collaborationId: outcome === "wrong-id" ? unrelated.collaborationId : summary.collaborationId,
            mailboxId: outcome === "wrong-mailbox" ? "another-mailbox" : mailbox.id,
            state: summary.state, updatedAt: summary.updatedAt, viewerAccess: "owner",
          } };
        },
        onCanonicalCollaborationMutation: (dto: any) => publications.push(store.acceptMutation(dto)),
        setPendingEndCollaborationMessageId: () => {}, setIsCollaborationParticipantPickerOpen: () => {},
        setIsCollaborationInviteComposerOpen: () => {}, setIsCollaborationActionsMenuOpen: () => {},
      });
      read(exactMail.id, mailbox.id, exactMail, "Inbox");
      await new Promise(resolve => setImmediate(resolve));
      await Promise.all(publications);
      const validRead = !["wrong-id", "wrong-mailbox", "read-failed"].includes(outcome);
      assert.equal(refreshes, validRead ? 2 : 1, `${provider}/${outcome}: exact successful access refreshes once`);
      const active = summaryStore.lookupActiveCollaborationSummary(store.getSnapshot(), workspaceId, locatorInput);
      assert.equal(active?.collaborationId ?? null, validRead && outcome !== "summary-absent" ? summary.collaborationId : null,
        `${provider}/${outcome}: only the authoritative list can promote the exact source`);
      assert.ok([...store.getSnapshot().values()].some(value => value.collaborationId === unrelated.collaborationId));
      if (["closed", "selection-changed"].includes(outcome)) {
        assert.deepEqual(projection.map(value => value.status), ["loading"], "access publication cannot reopen a stale modal");
      }
    }
  }

  for (const state of ["needs_review", "resolved"]) {
    for (const success of [true, false]) {
      const request = { identityKey: "exact-context" };
      const accepted: any[] = [];
      const published: any[] = [];
      const statuses: string[] = [];
      const dto = { ...summary, state, viewerAccess: "owner" };
      let resolves = 0, reopens = 0;
      const result = success ? { status: "success", collaboration: { ...dto, state: state === "resolved" ? "note_only" : "resolved" } } : { status: "network_failure" };
      const transition = evaluate("transitionCanonicalCollaboration", {
        activeCollaborationOwnerProjection: dto, collaborationOwnerProjectionRequestRef: { current: request },
        activeCollaborationOwnerContextKey: request.identityKey, collaborationLifecycleRequestRef: { current: null },
        setCollaborationLifecycleStatus: (s: string) => statuses.push(s),
        resolveCollaborationForOwner: async () => { resolves++; return result; },
        reopenCollaborationForOwner: async () => { reopens++; return result; },
        applyCanonicalCollaborationAccessResult: (...args: any[]) => accepted.push(args),
        onCanonicalCollaborationMutation: (dto: any) => published.push(dto),
      });
      await transition();
      assert.equal(resolves, state === "resolved" ? 0 : 1);
      assert.equal(reopens, state === "resolved" ? 1 : 0);
      assert.equal(accepted.length, success ? 1 : 0, "failed lifecycle cannot change summary authority");
      assert.equal(published.length, success ? 1 : 0, "one summary publication per successful lifecycle");
      assert.equal(statuses.at(-1), success ? "idle" : "failure");
    }
  }
  {
    const requestRef: any = { current: { identityKey: "closed-context" } };
    const published: any[] = [];
    const closedTransition = evaluate("transitionCanonicalCollaboration", {
      activeCollaborationOwnerProjection: { ...summary, viewerAccess: "owner" },
      collaborationOwnerProjectionRequestRef: requestRef, activeCollaborationOwnerContextKey: "closed-context",
      collaborationLifecycleRequestRef: { current: null }, setCollaborationLifecycleStatus: () => {},
      resolveCollaborationForOwner: async () => { requestRef.current = null; return { status: "success", collaboration: { ...summary, state: "resolved" } }; },
      onCanonicalCollaborationMutation: (dto: any) => published.push(dto),
      applyCanonicalCollaborationAccessResult: () => assert.fail("closed modal must not receive stale projection"),
    });
    await closedTransition();
    assert.equal(published.length, 1, "confirmed resolve still updates summaries after closing");
  }
  const panelSource = readFileSync("src/components/collaboration/CollaborationAccessPanel.tsx", "utf8");
  const panelTree = ts.createSourceFile("Panel.tsx", panelSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  function readStart(node: ts.Node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(panelTree) === "submitStart") declarations.set("submitStart", node.initializer!.getText(panelTree));
    ts.forEachChild(node, readStart);
  }
  readStart(panelTree);
  for (const participantType of ["team", "external"]) for (const created of [true, false]) for (const success of [true, false]) for (const open of [true, false]) {
    const published: any[] = [], modal: any[] = [];
    const result = success ? { status: "success", created, collaboration: summary } : { status: "network_failure" };
    const start = evaluate("submitStart", {
      locator: { mailboxId: mailbox.id }, participantType, mutationInFlight: false, startExternalEmail: "",
      selectedTeamMemberId: "member", initialState: "needs_review", normalizeEmail: (v: string) => v,
      clearSecureLink: () => {}, beginRequest: () => 1, setStartMutation: () => {},
      createCollaborationForOwner: async () => result, createCollaborationWithGuestForOwner: async () => result,
      isCurrentRequest: () => open, getCollaborationAccessFailureMessage: () => "failure",
      onCanonicalCreation: (dto: any) => published.push(dto), onCanonicalCollaboration: (...args: any[]) => modal.push(args),
      contextKey: "context", applyNewInvitation: () => {},
    });
    start({ preventDefault: () => {} });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(published.length, success ? 1 : 0, "one creation publication, including after close");
    assert.equal(modal.length, success && open ? 1 : 0);
    if (modal.length) assert.equal(modal[0][2], true, "modal callback cannot duplicate the summary refresh");
  }
  const create = evaluate("applyCanonicalCollaborationAccessResult", {
    collaborationOwnerProjectionRequestRef: { current: { identityKey: "create-context", messageId: mail.id, sourceMailboxId: mailbox.id, locator: { mailboxId: mailbox.id } } },
    activeCollaborationMessageId: mail.id, activeCollaborationSourceMailboxId: mailbox.id, activeCollaborationOwnerProjection: null,
    collaborationOwnerProjectionGenerationRef: { current: 1 }, setCollaborationOwnerCreateState: () => {}, setCollaborationOwnerProjection: () => {},
    onCanonicalCollaborationMutation: (dto: any) => opened.push(dto),
  });
  create(summary, "wrong-context"); assert.equal(opened.length, 1);
  create(summary, "create-context"); assert.equal(opened.length, 2, "successful create reaches summary projection");
  assert.match(source, /collaborationOwnerProjectionRequestRef\.current = null;\s*collaborationLifecycleRequestRef\.current = null;/);
  assert.match(source, /key=\{`\$\{activeMailbox.id\}-\$\{mailboxResetToken\}-\$\{collaborationSummaries.scopeKey\}`\}/);
  assert.match(declarations.get("beginCollaborationOwnerRead")!, /lookupResult.collaborationId !== expectedBinding.summary.collaborationId/);
  assert.equal((declarations.get("renderMessageCollaboration")!.match(/Open Collaboration/g) ?? []).length, 1);
  console.log("PASS Workspace Collaboration: actual Priority gates, suppression, exact older mail, reason composition, CTA rendering/click, lifecycle and create integration");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
