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
