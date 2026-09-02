import {
  isCanonicalCollaborationOwnerSourceLocator,
  type CollaborationOwnerSourceLocator,
} from "./collaborationOwnerSourceLocator";
import {
  __resetCollaborationOwnerApiTransportForTests,
  COLLABORATION_OWNER_ENDPOINT,
  performAuthenticatedCollaborationOwnerRequest,
  type CollaborationOwnerTransportFailure,
} from "./collaborationOwnerApiTransport";

export const COLLABORATION_OWNER_READ_ENDPOINT = COLLABORATION_OWNER_ENDPOINT;

const OPAQUE_ID_PATTERN = /^[A-Za-z0-9_-]{22,128}$/;
const CANONICAL_USER_ID_PATTERN = /^usr_[A-Za-z0-9_-]{21}[AQgw]$/;
const UNSAFE_DISPLAY_NAME_PATTERN = /[\p{Cc}\p{Cf}\p{Cs}]/u;
const MAX_PARTICIPANT_DISPLAY_NAME_BYTES = 256;

export type CollaborationOwnerReadState =
  | "needs_review"
  | "needs_action"
  | "note_only"
  | "resolved";

export type CollaborationOwnerReadMessage = {
  id: string;
  authorDisplayName: string;
  authorRole: "Cuevion user" | "Guest reviewer" | "System";
  text: string;
  visibility: "internal" | "shared";
  timestamp: number;
};

export type CollaborationOwnerReadParticipant = {
  userId: string;
  displayName: string;
  access: "owner" | "participant";
};

export type CollaborationOwnerReadDto = {
  collaborationId: string;
  mailboxId: string;
  state: CollaborationOwnerReadState;
  createdAt: number;
  updatedAt: number;
  source: {
    subject: string;
    senderDisplay: string;
    fromDisplay: string;
    timestamp: string;
    bodyText: string;
  };
  messages: CollaborationOwnerReadMessage[];
  viewerAccess: "owner" | "participant";
  participants: CollaborationOwnerReadParticipant[];
};

export type CollaborationOwnerReadFailureStatus =
  | "invalid_collaboration_id"
  | "not_found"
  | "unauthorized"
  | "forbidden"
  | "conflict"
  | "rate_limited"
  | "service_unavailable"
  | "internal_error"
  | "network_failure"
  | "invalid_response";

export type CollaborationOwnerReadResult =
  | { status: "success"; collaboration: CollaborationOwnerReadDto }
  | { status: CollaborationOwnerReadFailureStatus; retryAfterSeconds?: number };

export type CollaborationOwnerLookupResult =
  | { status: "success"; collaborationId: string }
  | { status: "invalid_source_locator" }
  | {
      status:
        | "not_found"
        | "unauthorized"
        | "forbidden"
        | "conflict"
        | "rate_limited"
        | "service_unavailable"
        | "internal_error"
        | "network_failure"
        | "invalid_response";
      retryAfterSeconds?: number;
    };

type OwnerOperationFailure = {
  status:
    | "not_found"
    | "unauthorized"
    | "forbidden"
    | "conflict"
    | "rate_limited"
    | "service_unavailable"
    | "internal_error"
    | "network_failure"
    | "invalid_response";
  retryAfterSeconds?: number;
};

function isExactRecord(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }

  const receivedKeys = Object.keys(value);
  return receivedKeys.length === keys.length && keys.every((key) => receivedKeys.includes(key));
}

function isSafeTimestamp(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

export function isValidCollaborationOwnerReadId(value: unknown): value is string {
  return typeof value === "string" && OPAQUE_ID_PATTERN.test(value);
}

export function isValidCollaborationParticipantUserId(
  value: unknown,
): value is string {
  return typeof value === "string" && CANONICAL_USER_ID_PATTERN.test(value);
}

function parseMessage(value: unknown): CollaborationOwnerReadMessage | null {
  if (
    !isExactRecord(value, [
      "id",
      "authorDisplayName",
      "authorRole",
      "text",
      "visibility",
      "timestamp",
    ]) ||
    !isValidCollaborationOwnerReadId(value.id) ||
    typeof value.authorDisplayName !== "string" ||
    value.authorDisplayName.length === 0 ||
    (value.authorRole !== "Cuevion user" &&
      value.authorRole !== "Guest reviewer" &&
      value.authorRole !== "System") ||
    typeof value.text !== "string" ||
    (value.visibility !== "internal" && value.visibility !== "shared") ||
    !isSafeTimestamp(value.timestamp)
  ) {
    return null;
  }

  return {
    id: value.id,
    authorDisplayName: value.authorDisplayName,
    authorRole: value.authorRole,
    text: value.text,
    visibility: value.visibility,
    timestamp: value.timestamp,
  };
}

function parseParticipant(value: unknown): CollaborationOwnerReadParticipant | null {
  if (
    !isExactRecord(value, ["userId", "displayName", "access"]) ||
    !isValidCollaborationParticipantUserId(value.userId) ||
    typeof value.displayName !== "string" ||
    value.displayName.length === 0 ||
    value.displayName !== value.displayName.trim() ||
    value.displayName !== value.displayName.normalize("NFC") ||
    UNSAFE_DISPLAY_NAME_PATTERN.test(value.displayName) ||
    new TextEncoder().encode(value.displayName).length > MAX_PARTICIPANT_DISPLAY_NAME_BYTES ||
    (value.access !== "owner" && value.access !== "participant")
  ) {
    return null;
  }

  return {
    userId: value.userId,
    displayName: value.displayName,
    access: value.access,
  };
}

export function parseCollaborationOwnerReadDto(
  value: unknown,
): CollaborationOwnerReadDto | null {
  if (
    !isExactRecord(value, [
      "collaborationId",
      "mailboxId",
      "state",
      "createdAt",
      "updatedAt",
      "source",
      "messages",
      "viewerAccess",
      "participants",
    ]) ||
    !isValidCollaborationOwnerReadId(value.collaborationId) ||
    typeof value.mailboxId !== "string" ||
    value.mailboxId.length === 0 ||
    value.mailboxId !== value.mailboxId.trim() ||
    (value.state !== "needs_review" &&
      value.state !== "needs_action" &&
      value.state !== "note_only" &&
      value.state !== "resolved") ||
    !isSafeTimestamp(value.createdAt) ||
    !isSafeTimestamp(value.updatedAt) ||
    value.updatedAt < value.createdAt ||
    !isExactRecord(value.source, [
      "subject",
      "senderDisplay",
      "fromDisplay",
      "timestamp",
      "bodyText",
    ]) ||
    typeof value.source.subject !== "string" ||
    typeof value.source.senderDisplay !== "string" ||
    typeof value.source.fromDisplay !== "string" ||
    typeof value.source.timestamp !== "string" ||
    typeof value.source.bodyText !== "string" ||
    !Array.isArray(value.messages) ||
    (value.viewerAccess !== "owner" && value.viewerAccess !== "participant") ||
    !Array.isArray(value.participants) ||
    value.participants.length < 1 ||
    value.participants.length > 16
  ) {
    return null;
  }

  const messages = value.messages.map(parseMessage);
  if (messages.some((message) => message === null)) {
    return null;
  }
  const participants = value.participants.map(parseParticipant);
  if (
    participants.some((participant) => participant === null) ||
    participants[0]?.access !== "owner" ||
    participants.filter((participant) => participant?.access === "owner").length !== 1
  ) {
    return null;
  }
  const parsedParticipants = participants as CollaborationOwnerReadParticipant[];
  const userIds = parsedParticipants.map((participant) => participant.userId);
  const explicitParticipants = parsedParticipants.slice(1);
  if (
    new Set(userIds).size !== userIds.length ||
    explicitParticipants.some((participant) => participant.access !== "participant") ||
    explicitParticipants.some(
      (participant, index) =>
        index > 0 && explicitParticipants[index - 1].userId >= participant.userId,
    )
  ) {
    return null;
  }

  return {
    collaborationId: value.collaborationId,
    mailboxId: value.mailboxId,
    state: value.state,
    createdAt: value.createdAt,
    updatedAt: value.updatedAt,
    source: {
      subject: value.source.subject,
      senderDisplay: value.source.senderDisplay,
      fromDisplay: value.source.fromDisplay,
      timestamp: value.source.timestamp,
      bodyText: value.source.bodyText,
    },
    messages: messages as CollaborationOwnerReadMessage[],
    viewerAccess: value.viewerAccess,
    participants: parsedParticipants,
  };
}

function isValidSourceLocator(
  locator: CollaborationOwnerSourceLocator,
): boolean {
  return isCanonicalCollaborationOwnerSourceLocator(locator);
}

function mapTransportFailure(
  failure: CollaborationOwnerTransportFailure,
): OwnerOperationFailure {
  if (failure.status === "rate_limited") {
    return {
      status: "rate_limited",
      ...(failure.retryAfterSeconds === undefined
        ? {}
        : { retryAfterSeconds: failure.retryAfterSeconds }),
    };
  }
  if (failure.status === "unauthorized") {
    return { status: "unauthorized" };
  }
  if (failure.status === "forbidden") {
    return { status: "forbidden" };
  }
  if (failure.status === "not_found") {
    return { status: "not_found" };
  }
  if (failure.status === "invalid_response") {
    return { status: "invalid_response" };
  }
  return { status: failure.status };
}

async function performAuthenticatedOwnerOperation(
  body: Record<string, unknown>,
): Promise<{ status: "success"; payload: unknown } | OwnerOperationFailure> {
  const result = await performAuthenticatedCollaborationOwnerRequest(body);
  return result.status === "response"
    ? { status: "success", payload: result.payload }
    : mapTransportFailure(result);
}

export async function lookupCollaborationForOwner(
  locator: CollaborationOwnerSourceLocator,
): Promise<CollaborationOwnerLookupResult> {
  if (!isValidSourceLocator(locator)) {
    return { status: "invalid_source_locator" };
  }

  const result = await performAuthenticatedOwnerOperation({
    operation: "lookup",
    mailboxId: locator.mailboxId,
    sourceRef: locator.sourceRef,
  });
  if (result.status !== "success") {
    return result;
  }

  if (
    !isExactRecord(result.payload, ["ok", "data"]) ||
    result.payload.ok !== true ||
    !isExactRecord(result.payload.data, ["collaborationId"]) ||
    !isValidCollaborationOwnerReadId(result.payload.data.collaborationId)
  ) {
    return { status: "invalid_response" };
  }

  return {
    status: "success",
    collaborationId: result.payload.data.collaborationId,
  };
}

export async function readCollaborationForOwner(
  collaborationId: string,
): Promise<CollaborationOwnerReadResult> {
  if (!isValidCollaborationOwnerReadId(collaborationId)) {
    return { status: "invalid_collaboration_id" };
  }

  const result = await performAuthenticatedOwnerOperation({
    operation: "read",
    collaborationId,
  });
  if (result.status !== "success") {
    return result;
  }

  const collaboration =
    isExactRecord(result.payload, ["ok", "data"]) &&
    result.payload.ok === true &&
    isExactRecord(result.payload.data, ["collaboration"])
      ? parseCollaborationOwnerReadDto(result.payload.data.collaboration)
      : null;
  return collaboration
    ? { status: "success", collaboration }
    : { status: "invalid_response" };
}

export function __resetCollaborationOwnerReadApiForTests() {
  __resetCollaborationOwnerApiTransportForTests();
}
