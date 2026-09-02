export const COLLABORATION_GUEST_ENDPOINT = "/api/collaboration/guest";
export const COLLABORATION_GUEST_CSRF_HEADER = "X-Cuevion-CSRF";

const BEARER_PATTERN = /^[A-Za-z0-9_-]{43,128}$/;
const OPAQUE_ID_PATTERN = /^[A-Za-z0-9_-]{22,128}$/;
const UNSAFE_BOUNDED_STRING_PATTERN = /[\p{Cc}\p{Cf}\p{Cs}]/u;
const UNSAFE_FREE_TEXT_PATTERN = /[\p{Cf}\p{Cs}\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]/u;
const MAX_MESSAGE_COUNT = 500;
const MAX_MESSAGE_BYTES = 16_384;
const MAX_SOURCE_BODY_BYTES = 131_072;

export type CollaborationGuestSession = {
  collaborationId: string;
  guestDisplayName: string;
  allowedActions: ["read", "reply"];
  identityAssurance: "link_possession";
  expiresAt: number;
};

export type CollaborationGuestMessage = {
  id: string;
  authorDisplayName: string;
  authorRole: "Cuevion user" | "Guest reviewer" | "System";
  text: string;
  timestamp: number;
};

export type CollaborationGuestDto = {
  collaborationId: string;
  state: "needs_review" | "needs_action" | "note_only" | "resolved";
  updatedAt: number;
  allowedActions: ["read", "reply"];
  sharedSource: {
    subject: string;
    senderDisplay: string;
    fromDisplay: string;
    timestamp: string;
    bodyText: string;
  };
  messages: CollaborationGuestMessage[];
};

export type CollaborationGuestFailureStatus =
  | "invitation_invalid"
  | "invitation_expired"
  | "invitation_revoked"
  | "invitation_already_exchanged"
  | "session_missing"
  | "session_expired"
  | "session_revoked"
  | "csrf_failed"
  | "origin_rejected"
  | "invalid_request"
  | "conflict"
  | "rate_limited"
  | "service_unavailable"
  | "internal_error"
  | "network_failure"
  | "invalid_response";

export type CollaborationGuestFailure = {
  status: CollaborationGuestFailureStatus;
  retryAfterSeconds?: number;
};

export type CollaborationGuestSessionResult =
  | {
      status: "success";
      session: CollaborationGuestSession;
      csrfToken: string;
    }
  | CollaborationGuestFailure;

export type CollaborationGuestReadResult =
  | { status: "success"; collaboration: CollaborationGuestDto }
  | CollaborationGuestFailure;

export type CollaborationGuestLogoutResult =
  | { status: "success"; loggedOut: true }
  | CollaborationGuestFailure;

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

function byteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

function isBoundedString(
  value: unknown,
  maximumBytes: number,
  allowEmpty = false,
): value is string {
  return (
    typeof value === "string" &&
    value === value.trim() &&
    (allowEmpty || value.length > 0) &&
    !UNSAFE_BOUNDED_STRING_PATTERN.test(value) &&
    byteLength(value) <= maximumBytes
  );
}

function isFreeText(value: unknown, maximumBytes: number): value is string {
  return (
    typeof value === "string" &&
    !UNSAFE_FREE_TEXT_PATTERN.test(value) &&
    byteLength(value) <= maximumBytes
  );
}

function isSafeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function hasReadReplyActions(value: unknown): value is ["read", "reply"] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    value[0] === "read" &&
    value[1] === "reply"
  );
}

export function isValidCollaborationGuestDisplayName(value: unknown): value is string {
  return isBoundedString(value, 256);
}

export function isValidCollaborationGuestReply(value: unknown): value is string {
  return (
    isFreeText(value, MAX_MESSAGE_BYTES) &&
    typeof value === "string" &&
    value.trim().length > 0
  );
}

export function parseCollaborationGuestSession(
  value: unknown,
): CollaborationGuestSession | null {
  if (
    !isExactRecord(value, [
      "collaborationId",
      "guestDisplayName",
      "allowedActions",
      "identityAssurance",
      "expiresAt",
    ]) ||
    typeof value.collaborationId !== "string" ||
    !OPAQUE_ID_PATTERN.test(value.collaborationId) ||
    !isBoundedString(value.guestDisplayName, 256) ||
    !hasReadReplyActions(value.allowedActions) ||
    value.identityAssurance !== "link_possession" ||
    !isSafeInteger(value.expiresAt)
  ) {
    return null;
  }
  return {
    collaborationId: value.collaborationId,
    guestDisplayName: value.guestDisplayName,
    allowedActions: ["read", "reply"],
    identityAssurance: "link_possession",
    expiresAt: value.expiresAt,
  };
}

function parseMessage(value: unknown): CollaborationGuestMessage | null {
  if (
    !isExactRecord(value, [
      "id",
      "authorDisplayName",
      "authorRole",
      "text",
      "timestamp",
    ]) ||
    typeof value.id !== "string" ||
    !OPAQUE_ID_PATTERN.test(value.id) ||
    !isBoundedString(value.authorDisplayName, 256) ||
    (value.authorRole !== "Cuevion user" &&
      value.authorRole !== "Guest reviewer" &&
      value.authorRole !== "System") ||
    !isFreeText(value.text, MAX_MESSAGE_BYTES) ||
    !isSafeInteger(value.timestamp)
  ) {
    return null;
  }
  return {
    id: value.id,
    authorDisplayName: value.authorDisplayName,
    authorRole: value.authorRole,
    text: value.text,
    timestamp: value.timestamp,
  };
}

export function parseCollaborationGuestDto(
  value: unknown,
): CollaborationGuestDto | null {
  if (
    !isExactRecord(value, [
      "collaborationId",
      "state",
      "updatedAt",
      "allowedActions",
      "sharedSource",
      "messages",
    ]) ||
    typeof value.collaborationId !== "string" ||
    !OPAQUE_ID_PATTERN.test(value.collaborationId) ||
    (value.state !== "needs_review" &&
      value.state !== "needs_action" &&
      value.state !== "note_only" &&
      value.state !== "resolved") ||
    !isSafeInteger(value.updatedAt) ||
    !hasReadReplyActions(value.allowedActions) ||
    !isExactRecord(value.sharedSource, [
      "subject",
      "senderDisplay",
      "fromDisplay",
      "timestamp",
      "bodyText",
    ]) ||
    !isBoundedString(value.sharedSource.subject, 998, true) ||
    !isBoundedString(value.sharedSource.senderDisplay, 512, true) ||
    !isBoundedString(value.sharedSource.fromDisplay, 512, true) ||
    !isBoundedString(value.sharedSource.timestamp, 128, true) ||
    !isFreeText(value.sharedSource.bodyText, MAX_SOURCE_BODY_BYTES) ||
    !Array.isArray(value.messages) ||
    value.messages.length > MAX_MESSAGE_COUNT
  ) {
    return null;
  }

  const messages: CollaborationGuestMessage[] = [];
  for (const valueMessage of value.messages) {
    const message = parseMessage(valueMessage);
    if (!message) {
      return null;
    }
    messages.push(message);
  }

  return {
    collaborationId: value.collaborationId,
    state: value.state,
    updatedAt: value.updatedAt,
    allowedActions: ["read", "reply"],
    sharedSource: {
      subject: value.sharedSource.subject,
      senderDisplay: value.sharedSource.senderDisplay,
      fromDisplay: value.sharedSource.fromDisplay,
      timestamp: value.sharedSource.timestamp,
      bodyText: value.sharedSource.bodyText,
    },
    messages,
  };
}

const failureStatuses: Record<string, CollaborationGuestFailureStatus> = {
  invitation_invalid: "invitation_invalid",
  invitation_expired: "invitation_expired",
  invitation_revoked: "invitation_revoked",
  invitation_already_exchanged: "invitation_already_exchanged",
  session_missing: "session_missing",
  session_expired: "session_expired",
  session_revoked: "session_revoked",
  csrf_failed: "csrf_failed",
  origin_rejected: "origin_rejected",
  invalid_request: "invalid_request",
  conflict: "conflict",
  rate_limited: "rate_limited",
  service_unavailable: "service_unavailable",
  internal_error: "internal_error",
  not_found: "service_unavailable",
};

const failureHttpStatuses: Record<string, number> = {
  invitation_invalid: 404,
  invitation_expired: 410,
  invitation_revoked: 410,
  invitation_already_exchanged: 409,
  session_missing: 401,
  session_expired: 401,
  session_revoked: 401,
  csrf_failed: 403,
  origin_rejected: 403,
  invalid_request: 400,
  conflict: 409,
  rate_limited: 429,
  service_unavailable: 503,
  internal_error: 500,
  not_found: 404,
};

function parseRetryAfter(response: Response): number | null | undefined {
  const value = response.headers.get("Retry-After");
  if (value === null) {
    return undefined;
  }
  if (!/^[1-9][0-9]?$/.test(value)) {
    return null;
  }
  const seconds = Number(value);
  return seconds <= 60 ? seconds : null;
}

async function parseFailureResponse(
  response: Response,
  payload: unknown,
): Promise<CollaborationGuestFailure> {
  if (
    !isExactRecord(payload, ["ok", "error"]) ||
    payload.ok !== false ||
    !isExactRecord(payload.error, ["code"]) ||
    typeof payload.error.code !== "string"
  ) {
    return { status: "invalid_response" };
  }
  const status = failureStatuses[payload.error.code];
  if (
    !status ||
    failureHttpStatuses[payload.error.code] !== response.status
  ) {
    return { status: "invalid_response" };
  }
  if (status === "rate_limited") {
    if (response.status !== 429) {
      return { status: "invalid_response" };
    }
    const retryAfterSeconds = parseRetryAfter(response);
    if (retryAfterSeconds === null) {
      return { status: "invalid_response" };
    }
    return {
      status,
      ...(retryAfterSeconds === undefined ? {} : { retryAfterSeconds }),
    };
  }
  return { status };
}

async function performRequest(
  init: RequestInit,
): Promise<
  | { status: "response"; response: Response; payload: unknown }
  | CollaborationGuestFailure
> {
  let response: Response;
  try {
    response = await fetch(COLLABORATION_GUEST_ENDPOINT, {
      credentials: "include",
      cache: "no-store",
      ...init,
    });
  } catch {
    return { status: "network_failure" };
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { status: "invalid_response" };
  }

  if (!response.ok) {
    return parseFailureResponse(response, payload);
  }
  return { status: "response", response, payload };
}

function jsonPost(body: Record<string, unknown>, csrfToken?: string): RequestInit {
  return {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(csrfToken === undefined
        ? {}
        : { [COLLABORATION_GUEST_CSRF_HEADER]: csrfToken }),
    },
    body: JSON.stringify(body),
  };
}

function parseSessionSuccess(payload: unknown): CollaborationGuestSessionResult {
  if (
    !isExactRecord(payload, ["ok", "data"]) ||
    payload.ok !== true ||
    !isExactRecord(payload.data, ["session", "csrfToken"]) ||
    typeof payload.data.csrfToken !== "string" ||
    !BEARER_PATTERN.test(payload.data.csrfToken)
  ) {
    return { status: "invalid_response" };
  }
  const session = parseCollaborationGuestSession(payload.data.session);
  return session
    ? { status: "success", session, csrfToken: payload.data.csrfToken }
    : { status: "invalid_response" };
}

function parseReadSuccess(payload: unknown): CollaborationGuestReadResult {
  if (
    !isExactRecord(payload, ["ok", "data"]) ||
    payload.ok !== true ||
    !isExactRecord(payload.data, ["collaboration"])
  ) {
    return { status: "invalid_response" };
  }
  const collaboration = parseCollaborationGuestDto(payload.data.collaboration);
  return collaboration
    ? { status: "success", collaboration }
    : { status: "invalid_response" };
}

export async function exchangeGuestInvitation(
  token: string,
  displayName: string,
): Promise<CollaborationGuestSessionResult> {
  if (!BEARER_PATTERN.test(token) || !isValidCollaborationGuestDisplayName(displayName)) {
    return { status: "invalid_request" };
  }
  const result = await performRequest(
    jsonPost({ operation: "exchange", token, displayName }),
  );
  return result.status === "response" ? parseSessionSuccess(result.payload) : result;
}

export async function bootstrapGuestSession(): Promise<CollaborationGuestSessionResult> {
  const result = await performRequest(jsonPost({ operation: "bootstrap" }));
  return result.status === "response" ? parseSessionSuccess(result.payload) : result;
}

export async function readGuestCollaboration(): Promise<CollaborationGuestReadResult> {
  const result = await performRequest({
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return result.status === "response" ? parseReadSuccess(result.payload) : result;
}

export async function replyToGuestCollaboration(
  text: string,
  csrfToken: string,
): Promise<CollaborationGuestReadResult> {
  if (!isValidCollaborationGuestReply(text) || !BEARER_PATTERN.test(csrfToken)) {
    return { status: "invalid_request" };
  }
  const result = await performRequest(
    jsonPost({ operation: "reply", text }, csrfToken),
  );
  return result.status === "response" ? parseReadSuccess(result.payload) : result;
}

export async function logoutGuestCollaboration(
  csrfToken: string,
): Promise<CollaborationGuestLogoutResult> {
  if (!BEARER_PATTERN.test(csrfToken)) {
    return { status: "invalid_request" };
  }
  const result = await performRequest(
    jsonPost({ operation: "logout" }, csrfToken),
  );
  if (result.status !== "response") {
    return result;
  }
  return isExactRecord(result.payload, ["ok", "data"]) &&
    result.payload.ok === true &&
    isExactRecord(result.payload.data, ["loggedOut"]) &&
    result.payload.data.loggedOut === true
    ? { status: "success", loggedOut: true }
    : { status: "invalid_response" };
}
