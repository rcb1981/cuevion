export const COLLABORATION_OWNER_ENDPOINT = "/api/collaboration/owner";

const CSRF_REFRESH_MARGIN_SECONDS = 15;

export type CollaborationOwnerTransportFailure = {
  status:
    | "unauthorized"
    | "forbidden"
    | "not_found"
    | "conflict"
    | "rate_limited"
    | "service_unavailable"
    | "internal_error"
    | "invalid_response"
    | "network_failure";
  retryAfterSeconds?: number;
};

export type CollaborationOwnerAuthenticatedResponse =
  | { status: "response"; httpStatus: number; payload: unknown }
  | CollaborationOwnerTransportFailure;

type CsrfState = {
  token: string;
  expiresAt: number;
};

type CsrfResult =
  | { status: "success"; csrf: CsrfState }
  | CollaborationOwnerTransportFailure;

type CollaborationOwnerAuthenticatedRequestOptions = {
  idempotencyKey?: string;
};

let csrfState: CsrfState | null = null;
let csrfBootstrapPromise: Promise<CsrfResult> | null = null;

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

function classifyFailure(response: Response): CollaborationOwnerTransportFailure {
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
  if (response.status === 409) {
    return { status: "conflict" };
  }
  if (response.status === 429) {
    const retryAfterSeconds = parseRetryAfter(response);
    return {
      status: "rate_limited",
      ...(retryAfterSeconds === undefined ? {} : { retryAfterSeconds }),
    };
  }
  if (response.status === 503) {
    return { status: "service_unavailable" };
  }
  if (response.status >= 500 && response.status <= 599) {
    return { status: "internal_error" };
  }
  return { status: "internal_error" };
}

async function bootstrapCsrf(): Promise<CsrfResult> {
  const currentEpochSeconds = Date.now() / 1000;
  if (
    csrfState &&
    csrfState.expiresAt - currentEpochSeconds > CSRF_REFRESH_MARGIN_SECONDS
  ) {
    return { status: "success", csrf: csrfState };
  }

  csrfState = null;
  if (csrfBootstrapPromise) {
    return csrfBootstrapPromise;
  }

  const pendingBootstrap = (async (): Promise<CsrfResult> => {
    let response: Response;
    try {
      response = await fetch(COLLABORATION_OWNER_ENDPOINT, {
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
      return { status: "network_failure" };
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

async function executeOwnerOperation(
  body: Readonly<Record<string, unknown>>,
  csrfToken: string,
  options: CollaborationOwnerAuthenticatedRequestOptions,
): Promise<{ response: Response; payload: unknown }> {
  const response = await fetch(COLLABORATION_OWNER_ENDPOINT, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Cuevion-CSRF": csrfToken,
      ...(options.idempotencyKey === undefined
        ? {}
        : { "X-Cuevion-Idempotency-Key": options.idempotencyKey }),
    },
    body: JSON.stringify(body),
  });
  return { response, payload: await readJson(response) };
}

export async function performAuthenticatedCollaborationOwnerRequest(
  body: Readonly<Record<string, unknown>>,
  options: CollaborationOwnerAuthenticatedRequestOptions = {},
): Promise<CollaborationOwnerAuthenticatedResponse> {
  let csrfResult = await bootstrapCsrf();
  if (csrfResult.status !== "success") {
    return csrfResult;
  }

  for (let attempt = 0; attempt < 2; attempt += 1) {
    let operationResult: { response: Response; payload: unknown };
    try {
      operationResult = await executeOwnerOperation(
        body,
        csrfResult.csrf.token,
        options,
      );
    } catch {
      return { status: "network_failure" };
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

    return {
      status: "response",
      httpStatus: operationResult.response.status,
      payload: operationResult.payload,
    };
  }

  return { status: "forbidden" };
}

export function __resetCollaborationOwnerApiTransportForTests() {
  csrfState = null;
  csrfBootstrapPromise = null;
}
