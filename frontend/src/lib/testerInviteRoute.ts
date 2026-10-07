export const TESTER_INVITE_PARAMETER = "tester_invite";
export const TESTER_INVITE_TOKEN_PATTERN =
  /^tsti_[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}$/;

export type TesterInviteRoute = { inviteToken: string | null };

type TesterInviteLocation = Pick<Location, "pathname" | "search" | "hash">;
type TesterInviteHistory = Pick<History, "replaceState">;

function fragmentParameters(hash: string) {
  return new URLSearchParams(hash.replace(/^#\??/, ""));
}

export function consumeTesterInviteRoute(
  location: TesterInviteLocation = window.location,
  history: TesterInviteHistory = window.history,
): TesterInviteRoute | null {
  const query = new URLSearchParams(location.search);
  const fragment = fragmentParameters(location.hash);
  const queryTokens = query.getAll(TESTER_INVITE_PARAMETER);
  const fragmentTokens = fragment.getAll(TESTER_INVITE_PARAMETER);
  if (queryTokens.length === 0 && fragmentTokens.length === 0) return null;

  query.delete(TESTER_INVITE_PARAMETER);
  fragment.delete(TESTER_INVITE_PARAMETER);
  const nextSearch = query.toString();
  const nextHash = fragment.toString();
  history.replaceState(
    null,
    "",
    location.pathname +
      (nextSearch ? "?" + nextSearch : "") +
      (nextHash ? "#" + nextHash : ""),
  );

  const originalFragmentNames = [...fragmentParameters(location.hash).keys()];
  const canonical =
    location.pathname === "/" &&
    queryTokens.length === 0 &&
    fragmentTokens.length === 1 &&
    originalFragmentNames.length === 1 &&
    originalFragmentNames[0] === TESTER_INVITE_PARAMETER;

  if (!canonical) return { inviteToken: null };
  const token = fragmentTokens[0] ?? "";
  return { inviteToken: TESTER_INVITE_TOKEN_PATTERN.test(token) ? token : null };
}
