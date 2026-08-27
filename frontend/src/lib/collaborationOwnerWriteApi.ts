import {
  COLLABORATION_OWNER_ENDPOINT,
  performAuthenticatedCollaborationOwnerRequest,
  type CollaborationOwnerTransportFailure,
} from "./collaborationOwnerApiTransport";
import {
  parseCollaborationOwnerReadDto,
  type CollaborationOwnerReadDto,
} from "./collaborationOwnerReadApi";
import {
  isTrustedCollaborationOwnerSourceLocator,
  type CollaborationOwnerSourceLocator,
} from "./collaborationOwnerSourceLocator";

export const COLLABORATION_OWNER_WRITE_ENDPOINT = COLLABORATION_OWNER_ENDPOINT;

export type CollaborationOwnerCreateState =
  | "needs_review"
  | "needs_action"
  | "note_only";

export type CollaborationOwnerCreateResult =
  | {
      status: "success";
      created: boolean;
      collaboration: CollaborationOwnerReadDto;
    }
  | { status: "invalid_source_locator" }
  | { status: "invalid_state" }
  | CollaborationOwnerTransportFailure;

function isExactRecord(
  value: unknown,
  keys: readonly string[],
): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }

  const receivedKeys = Object.keys(value);
  return (
    receivedKeys.length === keys.length &&
    keys.every((key) => receivedKeys.includes(key))
  );
}

function isCreateState(value: unknown): value is CollaborationOwnerCreateState {
  return (
    value === "needs_review" ||
    value === "needs_action" ||
    value === "note_only"
  );
}

export async function createCollaborationForOwner(
  locator: CollaborationOwnerSourceLocator,
  state: CollaborationOwnerCreateState,
): Promise<CollaborationOwnerCreateResult> {
  if (!isTrustedCollaborationOwnerSourceLocator(locator)) {
    return { status: "invalid_source_locator" };
  }
  if (!isCreateState(state)) {
    return { status: "invalid_state" };
  }

  const result = await performAuthenticatedCollaborationOwnerRequest({
    operation: "create",
    mailboxId: locator.mailboxId,
    sourceRef: locator.sourceRef,
    state,
  });
  if (result.status !== "response") {
    return result;
  }

  const data =
    isExactRecord(result.payload, ["ok", "data"]) &&
    result.payload.ok === true &&
    isExactRecord(result.payload.data, ["created", "collaboration"])
      ? result.payload.data
      : null;
  const created = data?.created;
  const collaboration = data
    ? parseCollaborationOwnerReadDto(data.collaboration)
    : null;
  if (
    typeof created !== "boolean" ||
    collaboration === null ||
    collaboration.mailboxId !== locator.mailboxId ||
    (created && collaboration.state !== state) ||
    result.httpStatus !== (created ? 201 : 200)
  ) {
    return { status: "invalid_response" };
  }

  return {
    status: "success",
    created,
    collaboration,
  };
}
