from __future__ import annotations

if __name__ != "api.collaboration.allowlist_bootstrap_http":
    raise ImportError(
        "Collaboration helpers must be imported as "
        "api.collaboration.allowlist_bootstrap_http"
    )

import hmac
import importlib
import os
import time
from collections.abc import Mapping

from . import authorization
from .http_adapter import (
    PublicResponse,
    extract_raw_headers,
    json_allowlist_bootstrap_success,
    json_failure,
    read_json_object,
    require_request_method,
)
from .models import normalize_v2_email
from .owner_authentication import resolve_verified_auth0_owner
from .owner_request_security import (
    OwnerSecurityError,
    derive_mailbox_allowlist_entry,
    derive_owner_allowlist_entry,
    normalize_owner_security_failure,
    parse_allowlist_hmac_key,
    parse_trusted_owner_origin,
    resolve_owner_request_context,
    valid_allowlist_mailbox_id,
)


BOOTSTRAP_HTTP_MODE = "allowlist_bootstrap"
BOOTSTRAP_TOKEN_ENVIRONMENT_NAME = (
    "CUEVION_COLLAB_V2_ALLOWLIST_BOOTSTRAP_TOKEN"
)
BOOTSTRAP_TOKEN_HEADER_NAME = "x-cuevion-allowlist-bootstrap"
ALLOWLIST_HMAC_KEY_ENVIRONMENT_NAME = "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY"
APP_ORIGIN_ENVIRONMENT_NAME = "CUEVION_APP_ORIGIN"
VERCEL_ENVIRONMENT_NAME = "VERCEL_ENV"
MAX_BOOTSTRAP_REQUEST_BYTES = 512
_BODY_FIELDS = frozenset({"mailboxId"})
_SUPPORTED_MAILBOX_PROVIDERS = frozenset({"google", "custom_imap"})


def _environment_value(environment: Mapping[str, str], name: str) -> object:
    try:
        return environment.get(name)
    except Exception:
        return None


def _fixed_failure(status: int, code: str) -> PublicResponse:
    return json_failure(code, status=status)


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
            BOOTSTRAP_TOKEN_HEADER_NAME,
            required=True,
        )
        supplied = parse_allowlist_hmac_key(header)
    except Exception:
        supplied = None
    candidate = supplied if supplied is not None else bytes(len(expected_token))
    matches = hmac.compare_digest(expected_token, candidate)
    return supplied is not None and matches is True


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


def _mailbox_authority_failure(result: object) -> PublicResponse | None:
    if type(result) is not dict:
        return _fixed_failure(503, "service_unavailable")
    status = result.get("status")
    if status == "unauthorized":
        return _fixed_failure(401, "unauthorized")
    if status == "not_found":
        return _fixed_failure(404, "not_found")
    if status in {"unavailable", "malformed", "conflict"}:
        return _fixed_failure(503, "service_unavailable")
    return None


def _owned_mailbox_is_verified(
    result: object,
    *,
    owner_context: object,
    mailbox_id: str,
) -> bool:
    if type(result) is not dict or result.get("status") != "ok":
        return False
    try:
        auth_runtime = importlib.import_module("api.auth.runtime")
        member = result.get("memberAuthority")
        user = result.get("user")
        inbox = result.get("inbox")
        return (
            type(member) is auth_runtime.AuthenticatedMemberContext
            and member.auth_source == "auth0"
            and member.user_type == "member"
            and member.email == owner_context.owner_email
            and member.workspace_id == owner_context.workspace_id
            and member.name == owner_context.display_name
            and type(user) is dict
            and normalize_v2_email(user.get("email")) == owner_context.owner_email
            and type(inbox) is dict
            and inbox.get("id") == mailbox_id
            and inbox.get("provider") in _SUPPORTED_MAILBOX_PROVIDERS
        )
    except Exception:
        return False


def _owner_failure(error: OwnerSecurityError) -> PublicResponse:
    status, code = normalize_owner_security_failure(error)
    return _fixed_failure(status, code)


def allowlist_bootstrap_response(
    request: object,
    *,
    http_mode: str,
    environment: Mapping[str, str] | None = None,
    now: int | None = None,
) -> PublicResponse:
    """Derive one owner/mailbox digest pair inside the gated runtime only."""

    source = os.environ if environment is None else environment
    timestamp = int(time.time()) if now is None else now
    try:
        require_request_method(request.command, expected_method="POST")  # type: ignore[attr-defined]
        if http_mode != BOOTSTRAP_HTTP_MODE:
            return _fixed_failure(404, "not_found")
        if _environment_value(source, VERCEL_ENVIRONMENT_NAME) != "production":
            return _fixed_failure(404, "not_found")

        raw_headers = extract_raw_headers(request)
        try:
            parse_trusted_owner_origin(
                _environment_value(source, APP_ORIGIN_ENVIRONMENT_NAME)
            )
        except OwnerSecurityError:
            return _fixed_failure(503, "service_unavailable")
        boundary_failure = _validate_host_and_origin(raw_headers)
        if boundary_failure is not None:
            return boundary_failure

        try:
            expected_token = parse_allowlist_hmac_key(
                _environment_value(source, BOOTSTRAP_TOKEN_ENVIRONMENT_NAME)
            )
        except ValueError:
            return _fixed_failure(404, "not_found")
        if not _operator_token_is_valid(raw_headers, expected_token):
            return _fixed_failure(404, "not_found")

        try:
            allowlist_key = parse_allowlist_hmac_key(
                _environment_value(source, ALLOWLIST_HMAC_KEY_ENVIRONMENT_NAME)
            )
        except ValueError:
            return _fixed_failure(503, "service_unavailable")
        if hmac.compare_digest(expected_token, allowlist_key):
            return _fixed_failure(503, "service_unavailable")

        payload = read_json_object(
            request,
            maximum_bytes=MAX_BOOTSTRAP_REQUEST_BYTES,
            allowed_fields=_BODY_FIELDS,
            required_fields=_BODY_FIELDS,
        )
        mailbox_id = payload.get("mailboxId")
        if not valid_allowlist_mailbox_id(mailbox_id):
            return _fixed_failure(400, "invalid_request")

        owner_context = _resolve_context(
            raw_headers,
            environment=source,
            now=timestamp,
        )
        try:
            owned_result = (
                authorization._resolve_verified_owned_managed_inbox_record(
                    raw_headers,
                    mailbox_id,
                )
            )
        except Exception:
            return _fixed_failure(503, "service_unavailable")
        authority_failure = _mailbox_authority_failure(owned_result)
        if authority_failure is not None:
            return authority_failure
        if not _owned_mailbox_is_verified(
            owned_result,
            owner_context=owner_context,
            mailbox_id=mailbox_id,
        ):
            return _fixed_failure(404, "not_found")

        owner_digest = derive_owner_allowlist_entry(
            allowlist_key,
            owner_context.issuer,
            owner_context.authentication_version,
            owner_context.subject,
        )
        mailbox_digest = derive_mailbox_allowlist_entry(
            allowlist_key,
            owner_context.issuer,
            owner_context.authentication_version,
            owner_context.subject,
            mailbox_id,
        )
        return json_allowlist_bootstrap_success(owner_digest, mailbox_digest)
    except OwnerSecurityError as error:
        return _owner_failure(error)


__all__ = (
    "ALLOWLIST_HMAC_KEY_ENVIRONMENT_NAME",
    "APP_ORIGIN_ENVIRONMENT_NAME",
    "BOOTSTRAP_HTTP_MODE",
    "BOOTSTRAP_TOKEN_ENVIRONMENT_NAME",
    "BOOTSTRAP_TOKEN_HEADER_NAME",
    "MAX_BOOTSTRAP_REQUEST_BYTES",
    "VERCEL_ENVIRONMENT_NAME",
    "allowlist_bootstrap_response",
)
