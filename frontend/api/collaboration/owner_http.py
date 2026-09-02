from __future__ import annotations

if __name__ != "api.collaboration.owner_http":
    raise ImportError(
        "Collaboration helpers must be imported as api.collaboration.owner_http"
    )

import json
import os
import time
from collections.abc import Mapping

from . import application, owner_rate_limit
from .http_adapter import (
    PublicResponse,
    extract_raw_headers,
    json_failure,
    json_rate_limited,
    json_success,
    read_json_object,
    require_request_method,
)
from .http_boundary import BoundaryError, get_security_header
from .models import (
    MAX_V2_TIMESTAMP_SECONDS,
    MIN_V2_TIMESTAMP_SECONDS,
    hash_v2_secret,
    is_v2_opaque_id,
    normalize_v2_email,
    normalize_v2_external_guest_projection,
    normalize_v2_owner_idempotency_key,
)
from .owner_authentication import resolve_verified_auth0_owner
from .owner_request_security import (
    OwnerSecurityError,
    issue_owner_csrf_token,
    normalize_owner_security_failure,
    owner_is_allowlisted,
    parse_owner_csrf_header,
    parse_owner_security_configuration,
    resolve_owner_request_context,
    validate_owner_mutation_origin,
    verify_owner_csrf_token,
)


MAX_OWNER_REQUEST_BYTES = 131_072
_OWNER_BODY_FIELDS = frozenset(
    {
        "operation",
        "collaborationId",
        "mailboxId",
        "sourceRef",
        "state",
        "text",
        "participantUserId",
        "invitedEmail",
        "inviteId",
    }
)
_SECURITY_CONFIGURATION_NAMES = (
    "CUEVION_APP_ORIGIN",
    "CUEVION_COLLAB_V2_OWNER_CSRF_KEY",
    "CUEVION_COLLAB_V2_OWNER_CSRF_KEY_PREVIOUS",
    "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY",
    "CUEVION_COLLAB_V2_OWNER_ALLOWLIST",
    "CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST",
)
_APPLICATION_FAILURES = {
    "auth_required": (401, "unauthorized"),
    "invalid_request": (400, "invalid_request"),
    "collaboration_not_found": (404, "not_found"),
    "mailbox_not_found": (404, "not_found"),
    "source_not_found": (404, "not_found"),
    "forbidden": (404, "not_found"),
    "source_changed": (409, "conflict"),
    "stale_thread": (409, "conflict"),
    "stale_invitation": (409, "conflict"),
    "idempotency_conflict": (409, "conflict"),
    "guest_capacity_reached": (409, "conflict"),
    "invite_expired": (409, "conflict"),
    "invite_revoked": (409, "conflict"),
    "already_revoked": (409, "conflict"),
    "invite_not_found": (404, "not_found"),
    "storage_unavailable": (503, "service_unavailable"),
    "storage_protocol_error": (503, "service_unavailable"),
    "index_hmac_unavailable": (503, "service_unavailable"),
    "provider_unavailable": (503, "service_unavailable"),
    "atomic_exchange_unavailable": (503, "service_unavailable"),
}
_OWNER_APPLICATION_FAILURE_EVENT = (
    "cuevion_collaboration_owner_application_failure"
)
_OWNER_APPLICATION_OPERATIONS = frozenset(
    {
        "csrf",
        "read",
        "lookup",
        "create",
        "create_with_guest",
        "add_participant",
        "append_shared",
        "append_internal",
        "issue_guest_invite",
        "revoke_guest_invite",
    }
)
_UNKNOWN_SAFE_FAILURE = "unknown_safe_failure"


def _trusted_security_snapshot(environment: Mapping[str, str]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    try:
        for name in _SECURITY_CONFIGURATION_NAMES:
            if name in environment:
                snapshot[name] = environment[name]
    except Exception:
        raise OwnerSecurityError("invalid_configuration") from None
    return snapshot


def _trusted_rate_limit_snapshot(environment: Mapping[str, str]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    try:
        for name in owner_rate_limit.RATE_LIMIT_CONFIGURATION_NAMES:
            if name in environment:
                snapshot[name] = environment[name]
    except Exception:
        raise ValueError("invalid owner rate-limit configuration") from None
    return snapshot


def _rate_limit_response(
    context: object,
    rate_class: str,
    configuration: object,
) -> PublicResponse | None:
    try:
        decision = owner_rate_limit.consume_owner_rate_limit(
            context,
            rate_class,
            configuration,
        )
    except Exception:
        return json_failure("service_unavailable", status=503)
    if (
        type(decision) is owner_rate_limit.OwnerRateLimitDecision
        and decision.status == "allowed"
        and decision.retry_after_seconds is None
    ):
        return None
    if (
        type(decision) is owner_rate_limit.OwnerRateLimitDecision
        and decision.status == "limited"
        and type(decision.retry_after_seconds) is int
        and 1 <= decision.retry_after_seconds <= 60
    ):
        return json_rate_limited(decision.retry_after_seconds)
    return json_failure("service_unavailable", status=503)


def _owner_failure(error: OwnerSecurityError) -> PublicResponse:
    status, code = normalize_owner_security_failure(error)
    return json_failure(code, status=status)


def _emit_application_failure_event(
    *,
    operation: str,
    internal_safe_code: str,
    public_status: int,
    public_code: str,
) -> None:
    if (
        operation not in _OWNER_APPLICATION_OPERATIONS
        or (
            internal_safe_code != _UNKNOWN_SAFE_FAILURE
            and internal_safe_code not in _APPLICATION_FAILURES
        )
    ):
        return
    event = {
        "event": _OWNER_APPLICATION_FAILURE_EVENT,
        "operation": operation,
        "internalSafeCode": internal_safe_code,
        "publicStatus": public_status,
        "publicCode": public_code,
    }
    try:
        print(
            json.dumps(
                event,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            flush=True,
        )
    except Exception:
        pass


def _application_failure(result: object, *, operation: str) -> PublicResponse:
    code = None
    if type(result) is dict:
        error = result.get("error")
        if type(error) is dict and set(error) == {"code"}:
            code = error.get("code")
    internal_safe_code = (
        code
        if type(code) is str and code in _APPLICATION_FAILURES
        else _UNKNOWN_SAFE_FAILURE
    )
    status, public_code = _APPLICATION_FAILURES.get(
        internal_safe_code,
        (500, "internal_error"),
    )
    if status >= 500:
        _emit_application_failure_event(
            operation=operation,
            internal_safe_code=internal_safe_code,
            public_status=status,
            public_code=public_code,
        )
    return json_failure(public_code, status=status)


def _require_exact_fields(payload: dict, fields: frozenset[str]) -> None:
    if type(payload) is not dict or set(payload) != fields:
        raise BoundaryError("invalid_json_fields", 400)


def _safe_invitation_metadata(
    value: object,
    *,
    collaboration_id: object,
) -> dict | None:
    required = {
        "inviteId",
        "collaborationId",
        "allowedActions",
        "identityAssurance",
        "expiresAt",
        "status",
    }
    optional = {"invitedEmail"}
    if (
        type(value) is not dict
        or not required <= set(value) <= required | optional
        or not is_v2_opaque_id(value.get("inviteId"))
        or value.get("collaborationId") != collaboration_id
        or value.get("allowedActions") != ["read", "reply"]
        or value.get("identityAssurance") != "link_possession"
        or type(value.get("expiresAt")) is not int
        or not MIN_V2_TIMESTAMP_SECONDS
        <= value["expiresAt"]
        <= MAX_V2_TIMESTAMP_SECONDS
        or value.get("status") not in {"active", "exchanged", "revoked", "expired"}
    ):
        return None
    if "invitedEmail" in value:
        invited_email = normalize_v2_email(value.get("invitedEmail"))
        if invited_email is None or invited_email != value.get("invitedEmail"):
            return None
    return value


def _external_guest_lifecycle(value: object) -> dict | None:
    normalized = normalize_v2_external_guest_projection([value])
    return normalized[0] if normalized == [value] else None


def _create_with_guest_success(result: object) -> dict | None:
    if type(result) is not dict:
        return None
    invitation_created = result.get("invitationCreated")
    expected = (
        {"created", "invitationCreated", "collaboration", "invitation", "token"}
        if invitation_created is True
        else {"created", "invitationCreated", "collaboration", "invitation"}
    )
    collaboration = result.get("collaboration")
    collaboration_id = (
        collaboration.get("collaborationId")
        if type(collaboration) is dict
        else None
    )
    if (
        set(result) != expected
        or type(result.get("created")) is not bool
        or type(invitation_created) is not bool
        or type(collaboration) is not dict
        or not is_v2_opaque_id(collaboration_id)
        or _safe_invitation_metadata(
            result.get("invitation"), collaboration_id=collaboration_id
        )
        is None
        or (invitation_created and hash_v2_secret(result.get("token")) is None)
    ):
        return None
    return result


def _issue_guest_success(result: object) -> dict | None:
    if type(result) is not dict:
        return None
    invitation_created = result.get("invitationCreated")
    expected = (
        {
            "status",
            "invitationCreated",
            "collaboration",
            "invitation",
            "token",
            "error",
        }
        if invitation_created is True
        else {
            "status",
            "invitationCreated",
            "collaboration",
            "invitation",
            "error",
        }
    )
    collaboration = result.get("collaboration")
    collaboration_id = (
        collaboration.get("collaborationId")
        if type(collaboration) is dict
        else None
    )
    if (
        set(result) != expected
        or result.get("status") != "ok"
        or type(invitation_created) is not bool
        or type(collaboration) is not dict
        or not is_v2_opaque_id(collaboration_id)
        or result.get("error") is not None
        or _safe_invitation_metadata(
            result.get("invitation"), collaboration_id=collaboration_id
        )
        is None
        or (invitation_created and hash_v2_secret(result.get("token")) is None)
    ):
        return None
    return {
        key: result[key]
        for key in (
            "invitationCreated",
            "collaboration",
            "invitation",
            *(("token",) if invitation_created else ()),
        )
    }


def _revoke_guest_success(result: object) -> dict | None:
    if (
        type(result) is not dict
        or set(result) != {"status", "collaboration", "invitation", "error"}
        or result.get("status") != "ok"
        or type(result.get("collaboration")) is not dict
        or result.get("error") is not None
        or _external_guest_lifecycle(result.get("invitation")) is None
    ):
        return None
    return {
        "collaboration": result["collaboration"],
        "invitation": result["invitation"],
    }


def _resolve_context(
    raw_headers: tuple[tuple[str, str], ...],
    *,
    environment: Mapping[str, str],
    now: int,
):
    return resolve_owner_request_context(
        raw_headers,
        authentication_resolver=lambda received_headers: resolve_verified_auth0_owner(
            received_headers,
            environment=environment,
            now=now,
        ),
        now=now,
    )


def owner_response(
    request: object,
    *,
    http_mode: str,
    environment: Mapping[str, str] | None = None,
    now: int | None = None,
) -> PublicResponse:
    """Execute the one explicitly activated, Auth0-owner-only POST boundary."""

    source = os.environ if environment is None else environment
    timestamp = int(time.time()) if now is None else now
    try:
        require_request_method(request.command, expected_method="POST")  # type: ignore[attr-defined]
        if http_mode not in {"owner_read", "owner_write"}:
            return json_failure("not_found", status=404)
        raw_headers = extract_raw_headers(request)
        configuration = parse_owner_security_configuration(
            _trusted_security_snapshot(source)
        )
        validate_owner_mutation_origin(raw_headers, configuration)
        context = _resolve_context(
            raw_headers,
            environment=source,
            now=timestamp,
        )
        if not owner_is_allowlisted(context, configuration):
            raise OwnerSecurityError("rollout_unavailable")
        try:
            rate_limit_configuration = (
                owner_rate_limit.parse_owner_rate_limit_configuration(
                    _trusted_rate_limit_snapshot(source)
                )
            )
        except Exception:
            return json_failure("service_unavailable", status=503)

        payload = read_json_object(
            request,
            maximum_bytes=MAX_OWNER_REQUEST_BYTES,
            allowed_fields=_OWNER_BODY_FIELDS,
            required_fields={"operation"},
        )
        operation = payload.get("operation")
        if type(operation) is not str:
            raise BoundaryError("invalid_value", 400)

        if operation == "csrf":
            _require_exact_fields(payload, frozenset({"operation"}))
            if get_security_header(raw_headers, "x-cuevion-csrf") is not None:
                raise BoundaryError("invalid_value", 400)
            limited = _rate_limit_response(
                context,
                owner_rate_limit.RATE_LIMIT_BOOTSTRAP,
                rate_limit_configuration,
            )
            if limited is not None:
                return limited
            token, expires_at = issue_owner_csrf_token(
                context,
                configuration,
                now=timestamp,
            )
            return json_success({"csrfToken": token, "expiresAt": expires_at})

        csrf_token = parse_owner_csrf_header(raw_headers)
        verify_owner_csrf_token(
            csrf_token,
            context,
            configuration,
            now=timestamp,
        )

        if operation == "read":
            _require_exact_fields(
                payload,
                frozenset({"operation", "collaborationId"}),
            )
            limited = _rate_limit_response(
                context,
                owner_rate_limit.RATE_LIMIT_READ,
                rate_limit_configuration,
            )
            if limited is not None:
                return limited
            result = application.read_v2_collaboration_for_verified_owner(
                context,
                raw_headers,
                payload.get("collaborationId"),
                owner_security_configuration=configuration,
            )
            if (
                type(result) is dict
                and result.get("status") == "ok"
                and type(result.get("collaboration")) is dict
                and result.get("error") is None
            ):
                return json_success({"collaboration": result["collaboration"]})
            return _application_failure(result, operation=operation)

        if operation == "lookup":
            _require_exact_fields(
                payload,
                frozenset({"operation", "mailboxId", "sourceRef"}),
            )
            limited = _rate_limit_response(
                context,
                owner_rate_limit.RATE_LIMIT_READ,
                rate_limit_configuration,
            )
            if limited is not None:
                return limited
            result = application.lookup_v2_collaboration_for_verified_owner(
                context,
                raw_headers,
                payload.get("mailboxId"),
                payload.get("sourceRef"),
                owner_security_configuration=configuration,
            )
            if (
                type(result) is dict
                and set(result) == {"status", "collaborationId", "error"}
                and result.get("status") == "ok"
                and is_v2_opaque_id(result.get("collaborationId"))
                and result.get("error") is None
            ):
                return json_success(
                    {"collaborationId": result["collaborationId"]}
                )
            return _application_failure(result, operation=operation)

        if http_mode != "owner_write":
            raise OwnerSecurityError("rollout_unavailable")

        if operation == "create":
            _require_exact_fields(
                payload,
                frozenset(
                    {
                        "operation",
                        "mailboxId",
                        "sourceRef",
                        "state",
                        "participantUserId",
                    }
                ),
            )
            limited = _rate_limit_response(
                context,
                owner_rate_limit.RATE_LIMIT_WRITE,
                rate_limit_configuration,
            )
            if limited is not None:
                return limited
            result = application.create_v2_collaboration_for_verified_owner(
                context,
                raw_headers,
                {
                    "mailboxId": payload.get("mailboxId"),
                    "sourceRef": payload.get("sourceRef"),
                    "state": payload.get("state"),
                    "participantUserId": payload.get("participantUserId"),
                },
                owner_security_configuration=configuration,
            )
            if (
                type(result) is dict
                and set(result) == {"created", "collaboration"}
                and type(result.get("created")) is bool
                and type(result.get("collaboration")) is dict
            ):
                return json_success(result, status=201 if result["created"] else 200)
            return _application_failure(result, operation=operation)

        if operation == "create_with_guest":
            fields = {"operation", "mailboxId", "sourceRef", "state"}
            if "invitedEmail" in payload:
                fields.add("invitedEmail")
            _require_exact_fields(payload, frozenset(fields))
            limited = _rate_limit_response(
                context,
                owner_rate_limit.RATE_LIMIT_WRITE,
                rate_limit_configuration,
            )
            if limited is not None:
                return limited
            application_payload = {
                "mailboxId": payload.get("mailboxId"),
                "sourceRef": payload.get("sourceRef"),
                "state": payload.get("state"),
            }
            if "invitedEmail" in payload:
                application_payload["invitedEmail"] = payload.get("invitedEmail")
            result = application.create_v2_collaboration_with_guest_for_verified_owner(
                context,
                raw_headers,
                application_payload,
                owner_security_configuration=configuration,
            )
            success = _create_with_guest_success(result)
            if success is not None:
                return json_success(
                    success,
                    status=201 if success["created"] else 200,
                )
            return _application_failure(result, operation=operation)

        if operation == "issue_guest_invite":
            fields = {"operation", "collaborationId"}
            if "invitedEmail" in payload:
                fields.add("invitedEmail")
            _require_exact_fields(payload, frozenset(fields))
            limited = _rate_limit_response(
                context,
                owner_rate_limit.RATE_LIMIT_WRITE,
                rate_limit_configuration,
            )
            if limited is not None:
                return limited
            application_payload = (
                {"invitedEmail": payload.get("invitedEmail")}
                if "invitedEmail" in payload
                else {}
            )
            result = application.issue_v2_guest_invitation_for_verified_owner(
                context,
                raw_headers,
                payload.get("collaborationId"),
                application_payload,
                owner_security_configuration=configuration,
            )
            success = _issue_guest_success(result)
            if success is not None:
                return json_success(success, status=201 if success["invitationCreated"] else 200)
            return _application_failure(result, operation=operation)

        if operation == "revoke_guest_invite":
            _require_exact_fields(
                payload,
                frozenset({"operation", "collaborationId", "inviteId"}),
            )
            limited = _rate_limit_response(
                context,
                owner_rate_limit.RATE_LIMIT_WRITE,
                rate_limit_configuration,
            )
            if limited is not None:
                return limited
            result = application.revoke_v2_guest_invitation_for_verified_owner(
                context,
                raw_headers,
                payload.get("collaborationId"),
                payload.get("inviteId"),
                owner_security_configuration=configuration,
            )
            success = _revoke_guest_success(result)
            if success is not None:
                return json_success(success)
            return _application_failure(result, operation=operation)

        if operation == "add_participant":
            _require_exact_fields(
                payload,
                frozenset(
                    {"operation", "collaborationId", "participantUserId"}
                ),
            )
            limited = _rate_limit_response(
                context,
                owner_rate_limit.RATE_LIMIT_WRITE,
                rate_limit_configuration,
            )
            if limited is not None:
                return limited
            result = application.add_v2_participant_for_verified_owner(
                context,
                raw_headers,
                payload.get("collaborationId"),
                {"participantUserId": payload.get("participantUserId")},
                owner_security_configuration=configuration,
            )
            if (
                type(result) is dict
                and result.get("status") == "ok"
                and type(result.get("collaboration")) is dict
                and result.get("error") is None
            ):
                return json_success({"collaboration": result["collaboration"]})
            return _application_failure(result, operation=operation)

        if operation in {"append_shared", "append_internal"}:
            _require_exact_fields(
                payload,
                frozenset({"operation", "collaborationId", "text"}),
            )
            service = (
                application.append_v2_shared_message_for_verified_owner
                if operation == "append_shared"
                else application.append_v2_internal_note_for_verified_owner
            )
            idempotency_key = normalize_v2_owner_idempotency_key(
                get_security_header(
                    raw_headers,
                    "x-cuevion-idempotency-key",
                    required=True,
                )
            )
            if idempotency_key is None:
                raise BoundaryError("invalid_value", 400)
            limited = _rate_limit_response(
                context,
                owner_rate_limit.RATE_LIMIT_WRITE,
                rate_limit_configuration,
            )
            if limited is not None:
                return limited
            result = service(
                context,
                raw_headers,
                payload.get("collaborationId"),
                {"text": payload.get("text")},
                idempotency_key=idempotency_key,
                owner_security_configuration=configuration,
            )
            if (
                type(result) is dict
                and set(result) == {"message", "updatedAt"}
                and type(result.get("message")) is dict
                and type(result.get("updatedAt")) is int
            ):
                return json_success(result)
            return _application_failure(result, operation=operation)

        raise BoundaryError("invalid_value", 400)
    except OwnerSecurityError as error:
        return _owner_failure(error)


__all__ = ("MAX_OWNER_REQUEST_BYTES", "owner_response")
