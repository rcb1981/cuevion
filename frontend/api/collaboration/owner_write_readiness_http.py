from __future__ import annotations

if __name__ != "api.collaboration.owner_write_readiness_http":
    raise ImportError(
        "Collaboration helpers must be imported as "
        "api.collaboration.owner_write_readiness_http"
    )

import hmac
import importlib
import os
from collections.abc import Mapping

from . import owner_rate_limit, owner_request_security, redis_store
from .http_adapter import (
    PublicResponse,
    extract_raw_headers,
    json_failure,
    json_success,
    read_json_object,
    require_request_method,
)


VERCEL_ENVIRONMENT_NAME = "VERCEL_ENV"
GLOBAL_HTTP_MODE_ENVIRONMENT_NAME = "CUEVION_COLLAB_V2_HTTP_MODE"
READINESS_MODE_ENVIRONMENT_NAME = (
    "CUEVION_COLLAB_V2_OWNER_WRITE_READINESS_MODE"
)
READINESS_TOKEN_ENVIRONMENT_NAME = (
    "CUEVION_COLLAB_V2_OWNER_WRITE_READINESS_TOKEN"
)
READINESS_TOKEN_HEADER_NAME = "x-cuevion-owner-write-readiness"
APP_ORIGIN_ENVIRONMENT_NAME = "CUEVION_APP_ORIGIN"
SESSION_SECRET_ENVIRONMENT_NAME = "CUEVION_AUTH_SESSION_SECRET"
MAILBOX_SECRET_ENVIRONMENT_NAME = "MAILBOX_SECRET_ENCRYPTION_KEY"
CSRF_KEY_ENVIRONMENT_NAME = "CUEVION_COLLAB_V2_OWNER_CSRF_KEY"
CSRF_PREVIOUS_KEY_ENVIRONMENT_NAME = (
    "CUEVION_COLLAB_V2_OWNER_CSRF_KEY_PREVIOUS"
)
ALLOWLIST_KEY_ENVIRONMENT_NAME = "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY"
OWNER_ALLOWLIST_ENVIRONMENT_NAME = "CUEVION_COLLAB_V2_OWNER_ALLOWLIST"
MAILBOX_ALLOWLIST_ENVIRONMENT_NAME = "CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST"
RATE_LIMIT_KEY_ENVIRONMENT_NAME = "CUEVION_COLLAB_V2_RATE_LIMIT_HMAC_KEY"
INDEX_KEY_ENVIRONMENT_NAME = "CUEVION_COLLAB_INDEX_HMAC_KEY"
INDEX_PREVIOUS_KEY_ENVIRONMENT_NAME = "CUEVION_COLLAB_INDEX_HMAC_KEY_PREVIOUS"

READINESS_MODE = "verify"
OWNER_READ_MODE = "owner_read"
MAX_READINESS_REQUEST_BYTES = 64
_BODY_FIELDS = frozenset({"operation"})
_ENCODED_SECRET_NAMES = (
    READINESS_TOKEN_ENVIRONMENT_NAME,
    CSRF_KEY_ENVIRONMENT_NAME,
    CSRF_PREVIOUS_KEY_ENVIRONMENT_NAME,
    ALLOWLIST_KEY_ENVIRONMENT_NAME,
    RATE_LIMIT_KEY_ENVIRONMENT_NAME,
    INDEX_KEY_ENVIRONMENT_NAME,
    INDEX_PREVIOUS_KEY_ENVIRONMENT_NAME,
)
_REQUIRED_SECRET_NAMES = frozenset(
    {
        READINESS_TOKEN_ENVIRONMENT_NAME,
        SESSION_SECRET_ENVIRONMENT_NAME,
        CSRF_KEY_ENVIRONMENT_NAME,
        ALLOWLIST_KEY_ENVIRONMENT_NAME,
        RATE_LIMIT_KEY_ENVIRONMENT_NAME,
        INDEX_KEY_ENVIRONMENT_NAME,
    }
)
_OWNER_SECURITY_NAMES = (
    APP_ORIGIN_ENVIRONMENT_NAME,
    CSRF_KEY_ENVIRONMENT_NAME,
    CSRF_PREVIOUS_KEY_ENVIRONMENT_NAME,
    ALLOWLIST_KEY_ENVIRONMENT_NAME,
    OWNER_ALLOWLIST_ENVIRONMENT_NAME,
    MAILBOX_ALLOWLIST_ENVIRONMENT_NAME,
)


def _fixed_failure(status: int, code: str) -> PublicResponse:
    return json_failure(code, status=status)


def _environment_value(environment: Mapping[str, str], name: str) -> object:
    try:
        return environment.get(name)
    except Exception:
        raise ValueError("invalid readiness configuration") from None


def _validate_host_and_origin(
    raw_headers: tuple[tuple[str, str], ...],
) -> PublicResponse | None:
    try:
        auth_http = importlib.import_module("api.auth.http")
        validated = auth_http.validate_header_pairs(raw_headers)
        auth_http.require_canonical_host(validated)
        auth_http.require_same_origin(validated)
    except Exception as error:
        try:
            status = error.status if type(error) is auth_http.HttpBoundaryError else 500
        except Exception:
            status = 500
        if status == 403:
            return _fixed_failure(403, "forbidden")
        if status == 400:
            return _fixed_failure(400, "invalid_request")
        return _fixed_failure(500, "internal_error")
    return None


def _operator_token_is_valid(
    raw_headers: tuple[tuple[str, str], ...],
    expected_token: bytes,
) -> bool:
    supplied: bytes | None = None
    try:
        auth_http = importlib.import_module("api.auth.http")
        header = auth_http.get_unique_header(
            raw_headers,
            READINESS_TOKEN_HEADER_NAME,
            required=True,
        )
        supplied = owner_request_security.parse_allowlist_hmac_key(header)
    except Exception:
        supplied = None
    candidate = supplied if supplied is not None else bytes(len(expected_token))
    matches = hmac.compare_digest(expected_token, candidate)
    return supplied is not None and matches is True


def _parse_session_secret(value: object) -> tuple[str, bytes]:
    if type(value) is not str:
        raise ValueError("invalid readiness configuration")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("invalid readiness configuration") from None
    if value != value.strip() or not 32 <= len(encoded) <= 4096:
        raise ValueError("invalid readiness configuration")
    return value, encoded


def _configuration_snapshot(
    environment: Mapping[str, str],
    names: tuple[str, ...],
) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for name in names:
        value = _environment_value(environment, name)
        if value is not None:
            if type(value) is not str:
                raise ValueError("invalid readiness configuration")
            snapshot[name] = value
    return snapshot


def _verify_runtime_configuration(environment: Mapping[str, str]) -> bool:
    try:
        required = {
            name: _environment_value(environment, name)
            for name in _REQUIRED_SECRET_NAMES
        }
        if any(type(value) is not str or not value for value in required.values()):
            return False

        owner_request_security.parse_owner_security_configuration(
            _configuration_snapshot(environment, _OWNER_SECURITY_NAMES)
        )
        owner_rate_limit.parse_owner_rate_limit_configuration(
            _configuration_snapshot(
                environment,
                owner_rate_limit.RATE_LIMIT_CONFIGURATION_NAMES,
            )
        )

        current_index = _environment_value(environment, INDEX_KEY_ENVIRONMENT_NAME)
        previous_index = _environment_value(
            environment,
            INDEX_PREVIOUS_KEY_ENVIRONMENT_NAME,
        )
        if type(current_index) is not str:
            return False
        if previous_index is not None and type(previous_index) is not str:
            return False
        index_keys = redis_store.resolve_v2_index_hmac_keys(
            current_index,
            "" if previous_index is None else previous_index,
        )
        if index_keys is None:
            return False

        parsed_secrets: list[tuple[str, bytes]] = []
        for name in _ENCODED_SECRET_NAMES:
            value = _environment_value(environment, name)
            if value is None:
                continue
            if type(value) is not str:
                return False
            parsed_secrets.append(
                (value, owner_request_security.parse_allowlist_hmac_key(value))
            )

        mailbox_value = _environment_value(environment, MAILBOX_SECRET_ENVIRONMENT_NAME)
        if mailbox_value is not None:
            mailbox_key = owner_rate_limit._decode_secret(
                mailbox_value,
                allow_padding=True,
            )
            if mailbox_key is None or len(mailbox_key) != 32:
                return False
            parsed_secrets.append((mailbox_value, mailbox_key))

        session_text, session_bytes = _parse_session_secret(
            _environment_value(environment, SESSION_SECRET_ENVIRONMENT_NAME)
        )
        for index, (encoded, decoded) in enumerate(parsed_secrets):
            if hmac.compare_digest(session_text, encoded) or hmac.compare_digest(
                session_bytes,
                decoded,
            ):
                return False
            for other_encoded, other_decoded in parsed_secrets[index + 1 :]:
                if hmac.compare_digest(decoded, other_decoded) or hmac.compare_digest(
                    encoded,
                    other_encoded,
                ):
                    return False
        return True
    except Exception:
        return False


def owner_write_readiness_response(
    request: object,
    *,
    environment: Mapping[str, str] | None = None,
) -> PublicResponse:
    """Verify the live owner-write secret relationships without mutating state."""

    source = os.environ if environment is None else environment
    if (
        _environment_value(source, VERCEL_ENVIRONMENT_NAME) != "production"
        or _environment_value(source, GLOBAL_HTTP_MODE_ENVIRONMENT_NAME)
        != OWNER_READ_MODE
        or _environment_value(source, READINESS_MODE_ENVIRONMENT_NAME)
        != READINESS_MODE
    ):
        return _fixed_failure(404, "not_found")

    require_request_method(request.command, expected_method="POST")  # type: ignore[attr-defined]
    raw_headers = extract_raw_headers(request)
    try:
        owner_request_security.parse_trusted_owner_origin(
            _environment_value(source, APP_ORIGIN_ENVIRONMENT_NAME)
        )
    except Exception:
        return _fixed_failure(503, "service_unavailable")
    boundary_failure = _validate_host_and_origin(raw_headers)
    if boundary_failure is not None:
        return boundary_failure

    try:
        expected_token = owner_request_security.parse_allowlist_hmac_key(
            _environment_value(source, READINESS_TOKEN_ENVIRONMENT_NAME)
        )
    except Exception:
        return _fixed_failure(404, "not_found")
    if not _operator_token_is_valid(raw_headers, expected_token):
        return _fixed_failure(404, "not_found")

    payload = read_json_object(
        request,
        maximum_bytes=MAX_READINESS_REQUEST_BYTES,
        allowed_fields=_BODY_FIELDS,
        required_fields=_BODY_FIELDS,
    )
    if payload.get("operation") != "verify":
        return _fixed_failure(400, "invalid_request")
    if not _verify_runtime_configuration(source):
        return _fixed_failure(503, "service_unavailable")

    return json_success(
        {
            "ownerWriteConfigurationValid": True,
            "requiredSecretsPresent": True,
            "secretSeparationValid": True,
        }
    )
