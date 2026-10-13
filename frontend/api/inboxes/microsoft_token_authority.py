"""Owner-scoped Microsoft OAuth tokens with fail-closed durable CAS refresh.

This is not an HTTP handler. Callers must first authenticate the Cuevion
member, verify ownership of a persisted managed inbox, and verify the Microsoft
account identity with Graph /me. No runtime/tmp fallback is permitted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from http.client import IncompleteRead
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from api.auth.email_address import is_valid_auth_email, normalize_auth_email
from api.inboxes import oauth_token_store as oauth_store

KEY_PREFIX = "cuevion:microsoft:owned-token:v1:"
GENERATION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
MAILBOX_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$")
TENANT_PATTERN = re.compile(r"^(?:common|organizations|consumers|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$")
REQUIRED_SCOPES = frozenset({"User.Read", "Mail.Read"})
MAX_TOKEN_RESPONSE_BYTES = 256 * 1024
MAX_TOKEN_LENGTH = 16384
REFRESH_MARGIN_SECONDS = 300

_RECORD_FIELDS = frozenset({
    "provider", "owner_email", "mailbox_id", "email", "account_id",
    "credential_generation", "access_token", "refresh_token", "token_type",
    "scope", "expires_at", "created_at", "updated_at",
})


class MicrosoftTokenError(Exception):
    """Only a fixed non-sensitive error code may be exposed to callers."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MicrosoftMailboxAuthority:
    owner_email: str
    mailbox_id: str
    email: str
    account_id: str
    credential_generation: str


def _canonical_authority(authority: MicrosoftMailboxAuthority) -> MicrosoftMailboxAuthority:
    if not isinstance(authority, MicrosoftMailboxAuthority):
        raise MicrosoftTokenError("microsoft_authority_invalid")
    if (
        not isinstance(authority.owner_email, str)
        or not isinstance(authority.email, str)
        or not isinstance(authority.mailbox_id, str)
        or not isinstance(authority.account_id, str)
        or not isinstance(authority.credential_generation, str)
    ):
        raise MicrosoftTokenError("microsoft_authority_invalid")
    owner = normalize_auth_email(authority.owner_email)
    email = normalize_auth_email(authority.email)
    if (
        not is_valid_auth_email(owner)
        or not is_valid_auth_email(email)
        or not MAILBOX_ID_PATTERN.fullmatch(authority.mailbox_id)
        or not GENERATION_PATTERN.fullmatch(authority.credential_generation)
        or not 1 <= len(authority.account_id) <= 256
        or authority.account_id != authority.account_id.strip()
        or any(not 33 <= ord(c) <= 126 for c in authority.account_id)
    ):
        raise MicrosoftTokenError("microsoft_authority_invalid")
    return MicrosoftMailboxAuthority(
        owner, authority.mailbox_id, email,
        authority.account_id, authority.credential_generation,
    )


def _key(authority: MicrosoftMailboxAuthority) -> str:
    canonical = _canonical_authority(authority)
    identity = (canonical.owner_email + "\x00" + canonical.mailbox_id).encode("utf-8")
    return KEY_PREFIX + hashlib.sha256(identity).hexdigest()


def _durable_config() -> dict:
    config = oauth_store._resolve_durable_store_config()
    if not config:
        raise MicrosoftTokenError("microsoft_token_store_unavailable")
    return config


def _snapshot(config: dict, key: str) -> tuple[dict | None, str | None]:
    record, serialized, error = oauth_store._read_durable_record_snapshot(config, key)
    if error:
        raise MicrosoftTokenError("microsoft_token_store_unavailable")
    if record is not None and (
        not isinstance(record, dict) or not isinstance(serialized, str)
    ):
        raise MicrosoftTokenError("microsoft_token_store_unavailable")
    return record, serialized


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise MicrosoftTokenError("microsoft_token_record_invalid")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise MicrosoftTokenError("microsoft_token_record_invalid") from None
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise MicrosoftTokenError("microsoft_token_record_invalid")
    return stamp


def _safe_token(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not 12 <= len(value) <= MAX_TOKEN_LENGTH
        or any(not 33 <= ord(c) <= 126 for c in value)
    ):
        raise MicrosoftTokenError("microsoft_token_payload_invalid")
    return value


def _validate_scopes(value: object) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise MicrosoftTokenError("microsoft_reconnect_required")
    raw_scopes = set(value.split())
    allowed = {
        "User.Read": {"User.Read", "https://graph.microsoft.com/User.Read"},
        "Mail.Read": {"Mail.Read", "https://graph.microsoft.com/Mail.Read"},
    }
    if any(not (allowed[name] & raw_scopes) for name in REQUIRED_SCOPES):
        raise MicrosoftTokenError("microsoft_reconnect_required")
    return value


def _validate_record(record: object, authority: MicrosoftMailboxAuthority) -> dict:
    auth = _canonical_authority(authority)
    if not isinstance(record, dict) or frozenset(record) != _RECORD_FIELDS:
        raise MicrosoftTokenError("microsoft_token_record_invalid")
    if (
        record.get("provider") != "microsoft"
        or record.get("owner_email") != auth.owner_email
        or record.get("mailbox_id") != auth.mailbox_id
        or record.get("email") != auth.email
        or record.get("account_id") != auth.account_id
        or record.get("credential_generation") != auth.credential_generation
    ):
        raise MicrosoftTokenError("microsoft_reconnect_required")
    try:
        _safe_token(record.get("access_token"), field="access")
        _safe_token(record.get("refresh_token"), field="refresh")
        _validate_scopes(record.get("scope"))
        _parse_utc(record.get("expires_at"))
        _parse_utc(record.get("created_at"))
        _parse_utc(record.get("updated_at"))
    except MicrosoftTokenError:
        raise MicrosoftTokenError("microsoft_token_record_invalid") from None
    if record.get("token_type") != "Bearer":
        raise MicrosoftTokenError("microsoft_token_record_invalid")
    return record


def _validated_payload(payload: object, *, existing: dict | None = None) -> dict:
    token_type = payload.get("token_type") if isinstance(payload, dict) else None
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise MicrosoftTokenError("microsoft_token_payload_invalid")
    access = _safe_token(payload.get("access_token"), field="access")
    refresh_candidate = payload.get("refresh_token")
    refresh = (
        _safe_token(refresh_candidate, field="refresh")
        if refresh_candidate is not None
        else existing.get("refresh_token") if existing else None
    )
    if not refresh:
        raise MicrosoftTokenError("microsoft_token_payload_invalid")
    expiry_seconds = payload.get("expires_in")
    if type(expiry_seconds) is not int or not 1 <= expiry_seconds <= 86400:
        raise MicrosoftTokenError("microsoft_token_payload_invalid")
    scope_candidate = payload.get("scope")
    scope = scope_candidate if isinstance(scope_candidate, str) else (
        existing.get("scope") if existing else None
    )
    try:
        _validate_scopes(scope)
    except MicrosoftTokenError:
        raise MicrosoftTokenError("microsoft_token_payload_invalid") from None
    return {
        "access_token": access,
        "refresh_token": refresh,
        "scope": scope,
        "expires_in": expiry_seconds,
    }


def _new_record(
    authority: MicrosoftMailboxAuthority,
    token_payload: dict,
    *,
    now: datetime,
    existing: dict | None = None,
) -> dict:
    auth = _canonical_authority(authority)
    p = _validated_payload(token_payload, existing=existing)
    return {
        "provider": "microsoft",
        "owner_email": auth.owner_email,
        "mailbox_id": auth.mailbox_id,
        "email": auth.email,
        "account_id": auth.account_id,
        "credential_generation": auth.credential_generation,
        "access_token": p["access_token"],
        "refresh_token": p["refresh_token"],
        "token_type": "Bearer",
        "scope": p["scope"],
        "expires_at": (now + timedelta(seconds=p["expires_in"])).isoformat(),
        "created_at": existing["created_at"] if existing else now.isoformat(),
        "updated_at": now.isoformat(),
    }


def _commit(config: dict, key: str, serialized: str | None, record: dict) -> dict:
    # Reuse the tested generic Redis EVAL-CAS helper. It never sets an
    # expiration and verifies the exact serialized record after the write.
    stored, error = oauth_store._write_google_durable_record_if_unchanged(
        config, key, serialized, record,
    )
    if error:
        code = error.get("code") if isinstance(error, dict) else None
        if code == "gmail_token_write_conflict":
            raise MicrosoftTokenError("microsoft_token_write_conflict")
        raise MicrosoftTokenError("microsoft_token_store_unavailable")
    if stored != record:
        raise MicrosoftTokenError("microsoft_token_store_unavailable")
    return stored


def persist_owned_microsoft_token(
    authority: MicrosoftMailboxAuthority,
    token_payload: dict,
    *,
    mode: str = "connect",
    previous_generation: str | None = None,
    now: datetime | None = None,
) -> dict:
    """CAS-create/reconnect only after authoritative mailbox ownership checks.

    For reconnect, exact owner, mailbox, email and Graph account must match the
    old token. Callers must update the managed mailbox generation in one
    separately verified step and must not expose the record to the client.
    """
    auth = _canonical_authority(authority)
    if mode not in {"connect", "reconnect"}:
        raise MicrosoftTokenError("microsoft_authority_invalid")
    if mode == "connect" and previous_generation is not None:
        raise MicrosoftTokenError("microsoft_authority_invalid")
    if mode == "reconnect" and (
        not isinstance(previous_generation, str)
        or not GENERATION_PATTERN.fullmatch(previous_generation)
        or previous_generation == auth.credential_generation
    ):
        raise MicrosoftTokenError("microsoft_authority_invalid")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() != timedelta(0):
        raise MicrosoftTokenError("microsoft_authority_invalid")
    config = _durable_config()
    key = _key(auth)
    old, serialized = _snapshot(config, key)
    if mode == "connect":
        if old is not None:
            raise MicrosoftTokenError("microsoft_token_write_conflict")
        next_record = _new_record(auth, token_payload, now=current)
    else:
        previous = MicrosoftMailboxAuthority(
            auth.owner_email, auth.mailbox_id, auth.email, auth.account_id, previous_generation
        )
        _validate_record(old, previous)
        # A newly approved OAuth consent must include a fresh refresh token,
        # not silently retain credentials from an old grant.
        if not isinstance(token_payload, dict) or not token_payload.get("refresh_token"):
            raise MicrosoftTokenError("microsoft_token_payload_invalid")
        next_record = _new_record(auth, token_payload, now=current, existing=old)
    return _commit(config, key, serialized, next_record)


def load_owned_microsoft_token(authority: MicrosoftMailboxAuthority) -> dict:
    auth = _canonical_authority(authority)
    current, _serialized = _snapshot(_durable_config(), _key(auth))
    if current is None:
        raise MicrosoftTokenError("microsoft_reconnect_required")
    return _validate_record(current, auth)


def read_owned_microsoft_token_generation(
    authority: MicrosoftMailboxAuthority,
) -> str | None:
    """Inspect one durable token generation without exposing credentials.

    The key is derived only from the authenticated owner and exact mailbox;
    every other stored authority field is validated before returning the
    generation. None means no token exists, not a recoverable store failure.
    """
    auth = _canonical_authority(authority)
    stored, _serialized = _snapshot(_durable_config(), _key(auth))
    if stored is None:
        return None
    if type(stored) is not dict:
        raise MicrosoftTokenError("microsoft_token_record_invalid")
    generation = stored.get("credential_generation")
    if type(generation) is not str or not GENERATION_PATTERN.fullmatch(generation):
        raise MicrosoftTokenError("microsoft_token_record_invalid")
    actual = MicrosoftMailboxAuthority(
        auth.owner_email, auth.mailbox_id, auth.email, auth.account_id, generation,
    )
    _validate_record(stored, actual)
    return generation


def _exchange_refresh(refresh_token: str, *, opener=urlopen) -> dict:
    client_id = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
    client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
    tenant = os.getenv("MICROSOFT_OAUTH_TENANT", "").strip() or "common"
    if (
        not 8 <= len(client_id) <= 256
        or not 8 <= len(client_secret) <= 2048
        or not TENANT_PATTERN.fullmatch(tenant)
    ):
        raise MicrosoftTokenError("microsoft_oauth_configuration_unavailable")
    payload = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }).encode("utf-8")
    request = Request(
        "https://login.microsoftonline.com/" + tenant + "/oauth2/v2.0/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with opener(request, timeout=20) as response:
            body = response.read(MAX_TOKEN_RESPONSE_BYTES + 1)
    except HTTPError as error:
        status = error.code
        try:
            error.close()
        except Exception:
            pass
        if status in (400, 401, 403):
            raise MicrosoftTokenError("microsoft_reconnect_required") from None
        raise MicrosoftTokenError("microsoft_refresh_unavailable") from None
    except (URLError, TimeoutError, OSError, IncompleteRead):
        raise MicrosoftTokenError("microsoft_refresh_unavailable") from None
    if not isinstance(body, bytes) or len(body) > MAX_TOKEN_RESPONSE_BYTES:
        raise MicrosoftTokenError("microsoft_refresh_unavailable")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MicrosoftTokenError("microsoft_refresh_unavailable") from None
    if not isinstance(parsed, dict):
        raise MicrosoftTokenError("microsoft_refresh_unavailable")
    return parsed


def _read_concurrent_refresh_winner(
    config: dict,
    key: str,
    authority: MicrosoftMailboxAuthority,
    previous: dict,
    *,
    now: datetime,
) -> dict | None:
    """A rotated refresh token may invalidate simultaneous old-token requests.

    Accept only an already-persisted, still-valid replacement in the exact
    same authority generation. A new reconnect generation is never adopted.
    """
    winner, _ = _snapshot(config, key)
    winner = _validate_record(winner, authority)
    if (
        winner["access_token"] != previous["access_token"]
        and _parse_utc(winner["expires_at"]) > now
    ):
        return winner
    return None


def ensure_owned_microsoft_access_token(
    authority: MicrosoftMailboxAuthority,
    *,
    now: datetime | None = None,
    opener=urlopen,
) -> dict:
    """Read or atomically refresh the exact owned Microsoft token record."""
    auth = _canonical_authority(authority)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() != timedelta(0):
        raise MicrosoftTokenError("microsoft_authority_invalid")
    config = _durable_config()
    key = _key(auth)
    old, serialized = _snapshot(config, key)
    old = _validate_record(old, auth)
    if _parse_utc(old["expires_at"]) > current + timedelta(seconds=REFRESH_MARGIN_SECONDS):
        return old
    try:
        fresh_payload = _exchange_refresh(old["refresh_token"], opener=opener)
    except MicrosoftTokenError as error:
        if error.code != "microsoft_reconnect_required":
            raise
        # Another worker may have rotated the refresh token in the meantime.
        winner = _read_concurrent_refresh_winner(
            config, key, auth, old, now=current,
        )
        if winner is not None:
            return winner
        raise

    next_record = _new_record(auth, fresh_payload, now=current, existing=old)
    try:
        return _commit(config, key, serialized, next_record)
    except MicrosoftTokenError as error:
        if error.code != "microsoft_token_write_conflict":
            raise
        # A concurrent refresh or reconnect won. Never overwrite it.
        winner = _read_concurrent_refresh_winner(
            config, key, auth, old, now=current,
        )
        if winner is not None:
            return winner
        raise MicrosoftTokenError("microsoft_token_write_conflict") from None
