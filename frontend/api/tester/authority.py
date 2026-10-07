"""Server-only Tester Invite authority.

Tester Invite is intentionally separate from Team Invite. It authorizes only an
attempt to create one new standalone Cuevion owner account. It never grants
membership in an existing workspace and never creates account authority itself.
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
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from api.auth.email_address import is_valid_auth_email, normalize_auth_email

TESTER_INVITE_SCHEMA_VERSION = 1
TESTER_INVITE_ID_BYTES = 16
TESTER_INVITE_TOKEN_BYTES = 32
TESTER_INVITE_TTL_MS = 7 * 24 * 60 * 60 * 1000
TESTER_INVITE_STATUSES = frozenset({"invited", "provisioned", "cancelled"})

_KV_URL_ENV = "KV_REST_API_URL"
_KV_TOKEN_ENV = "KV_REST_API_TOKEN"
_ADMIN_IDS_ENV = "CUEVION_TESTER_ADMIN_USER_IDS"
_NAMESPACE_ENV = "CUEVION_TESTER_AUTHORITY_NAMESPACE"
_PLATFORM_ENV = "VERCEL_ENV"
_ALLOWED_NAMESPACES = frozenset({"production", "preview", "development"})
_KV_TIMEOUT_SECONDS = 10
_KV_MAX_RESPONSE_BYTES = 128 * 1024
_BASE_PREFIX = "cuevion:tester:v1"

_INVITATION_ID_RE = re.compile(r"tsti_[A-Za-z0-9_-]{22}")
_TOKEN_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_TOKEN_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_USER_ID_RE = re.compile(r"usr_[A-Za-z0-9_-]{21}[AQgw]")
_WORKSPACE_ID_RE = re.compile(r"wsp_[A-Za-z0-9_-]{21}[AQgw]")
_SOURCE_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")

CommandTransport = Callable[[list[object]], dict[str, object]]
Clock = Callable[[], int]
RandomBytes = Callable[[int], bytes]


class TesterInviteAuthorityError(Exception):
    __slots__ = ("code",)
    _CODES = frozenset({
        "tester_authority_unavailable",
        "forbidden",
        "invalid_request",
        "invalid_invite",
        "expired_invite",
        "cancelled_invite",
        "used_invite",
        "live_invitation_exists",
        "account_already_provisioned",
        "conflict",
    })

    def __init__(self, code: str = "tester_authority_unavailable") -> None:
        self.code = code if code in self._CODES else "tester_authority_unavailable"
        Exception.__init__(self)

    def __str__(self) -> str:
        return "tester invite authority rejected the request"

    __repr__ = __str__


@dataclass(frozen=True, slots=True, repr=False)
class ProvisioningTesterInvitation:
    invitation_id: str
    email: str
    display_name: str
    token_digest: str
    status: str
    expires_at: int
    source_request_id: str | None
    provisioned_user_id: str | None
    provisioned_workspace_id: str | None

    def __repr__(self) -> str:
        return "<ProvisioningTesterInvitation>"


def _canonical_json(value: object) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _valid_invitation_id(value: object) -> bool:
    return type(value) is str and _INVITATION_ID_RE.fullmatch(value) is not None


def _valid_user_id(value: object) -> bool:
    return type(value) is str and _USER_ID_RE.fullmatch(value) is not None


def _valid_workspace_id(value: object) -> bool:
    return type(value) is str and _WORKSPACE_ID_RE.fullmatch(value) is not None


def _normalize_display_name(value: object) -> str:
    if type(value) is not str:
        return ""
    normalized = value.strip()
    try:
        encoded = normalized.encode("utf-8", errors="strict")
    except UnicodeError:
        return ""
    if not normalized or len(encoded) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in normalized):
        return ""
    return normalized


def _normalize_source_request_id(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _SOURCE_REQUEST_ID_RE.fullmatch(value) is None:
        return None
    return value


def generate_invitation_id(*, random_bytes: RandomBytes = secrets.token_bytes) -> str:
    raw = random_bytes(TESTER_INVITE_ID_BYTES)
    if type(raw) is not bytes or len(raw) != TESTER_INVITE_ID_BYTES:
        raise TesterInviteAuthorityError()
    result = "tsti_" + _base64url(raw)
    if not _valid_invitation_id(result):
        raise TesterInviteAuthorityError()
    return result


def generate_invitation_token(invitation_id: str, *, random_bytes: RandomBytes = secrets.token_bytes) -> tuple[str, str]:
    if not _valid_invitation_id(invitation_id):
        raise TesterInviteAuthorityError("invalid_request")
    raw = random_bytes(TESTER_INVITE_TOKEN_BYTES)
    if type(raw) is not bytes or len(raw) != TESTER_INVITE_TOKEN_BYTES:
        raise TesterInviteAuthorityError()
    secret = _base64url(raw)
    if _TOKEN_SECRET_RE.fullmatch(secret) is None:
        raise TesterInviteAuthorityError()
    token = f"{invitation_id}.{secret}"
    return token, hashlib.sha256(token.encode("ascii")).hexdigest()


def _parse_token(raw_token: object) -> tuple[str, str] | None:
    if type(raw_token) is not str or raw_token != raw_token.strip():
        return None
    parts = raw_token.split(".")
    if len(parts) != 2 or not _valid_invitation_id(parts[0]) or _TOKEN_SECRET_RE.fullmatch(parts[1]) is None:
        return None
    return parts[0], parts[1]


def verify_invitation_token(raw_token: object, expected_digest: object) -> bool:
    if _parse_token(raw_token) is None or type(expected_digest) is not str or _TOKEN_DIGEST_RE.fullmatch(expected_digest) is None:
        return False
    actual = hashlib.sha256(str(raw_token).encode("ascii")).hexdigest()
    return hmac.compare_digest(actual, expected_digest)


def _build_record(*, invitation_id: str, email: str, display_name: str, token_digest: str, created_by_user_id: str, now_ms: int, source_request_id: str | None) -> dict[str, object]:
    try:
        canonical_email = normalize_auth_email(email)
    except Exception:
        raise TesterInviteAuthorityError("invalid_request") from None
    name = _normalize_display_name(display_name)
    source = _normalize_source_request_id(source_request_id)
    if (
        not _valid_invitation_id(invitation_id)
        or not is_valid_auth_email(canonical_email)
        or not name
        or type(token_digest) is not str
        or _TOKEN_DIGEST_RE.fullmatch(token_digest) is None
        or not _valid_user_id(created_by_user_id)
        or type(now_ms) is not int
        or now_ms < 0
        or (source_request_id is not None and source is None)
    ):
        raise TesterInviteAuthorityError("invalid_request")
    result: dict[str, object] = {
        "v": 1,
        "id": invitation_id,
        "inviteeEmail": canonical_email,
        "inviteeName": name,
        "tokenDigest": token_digest,
        "status": "invited",
        "createdByUserId": created_by_user_id,
        "createdAt": now_ms,
        "updatedAt": now_ms,
        "expiresAt": now_ms + TESTER_INVITE_TTL_MS,
    }
    if source is not None:
        result["sourceRequestId"] = source
    return result


def _normalize_record(value: object) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    required = {"v","id","inviteeEmail","inviteeName","tokenDigest","status","createdByUserId","createdAt","updatedAt","expiresAt"}
    optional = {"sourceRequestId","cancelledAt","provisionedAt","provisionedUserId","provisionedWorkspaceId"}
    if not required.issubset(value) or not set(value).issubset(required | optional):
        return None
    try:
        email = normalize_auth_email(value["inviteeEmail"])
    except Exception:
        return None
    name = _normalize_display_name(value["inviteeName"])
    status = value["status"]
    created_at = value["createdAt"]
    updated_at = value["updatedAt"]
    expires_at = value["expiresAt"]
    source = value.get("sourceRequestId")
    if (
        type(value["v"]) is not int or value["v"] != 1
        or not _valid_invitation_id(value["id"])
        or email != value["inviteeEmail"] or not is_valid_auth_email(email)
        or name != value["inviteeName"] or not name
        or type(value["tokenDigest"]) is not str or _TOKEN_DIGEST_RE.fullmatch(value["tokenDigest"]) is None
        or type(status) is not str or status not in TESTER_INVITE_STATUSES
        or not _valid_user_id(value["createdByUserId"])
        or type(created_at) is not int or type(updated_at) is not int or type(expires_at) is not int
        or not 0 <= created_at <= updated_at < expires_at
        or (source is not None and _normalize_source_request_id(source) != source)
    ):
        return None
    terminal = {"cancelledAt","provisionedAt","provisionedUserId","provisionedWorkspaceId"}
    if status == "invited" and any(field in value for field in terminal):
        return None
    if status == "cancelled":
        cancelled_at = value.get("cancelledAt")
        if type(cancelled_at) is not int or cancelled_at != updated_at or not created_at <= cancelled_at < expires_at:
            return None
        if any(field in value for field in ("provisionedAt","provisionedUserId","provisionedWorkspaceId")):
            return None
    if status == "provisioned":
        provisioned_at = value.get("provisionedAt")
        if type(provisioned_at) is not int or provisioned_at != updated_at or not created_at <= provisioned_at < expires_at:
            return None
        if not _valid_user_id(value.get("provisionedUserId")) or not _valid_workspace_id(value.get("provisionedWorkspaceId")) or "cancelledAt" in value:
            return None
    return dict(value)


def project_public_invitation(value: object) -> dict[str, object] | None:
    record = _normalize_record(value)
    if record is None:
        return None
    return {"inviteeName": record["inviteeName"], "status": record["status"], "expiresAt": record["expiresAt"]}


def _resolve_namespace(environment: Mapping[str, str]) -> str | None:
    platform = environment.get(_PLATFORM_ENV)
    explicit = environment.get(_NAMESPACE_ENV)

    if platform is not None:
        if (
            type(platform) is not str
            or platform not in _ALLOWED_NAMESPACES
            or (
                explicit is not None
                and (
                    type(explicit) is not str
                    or explicit != platform
                )
            )
        ):
            return None
        return platform

    if (
        type(explicit) is str
        and explicit in _ALLOWED_NAMESPACES
    ):
        return explicit
    return None


def _token_key(namespace: str, invitation_id: str, digest: str) -> str:
    return f"{_BASE_PREFIX}:{namespace}:token:{invitation_id}:{digest}"


def _invitation_key(namespace: str, invitation_id: str) -> str:
    return f"{_BASE_PREFIX}:{namespace}:invite:{invitation_id}"


def _recipient_key(namespace: str, email: str) -> str:
    return f"{_BASE_PREFIX}:{namespace}:recipient:{email}"


_PRIMARY_SNAPSHOT_LUA = """
local result = {}
for index = 1, #KEYS do result[index] = redis.call('GET', KEYS[index]) or false end
return result
""".strip()

_ISSUE_INVITATION_LUA = """
local current_raw = redis.call('GET', KEYS[3])
if current_raw then
  local ok, current = pcall(cjson.decode, current_raw)
  if not ok or type(current) ~= 'table' or type(current.status) ~= 'string' then return 'malformed' end
  if current.status == 'provisioned' then return 'already_provisioned' end
  if current.status == 'invited' then
    if type(current.expiresAt) ~= 'number' then return 'malformed' end
    if current.expiresAt > tonumber(ARGV[2]) then return 'invite_live' end
  elseif current.status ~= 'cancelled' then return 'malformed' end
end
if redis.call('GET', KEYS[1]) or redis.call('GET', KEYS[2]) then return 'collision' end
redis.call('SET', KEYS[1], ARGV[1])
redis.call('SET', KEYS[2], ARGV[1])
redis.call('SET', KEYS[3], ARGV[1])
return 'applied'
""".strip()

_TRANSITION_INVITATION_LUA = """
local current = redis.call('GET', KEYS[1])
if not current or current ~= ARGV[1] then return 'stale' end
if redis.call('GET', KEYS[2]) ~= ARGV[1] or redis.call('GET', KEYS[3]) ~= ARGV[1] then return 'stale' end
redis.call('SET', KEYS[1], ARGV[2])
redis.call('SET', KEYS[2], ARGV[2])
redis.call('SET', KEYS[3], ARGV[2])
return 'applied'
""".strip()


def _resolve_store_config(environment: Mapping[str, str]) -> tuple[str, str] | None:
    try:
        url, token = environment[_KV_URL_ENV], environment[_KV_TOKEN_ENV]
    except Exception:
        return None
    if type(url) is not str or type(token) is not str or not url or not token or url != url.strip() or token != token.strip() or not url.startswith("https://"):
        return None
    return url.rstrip("/"), token


def _runtime_transport(config: tuple[str, str]) -> CommandTransport:
    rest_url, rest_token = config
    def perform(command: list[object]) -> dict[str, object]:
        request = Request(rest_url, data=_canonical_json(command).encode("utf-8"), headers={"Authorization": f"Bearer {rest_token}", "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=_KV_TIMEOUT_SECONDS) as response:
                raw = response.read(_KV_MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError):
            raise TesterInviteAuthorityError() from None
        if len(raw) > _KV_MAX_RESPONSE_BYTES:
            raise TesterInviteAuthorityError()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            raise TesterInviteAuthorityError() from None
        if type(payload) is not dict:
            raise TesterInviteAuthorityError()
        return payload
    return perform


def _resolve_admin_ids(environment: Mapping[str, str]) -> frozenset[str]:
    raw = environment.get(_ADMIN_IDS_ENV)
    if type(raw) is not str or not raw or raw != raw.strip():
        return frozenset()
    values = tuple(part.strip() for part in raw.split(","))
    if any(not _valid_user_id(value) for value in values) or len(values) != len(set(values)):
        return frozenset()
    return frozenset(values)


class RuntimeTesterInviteAuthority:
    def __init__(self, command_transport: CommandTransport | None, *, environment: Mapping[str, str] | None = None, now_ms: Clock | None = None, random_bytes: RandomBytes = secrets.token_bytes) -> None:
        self._transport = command_transport
        self._environment = dict(os.environ if environment is None else environment)
        self._namespace = _resolve_namespace(self._environment)
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._random_bytes = random_bytes

    def _require_namespace(self) -> str:
        if self._namespace is None:
            raise TesterInviteAuthorityError()
        return self._namespace

    def _command(self, command: list[object]) -> object:
        if self._transport is None:
            raise TesterInviteAuthorityError()
        try:
            payload = self._transport(command)
        except TesterInviteAuthorityError:
            raise
        except Exception:
            raise TesterInviteAuthorityError() from None
        if type(payload) is not dict or set(payload) != {"result"}:
            raise TesterInviteAuthorityError()
        return payload["result"]

    def _snapshot(self, keys: list[str]) -> list[str]:
        result = self._command(["EVAL", _PRIMARY_SNAPSHOT_LUA, len(keys), *keys])
        if type(result) is not list or len(result) != len(keys) or any(type(item) is not str for item in result):
            raise TesterInviteAuthorityError()
        return result

    def _atomic(self, script: str, keys: list[str], arguments: list[object]) -> tuple[str | None, TesterInviteAuthorityError | None]:
        try:
            result = self._command(["EVAL", script, len(keys), *keys, *arguments])
        except TesterInviteAuthorityError as error:
            return None, error
        return (result, None) if type(result) is str else (None, TesterInviteAuthorityError())

    def has_admin_authority(self, actor_user_id: object) -> bool:
        return (
            self._namespace is not None
            and _valid_user_id(actor_user_id)
            and actor_user_id in _resolve_admin_ids(self._environment)
        )

    def _require_admin(self, actor_user_id: object) -> str:
        if not _valid_user_id(actor_user_id) or actor_user_id not in _resolve_admin_ids(self._environment):
            raise TesterInviteAuthorityError("forbidden")
        return str(actor_user_id)

    def issue_invitation(self, *, actor_user_id: str, invitee_email: str, invitee_name: str, source_request_id: str | None = None) -> dict[str, object]:
        actor = self._require_admin(actor_user_id)
        try:
            email = normalize_auth_email(invitee_email)
        except Exception:
            raise TesterInviteAuthorityError("invalid_request") from None
        name = _normalize_display_name(invitee_name)
        source = _normalize_source_request_id(source_request_id)
        if not is_valid_auth_email(email) or not name or (source_request_id is not None and source is None):
            raise TesterInviteAuthorityError("invalid_request")
        now_ms = self._now_ms()
        invitation_id = generate_invitation_id(random_bytes=self._random_bytes)
        raw_token, digest = generate_invitation_token(invitation_id, random_bytes=self._random_bytes)
        record = _build_record(invitation_id=invitation_id, email=email, display_name=name, token_digest=digest, created_by_user_id=actor, now_ms=now_ms, source_request_id=source)
        wire = _canonical_json(record)
        namespace = self._require_namespace()
        keys = [
            _token_key(namespace, invitation_id, digest),
            _invitation_key(namespace, invitation_id),
            _recipient_key(namespace, email),
        ]
        result, command_error = self._atomic(_ISSUE_INVITATION_LUA, keys, [wire, now_ms])
        if result == "invite_live":
            raise TesterInviteAuthorityError("live_invitation_exists")
        if result == "already_provisioned":
            raise TesterInviteAuthorityError("account_already_provisioned")
        if result not in {"applied", None}:
            raise TesterInviteAuthorityError()
        try:
            readback = self._snapshot(keys)
        except TesterInviteAuthorityError:
            raise command_error or TesterInviteAuthorityError() from None
        if any(item != wire for item in readback):
            raise command_error or TesterInviteAuthorityError()
        public = project_public_invitation(record)
        if public is None:
            raise TesterInviteAuthorityError()
        return {"invite": {"invitationId": invitation_id, "inviteeEmail": email, **public, **({"sourceRequestId": source} if source else {})}, "rawToken": raw_token}

    def read_provisioning_invitation(self, raw_token: str, *, allow_provisioned: bool = False) -> ProvisioningTesterInvitation:
        parsed = _parse_token(raw_token)
        if parsed is None:
            raise TesterInviteAuthorityError("invalid_invite")
        invitation_id, _ = parsed
        digest = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
        namespace = self._require_namespace()
        token_key = _token_key(namespace, invitation_id, digest)
        result = self._command(["EVAL", _PRIMARY_SNAPSHOT_LUA, 1, token_key])
        if type(result) is not list or len(result) != 1 or type(result[0]) is not str:
            raise TesterInviteAuthorityError("invalid_invite")
        raw = result[0]
        try:
            record = _normalize_record(json.loads(raw))
        except Exception:
            record = None
        if record is None or raw != _canonical_json(record) or record["id"] != invitation_id or record["tokenDigest"] != digest or not verify_invitation_token(raw_token, digest):
            raise TesterInviteAuthorityError("invalid_invite")
        keys = [
            token_key,
            _invitation_key(namespace, invitation_id),
            _recipient_key(namespace, str(record["inviteeEmail"])),
        ]
        if any(item != raw for item in self._snapshot(keys)):
            raise TesterInviteAuthorityError("conflict")
        now_ms = self._now_ms()
        status = str(record["status"])
        if status == "cancelled":
            raise TesterInviteAuthorityError("cancelled_invite")
        if status == "provisioned" and not allow_provisioned:
            raise TesterInviteAuthorityError("used_invite")
        if status == "invited" and int(record["expiresAt"]) <= now_ms:
            raise TesterInviteAuthorityError("expired_invite")
        return ProvisioningTesterInvitation(
            invitation_id, str(record["inviteeEmail"]), str(record["inviteeName"]), digest, status, int(record["expiresAt"]),
            str(record["sourceRequestId"]) if "sourceRequestId" in record else None,
            str(record["provisionedUserId"]) if status == "provisioned" else None,
            str(record["provisionedWorkspaceId"]) if status == "provisioned" else None,
        )

    def cancel_invitation(
        self,
        *,
        actor_user_id: str,
        invitation_id: str,
    ) -> dict[str, object]:
        self._require_admin(actor_user_id)
        if not _valid_invitation_id(invitation_id):
            raise TesterInviteAuthorityError("invalid_request")
        result = self._command(
            [
                "EVAL",
                _PRIMARY_SNAPSHOT_LUA,
                1,
                _invitation_key(self._require_namespace(), invitation_id),
            ]
        )
        if type(result) is not list or len(result) != 1 or type(result[0]) is not str:
            raise TesterInviteAuthorityError("invalid_invite")
        raw = result[0]
        try:
            record = _normalize_record(json.loads(raw))
        except Exception:
            record = None
        if record is None or raw != _canonical_json(record):
            raise TesterInviteAuthorityError()
        if record["status"] == "provisioned":
            raise TesterInviteAuthorityError("used_invite")
        if record["status"] == "cancelled":
            public = project_public_invitation(record)
            if public is None:
                raise TesterInviteAuthorityError()
            return {"invitationId": invitation_id, **public}

        now_ms = self._now_ms()
        if (
            type(now_ms) is not int
            or now_ms < int(record["createdAt"])
            or int(record["expiresAt"]) <= now_ms
        ):
            raise TesterInviteAuthorityError("expired_invite")
        candidate = {
            **record,
            "status": "cancelled",
            "updatedAt": now_ms,
            "cancelledAt": now_ms,
        }
        next_wire = _canonical_json(candidate)
        keys = [
            _token_key(
                self._require_namespace(),
                invitation_id,
                str(record["tokenDigest"]),
            ),
            _invitation_key(self._require_namespace(), invitation_id),
            _recipient_key(
                self._require_namespace(),
                str(record["inviteeEmail"]),
            ),
        ]
        result, command_error = self._atomic(
            _TRANSITION_INVITATION_LUA,
            keys,
            [raw, next_wire],
        )
        if result not in {"applied", None}:
            raise TesterInviteAuthorityError("conflict")
        try:
            readback = self._snapshot(keys)
        except TesterInviteAuthorityError:
            raise command_error or TesterInviteAuthorityError() from None
        if any(item != next_wire for item in readback):
            raise command_error or TesterInviteAuthorityError("conflict")
        public = project_public_invitation(candidate)
        if public is None:
            raise TesterInviteAuthorityError()
        return {"invitationId": invitation_id, **public}

    def mark_provisioned(self, *, raw_token: str, user_id: str, workspace_id: str) -> ProvisioningTesterInvitation:
        current = self.read_provisioning_invitation(raw_token, allow_provisioned=True)
        if current.status == "provisioned":
            if current.provisioned_user_id != user_id or current.provisioned_workspace_id != workspace_id:
                raise TesterInviteAuthorityError("conflict")
            return current
        if not _valid_user_id(user_id) or not _valid_workspace_id(workspace_id):
            raise TesterInviteAuthorityError("invalid_request")
        invitation_id, digest, now_ms = current.invitation_id, current.token_digest, self._now_ms()
        namespace = self._require_namespace()
        result = self._command(
            [
                "EVAL",
                _PRIMARY_SNAPSHOT_LUA,
                1,
                _invitation_key(namespace, invitation_id),
            ]
        )
        if type(result) is not list or len(result) != 1 or type(result[0]) is not str:
            raise TesterInviteAuthorityError()
        raw = result[0]
        record = _normalize_record(json.loads(raw))
        if record is None or record["status"] != "invited" or record["tokenDigest"] != digest or int(record["expiresAt"]) <= now_ms:
            raise TesterInviteAuthorityError("conflict")
        candidate = {**record, "status": "provisioned", "updatedAt": now_ms, "provisionedAt": now_ms, "provisionedUserId": user_id, "provisionedWorkspaceId": workspace_id}
        next_wire = _canonical_json(candidate)
        keys = [
            _token_key(namespace, invitation_id, digest),
            _invitation_key(namespace, invitation_id),
            _recipient_key(namespace, current.email),
        ]
        result, command_error = self._atomic(_TRANSITION_INVITATION_LUA, keys, [raw, next_wire])
        if result not in {"applied", None}:
            raise TesterInviteAuthorityError("conflict")
        try:
            readback = self._snapshot(keys)
        except TesterInviteAuthorityError:
            raise command_error or TesterInviteAuthorityError() from None
        if any(item != next_wire for item in readback):
            raise command_error or TesterInviteAuthorityError("conflict")
        return self.read_provisioning_invitation(raw_token, allow_provisioned=True)


def build_runtime_tester_invite_authority(environment: Mapping[str, str] | None = None, *, command_transport: CommandTransport | None = None, now_ms: Clock | None = None, random_bytes: RandomBytes = secrets.token_bytes) -> RuntimeTesterInviteAuthority:
    source = os.environ if environment is None else environment
    transport = command_transport
    if transport is None:
        config = _resolve_store_config(source)
        transport = _runtime_transport(config) if config else None
    return RuntimeTesterInviteAuthority(transport, environment=source, now_ms=now_ms, random_bytes=random_bytes)
