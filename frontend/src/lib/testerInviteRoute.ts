export const TESTER_INVITE_PARAMETER = "tester_invite";
export const TESTER_INVITE_TOKEN_PATTERN =
  /^tsti_[A-Za-z0-9_-]{1,64}\.[A-Za-z0-9_-]{43}$/;

export type TesterInviteRoute = {
  inviteToken: string;
};

type TesterInviteLocation = Pick<Location, "pathname" | "search" | "hash">;
type TesterInviteHistory = Pick<History, "replaceState">;

function scrubTesterInviteCredential(
  location: TesterInviteLocation,
  history: TesterInviteHistory,
) {
  const query = new URLSearchParams(location.search);
  const fragment = new URLSearchParams(location.hash.replace(/^#\??/, ""));
  query.delete(TESTER_INVITE_PARAMETER);
  fragment.delete(TESTER_INVITE_PARAMETER);

  const nextSearch = query.toString();
  const nextHash = fragment.toString();
  const scrubbedUrl = `${location.pathname}${
    nextSearch ? `?${nextSearch}` : ""
  }${nextHash ? `#${nextHash}` : ""}`;

  history.replaceState(null, "", scrubbedUrl);
}

/**
 * Consume a standalone tester invite from fragment-only transport.
 *
 * The raw bearer must be removed from the visible URL synchronously and is
 * returned only in memory. Query-string transport is rejected because query
 * credentials can reach HTTP access logs and Referer surfaces.
 */
export function consumeTesterInviteRoute(
  location: TesterInviteLocation =
    window.location,
  history: TesterInviteHistory =
    window.history,
): TesterInviteRoute | null {
  const query = new URLSearchParams(location.search);
  const fragment = new URLSearchParams(location.hash.replace(/^#\??/, ""));
  const queryTokens = query.getAll(TESTER_INVITE_PARAMETER);
  const fragmentTokens = fragment.getAll(TESTER_INVITE_PARAMETER);
  const hasCredential = queryTokens.length > 0 || fragmentTokens.length > 0;

  if (!hasCredential) {
    return null;
  }

  scrubTesterInviteCredential(location, history);

  const parameterNames = [...fragment.keys()];
  const isCanonicalFragment =
    location.pathname === "/" &&
    queryTokens.length === 0 &&
    fragmentTokens.length === 1 &&
    parameterNames.length === 1 &&
    parameterNames[0] === TESTER_INVITE_PARAMETER;

  if (!isCanonicalFragment) {
    return { inviteToken: "" };
  }

  const inviteToken = fragmentTokens[0] ?? "";
  return {
    inviteToken: TESTER_INVITE_TOKEN_PATTERN.test(inviteToken)
      ? inviteToken
      : "",
  };
}
