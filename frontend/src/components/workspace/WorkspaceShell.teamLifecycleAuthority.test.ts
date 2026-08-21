import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const source = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);

function sliceBetween(startMarker: string, endMarker: string, fromIndex = 0) {
  const start = source.indexOf(startMarker, fromIndex);
  assert.notEqual(start, -1, `missing start marker: ${startMarker}`);
  const end = source.indexOf(endMarker, start + startMarker.length);
  assert.notEqual(end, -1, `missing end marker: ${endMarker}`);
  return source.slice(start, end);
}

function assertInOrder(block: string, markers: string[]) {
  let cursor = 0;
  markers.forEach((marker) => {
    const index = block.indexOf(marker, cursor);
    assert.notEqual(index, -1, `missing ordered marker: ${marker}`);
    cursor = index + marker.length;
  });
}

function indicesOf(block: string, marker: string) {
  const indices: number[] = [];
  let cursor = 0;
  while (cursor < block.length) {
    const index = block.indexOf(marker, cursor);
    if (index === -1) {
      break;
    }
    indices.push(index);
    cursor = index + marker.length;
  }
  return indices;
}

const refreshBlock = sliceBetween(
  "const refreshProductionTeamAuthority = useCallback(",
  "const teamRosterPresentation = getTeamRosterPresentation(",
);
assert.match(
  refreshBlock,
  /Promise\.all\(\[\s*fetchTeamMembers\(\),\s*fetchPendingTeamInvites\(\),\s*\]\)/,
  "production authority refresh must read active members and pending invitations together",
);
assert.match(
  refreshBlock,
  /replaceWithAuthoritativeTeamMembers\([\s\S]*?membersResult\.members/,
  "active members must come only from the authoritative member read",
);
assert.match(
  refreshBlock,
  /setPendingTeamInvites\(pendingResult\.invitations\)/,
  "pending invitations must remain a separate authoritative projection",
);
assert.match(
  refreshBlock,
  /pendingResult\.status === "unauthorized"[\s\S]*?pendingResult\.status === "forbidden"[\s\S]*?onFreshTeamInviteUrlsChange\(\(\) => \(\{\}\)\)/,
  "loss of management authority must clear ephemeral invitation bearers",
);
assert.match(
  refreshBlock,
  /teamAuthorityRefreshCoordinator\.run\([\s\S]*?Promise\.all/,
  "production refreshes must use the behaviorally tested latest-refresh coordinator",
);
const memberReplacementStart = refreshBlock.indexOf(
  "replaceWithAuthoritativeTeamMembers(",
);
assert.notEqual(memberReplacementStart, -1);
const memberReplacementEnd = refreshBlock.indexOf(");", memberReplacementStart);
assert.notEqual(memberReplacementEnd, -1);
const memberReplacementCall = refreshBlock.slice(
  memberReplacementStart,
  memberReplacementEnd,
);
assert.doesNotMatch(
  memberReplacementCall,
  /pendingResult\.invitations/,
  "pending invitations must never be merged into the active-member roster",
);

assert.match(
  source,
  /const \[pendingTeamInvites, setPendingTeamInvites\] = useState<TeamInvite\[]>\(\[]\)/,
);
assert.match(source, />\s*Pending invitations\s*</);

const limitedIssueBlock = sliceBetween(
  "const issueAndSendInviteOnlyTeamInvite = async ({",
  "const issueSharedTeamInvite = async ({",
);
const limitedProductionStart = limitedIssueBlock.indexOf(
  "const issueResult = await issueTeamInvite({",
);
assert.notEqual(limitedProductionStart, -1);
const limitedDemoBlock = limitedIssueBlock.slice(0, limitedProductionStart);
const limitedProductionBlock = limitedIssueBlock.slice(limitedProductionStart);
assert.match(limitedDemoBlock, /if \(showDemoContent\) \{[\s\S]*?setTeamMembers\(/);
assert.match(limitedDemoBlock, /setTeamMembers\([\s\S]*?return true;/);
assertInOrder(limitedProductionBlock, [
  "const issueResult = await issueTeamInvite({",
  "const sendResult = await sendTeamInviteEmail({",
]);
assert.match(limitedProductionBlock, /await refreshProductionTeamAuthority\(\)/);
assert.doesNotMatch(
  limitedProductionBlock,
  /setTeamMembers\(/,
  "production Limited issue must wait for server readback instead of appending locally",
);
assertInOrder(limitedProductionBlock, [
  "const cancellationResult = await cancelTeamInvite({",
  "await refreshProductionTeamAuthority();",
  "The invitation was cancelled.",
]);
assert.match(
  limitedProductionBlock,
  /Invitation cancellation could not be confirmed\./,
  "delivery failure must not falsely claim cancellation when cancellation is unconfirmed",
);

const sharedIssueBlock = sliceBetween(
  "const issueSharedTeamInvite = async ({",
  "const syncInviteOnlyTeamInviteStatuses = async () => {",
);
const sharedProductionStart = sharedIssueBlock.indexOf(
  "const issueResult = await issueTeamInvite({",
);
assert.notEqual(sharedProductionStart, -1);
const sharedDemoBlock = sharedIssueBlock.slice(0, sharedProductionStart);
const sharedProductionBlock = sharedIssueBlock.slice(sharedProductionStart);
assert.match(sharedDemoBlock, /if \(showDemoContent\) \{[\s\S]*?setTeamMembers\(/);
assertInOrder(sharedProductionBlock, [
  "const issueResult = await issueTeamInvite({",
  "onFreshTeamInviteUrlsChange((current) => ({",
  "await refreshProductionTeamAuthority()",
]);
assert.doesNotMatch(
  sharedProductionBlock,
  /setTeamMembers\(/,
  "production Shared issue must not append a local pending member",
);
assert.match(
  sharedProductionBlock,
  /Invite created, but confirmed Team state is temporarily unavailable\.[\s\S]*?return true;/,
  "a confirmed one-time Shared link must survive a separate projection-read failure",
);

const pendingCancelBlock = sliceBetween(
  "const result = await cancelTeamInvite({",
  'setTeamFeedbackMessage("Invite cancelled");',
);
assertInOrder(pendingCancelBlock, [
  "const result = await cancelTeamInvite({",
  "await refreshProductionTeamAuthority()",
]);
assert.doesNotMatch(pendingCancelBlock, /setTeamMembers\(/);

const revokeBlock = sliceBetween(
  "const removeResult = await removeTeamMember({",
  "} finally {",
);
assertInOrder(revokeBlock, [
  "const removeResult = await removeTeamMember({",
  "await refreshProductionTeamAuthority()",
]);
assert.doesNotMatch(
  revokeBlock,
  /setTeamMembers\(/,
  "production revoke must not synthesize an Access removed row",
);
const revokeHandlerBlock = sliceBetween(
  'if (activeTeamConfirmation === "revoke" && activeTeamMemberIndex !== null) {',
  'if (\n                      activeTeamConfirmation === "remove-member"',
);
assert.match(
  revokeHandlerBlock,
  /if \(showDemoContent\) \{[\s\S]*?setTeamMembers\([\s\S]*?\} else \{[\s\S]*?await removeTeamMember/,
  "demo revoke must remain local while production uses the server",
);

const accessBlock = sliceBetween(
  "const result = await changeTeamMemberAccess({",
  "} finally {",
);
assertInOrder(accessBlock, [
  "const result = await changeTeamMemberAccess({",
  "await refreshProductionTeamAuthority()",
]);
assert.doesNotMatch(
  accessBlock,
  /setTeamMembers\(/,
  "production access changes must wait for server readback",
);
const accessDemoBranch = source.slice(
  source.lastIndexOf("if (allowLocalTeamMemberMutation) {", source.indexOf("const result = await changeTeamMemberAccess({")),
  source.indexOf("const result = await changeTeamMemberAccess({"),
);
assert.match(
  accessDemoBranch,
  /if \(allowLocalTeamMemberMutation\) \{[\s\S]*?setTeamMembers\([\s\S]*?return;/,
  "demo access changes must remain local and return before the production client",
);

const storageWrites = indicesOf(
  source,
  "window.localStorage.setItem(teamMembersStorageKey, JSON.stringify(teamMembers));",
);
assert.equal(storageWrites.length, 1);
const teamStorageGuard = source.slice(Math.max(0, storageWrites[0] - 80), storageWrites[0] + 120);
assert.match(
  teamStorageGuard,
  /if \(showDemoContent\) \{[\s\S]*?window\.localStorage\.setItem/,
  "production Team authority must not be persisted to browser storage",
);
assert.doesNotMatch(
  source,
  /localStorage\.setItem\([^)]*freshTeamInviteUrls/,
  "fresh bearer URLs must remain ephemeral React state",
);
assert.match(
  source,
  /const \[scopedFreshTeamInviteUrls, setScopedFreshTeamInviteUrls\] = useState\([\s\S]*?createScopedFreshTeamInviteUrls\(teamRosterAuthorityKey\)/,
  "fresh bearer URLs must live in workspace-scoped memory above the conditionally mounted Team view",
);
assert.match(
  source,
  /freshTeamInviteUrls=\{workspaceFreshTeamInviteUrls\}[\s\S]*?onFreshTeamInviteUrlsChange=\{handleFreshTeamInviteUrlsChange\}/,
  "Team view remounts must receive the same workspace-scoped one-time URLs",
);
assert.match(
  source,
  /copyFreshTeamInviteUrl[\s\S]*?catch \{[\s\S]*?Copy the invite link shown below manually\./,
  "manual delivery must remain possible when clipboard permission is denied",
);
assert.match(
  source,
  /Newly issued link \(server confirmed\)/,
  "a confirmed one-time URL must remain visible while projection readback is unavailable",
);
assert.match(
  source,
  /const visibleMemberOfEntries = isDemoWorkspace \? memberOfEntries : \[\];/,
  "browser-backed Team memberships must be invisible outside explicit demo mode",
);
assert.match(
  source,
  /if \(!isDemoWorkspace\) \{\s*window\.localStorage\.removeItem\(teamMembershipsStorageKey\);/,
  "production must scrub the obsolete browser Team-membership authority",
);

assert.match(
  source,
  /const \[activeTeamMemberEmail, setActiveTeamMemberEmail\] = useState<string \| null>\(null\)/,
  "member selection must use a stable canonical email rather than a mutable array index",
);
assert.match(
  source,
  /findTeamMemberIndexByEmail\([\s\S]*?teamRosterPresentation\.members,[\s\S]*?activeTeamMemberEmail/,
);
assert.doesNotMatch(
  source,
  /useState<number \| null>\(null\)[\s\S]{0,100}activeTeamMemberIndex/,
  "authoritative polling must not retarget a selected member by index",
);

const demoActionsStart = source.indexOf(
  ') : showDemoContent && activeTeamMember.status === "Access removed" ? (',
);
assert.notEqual(demoActionsStart, -1);
const demoActionsEnd = source.indexOf(
  "\n                </div>\n                {teamFeedbackMessage",
  demoActionsStart,
);
assert.notEqual(demoActionsEnd, -1);
const demoActionsBlock = source.slice(demoActionsStart, demoActionsEnd);
for (const label of [
  "Restore access",
  "Remove member",
  "Re-send invite",
  "Resend invite",
]) {
  assert.match(demoActionsBlock, new RegExp(label));
}
for (const action of ["remove-member", "resend-invite", "cancel-invite"]) {
  const actionIndices = indicesOf(source, `setActiveTeamConfirmation("${action}")`);
  assert.ok(actionIndices.length > 0, `missing ${action} action`);
  actionIndices.forEach((index) => {
    assert.ok(
      index >= demoActionsStart && index < demoActionsEnd,
      `${action} must only be reachable inside the demo action branch`,
    );
  });
}

const pendingCancelCallIndex = source.indexOf("const result = await cancelTeamInvite({");
assert.notEqual(pendingCancelCallIndex, -1);
const pendingCancelGuardIndex = source.lastIndexOf(
  "if (isSendingTeamInvite) {",
  pendingCancelCallIndex,
);
assert.ok(
  pendingCancelGuardIndex >= 0 && pendingCancelCallIndex - pendingCancelGuardIndex < 400,
  "pending cancellation must ignore duplicate clicks",
);
const confirmationHandler = sliceBetween(
  "disabled={isSendingTeamInvite}\n                  onClick={async () => {",
  "className={",
  source.indexOf("{activeTeamConfirmation && modalHost"),
);
assert.match(confirmationHandler, /if \(isSendingTeamInvite\) \{\s*return;/);
const accessCallIndex = source.indexOf("const result = await changeTeamMemberAccess({");
assert.notEqual(accessCallIndex, -1);
const accessGuardIndex = source.lastIndexOf("isSendingTeamInvite ||", accessCallIndex);
assert.ok(
  accessGuardIndex >= 0 && accessCallIndex - accessGuardIndex < 2_000,
  "access mutation must retain its in-flight guard",
);
