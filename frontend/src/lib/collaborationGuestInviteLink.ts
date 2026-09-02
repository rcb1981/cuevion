const GUEST_BEARER_PATTERN = /^[A-Za-z0-9_-]{43,128}$/;
const GUEST_FRAGMENT_PREFIX = "#collab_guest=";

export type CollaborationGuestRoute = {
  mode: "collaboration_guest";
  token: string | null;
};

export function isValidCollaborationGuestBearer(value: unknown): value is string {
  return typeof value === "string" && GUEST_BEARER_PATTERN.test(value);
}

function parseCanonicalOrigin(value: string): URL | null {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }

  const isSecureOrigin = parsed.protocol === "https:";
  const isLoopbackHttp =
    parsed.protocol === "http:" &&
    (parsed.hostname === "localhost" ||
      parsed.hostname === "127.0.0.1" ||
      parsed.hostname === "[::1]");

  if (
    (!isSecureOrigin && !isLoopbackHttp) ||
    parsed.username !== "" ||
    parsed.password !== "" ||
    parsed.pathname !== "/" ||
    parsed.search !== "" ||
    parsed.hash !== ""
  ) {
    return null;
  }

  return parsed;
}

export function buildCollaborationGuestInviteLink(
  rawInvitationToken: unknown,
  origin: unknown,
): string | null {
  if (
    !isValidCollaborationGuestBearer(rawInvitationToken) ||
    typeof origin !== "string"
  ) {
    return null;
  }

  const parsedOrigin = parseCanonicalOrigin(origin);
  if (!parsedOrigin) {
    return null;
  }

  return `${parsedOrigin.origin}/${GUEST_FRAGMENT_PREFIX}${rawInvitationToken}`;
}

export function parseCollaborationGuestRoute(
  hash: unknown,
  search: unknown = "",
): CollaborationGuestRoute | null {
  if (
    typeof hash !== "string" ||
    typeof search !== "string" ||
    search !== "" ||
    !hash.startsWith(GUEST_FRAGMENT_PREFIX)
  ) {
    return null;
  }

  const token = hash.slice(GUEST_FRAGMENT_PREFIX.length);
  if (!isValidCollaborationGuestBearer(token)) {
    return null;
  }

  return { mode: "collaboration_guest", token };
}

export function parseCollaborationGuestEntryRoute(
  hash: unknown,
  search: unknown = "",
): CollaborationGuestRoute | null {
  const invitationRoute = parseCollaborationGuestRoute(hash, search);
  if (invitationRoute) {
    return invitationRoute;
  }
  return hash === "#collab_guest" && search === ""
    ? { mode: "collaboration_guest", token: null }
    : null;
}
