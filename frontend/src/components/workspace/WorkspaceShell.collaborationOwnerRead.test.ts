declare const process: { exitCode?: number };

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

function sourceBetween(source: string, startMarker: string, endMarker: string) {
  const start = source.indexOf(startMarker);
  assert.notEqual(start, -1, `Missing start marker: ${startMarker}`);
  const end = source.indexOf(endMarker, start);
  assert.notEqual(end, -1, `Missing end marker: ${endMarker}`);
  return source.slice(start, end);
}

try {
  const workspaceSource = fs.readFileSync(
    path.resolve(__dirname, "./WorkspaceShell.tsx"),
    "utf8",
  );
  const ownerReadRegion = sourceBetween(
    workspaceSource,
    "const beginCollaborationOwnerRead = (",
    "const openShareCollaboration = (",
  );
  const explicitOpenRegion = sourceBetween(
    workspaceSource,
    "const openShareCollaboration = (",
    "const clearCollaborationDraft = (",
  );
  const closeRegion = sourceBetween(
    workspaceSource,
    "const closeCollaborationOverlay = () =>",
    "const syncCollaborationMentionState = (",
  );
  const ownerCreateRegion = sourceBetween(
    workspaceSource,
    "const createMessageCollaboration = () =>",
    "const sendCollaborationReply = (",
  );
  const ownerCreateFailureCopyRegion = sourceBetween(
    workspaceSource,
    "function getCollaborationOwnerCreateFailureMessage(",
    "const primaryNavigationItems = [",
  );

  assert.equal((workspaceSource.match(/lookupCollaborationForOwner\(/g) ?? []).length, 1);
  assert.equal((workspaceSource.match(/readCollaborationForOwner\(/g) ?? []).length, 1);
  assert.equal(ownerReadRegion.includes("deriveCollaborationOwnerSourceLocator"), true);
  assert.ok(
    ownerReadRegion.indexOf("lookupCollaborationForOwner") <
      ownerReadRegion.indexOf("readCollaborationForOwner"),
  );
  assert.equal(
    ownerReadRegion.includes("readCollaborationForOwner(\n        lookupResult.collaborationId"),
    true,
  );
  assert.equal(
    ownerReadRegion.includes(
      "readResult.collaboration.collaborationId !== lookupResult.collaborationId",
    ),
    true,
  );
  assert.equal(
    ownerReadRegion.includes(
      "readResult.collaboration.mailboxId !== trustedLocator.mailboxId",
    ),
    true,
  );
  assert.ok((ownerReadRegion.match(/if \(!isCurrentRequest\(\)\)/g) ?? []).length >= 2);
  assert.equal(
    ownerReadRegion.includes(
      "activeRequest?.inFlight && activeRequest.identityKey === identityKey",
    ),
    true,
  );

  assert.equal(explicitOpenRegion.includes("loadOwnerProjection: true"), true);
  assert.equal(
    explicitOpenRegion.includes("if (options?.loadOwnerProjection)"),
    true,
  );
  assert.equal(explicitOpenRegion.includes("beginCollaborationOwnerRead("), true);
  assert.equal(closeRegion.includes("fenceCollaborationOwnerProjection();"), true);

  assert.equal(
    (workspaceSource.match(/createCollaborationForOwner\(/g) ?? []).length,
    1,
    "The visible owner-v2 start path must have one create invocation",
  );
  assert.equal(
    ownerCreateRegion.includes(
      "createCollaborationForOwner(\n          trustedLocator,\n          \"needs_review\",",
    ),
    true,
  );
  assert.equal(
    ownerCreateRegion.includes("collaboration: result.collaboration"),
    true,
    "The complete validated create DTO must become the server projection directly",
  );
  assert.equal(ownerCreateRegion.includes("result.created"), false);
  assert.equal(ownerCreateRegion.includes("lookupCollaborationForOwner("), false);
  assert.equal(ownerCreateRegion.includes("readCollaborationForOwner("), false);
  assert.equal(ownerCreateRegion.includes("createCollaborationThread("), false);
  assert.equal(ownerCreateRegion.includes("updateMessageById("), false);
  assert.equal(ownerCreateRegion.includes("setMailboxStore("), false);
  assert.equal(ownerCreateRegion.includes("setDraftCollaborationByMessageId"), true);
  assert.ok(
    ownerCreateRegion.indexOf("if (currentLocator)") <
      ownerCreateRegion.indexOf("setDraftCollaborationByMessageId"),
    "Only unsupported contexts may reach the unchanged legacy draft path",
  );
  assert.equal(
    ownerCreateRegion.slice(
      ownerCreateRegion.indexOf("if (currentLocator)"),
      ownerCreateRegion.indexOf("const initialCollaboration"),
    ).includes("setDraftCollaborationByMessageId"),
    false,
  );

  for (const forbiddenOwnerCreateSideEffect of [
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "X-Cuevion-Idempotency-Key",
    "setInterval(",
    "setTimeout(",
  ]) {
    assert.equal(
      ownerCreateRegion.includes(forbiddenOwnerCreateSideEffect),
      false,
      `Owner create path must not contain ${forbiddenOwnerCreateSideEffect}`,
    );
  }
  assert.equal(
    ownerCreateRegion.includes("deriveCollaborationOwnerSourceLocator({"),
    true,
  );
  assert.equal(ownerCreateRegion.includes("locator: trustedLocator"), true);
  assert.equal(ownerCreateRegion.includes("activeRequest?.inFlight"), true);
  assert.equal(ownerCreateRegion.includes('operation: "create"'), true);
  assert.equal(ownerCreateRegion.includes("currentRequest.requestId === requestId"), true);
  assert.equal(ownerCreateRegion.includes("currentRequest.messageId === messageId"), true);
  assert.equal(
    ownerCreateRegion.includes("currentRequest.sourceMailboxId === sourceMailboxId"),
    true,
  );
  assert.equal(ownerCreateRegion.includes("if (!isCurrentRequest())"), true);
  assert.equal(
    ownerCreateRegion.includes('failureStatus: "invalid_source_locator"'),
    true,
  );
  assert.equal(
    workspaceSource.includes(
      'collaborationOwnerCreateState.status === "loading" ||\n                              collaborationOwnerProjection.status === "loading"',
    ),
    true,
  );
  assert.equal(
    workspaceSource.includes("data-collaboration-owner-create-feedback"),
    true,
  );
  assert.equal(workspaceSource.includes("Starting collaboration…"), true);

  for (const failureStatus of [
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "rate_limited",
    "service_unavailable",
    "internal_error",
    "invalid_response",
    "network_failure",
    "invalid_source_locator",
    "invalid_state",
  ]) {
    assert.equal(
      ownerCreateFailureCopyRegion.includes(`\"${failureStatus}\"`),
      true,
      `Missing bounded create failure copy for ${failureStatus}`,
    );
  }
  assert.equal(ownerCreateFailureCopyRegion.includes("404"), false);
  assert.equal(
    ownerCreateFailureCopyRegion.includes(
      "Collaboration is temporarily unavailable. Try again later.",
    ),
    true,
  );

  for (const forbiddenOwnerReadSideEffect of [
    "setMailboxStore(",
    "createCollaborationThread(",
    "mutateCollaborationThread(",
    "issueCollaborationInvite(",
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "setInterval(",
    "setTimeout(",
  ]) {
    assert.equal(
      ownerReadRegion.includes(forbiddenOwnerReadSideEffect),
      false,
      `Owner read path must not contain ${forbiddenOwnerReadSideEffect}`,
    );
  }
  assert.equal(ownerReadRegion.includes("status: \"failure\""), true);
  assert.equal(workspaceSource.includes("data-collaboration-owner-read-projection"), true);
  assert.equal(workspaceSource.includes('data-collaboration-owner-read-only="true"'), true);
  assert.equal(workspaceSource.includes('data-collaboration-owner-write-controls="hidden"'), true);
  assert.equal(
    workspaceSource.includes(
      "activeCollaborationOwnerProjection?.source.subject ??\n                          activeCollaborationMessage.subject",
    ),
    true,
  );

  const ownerReadCallIndex = workspaceSource.indexOf("lookupCollaborationForOwner(");
  const precedingEffectIndex = workspaceSource.lastIndexOf("useEffect(() => {", ownerReadCallIndex);
  const precedingOwnerFunctionIndex = workspaceSource.lastIndexOf(
    "const beginCollaborationOwnerRead = (",
    ownerReadCallIndex,
  );
  assert.ok(
    precedingOwnerFunctionIndex > precedingEffectIndex,
    "Owner lookup must be owned by the explicit-open function, not an effect",
  );
} catch (error) {
  process.exitCode = 1;
  console.error("FAIL: Workspace Collaboration owner-read integration contract");
  console.error(error);
}
