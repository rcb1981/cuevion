# C3C neutral HTTP authority: pre-edit trace

Recorded before implementing the notification HTTP boundary.

- `api/collaboration/owner.py:handler._respond` selects the Collaboration owner
  rollout modes. `api/collaboration/owner_http.py:owner_response` then checks
  `owner_is_allowlisted` before its CSRF/read operations. This route cannot serve
  arbitrary current Team users or future authenticated testers.
- The new route is `POST /api/notifications`. It does not reuse the owner rollout
  allowlist or mailbox permissions. Notification identity is the current
  authenticated `usr_*` plus the session's current canonical `wsp_*`.
- `api/auth/runtime.py:resolve_authenticated_member_session` loads the existing
  server session and revalidates the current account graph on every request.
  `_current_authority_member_context` verifies active user, primary verified
  email, active workspace, active exact workspace membership, and security epoch.
  Its `AuthenticatedMemberContext` supplies canonical user ID, workspace ID, name,
  and current workspace role. Guest web cookies never supply this context.
- `api/team/authority.py:resolve_active_member_by_user_id` resolves active Team
  entitlement by an exact user pointer and membership (two bounded GETs). For a
  non-owner workspace member the new read boundary will revalidate this current
  Team entitlement, consistent with
  `api/collaboration/authorization.py:_resolve_active_team_member`. Owner role is
  the exact `api/auth/models.py:WorkspaceRole.OWNER` value `owner`.
- `api/auth/http.py` supplies duplicate-safe header snapshots, exact canonical
  Host and Origin checks, fixed errors, and no-store/nosniff responses.
  `api/team/http_security.py:require_safe_json_mutation` is the existing generic
  browser CSRF policy: exact trusted Origin/Host and non-simple
  `application/json` POST. The new route pins canonical Host and Origin before
  calling it, so an unset development origin cannot become caller authority.
  This generic policy does not issue or require an owner-only CSRF token.
- `api/collaboration/http_adapter.py:read_json_object` and
  `api/collaboration/http_boundary.py:parse_json_object` provide bounded body
  framing, strict UTF-8, duplicate-key rejection and closed JSON fields. The new
  route will use a 2,048-byte limit and accept only the relevant operation fields.
- `api/collaboration/owner_rate_limit.py` supplies the existing GCRA Redis Lua
  limiter, validated distinct rate-limit HMAC configuration, server TIME and
  expiring bounded state. Notifications reuse that algorithm/root with a new
  notification domain and distinct summary/list/mark_read purposes keyed by
  canonical user and workspace. No migration/operator limiter is reused.

The endpoint owns no frontend rendering, polling, click routing, sidebar count,
or localStorage read-state behavior; those remain C3D.

## Recipient authority review and implementation

The subsequent bounded review traced
`api/collaboration/application.py:_build_verified_thread_dto`: visible participants
must still resolve through current Team authority with
`sourceInvitationId == membershipRef`. Raw historical thread participant IDs
alone are insufficient, because a removed and re-invited account receives a new
Team grant. `notifications/recipients.py:resolve_notification_recipients` now
performs this same current check before a message's Redis mutation. It preserves
the owner without a Team-enrollment lookup, deduplicates recipients, excludes the
canonical actor, skips inactive/replaced grants, and fails closed on authority
outages. Legacy records without `ownerUserId` produce no inferred recipients.
The Redis mutation revalidates the intended IDs against its canonical thread;
the accepted external-Team-check/Redis-commit race remains unchanged.

Verified rate policies are 60/minute with burst 20 separately for `summary` and
`list`, and 30/minute with burst 10 for `mark_read`, per user/workspace/purpose.
An isolated local Redis measurement found one outer EVAL per limiter request,
three nested commands for a fresh allowed bucket (TIME, GET, SET), and four for
an existing allowed bucket (additional PTTL). Denied requests do not write.
