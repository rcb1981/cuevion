import {
  COLLABORATION_OWNER_ENDPOINT,
  performAuthenticatedCollaborationOwnerRequest,
  type CollaborationOwnerTransportFailure,
} from "./collaborationOwnerApiTransport";
import {
  isCanonicalCollaborationExternalGuestEmail,
  isValidCollaborationOwnerReadId,
  isValidCollaborationParticipantUserId,
  parseCollaborationExternalGuest,
  parseCollaborationOwnerReadDto,
  type CollaborationExternalGuest,
  type CollaborationOwnerReadMessage,
  type CollaborationOwnerReadDto,
  type CollaborationOwnerViewerReadDto,
} from "./collaborationOwnerReadApi";
import {
  isTrustedCollaborationOwnerSourceLocator,
  type CollaborationOwnerSourceLocator,
} from "./collaborationOwnerSourceLocator";
import { isValidCollaborationGuestBearer } from "./collaborationGuestInviteLink";

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
  | { status: "invalid_participant_user_id" }
  | CollaborationOwnerTransportFailure;

export type CollaborationOwnerGuestInvitationMetadata = {
  inviteId: string;
  collaborationId: string;
  allowedActions: ["read", "reply"];
  identityAssurance: "link_possession";
  expiresAt: number;
  status: "active" | "exchanged" | "revoked" | "expired";
  invitedEmail?: string;
};

type CollaborationOwnerInvitationCreation =
  | { invitationCreated: true; token: string }
  | { invitationCreated: false };

export type CollaborationOwnerCreateWithGuestResult =
  | ({
      status: "success";
      created: boolean;
      collaboration: CollaborationOwnerViewerReadDto;
      invitation: CollaborationOwnerGuestInvitationMetadata;
    } & CollaborationOwnerInvitationCreation)
  | { status: "invalid_source_locator" }
  | { status: "invalid_state" }
  | { status: "invalid_invited_email" }
  | CollaborationOwnerTransportFailure;

export type CollaborationOwnerIssueGuestInvitationResult =
  | ({
      status: "success";
      collaboration: CollaborationOwnerViewerReadDto;
      invitation: CollaborationOwnerGuestInvitationMetadata;
    } & CollaborationOwnerInvitationCreation)
  | { status: "invalid_collaboration_id" }
  | { status: "invalid_invited_email" }
  | CollaborationOwnerTransportFailure;

export type CollaborationOwnerRevokeGuestInvitationResult =
  | {
      status: "success";
      collaboration: CollaborationOwnerViewerReadDto;
      invitation: CollaborationExternalGuest & { status: "revoked" };
    }
  | { status: "invalid_collaboration_id" }
  | { status: "invalid_invite_id" }
  | CollaborationOwnerTransportFailure;

export type CollaborationOwnerAddParticipantResult =
  | { status: "success"; collaboration: CollaborationOwnerReadDto }
  | { status: "invalid_collaboration_id" }
  | { status: "invalid_participant_user_id" }
  | CollaborationOwnerTransportFailure;

export type CollaborationOwnerAppendResult =
  | {
      status: "success";
      message: CollaborationOwnerReadMessage;
      updatedAt: number;
    }
  | CollaborationOwnerTransportFailure;

export type CollaborationOwnerAppendOperation = Readonly<{
  execute: () => Promise<CollaborationOwnerAppendResult>;
}>;

export type CollaborationOwnerAppendPreparationResult =
  | { status: "ready"; operation: CollaborationOwnerAppendOperation }
  | { status: "invalid_collaboration_id" }
  | { status: "invalid_text" };

const MAX_APPEND_TEXT_BYTES = 16_384;
const MIN_APPEND_TIMESTAMP = 1_577_836_800_000;
const MAX_APPEND_TIMESTAMP = 4_102_444_800_999;
const MIN_INVITATION_TIMESTAMP_SECONDS = 1_577_836_800;
const MAX_INVITATION_TIMESTAMP_SECONDS = 4_102_444_800;
const BASE64URL_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
const FORBIDDEN_APPEND_TEXT_CATEGORY = /[\p{Cf}\p{Cs}]/u;
const CONTROL_CHARACTER = /\p{Cc}/u;

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

export function parseCollaborationOwnerGuestInvitationMetadata(
  value: unknown,
): CollaborationOwnerGuestInvitationMetadata | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const keys = [
    "inviteId",
    "collaborationId",
    "allowedActions",
    "identityAssurance",
    "expiresAt",
    "status",
    ...(Object.prototype.hasOwnProperty.call(record, "invitedEmail")
      ? ["invitedEmail"]
      : []),
  ];
  if (
    !isExactRecord(record, keys) ||
    !isValidCollaborationOwnerReadId(record.inviteId) ||
    !isValidCollaborationOwnerReadId(record.collaborationId) ||
    !Array.isArray(record.allowedActions) ||
    record.allowedActions.length !== 2 ||
    record.allowedActions[0] !== "read" ||
    record.allowedActions[1] !== "reply" ||
    record.identityAssurance !== "link_possession" ||
    typeof record.expiresAt !== "number" ||
    !Number.isSafeInteger(record.expiresAt) ||
    record.expiresAt < MIN_INVITATION_TIMESTAMP_SECONDS ||
    record.expiresAt > MAX_INVITATION_TIMESTAMP_SECONDS ||
    (record.status !== "active" &&
      record.status !== "exchanged" &&
      record.status !== "revoked" &&
      record.status !== "expired") ||
    (Object.prototype.hasOwnProperty.call(record, "invitedEmail") &&
      !isCanonicalCollaborationExternalGuestEmail(record.invitedEmail))
  ) {
    return null;
  }
  return {
    inviteId: record.inviteId,
    collaborationId: record.collaborationId,
    allowedActions: ["read", "reply"],
    identityAssurance: "link_possession",
    expiresAt: record.expiresAt,
    status: record.status,
    ...(typeof record.invitedEmail === "string"
      ? { invitedEmail: record.invitedEmail }
      : {}),
  };
}

function parseVerifiedOwnerCollaboration(
  value: unknown,
): CollaborationOwnerViewerReadDto | null {
  const collaboration = parseCollaborationOwnerReadDto(value);
  return collaboration?.viewerAccess === "owner" ? collaboration : null;
}

function invitationMatchesOwnerProjection(
  invitation: CollaborationOwnerGuestInvitationMetadata,
  collaboration: CollaborationOwnerViewerReadDto,
): boolean {
  const projected = collaboration.externalGuests.find(
    (guest) => guest.inviteId === invitation.inviteId,
  );
  if (
    !projected ||
    projected.expiresAt !== invitation.expiresAt ||
    projected.invitedEmail !== invitation.invitedEmail
  ) {
    return false;
  }
  if (invitation.status === "active") {
    return projected.status === "pending";
  }
  if (invitation.status === "revoked") {
    return projected.status === "revoked";
  }
  if (invitation.status === "expired") {
    return projected.status === "expired";
  }
  return projected.status !== "pending";
}

function externalGuestRecordsMatch(
  left: CollaborationExternalGuest,
  right: CollaborationExternalGuest,
): boolean {
  return (
    left.inviteId === right.inviteId &&
    left.status === right.status &&
    left.expiresAt === right.expiresAt &&
    left.invitedEmail === right.invitedEmail &&
    left.displayName === right.displayName
  );
}

function isSafeAppendTimestamp(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= MIN_APPEND_TIMESTAMP &&
    value <= MAX_APPEND_TIMESTAMP
  );
}

function isValidAppendText(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    new TextEncoder().encode(value).byteLength > MAX_APPEND_TEXT_BYTES
  ) {
    return false;
  }

  for (const character of value) {
    if (
      FORBIDDEN_APPEND_TEXT_CATEGORY.test(character) ||
      (CONTROL_CHARACTER.test(character) &&
        character !== "\n" &&
        character !== "\r" &&
        character !== "\t")
    ) {
      return false;
    }
  }
  return true;
}

function generateAppendIdempotencyKey(): string {
  const bytes = new Uint8Array(32);
  globalThis.crypto.getRandomValues(bytes);

  let encoded = "";
  for (let index = 0; index < bytes.length; index += 3) {
    const first = bytes[index];
    const second = bytes[index + 1];
    const third = bytes[index + 2];
    encoded += BASE64URL_ALPHABET[first >> 2];
    encoded += BASE64URL_ALPHABET[((first & 0x03) << 4) | ((second ?? 0) >> 4)];
    if (second !== undefined) {
      encoded +=
        BASE64URL_ALPHABET[((second & 0x0f) << 2) | ((third ?? 0) >> 6)];
    }
    if (third !== undefined) {
      encoded += BASE64URL_ALPHABET[third & 0x3f];
    }
  }
  return encoded;
}

function parseAppendMessage(
  value: unknown,
  visibility: "internal" | "shared",
  text: string,
): CollaborationOwnerReadMessage | null {
  if (
    !isExactRecord(value, [
      "id",
      "authorDisplayName",
      "authorRole",
      "text",
      "timestamp",
      "visibility",
    ]) ||
    !isValidCollaborationOwnerReadId(value.id) ||
    typeof value.authorDisplayName !== "string" ||
    value.authorDisplayName.length === 0 ||
    value.authorRole !== "Cuevion user" ||
    value.text !== text ||
    value.visibility !== visibility ||
    !isSafeAppendTimestamp(value.timestamp)
  ) {
    return null;
  }

  return {
    id: value.id,
    authorDisplayName: value.authorDisplayName,
    authorRole: value.authorRole,
    text: value.text,
    timestamp: value.timestamp,
    visibility,
  };
}

async function executeAppendOperation(
  collaborationId: string,
  visibility: "internal" | "shared",
  text: string,
  idempotencyKey: string,
): Promise<CollaborationOwnerAppendResult> {
  const result = await performAuthenticatedCollaborationOwnerRequest(
    {
      operation:
        visibility === "internal" ? "append_internal" : "append_shared",
      collaborationId,
      text,
    },
    { idempotencyKey },
  );
  if (result.status !== "response") {
    return result;
  }

  const data =
    result.httpStatus === 200 &&
    isExactRecord(result.payload, ["ok", "data"]) &&
    result.payload.ok === true &&
    isExactRecord(result.payload.data, ["message", "updatedAt"])
      ? result.payload.data
      : null;
  const message = data ? parseAppendMessage(data.message, visibility, text) : null;
  if (
    message === null ||
    !isSafeAppendTimestamp(data?.updatedAt) ||
    data.updatedAt !== message.timestamp
  ) {
    return { status: "invalid_response" };
  }

  return { status: "success", message, updatedAt: data.updatedAt };
}

function prepareAppendOperation(
  collaborationId: string,
  visibility: "internal" | "shared",
  text: string,
): CollaborationOwnerAppendPreparationResult {
  if (!isValidCollaborationOwnerReadId(collaborationId)) {
    return { status: "invalid_collaboration_id" };
  }
  if (!isValidAppendText(text)) {
    return { status: "invalid_text" };
  }

  const idempotencyKey = generateAppendIdempotencyKey();
  return {
    status: "ready",
    operation: Object.freeze({
      execute: () =>
        executeAppendOperation(
          collaborationId,
          visibility,
          text,
          idempotencyKey,
        ),
    }),
  };
}

export function prepareInternalCollaborationMessageForOwner(
  collaborationId: string,
  text: string,
): CollaborationOwnerAppendPreparationResult {
  return prepareAppendOperation(collaborationId, "internal", text);
}

export function prepareSharedCollaborationMessageForOwner(
  collaborationId: string,
  text: string,
): CollaborationOwnerAppendPreparationResult {
  return prepareAppendOperation(collaborationId, "shared", text);
}

export async function createCollaborationForOwner(
  locator: CollaborationOwnerSourceLocator,
  state: CollaborationOwnerCreateState,
  participantUserId?: string,
): Promise<CollaborationOwnerCreateResult> {
  if (!isTrustedCollaborationOwnerSourceLocator(locator)) {
    return { status: "invalid_source_locator" };
  }
  if (!isCreateState(state)) {
    return { status: "invalid_state" };
  }
  if (!isValidCollaborationParticipantUserId(participantUserId)) {
    return { status: "invalid_participant_user_id" };
  }

  const result = await performAuthenticatedCollaborationOwnerRequest({
    operation: "create",
    mailboxId: locator.mailboxId,
    sourceRef: locator.sourceRef,
    state,
    participantUserId,
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

export async function createCollaborationWithGuestForOwner(
  locator: CollaborationOwnerSourceLocator,
  state: CollaborationOwnerCreateState,
  invitedEmail?: string,
): Promise<CollaborationOwnerCreateWithGuestResult> {
  if (!isTrustedCollaborationOwnerSourceLocator(locator)) {
    return { status: "invalid_source_locator" };
  }
  if (!isCreateState(state)) {
    return { status: "invalid_state" };
  }
  if (
    invitedEmail !== undefined &&
    !isCanonicalCollaborationExternalGuestEmail(invitedEmail)
  ) {
    return { status: "invalid_invited_email" };
  }

  const result = await performAuthenticatedCollaborationOwnerRequest({
    operation: "create_with_guest",
    mailboxId: locator.mailboxId,
    sourceRef: locator.sourceRef,
    state,
    ...(invitedEmail === undefined ? {} : { invitedEmail }),
  });
  if (result.status !== "response") {
    return result;
  }

  const data =
    isExactRecord(result.payload, ["ok", "data"]) &&
    result.payload.ok === true &&
    typeof result.payload.data === "object" &&
    result.payload.data !== null &&
    !Array.isArray(result.payload.data)
      ? (result.payload.data as Record<string, unknown>)
      : null;
  const invitationCreated = data?.invitationCreated;
  const expectedKeys =
    invitationCreated === true
      ? [
          "created",
          "invitationCreated",
          "collaboration",
          "invitation",
          "token",
        ]
      : ["created", "invitationCreated", "collaboration", "invitation"];
  if (!data || !isExactRecord(data, expectedKeys)) {
    return { status: "invalid_response" };
  }

  const created = data.created;
  const collaboration = parseVerifiedOwnerCollaboration(data.collaboration);
  const invitation = parseCollaborationOwnerGuestInvitationMetadata(
    data.invitation,
  );
  const token = isValidCollaborationGuestBearer(data.token)
    ? data.token
    : null;
  if (
    typeof created !== "boolean" ||
    typeof invitationCreated !== "boolean" ||
    collaboration === null ||
    invitation === null ||
    collaboration.mailboxId !== locator.mailboxId ||
    (created && collaboration.state !== state) ||
    invitation.collaborationId !== collaboration.collaborationId ||
    invitation.invitedEmail !== invitedEmail ||
    !invitationMatchesOwnerProjection(invitation, collaboration) ||
    result.httpStatus !== (created ? 201 : 200) ||
    (invitationCreated && token === null)
  ) {
    return { status: "invalid_response" };
  }

  return invitationCreated
    ? {
        status: "success",
        created,
        invitationCreated: true,
        collaboration,
        invitation,
        token: token as string,
      }
    : {
        status: "success",
        created,
        invitationCreated: false,
        collaboration,
        invitation,
      };
}

export async function issueGuestInvitationForOwner(
  collaborationId: string,
  invitedEmail?: string,
): Promise<CollaborationOwnerIssueGuestInvitationResult> {
  if (!isValidCollaborationOwnerReadId(collaborationId)) {
    return { status: "invalid_collaboration_id" };
  }
  if (
    invitedEmail !== undefined &&
    !isCanonicalCollaborationExternalGuestEmail(invitedEmail)
  ) {
    return { status: "invalid_invited_email" };
  }

  const result = await performAuthenticatedCollaborationOwnerRequest({
    operation: "issue_guest_invite",
    collaborationId,
    ...(invitedEmail === undefined ? {} : { invitedEmail }),
  });
  if (result.status !== "response") {
    return result;
  }

  const data =
    isExactRecord(result.payload, ["ok", "data"]) &&
    result.payload.ok === true &&
    typeof result.payload.data === "object" &&
    result.payload.data !== null &&
    !Array.isArray(result.payload.data)
      ? (result.payload.data as Record<string, unknown>)
      : null;
  const invitationCreated = data?.invitationCreated;
  const expectedKeys =
    invitationCreated === true
      ? ["invitationCreated", "collaboration", "invitation", "token"]
      : ["invitationCreated", "collaboration", "invitation"];
  if (!data || !isExactRecord(data, expectedKeys)) {
    return { status: "invalid_response" };
  }

  const collaboration = parseVerifiedOwnerCollaboration(data.collaboration);
  const invitation = parseCollaborationOwnerGuestInvitationMetadata(
    data.invitation,
  );
  const token = isValidCollaborationGuestBearer(data.token)
    ? data.token
    : null;
  if (
    typeof invitationCreated !== "boolean" ||
    collaboration === null ||
    invitation === null ||
    collaboration.collaborationId !== collaborationId ||
    invitation.collaborationId !== collaborationId ||
    (invitationCreated && invitation.invitedEmail !== invitedEmail) ||
    !invitationMatchesOwnerProjection(invitation, collaboration) ||
    result.httpStatus !== (invitationCreated ? 201 : 200) ||
    (invitationCreated && token === null)
  ) {
    return { status: "invalid_response" };
  }

  return invitationCreated
    ? {
        status: "success",
        invitationCreated: true,
        collaboration,
        invitation,
        token: token as string,
      }
    : {
        status: "success",
        invitationCreated: false,
        collaboration,
        invitation,
      };
}

export async function revokeGuestInvitationForOwner(
  collaborationId: string,
  inviteId: string,
): Promise<CollaborationOwnerRevokeGuestInvitationResult> {
  if (!isValidCollaborationOwnerReadId(collaborationId)) {
    return { status: "invalid_collaboration_id" };
  }
  if (!isValidCollaborationOwnerReadId(inviteId)) {
    return { status: "invalid_invite_id" };
  }

  const result = await performAuthenticatedCollaborationOwnerRequest({
    operation: "revoke_guest_invite",
    collaborationId,
    inviteId,
  });
  if (result.status !== "response") {
    return result;
  }

  const data =
    result.httpStatus === 200 &&
    isExactRecord(result.payload, ["ok", "data"]) &&
    result.payload.ok === true &&
    isExactRecord(result.payload.data, ["collaboration", "invitation"])
      ? result.payload.data
      : null;
  const collaboration = data
    ? parseVerifiedOwnerCollaboration(data.collaboration)
    : null;
  const invitation = data
    ? parseCollaborationExternalGuest(data.invitation)
    : null;
  const projectedInvitation = collaboration?.externalGuests.find(
    (guest) => guest.inviteId === inviteId,
  );
  if (
    collaboration === null ||
    invitation === null ||
    invitation.status !== "revoked" ||
    collaboration.collaborationId !== collaborationId ||
    invitation.inviteId !== inviteId ||
    !projectedInvitation ||
    !externalGuestRecordsMatch(invitation, projectedInvitation)
  ) {
    return { status: "invalid_response" };
  }

  return {
    status: "success",
    collaboration,
    invitation: { ...invitation, status: "revoked" },
  };
}

export async function addParticipantToCollaborationForOwner(
  collaborationId: string,
  participantUserId: string,
): Promise<CollaborationOwnerAddParticipantResult> {
  if (!isValidCollaborationOwnerReadId(collaborationId)) {
    return { status: "invalid_collaboration_id" };
  }
  if (!isValidCollaborationParticipantUserId(participantUserId)) {
    return { status: "invalid_participant_user_id" };
  }

  const result = await performAuthenticatedCollaborationOwnerRequest({
    operation: "add_participant",
    collaborationId,
    participantUserId,
  });
  if (result.status !== "response") {
    return result;
  }

  const collaboration =
    result.httpStatus === 200 &&
    isExactRecord(result.payload, ["ok", "data"]) &&
    result.payload.ok === true &&
    isExactRecord(result.payload.data, ["collaboration"])
      ? parseCollaborationOwnerReadDto(result.payload.data.collaboration)
      : null;
  return collaboration
    ? { status: "success", collaboration }
    : { status: "invalid_response" };
}
