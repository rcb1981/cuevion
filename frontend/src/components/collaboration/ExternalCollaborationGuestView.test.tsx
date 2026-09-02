// @ts-nocheck
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  ExternalCollaborationGuestView,
  mapCollaborationGuestFailureToState,
  shouldOfferInviteAfterBootstrap,
} from "./ExternalCollaborationGuestView";

const source = readFileSync(
  resolve(process.cwd(), "src/components/collaboration/ExternalCollaborationGuestView.tsx"),
  "utf8",
);

assert.equal(mapCollaborationGuestFailureToState("invitation_expired"), "invitation_expired");
assert.equal(mapCollaborationGuestFailureToState("invitation_revoked"), "invitation_revoked");
assert.equal(
  mapCollaborationGuestFailureToState("invitation_already_exchanged"),
  "invitation_already_used",
);
assert.equal(mapCollaborationGuestFailureToState("session_expired"), "session_expired");
assert.equal(mapCollaborationGuestFailureToState("session_revoked"), "session_revoked");
assert.equal(mapCollaborationGuestFailureToState("rate_limited"), "rate_limited");
assert.equal(mapCollaborationGuestFailureToState("network_failure"), "retryable_error");
assert.equal(mapCollaborationGuestFailureToState("invalid_response"), "service_unavailable");

assert.equal(shouldOfferInviteAfterBootstrap("session_missing", true), true);
assert.equal(shouldOfferInviteAfterBootstrap("session_expired", true), true);
assert.equal(shouldOfferInviteAfterBootstrap("session_revoked", true), true);
assert.equal(shouldOfferInviteAfterBootstrap("service_unavailable", true), false);
assert.equal(shouldOfferInviteAfterBootstrap("session_missing", false), false);

assert.equal(typeof ExternalCollaborationGuestView, "function");
assert.equal(source.includes("Auth0"), false);
assert.equal(source.includes("Team Settings"), false);
assert.equal(source.includes("Checking your session"), true);

assert.ok(
  source.indexOf("const result = await api.bootstrap()") <
    source.indexOf("const result = await api.exchange(inviteToken, displayName)"),
  "session bootstrap must be defined before invite exchange",
);
assert.match(
  source,
  /if \(result\.status === "success"\) \{[\s\S]*?setInviteToken\(null\);[\s\S]*?setCsrfToken\(result\.csrfToken\);[\s\S]*?await loadCollaboration\(\);/,
);
assert.match(source, /useLayoutEffect\(\(\) => \{[\s\S]*?scrubCollaborationGuestFragment\(\);/);
assert.match(source, /window\.location\.search\}#collab_guest/);
assert.match(source, /shouldOfferInviteAfterBootstrap\(result\.status, inviteToken !== null\)/);
assert.match(source, /setCollaboration\(result\.collaboration\);[\s\S]*?setDraft\(""\);/);

const recoveryStart = source.indexOf("const recoverSessionAfterReplyFailure");
const recoveryEnd = source.indexOf("const handleReply", recoveryStart);
const recoveryBlock = source.slice(recoveryStart, recoveryEnd);
assert.match(recoveryBlock, /await api\.bootstrap\(\)/);
assert.doesNotMatch(recoveryBlock, /api\.reply/);
assert.match(recoveryBlock, /press Send reply again/);

for (const requiredCopy of [
  "No Cuevion account is required",
  "Your name",
  "Open collaboration",
  "You can see shared collaboration messages only.",
  "Send reply",
  "Leave collaboration",
  "You’ve left this collaboration.",
]) {
  assert.equal(source.includes(requiredCopy), true, `Missing copy: ${requiredCopy}`);
}

for (const forbiddenField of [
  "localStorage",
  "sessionStorage",
  "IndexedDB",
  "ownerEmail",
  "workspaceId",
  "mailboxId",
  "participants",
  "externalGuests",
  "Internal Notes",
  "console.log",
]) {
  assert.equal(source.includes(forbiddenField), false, `Unsafe guest-view field: ${forbiddenField}`);
}
assert.equal(source.includes("{inviteToken}"), false);
assert.equal(source.includes("document.title"), false);
assert.equal(source.includes("useDeferredValue"), false);
assert.equal(source.includes("startTransition"), false);
assert.equal(source.includes("setTimeout"), false);
