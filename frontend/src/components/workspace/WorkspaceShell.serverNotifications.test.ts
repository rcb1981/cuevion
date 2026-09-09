import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { matchesExactMailboxMessageIdentity } from "../../lib/exactMailboxMessageApi";
import { notification, scope, exactResponse, collaboration } from "../../lib/notificationsTestFixtures.test";
const source = fs.readFileSync(path.join(__dirname, "WorkspaceShell.tsx"), "utf8");
const start = source.indexOf("  useLayoutEffect(() => {\n    const request = serverNotificationDisplay;");
assert.ok(start > 0);
const displayEffects = source.slice(start, source.indexOf("  // Mobile compose handoff:", start));
function displayFixture(options: { activity?: boolean; canonical?: boolean; hidden?: boolean } = {}) {
  const row = notification(1, options.activity === false ? { activityId: null, kind: "collaboration_started" } : {});
  const message = exactResponse().message, projected = collaboration(row), completions: boolean[] = [], frames: (() => void)[] = [];
  let current = true, layout: () => void = () => {}, effect: () => void = () => {};
  const activity = { getClientRects: () => [1], scrollIntoView: () => { calls.push("scroll"); }, focus: () => { context.document.activeElement = activity; calls.push("focus"); } };
  const projection = { contains: (node: unknown) => node === activity, isConnected: true, getClientRects: () => options.hidden ? [] : [1] };
  const calls: string[] = [];
  const context: any = {
    serverNotificationDisplay: { ticket: { notification: row, scope, generation: 1, isCurrent: () => current }, target: { message, folder: "Inbox" }, collaboration: options.canonical ? projected : null, complete: (value: boolean) => completions.push(value) },
    useLayoutEffect: (fn: () => void) => { layout = fn; }, useEffect: (fn: () => void) => { effect = fn; },
    mailbox: { id: "main" }, matchesExactMailboxMessageIdentity, fullMessageModalMessage: message,
    fullMessageModalDialogRef: { current: { dataset: { fullMessageModalMessageId: message.id }, getClientRects: () => options.hidden ? [] : [1] } },
    activeCollaborationMessage: message, activeCollaborationMessageId: message.id, activeCollaborationSourceMailboxId: "main", activeCollaborationOwnerProjection: projected,
    exactNotificationProjectionRef: { current: projection }, collaborationMessageRefs: { current: { [row.activityId!]: activity } },
    requestAnimationFrame: (fn: () => void) => { frames.push(fn); return frames.length; }, cancelAnimationFrame: () => {}, document: { activeElement: null },
    workspaceDataMode: "live", hasAuthenticatedMemberAuthority: true, managedInboxes: [{ id: "main" }], deriveCollaborationOwnerSourceLocator: () => ({ mailboxId: "main", sourceRef: {} }),
    collaborationOwnerProjectionGenerationRef: { current: 0 }, collaborationOwnerProjectionRequestRef: { current: null },
  };
  for (const name of ["setActiveSmartFolderId", "setActiveFolder", "setIsSharedView", "setSelectionState", "closeCollaborationOverlay", "resetFullMessageModalSize", "setIsFullMessageOpen", "openCollaborationOverlay", "setCollaborationOwnerProjection", "setHighlightedCollaborationMessageId", "setCollaborationHistoryExpanded"]) context[name] = (...args: any[]) => { calls.push(name); if (name === "setCollaborationOwnerProjection") context.activeCollaborationOwnerProjection = args[0].collaboration; };
  vm.runInNewContext(displayEffects, context);
  return { context, completions, calls, layout: () => layout(), effect: () => effect(), frame: () => { frames.shift()?.(); }, stale: () => { current = false; } };
}
for (const forbidden of ["buildVisibleNotificationItems", "readNotificationIds", "notificationReadStorageKey", "markNotificationSourceIdsRead", "notification:resolved:", "notification:reply:", "notification:mention:"]) test(`local authority removed: ${forbidden}`, () => assert.ok(!source.includes(forbidden)));
test("sidebar consumes shared server unread count", () => assert.ok(source.includes("notificationUnreadCount={notifications.state.unreadCount}")));
test("Dashboard and workbench receive exactly the same shared authority and handler", () => { assert.equal((source.match(/notifications=\{sharedNotificationProps\}/g) ?? []).length, 2); assert.ok(source.includes("const sharedNotificationProps: ServerNotificationsProps = { ...notifications, onOpen: handleOpenServerNotification }")); });
test("no stale localStorage read arrays can influence visible notifications", () => { for (const file of ["../../lib/workspaceNotificationStore.ts", "../../lib/useWorkspaceNotifications.ts", "ServerNotifications.tsx"]) assert.ok(!fs.readFileSync(path.join(__dirname, file), "utf8").includes("localStorage")); });
test("mail selection does not itself confirm display", () => { const f = displayFixture(); f.layout(); assert.ok(f.calls.includes("setSelectionState")); assert.deepEqual(f.completions, []); });
test("mail requires exact rendered modal before confirmation", () => { const f = displayFixture(); f.effect(); assert.deepEqual(f.completions, []); f.frame(); assert.deepEqual(f.completions, [true]); });
test("wrong rendered source identity does not confirm mail", () => { const f = displayFixture(); f.context.fullMessageModalMessage = { ...f.context.fullMessageModalMessage, providerMessageId: "wrong" }; f.effect(); f.frame(); assert.deepEqual(f.completions, []); });
test("hidden source modal does not confirm display", () => { const f = displayFixture({ hidden: true }); f.effect(); f.frame(); assert.deepEqual(f.completions, []); });
test("canonical loading does not confirm Collaboration display", () => { const f = displayFixture({ canonical: true }); f.context.activeCollaborationOwnerProjection = null; f.effect(); f.frame(); assert.deepEqual(f.completions, []); });
test("canonical activity is scrolled and focused before next frame confirmation", () => { const f = displayFixture({ canonical: true }); f.layout(); f.effect(); f.frame(); assert.deepEqual(f.completions, []); assert.deepEqual(f.calls.slice(-2), ["scroll", "focus"]); f.frame(); assert.deepEqual(f.completions, [true]); });
test("null activity needs exact canonical visible projection", () => { const f = displayFixture({ canonical: true, activity: false }); f.effect(); f.frame(); assert.deepEqual(f.completions, []); f.frame(); assert.deepEqual(f.completions, [true]); assert.ok(!f.calls.includes("focus")); });
test("missing rendered activity never confirms success", () => { const f = displayFixture({ canonical: true }); f.context.collaborationMessageRefs.current = {}; f.effect(); f.frame(); assert.deepEqual(f.completions, [false]); });
test("activity that cannot receive focus never confirms success", () => { const f = displayFixture({ canonical: true }); Object.values(f.context.collaborationMessageRefs.current).forEach((activity: any) => { activity.focus = () => {}; }); f.effect(); f.frame(); assert.deepEqual(f.completions, [false]); });
test("A then B fence between focus and painted frame prevents confirmation", () => { const f = displayFixture({ canonical: true }); f.effect(); f.frame(); f.stale(); f.frame(); assert.deepEqual(f.completions, []); });
test("removed projection cannot confirm after focus", () => { const f = displayFixture({ canonical: true }); f.effect(); f.frame(); f.context.exactNotificationProjectionRef.current.isConnected = false; f.frame(); assert.deepEqual(f.completions, []); });
test("wrong canonical Collaboration cannot confirm display", () => { const f = displayFixture({ canonical: true }); f.context.activeCollaborationOwnerProjection = { collaborationId: "wrong" }; f.effect(); f.frame(); assert.deepEqual(f.completions, []); });
test("exact notification open uses canonical ID projection without source lookup or full mailbox refresh", () => { assert.ok(!displayEffects.includes("lookupCollaborationForOwner")); assert.ok(!displayEffects.includes("onArchiveFolderOpen")); assert.ok(!displayEffects.includes("onTrashFolderOpen")); assert.ok(!displayEffects.includes("refresh")); assert.ok(displayEffects.includes("exactNotification: true")); });
