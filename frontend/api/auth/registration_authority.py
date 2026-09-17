"""Invite-bound registration authority for Auth0 database signups.

Authentication stays in Auth0. Cuevion alone decides whether a new canonical
account may be registered. Team invites mint short-lived opaque grants; Auth0's
Pre User Registration Action presents the grant back to this server for
validation. The grant is bound to the invitee email, Cuevion client and exact
OAuth transaction state, while the existing callback still proves the original
Team invitation before provisioning any Cuevion account.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from api.auth import auth0_flow, email_address, http, runtime, session_store


ROUTE = "/api/auth/registration-authority"
ACR_PREFIX = "urn:cuevion:registration-grant:v1:"
GRANT_KEY_PREFIX = "cuevion:auth:v1:registration-grant:"
GRANT_TTL_SECONDS = auth0_flow.AUTH_TRANSACTION_TTL_SECONDS
MAX_REQUEST_BODY_BYTES = 2_048
_SHARED_SECRET_ENV = "CUEVION_AUTH0_REGISTRATION_AUTHORITY_SECRET"

_GRANT_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_STATE_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_AUTHORITY_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,160}")
_AUTHORITY_DIGEST_RE = re.compile(r"[a-f0-9]{64}")
_SOURCES = frozenset({"team_invite", "paid_signup", "manual_test_access"})
_ACTIVE_SOURCES = frozenset({"team_invite"})


class RegistrationAuthorityUnavailable(Exception):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class RegistrationGrantRecord:
    source: str
    email: str
    client_id: str
    oauth_state: str
    authority_id: str
    authority_digest: str
    created_at: int
    expires_at: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        valid = (
            type(self.schema_version) is int
            and self.schema_version == 1
            and type(self.source) is str
            and self.source in _SOURCES
            and type(self.email) is str
            and self.email == email_address.normalize_auth_email(self.email)
            and email_address.is_valid_auth_email(self.email)
            and type(self.client_id) is str
            and 1 <= len(self.client_id) <= 512
            and self.client_id.isascii()
            and all(33 <= ord(character) <= 126 for character in self.client_id)
            and type(self.oauth_state) is str
            and _STATE_RE.fullmatch(self.oauth_state) is not None
            and type(self.authority_id) is str
            and _AUTHORITY_ID_RE.fullmatch(self.authority_id) is not None
            and type(self.authority_digest) is str
            and _AUTHORITY_DIGEST_RE.fullmatch(self.authority_digest) is not None
            and type(self.created_at) is int
            and type(self.expires_at) is int
            and 0 <= self.created_at < self.expires_at
            and self.expires_at - self.created_at <= GRANT_TTL_SECONDS
        )
        if not valid:
            raise ValueError("invalid registration grant")

    def __repr__(self) -> str:
        return "<RegistrationGrantRecord>"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _grant_key(grant: object) -> str:
    if type(grant) is not str or _GRANT_RE.fullmatch(grant) is None:
        raise ValueError("invalid registration grant")
    digest = hashlib.sha256(grant.encode("ascii")).hexdigest()
    return GRANT_KEY_PREFIX + digest


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("invalid json")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid json")


def _encode_record(record: RegistrationGrantRecord) -> str:
    record.__post_init__()
    return json.dumps(
        {
            "authorityDigest": record.authority_digest,
            "authorityId": record.authority_id,
            "clientId": record.client_id,
            "createdAt": record.created_at,
            "email": record.email,
            "expiresAt": record.expires_at,
            "oauthState": record.oauth_state,
            "schemaVersion": record.schema_version,
            "source": record.source,
        },
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_record(raw: object) -> RegistrationGrantRecord | None:
    if type(raw) is not str or not 2 <= len(raw.encode("utf-8")) <= 4_096:
        return None
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
        required = {
            "authorityDigest", "authorityId", "clientId", "createdAt", "email",
            "expiresAt", "oauthState", "schemaVersion", "source",
        }
        if type(value) is not dict or set(value) != required:
            return None
        return RegistrationGrantRecord(
            source=value["source"],
            email=value["email"],
            client_id=value["clientId"],
            oauth_state=value["oauthState"],
            authority_id=value["authorityId"],
            authority_digest=value["authorityDigest"],
            created_at=value["createdAt"],
            expires_at=value["expiresAt"],
            schema_version=value["schemaVersion"],
        )
    except (TypeError, ValueError, KeyError, RecursionError, json.JSONDecodeError):
        return None


CommandTransport = Callable[[list[object]], dict[str, object]]


class RegistrationGrantStore:
    __slots__ = ("_transport",)

    def __init__(self, command_transport: CommandTransport) -> None:
        if not callable(command_transport):
            raise RegistrationAuthorityUnavailable()
        self._transport = command_transport

    def _command(self, command: list[object]) -> object:
        try:
            payload = self._transport(command)
        except Exception:
            raise RegistrationAuthorityUnavailable() from None
        if type(payload) is not dict or set(payload) != {"result"}:
            raise RegistrationAuthorityUnavailable()
        return payload["result"]

    def put(self, grant: str, record: RegistrationGrantRecord, *, now: int) -> bool:
        encoded = _encode_record(record)
        if type(now) is not int or not record.created_at <= now < record.expires_at:
            return False
        result = self._command([
            "SET", _grant_key(grant), encoded,
            "EX", record.expires_at - now, "NX",
        ])
        if result == "OK":
            return True
        if result is None:
            return False
        raise RegistrationAuthorityUnavailable()

    def get(self, grant: str, *, now: int) -> RegistrationGrantRecord | None:
        if type(now) is not int:
            return None
        raw = self._command(["GET", _grant_key(grant)])
        if raw is None:
            return None
        record = _decode_record(raw)
        if record is None:
            raise RegistrationAuthorityUnavailable()
        if not record.created_at <= now < record.expires_at:
            return None
        return record


def build_runtime_registration_store(
    environment: Mapping[str, str] | None = None,
) -> RegistrationGrantStore:
    return RegistrationGrantStore(session_store.build_kv_command_transport(environment))


def issue_team_invite_grant(
    store: RegistrationGrantStore,
    *,
    email: str,
    client_id: str,
    oauth_state: str,
    invitation_id: str,
    invitation_token_digest: str,
    invitation_expires_at_ms: int,
    now: int,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> str:
    canonical_email = email_address.normalize_auth_email(email)
    if (
        not email_address.is_valid_auth_email(canonical_email)
        or type(invitation_expires_at_ms) is not int
        or invitation_expires_at_ms < 0
    ):
        raise ValueError("invalid registration authority")
    expires_at = min(now + GRANT_TTL_SECONDS, invitation_expires_at_ms // 1_000)
    if type(now) is not int or expires_at <= now:
        raise ValueError("invalid registration authority")
    raw = random_bytes(32)
    if type(raw) is not bytes or len(raw) != 32:
        raise RegistrationAuthorityUnavailable()
    grant = _base64url(raw)
    record = RegistrationGrantRecord(
        source="team_invite",
        email=canonical_email,
        client_id=client_id,
        oauth_state=oauth_state,
        authority_id=invitation_id,
        authority_digest=invitation_token_digest,
        created_at=now,
        expires_at=expires_at,
    )
    if not store.put(grant, record, now=now):
        raise RegistrationAuthorityUnavailable()
    return grant


def _authorization_location(response: http.PublicResponse) -> tuple[str, str] | None:
    if response.status != 303:
        return None
    locations = [
        value for name, value in response.headers if name.casefold() == "location"
    ]
    if len(locations) != 1:
        return None
    location = locations[0]
    try:
        parsed = urlsplit(location)
        if (
            parsed.scheme != "https"
            or parsed.netloc != auth0_flow.AUTH0_DOMAIN
            or parsed.path != "/authorize"
            or parsed.fragment
        ):
            return None
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=32,
            encoding="utf-8",
            errors="strict",
        )
        states = [value for name, value in pairs if name == "state"]
        if len(states) != 1 or _STATE_RE.fullmatch(states[0]) is None:
            raise ValueError
        if any(name == "acr_values" for name, _value in pairs):
            raise ValueError
        return location, states[0]
    except (TypeError, ValueError, UnicodeError):
        raise RegistrationAuthorityUnavailable() from None


def _with_grant(location: str, grant: str) -> str:
    parsed = urlsplit(location)
    pairs = parse_qsl(
        parsed.query,
        keep_blank_values=True,
        strict_parsing=True,
        max_num_fields=32,
        encoding="utf-8",
        errors="strict",
    )
    if any(name == "acr_values" for name, _value in pairs):
        raise RegistrationAuthorityUnavailable()
    pairs.append(("acr_values", ACR_PREFIX + grant))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), ""))


def _replace_location(response: http.PublicResponse, location: str) -> http.PublicResponse:
    replaced = False
    headers: list[tuple[str, str]] = []
    for name, value in response.headers:
        if name.casefold() == "location":
            if replaced:
                raise RegistrationAuthorityUnavailable()
            headers.append((name, location))
            replaced = True
        else:
            headers.append((name, value))
    if not replaced:
        raise RegistrationAuthorityUnavailable()
    return http.PublicResponse(response.status, tuple(headers), response.body)


def login_response(
    method: str,
    raw_headers: tuple[tuple[str, str], ...],
    raw_path: str | None = None,
) -> http.PublicResponse:
    """Decorate only validated Team-invite authorization redirects with a grant."""

    # Keep the established runtime as the first and authoritative HTTP/auth
    # boundary. Normal login and every rejected invite path remain byte-for-byte
    # on that existing lane.
    response = runtime.login_response(method, raw_headers, raw_path)
    try:
        authorization = _authorization_location(response)
        if authorization is None or raw_path is None:
            return response
        token = runtime._parse_login_invite(raw_path)
        if token is None:
            return response
        source = os.environ
        team = runtime._team_authority(source, None)
        invitation = team.read_provisioning_invitation(token, allow_accepted=True)
        configuration = auth0_flow.parse_auth0_configuration(source)
        location, oauth_state = authorization
        timestamp = int(time.time())
        grant = issue_team_invite_grant(
            build_runtime_registration_store(source),
            email=invitation.email,
            client_id=configuration.client_id,
            oauth_state=oauth_state,
            invitation_id=invitation.invitation_id,
            invitation_token_digest=invitation.token_digest,
            invitation_expires_at_ms=invitation.expires_at,
            now=timestamp,
        )
        return _replace_location(response, _with_grant(location, grant))
    except Exception:
        # Never send the already-built transaction cookie if registration
        # authority could not be durably established.
        return runtime._authentication_unavailable_response()


def _shared_secret(source: Mapping[str, str]) -> str:
    try:
        secret = source[_SHARED_SECRET_ENV]
    except Exception:
        raise RegistrationAuthorityUnavailable() from None
    if (
        type(secret) is not str
        or secret != secret.strip()
        or not 32 <= len(secret.encode("utf-8")) <= 4_096
        or any(ord(character) <= 31 or ord(character) == 127 for character in secret)
    ):
        raise RegistrationAuthorityUnavailable()
    return secret


def _authorized_action(headers: tuple[tuple[str, str], ...], secret: str) -> bool:
    values = [value for name, value in headers if name.casefold() == "authorization"]
    if len(values) != 1:
        return False
    candidate = values[0]
    expected = "Bearer " + secret
    return (
        type(candidate) is str
        and len(candidate) == len(expected)
        and hmac.compare_digest(candidate, expected)
    )


def _content_type_is_json(headers: tuple[tuple[str, str], ...]) -> bool:
    values = [value for name, value in headers if name.casefold() == "content-type"]
    return len(values) == 1 and values[0].split(";", 1)[0].strip().lower() == "application/json"


def _request_payload(body: object) -> dict[str, str]:
    if type(body) is not bytes or not 2 <= len(body) <= MAX_REQUEST_BODY_BYTES:
        raise ValueError("invalid request")
    value = json.loads(
        body.decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_json_constant,
    )
    if type(value) is not dict or set(value) != {"acrValue", "clientId", "email", "state"}:
        raise ValueError("invalid request")
    if any(type(item) is not str for item in value.values()):
        raise ValueError("invalid request")
    return value  # type: ignore[return-value]


def _grant_from_acr(value: object) -> str:
    if type(value) is not str or not value.startswith(ACR_PREFIX):
        raise ValueError("invalid request")
    grant = value[len(ACR_PREFIX):]
    if _GRANT_RE.fullmatch(grant) is None:
        raise ValueError("invalid request")
    return grant


def _denied(status: int = 403) -> http.PublicResponse:
    return http.json_response(status, {"authorized": False})


def validation_response(
    method: str,
    raw_headers: tuple[tuple[str, str], ...],
    body: bytes,
    *,
    environment: Mapping[str, str] | None = None,
    now: int | None = None,
    store_factory: Callable[[Mapping[str, str]], RegistrationGrantStore] = build_runtime_registration_store,
) -> http.PublicResponse:
    """Validate one Auth0 Pre User Registration request, fail-closed."""

    source = os.environ if environment is None else environment
    try:
        http.require_method(method, "POST")
        headers = http.validate_header_pairs(raw_headers)
        http.require_canonical_host(headers)
        secret = _shared_secret(source)
        if not _authorized_action(headers, secret):
            return _denied()
        if not _content_type_is_json(headers):
            return _denied(415)
        payload = _request_payload(body)
        client_id = payload["clientId"]
        expected_client_id = source.get("CUEVION_AUTH0_CLIENT_ID")
        if type(expected_client_id) is not str or not expected_client_id:
            raise RegistrationAuthorityUnavailable()
        if client_id != expected_client_id:
            return _denied()
        email = email_address.normalize_auth_email(payload["email"])
        if not email_address.is_valid_auth_email(email):
            return _denied()
        state = payload["state"]
        if _STATE_RE.fullmatch(state) is None:
            return _denied()
        grant = _grant_from_acr(payload["acrValue"])
        timestamp = int(time.time()) if now is None else now
        record = store_factory(source).get(grant, now=timestamp)
        if (
            record is None
            or record.source not in _ACTIVE_SOURCES
            or record.email != email
            or record.client_id != client_id
            or record.oauth_state != state
        ):
            return _denied()
        return http.json_response(200, {"authorized": True})
    except http.HttpBoundaryError as error:
        return _denied(error.status)
    except (RegistrationAuthorityUnavailable, session_store.SessionConfigurationError):
        return _denied(503)
    except Exception:
        return _denied()
