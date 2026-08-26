import type { CollaborationOwnerSourceLocator } from "./collaborationOwnerSourceLocator";

export const COLLABORATION_OWNER_READ_ENDPOINT = "/api/collaboration/owner";

const CSRF_REFRESH_MARGIN_SECONDS = 15;
const OPAQUE_ID_PATTERN = /^[A-Za-z0-9_-]{22,128}$/;

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
};

type CollaborationOwnerReadFailureStatus =
  | "invalid_collaboration_id"
  | "not_found"
  | "unauthorized"
  | "forbidden"
  | "rate_limited"
  | "unavailable"
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
        | "rate_limited"
        | "unavailable"
        | "invalid_response";
      retryAfterSeconds?: number;
    };

type OwnerOperationFailure = {
  status:
    | "not_found"
    | "unauthorized"
    | "forbidden"
    | "rate_limited"
    | "unavailable"
    | "invalid_response";
  retryAfterSeconds?: number;
};

type CsrfState = {
  token: string;
  expiresAt: number;
};

type CsrfResult =
  | { status: "success"; csrf: CsrfState }
  | OwnerOperationFailure;

let csrfState: CsrfState | null = null;
let csrfBootstrapPromise: Promise<CsrfResult> | null = null;

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

function parseCollaboration(value: unknown): CollaborationOwnerReadDto | null {
  if (
    !isExactRecord(value, [
      "collaborationId",
      "mailboxId",
      "state",
      "createdAt",
      "updatedAt",
      "source",
      "messages",
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
    !Array.isArray(value.messages)
  ) {
    return null;
  }

  const messages = value.messages.map(parseMessage);
  if (messages.some((message) => message === null)) {
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
  };
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function parseRetryAfter(response: Response): number | undefined {
  const value = response.headers.get("Retry-After");
  if (value === null || !/^(?:[1-9]|[1-5][0-9]|60)$/.test(value)) {
    return undefined;
  }

  return Number(value);
}

function classifyFailure(response: Response): OwnerOperationFailure {
  if (response.status === 401) {
    csrfState = null;
    return { status: "unauthorized" };
  }
  if (response.status === 403) {
    return { status: "forbidden" };
  }
  if (response.status === 404) {
    return { status: "not_found" };
  }
  if (response.status === 429) {
    const retryAfterSeconds = parseRetryAfter(response);
    return {
      status: "rate_limited",
      ...(retryAfterSeconds === undefined ? {} : { retryAfterSeconds }),
    };
  }
  if (response.status >= 200 && response.status < 300) {
    return { status: "invalid_response" };
  }
  return { status: "unavailable" };
}

async function bootstrapCsrf(): Promise<CsrfResult> {
  const currentEpochSeconds = Date.now() / 1000;
  if (csrfState && csrfState.expiresAt - currentEpochSeconds > CSRF_REFRESH_MARGIN_SECONDS) {
    return { status: "success", csrf: csrfState };
  }

  csrfState = null;
  if (csrfBootstrapPromise) {
    return csrfBootstrapPromise;
  }

  const pendingBootstrap = (async (): Promise<CsrfResult> => {
    let response: Response;
    try {
      response = await fetch(COLLABORATION_OWNER_READ_ENDPOINT, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ operation: "csrf" }),
      });
    } catch {
      return { status: "unavailable" };
    }

    if (!response.ok) {
      return classifyFailure(response);
    }

    const payload = await readJson(response);
    if (
      !isExactRecord(payload, ["ok", "data"]) ||
      payload.ok !== true ||
      !isExactRecord(payload.data, ["csrfToken", "expiresAt"]) ||
      typeof payload.data.csrfToken !== "string" ||
      payload.data.csrfToken.length === 0 ||
      !Number.isSafeInteger(payload.data.expiresAt) ||
      (payload.data.expiresAt as number) <= Date.now() / 1000
    ) {
      return { status: "invalid_response" };
    }

    csrfState = {
      token: payload.data.csrfToken,
      expiresAt: payload.data.expiresAt as number,
    };
    return { status: "success", csrf: csrfState };
  })().finally(() => {
    csrfBootstrapPromise = null;
  });

  csrfBootstrapPromise = pendingBootstrap;
  return pendingBootstrap;
}

function isValidSourceLocator(
  locator: CollaborationOwnerSourceLocator,
): boolean {
  if (
    !isExactRecord(locator, ["mailboxId", "sourceRef"]) ||
    typeof locator.mailboxId !== "string" ||
    locator.mailboxId.length === 0 ||
    locator.mailboxId !== locator.mailboxId.trim()
  ) {
    return false;
  }

  const sourceRef: unknown = locator.sourceRef;

  if (
    isExactRecord(sourceRef, ["providerMessageId"]) &&
    typeof sourceRef.providerMessageId === "string" &&
    /^\S+$/.test(sourceRef.providerMessageId)
  ) {
    return true;
  }

  return (
    isExactRecord(sourceRef, ["folder", "uidValidity", "imapUid"]) &&
    sourceRef.folder === "INBOX" &&
    typeof sourceRef.uidValidity === "string" &&
    /^[1-9][0-9]*$/.test(sourceRef.uidValidity) &&
    typeof sourceRef.imapUid === "string" &&
    /^[1-9][0-9]*$/.test(sourceRef.imapUid)
  );
}

async function executeOwnerOperation(
  body: Record<string, unknown>,
  csrfToken: string,
): Promise<{ response: Response; payload: unknown }> {
  const response = await fetch(COLLABORATION_OWNER_READ_ENDPOINT, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Cuevion-CSRF": csrfToken,
    },
    body: JSON.stringify(body),
  });
  return { response, payload: await readJson(response) };
}

async function performAuthenticatedOwnerOperation(
  body: Record<string, unknown>,
): Promise<{ status: "success"; payload: unknown } | OwnerOperationFailure> {
  let csrfResult = await bootstrapCsrf();
  if (csrfResult.status !== "success") {
    return csrfResult;
  }

  for (let attempt = 0; attempt < 2; attempt += 1) {
    let operationResult: { response: Response; payload: unknown };
    try {
      operationResult = await executeOwnerOperation(body, csrfResult.csrf.token);
    } catch {
      return { status: "unavailable" };
    }

    if (operationResult.response.status === 403 && attempt === 0) {
      csrfState = null;
      csrfResult = await bootstrapCsrf();
      if (csrfResult.status !== "success") {
        return csrfResult;
      }
      continue;
    }

    if (!operationResult.response.ok) {
      return classifyFailure(operationResult.response);
    }

    return { status: "success", payload: operationResult.payload };
  }

  return { status: "forbidden" };
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
      ? parseCollaboration(result.payload.data.collaboration)
      : null;
  return collaboration
    ? { status: "success", collaboration }
    : { status: "invalid_response" };
}

export function __resetCollaborationOwnerReadApiForTests() {
  csrfState = null;
  csrfBootstrapPromise = null;
}
