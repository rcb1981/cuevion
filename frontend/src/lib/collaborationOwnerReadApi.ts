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
const CANONICAL_EMAIL_PATTERN =
  /^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;
const UNSAFE_DISPLAY_NAME_PATTERN = /[\p{Cc}\p{Cf}\p{Cs}]/u;
const MAX_PARTICIPANT_DISPLAY_NAME_BYTES = 256;
const MAX_EXTERNAL_GUESTS = 16;
const MIN_EXTERNAL_GUEST_TIMESTAMP_SECONDS = 1_577_836_800;
const MAX_EXTERNAL_GUEST_TIMESTAMP_SECONDS = 4_102_444_800;

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

export type CollaborationExternalGuestStatus =
  | "pending"
  | "active"
  | "logged_out"
  | "revoked"
  | "expired";

export type CollaborationExternalGuest = {
  inviteId: string;
  status: CollaborationExternalGuestStatus;
  expiresAt: number;
  invitedEmail?: string;
  displayName?: string;
};

type CollaborationOwnerReadDtoBase = {
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
  participants: CollaborationOwnerReadParticipant[];
};

export type CollaborationOwnerViewerReadDto = CollaborationOwnerReadDtoBase & {
  viewerAccess: "owner";
  externalGuests: CollaborationExternalGuest[];
};

export type CollaborationParticipantViewerReadDto = CollaborationOwnerReadDtoBase & {
  viewerAccess: "participant";
};

export type CollaborationOwnerReadDto =
  | CollaborationOwnerViewerReadDto
  | CollaborationParticipantViewerReadDto;

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

function isCanonicalExternalGuestEmail(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value !== value.toLowerCase() ||
    new TextEncoder().encode(value).length > 320 ||
    !CANONICAL_EMAIL_PATTERN.test(value)
  ) {
    return false;
  }
  const [localPart, domain] = value.split("@");
  return localPart.length <= 64 && domain.length <= 253;
}

function parseExternalGuest(value: unknown): CollaborationExternalGuest | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const keys = [
    "inviteId",
    "status",
    "expiresAt",
    ...(Object.prototype.hasOwnProperty.call(record, "invitedEmail")
      ? ["invitedEmail"]
      : []),
    ...(Object.prototype.hasOwnProperty.call(record, "displayName")
      ? ["displayName"]
      : []),
  ];
  if (
    !isExactRecord(record, keys) ||
    !isValidCollaborationOwnerReadId(record.inviteId) ||
    (record.status !== "pending" &&
      record.status !== "active" &&
      record.status !== "logged_out" &&
      record.status !== "revoked" &&
      record.status !== "expired") ||
    typeof record.expiresAt !== "number" ||
    !Number.isSafeInteger(record.expiresAt) ||
    record.expiresAt < MIN_EXTERNAL_GUEST_TIMESTAMP_SECONDS ||
    record.expiresAt > MAX_EXTERNAL_GUEST_TIMESTAMP_SECONDS ||
    (Object.prototype.hasOwnProperty.call(record, "invitedEmail") &&
      !isCanonicalExternalGuestEmail(record.invitedEmail)) ||
    (Object.prototype.hasOwnProperty.call(record, "displayName") &&
      (typeof record.displayName !== "string" ||
        record.displayName.length === 0 ||
        record.displayName !== record.displayName.trim() ||
        UNSAFE_DISPLAY_NAME_PATTERN.test(record.displayName) ||
        new TextEncoder().encode(record.displayName).length > 256)) ||
    (record.status === "pending" &&
      Object.prototype.hasOwnProperty.call(record, "displayName"))
  ) {
    return null;
  }
  return {
    inviteId: record.inviteId,
    status: record.status,
    expiresAt: record.expiresAt,
    ...(typeof record.invitedEmail === "string"
      ? { invitedEmail: record.invitedEmail }
      : {}),
    ...(typeof record.displayName === "string"
      ? { displayName: record.displayName }
      : {}),
  };
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
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const viewerAccess = record.viewerAccess;
  const exactKeys = [
    "collaborationId",
    "mailboxId",
    "state",
    "createdAt",
    "updatedAt",
    "source",
    "messages",
    "viewerAccess",
    "participants",
    ...(viewerAccess === "owner" ? ["externalGuests"] : []),
  ];
  if (
    (viewerAccess !== "owner" && viewerAccess !== "participant") ||
    !isExactRecord(record, exactKeys) ||
    !isValidCollaborationOwnerReadId(record.collaborationId) ||
    typeof record.mailboxId !== "string" ||
    record.mailboxId.length === 0 ||
    record.mailboxId !== record.mailboxId.trim() ||
    (record.state !== "needs_review" &&
      record.state !== "needs_action" &&
      record.state !== "note_only" &&
      record.state !== "resolved") ||
    !isSafeTimestamp(record.createdAt) ||
    !isSafeTimestamp(record.updatedAt) ||
    record.updatedAt < record.createdAt ||
    !isExactRecord(record.source, [
      "subject",
      "senderDisplay",
      "fromDisplay",
      "timestamp",
      "bodyText",
    ]) ||
    typeof record.source.subject !== "string" ||
    typeof record.source.senderDisplay !== "string" ||
    typeof record.source.fromDisplay !== "string" ||
    typeof record.source.timestamp !== "string" ||
    typeof record.source.bodyText !== "string" ||
    !Array.isArray(record.messages) ||
    !Array.isArray(record.participants) ||
    record.participants.length < 1 ||
    record.participants.length > 16 ||
    (viewerAccess === "owner" && !Array.isArray(record.externalGuests))
  ) {
    return null;
  }

  const messages = record.messages.map(parseMessage);
  if (messages.some((message) => message === null)) {
    return null;
  }
  const participants = record.participants.map(parseParticipant);
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

  const externalGuests =
    viewerAccess === "owner"
      ? (record.externalGuests as unknown[]).map(parseExternalGuest)
      : [];
  if (
    externalGuests.length > MAX_EXTERNAL_GUESTS ||
    externalGuests.some((guest) => guest === null)
  ) {
    return null;
  }
  const parsedExternalGuests = externalGuests as CollaborationExternalGuest[];
  if (
    parsedExternalGuests.some(
      (guest, index) =>
        index > 0 &&
        parsedExternalGuests[index - 1].inviteId >= guest.inviteId,
    )
  ) {
    return null;
  }

  const parsedBase: CollaborationOwnerReadDtoBase = {
    collaborationId: record.collaborationId,
    mailboxId: record.mailboxId,
    state: record.state as CollaborationOwnerReadState,
    createdAt: record.createdAt,
    updatedAt: record.updatedAt,
    source: {
      subject: record.source.subject,
      senderDisplay: record.source.senderDisplay,
      fromDisplay: record.source.fromDisplay,
      timestamp: record.source.timestamp,
      bodyText: record.source.bodyText,
    },
    messages: messages as CollaborationOwnerReadMessage[],
    participants: parsedParticipants,
  };
  return viewerAccess === "owner"
    ? { ...parsedBase, viewerAccess, externalGuests: parsedExternalGuests }
    : { ...parsedBase, viewerAccess };
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
