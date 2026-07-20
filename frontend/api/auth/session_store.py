"""Opaque server-side session storage for the Auth0 authentication lane."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import ssl
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from cuevion_auth.session_credentials import (
    DerivedSessionCredential,
    derive_request_session_credential,
    parse_session_key_configuration,
)


SESSION_COOKIE_NAME = "__Host-cuevion_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
SESSION_SCHEMA_VERSION = 1
SESSION_KEY_PREFIX = "cuevion:auth:v1:session:"
TRANSACTION_USE_KEY_PREFIX = "cuevion:auth:v1:tx-used:"
MAX_KV_RESPONSE_BYTES = 32_768
KV_TIMEOUT_SECONDS = 5

_SESSION_SECRET_ENV = "CUEVION_AUTH_SESSION_SECRET"
_KV_URL_ENV = "KV_REST_API_URL"
_KV_TOKEN_ENV = "KV_REST_API_TOKEN"
_LOOKUP_KEY_INFO = b"cuevion/auth/session-lookup-key/v1"
_BINDING_KEY_INFO = b"cuevion/auth/session-binding-key/v1"
_TRANSACTION_KEY_INFO = b"cuevion/auth/transaction-use-key/v1"
_HKDF_SALT = b"cuevion/auth/key-derivation/v1\x00"
_BASE64URL_32_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_ACCOUNT_ID_RE = re.compile(r"(?:usr|wsp)_[A-Za-z0-9_-]{22}")
_SECURITY_TEXT_RE = re.compile(r"[!-~]{1,512}")


class SessionStoreUnavailable(Exception):
    """Value-free failure for unavailable or malformed durable storage."""

    __slots__ = ()

    def __str__(self) -> str:
        return "authentication session storage is unavailable"


class SessionConfigurationError(Exception):
    """Value-free failure for missing or invalid trusted configuration."""

    __slots__ = ()

    def __str__(self) -> str:
        return "authentication session configuration is invalid"


@dataclass(frozen=True, slots=True)
class ServerSessionRecord:
    schema_version: int
    session_id: str
    user_id: str
    workspace_id: str
    security_epoch: int
    issuer: str
    subject: str
    created_at: int
    expires_at: int
    binding_digest: str

    def __post_init__(self) -> None:
        valid = (
            type(self.schema_version) is int
            and self.schema_version == SESSION_SCHEMA_VERSION
            and type(self.session_id) is str
            and _BASE64URL_32_RE.fullmatch(self.session_id) is not None
            and type(self.user_id) is str
            and _ACCOUNT_ID_RE.fullmatch(self.user_id) is not None
            and self.user_id.startswith("usr_")
            and type(self.workspace_id) is str
            and _ACCOUNT_ID_RE.fullmatch(self.workspace_id) is not None
            and self.workspace_id.startswith("wsp_")
            and type(self.security_epoch) is int
            and self.security_epoch >= 1
            and type(self.issuer) is str
            and _SECURITY_TEXT_RE.fullmatch(self.issuer) is not None
            and type(self.subject) is str
            and _SECURITY_TEXT_RE.fullmatch(self.subject) is not None
            and type(self.created_at) is int
            and self.created_at >= 0
            and type(self.expires_at) is int
            and self.created_at < self.expires_at
            and self.expires_at - self.created_at <= SESSION_TTL_SECONDS
            and type(self.binding_digest) is str
            and _BASE64URL_32_RE.fullmatch(self.binding_digest) is not None
        )
        if not valid:
            raise ValueError("invalid server session record")


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _derive_key(secret: str, info: bytes) -> bytes:
    if (
        type(secret) is not str
        or secret != secret.strip()
        or not 32 <= len(secret.encode("utf-8")) <= 4096
    ):
        raise SessionConfigurationError()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=info,
    ).derive(secret.encode("utf-8"))


def resolve_session_secret(
    environment: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if environment is None else environment
    try:
        secret = source[_SESSION_SECRET_ENV]
        _derive_key(secret, _LOOKUP_KEY_INFO)
    except Exception:
        raise SessionConfigurationError() from None
    return secret


def _session_key_configuration(secret: str):
    lookup = _base64url(_derive_key(secret, _LOOKUP_KEY_INFO))
    binding = _base64url(_derive_key(secret, _BINDING_KEY_INFO))
    try:
        return parse_session_key_configuration(
            {
                "lookup_current_epoch": "1",
                "lookup_current_key": lookup,
                "binding_current_epoch": "1",
                "binding_current_key": binding,
            }
        )
    except Exception:
        raise SessionConfigurationError() from None


def _header_pairs(headers: object) -> tuple[tuple[str, str], ...]:
    try:
        if type(headers) is tuple:
            items = headers
        elif hasattr(headers, "raw_items"):
            items = tuple(headers.raw_items())
        else:
            items = tuple(headers.items())
    except Exception:
        return ()
    return items if all(type(item) is tuple for item in items) else ()


def derive_session_credential(
    headers: object,
    secret: str,
) -> DerivedSessionCredential | None:
    configuration = _session_key_configuration(secret)
    try:
        return derive_request_session_credential(
            _header_pairs(headers), configuration
        )
    except Exception:
        return None


def _credential_for_cookie_value(
    cookie_value: str,
    secret: str,
) -> DerivedSessionCredential:
    credential = derive_request_session_credential(
        (("cookie", f"{SESSION_COOKIE_NAME}={cookie_value}"),),
        _session_key_configuration(secret),
    )
    if credential is None:
        raise SessionConfigurationError()
    return credential


def build_session_cookie(cookie_value: str) -> str:
    if type(cookie_value) is not str or len(cookie_value) > 128:
        raise ValueError("invalid session cookie")
    return (
        f"{SESSION_COOKIE_NAME}={cookie_value}; Path=/; "
        f"Max-Age={SESSION_TTL_SECONDS}; Secure; HttpOnly; SameSite=Lax"
    )


def clear_session_cookie() -> str:
    return (
        f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; "
        "Expires=Thu, 01 Jan 1970 00:00:00 GMT; Secure; HttpOnly; SameSite=Lax"
    )


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _encode_record(record: ServerSessionRecord) -> str:
    return json.dumps(
        {
            "schemaVersion": record.schema_version,
            "sessionId": record.session_id,
            "userId": record.user_id,
            "workspaceId": record.workspace_id,
            "securityEpoch": record.security_epoch,
            "issuer": record.issuer,
            "subject": record.subject,
            "createdAt": record.created_at,
            "expiresAt": record.expires_at,
            "bindingDigest": record.binding_digest,
        },
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _decode_record(raw: object) -> ServerSessionRecord | None:
    if type(raw) is not str or not 2 <= len(raw.encode("utf-8")) <= 4096:
        return None
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
        if type(payload) is not dict or set(payload) != {
            "schemaVersion",
            "sessionId",
            "userId",
            "workspaceId",
            "securityEpoch",
            "issuer",
            "subject",
            "createdAt",
            "expiresAt",
            "bindingDigest",
        }:
            return None
        return ServerSessionRecord(
            schema_version=payload["schemaVersion"],
            session_id=payload["sessionId"],
            user_id=payload["userId"],
            workspace_id=payload["workspaceId"],
            security_epoch=payload["securityEpoch"],
            issuer=payload["issuer"],
            subject=payload["subject"],
            created_at=payload["createdAt"],
            expires_at=payload["expiresAt"],
            binding_digest=payload["bindingDigest"],
        )
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        return None


CommandTransport = Callable[[list[object]], dict[str, object]]


class AuthSessionStore:
    """Small injected Redis-command boundary using existing Vercel KV variables."""

    __slots__ = ("_transport",)

    def __init__(self, command_transport: CommandTransport) -> None:
        if not callable(command_transport):
            raise SessionConfigurationError()
        self._transport = command_transport

    def _command(self, command: list[object]) -> object:
        try:
            payload = self._transport(command)
        except Exception:
            raise SessionStoreUnavailable() from None
        if type(payload) is not dict or set(payload) != {"result"}:
            raise SessionStoreUnavailable()
        return payload["result"]

    def put(self, lookup_digest: str, record: ServerSessionRecord) -> None:
        ttl = record.expires_at - record.created_at
        result = self._command(
            [
                "SET",
                SESSION_KEY_PREFIX + lookup_digest,
                _encode_record(record),
                "EX",
                ttl,
            ]
        )
        if result != "OK":
            raise SessionStoreUnavailable()

    def get(self, lookup_digest: str) -> ServerSessionRecord | None:
        result = self._command(["GET", SESSION_KEY_PREFIX + lookup_digest])
        if result is None:
            return None
        record = _decode_record(result)
        if record is None:
            raise SessionStoreUnavailable()
        return record

    def delete(self, lookup_digest: str) -> None:
        result = self._command(["DEL", SESSION_KEY_PREFIX + lookup_digest])
        if type(result) is not int or type(result) is bool or result not in (0, 1):
            raise SessionStoreUnavailable()

    def consume_transaction(
        self,
        transaction_id: str,
        secret: str,
        ttl_seconds: int,
    ) -> bool:
        if (
            type(transaction_id) is not str
            or _BASE64URL_32_RE.fullmatch(transaction_id) is None
            or type(ttl_seconds) is not int
            or not 1 <= ttl_seconds <= 600
        ):
            return False
        key = _derive_key(secret, _TRANSACTION_KEY_INFO)
        digest = _base64url(
            hmac.new(key, transaction_id.encode("ascii"), hashlib.sha256).digest()
        )
        result = self._command(
            [
                "SET",
                TRANSACTION_USE_KEY_PREFIX + digest,
                "1",
                "EX",
                ttl_seconds,
                "NX",
            ]
        )
        if result == "OK":
            return True
        if result is None:
            return False
        raise SessionStoreUnavailable()


def _validate_kv_configuration(
    environment: Mapping[str, str],
) -> tuple[str, str, int, str, str]:
    try:
        raw_url = environment[_KV_URL_ENV]
        token = environment[_KV_TOKEN_ENV]
        parsed = urlsplit(raw_url)
    except Exception:
        raise SessionConfigurationError() from None
    if (
        type(raw_url) is not str
        or raw_url != raw_url.strip()
        or type(token) is not str
        or token != token.strip()
        or not 16 <= len(token) <= 4096
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise SessionConfigurationError()
    try:
        port = parsed.port or 443
    except ValueError:
        raise SessionConfigurationError() from None
    return parsed.hostname, parsed.hostname, port, "/", token


def build_kv_command_transport(
    environment: Mapping[str, str] | None = None,
    *,
    connection_factory: Callable[..., object] = http.client.HTTPSConnection,
) -> CommandTransport:
    source = os.environ if environment is None else environment
    host_header, hostname, port, path, token = _validate_kv_configuration(source)

    def perform(command: list[object]) -> dict[str, object]:
        body = json.dumps(command, separators=(",", ":")).encode("utf-8")
        if len(body) > 16_384:
            raise SessionStoreUnavailable()
        connection = None
        try:
            connection = connection_factory(
                hostname,
                port,
                timeout=KV_TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            )
            connection.request(
                "POST",
                path,
                body=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Host": host_header,
                },
            )
            response = connection.getresponse()
            content_length = response.getheader("Content-Length")
            if content_length is not None:
                if not content_length.isascii() or not content_length.isdigit():
                    raise SessionStoreUnavailable()
                if int(content_length) > MAX_KV_RESPONSE_BYTES:
                    raise SessionStoreUnavailable()
            raw = response.read(MAX_KV_RESPONSE_BYTES + 1)
            if response.status != 200 or len(raw) > MAX_KV_RESPONSE_BYTES:
                raise SessionStoreUnavailable()
            content_type = (response.getheader("Content-Type") or "").split(";", 1)[0]
            if content_type.strip().lower() != "application/json":
                raise SessionStoreUnavailable()
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
            if type(payload) is not dict:
                raise SessionStoreUnavailable()
            return payload
        except SessionStoreUnavailable:
            raise
        except Exception:
            raise SessionStoreUnavailable() from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    return perform


def build_runtime_session_store(
    environment: Mapping[str, str] | None = None,
) -> AuthSessionStore:
    return AuthSessionStore(build_kv_command_transport(environment))


def create_server_session(
    store: AuthSessionStore,
    *,
    secret: str,
    user_id: str,
    workspace_id: str,
    security_epoch: int,
    issuer: str,
    subject: str,
    now: int,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> tuple[ServerSessionRecord, str]:
    raw_secret = random_bytes(32)
    raw_session_id = random_bytes(32)
    if type(raw_secret) is not bytes or len(raw_secret) != 32:
        raise SessionConfigurationError()
    if type(raw_session_id) is not bytes or len(raw_session_id) != 32:
        raise SessionConfigurationError()
    cookie_value = f"v1.1.1.1.{_base64url(raw_secret)}"
    derived = _credential_for_cookie_value(cookie_value, secret)
    record = ServerSessionRecord(
        schema_version=SESSION_SCHEMA_VERSION,
        session_id=_base64url(raw_session_id),
        user_id=user_id,
        workspace_id=workspace_id,
        security_epoch=security_epoch,
        issuer=issuer,
        subject=subject,
        created_at=now,
        expires_at=now + SESSION_TTL_SECONDS,
        binding_digest=derived.credential_binding_digest,
    )
    store.put(derived.credential_lookup_digest, record)
    return record, build_session_cookie(cookie_value)


def load_server_session(
    store: AuthSessionStore,
    *,
    headers: object,
    secret: str,
    now: int,
) -> tuple[ServerSessionRecord | None, str | None]:
    derived = derive_session_credential(headers, secret)
    if derived is None:
        return None, None
    lookup_digest = derived.credential_lookup_digest
    record = store.get(lookup_digest)
    if record is None:
        return None, lookup_digest
    valid = (
        hmac.compare_digest(
            record.binding_digest, derived.credential_binding_digest
        )
        and record.created_at <= now < record.expires_at
    )
    if not valid:
        store.delete(lookup_digest)
        return None, lookup_digest
    return record, lookup_digest


def revoke_request_session(
    store: AuthSessionStore,
    *,
    headers: object,
    secret: str,
) -> None:
    derived = derive_session_credential(headers, secret)
    if derived is not None:
        store.delete(derived.credential_lookup_digest)


__all__ = (
    "SESSION_COOKIE_NAME",
    "SESSION_TTL_SECONDS",
    "SessionStoreUnavailable",
    "SessionConfigurationError",
    "ServerSessionRecord",
    "AuthSessionStore",
    "resolve_session_secret",
    "build_session_cookie",
    "clear_session_cookie",
    "build_kv_command_transport",
    "build_runtime_session_store",
    "create_server_session",
    "load_server_session",
    "revoke_request_session",
)
