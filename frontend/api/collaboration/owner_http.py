from __future__ import annotations

if __name__ != "api.collaboration.owner_http":
    raise ImportError(
        "Collaboration helpers must be imported as api.collaboration.owner_http"
    )

import os
import time
from collections.abc import Mapping

from . import application
from .http_adapter import (
    PublicResponse,
    extract_raw_headers,
    json_failure,
    json_success,
    read_json_object,
    require_request_method,
)
from .http_boundary import BoundaryError, get_security_header
from .models import normalize_v2_owner_idempotency_key
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
    "idempotency_conflict": (409, "conflict"),
    "storage_unavailable": (503, "service_unavailable"),
    "storage_protocol_error": (503, "service_unavailable"),
    "index_hmac_unavailable": (503, "service_unavailable"),
    "provider_unavailable": (503, "service_unavailable"),
    "atomic_exchange_unavailable": (503, "service_unavailable"),
}


def _trusted_security_snapshot(environment: Mapping[str, str]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    try:
        for name in _SECURITY_CONFIGURATION_NAMES:
            if name in environment:
                snapshot[name] = environment[name]
    except Exception:
        raise OwnerSecurityError("invalid_configuration") from None
    return snapshot


def _owner_failure(error: OwnerSecurityError) -> PublicResponse:
    status, code = normalize_owner_security_failure(error)
    return json_failure(code, status=status)


def _application_failure(result: object) -> PublicResponse:
    code = None
    if type(result) is dict:
        error = result.get("error")
        if type(error) is dict and set(error) == {"code"}:
            code = error.get("code")
    status, public_code = _APPLICATION_FAILURES.get(
        code,
        (500, "internal_error"),
    )
    return json_failure(public_code, status=status)


def _require_exact_fields(payload: dict, fields: frozenset[str]) -> None:
    if type(payload) is not dict or set(payload) != fields:
        raise BoundaryError("invalid_json_fields", 400)


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
            return _application_failure(result)

        if http_mode != "owner_write":
            raise OwnerSecurityError("rollout_unavailable")

        if operation == "create":
            _require_exact_fields(
                payload,
                frozenset(
                    {"operation", "mailboxId", "sourceRef", "state"}
                ),
            )
            result = application.create_v2_collaboration_for_verified_owner(
                context,
                raw_headers,
                {
                    "mailboxId": payload.get("mailboxId"),
                    "sourceRef": payload.get("sourceRef"),
                    "state": payload.get("state"),
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
            return _application_failure(result)

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
            return _application_failure(result)

        raise BoundaryError("invalid_value", 400)
    except OwnerSecurityError as error:
        return _owner_failure(error)


__all__ = ("MAX_OWNER_REQUEST_BYTES", "owner_response")
