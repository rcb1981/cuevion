export const AUTH0_LOGIN_ENDPOINT = "/api/auth/login";
export const AUTH0_SESSION_ENDPOINT = "/api/auth/session";
export const AUTH0_LOGOUT_ENDPOINT = "/api/auth/logout";

const CANONICAL_LOGIN_URL = "https://app.cuevion.com/login";
const AUTH0_LOGOUT_HOSTNAME = "cuevion-dev.eu.auth0.com";

export type AuthenticationContext = "auth0" | "collaboration";

export type CuevionSessionUser = {
  email: string;
  name: string;
  userType: "member";
  userId?: string;
  workspaceId?: string;
};

export type StartupSessionResult =
  | {
      status: "authenticated";
      authSource: "auth0";
      user: CuevionSessionUser;
    }
  | {
      status: "unauthenticated" | "unavailable";
      authSource: null;
      user: null;
    };

type SessionProbeResult =
  | { status: "authenticated"; user: CuevionSessionUser }
  | { status: "unauthenticated" | "unavailable"; user: null };

export type LogoutResult =
  | { ok: true; logoutUrl: string | null }
  | { ok: false; logoutUrl: null };

type FetchImplementation = typeof fetch;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeMember(value: unknown): CuevionSessionUser | null {
  if (!isRecord(value)) {
    return null;
  }

  const email = typeof value.email === "string" ? value.email.trim().toLowerCase() : "";
  const name = typeof value.name === "string" ? value.name.trim() : "";
  const userId = typeof value.userId === "string" ? value.userId.trim() : "";
  const workspaceId =
    typeof value.workspaceId === "string" ? value.workspaceId.trim() : "";

  if (
    !email ||
    !name ||
    value.userType !== "member" ||
    !userId ||
    !workspaceId
  ) {
    return null;
  }

  return {
    email,
    name,
    userType: "member",
    ...(userId ? { userId } : {}),
    ...(workspaceId ? { workspaceId } : {}),
  };
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function probeAuth0Session(
  fetchImplementation: FetchImplementation,
): Promise<SessionProbeResult> {
  let response: Response;

  try {
    response = await fetchImplementation(AUTH0_SESSION_ENDPOINT, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
  } catch {
    return { status: "unavailable", user: null };
  }

  if (response.status === 401) {
    return { status: "unauthenticated", user: null };
  }

  if (!response.ok) {
    return { status: "unavailable", user: null };
  }

  const payload = await readJson(response);
  if (isRecord(payload) && payload.authenticated === false) {
    return { status: "unauthenticated", user: null };
  }

  if (
    !isRecord(payload) ||
    payload.authenticated !== true ||
    payload.authSource !== "auth0"
  ) {
    return { status: "unavailable", user: null };
  }

  const user = normalizeMember(payload);
  return user
    ? { status: "authenticated", user }
    : { status: "unavailable", user: null };
}

export async function loadStartupSession(
  fetchImplementation: FetchImplementation = fetch,
): Promise<StartupSessionResult> {
  const auth0Session = await probeAuth0Session(fetchImplementation);

  if (auth0Session.status === "authenticated") {
    return {
      status: "authenticated",
      authSource: "auth0",
      user: auth0Session.user,
    };
  }

  return { status: auth0Session.status, authSource: null, user: null };
}

export function isAuth0LoginPath(pathname: string) {
  const normalizedPath = pathname.replace(/\/+$/, "") || "/";
  return normalizedPath === "/login";
}

export function hasAuthCallbackError(search: string) {
  const params = new URLSearchParams(search);
  return params.has("error") || params.has("auth_error");
}

export function getSessionAccountStorageKey(
  authenticationContext: AuthenticationContext | null,
  user: Pick<CuevionSessionUser, "email" | "workspaceId"> | null,
) {
  if (!authenticationContext || !user) {
    return "";
  }

  return authenticationContext === "auth0"
    ? user.workspaceId?.trim() ?? ""
    : user.email.trim().toLowerCase();
}

export function getSessionViewerStorageKey(
  authenticationContext: AuthenticationContext | null,
  user: Pick<CuevionSessionUser, "email" | "userId"> | null,
) {
  if (!authenticationContext || !user) {
    return "";
  }

  return authenticationContext === "auth0"
    ? user.userId?.trim() ?? ""
    : user.email.trim().toLowerCase();
}

export function isAllowedAuth0LogoutUrl(value: unknown): value is string {
  if (typeof value !== "string" || !value) {
    return false;
  }

  try {
    const url = new URL(value);
    const clientIds = url.searchParams.getAll("client_id");
    const returnTargets = url.searchParams.getAll("returnTo");
    const queryKeys = Array.from(url.searchParams.keys());

    return (
      url.protocol === "https:" &&
      url.hostname === AUTH0_LOGOUT_HOSTNAME &&
      url.port === "" &&
      url.username === "" &&
      url.password === "" &&
      url.pathname === "/v2/logout" &&
      url.hash === "" &&
      queryKeys.length === 2 &&
      queryKeys.every((key) => key === "client_id" || key === "returnTo") &&
      clientIds.length === 1 &&
      Boolean(clientIds[0]) &&
      returnTargets.length === 1 &&
      returnTargets[0] === CANONICAL_LOGIN_URL
    );
  } catch {
    return false;
  }
}

export async function logoutAuth0Session(
  fetchImplementation: FetchImplementation = fetch,
): Promise<LogoutResult> {
  try {
    const response = await fetchImplementation(AUTH0_LOGOUT_ENDPOINT, {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });

    if (!response.ok) {
      return { ok: false, logoutUrl: null };
    }

    const payload = await readJson(response);
    const logoutUrl = isRecord(payload) ? payload.logoutUrl : null;
    return isAllowedAuth0LogoutUrl(logoutUrl)
      ? { ok: true, logoutUrl }
      : { ok: false, logoutUrl: null };
  } catch {
    return { ok: false, logoutUrl: null };
  }
}
