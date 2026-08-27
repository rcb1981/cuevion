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
  const ownerWriteSource = fs.readFileSync(
    path.resolve(__dirname, "../../lib/collaborationOwnerWriteApi.ts"),
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
  const ownerProjectionFenceRegion = sourceBetween(
    workspaceSource,
    "const fenceCollaborationOwnerProjection = () =>",
    "const beginCollaborationOwnerRead = (",
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
  const ownerInternalNoteFailureCopyRegion = sourceBetween(
    workspaceSource,
    "function getCollaborationOwnerInternalNoteFailureMessage(",
    "function getCollaborationOwnerSharedMessageFailureMessage(",
  );
  const ownerSharedMessageFailureCopyRegion = sourceBetween(
    workspaceSource,
    "function getCollaborationOwnerSharedMessageFailureMessage(",
    "const primaryNavigationItems = [",
  );
  const ownerSharedMessageRegion = sourceBetween(
    workspaceSource,
    "const submitCollaborationOwnerSharedMessage = () =>",
    "const submitCollaborationOwnerInternalNote = () =>",
  );
  const ownerInternalNoteRegion = sourceBetween(
    workspaceSource,
    "const submitCollaborationOwnerInternalNote = () =>",
    "const sendCollaborationReply = (",
  );
  const ownerInternalNoteComposerRegion = sourceBetween(
    workspaceSource,
    "data-collaboration-owner-internal-note-composer",
    ") : isPreStartCollaboration ? (",
  );
  const ownerSharedMessageComposerRegion = sourceBetween(
    workspaceSource,
    "data-collaboration-owner-shared-message-composer",
    "data-collaboration-owner-internal-note-composer",
  );
  const ownerAppendClientRegion = sourceBetween(
    ownerWriteSource,
    "async function executeAppendOperation(",
    "export async function createCollaborationForOwner(",
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
  assert.equal(
    workspaceSource.includes('data-collaboration-owner-internal-note-enabled="true"'),
    true,
  );
  assert.equal(
    workspaceSource.includes('data-collaboration-owner-shared-message-enabled="true"'),
    true,
  );
  assert.equal(workspaceSource.includes('data-collaboration-owner-read-only="true"'), false);
  assert.equal(workspaceSource.includes('data-collaboration-owner-write-controls="hidden"'), false);
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

  assert.equal(
    (workspaceSource.match(/prepareInternalCollaborationMessageForOwner\(/g) ?? [])
      .length,
    1,
    "One new logical Internal Note must prepare exactly once",
  );
  assert.equal(
    (ownerInternalNoteRegion.match(/pendingRequest\.operation\.execute\(\)/g) ?? [])
      .length,
    1,
    "Submission and explicit retry must execute the retained operation",
  );
  assert.equal(ownerAppendClientRegion.includes('visibility === "internal" ? "append_internal"'), true);
  assert.equal(ownerAppendClientRegion.includes("prepareInternalCollaborationMessageForOwner("), true);
  assert.equal(ownerInternalNoteRegion.includes("prepareSharedCollaborationMessageForOwner"), false);
  assert.equal(ownerInternalNoteRegion.includes("append_shared"), false);
  assert.equal(ownerInternalNoteRegion.includes("sendCollaborationReply("), false);
  assert.equal(ownerInternalNoteRegion.includes("mutateCollaborationThread("), false);
  assert.equal(ownerInternalNoteRegion.includes("createCollaborationThread("), false);
  assert.equal(ownerInternalNoteRegion.includes("sendGmailMessage("), false);
  assert.equal(ownerInternalNoteRegion.includes("connectInboxWithImap("), false);

  assert.equal(
    (workspaceSource.match(/prepareSharedCollaborationMessageForOwner\(/g) ?? [])
      .length,
    1,
    "One new logical Shared Message must prepare exactly once",
  );
  assert.equal(
    (ownerSharedMessageRegion.match(/pendingRequest\.operation\.execute\(\)/g) ?? [])
      .length,
    1,
    "Submission and explicit retry must execute the retained shared operation",
  );
  assert.equal(ownerAppendClientRegion.includes('visibility === "internal" ? "append_internal" : "append_shared"'), true);
  assert.equal(ownerAppendClientRegion.includes("prepareSharedCollaborationMessageForOwner("), true);
  assert.equal(ownerSharedMessageRegion.includes("prepareInternalCollaborationMessageForOwner"), false);
  assert.equal(ownerSharedMessageRegion.includes("append_internal"), false);
  for (const forbiddenSharedAction of [
    "sendCollaborationReply(",
    "openComposeFromMessage(",
    "buildGmailReplyContext(",
    "buildImapReplyContext(",
    "sendEmail(",
    "sendGmail(",
    "sendGmailMessage(",
    "sendSmtp(",
    "sendImap(",
    "connectInboxWithImap(",
    "Reply All",
    "mutateCollaborationThread(",
    "createCollaborationThread(",
    "updateMessageById(",
    "setMailboxStore(",
    "setDraftCollaborationByMessageId",
  ]) {
    assert.equal(
      ownerSharedMessageRegion.includes(forbiddenSharedAction),
      false,
      `Owner Shared Message must not use ${forbiddenSharedAction}`,
    );
  }

  assert.equal(ownerSharedMessageRegion.includes("!collaborationOwnerSharedMessageDraft.trim()"), true);
  assert.equal(
    ownerSharedMessageRegion.includes(
      "projection.collaboration.collaborationId,\n        collaborationOwnerSharedMessageDraft,",
    ),
    true,
    "Shared preparation must receive the exact untrimmed draft",
  );
  assert.equal(
    ownerSharedMessageRegion.includes(
      "existingRequest.text === collaborationOwnerSharedMessageDraft",
    ),
    true,
  );
  assert.equal(ownerSharedMessageRegion.includes("existingRequest?.inFlight"), true);
  assert.equal(ownerSharedMessageRegion.includes("request.inFlight = true"), true);
  assert.ok(
    ownerSharedMessageRegion.indexOf("request.inFlight = true") <
      ownerSharedMessageRegion.indexOf("pendingRequest.operation.execute()"),
    "Double-click fencing must be installed before the shared operation executes",
  );
  assert.equal(ownerSharedMessageComposerRegion.includes('status === "sending"'), true);
  assert.equal(ownerSharedMessageComposerRegion.includes("Retry Shared Message"), true);
  assert.equal(ownerSharedMessageComposerRegion.includes("Add Shared Message"), true);
  assert.equal(ownerSharedMessageComposerRegion.includes("email the source-message sender"), true);
  assert.equal(
    ownerSharedMessageComposerRegion.includes(
      "collaborationOwnerSharedMessageRequestRef.current = null",
    ),
    true,
    "Editing a failed logical shared message must abandon its prepared operation",
  );
  assert.ok(
    ownerSharedMessageRegion.indexOf('result.status !== "success"') <
      ownerSharedMessageRegion.indexOf(
        "collaborationOwnerSharedMessageRequestRef.current = null",
        ownerSharedMessageRegion.indexOf('result.status !== "success"'),
      ),
    "A failed shared execution must retain the prepared operation until retry or edit",
  );
  assert.equal(
    ownerSharedMessageRegion.includes("message.id === result.message.id"),
    true,
    "Committed shared server message IDs are the dedupe authority",
  );
  assert.equal(ownerSharedMessageRegion.includes("updatedAt: result.updatedAt"), true);
  assert.equal(ownerSharedMessageRegion.includes("result.message"), true);
  assert.equal(ownerSharedMessageRegion.includes("...current.collaboration"), true);
  assert.equal(ownerSharedMessageRegion.includes("source:"), false);
  assert.equal(ownerSharedMessageRegion.includes("state:"), false);
  assert.equal(ownerSharedMessageRegion.includes("readCollaborationForOwner("), false);
  assert.equal(ownerSharedMessageRegion.includes("lookupCollaborationForOwner("), false);
  assert.equal(ownerSharedMessageRegion.includes("authorDisplayName:"), false);
  assert.equal(ownerSharedMessageRegion.includes("authorRole:"), false);
  assert.equal(ownerSharedMessageRegion.includes("timestamp:"), false);
  assert.equal(ownerSharedMessageRegion.includes("visibility:"), false);
  assert.equal(ownerSharedMessageRegion.includes("closeCollaborationOverlay("), false);
  assert.ok(
    ownerSharedMessageRegion.indexOf("setCollaborationOwnerProjection(") <
      ownerSharedMessageRegion.indexOf(
        "collaborationOwnerSharedMessageRequestRef.current = null",
        ownerSharedMessageRegion.indexOf('result.status !== "success"'),
      ),
    "Shared draft and retained operation may clear only after success projection",
  );

  for (const staleFence of [
    "collaborationOwnerSharedMessageGenerationRef.current === request.requestId",
    "currentProjectionRequest?.identityKey === request.identityKey",
    "currentProjectionRequest.requestId === request.projectionRequestId",
    "currentProjectionRequest.messageId === request.messageId",
    "currentProjectionRequest.sourceMailboxId === request.sourceMailboxId",
    "current.collaboration.collaborationId !== pendingRequest.collaborationId",
    "current.collaboration.mailboxId !== pendingRequest.sourceMailboxId",
  ]) {
    assert.equal(
      ownerSharedMessageRegion.includes(staleFence),
      true,
      `Missing owner Shared Message stale fence: ${staleFence}`,
    );
  }
  assert.equal(closeRegion.includes("fenceCollaborationOwnerProjection();"), true);
  assert.equal(
    ownerProjectionFenceRegion.includes(
      "collaborationOwnerSharedMessageRequestRef.current = null",
    ),
    true,
  );
  assert.equal(
    ownerProjectionFenceRegion.includes(
      "collaborationOwnerSharedMessageGenerationRef.current += 1",
    ),
    true,
  );
  assert.equal(workspaceSource.includes("collaborationOwnerSharedMessageGenerationRef"), true);
  assert.equal(workspaceSource.includes("collaborationOwnerInternalNoteRequestRef"), true);

  for (const failureStatus of [
    "invalid_collaboration_id",
    "invalid_text",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "rate_limited",
    "service_unavailable",
    "internal_error",
    "invalid_response",
    "network_failure",
  ]) {
    assert.equal(
      ownerSharedMessageFailureCopyRegion.includes(`\"${failureStatus}\"`),
      true,
      `Missing bounded Shared Message failure copy for ${failureStatus}`,
    );
  }
  assert.equal(ownerSharedMessageFailureCopyRegion.includes("404"), false);
  assert.equal(
    ownerSharedMessageFailureCopyRegion.includes(
      "Shared messages are temporarily unavailable. Retry later.",
    ),
    true,
  );

  for (const forbiddenPersistence of [
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "saveLiveInboxSnapshot(",
  ]) {
    assert.equal(
      ownerSharedMessageRegion.includes(forbiddenPersistence),
      false,
      `Owner Shared Message must not use ${forbiddenPersistence}`,
    );
  }

  assert.equal(ownerInternalNoteRegion.includes("!collaborationOwnerInternalNoteDraft.trim()"), true);
  assert.equal(
    ownerInternalNoteRegion.includes(
      "projection.collaboration.collaborationId,\n        collaborationOwnerInternalNoteDraft,",
    ),
    true,
    "Preparation must receive the exact untrimmed draft",
  );
  assert.equal(
    ownerInternalNoteRegion.includes(
      "existingRequest.text === collaborationOwnerInternalNoteDraft",
    ),
    true,
  );
  assert.equal(ownerInternalNoteRegion.includes("existingRequest?.inFlight"), true);
  assert.equal(ownerInternalNoteRegion.includes("request.inFlight = true"), true);
  assert.equal(ownerInternalNoteComposerRegion.includes('status === "sending"'), true);
  assert.equal(ownerInternalNoteComposerRegion.includes("Retry Internal Note"), true);
  assert.equal(ownerInternalNoteComposerRegion.includes("Add Internal Note"), true);
  assert.equal(
    ownerInternalNoteComposerRegion.includes(
      "collaborationOwnerInternalNoteRequestRef.current = null",
    ),
    true,
    "Editing a failed logical note must explicitly abandon its prepared operation",
  );

  assert.equal(
    ownerInternalNoteRegion.includes(
      "result.status !== \"success\"",
    ),
    true,
  );
  assert.ok(
    ownerInternalNoteRegion.indexOf('result.status !== "success"') <
      ownerInternalNoteRegion.indexOf(
        'collaborationOwnerInternalNoteRequestRef.current = null',
        ownerInternalNoteRegion.indexOf('result.status !== "success"'),
      ),
    "A failed execution must retain the prepared operation until retry or edit",
  );
  assert.equal(ownerInternalNoteRegion.includes("pendingRequest.operation.execute()"), true);
  assert.equal(
    ownerInternalNoteRegion.includes(
      "message.id === result.message.id",
    ),
    true,
    "Committed server message IDs are the dedupe authority",
  );
  assert.equal(ownerInternalNoteRegion.includes("updatedAt: result.updatedAt"), true);
  assert.equal(ownerInternalNoteRegion.includes("result.message"), true);
  assert.equal(ownerInternalNoteRegion.includes("readCollaborationForOwner("), false);
  assert.equal(ownerInternalNoteRegion.includes("lookupCollaborationForOwner("), false);
  assert.equal(ownerInternalNoteRegion.includes("authorDisplayName:"), false);
  assert.equal(ownerInternalNoteRegion.includes("timestamp:"), false);
  assert.equal(ownerInternalNoteRegion.includes("visibility:"), false);

  for (const staleFence of [
    "collaborationOwnerProjectionGenerationRef.current === request.requestId",
    "currentProjectionRequest?.identityKey === request.identityKey",
    "currentProjectionRequest.requestId === request.projectionRequestId",
    "currentProjectionRequest.messageId === request.messageId",
    "currentProjectionRequest.sourceMailboxId === request.sourceMailboxId",
    "current.collaboration.collaborationId !== pendingRequest.collaborationId",
    "current.collaboration.mailboxId !== pendingRequest.sourceMailboxId",
  ]) {
    assert.equal(
      ownerInternalNoteRegion.includes(staleFence),
      true,
      `Missing owner Internal Note stale fence: ${staleFence}`,
    );
  }
  assert.equal(closeRegion.includes("fenceCollaborationOwnerProjection();"), true);
  assert.equal(
    workspaceSource.includes("collaborationOwnerInternalNoteRequestRef.current = null;\n    setCollaborationOwnerInternalNoteDraft(\"\");"),
    true,
  );

  for (const failureStatus of [
    "invalid_collaboration_id",
    "invalid_text",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "rate_limited",
    "service_unavailable",
    "internal_error",
    "invalid_response",
    "network_failure",
  ]) {
    assert.equal(
      ownerInternalNoteFailureCopyRegion.includes(`\"${failureStatus}\"`),
      true,
      `Missing bounded Internal Note failure copy for ${failureStatus}`,
    );
  }
  assert.equal(ownerInternalNoteFailureCopyRegion.includes("404"), false);
  assert.equal(
    ownerInternalNoteFailureCopyRegion.includes(
      "Internal notes are temporarily unavailable. Retry later.",
    ),
    true,
  );

  assert.equal(ownerInternalNoteComposerRegion.includes("entry.authorDisplayName"), false);
  assert.equal(workspaceSource.includes("entry.authorDisplayName"), true);
  assert.equal(workspaceSource.includes("entry.authorRole"), true);
  assert.equal(workspaceSource.includes("entry.text"), true);
  assert.equal(workspaceSource.includes("entry.timestamp"), true);
  assert.equal(workspaceSource.includes('"Internal · Private"'), true);
  assert.equal(workspaceSource.includes("Server collaboration · Read only"), false);
  assert.equal(workspaceSource.includes("Server projection is read only."), false);

  for (const forbiddenPersistence of [
    "updateMessageById(",
    "setMailboxStore(",
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "saveLiveInboxSnapshot(",
  ]) {
    assert.equal(
      ownerInternalNoteRegion.includes(forbiddenPersistence),
      false,
      `Owner Internal Note must not use ${forbiddenPersistence}`,
    );
  }
  assert.equal(explicitOpenRegion.includes("submitCollaborationOwnerInternalNote("), false);
  assert.equal(workspaceSource.includes("openShareCollaboration("), true);
  assert.equal(workspaceSource.includes("setCollaborationHistoryExpanded("), true);
} catch (error) {
  process.exitCode = 1;
  console.error("FAIL: Workspace Collaboration owner-read integration contract");
  console.error(error);
}
