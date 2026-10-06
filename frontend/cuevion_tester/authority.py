"""Server-only authority for standalone Cuevion tester invitations.

This module is intentionally not wired to an HTTP route or Auth0 callback yet.
It provides the durable invitation boundary for T1: only a globally allowlisted
Cuevion account may issue/cancel a tester invite; the raw bearer is returned once
and is never stored; and provisioning is an atomic terminal transition that binds
the invitation to one canonical Cuevion owner user/workspace pair.
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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from api.auth.email_address import is_valid_auth_email, normalize_auth_email
from api.auth.runtime import AuthenticatedMemberContext


TESTER_AUTHORITY_SCHEMA_VERSION = 1
TESTER_INVITE_TOKEN_BYTES = 32
TESTER_INVITE_ID_BYTES = 16
TESTER_INVITE_TTL_MS = 7 * 24 * 60 * 60 * 1000
TESTER_INVITE_STATUSES = frozenset({"invited", "cancelled", "provisioned"})
TESTER_INVITE_SOURCES = frozenset({"direct", "early_access"})

_KV_URL_ENV = "KV_REST_API_URL"
_KV_TOKEN_ENV = "KV_REST_API_TOKEN"
_TESTER_ADMIN_USER_IDS_ENV = "CUEVION_TESTER_ADMIN_USER_IDS"
_KV_TIMEOUT_SECONDS = 10
_KV_MAX_RESPONSE_BYTES = 256 * 1024
_TESTER_PREFIX = "cuevion:tester:v1"

_INVITATION_ID_RE = re.compile(r"tsti_[A-Za-z0-9_-]{1,64}")
_TOKEN_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_TOKEN_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_USER_ID_RE = re.compile(r"usr_[A-Za-z0-9_-]{21}[AQgw]")
_WORKSPACE_ID_RE = re.compile(r"wsp_[A-Za-z0-9_-]{21}[AQgw]")
_SOURCE_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")

TesterError = dict[str, str]
CommandTransport = Callable[[list[object]], dict[str, object]]
Clock = Callable[[], int]
RandomBytes = Callable[[int], bytes]
TesterAdminValidator = Callable[[AuthenticatedMemberContext], object]


class TesterInviteAuthorityError(Exception):
    """Redacted failure at the server-only tester provisioning boundary."""

    __slots__ = ("code",)

    def __init__(self, code: str = "tester_authority_unavailable") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class ProvisioningTesterInvite:
    """Validated tester intent. The raw invite bearer is never retained."""

    invitation_id: str
    email: str
    display_name: str
    token_digest: str = field(repr=False)
    status: str
    expires_at: int
    source: str
    source_request_id: str | None = None
    provisioned_user_id: str | None = None
    provisioned_workspace_id: str | None = None

    def __repr__(self) -> str:
        return "<ProvisioningTesterInvite>"


def _error(code: str, message: str) -> TesterError:
    return {"code": code, "message": message}


def _unavailable_error() -> TesterError:
    return _error(
        "tester_authority_unavailable",
        "Tester invitation authority is temporarily unavailable.",
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _valid_invitation_id(value: object) -> bool:
    return type(value) is str and _INVITATION_ID_RE.fullmatch(value) is not None


def _valid_token_digest(value: object) -> bool:
    return type(value) is str and _TOKEN_DIGEST_RE.fullmatch(value) is not None


def _valid_user_id(value: object) -> bool:
    return type(value) is str and _USER_ID_RE.fullmatch(value) is not None


def _valid_workspace_id(value: object) -> bool:
    return type(value) is str and _WORKSPACE_ID_RE.fullmatch(value) is not None


def _normalize_email(value: object) -> str:
    if type(value) is not str:
        return ""
    normalized = normalize_auth_email(value)
    return normalized if is_valid_auth_email(normalized) else ""


def _normalize_display_name(value: object) -> str:
    if type(value) is not str:
        return ""
    normalized = value.strip()
    try:
        encoded_length = len(normalized.encode("utf-8"))
    except UnicodeEncodeError:
        return ""
    if (
        not normalized
        or encoded_length > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        return ""
    return normalized


def _normalize_source_request_id(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or value != value.strip():
        return None
    return value if _SOURCE_REQUEST_ID_RE.fullmatch(value) is not None else None


def _parse_invitation_token(token: object) -> tuple[str, str] | None:
    if type(token) is not str or token != token.strip() or token.count(".") != 1:
        return None
    invitation_id, secret = token.split(".", 1)
    if (
        not _valid_invitation_id(invitation_id)
        or _TOKEN_SECRET_RE.fullmatch(secret) is None
    ):
        return None
    return invitation_id, secret


def generate_invitation_id(*, random_bytes: RandomBytes = secrets.token_bytes) -> str:
    value = random_bytes(TESTER_INVITE_ID_BYTES)
    if type(value) is not bytes or len(value) != TESTER_INVITE_ID_BYTES:
        raise ValueError("invalid tester invitation randomness")
    return "tsti_" + _base64url(value)


def generate_invitation_token(
    invitation_id: str,
    *,
    random_bytes: RandomBytes = secrets.token_bytes,
) -> tuple[str, str]:
    if not _valid_invitation_id(invitation_id):
        raise ValueError("invalid tester invitation id")
    value = random_bytes(TESTER_INVITE_TOKEN_BYTES)
    if type(value) is not bytes or len(value) != TESTER_INVITE_TOKEN_BYTES:
        raise ValueError("invalid tester invitation randomness")
    raw_token = f"{invitation_id}.{_base64url(value)}"
    return raw_token, hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def verify_invitation_token(raw_token: object, token_digest: object) -> bool:
    parsed = _parse_invitation_token(raw_token)
    if parsed is None or not _valid_token_digest(token_digest):
        return False
    candidate = hashlib.sha256(str(raw_token).encode("ascii")).hexdigest()
    return hmac.compare_digest(candidate, str(token_digest))


def _require_actor(actor: object) -> AuthenticatedMemberContext:
    if (
        type(actor) is not AuthenticatedMemberContext
        or not _valid_user_id(actor.user_id)
    ):
        raise ValueError("invalid tester admin actor")
    return actor


def build_invitation_record(
    *,
    invitation_id: str,
    actor: AuthenticatedMemberContext,
    invitee_email: str,
    invitee_name: str,
    token_digest: str,
    now_ms: int,
    source: str = "direct",
    source_request_id: str | None = None,
) -> dict[str, object]:
    canonical_actor = _require_actor(actor)
    canonical_email = _normalize_email(invitee_email)
    canonical_name = _normalize_display_name(invitee_name)
    normalized_request_id = _normalize_source_request_id(source_request_id)
    if (
        not _valid_invitation_id(invitation_id)
        or not canonical_email
        or not canonical_name
        or not _valid_token_digest(token_digest)
        or type(now_ms) is not int
        or now_ms < 0
        or type(source) is not str
        or source not in TESTER_INVITE_SOURCES
        or (source == "direct" and source_request_id is not None)
        or (
            source == "early_access"
            and (source_request_id is None or normalized_request_id is None)
        )
    ):
        raise ValueError("invalid tester invitation record")
    record: dict[str, object] = {
        "v": TESTER_AUTHORITY_SCHEMA_VERSION,
        "id": invitation_id,
        "inviteeEmail": canonical_email,
        "displayName": canonical_name,
        "status": "invited",
        "source": source,
        "createdAt": now_ms,
        "updatedAt": now_ms,
        "expiresAt": now_ms + TESTER_INVITE_TTL_MS,
        "createdByUserId": canonical_actor.user_id,
        "tokenDigest": token_digest,
    }
    if normalized_request_id is not None:
        record["sourceRequestId"] = normalized_request_id
    return record


def _normalize_invitation_record(value: object) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    required = {
        "v",
        "id",
        "inviteeEmail",
        "displayName",
        "status",
        "source",
        "createdAt",
        "updatedAt",
        "expiresAt",
        "createdByUserId",
        "tokenDigest",
    }
    optional = {
        "sourceRequestId",
        "cancelledAt",
        "provisionedAt",
        "provisionedUserId",
        "provisionedWorkspaceId",
    }
    if not required.issubset(value) or not set(value).issubset(required | optional):
        return None

    invitation_id = value.get("id")
    email = _normalize_email(value.get("inviteeEmail"))
    display_name = _normalize_display_name(value.get("displayName"))
    status = value.get("status")
    source = value.get("source")
    created_at = value.get("createdAt")
    updated_at = value.get("updatedAt")
    expires_at = value.get("expiresAt")
    created_by_user_id = value.get("createdByUserId")
    token_digest = value.get("tokenDigest")
    source_request_id = _normalize_source_request_id(value.get("sourceRequestId"))

    if (
        type(value.get("v")) is not int
        or value.get("v") != TESTER_AUTHORITY_SCHEMA_VERSION
        or not _valid_invitation_id(invitation_id)
        or not email
        or not display_name
        or type(status) is not str
        or status not in TESTER_INVITE_STATUSES
        or type(source) is not str
        or source not in TESTER_INVITE_SOURCES
        or type(created_at) is not int
        or type(updated_at) is not int
        or type(expires_at) is not int
        or created_at < 0
        or not created_at <= updated_at
        or expires_at != created_at + TESTER_INVITE_TTL_MS
        or not _valid_user_id(created_by_user_id)
        or not _valid_token_digest(token_digest)
        or (source == "direct" and "sourceRequestId" in value)
        or (
            source == "early_access"
            and ("sourceRequestId" not in value or source_request_id is None)
        )
    ):
        return None

    normalized: dict[str, object] = {
        "v": TESTER_AUTHORITY_SCHEMA_VERSION,
        "id": invitation_id,
        "inviteeEmail": email,
        "displayName": display_name,
        "status": status,
        "source": source,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "expiresAt": expires_at,
        "createdByUserId": created_by_user_id,
        "tokenDigest": token_digest,
    }
    if source_request_id is not None:
        normalized["sourceRequestId"] = source_request_id

    if status == "invited":
        if any(
            field in value
            for field in (
                "cancelledAt",
                "provisionedAt",
                "provisionedUserId",
                "provisionedWorkspaceId",
            )
        ):
            return None
    elif status == "cancelled":
        cancelled_at = value.get("cancelledAt")
        if (
            type(cancelled_at) is not int
            or not created_at <= cancelled_at == updated_at
            or any(
                field in value
                for field in (
                    "provisionedAt",
                    "provisionedUserId",
                    "provisionedWorkspaceId",
                )
            )
        ):
            return None
        normalized["cancelledAt"] = cancelled_at
    else:
        provisioned_at = value.get("provisionedAt")
        provisioned_user_id = value.get("provisionedUserId")
        provisioned_workspace_id = value.get("provisionedWorkspaceId")
        if (
            type(provisioned_at) is not int
            or not created_at <= provisioned_at == updated_at
            or not _valid_user_id(provisioned_user_id)
            or not _valid_workspace_id(provisioned_workspace_id)
            or "cancelledAt" in value
        ):
            return None
        normalized["provisionedAt"] = provisioned_at
        normalized["provisionedUserId"] = provisioned_user_id
        normalized["provisionedWorkspaceId"] = provisioned_workspace_id
    return normalized


def project_public_invitation(value: object) -> dict[str, object] | None:
    record = _normalize_invitation_record(value)
    if record is None:
        return None
    return {
        "displayName": record["displayName"],
        "status": record["status"],
        "expiresAt": record["expiresAt"],
    }


def project_admin_invitation(value: object) -> dict[str, object] | None:
    record = _normalize_invitation_record(value)
    if record is None:
        return None
    projected = {
        "id": record["id"],
        "inviteeEmail": record["inviteeEmail"],
        "displayName": record["displayName"],
        "status": record["status"],
        "source": record["source"],
        "expiresAt": record["expiresAt"],
    }
    if "sourceRequestId" in record:
        projected["sourceRequestId"] = record["sourceRequestId"]
    return projected


def _invitation_token_key(invitation_id: str, token_digest: str) -> str:
    return f"{_TESTER_PREFIX}:invite-token:{invitation_id}:{token_digest}"


def _invitation_key(invitation_id: str) -> str:
    return f"{_TESTER_PREFIX}:invite:{invitation_id}"


def _recipient_key(email: str) -> str:
    return f"{_TESTER_PREFIX}:recipient:{email}"


_ISSUE_INVITATION_LUA = r"""
local current_raw = redis.call('GET', KEYS[3])
if current_raw then
  local ok, current = pcall(cjson.decode, current_raw)
  if not ok or type(current) ~= 'table' or type(current.status) ~= 'string'
     or type(current.expiresAt) ~= 'number' then return 'malformed' end
  if current.status == 'invited' and current.expiresAt > tonumber(ARGV[2]) then
    return 'invite_live'
  end
  if current.status == 'provisioned' then return 'account_provisioned' end
end
if redis.call('GET', KEYS[1]) or redis.call('GET', KEYS[2]) then return 'collision' end
redis.call('SET', KEYS[1], ARGV[1])
redis.call('SET', KEYS[2], ARGV[1])
redis.call('SET', KEYS[3], ARGV[1])
return 'applied'
""".strip()


_TRANSITION_INVITATION_LUA = r"""
local token_raw = redis.call('GET', KEYS[1])
local invite_raw = redis.call('GET', KEYS[2])
local recipient_raw = redis.call('GET', KEYS[3])
if not token_raw or token_raw ~= ARGV[1]
   or invite_raw ~= ARGV[1] or recipient_raw ~= ARGV[1] then return 'stale' end
local ok, current = pcall(cjson.decode, token_raw)
if not ok or type(current) ~= 'table' or type(current.status) ~= 'string'
   or type(current.expiresAt) ~= 'number' then return 'malformed' end
if current.status ~= 'invited' then return current.status end
if current.expiresAt <= tonumber(ARGV[3]) then return 'expired' end
redis.call('SET', KEYS[1], ARGV[2])
redis.call('SET', KEYS[2], ARGV[2])
redis.call('SET', KEYS[3], ARGV[2])
return 'applied'
""".strip()


_SNAPSHOT_LUA = r"""
local values = {}
for index = 1, #KEYS do
  values[index] = redis.call('GET', KEYS[index]) or false
end
return values
""".strip()


ATOMIC_MUTATION_SCRIPTS = {
    "issue": _ISSUE_INVITATION_LUA,
    "transition": _TRANSITION_INVITATION_LUA,
}


def _resolve_store_config(environment: Mapping[str, str]) -> tuple[str, str] | None:
    try:
        rest_url = environment[_KV_URL_ENV]
        rest_token = environment[_KV_TOKEN_ENV]
    except Exception:
        return None
    if (
        type(rest_url) is not str
        or type(rest_token) is not str
        or not rest_url
        or not rest_token
        or rest_url != rest_url.strip()
        or rest_token != rest_token.strip()
        or not rest_url.startswith("https://")
    ):
        return None
    return rest_url.rstrip("/"), rest_token


def _runtime_transport(config: tuple[str, str]) -> CommandTransport:
    rest_url, rest_token = config

    def perform(command: list[object]) -> dict[str, object]:
        request = Request(
            rest_url,
            data=_canonical_json(command).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {rest_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=_KV_TIMEOUT_SECONDS) as response:
                raw = response.read(_KV_MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError):
            raise RuntimeError("Tester store unavailable") from None
        if len(raw) > _KV_MAX_RESPONSE_BYTES:
            raise RuntimeError("Tester store unavailable")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise RuntimeError("Tester store unavailable") from None
        if type(payload) is not dict:
            raise RuntimeError("Tester store unavailable")
        return payload

    return perform


def _runtime_admin_validator(
    environment: Mapping[str, str],
) -> TesterAdminValidator:
    raw = environment.get(_TESTER_ADMIN_USER_IDS_ENV, "")
    values = [value.strip() for value in raw.split(",") if value.strip()]
    allowed = (
        frozenset(values)
        if values and all(_valid_user_id(value) for value in values)
        else frozenset()
    )

    def validate(actor: AuthenticatedMemberContext) -> object:
        return actor.user_id in allowed

    return validate


class RuntimeTesterInviteAuthority:
    """Narrow invitation authority; no account or workspace write occurs here."""

    __slots__ = (
        "_transport",
        "_now_ms",
        "_random_bytes",
        "_admin_validator",
    )

    def __init__(
        self,
        command_transport: CommandTransport | None,
        *,
        now_ms: Clock | None = None,
        random_bytes: RandomBytes = secrets.token_bytes,
        admin_validator: TesterAdminValidator | None = None,
    ) -> None:
        self._transport = command_transport
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._random_bytes = random_bytes
        self._admin_validator = admin_validator

    def _command(self, command: list[object]) -> object:
        if self._transport is None:
            raise TesterInviteAuthorityError()
        try:
            payload = self._transport(command)
        except Exception:
            raise TesterInviteAuthorityError() from None
        if type(payload) is not dict or set(payload) != {"result"}:
            raise TesterInviteAuthorityError()
        return payload["result"]

    def _get_primary_raw(self, key: str) -> str | None:
        result = self._command(
            ["EVAL", "return redis.call('GET', KEYS[1])", 1, key]
        )
        if result is None:
            return None
        if type(result) is not str:
            raise TesterInviteAuthorityError()
        return result

    def _snapshot(self, keys: list[str]) -> list[str]:
        result = self._command(["EVAL", _SNAPSHOT_LUA, len(keys), *keys])
        if (
            type(result) is not list
            or len(result) != len(keys)
            or any(type(item) is not str for item in result)
        ):
            raise TesterInviteAuthorityError()
        return result

    def _atomic(
        self,
        operation: str,
        keys: list[str],
        arguments: list[object],
    ) -> str:
        script = ATOMIC_MUTATION_SCRIPTS.get(operation)
        if script is None:
            raise TesterInviteAuthorityError()
        result = self._command(["EVAL", script, len(keys), *keys, *arguments])
        if type(result) is not str:
            raise TesterInviteAuthorityError()
        return result

    def _require_admin(self, actor: object) -> AuthenticatedMemberContext:
        canonical_actor = _require_actor(actor)
        validator = self._admin_validator
        if validator is None:
            raise TesterInviteAuthorityError("forbidden")
        try:
            allowed = validator(canonical_actor)
        except Exception:
            raise TesterInviteAuthorityError() from None
        if allowed is not True and allowed != "authorized":
            raise TesterInviteAuthorityError("forbidden")
        return canonical_actor

    def _read_by_token(
        self,
        token: object,
        *,
        allow_provisioned: bool,
    ) -> tuple[dict[str, object], str]:
        parsed = _parse_invitation_token(token)
        if parsed is None or type(allow_provisioned) is not bool:
            raise TesterInviteAuthorityError("invalid_invite")
        invitation_id, _secret = parsed
        digest = hashlib.sha256(str(token).encode("ascii")).hexdigest()
        token_key = _invitation_token_key(invitation_id, digest)
        raw = self._get_primary_raw(token_key)
        if raw is None:
            raise TesterInviteAuthorityError("invalid_invite")
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise TesterInviteAuthorityError() from None
        record = _normalize_invitation_record(decoded)
        if (
            record is None
            or raw != _canonical_json(record)
            or record["id"] != invitation_id
            or record["tokenDigest"] != digest
            or not verify_invitation_token(token, digest)
        ):
            raise TesterInviteAuthorityError("invalid_invite")

        snapshots = self._snapshot(
            [
                token_key,
                _invitation_key(invitation_id),
                _recipient_key(str(record["inviteeEmail"])),
            ]
        )
        if any(snapshot != raw for snapshot in snapshots):
            raise TesterInviteAuthorityError("conflict")

        now_ms = self._now_ms()
        if type(now_ms) is not int or now_ms < int(record["createdAt"]):
            raise TesterInviteAuthorityError()
        status = record["status"]
        if status == "cancelled":
            raise TesterInviteAuthorityError("cancelled_invite")
        if status == "provisioned":
            if not allow_provisioned:
                raise TesterInviteAuthorityError("used_invite")
            return record, raw
        if int(record["expiresAt"]) <= now_ms:
            raise TesterInviteAuthorityError("expired_invite")
        return record, raw

    @staticmethod
    def _project_provisioning(
        record: dict[str, object],
    ) -> ProvisioningTesterInvite:
        return ProvisioningTesterInvite(
            invitation_id=str(record["id"]),
            email=str(record["inviteeEmail"]),
            display_name=str(record["displayName"]),
            token_digest=str(record["tokenDigest"]),
            status=str(record["status"]),
            expires_at=int(record["expiresAt"]),
            source=str(record["source"]),
            source_request_id=(
                str(record["sourceRequestId"])
                if "sourceRequestId" in record
                else None
            ),
            provisioned_user_id=(
                str(record["provisionedUserId"])
                if "provisionedUserId" in record
                else None
            ),
            provisioned_workspace_id=(
                str(record["provisionedWorkspaceId"])
                if "provisionedWorkspaceId" in record
                else None
            ),
        )

    def issue_invitation(
        self,
        *,
        actor: AuthenticatedMemberContext,
        invitee_email: str,
        invitee_name: str,
        source: str = "direct",
        source_request_id: str | None = None,
    ) -> tuple[dict[str, object] | None, TesterError | None]:
        try:
            canonical_actor = self._require_admin(actor)
        except TesterInviteAuthorityError as error:
            return None, (
                _error("forbidden", "Tester admin authority is required.")
                if error.code == "forbidden"
                else _unavailable_error()
            )

        email = _normalize_email(invitee_email)
        name = _normalize_display_name(invitee_name)
        if not email or not name:
            return None, _error(
                "invalid_request",
                "Tester invitation fields are invalid.",
            )
        if email == _normalize_email(canonical_actor.email):
            return None, _error(
                "self_invite",
                "A tester admin cannot invite their own active account.",
            )

        try:
            now_ms = self._now_ms()
            invitation_id = generate_invitation_id(
                random_bytes=self._random_bytes
            )
            raw_token, token_digest = generate_invitation_token(
                invitation_id,
                random_bytes=self._random_bytes,
            )
            record = build_invitation_record(
                invitation_id=invitation_id,
                actor=canonical_actor,
                invitee_email=email,
                invitee_name=name,
                token_digest=token_digest,
                now_ms=now_ms,
                source=source,
                source_request_id=source_request_id,
            )
        except Exception:
            return None, _unavailable_error()

        record_wire = _canonical_json(record)
        try:
            result = self._atomic(
                "issue",
                [
                    _invitation_token_key(invitation_id, token_digest),
                    _invitation_key(invitation_id),
                    _recipient_key(email),
                ],
                [record_wire, str(now_ms)],
            )
        except TesterInviteAuthorityError:
            return None, _unavailable_error()

        if result == "invite_live":
            return None, _error(
                "live_invitation_exists",
                "A live tester invitation already exists.",
            )
        if result == "account_provisioned":
            return None, _error(
                "account_already_provisioned",
                "This recipient already provisioned Cuevion access.",
            )
        if result != "applied":
            return None, _unavailable_error()

        try:
            snapshots = self._snapshot(
                [
                    _invitation_token_key(invitation_id, token_digest),
                    _invitation_key(invitation_id),
                    _recipient_key(email),
                ]
            )
        except TesterInviteAuthorityError:
            return None, _unavailable_error()
        if any(snapshot != record_wire for snapshot in snapshots):
            return None, _unavailable_error()

        projection = project_admin_invitation(record)
        if projection is None:
            return None, _unavailable_error()
        return {"invite": projection, "rawToken": raw_token}, None

    def lookup_invitation(
        self,
        *,
        token: str,
    ) -> tuple[dict[str, object] | None, TesterError | None]:
        try:
            record, _raw = self._read_by_token(
                token,
                allow_provisioned=True,
            )
        except TesterInviteAuthorityError as error:
            mapping = {
                "invalid_invite": _error(
                    "invalid_invite", "Tester invitation was not found."
                ),
                "expired_invite": _error(
                    "expired_invite", "Tester invitation has expired."
                ),
                "cancelled_invite": _error(
                    "cancelled_invite", "Tester invitation was cancelled."
                ),
            }
            return None, mapping.get(error.code, _unavailable_error())

        projection = project_public_invitation(record)
        return (
            (projection, None)
            if projection is not None
            else (None, _unavailable_error())
        )

    def read_provisioning_invitation(
        self,
        token: str,
        *,
        allow_provisioned: bool = False,
    ) -> ProvisioningTesterInvite:
        record, _raw = self._read_by_token(
            token,
            allow_provisioned=allow_provisioned,
        )
        return self._project_provisioning(record)

    def cancel_invitation(
        self,
        *,
        actor: AuthenticatedMemberContext,
        invitation_id: str,
    ) -> tuple[dict[str, object] | None, TesterError | None]:
        try:
            self._require_admin(actor)
        except TesterInviteAuthorityError as error:
            return None, (
                _error("forbidden", "Tester admin authority is required.")
                if error.code == "forbidden"
                else _unavailable_error()
            )
        if not _valid_invitation_id(invitation_id):
            return None, _error(
                "invalid_request",
                "Tester invitation id is invalid.",
            )
        try:
            raw = self._get_primary_raw(_invitation_key(invitation_id))
            decoded = json.loads(raw) if raw is not None else None
            record = _normalize_invitation_record(decoded)
        except Exception:
            return None, _unavailable_error()
        if record is None or raw is None or raw != _canonical_json(record):
            return None, _error(
                "invalid_invite",
                "Tester invitation was not found.",
            )
        now_ms = self._now_ms()
        if record["status"] == "provisioned":
            return None, _error(
                "used_invite",
                "Tester invitation has already been provisioned.",
            )
        if record["status"] == "cancelled":
            projection = project_admin_invitation(record)
            return (projection, None) if projection else (None, _unavailable_error())
        if int(record["expiresAt"]) <= now_ms:
            return None, _error(
                "expired_invite",
                "Tester invitation has expired.",
            )
        cancelled = {
            **record,
            "status": "cancelled",
            "updatedAt": now_ms,
            "cancelledAt": now_ms,
        }
        cancelled_wire = _canonical_json(cancelled)
        try:
            result = self._atomic(
                "transition",
                [
                    _invitation_token_key(
                        invitation_id,
                        str(record["tokenDigest"]),
                    ),
                    _invitation_key(invitation_id),
                    _recipient_key(str(record["inviteeEmail"])),
                ],
                [raw, cancelled_wire, str(now_ms)],
            )
        except TesterInviteAuthorityError:
            return None, _unavailable_error()
        if result == "expired":
            return None, _error(
                "expired_invite",
                "Tester invitation has expired.",
            )
        if result not in {"applied", "cancelled"}:
            return None, _error(
                "conflict",
                "Tester invitation changed concurrently.",
            )
        try:
            snapshots = self._snapshot(
                [
                    _invitation_token_key(
                        invitation_id,
                        str(record["tokenDigest"]),
                    ),
                    _invitation_key(invitation_id),
                    _recipient_key(str(record["inviteeEmail"])),
                ]
            )
        except TesterInviteAuthorityError:
            return None, _unavailable_error()
        if any(snapshot != cancelled_wire for snapshot in snapshots):
            return None, _unavailable_error()
        projection = project_admin_invitation(cancelled)
        return (projection, None) if projection else (None, _unavailable_error())

    def mark_provisioned(
        self,
        *,
        token: str,
        user_id: str,
        workspace_id: str,
    ) -> ProvisioningTesterInvite:
        if not _valid_user_id(user_id) or not _valid_workspace_id(workspace_id):
            raise TesterInviteAuthorityError("invalid_provisioning")
        record, raw = self._read_by_token(token, allow_provisioned=True)
        if record["status"] == "provisioned":
            if (
                record.get("provisionedUserId") != user_id
                or record.get("provisionedWorkspaceId") != workspace_id
            ):
                raise TesterInviteAuthorityError("conflict")
            return self._project_provisioning(record)

        now_ms = self._now_ms()
        provisioned = {
            **record,
            "status": "provisioned",
            "updatedAt": now_ms,
            "provisionedAt": now_ms,
            "provisionedUserId": user_id,
            "provisionedWorkspaceId": workspace_id,
        }
        provisioned_wire = _canonical_json(provisioned)
        invitation_id = str(record["id"])
        result = self._atomic(
            "transition",
            [
                _invitation_token_key(
                    invitation_id,
                    str(record["tokenDigest"]),
                ),
                _invitation_key(invitation_id),
                _recipient_key(str(record["inviteeEmail"])),
            ],
            [raw, provisioned_wire, str(now_ms)],
        )
        if result != "applied":
            if result == "expired":
                raise TesterInviteAuthorityError("expired_invite")
            raise TesterInviteAuthorityError("conflict")

        reread, _reread_raw = self._read_by_token(
            token,
            allow_provisioned=True,
        )
        if (
            reread["status"] != "provisioned"
            or reread.get("provisionedUserId") != user_id
            or reread.get("provisionedWorkspaceId") != workspace_id
        ):
            raise TesterInviteAuthorityError()
        return self._project_provisioning(reread)


def build_runtime_tester_invite_authority(
    environment: Mapping[str, str] | None = None,
    *,
    command_transport: CommandTransport | None = None,
    now_ms: Clock | None = None,
    random_bytes: RandomBytes = secrets.token_bytes,
    admin_validator: TesterAdminValidator | None = None,
) -> RuntimeTesterInviteAuthority:
    source = os.environ if environment is None else environment
    transport = command_transport
    if transport is None:
        config = _resolve_store_config(source)
        transport = _runtime_transport(config) if config is not None else None
    validator = (
        _runtime_admin_validator(source)
        if admin_validator is None
        else admin_validator
    )
    return RuntimeTesterInviteAuthority(
        transport,
        now_ms=now_ms,
        random_bytes=random_bytes,
        admin_validator=validator,
    )


__all__ = (
    "ATOMIC_MUTATION_SCRIPTS",
    "ProvisioningTesterInvite",
    "RuntimeTesterInviteAuthority",
    "TESTER_AUTHORITY_SCHEMA_VERSION",
    "TESTER_INVITE_ID_BYTES",
    "TESTER_INVITE_SOURCES",
    "TESTER_INVITE_STATUSES",
    "TESTER_INVITE_TOKEN_BYTES",
    "TESTER_INVITE_TTL_MS",
    "TesterInviteAuthorityError",
    "build_invitation_record",
    "build_runtime_tester_invite_authority",
    "generate_invitation_id",
    "generate_invitation_token",
    "project_admin_invitation",
    "project_public_invitation",
    "verify_invitation_token",
)
