"""Neutral, current-account notification reads and exact read-state writes."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping

from api.auth import http as security, models as account_models, runtime
from api.collaboration import http_adapter, http_boundary
from api.collaboration.models import normalize_v2_user_id, normalize_v2_workspace_id
from api.team import authority as team_authority
from api.team.http_security import require_safe_json_mutation

from . import models, rate_limit, store


MAX_REQUEST_BYTES = 2_048
MAX_CURSOR_BYTES = 1_024
_REQUEST_FIELDS = frozenset({"operation", "limit", "cursor", "notificationId"})


def _failure(status: int, code: str, *, cookies=(), retry_after=None):
    extras = (("Allow", "POST"),) if status == 405 else ()
    if retry_after is not None:
        extras = (("Retry-After", str(retry_after)),)
    return security.json_response(
        status,
        {"error": {"code": code, "message": "The notification request could not be completed."}},
        set_cookies=cookies, extra_headers=extras,
    )


def _read_payload(request, headers) -> dict:
    size = http_adapter.preflight_content_length(
        headers, maximum_bytes=MAX_REQUEST_BYTES, required=True,
    )
    body = request.rfile.read(size)
    checked = http_boundary.require_bounded_body(
        headers, body, maximum_bytes=MAX_REQUEST_BYTES,
    )
    return http_boundary.parse_json_object(
        http_boundary.decode_strict_utf8(checked),
        allowed_fields=_REQUEST_FIELDS, required_fields={"operation"},
        reject_numbers=False,
    )


def _valid_request(payload: dict) -> bool:
    operation = payload.get("operation")
    if type(operation) is not str:
        return False
    if operation == "summary":
        return set(payload) == {"operation"}
    if operation == "mark_read":
        return (set(payload) == {"operation", "notificationId"}
                and models.is_notification_id(payload.get("notificationId")))
    if operation == "list":
        limit = payload.get("limit", models.MAX_NOTIFICATION_PAGE_SIZE)
        cursor = payload.get("cursor")
        return (
            set(payload).issubset({"operation", "limit", "cursor"})
            and type(limit) is int and 1 <= limit <= models.MAX_NOTIFICATION_PAGE_SIZE
            and (cursor is None or (type(cursor) is str and cursor.isascii()
                 and 1 <= len(cursor) <= MAX_CURSOR_BYTES))
        )
    return False


def _team_failure(member, environment):
    if member.membership_role == account_models.WorkspaceRole.OWNER.value:
        return None
    if member.membership_role not in {
        account_models.WorkspaceRole.ADMIN.value,
        account_models.WorkspaceRole.MEMBER.value,
    }:
        return _failure(403, "forbidden")
    membership, error = team_authority.build_runtime_team_authority(
        environment,
    ).resolve_active_member_by_user_id(
        workspace_id=member.workspace_id, member_user_id=member.user_id,
    )
    if error is not None:
        code = error.get("code") if type(error) is dict else None
        return _failure(
            403 if code == "team_member_not_active" else 503,
            "forbidden" if code == "team_member_not_active" else "service_unavailable",
        )
    if (type(membership) is not dict
            or membership.get("memberUserId") != member.user_id):
        return _failure(503, "service_unavailable")
    return None


def _public_success(operation: str, result: object, limit: int, cursor, member, requested_id):
    if (type(result) is not dict or result.get("v") != 1
            or type(result.get("v")) is not int
            or type(result.get("unreadCount")) is not int
            or not 0 <= result["unreadCount"] <= 1_000):
        return _failure(503, "service_unavailable")
    payload = {"v": 1, "unreadCount": result["unreadCount"]}
    if operation == "summary":
        return security.json_response(200, payload)
    if operation == "mark_read":
        notification = models.normalize_notification_dto(result.get("notification"))
        if (notification is None or notification.get("readAt") is None
                or notification["workspaceId"] != member.workspace_id
                or notification["notificationId"] != requested_id):
            return _failure(503, "service_unavailable")
        payload["notification"] = notification
    else:
        rows = result.get("notifications")
        next_cursor = result.get("nextCursor")
        if (type(rows) is not list or len(rows) > limit
                or (next_cursor is not None and (
                    type(next_cursor) is not str or not next_cursor.isascii()
                    or not 1 <= len(next_cursor) <= MAX_CURSOR_BYTES
                    or next_cursor == cursor))):
            return _failure(503, "service_unavailable")
        notifications = [models.normalize_notification_dto(row) for row in rows]
        if (any(row is None or row["workspaceId"] != member.workspace_id
                for row in notifications)
                or (next_cursor is not None and not notifications)):
            return _failure(503, "service_unavailable")
        if len({row["notificationId"] for row in notifications}) != len(notifications):
            return _failure(503, "service_unavailable")
        payload.update(notifications=notifications, nextCursor=next_cursor)
    return security.json_response(200, payload)


def notifications_response(
    request: object, *, environment: Mapping[str, str] | None = None,
    now: int | None = None,
) -> security.PublicResponse:
    source = os.environ if environment is None else environment
    timestamp = int(time.time()) if now is None else now
    try:
        security.require_method(request.command, "POST")
        if request.path != "/api/notifications":
            return _failure(400, "invalid_request")
        headers = security.snapshot_request_headers(request)
        security.require_canonical_host(headers)
        security.require_same_origin(headers)
        # Reuse the app's generic non-simple JSON CSRF contract, after pinning
        # the origin independently of environment/development fallbacks.
        require_safe_json_mutation(headers, source)
        resolution = runtime.resolve_authenticated_member(
            headers, environment=source, now=timestamp,
        )
        if type(resolution) is not runtime.AuthenticatedMemberResolution:
            return _failure(503, "service_unavailable")
        if resolution.outcome is runtime.MemberResolutionOutcome.UNAUTHENTICATED:
            return _failure(401, "unauthorized", cookies=resolution.set_cookies)
        if resolution.outcome is not runtime.MemberResolutionOutcome.AUTHENTICATED:
            return _failure(503, "service_unavailable", cookies=resolution.set_cookies)
        member = resolution.member
        if (type(member) is not runtime.AuthenticatedMemberContext
                or normalize_v2_user_id(member.user_id) != member.user_id
                or normalize_v2_workspace_id(member.workspace_id) != member.workspace_id):
            return _failure(503, "service_unavailable")
        rejected = _team_failure(member, source)
        if rejected is not None:
            return rejected
        payload = _read_payload(request, headers)
        if not _valid_request(payload):
            return _failure(400, "invalid_request")
        operation = payload["operation"]
        decision = rate_limit.consume_notification_rate_limit(
            member, operation, environment=source,
        )
        if decision.status == "limited":
            return _failure(429, "rate_limited", retry_after=decision.retry_after_seconds)
        if decision.status != "allowed":
            return _failure(503, "service_unavailable")
        limit = payload.get("limit", models.MAX_NOTIFICATION_PAGE_SIZE)
        cursor = payload.get("cursor")
        if operation == "summary":
            result = store.summary(member.workspace_id, member.user_id)
        elif operation == "list":
            result = store.list_notifications(
                member.workspace_id, member.user_id, limit=limit, cursor=cursor,
            )
        else:
            result = store.mark_read(
                member.workspace_id, member.user_id, payload["notificationId"],
            )
        status = result.get("status") if type(result) is dict else None
        if status == "malformed":
            return _failure(400, "invalid_request")
        if status == "not_found":
            return _failure(404, "not_found")
        if status != "ok":
            return _failure(503, "service_unavailable")
        return _public_success(
            operation, result, limit, cursor, member, payload.get("notificationId"),
        )
    except (security.HttpBoundaryError, http_boundary.BoundaryError) as error:
        status = error.status if error.status in {400, 403, 405, 413, 415} else 400
        code = {
            400: "invalid_request", 403: "forbidden", 405: "method_not_allowed",
            413: "payload_too_large", 415: "unsupported_media_type",
        }[status]
        return _failure(status, code)
    except Exception:
        return _failure(503, "service_unavailable")
