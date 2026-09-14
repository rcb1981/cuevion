import "sucrase/register/tsx.js";
import assert from "node:assert/strict";
import { createElement } from "react";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createTeamMemberController, isTeamMemberSession, TeamMemberShell } from "./TeamMemberShell";
import type { CuevionSessionUser } from "../../lib/authApi";
import type { CollaborationOwnerReadResult, CollaborationParticipantViewerReadDto } from "../../lib/collaborationOwnerReadApi";
import type { CollaborationOwnerAppendResult } from "../../lib/collaborationOwnerWriteApi";

const member: CuevionSessionUser = { userType: "member", workspaceRole: "member", userId: `usr_${"A".repeat(22)}`, workspaceId: `wsp_${"A".repeat(22)}`, name: "Member", email: "member@example.com" };
Object.assign(globalThis, { React });
const firstId = "A".repeat(22);
const secondId = "B".repeat(22);
function dto(id = firstId): CollaborationParticipantViewerReadDto {
  return { collaborationId: id, mailboxId: "mailbox-private-to-owner", viewerAccess: "participant", state: "needs_action", createdAt: 1, updatedAt: 1, source: { subject: "Shared conversation", senderDisplay: "Sender", fromDisplay: "sender@example.com", timestamp: "Today", bodyText: "Authorized source context" }, participants: [{ userId: member.userId!, displayName: "Member", access: "participant" }], messages: [] };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const success = (id = firstId): CollaborationOwnerReadResult => ({ status: "success", collaboration: dto(id) });
const appendSuccess: CollaborationOwnerAppendResult = { status: "success", updatedAt: 2, message: { id: "C".repeat(22), authorDisplayName: "Member", authorRole: "Cuevion user", authorUserId: member.userId!, text: "Reply", visibility: "internal", timestamp: 2 } };
const unusedPreparation = () => ({ status: "invalid_text" } as const);
const flush = () => new Promise<void>((resolve) => setImmediate(resolve));

async function run() {
  for (const workspaceRole of ["owner", "admin", undefined]) {
    const user = { ...member, workspaceRole } as CuevionSessionUser;
    assert.equal(isTeamMemberSession(user), false);
    let calls = 0;
    const blocked = createTeamMemberController(user, { read: async () => { calls++; return success(); }, prepare: unusedPreparation });
    blocked.setDiscovery([firstId]);
    assert.equal(calls, 0);
    const html = renderToStaticMarkup(createElement(TeamMemberShell, { authenticatedUser: user }));
    assert.match(html, /Collaboration access is unavailable/);
    assert.doesNotMatch(html, /Your collaborations|Reply visibility|Original email/);
  }
  assert.equal(isTeamMemberSession({ ...member, userId: "email@example.com" }), false);

  // Render the actual member shell with owner storage/network treated as fatal.
  const originalWindow = globalThis.window;
  const originalFetch = globalThis.fetch;
  Object.assign(globalThis, {
    window: {
      get localStorage() { throw new Error("Member shell accessed owner storage"); },
      get sessionStorage() { throw new Error("Member shell accessed durable storage"); },
    },
    fetch: () => { throw new Error("Member render started owner hydration"); },
  });
  try {
    const html = renderToStaticMarkup(createElement(TeamMemberShell, { authenticatedUser: member }));
    assert.match(html, /Collaboration/);
    assert.match(html, /Sign out/);
    assert.doesNotMatch(html, /Inboxes|Connect inbox|Connect mailbox|Invite member|Team management|Settings|Onboarding/);
    assert.doesNotMatch(html, /onboarding.complete|mailbox.credentials/i);
  } finally {
    globalThis.window = originalWindow;
    globalThis.fetch = originalFetch;
  }

  // Removed discovery and unmounted sessions cannot publish late source data.
  const oldRead = deferred<CollaborationOwnerReadResult>();
  const scoped = createTeamMemberController(member, { read: () => oldRead.promise, prepare: unusedPreparation });
  scoped.setDiscovery([firstId]);
  scoped.setDiscovery([]);
  oldRead.resolve(success());
  await flush();
  assert.equal(scoped.getSnapshot().entries.size, 0);
  const unmountedRead = deferred<CollaborationOwnerReadResult>();
  const unmounted = createTeamMemberController(member, { read: () => unmountedRead.promise, prepare: unusedPreparation });
  unmounted.setDiscovery([firstId]); unmounted.cancel(); unmountedRead.resolve(success());
  await flush();
  assert.equal(unmounted.getSnapshot().entries.size, 0);

  // A later read fences an older success, including a response after access loss.
  const earlier = deferred<CollaborationOwnerReadResult>();
  let reads = 0;
  const refreshed = createTeamMemberController(member, { read: async () => ++reads === 1 ? earlier.promise : { status: "forbidden" }, prepare: unusedPreparation });
  refreshed.setDiscovery([firstId]);
  await refreshed.refresh(firstId);
  earlier.resolve(success()); await flush();
  assert.equal(refreshed.getSnapshot().entries.get(firstId)?.status, "failure");

  for (const collaboration of [{ ...dto(), viewerAccess: "owner", externalGuests: [] }, { ...dto(), participants: [] }, dto(secondId)]) {
    const wrongProjection = createTeamMemberController(member, { read: async () => ({ status: "success", collaboration } as CollaborationOwnerReadResult), prepare: unusedPreparation });
    wrongProjection.setDiscovery([firstId]); await flush();
    assert.equal(wrongProjection.getSnapshot().entries.get(firstId)?.status, "failure");
    assert.equal(await wrongProjection.send("reply", "shared"), false);
  }

  const otherRead = deferred<CollaborationOwnerReadResult>();
  const expired = createTeamMemberController(member, { read: async (id) => id === firstId ? { status: "unauthorized" } : otherRead.promise, prepare: unusedPreparation });
  expired.setDiscovery([firstId, secondId]); await flush();
  otherRead.resolve(success(secondId)); await flush();
  assert.equal(expired.getSnapshot().authenticationLost, true);
  assert.equal(expired.getSnapshot().entries.size, 0);

  // The same prepared operation survives an uncertain result; clicks while
  // in flight do not create another operation or another execution.
  let preparations = 0;
  let executions = 0;
  const firstAppend = deferred<CollaborationOwnerAppendResult>();
  const replies = createTeamMemberController(member, {
    read: async (id) => success(id),
    prepare: () => { preparations++; return { status: "ready", operation: { execute: async () => ++executions === 1 ? firstAppend.promise : appendSuccess } }; },
  });
  replies.setDiscovery([firstId]); await flush();
  const sending = replies.send("Reply", "internal");
  assert.equal(await replies.send("Reply", "internal"), false);
  assert.equal(preparations, 1); assert.equal(executions, 1);
  firstAppend.resolve({ status: "network_failure" });
  assert.equal(await sending, false);
  replies.select(firstId);
  replies.setDiscovery([firstId, secondId]); await flush();
  assert.equal(await replies.send("Reply", "internal"), true);
  assert.equal(preparations, 1); assert.equal(executions, 2);
  const replyEntry = replies.getSnapshot().entries.get(firstId);
  assert.equal(replyEntry?.status === "ready" && replyEntry.collaboration.messages.length, 1);

  const lateAppend = deferred<CollaborationOwnerAppendResult>();
  const removed = createTeamMemberController(member, { read: async () => success(), prepare: () => ({ status: "ready", operation: { execute: () => lateAppend.promise } }) });
  removed.setDiscovery([firstId]); await flush();
  const pending = removed.send("Reply", "internal");
  removed.setDiscovery([]); lateAppend.resolve(appendSuccess);
  assert.equal(await pending, false); assert.equal(removed.getSnapshot().entries.size, 0);

  const deniedReply = createTeamMemberController(member, { read: async () => success(), prepare: () => ({ status: "ready", operation: { execute: async () => ({ status: "unauthorized" }) } }) });
  deniedReply.setDiscovery([firstId]); await flush();
  assert.equal(await deniedReply.send("Reply", "shared"), false);
  assert.equal(deniedReply.getSnapshot().authenticationLost, true);
  assert.equal(deniedReply.getSnapshot().entries.size, 0);
  const signingOut = createTeamMemberController(member, { read: async () => success(), prepare: unusedPreparation });
  signingOut.setDiscovery([firstId]); await flush();
  signingOut.endSession();
  signingOut.setDiscovery([firstId]); await flush();
  assert.equal(signingOut.getSnapshot().entries.size, 0);
  assert.equal(signingOut.getSnapshot().authenticationLost, true);
  console.log("TeamMemberShell: role isolation, participant reads, stale responses, lost authority, and idempotent replies passed");
}

void run().catch((error) => { console.error(error); process.exitCode = 1; });
