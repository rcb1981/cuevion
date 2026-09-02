from __future__ import annotations

if __name__ != "api.collaboration.guest_http":
    raise ImportError(
        "Collaboration helpers must be imported as api.collaboration.guest_http"
    )

import os
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

from . import application, guest_rate_limit, guest_session
from .http_adapter import (
    PublicResponse,
    extract_raw_headers,
    json_failure,
    json_rate_limited,
    json_success,
    read_json_object,
    validate_no_body_request,
    with_set_cookie,
)
from .http_boundary import BoundaryError
from .models import (
    MAX_V2_MESSAGES,
    MAX_V2_MESSAGE_TEXT,
    MAX_V2_SOURCE_BODY,
    _v2_bounded_string,
    _v2_free_text,
    is_v2_opaque_id,
)


GUEST_HTTP_MODE_ENVIRONMENT_NAME = "CUEVION_COLLAB_V2_GUEST_HTTP_MODE"
GUEST_HTTP_MODE_ACTIVE = "guest_on"
GUEST_ENDPOINT_PATH = "/api/collaboration/guest"
MAX_GUEST_REQUEST_BYTES = 32_768
_POST_FIELDS = frozenset({"operation", "token", "displayName", "text"})
_SESSION_FIELDS = frozenset(
    {
        "collaborationId",
        "guestDisplayName",
        "allowedActions",
        "identityAssurance",
        "expiresAt",
    }
)
_COLLABORATION_FIELDS = frozenset(
    {
        "collaborationId",
        "state",
        "updatedAt",
        "allowedActions",
        "sharedSource",
        "messages",
    }
)
_SOURCE_FIELDS = frozenset(
    {"subject", "senderDisplay", "fromDisplay", "timestamp", "bodyText"}
)
_MESSAGE_FIELDS = frozenset(
    {"id", "authorDisplayName", "authorRole", "text", "timestamp"}
)


def parse_guest_http_mode(value: object) -> str:
    return GUEST_HTTP_MODE_ACTIVE if type(value) is str and value == GUEST_HTTP_MODE_ACTIVE else "off"


def parse_guest_http_mode_mapping(values: object) -> str:
    if not isinstance(values, Mapping):
        return "off"
    try:
        value = values.get(GUEST_HTTP_MODE_ENVIRONMENT_NAME)
    except Exception:
        return "off"
    return parse_guest_http_mode(value)


def _trusted_snapshot(
    environment: Mapping[str, str],
    names: tuple[str, ...],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in names:
        try:
            value = environment.get(name)
        except Exception:
            value = None
        if type(value) is str:
            result[name] = value
    return result


def _parse_security_configuration(
    environment: Mapping[str, str],
) -> tuple[object, object] | None:
    if not guest_session.guest_origin_configuration_is_valid(environment):
        return None
    try:
        csrf_configuration = guest_session.parse_guest_csrf_configuration(
            _trusted_snapshot(
                environment,
                guest_session.GUEST_CSRF_CONFIGURATION_NAMES,
            )
        )
        rate_configuration = guest_rate_limit.parse_guest_rate_limit_configuration(
            _trusted_snapshot(
                environment,
                guest_rate_limit.RATE_LIMIT_CONFIGURATION_NAMES,
            )
        )
    except Exception:
        return None
    return csrf_configuration, rate_configuration


def _exact_endpoint(request: object) -> None:
    try:
        target = request.path  # type: ignore[attr-defined]
    except Exception:
        raise BoundaryError("invalid_value", 400) from None
    if type(target) is not str:
        raise BoundaryError("invalid_value", 400)
    try:
        parsed = urlsplit(target)
    except ValueError:
        raise BoundaryError("invalid_value", 400) from None
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.path != GUEST_ENDPOINT_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise BoundaryError("invalid_value", 400)


def _exact_fields(payload: dict, expected: frozenset[str]) -> None:
    if type(payload) is not dict or set(payload) != expected:
        raise BoundaryError("invalid_json_fields", 400)


def _guest_failure(result: object) -> PublicResponse:
    code = None
    if type(result) is dict and type(result.get("error")) is dict:
        code = result["error"].get("code")
    mapping = {
        "invalid_request": (400, "invalid_request"),
        "invite_not_found": (404, "invitation_invalid"),
        "invite_expired": (410, "invitation_expired"),
        "invite_revoked": (410, "invitation_revoked"),
        "invite_already_exchanged": (409, "invitation_already_exchanged"),
        "session_not_found": (401, "session_missing"),
        "session_expired": (401, "session_expired"),
        "session_revoked": (401, "session_revoked"),
        "csrf_failed": (403, "csrf_failed"),
        "origin_rejected": (403, "origin_rejected"),
        "storage_unavailable": (503, "service_unavailable"),
        "atomic_exchange_unavailable": (503, "service_unavailable"),
        "index_hmac_unavailable": (503, "service_unavailable"),
        "forbidden": (401, "session_revoked"),
        "collaboration_not_found": (401, "session_revoked"),
        "already_logged_out": (401, "session_revoked"),
        "stale_thread": (409, "conflict"),
    }
    status, public_code = mapping.get(code, (500, "internal_error"))
    return json_failure(public_code, status=status)


def _safe_session(value: object) -> dict | None:
    if type(value) is not dict or set(value) != _SESSION_FIELDS:
        return None
    if (
        not is_v2_opaque_id(value.get("collaborationId"))
        or _v2_bounded_string(value.get("guestDisplayName"), max_length=256)
        != value.get("guestDisplayName")
        or value.get("allowedActions") != ["read", "reply"]
        or value.get("identityAssurance") != "link_possession"
        or type(value.get("expiresAt")) is not int
    ):
        return None
    return {
        "collaborationId": value["collaborationId"],
        "guestDisplayName": value["guestDisplayName"],
        "allowedActions": ["read", "reply"],
        "identityAssurance": "link_possession",
        "expiresAt": value["expiresAt"],
    }


def _safe_guest_collaboration(value: object) -> dict | None:
    if type(value) is not dict or set(value) != _COLLABORATION_FIELDS:
        return None
    source = value.get("sharedSource")
    messages = value.get("messages")
    if (
        not is_v2_opaque_id(value.get("collaborationId"))
        or value.get("state")
        not in {"needs_review", "needs_action", "note_only", "resolved"}
        or type(value.get("updatedAt")) is not int
        or value.get("allowedActions") != ["read", "reply"]
        or type(source) is not dict
        or set(source) != _SOURCE_FIELDS
        or type(messages) is not list
        or len(messages) > MAX_V2_MESSAGES
    ):
        return None
    if (
        _v2_bounded_string(source.get("subject"), max_length=998, allow_empty=True)
        != source.get("subject")
        or _v2_bounded_string(source.get("senderDisplay"), max_length=512, allow_empty=True)
        != source.get("senderDisplay")
        or _v2_bounded_string(source.get("fromDisplay"), max_length=512, allow_empty=True)
        != source.get("fromDisplay")
        or _v2_bounded_string(source.get("timestamp"), max_length=128, allow_empty=True)
        != source.get("timestamp")
        or _v2_free_text(source.get("bodyText"), max_length=MAX_V2_SOURCE_BODY)
        != source.get("bodyText")
    ):
        return None
    safe_messages: list[dict] = []
    for message in messages:
        if (
            type(message) is not dict
            or set(message) != _MESSAGE_FIELDS
            or not is_v2_opaque_id(message.get("id"))
            or _v2_bounded_string(message.get("authorDisplayName"), max_length=256)
            != message.get("authorDisplayName")
            or message.get("authorRole")
            not in {"Cuevion user", "Guest reviewer", "System"}
            or _v2_free_text(message.get("text"), max_length=MAX_V2_MESSAGE_TEXT)
            != message.get("text")
            or type(message.get("timestamp")) is not int
        ):
            return None
        safe_messages.append(dict(message))
    return {
        "collaborationId": value["collaborationId"],
        "state": value["state"],
        "updatedAt": value["updatedAt"],
        "allowedActions": ["read", "reply"],
        "sharedSource": dict(source),
        "messages": safe_messages,
    }


def _cookie_result(raw_headers: object) -> tuple[str | None, PublicResponse | None]:
    result = guest_session.resolve_guest_session_cookie(raw_headers)
    if result.get("status") != "ok":
        return None, _guest_failure(result)
    raw_session_id = result.get("sessionId")
    if not guest_session.is_v2_guest_bearer(raw_session_id):
        return None, json_failure("session_missing", status=401)
    return raw_session_id, None


def _limited_response(
    raw_bearer: str,
    rate_class: str,
    configuration: object,
    *,
    command_transport=None,
) -> PublicResponse | None:
    decision = guest_rate_limit.consume_guest_rate_limit(
        raw_bearer,
        rate_class,
        configuration,
        command_transport=command_transport,
    )
    if decision.status == "allowed":
        return None
    if decision.status == "limited" and decision.retry_after_seconds is not None:
        return json_rate_limited(decision.retry_after_seconds)
    return json_failure("service_unavailable", status=503)


def _origin_failure(
    raw_headers: object,
    environment: Mapping[str, str],
) -> PublicResponse | None:
    result = guest_session.validate_guest_request_origin(
        raw_headers,
        environment=environment,
    )
    return None if result.get("status") == "ok" else _guest_failure(result)


def _post_response(
    request: object,
    raw_headers: tuple[tuple[str, str], ...],
    *,
    environment: Mapping[str, str],
    now: int,
    csrf_configuration: object,
    rate_configuration: object,
    command_transport=None,
) -> PublicResponse:
    origin_failure = _origin_failure(raw_headers, environment)
    if origin_failure is not None:
        return origin_failure
    payload = read_json_object(
        request,
        maximum_bytes=MAX_GUEST_REQUEST_BYTES,
        allowed_fields=_POST_FIELDS,
        required_fields={"operation"},
    )
    operation = payload.get("operation")
    if type(operation) is not str:
        raise BoundaryError("invalid_value", 400)

    if operation == "exchange":
        _exact_fields(payload, frozenset({"operation", "token", "displayName"}))
        raw_token = payload.get("token")
        display_name = payload.get("displayName")
        if (
            not guest_session.is_v2_guest_bearer(raw_token)
            or _v2_bounded_string(display_name, max_length=256) != display_name
        ):
            raise BoundaryError("invalid_value", 400)
        limited = _limited_response(
            raw_token,
            guest_rate_limit.RATE_LIMIT_EXCHANGE,
            rate_configuration,
            command_transport=command_transport,
        )
        if limited is not None:
            return limited
        result = guest_session.exchange_v2_invitation(
            raw_token,
            guest_display_name=display_name,
            now=now,
            command_transport=command_transport,
            csrf_token_deriver=lambda raw_session_id: guest_session.derive_guest_csrf_token(
                raw_session_id,
                csrf_configuration,
            ),
        )
        if result.get("status") != "ok":
            return _guest_failure(result)
        session = _safe_session(result.get("session"))
        raw_session_id = result.get("sessionId")
        csrf_token = result.get("csrfToken")
        if (
            session is None
            or not guest_session.is_v2_guest_bearer(raw_session_id)
            or not guest_session.is_v2_guest_bearer(csrf_token)
        ):
            return json_failure("internal_error", status=500)
        cookie = guest_session.build_guest_session_cookie(
            raw_session_id,
            expires_at=session["expiresAt"],
            now=now,
            environment=environment,
        )
        if cookie is None:
            return json_failure("internal_error", status=500)
        return with_set_cookie(
            json_success({"session": session, "csrfToken": csrf_token}),
            cookie,
        )

    if operation == "bootstrap":
        _exact_fields(payload, frozenset({"operation"}))
        raw_session_id, cookie_failure = _cookie_result(raw_headers)
        if cookie_failure is not None:
            return cookie_failure
        if raw_session_id is None:
            return json_failure("internal_error", status=500)
        limited = _limited_response(
            raw_session_id,
            guest_rate_limit.RATE_LIMIT_BOOTSTRAP,
            rate_configuration,
            command_transport=command_transport,
        )
        if limited is not None:
            return limited
        result = guest_session.bootstrap_v2_guest_session(
            raw_session_id,
            csrf_token_deriver=lambda session_id: guest_session.derive_guest_csrf_token(
                session_id,
                csrf_configuration,
            ),
            now=now,
            command_transport=command_transport,
        )
        if result.get("status") != "ok":
            return _guest_failure(result)
        session = _safe_session(result.get("session"))
        csrf_token = result.get("csrfToken")
        if session is None or not guest_session.is_v2_guest_bearer(csrf_token):
            return json_failure("internal_error", status=500)
        return json_success({"session": session, "csrfToken": csrf_token})

    if operation == "reply":
        _exact_fields(payload, frozenset({"operation", "text"}))
        text = payload.get("text")
        if _v2_free_text(text, max_length=MAX_V2_MESSAGE_TEXT) != text:
            raise BoundaryError("invalid_value", 400)
        raw_session_id, cookie_failure = _cookie_result(raw_headers)
        if cookie_failure is not None:
            return cookie_failure
        if raw_session_id is None:
            return json_failure("internal_error", status=500)
        limited = _limited_response(
            raw_session_id,
            guest_rate_limit.RATE_LIMIT_REPLY,
            rate_configuration,
            command_transport=command_transport,
        )
        if limited is not None:
            return limited
        result = application.append_v2_shared_reply_for_guest(
            raw_headers,
            text,
            now=now,
            command_transport=command_transport,
            environment=environment,
        )
        if result.get("status") != "ok":
            return _guest_failure(result)
        collaboration = _safe_guest_collaboration(result.get("collaboration"))
        if collaboration is None:
            return json_failure("internal_error", status=500)
        return json_success({"collaboration": collaboration})

    if operation == "logout":
        _exact_fields(payload, frozenset({"operation"}))
        raw_session_id, cookie_failure = _cookie_result(raw_headers)
        if cookie_failure is not None:
            return cookie_failure
        if raw_session_id is None:
            return json_failure("internal_error", status=500)
        limited = _limited_response(
            raw_session_id,
            guest_rate_limit.RATE_LIMIT_LOGOUT,
            rate_configuration,
            command_transport=command_transport,
        )
        if limited is not None:
            return limited
        resolved = guest_session.resolve_guest_v2_mutation_context(
            "POST",
            raw_headers,
            now=now,
            command_transport=command_transport,
            environment=environment,
        )
        if resolved.get("status") != "ok":
            return _guest_failure(resolved)
        result = guest_session.logout_v2_guest_session(
            resolved.get("context"),
            now=now,
            command_transport=command_transport,
        )
        if result.get("status") not in {"ok", "already_logged_out"}:
            return _guest_failure(result)
        return with_set_cookie(
            json_success({"loggedOut": True}),
            guest_session.clear_guest_session_cookie(environment=environment),
        )

    raise BoundaryError("invalid_value", 400)


def guest_response(
    request: object,
    *,
    http_mode: str,
    environment: Mapping[str, str] | None = None,
    now: int | None = None,
    command_transport=None,
) -> PublicResponse:
    if http_mode != GUEST_HTTP_MODE_ACTIVE:
        return json_failure("not_found", status=404)
    _exact_endpoint(request)
    try:
        method = request.command  # type: ignore[attr-defined]
    except Exception:
        raise BoundaryError("method_not_allowed", 405) from None
    if type(method) is not str or method not in {"GET", "POST"}:
        raise BoundaryError("method_not_allowed", 405)
    source = os.environ if environment is None else environment
    timestamp = int(time.time()) if now is None else now
    if type(timestamp) is not int:
        return json_failure("service_unavailable", status=503)
    security_configuration = _parse_security_configuration(source)
    if security_configuration is None:
        return json_failure("service_unavailable", status=503)
    csrf_configuration, rate_configuration = security_configuration
    raw_headers = extract_raw_headers(request)

    if method == "POST":
        return _post_response(
            request,
            raw_headers,
            environment=source,
            now=timestamp,
            csrf_configuration=csrf_configuration,
            rate_configuration=rate_configuration,
            command_transport=command_transport,
        )

    validate_no_body_request(request)
    raw_session_id, cookie_failure = _cookie_result(raw_headers)
    if cookie_failure is not None:
        return cookie_failure
    if raw_session_id is None:
        return json_failure("internal_error", status=500)
    limited = _limited_response(
        raw_session_id,
        guest_rate_limit.RATE_LIMIT_READ,
        rate_configuration,
        command_transport=command_transport,
    )
    if limited is not None:
        return limited
    result = (
        application.read_v2_collaboration_for_guest(raw_headers)
        if command_transport is None
        else application._read_v2_collaboration_for_guest(
            raw_headers,
            now=timestamp,
            command_transport=command_transport,
        )
    )
    if result.get("status") != "ok":
        return _guest_failure(result)
    collaboration = _safe_guest_collaboration(result.get("collaboration"))
    if collaboration is None:
        return json_failure("internal_error", status=500)
    return json_success({"collaboration": collaboration})


__all__ = (
    "GUEST_ENDPOINT_PATH",
    "GUEST_HTTP_MODE_ACTIVE",
    "GUEST_HTTP_MODE_ENVIRONMENT_NAME",
    "MAX_GUEST_REQUEST_BYTES",
    "guest_response",
    "parse_guest_http_mode",
    "parse_guest_http_mode_mapping",
)
