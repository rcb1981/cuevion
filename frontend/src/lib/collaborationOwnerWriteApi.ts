import {
  COLLABORATION_OWNER_ENDPOINT,
  performAuthenticatedCollaborationOwnerRequest,
  type CollaborationOwnerTransportFailure,
} from "./collaborationOwnerApiTransport";
import {
  isValidCollaborationOwnerReadId,
  parseCollaborationOwnerReadDto,
  type CollaborationOwnerReadMessage,
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
