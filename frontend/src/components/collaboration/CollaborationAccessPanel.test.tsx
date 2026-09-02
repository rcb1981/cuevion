// @ts-nocheck

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import type { CollaborationOwnerReadDto } from "../../lib/collaborationOwnerReadApi";
import type { CollaborationOwnerSourceLocator } from "../../lib/collaborationOwnerSourceLocator";
import {
  CollaborationAccessPanel,
  getCollaborationAccessFailureMessage,
  getEligibleCollaborationTeamMembers,
  getExternalGuestStatusLabel,
  type CollaborationTeamRosterMember,
} from "./CollaborationAccessPanel";

const OWNER_USER_ID = `usr_${"O".repeat(21)}A`;
const ELIGIBLE_USER_ID = `usr_${"E".repeat(21)}Q`;
const EXISTING_USER_ID = `usr_${"P".repeat(21)}g`;
const COLLABORATION_ID = "C".repeat(22);
const INVITE_IDS = ["D", "F", "G", "H", "I"].map((value) => value.repeat(22));
const locator = {
  mailboxId: "mailbox-google",
  sourceRef: { providerMessageId: "provider-message" },
} as CollaborationOwnerSourceLocator;

function baseCollaboration(
  viewerAccess: "owner" | "participant",
): CollaborationOwnerReadDto {
  const base = {
    collaborationId: COLLABORATION_ID,
    mailboxId: "mailbox-google",
    state: "needs_review" as const,
    createdAt: 1_800_000_000_000,
    updatedAt: 1_800_000_001_000,
    source: {
      subject: "Review this",
      senderDisplay: "Sender",
      fromDisplay: "sender@example.test",
      timestamp: "2027-01-15T08:00:00.000Z",
      bodyText: "Source body",
    },
    messages: [],
    participants: [
      { userId: OWNER_USER_ID, displayName: "Workspace Owner", access: "owner" as const },
      { userId: EXISTING_USER_ID, displayName: "Existing Member", access: "participant" as const },
    ],
  };
  if (viewerAccess === "participant") {
    return { ...base, viewerAccess };
  }
  return {
    ...base,
    viewerAccess,
    externalGuests: [
      {
        inviteId: INVITE_IDS[0],
        status: "pending",
        expiresAt: 1_900_000_000,
        invitedEmail: "pending@example.test",
      },
      {
        inviteId: INVITE_IDS[1],
        status: "active",
        expiresAt: 1_900_000_000,
        invitedEmail: "active@example.test",
        displayName: "Active Guest",
      },
      {
        inviteId: INVITE_IDS[2],
        status: "logged_out",
        expiresAt: 1_900_000_000,
      },
      {
        inviteId: INVITE_IDS[3],
        status: "revoked",
        expiresAt: 1_900_000_000,
      },
      {
        inviteId: INVITE_IDS[4],
        status: "expired",
        expiresAt: 1_900_000_000,
      },
    ],
  };
}

const roster: CollaborationTeamRosterMember[] = [
  {
    memberUserId: ELIGIBLE_USER_ID,
    displayName: "Eligible Member",
    email: "eligible@example.test",
    status: "active",
  },
  {
    memberUserId: OWNER_USER_ID,
    displayName: "Owner Self",
    email: "owner@example.test",
    status: "active",
  },
  {
    memberUserId: null,
    displayName: "Legacy Member",
    email: "legacy@example.test",
    status: "active",
  },
  {
    memberUserId: EXISTING_USER_ID,
    displayName: "Already Added",
    email: "participant@example.test",
    status: "active",
  },
  {
    memberUserId: "member@example.test",
    displayName: "Malformed Member",
    email: "malformed@example.test",
    status: "active",
  },
];

function renderPanel(
  overrides: Partial<Parameters<typeof CollaborationAccessPanel>[0]>,
) {
  return renderToStaticMarkup(
    <CollaborationAccessPanel
      mode="start"
      contextKey="message-context"
      viewerIdentityKey="viewer-context"
      locator={locator}
      collaboration={null}
      teamMembers={roster}
      currentMemberUserId={OWNER_USER_ID}
      currentUserEmail="owner@example.test"
      onCanonicalCollaboration={() => undefined}
      onRequestOverlayClose={() => undefined}
      onSecureLinkVisibilityChange={() => undefined}
      {...overrides}
    />,
  );
}

try {
  const eligible = getEligibleCollaborationTeamMembers({
    teamMembers: roster,
    currentMemberUserId: OWNER_USER_ID,
    currentUserEmail: "owner@example.test",
    collaboration: baseCollaboration("owner"),
  });
  assert.deepEqual(
    eligible.map((member) => member.memberUserId),
    [ELIGIBLE_USER_ID],
    "only an active, canonical, non-self, not-yet-participating Team member is eligible",
  );

  const startMarkup = renderPanel({});
  assert.match(startMarkup, /Start collaboration/);
  assert.match(startMarkup, /Team member/);
  assert.match(startMarkup, /External guest/);
  assert.match(startMarkup, /shared messages and internal notes/);
  assert.match(startMarkup, /only see shared collaboration messages/);
  assert.match(startMarkup, /Needs input/);
  assert.match(startMarkup, /Needs action/);
  assert.match(startMarkup, /Notes only/);
  assert.doesNotMatch(startMarkup, /Owner Self/);
  assert.doesNotMatch(startMarkup, /Legacy Member/);
  assert.doesNotMatch(startMarkup, /Already Added/);
  assert.doesNotMatch(startMarkup, /Malformed Member/);

  const emptyTeamMarkup = renderPanel({ teamMembers: [] });
  assert.match(emptyTeamMarkup, /No other eligible Team members yet/);
  assert.match(emptyTeamMarkup, /Add a Team member in Team Settings first/);
  assert.match(emptyTeamMarkup, /External guest access remains available/);
  assert.match(emptyTeamMarkup, /value="external"/);

  const ownerMarkup = renderPanel({
    mode: "access",
    collaboration: baseCollaboration("owner"),
  });
  assert.match(ownerMarkup, />Access</);
  assert.match(ownerMarkup, /Team members/);
  assert.match(ownerMarkup, /Workspace Owner/);
  assert.match(ownerMarkup, />Owner</);
  assert.match(ownerMarkup, /Existing Member/);
  assert.match(ownerMarkup, />Team member</);
  assert.match(ownerMarkup, /External guests/);
  for (const label of ["Pending", "Active", "Left collaboration", "Revoked", "Expired"]) {
    assert.match(ownerMarkup, new RegExp(label));
  }
  assert.match(ownerMarkup, /Active Guest/);
  assert.match(ownerMarkup, /active@example\.test/);
  assert.match(ownerMarkup, /Secure-link guest/);
  assert.equal((ownerMarkup.match(/Revoke access/g) ?? []).length, 2);
  assert.doesNotMatch(ownerMarkup, new RegExp(OWNER_USER_ID));
  assert.doesNotMatch(ownerMarkup, new RegExp(INVITE_IDS[0]));

  const participantMarkup = renderPanel({
    mode: "access",
    collaboration: baseCollaboration("participant"),
  });
  assert.match(participantMarkup, /Workspace Owner/);
  assert.match(participantMarkup, /Existing Member/);
  assert.doesNotMatch(participantMarkup, /pending@example\.test/);
  assert.doesNotMatch(participantMarkup, /Invite external guest/);
  assert.doesNotMatch(participantMarkup, /Add Team member/);
  assert.doesNotMatch(participantMarkup, /Revoke access/);

  assert.equal(getExternalGuestStatusLabel("pending"), "Pending");
  assert.equal(getExternalGuestStatusLabel("active"), "Active");
  assert.equal(getExternalGuestStatusLabel("logged_out"), "Left collaboration");
  assert.equal(getExternalGuestStatusLabel("revoked"), "Revoked");
  assert.equal(getExternalGuestStatusLabel("expired"), "Expired");

  for (const status of [
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "rate_limited",
    "service_unavailable",
    "network_failure",
    "invalid_response",
  ]) {
    const message = getCollaborationAccessFailureMessage(status);
    assert.equal(message.length > 0, true);
    assert.doesNotMatch(message, /\b(?:401|403|404|409|429|500|503)\b/);
  }
  assert.equal(
    getCollaborationAccessFailureMessage("not_found", "start"),
    "Collaboration changes are temporarily unavailable.",
  );
  assert.equal(
    getCollaborationAccessFailureMessage("service_unavailable", "start"),
    "Collaboration changes are temporarily unavailable.",
  );
  assert.equal(
    getCollaborationAccessFailureMessage("not_found", "manage"),
    "This Collaboration is no longer available.",
  );
  assert.equal(
    getCollaborationAccessFailureMessage("invalid_collaboration_id", "manage"),
    "This Collaboration is no longer available.",
  );
  assert.equal(
    getCollaborationAccessFailureMessage("unauthorized", "start"),
    "Sign in again to change Collaboration access.",
  );
  assert.equal(
    getCollaborationAccessFailureMessage("forbidden", "manage"),
    "You don’t have permission to change Collaboration access.",
  );
  assert.equal(
    getCollaborationAccessFailureMessage("conflict", "manage"),
    "Collaboration access changed. Review the current access and try again.",
  );
  assert.equal(
    getCollaborationAccessFailureMessage("rate_limited", "manage"),
    "Too many Collaboration changes were requested. Try again shortly.",
  );
  assert.equal(
    getCollaborationAccessFailureMessage("network_failure", "start"),
    "The change may not have completed. Check your connection and try again explicitly.",
  );

  const source = readFileSync(
    resolve(process.cwd(), "src/components/collaboration/CollaborationAccessPanel.tsx"),
    "utf8",
  );
  const workspaceSource = readFileSync(
    resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
    "utf8",
  );
  const legacyCreateStart = workspaceSource.indexOf(
    "const createMessageCollaboration = () =>",
  );
  const legacyCreateEnd = workspaceSource.indexOf(
    "const submitCollaborationOwnerSharedMessage = () =>",
    legacyCreateStart,
  );
  const legacyCreateRegion = workspaceSource.slice(legacyCreateStart, legacyCreateEnd);
  const trustedLocatorFence = legacyCreateRegion.indexOf("if (currentLocator)");
  const legacyDraftWrite = legacyCreateRegion.indexOf("setDraftCollaborationByMessageId");

  assert.equal((source.match(/createCollaborationForOwner\(/g) ?? []).length, 1);
  assert.equal((source.match(/createCollaborationWithGuestForOwner\(/g) ?? []).length, 1);
  assert.equal((source.match(/addParticipantToCollaborationForOwner\(/g) ?? []).length, 1);
  assert.equal((source.match(/issueGuestInvitationForOwner\(/g) ?? []).length, 1);
  assert.equal((source.match(/revokeGuestInvitationForOwner\(/g) ?? []).length, 1);
  assert.match(source, /Email \(optional\)/);
  assert.equal(
    (source.match(/Email is optional and only helps identify the guest\. Access is controlled by the secure link, which you’ll share yourself\./g) ?? []).length,
    2,
  );
  assert.match(source, /createCollaborationForOwner\([\s\S]*?selectedTeamMemberId/);
  assert.match(source, /createCollaborationWithGuestForOwner\([\s\S]*?normalizedExternalEmail \|\| undefined/);
  assert.match(source, /createCollaborationForOwner\([\s\S]*?getCollaborationAccessFailureMessage\(result\.status, "start"\)/);
  assert.match(source, /createCollaborationWithGuestForOwner\([\s\S]*?getCollaborationAccessFailureMessage\(result\.status, "start"\)/);
  assert.equal(
    (source.match(/getCollaborationAccessFailureMessage\(result\.status, "start"\)/g) ?? []).length,
    2,
  );
  assert.equal(
    (source.match(/getCollaborationAccessFailureMessage\(result\.status\)/g) ?? []).length,
    3,
    "existing Add Team, Invite External, and Revoke mutations keep management failure semantics",
  );
  assert.match(source, /issueGuestInvitationForOwner\([\s\S]*?normalizedInvitedEmail \|\| undefined/);
  assert.match(source, /revokeGuestInvitationForOwner\(collaborationId, inviteId\)/);
  assert.match(source, /onCanonicalCollaboration\(result\.collaboration, contextKey\)/);
  assert.match(source, /buildCollaborationGuestInviteLink\(token, window\.location\.origin\)/);
  assert.match(source, /setSecureLink\(\{ inviteId: invitation\.inviteId, url \}\)/);
  assert.match(source, /navigator\.clipboard\.writeText\(secureLink\.url\)/);
  assert.match(source, /Couldn’t copy automatically\. Select the link and copy it manually\./);
  assert.match(source, /Cuevion does not store the invitation link/);
  assert.match(source, /Cuevion doesn’t store this link\. Make sure you’ve copied it before closing/);
  assert.match(source, /invitationCreated \? result\.token : undefined/);
  assert.match(source, /An invitation for this guest already exists/);
  assert.match(source, /requestGenerationRef\.current \+= 1/);
  assert.match(source, /previous\.contextKey !== contextKey/);
  assert.match(source, /previous\.viewerIdentityKey !== viewerIdentityKey/);
  assert.match(source, /previous\.collaborationId !== collaborationId/);
  assert.match(source, /if \(!isCurrentRequest\(requestId\)\)/);
  assert.match(source, /externalGuests\.length >= 16/);
  assert.match(source, /collaboration\.participants\.length >= 16/);

  for (const forbidden of [
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "console.log",
    "console.error",
    "buildCollaborationInviteToken",
    "buildCollaborationInviteLink",
    "external_review",
    "createCollaborationThread",
    "updateMessageById",
    "setMailboxStore",
    "setDraftCollaborationByMessageId",
    "useDeferredValue",
    "startTransition",
    "setTimeout(",
    "setInterval(",
    "Regenerate",
    "Recover link",
    "Resend link",
  ]) {
    assert.equal(source.includes(forbidden), false, `owner v2 access must not use ${forbidden}`);
  }

  assert.ok(trustedLocatorFence >= 0 && trustedLocatorFence < legacyDraftWrite);
  assert.equal(legacyCreateRegion.includes("createCollaborationForOwner("), false);
  assert.equal(legacyCreateRegion.includes("createCollaborationWithGuestForOwner("), false);
  assert.match(legacyCreateRegion, /if \(currentLocator\) \{[\s\S]*?return;[\s\S]*?\}/);
  assert.match(workspaceSource, /role="dialog"[\s\S]*?aria-modal="true"[\s\S]*?aria-labelledby="collaboration-dialog-title"/);
  assert.match(workspaceSource, /collaborationOverlayOpenerRef\.current = document\.activeElement/);
  assert.match(workspaceSource, /collaborationOpener\?\.focus\(\)/);
  assert.match(workspaceSource, /isCollaborationSecureLinkVisible[\s\S]*?setIsCollaborationSecureLinkClosePending\(true\)/);
  assert.match(workspaceSource, /memberUserId: member\.memberUserId/);
} catch (error) {
  process.exitCode = 1;
  console.error("FAIL: Collaboration access management contract");
  console.error(error);
}
