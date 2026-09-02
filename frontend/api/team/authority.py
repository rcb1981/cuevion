"""Production Team invitation and collaboration-membership authority.

This module is deliberately independent from Collaboration routing.  It uses the
existing Vercel/Upstash Redis service as a narrow Team store, derives management
authority from an ``AuthenticatedMemberContext``, and never creates an account
workspace membership for an invited Team member.
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
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from api.auth import account_authority, models
from api.auth.email_address import is_valid_auth_email, normalize_auth_email
from api.auth.runtime import AuthenticatedMemberContext
from cuevion_auth.current_account_repository_contract import (
    CurrentAccountByUserAuthority,
    CurrentAccountByUserAuthorityResult,
    CurrentAccountReadOutcome,
)


TEAM_AUTHORITY_SCHEMA_VERSION = 2
TEAM_INVITE_TOKEN_BYTES = 32
TEAM_INVITE_ID_BYTES = 16
TEAM_INVITE_TTL_MS = 7 * 24 * 60 * 60 * 1000
TEAM_ACCESS_LEVELS = frozenset({"Limited", "Shared"})
TEAM_INVITE_STATUSES = frozenset({"invited", "accepted", "declined", "cancelled"})

_LEGACY_TEAM_ACCESS_LEVELS = {
    "review": "Shared",
    "admin": "Shared",
    "editor": "Shared",
    "shared": "Shared",
    "limited": "Limited",
}

_KV_URL_ENV = "KV_REST_API_URL"
_KV_TOKEN_ENV = "KV_REST_API_TOKEN"
_KV_TIMEOUT_SECONDS = 10
_KV_MAX_RESPONSE_BYTES = 256 * 1024
_TEAM_V2_PREFIX = "cuevion:team:v2"
_TEAM_V1_PREFIX = "cuevion:team:v1"
_INVITATION_ID_RE = re.compile(r"tinv_[A-Za-z0-9_-]{1,64}")
_TOKEN_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_TOKEN_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_MEMBER_USER_ID_RE = re.compile(r"usr_[A-Za-z0-9_-]{21}[AQgw]")

TeamError = dict[str, str]
CommandTransport = Callable[[list[object]], dict[str, object]]
Clock = Callable[[], int]
RandomBytes = Callable[[int], bytes]
InviterOwnerValidator = Callable[[str, str], object]


def _error(code: str, message: str) -> TeamError:
    return {"code": code, "message": message}


def _unavailable_error() -> TeamError:
    return _error(
        "team_authority_unavailable",
        "Team membership is temporarily unavailable.",
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


def _valid_member_user_id(value: object) -> bool:
    return (
        type(value) is str
        and _MEMBER_USER_ID_RE.fullmatch(value) is not None
    )


def _parse_invitation_token(token: object) -> tuple[str, str] | None:
    if type(token) is not str or token != token.strip() or token.count(".") != 1:
        return None
    invitation_id, secret = token.split(".", 1)
    if not _valid_invitation_id(invitation_id) or _TOKEN_SECRET_RE.fullmatch(secret) is None:
        return None
    return invitation_id, secret


def generate_invitation_id(*, random_bytes: RandomBytes = secrets.token_bytes) -> str:
    value = random_bytes(TEAM_INVITE_ID_BYTES)
    if type(value) is not bytes or len(value) != TEAM_INVITE_ID_BYTES:
        raise ValueError("invalid invitation randomness")
    return "tinv_" + _base64url(value)


def generate_invitation_token(
    invitation_id: str,
    *,
    random_bytes: RandomBytes = secrets.token_bytes,
) -> tuple[str, str]:
    if not _valid_invitation_id(invitation_id):
        raise ValueError("invalid invitation id")
    value = random_bytes(TEAM_INVITE_TOKEN_BYTES)
    if type(value) is not bytes or len(value) != TEAM_INVITE_TOKEN_BYTES:
        raise ValueError("invalid invitation randomness")
    raw_token = f"{invitation_id}.{_base64url(value)}"
    return raw_token, hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def verify_invitation_token(raw_token: object, token_digest: object) -> bool:
    parsed = _parse_invitation_token(raw_token)
    if parsed is None or not _valid_token_digest(token_digest):
        return False
    candidate = hashlib.sha256(str(raw_token).encode("ascii")).hexdigest()
    return hmac.compare_digest(candidate, str(token_digest))


def _require_actor(actor: object) -> AuthenticatedMemberContext:
    if type(actor) is not AuthenticatedMemberContext:
        raise ValueError("invalid Team actor")
    return actor


def _require_owner(actor: object) -> AuthenticatedMemberContext:
    member = _require_actor(actor)
    if member.membership_role != "owner":
        raise PermissionError("Team owner authority is required")
    return member


def _normalize_email(value: object) -> str:
    if type(value) is not str:
        return ""
    normalized = normalize_auth_email(value)
    return normalized if is_valid_auth_email(normalized) else ""


def _normalize_legacy_member_identifier(value: object) -> str:
    """Match the historical v1 roster key's string/strip/lower contract."""

    return value.strip().lower() if type(value) is str and value.strip() else ""


def _normalize_display_name(value: object) -> str:
    if type(value) is not str:
        return ""
    normalized = value.strip()
    try:
        encoded_length = len(normalized.encode("utf-8"))
    except UnicodeEncodeError:
        return ""
    if not normalized or encoded_length > 256:
        return ""
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return ""
    return normalized


def _normalize_legacy_display_name(value: object) -> str:
    """Match the historical v1 roster's string-and-strip name contract."""

    return value.strip() if type(value) is str and value.strip() else ""


def build_invitation_record(
    *,
    invitation_id: str,
    actor: AuthenticatedMemberContext,
    invitee_email: str,
    invitee_name: str,
    access_level: str,
    token_digest: str,
    now_ms: int,
) -> dict[str, object]:
    canonical_actor = _require_owner(actor)
    canonical_email = _normalize_email(invitee_email)
    canonical_name = _normalize_display_name(invitee_name)
    if (
        not _valid_invitation_id(invitation_id)
        or not canonical_email
        or not canonical_name
        or type(access_level) is not str
        or access_level not in TEAM_ACCESS_LEVELS
        or not _valid_token_digest(token_digest)
        or type(now_ms) is not int
        or now_ms < 0
    ):
        raise ValueError("invalid Team invitation record")
    return {
        "v": TEAM_AUTHORITY_SCHEMA_VERSION,
        "id": invitation_id,
        "workspaceId": canonical_actor.workspace_id,
        "inviteeEmail": canonical_email,
        "inviteeName": canonical_name,
        "displayName": canonical_name,
        "accessLevel": access_level,
        "status": "invited",
        "createdAt": now_ms,
        "updatedAt": now_ms,
        "expiresAt": now_ms + TEAM_INVITE_TTL_MS,
        "createdByUserId": canonical_actor.user_id,
        "createdByUserName": canonical_actor.name,
        "tokenDigest": token_digest,
    }


def _normalize_invitation_record(value: object) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    invitation_id = value.get("id")
    workspace_id = value.get("workspaceId")
    invitee_email = _normalize_email(value.get("inviteeEmail"))
    invitee_name = _normalize_display_name(
        value.get("inviteeName") or value.get("displayName")
    )
    access_level = value.get("accessLevel")
    status = value.get("status")
    created_at = value.get("createdAt")
    updated_at = value.get("updatedAt")
    expires_at = value.get("expiresAt")
    created_by_user_id = value.get("createdByUserId")
    created_by_user_name = _normalize_display_name(value.get("createdByUserName"))
    token_digest = value.get("tokenDigest")
    if (
        type(value.get("v")) is not int
        or value.get("v") != TEAM_AUTHORITY_SCHEMA_VERSION
        or not _valid_invitation_id(invitation_id)
        or type(workspace_id) is not str
        or not workspace_id
        or workspace_id != workspace_id.strip()
        or not invitee_email
        or not invitee_name
        or type(access_level) is not str
        or access_level not in TEAM_ACCESS_LEVELS
        or type(status) is not str
        or status not in TEAM_INVITE_STATUSES
        or type(created_at) is not int
        or type(updated_at) is not int
        or type(expires_at) is not int
        or created_at < 0
        or not created_at <= updated_at
        or expires_at != created_at + TEAM_INVITE_TTL_MS
        or type(created_by_user_id) is not str
        or not created_by_user_id
        or not created_by_user_name
        or not _valid_token_digest(token_digest)
    ):
        return None
    normalized: dict[str, object] = {
        "v": TEAM_AUTHORITY_SCHEMA_VERSION,
        "id": invitation_id,
        "workspaceId": workspace_id,
        "inviteeEmail": invitee_email,
        "inviteeName": invitee_name,
        "displayName": invitee_name,
        "accessLevel": access_level,
        "status": status,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "expiresAt": expires_at,
        "createdByUserId": created_by_user_id,
        "createdByUserName": created_by_user_name,
        "tokenDigest": token_digest,
    }
    optional_int_fields = ("acceptedAt", "declinedAt", "cancelledAt")
    for field in optional_int_fields:
        field_value = value.get(field)
        if field_value is not None:
            if type(field_value) is not int or not created_at <= field_value:
                return None
            normalized[field] = field_value
    for field in ("acceptedByUserId", "acceptedByEmail"):
        field_value = value.get(field)
        if field_value is not None:
            if type(field_value) is not str or not field_value:
                return None
            normalized[field] = (
                _normalize_email(field_value)
                if field == "acceptedByEmail"
                else field_value
            )
            if not normalized[field]:
                return None
    if status == "accepted" and any(
        field not in normalized
        for field in ("acceptedAt", "acceptedByUserId", "acceptedByEmail")
    ):
        return None
    if status == "declined" and "declinedAt" not in normalized:
        return None
    if status == "cancelled" and "cancelledAt" not in normalized:
        return None
    return normalized


def project_public_invitation(value: object) -> dict[str, object] | None:
    record = _normalize_invitation_record(value)
    if record is None:
        return None
    return {
        "displayName": record["displayName"],
        "accessLevel": record["accessLevel"],
        "status": record["status"],
        "expiresAt": record["expiresAt"],
    }


def project_pending_invitation(value: object) -> dict[str, object] | None:
    record = _normalize_invitation_record(value)
    if record is None:
        return None
    return {
        "id": record["id"],
        "inviteeEmail": record["inviteeEmail"],
        "displayName": record["displayName"],
        "accessLevel": record["accessLevel"],
        "status": record["status"],
        "expiresAt": record["expiresAt"],
    }


def build_membership_record(
    *,
    invitation_record: dict[str, object],
    recipient: AuthenticatedMemberContext,
    accepted_at: int,
) -> dict[str, object]:
    invitation = _normalize_invitation_record(invitation_record)
    canonical_recipient = _require_actor(recipient)
    if (
        invitation is None
        or type(accepted_at) is not int
        or accepted_at < int(invitation["createdAt"])
        or _normalize_email(canonical_recipient.email) != invitation["inviteeEmail"]
    ):
        raise ValueError("invalid Team membership record")
    return {
        "v": TEAM_AUTHORITY_SCHEMA_VERSION,
        "workspaceId": invitation["workspaceId"],
        "email": invitation["inviteeEmail"],
        "verifiedRecipientEmail": invitation["inviteeEmail"],
        "memberUserId": canonical_recipient.user_id,
        "displayName": invitation["displayName"],
        "accessLevel": invitation["accessLevel"],
        "status": "active",
        "sourceInvitationId": invitation["id"],
        "createdAt": invitation["createdAt"],
        "updatedAt": accepted_at,
        "acceptedAt": accepted_at,
    }


def _normalize_membership_record(value: object) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    workspace_id = value.get("workspaceId")
    email = _normalize_email(value.get("email"))
    verified_email = _normalize_email(value.get("verifiedRecipientEmail"))
    member_user_id = value.get("memberUserId")
    source_invitation_id = value.get("sourceInvitationId")
    display_name = _normalize_display_name(value.get("displayName") or value.get("name"))
    access_level = value.get("accessLevel")
    status = value.get("status")
    created_at = value.get("createdAt")
    updated_at = value.get("updatedAt")
    accepted_at = value.get("acceptedAt")
    if (
        type(value.get("v")) is not int
        or value.get("v") != TEAM_AUTHORITY_SCHEMA_VERSION
        or type(workspace_id) is not str
        or not workspace_id
        or workspace_id != workspace_id.strip()
        or not email
        or verified_email != email
        or type(member_user_id) is not str
        or not member_user_id
        or not _valid_invitation_id(source_invitation_id)
        or not display_name
        or type(access_level) is not str
        or access_level not in TEAM_ACCESS_LEVELS
        or type(status) is not str
        or status not in {"active", "removed"}
        or type(created_at) is not int
        or type(updated_at) is not int
        or type(accepted_at) is not int
        or not created_at <= accepted_at <= updated_at
    ):
        return None
    normalized: dict[str, object] = {
        "v": TEAM_AUTHORITY_SCHEMA_VERSION,
        "workspaceId": workspace_id,
        "email": email,
        "verifiedRecipientEmail": verified_email,
        "memberUserId": member_user_id,
        "displayName": display_name,
        "accessLevel": access_level,
        "status": status,
        "sourceInvitationId": source_invitation_id,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "acceptedAt": accepted_at,
    }
    for field in ("removedAt", "revokedAt"):
        field_value = value.get(field)
        if field_value is not None:
            if type(field_value) is not int or field_value < accepted_at:
                return None
            normalized[field] = field_value
    return normalized


def _normalize_legacy_membership_record(value: object) -> dict[str, object] | None:
    """Validate the v1 records still projected by the safe Team roster.

    The legacy bearer is retained in the normalized form only for an active
    record, where the existing roster reader still requires it. Callers must
    never project this internal value to a response.
    """

    if (
        type(value) is not dict
        or type(value.get("v")) is not int
        or value.get("v") != 1
    ):
        return None
    workspace_id = value.get("workspaceId")
    email = _normalize_legacy_member_identifier(value.get("email"))
    display_name = _normalize_legacy_display_name(
        value.get("displayName") or value.get("name")
    )
    raw_access_level = value.get("accessLevel")
    access_level = (
        _LEGACY_TEAM_ACCESS_LEVELS.get(raw_access_level.strip().lower())
        if type(raw_access_level) is str
        else None
    )
    raw_status = value.get("status")
    status = raw_status.strip().lower() if type(raw_status) is str else ""
    created_at = value.get("createdAt")
    updated_at = value.get("updatedAt")
    accepted_at = value.get("acceptedAt")
    if (
        type(workspace_id) is not str
        or not workspace_id
        or workspace_id != workspace_id.strip()
        or not email
        or not display_name
        or type(access_level) is not str
        or access_level not in TEAM_ACCESS_LEVELS
        or type(status) is not str
        or status not in {"active", "removed"}
        or type(created_at) is not int
        or type(updated_at) is not int
        or type(accepted_at) is not int
        or not created_at <= accepted_at <= updated_at
    ):
        return None
    normalized: dict[str, object] = {
        "v": 1,
        "workspaceId": workspace_id,
        "email": email,
        "displayName": display_name,
        "accessLevel": access_level,
        "status": status,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "acceptedAt": accepted_at,
    }
    if status == "active":
        invite_token = value.get("inviteToken")
        if type(invite_token) is not str or not invite_token.strip():
            return None
        normalized["inviteToken"] = invite_token.strip()
    for field in ("removedAt", "revokedAt"):
        field_value = value.get(field)
        if field_value is not None:
            if type(field_value) is not int or not accepted_at <= field_value <= updated_at:
                return None
            normalized[field] = field_value
    return normalized


def _normalize_mutable_membership_record(value: object) -> dict[str, object] | None:
    return _normalize_membership_record(value) or _normalize_legacy_membership_record(value)


def _project_mutable_team_member(value: object) -> dict[str, object] | None:
    record = _normalize_mutable_membership_record(value)
    if record is None:
        return None
    return {
        "email": record["email"],
        "displayName": record["displayName"],
        "accessLevel": record["accessLevel"],
        "status": record["status"],
    }


def _build_member_user_pointer(value: object) -> dict[str, object] | None:
    record = _normalize_membership_record(value)
    if record is None or record["status"] != "active":
        return None
    return {
        "v": TEAM_AUTHORITY_SCHEMA_VERSION,
        "workspaceId": record["workspaceId"],
        "memberUserId": record["memberUserId"],
        "email": record["email"],
        "sourceInvitationId": record["sourceInvitationId"],
        "status": "active",
    }


def _normalize_member_user_pointer(value: object) -> dict[str, object] | None:
    required = {
        "v",
        "workspaceId",
        "memberUserId",
        "email",
        "sourceInvitationId",
        "status",
    }
    if type(value) is not dict or set(value) != required:
        return None
    workspace_id = value.get("workspaceId")
    member_user_id = value.get("memberUserId")
    email = _normalize_email(value.get("email"))
    source_invitation_id = value.get("sourceInvitationId")
    if (
        type(value.get("v")) is not int
        or value.get("v") != TEAM_AUTHORITY_SCHEMA_VERSION
        or type(workspace_id) is not str
        or not workspace_id
        or workspace_id != workspace_id.strip()
        or not _valid_member_user_id(member_user_id)
        or not email
        or value.get("email") != email
        or not _valid_invitation_id(source_invitation_id)
        or value.get("status") != "active"
    ):
        return None
    return {
        "v": TEAM_AUTHORITY_SCHEMA_VERSION,
        "workspaceId": workspace_id,
        "memberUserId": member_user_id,
        "email": email,
        "sourceInvitationId": source_invitation_id,
        "status": "active",
    }


def project_team_member(value: object) -> dict[str, object] | None:
    record = _normalize_mutable_membership_record(value)
    if record is None or record["status"] != "active":
        return None
    projected = {
        "email": record["email"],
        "displayName": record["displayName"],
        "accessLevel": record["accessLevel"],
        "status": record["status"],
    }
    if record["v"] == TEAM_AUTHORITY_SCHEMA_VERSION:
        projected["memberUserId"] = record["memberUserId"]
    return projected


def _invitation_token_key(invitation_id: str, token_digest: str) -> str:
    return f"{_TEAM_V2_PREFIX}:invite-token:{invitation_id}:{token_digest}"


def _workspace_invitation_key(workspace_id: str, invitation_id: str) -> str:
    return f"{_TEAM_V2_PREFIX}:workspace-invite:{workspace_id}:{invitation_id}"


def _workspace_recipient_invitation_key(workspace_id: str, email: str) -> str:
    return f"{_TEAM_V2_PREFIX}:recipient-invite:{workspace_id}:{email}"


def _pending_index_key(workspace_id: str) -> str:
    return f"{_TEAM_V2_PREFIX}:pending-index:{workspace_id}"


def _member_key(workspace_id: str, email: str) -> str:
    # Membership stays on the existing roster namespace so the already-safe
    # roster read observes the secure v2, token-free record immediately.
    return f"{_TEAM_V1_PREFIX}:member:{workspace_id}:{email}"


def _members_index_key(workspace_id: str) -> str:
    return f"{_TEAM_V1_PREFIX}:members-index:{workspace_id}"


def _member_user_pointer_key(workspace_id: str, member_user_id: str) -> str:
    return f"{_TEAM_V2_PREFIX}:member-user:{workspace_id}:{member_user_id}"


_INDEX_HELPERS_LUA = r"""
local function decode_object(raw)
  if not raw then return nil, true end
  local ok, value = pcall(cjson.decode, raw)
  if not ok or type(value) ~= 'table' then return nil, false end
  return value, true
end
local function decode_index(raw)
  if not raw then return {}, true end
  if string.sub(raw, 1, 1) ~= '[' or string.sub(raw, -1) ~= ']' then return nil, false end
  local ok, value = pcall(cjson.decode, raw)
  if not ok or type(value) ~= 'table' then return nil, false end
  local result = {}
  for index = 1, #value do
    if type(value[index]) ~= 'string' then return nil, false end
    result[#result + 1] = value[index]
  end
  return result, true
end
local function encode_index(values)
  if #values == 0 then return '[]' end
  return cjson.encode(values)
end
local function add_unique(values, wanted)
  for index = 1, #values do
    if values[index] == wanted then return values end
  end
  values[#values + 1] = wanted
  return values
end
local function remove_value(values, unwanted)
  local result = {}
  for index = 1, #values do
    if values[index] ~= unwanted then result[#result + 1] = values[index] end
  end
  return result
end
""".strip()


_ISSUE_INVITATION_LUA = (_INDEX_HELPERS_LUA + r"""
-- Atomically reserve one workspace recipient while preventing an active member
-- or another live invited record from winning the same workspace slot.
local member_raw = redis.call('GET', KEYS[4])
if member_raw then
  local member, valid_member = decode_object(member_raw)
  if not valid_member or type(member.status) ~= 'string' then return 'malformed' end
  if member.status == 'active' then return 'member_active' end
end
local current_raw = redis.call('GET', KEYS[3])
local superseded_invitation_id = nil
if current_raw then
  local current, valid_current = decode_object(current_raw)
  if not valid_current or type(current.status) ~= 'string' or type(current.expiresAt) ~= 'number' then
    return 'malformed'
  end
  if current.status == 'invited' and current.expiresAt > tonumber(ARGV[2]) then
    return 'invite_live'
  end
  if current.status == 'invited' and current.expiresAt <= tonumber(ARGV[2]) then
    if type(current.id) ~= 'string' or current.workspaceId ~= ARGV[4] or current.inviteeEmail ~= ARGV[5] then
      return 'malformed'
    end
    superseded_invitation_id = current.id
  end
end
if redis.call('GET', KEYS[1]) or redis.call('GET', KEYS[2]) then return 'collision' end
local pending, valid_pending = decode_index(redis.call('GET', KEYS[5]))
if not valid_pending then return 'malformed' end
if superseded_invitation_id then pending = remove_value(pending, superseded_invitation_id) end
pending = add_unique(pending, ARGV[3])
redis.call('SET', KEYS[1], ARGV[1])
redis.call('SET', KEYS[2], ARGV[1])
redis.call('SET', KEYS[3], ARGV[1])
redis.call('SET', KEYS[5], encode_index(pending))
return 'applied'
""").strip()


_ACCEPT_INVITATION_LUA = (_INDEX_HELPERS_LUA + r"""
-- Only invited, unexpired state may become accepted.  The other terminal
-- statuses (declined and cancelled) are intentionally named and rejected.
local current_raw = redis.call('GET', KEYS[1])
if not current_raw or current_raw ~= ARGV[1] then return 'stale' end
local current, valid_current = decode_object(current_raw)
if not valid_current or type(current.status) ~= 'string' or type(current.expiresAt) ~= 'number' then
  return 'malformed'
end
if current.status ~= 'invited' then return current.status end
if current.expiresAt <= tonumber(ARGV[4]) then return 'expired' end
if redis.call('GET', KEYS[2]) ~= ARGV[1] or redis.call('GET', KEYS[3]) ~= ARGV[1] then
  return 'stale'
end
local existing_member_raw = redis.call('GET', KEYS[4])
if existing_member_raw then
  local existing_member, valid_member = decode_object(existing_member_raw)
  if not valid_member or type(existing_member.status) ~= 'string' then return 'malformed' end
  if existing_member.status == 'active' then return 'member_active' end
end
local pointer, valid_pointer = decode_object(ARGV[9])
if not valid_pointer
  or pointer.v ~= 2
  or pointer.workspaceId ~= ARGV[7]
  or pointer.memberUserId ~= ARGV[8]
  or pointer.email ~= ARGV[5]
  or type(pointer.sourceInvitationId) ~= 'string'
  or pointer.sourceInvitationId ~= ARGV[6]
  or pointer.status ~= 'active' then
  return 'malformed'
end
local existing_user_pointer_raw = redis.call('GET', KEYS[7])
if existing_user_pointer_raw then
  local existing_pointer, valid_existing_pointer = decode_object(existing_user_pointer_raw)
  if not valid_existing_pointer
    or existing_pointer.v ~= 2
    or existing_pointer.workspaceId ~= ARGV[7]
    or existing_pointer.memberUserId ~= ARGV[8]
    or type(existing_pointer.email) ~= 'string'
    or type(existing_pointer.sourceInvitationId) ~= 'string'
    or existing_pointer.status ~= 'active' then
    return 'malformed'
  end
  return 'member_active'
end
local members, valid_members = decode_index(redis.call('GET', KEYS[5]))
local pending, valid_pending = decode_index(redis.call('GET', KEYS[6]))
if not valid_members or not valid_pending then return 'malformed' end
members = add_unique(members, ARGV[5])
pending = remove_value(pending, ARGV[6])
redis.call('SET', KEYS[1], ARGV[2])
redis.call('SET', KEYS[2], ARGV[2])
redis.call('SET', KEYS[3], ARGV[2])
redis.call('SET', KEYS[4], ARGV[3])
redis.call('SET', KEYS[5], encode_index(members))
redis.call('SET', KEYS[6], encode_index(pending))
redis.call('SET', KEYS[7], ARGV[9])
return 'applied'
-- accepted declined cancelled
""").strip()


_TERMINAL_INVITATION_LUA = (_INDEX_HELPERS_LUA + r"""
-- A terminal target is closed to accepted, declined, or cancelled only.
local terminal = {accepted = true, declined = true, cancelled = true}
if not terminal[ARGV[5]] then return 'malformed' end
local current_raw = redis.call('GET', KEYS[1])
if not current_raw or current_raw ~= ARGV[1] then return 'stale' end
local current, valid_current = decode_object(current_raw)
if not valid_current or type(current.status) ~= 'string' or type(current.expiresAt) ~= 'number' then
  return 'malformed'
end
if current.status ~= 'invited' then return current.status end
if current.expiresAt <= tonumber(ARGV[3]) then return 'expired' end
if redis.call('GET', KEYS[2]) ~= ARGV[1] or redis.call('GET', KEYS[3]) ~= ARGV[1] then
  return 'stale'
end
local pending, valid_pending = decode_index(redis.call('GET', KEYS[4]))
if not valid_pending then return 'malformed' end
pending = remove_value(pending, ARGV[4])
redis.call('SET', KEYS[1], ARGV[2])
redis.call('SET', KEYS[2], ARGV[2])
redis.call('SET', KEYS[3], ARGV[2])
redis.call('SET', KEYS[4], encode_index(pending))
return 'applied'
""").strip()


_REMOVE_MEMBER_LUA = (_INDEX_HELPERS_LUA + r"""
local current_raw = redis.call('GET', KEYS[1])
if not current_raw then return 'missing' end
if current_raw ~= ARGV[1] then return 'stale' end
local current, valid_current = decode_object(current_raw)
if not valid_current or type(current.status) ~= 'string' then return 'malformed' end
if current.status ~= 'active' then return 'not_active' end
local members, valid_members = decode_index(redis.call('GET', KEYS[2]))
if not valid_members then return 'malformed' end
if #KEYS == 3 then
  local pointer_raw = redis.call('GET', KEYS[3])
  if not pointer_raw or pointer_raw ~= ARGV[4] then return 'stale' end
elseif #KEYS ~= 2 then
  return 'malformed'
end
members = remove_value(members, ARGV[3])
redis.call('SET', KEYS[1], ARGV[2])
redis.call('SET', KEYS[2], encode_index(members))
if #KEYS == 3 then redis.call('DEL', KEYS[3]) end
return 'applied'
""").strip()


_UPDATE_MEMBER_ACCESS_LUA = r"""
local current_raw = redis.call('GET', KEYS[1])
if not current_raw then return 'missing' end
if current_raw ~= ARGV[1] then return 'stale' end
local ok, current = pcall(cjson.decode, current_raw)
if not ok or type(current) ~= 'table' or type(current.status) ~= 'string' then return 'malformed' end
if current.status ~= 'active' then return 'not_active' end
redis.call('SET', KEYS[1], ARGV[2])
return 'applied'
""".strip()


_PRUNE_PENDING_LUA = (_INDEX_HELPERS_LUA + r"""
local current_raw = redis.call('GET', KEYS[1])
if not current_raw then return 'missing' end
if current_raw ~= ARGV[1] then return 'stale' end
local current, valid_current = decode_index(current_raw)
local replacement, valid_replacement = decode_index(ARGV[2])
if not valid_current or not valid_replacement then return 'malformed' end
redis.call('SET', KEYS[1], encode_index(replacement))
return 'applied'
""").strip()


ATOMIC_MUTATION_SCRIPTS = {
    "issue": _ISSUE_INVITATION_LUA,
    "accept": _ACCEPT_INVITATION_LUA,
    "decline": _TERMINAL_INVITATION_LUA,
    "cancel": _TERMINAL_INVITATION_LUA,
    "remove": _REMOVE_MEMBER_LUA,
    "update_access": _UPDATE_MEMBER_ACCESS_LUA,
    "prune_pending": _PRUNE_PENDING_LUA,
}


def _normalize_index(raw: object) -> list[str] | None:
    if raw is None:
        return []
    try:
        value = json.loads(raw) if type(raw) is str else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        return None
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _decode_record(raw: object, normalizer: Callable[[object], Any]) -> object | None:
    if type(raw) is not str:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return normalizer(value)


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
            raise RuntimeError("Team store unavailable") from None
        if len(raw) > _KV_MAX_RESPONSE_BYTES:
            raise RuntimeError("Team store unavailable")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise RuntimeError("Team store unavailable") from None
        if type(payload) is not dict:
            raise RuntimeError("Team store unavailable")
        return payload

    return perform


class RuntimeTeamAuthority:
    """Narrow server-only Team authority and atomic store composition."""

    def __init__(
        self,
        command_transport: CommandTransport | None,
        *,
        environment: Mapping[str, str] | None = None,
        now_ms: Clock | None = None,
        random_bytes: RandomBytes = secrets.token_bytes,
        inviter_owner_validator: InviterOwnerValidator | None = None,
    ) -> None:
        self._transport = command_transport
        self._environment = dict(os.environ if environment is None else environment)
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._random_bytes = random_bytes
        self._inviter_owner_validator = inviter_owner_validator

    def _command(self, command: list[object]) -> tuple[object | None, TeamError | None]:
        if self._transport is None:
            return None, _unavailable_error()
        try:
            payload = self._transport(command)
        except Exception:
            return None, _unavailable_error()
        if type(payload) is not dict or set(payload) != {"result"}:
            return None, _unavailable_error()
        return payload["result"], None

    def _get_raw(self, key: str) -> tuple[str | None, TeamError | None]:
        result, error = self._command(["GET", key])
        if error:
            return None, error
        if result is None:
            return None, None
        if type(result) is not str:
            return None, _unavailable_error()
        return result, None

    def _atomic(self, operation: str, keys: list[str], arguments: list[object]) -> tuple[str | None, TeamError | None]:
        script = ATOMIC_MUTATION_SCRIPTS[operation]
        result, error = self._command(["EVAL", script, len(keys), *keys, *arguments])
        if error:
            return None, error
        if type(result) is not str:
            return None, _unavailable_error()
        return result, None

    def _read_invitation_by_token(self, token: object) -> tuple[dict[str, object] | None, str | None, TeamError | None]:
        parsed = _parse_invitation_token(token)
        if parsed is None:
            return None, None, _error("invalid_invite", "Team invitation is invalid.")
        invitation_id, _secret = parsed
        token_digest = hashlib.sha256(str(token).encode("ascii")).hexdigest()
        raw, read_error = self._get_raw(_invitation_token_key(invitation_id, token_digest))
        if read_error:
            return None, None, read_error
        record = _decode_record(raw, _normalize_invitation_record)
        if (
            record is None
            or record["id"] != invitation_id
            or record["tokenDigest"] != token_digest
            or not verify_invitation_token(token, token_digest)
        ):
            return None, None, _error("invalid_invite", "Team invitation is invalid.")
        return record, raw, None

    def _terminal_error(self, record: dict[str, object], now_ms: int) -> TeamError | None:
        status = record["status"]
        if status == "cancelled":
            return _error("cancelled_invite", "Team invitation has been cancelled.")
        if status == "declined":
            return _error("declined_invite", "Team invitation has been declined.")
        if status == "accepted":
            return _error("used_invite", "Team invitation has already been used.")
        if int(record["expiresAt"]) <= now_ms:
            return _error("expired_invite", "Team invitation has expired.")
        return None

    def _inviter_is_active_owner(self, invitation: dict[str, object]) -> tuple[bool, TeamError | None]:
        user_id = str(invitation["createdByUserId"])
        workspace_id = str(invitation["workspaceId"])
        if self._inviter_owner_validator is not None:
            try:
                outcome = self._inviter_owner_validator(user_id, workspace_id)
            except Exception:
                return False, _unavailable_error()
            if outcome is True or outcome == "authorized":
                return True, None
            if outcome is False or outcome == "not_authorized":
                return False, _error("cancelled_invite", "Invitation owner authority is no longer active.")
            return False, _unavailable_error()
        try:
            reader = account_authority.build_runtime_account_authority(self._environment)
            result = reader.read_current_account_by_user(user_id, workspace_id)
        except Exception:
            return False, _unavailable_error()
        if type(result) is not CurrentAccountByUserAuthorityResult:
            return False, _unavailable_error()
        if result.outcome in {CurrentAccountReadOutcome.UNAVAILABLE, CurrentAccountReadOutcome.INTERNAL_ERROR}:
            return False, _unavailable_error()
        if result.outcome is not CurrentAccountReadOutcome.FOUND:
            return False, _error("cancelled_invite", "Invitation owner authority is no longer active.")
        current = result.authority
        if type(current) is not CurrentAccountByUserAuthority:
            return False, _unavailable_error()
        valid = (
            current.user.user_id == user_id
            and current.user.status is models.UserStatus.ACTIVE
            and current.primary_verified_email.status is models.VerifiedEmailStatus.VERIFIED
            and current.workspace.workspace_id == workspace_id
            and current.workspace.status is models.WorkspaceStatus.ACTIVE
            and current.workspace_membership.user_id == user_id
            and current.workspace_membership.workspace_id == workspace_id
            and current.workspace_membership.status is models.WorkspaceMembershipStatus.ACTIVE
            and current.workspace_membership.role is models.WorkspaceRole.OWNER
        )
        if not valid:
            return False, _error("cancelled_invite", "Invitation owner authority is no longer active.")
        return True, None

    def _invitation_readback(self, record: dict[str, object], *, pending: bool) -> bool:
        expected = _canonical_json(record)
        workspace_id = str(record["workspaceId"])
        invitation_id = str(record["id"])
        email = str(record["inviteeEmail"])
        digest = str(record["tokenDigest"])
        for key in (
            _invitation_token_key(invitation_id, digest),
            _workspace_invitation_key(workspace_id, invitation_id),
            _workspace_recipient_invitation_key(workspace_id, email),
        ):
            raw, error = self._get_raw(key)
            if error or raw != expected:
                return False
        index_raw, index_error = self._get_raw(_pending_index_key(workspace_id))
        index = _normalize_index(index_raw)
        return index_error is None and index is not None and ((invitation_id in index) is pending)

    def _membership_readback(self, record: dict[str, object], *, indexed: bool) -> bool:
        expected = _canonical_json(record)
        workspace_id = str(record["workspaceId"])
        email = str(record["email"])
        raw, error = self._get_raw(_member_key(workspace_id, email))
        if error or raw != expected:
            return False
        index_raw, index_error = self._get_raw(_members_index_key(workspace_id))
        index = _normalize_index(index_raw)
        return index_error is None and index is not None and ((email in index) is indexed)

    def _member_user_pointer_readback(self, record: dict[str, object], *, present: bool) -> bool:
        pointer = _build_member_user_pointer(record)
        if pointer is None:
            return False
        raw, error = self._get_raw(
            _member_user_pointer_key(
                str(pointer["workspaceId"]),
                str(pointer["memberUserId"]),
            )
        )
        if error:
            return False
        return raw == _canonical_json(pointer) if present else raw is None

    def resolve_active_member_by_user_id(
        self,
        *,
        workspace_id: str,
        member_user_id: str,
    ) -> tuple[dict[str, object] | None, TeamError | None]:
        """Resolve one exact active v2 member through bounded pointer reads."""

        if (
            type(workspace_id) is not str
            or not workspace_id
            or workspace_id != workspace_id.strip()
            or not _valid_member_user_id(member_user_id)
        ):
            return None, _error("invalid_request", "Team member identity is invalid.")

        pointer_raw, pointer_error = self._get_raw(
            _member_user_pointer_key(workspace_id, member_user_id)
        )
        if pointer_error:
            return None, pointer_error
        if pointer_raw is None:
            return None, _error(
                "team_member_not_active",
                "Team member is not active.",
            )
        pointer = _decode_record(pointer_raw, _normalize_member_user_pointer)
        if (
            pointer is None
            or pointer["workspaceId"] != workspace_id
            or pointer["memberUserId"] != member_user_id
        ):
            return None, _unavailable_error()

        member_raw, member_error = self._get_raw(
            _member_key(workspace_id, str(pointer["email"]))
        )
        if member_error:
            return None, member_error
        membership = _decode_record(member_raw, _normalize_membership_record)
        if membership is None:
            return None, _unavailable_error()
        if membership["status"] != "active":
            return None, _error(
                "team_member_not_active",
                "Team member is not active.",
            )
        canonical_pointer = _build_member_user_pointer(membership)
        if (
            canonical_pointer is None
            or canonical_pointer != pointer
            or membership["workspaceId"] != workspace_id
            or membership["memberUserId"] != member_user_id
        ):
            return None, _unavailable_error()
        return {
            "memberUserId": membership["memberUserId"],
            "displayName": membership["displayName"],
            "accessLevel": membership["accessLevel"],
            "sourceInvitationId": membership["sourceInvitationId"],
        }, None

    def issue_invitation(self, *, actor: AuthenticatedMemberContext, invitee_email: str, invitee_name: str, access_level: str):
        try:
            canonical_actor = _require_owner(actor)
        except (TypeError, ValueError, PermissionError):
            return None, _error("forbidden", "Team owner authority is required.")
        email = _normalize_email(invitee_email)
        name = _normalize_display_name(invitee_name)
        if (
            not email
            or not name
            or type(access_level) is not str
            or access_level not in TEAM_ACCESS_LEVELS
        ):
            return None, _error("invalid_request", "Invitation fields are invalid.")
        if email == _normalize_email(canonical_actor.email):
            return None, _error("live_invitation_exists", "The workspace owner cannot invite themselves.")
        try:
            now_ms = self._now_ms()
            invitation_id = generate_invitation_id(random_bytes=self._random_bytes)
            raw_token, token_digest = generate_invitation_token(
                invitation_id,
                random_bytes=self._random_bytes,
            )
            record = build_invitation_record(
                invitation_id=invitation_id,
                actor=canonical_actor,
                invitee_email=email,
                invitee_name=name,
                access_level=access_level,
                token_digest=token_digest,
                now_ms=now_ms,
            )
        except (TypeError, ValueError, PermissionError):
            return None, _unavailable_error()
        record_wire = _canonical_json(record)
        result, command_error = self._atomic(
            "issue",
            [
                _invitation_token_key(invitation_id, token_digest),
                _workspace_invitation_key(canonical_actor.workspace_id, invitation_id),
                _workspace_recipient_invitation_key(canonical_actor.workspace_id, email),
                _member_key(canonical_actor.workspace_id, email),
                _pending_index_key(canonical_actor.workspace_id),
            ],
            [
                record_wire,
                str(now_ms),
                invitation_id,
                canonical_actor.workspace_id,
                email,
            ],
        )
        if result == "member_active":
            return None, _error("team_member_exists", "An active Team member already uses this email.")
        if result == "invite_live":
            return None, _error("live_invitation_exists", "A live Team invitation already exists.")
        if result not in {"applied", None}:
            return None, _unavailable_error()
        if not self._invitation_readback(record, pending=True):
            return None, command_error or _unavailable_error()
        return {"invite": project_pending_invitation(record), "rawToken": raw_token}, None

    def lookup_invitation(self, *, token: str):
        record, _raw, read_error = self._read_invitation_by_token(token)
        if read_error or record is None:
            return None, read_error
        if record["status"] == "invited" and int(record["expiresAt"]) <= self._now_ms():
            return None, _error("expired_invite", "Team invitation has expired.")
        projection = project_public_invitation(record)
        return (projection, None) if projection is not None else (None, _unavailable_error())

    def list_pending_invitations(self, *, actor: AuthenticatedMemberContext):
        try:
            canonical_actor = _require_owner(actor)
        except (TypeError, ValueError, PermissionError):
            return None, _error("forbidden", "Team owner authority is required.")
        now_ms = self._now_ms()
        pending_key = _pending_index_key(canonical_actor.workspace_id)
        for _attempt in range(3):
            index_raw, index_error = self._get_raw(pending_key)
            if index_error:
                return None, index_error
            invitation_ids = _normalize_index(index_raw)
            if invitation_ids is None:
                return None, _unavailable_error()
            projected: list[dict[str, object]] = []
            retained_ids: list[str] = []
            for invitation_id in invitation_ids:
                if not _valid_invitation_id(invitation_id):
                    return None, _unavailable_error()
                raw, error = self._get_raw(
                    _workspace_invitation_key(canonical_actor.workspace_id, invitation_id)
                )
                if error:
                    return None, error
                if raw is None:
                    continue
                record = _decode_record(raw, _normalize_invitation_record)
                if (
                    record is None
                    or record["id"] != invitation_id
                    or record["workspaceId"] != canonical_actor.workspace_id
                ):
                    return None, _unavailable_error()
                if record["status"] != "invited" or int(record["expiresAt"]) <= now_ms:
                    continue
                projection = project_pending_invitation(record)
                if projection is None:
                    return None, _unavailable_error()
                retained_ids.append(invitation_id)
                projected.append(projection)
            if retained_ids == invitation_ids:
                return projected, None
            if index_raw is None:
                return None, _unavailable_error()
            replacement_wire = _canonical_json(retained_ids)
            result, command_error = self._atomic(
                "prune_pending",
                [pending_key],
                [index_raw, replacement_wire],
            )
            if result in {"stale", "missing"}:
                continue
            if result not in {"applied", None}:
                return None, _unavailable_error()
            readback, readback_error = self._get_raw(pending_key)
            if readback_error:
                return None, readback_error
            if readback == replacement_wire:
                return projected, None
            return None, command_error or _unavailable_error()
        return None, _unavailable_error()

    def accept_invitation(self, *, actor: AuthenticatedMemberContext, token: str):
        try:
            recipient = _require_actor(actor)
        except (TypeError, ValueError):
            return None, _error("unauthorized", "Authentication is required.")
        invitation, current_wire, read_error = self._read_invitation_by_token(token)
        if read_error or invitation is None or current_wire is None:
            return None, read_error
        now_ms = self._now_ms()
        terminal_error = self._terminal_error(invitation, now_ms)
        if terminal_error:
            return None, terminal_error
        if recipient.user_id == invitation["createdByUserId"]:
            return None, _error(
                "wrong_recipient",
                "The workspace owner cannot accept their own Team invitation.",
            )
        if _normalize_email(recipient.email) != invitation["inviteeEmail"]:
            return None, _error("wrong_recipient", "This invitation belongs to another recipient.")
        owner_valid, owner_error = self._inviter_is_active_owner(invitation)
        if not owner_valid:
            return None, owner_error or _unavailable_error()
        accepted_invitation = {
            **invitation,
            "status": "accepted",
            "updatedAt": now_ms,
            "acceptedAt": now_ms,
            "acceptedByUserId": recipient.user_id,
            "acceptedByEmail": _normalize_email(recipient.email),
        }
        membership = build_membership_record(
            invitation_record=invitation,
            recipient=recipient,
            accepted_at=now_ms,
        )
        accepted_wire = _canonical_json(accepted_invitation)
        member_wire = _canonical_json(membership)
        member_user_pointer = _build_member_user_pointer(membership)
        if member_user_pointer is None:
            return None, _unavailable_error()
        member_user_pointer_wire = _canonical_json(member_user_pointer)
        workspace_id = str(invitation["workspaceId"])
        invitation_id = str(invitation["id"])
        email = str(invitation["inviteeEmail"])
        result, command_error = self._atomic(
            "accept",
            [
                _invitation_token_key(invitation_id, str(invitation["tokenDigest"])),
                _workspace_invitation_key(workspace_id, invitation_id),
                _workspace_recipient_invitation_key(workspace_id, email),
                _member_key(workspace_id, email),
                _members_index_key(workspace_id),
                _pending_index_key(workspace_id),
                _member_user_pointer_key(workspace_id, recipient.user_id),
            ],
            [
                current_wire,
                accepted_wire,
                member_wire,
                str(now_ms),
                email,
                invitation_id,
                workspace_id,
                recipient.user_id,
                member_user_pointer_wire,
            ],
        )
        mapped_error = self._transition_result_error(result)
        if mapped_error:
            return None, mapped_error
        if result not in {"applied", None}:
            return None, _unavailable_error()
        if (
            not self._invitation_readback(accepted_invitation, pending=False)
            or not self._membership_readback(membership, indexed=True)
            or not self._member_user_pointer_readback(membership, present=True)
        ):
            return None, command_error or _unavailable_error()
        return {
            "invite": project_public_invitation(accepted_invitation),
            "member": project_team_member(membership),
        }, None

    def _transition_result_error(self, result: str | None) -> TeamError | None:
        mapping = {
            "expired": _error("expired_invite", "Team invitation has expired."),
            "cancelled": _error("cancelled_invite", "Team invitation has been cancelled."),
            "declined": _error("declined_invite", "Team invitation has been declined."),
            "accepted": _error("used_invite", "Team invitation has already been used."),
            "member_active": _error("team_member_exists", "An active Team member already exists."),
        }
        return mapping.get(result)

    def _recipient_terminal_transition(self, *, actor: AuthenticatedMemberContext, token: str, target: str):
        try:
            recipient = _require_actor(actor)
        except (TypeError, ValueError):
            return None, _error("unauthorized", "Authentication is required.")
        invitation, current_wire, read_error = self._read_invitation_by_token(token)
        if read_error or invitation is None or current_wire is None:
            return None, read_error
        now_ms = self._now_ms()
        terminal_error = self._terminal_error(invitation, now_ms)
        if terminal_error:
            return None, terminal_error
        if _normalize_email(recipient.email) != invitation["inviteeEmail"]:
            return None, _error("wrong_recipient", "This invitation belongs to another recipient.")
        candidate = {
            **invitation,
            "status": target,
            "updatedAt": now_ms,
            f"{target}At": now_ms,
        }
        return self._apply_terminal_transition(invitation, current_wire, candidate, target)

    def _apply_terminal_transition(self, invitation: dict[str, object], current_wire: str, candidate: dict[str, object], target: str):
        workspace_id = str(invitation["workspaceId"])
        invitation_id = str(invitation["id"])
        email = str(invitation["inviteeEmail"])
        now_ms = int(candidate["updatedAt"])
        candidate_wire = _canonical_json(candidate)
        operation = "decline" if target == "declined" else "cancel"
        result, command_error = self._atomic(
            operation,
            [
                _invitation_token_key(invitation_id, str(invitation["tokenDigest"])),
                _workspace_invitation_key(workspace_id, invitation_id),
                _workspace_recipient_invitation_key(workspace_id, email),
                _pending_index_key(workspace_id),
            ],
            [current_wire, candidate_wire, str(now_ms), invitation_id, target],
        )
        mapped_error = self._transition_result_error(result)
        if mapped_error:
            return None, mapped_error
        if result not in {"applied", None}:
            return None, _unavailable_error()
        if not self._invitation_readback(candidate, pending=False):
            return None, command_error or _unavailable_error()
        projection = project_public_invitation(candidate)
        return ({"invite": projection}, None) if projection is not None else (None, _unavailable_error())

    def decline_invitation(self, *, actor: AuthenticatedMemberContext, token: str):
        return self._recipient_terminal_transition(actor=actor, token=token, target="declined")

    def cancel_invitation(self, *, actor: AuthenticatedMemberContext, invitation_id: str):
        try:
            canonical_actor = _require_owner(actor)
        except (TypeError, ValueError, PermissionError):
            return None, _error("forbidden", "Team owner authority is required.")
        if not _valid_invitation_id(invitation_id):
            return None, _error("invalid_invite", "Team invitation is invalid.")
        current_wire, read_error = self._get_raw(
            _workspace_invitation_key(canonical_actor.workspace_id, invitation_id)
        )
        if read_error:
            return None, read_error
        invitation = _decode_record(current_wire, _normalize_invitation_record)
        if invitation is None or invitation["workspaceId"] != canonical_actor.workspace_id or current_wire is None:
            return None, _error("invalid_invite", "Team invitation is invalid.")
        now_ms = self._now_ms()
        terminal_error = self._terminal_error(invitation, now_ms)
        if terminal_error:
            return None, terminal_error
        candidate = {
            **invitation,
            "status": "cancelled",
            "updatedAt": now_ms,
            "cancelledAt": now_ms,
        }
        result, error = self._apply_terminal_transition(
            invitation,
            current_wire,
            candidate,
            "cancelled",
        )
        if error or result is None:
            return None, error
        pending_projection = project_pending_invitation(candidate)
        return ({"invite": pending_projection}, None) if pending_projection else (None, _unavailable_error())

    def remove_member(self, *, actor: AuthenticatedMemberContext, member_email: str):
        try:
            canonical_actor = _require_owner(actor)
        except (TypeError, ValueError, PermissionError):
            return None, _error("forbidden", "Team owner authority is required.")
        email = _normalize_legacy_member_identifier(member_email)
        strict_email = _normalize_email(member_email)
        if not email:
            return None, _error("invalid_request", "Member identifier is invalid.")
        key = _member_key(canonical_actor.workspace_id, email)
        current_wire, read_error = self._get_raw(key)
        if read_error:
            return None, read_error
        current = _decode_record(current_wire, _normalize_mutable_membership_record)
        if current is None or current_wire is None:
            return None, _error("team_member_not_found", "Team member was not found.")
        if current["workspaceId"] != canonical_actor.workspace_id or current["email"] != email:
            return None, _error("team_member_not_found", "Team member was not found.")
        if not strict_email and current["v"] != 1:
            return None, _error("team_member_not_found", "Team member was not found.")
        if current["status"] != "active":
            return None, _error("team_member_not_active", "Team member is not active.")
        removed_at = self._now_ms()
        if current["v"] == TEAM_AUTHORITY_SCHEMA_VERSION:
            removed = {
                **current,
                "status": "removed",
                "updatedAt": removed_at,
                "removedAt": removed_at,
                "revokedAt": removed_at,
            }
        else:
            # A removed v1 record is no longer roster-visible, so there is no
            # compatibility reason to retain its raw legacy invitation bearer.
            removed = {
                "v": 1,
                "workspaceId": current["workspaceId"],
                "email": current["email"],
                "displayName": current["displayName"],
                "accessLevel": current["accessLevel"],
                "status": "removed",
                "createdAt": current["createdAt"],
                "updatedAt": removed_at,
                "acceptedAt": current["acceptedAt"],
                "removedAt": removed_at,
                "revokedAt": removed_at,
            }
        removed_wire = _canonical_json(removed)
        mutation_keys = [key, _members_index_key(canonical_actor.workspace_id)]
        mutation_arguments: list[object] = [current_wire, removed_wire, email]
        pointer_required = current["v"] == TEAM_AUTHORITY_SCHEMA_VERSION
        if pointer_required:
            pointer = _build_member_user_pointer(current)
            if pointer is None:
                return None, _unavailable_error()
            mutation_keys.append(
                _member_user_pointer_key(
                    canonical_actor.workspace_id,
                    str(current["memberUserId"]),
                )
            )
            mutation_arguments.append(_canonical_json(pointer))
        result, command_error = self._atomic(
            "remove",
            mutation_keys,
            mutation_arguments,
        )
        if result == "missing":
            return None, _error("team_member_not_found", "Team member was not found.")
        if result == "not_active":
            return None, _error("team_member_not_active", "Team member is not active.")
        if result not in {"applied", None}:
            return None, _unavailable_error()
        pointer_removed = (
            not pointer_required
            or self._member_user_pointer_readback(current, present=False)
        )
        if not self._membership_readback(removed, indexed=False) or not pointer_removed:
            return None, command_error or _unavailable_error()
        return {"email": email, "status": "removed", "removedAt": removed_at}, None

    def update_member_access(self, *, actor: AuthenticatedMemberContext, member_email: str, access_level: str):
        try:
            canonical_actor = _require_owner(actor)
        except (TypeError, ValueError, PermissionError):
            return None, _error("forbidden", "Team owner authority is required.")
        email = _normalize_legacy_member_identifier(member_email)
        strict_email = _normalize_email(member_email)
        if (
            not email
            or type(access_level) is not str
            or access_level not in TEAM_ACCESS_LEVELS
        ):
            return None, _error("invalid_request", "Member access update is invalid.")
        key = _member_key(canonical_actor.workspace_id, email)
        current_wire, read_error = self._get_raw(key)
        if read_error:
            return None, read_error
        current = _decode_record(current_wire, _normalize_mutable_membership_record)
        if current is None or current_wire is None:
            return None, _error("team_member_not_found", "Team member was not found.")
        if current["workspaceId"] != canonical_actor.workspace_id or current["email"] != email:
            return None, _error("team_member_not_found", "Team member was not found.")
        if not strict_email and current["v"] != 1:
            return None, _error("team_member_not_found", "Team member was not found.")
        if current["status"] != "active":
            return None, _error("team_member_not_active", "Team member is not active.")
        updated_at = self._now_ms()
        if current["v"] == TEAM_AUTHORITY_SCHEMA_VERSION:
            updated = {
                **current,
                "accessLevel": access_level,
                "updatedAt": updated_at,
            }
        else:
            # Keep only the fields the safe legacy roster reader requires.
            # Its raw bearer remains internal and is never part of the DTO.
            updated = {
                "v": 1,
                "workspaceId": current["workspaceId"],
                "email": current["email"],
                "displayName": current["displayName"],
                "accessLevel": access_level,
                "status": "active",
                "inviteToken": current["inviteToken"],
                "createdAt": current["createdAt"],
                "updatedAt": updated_at,
                "acceptedAt": current["acceptedAt"],
            }
        updated_wire = _canonical_json(updated)
        result, command_error = self._atomic("update_access", [key], [current_wire, updated_wire])
        if result == "missing":
            return None, _error("team_member_not_found", "Team member was not found.")
        if result == "not_active":
            return None, _error("team_member_not_active", "Team member is not active.")
        if result not in {"applied", None}:
            return None, _unavailable_error()
        raw, readback_error = self._get_raw(key)
        if readback_error or raw != updated_wire:
            return None, command_error or readback_error or _unavailable_error()
        projection = _project_mutable_team_member(updated)
        return (projection, None) if projection is not None else (None, _unavailable_error())

    def list_members(self, *, actor: AuthenticatedMemberContext):
        try:
            canonical_actor = _require_actor(actor)
        except (TypeError, ValueError):
            return None, _error("unauthorized", "Authentication is required.")
        index_raw, index_error = self._get_raw(_members_index_key(canonical_actor.workspace_id))
        if index_error:
            return None, index_error
        emails = _normalize_index(index_raw)
        if emails is None:
            return None, _unavailable_error()
        members: list[dict[str, object]] = []
        for email in emails:
            normalized_email = _normalize_email(email)
            if not normalized_email:
                return None, _unavailable_error()
            raw, error = self._get_raw(_member_key(canonical_actor.workspace_id, normalized_email))
            if error:
                return None, error
            record = _decode_record(raw, _normalize_membership_record)
            if record is None or record["workspaceId"] != canonical_actor.workspace_id or record["email"] != normalized_email:
                return None, _unavailable_error()
            if record["status"] == "active":
                projected = project_team_member(record)
                if projected is None:
                    return None, _unavailable_error()
                members.append(projected)
        return members, None


def build_runtime_team_authority(
    environment: Mapping[str, str] | None = None,
    *,
    command_transport: CommandTransport | None = None,
    now_ms: Clock | None = None,
    random_bytes: RandomBytes = secrets.token_bytes,
    inviter_owner_validator: InviterOwnerValidator | None = None,
) -> RuntimeTeamAuthority:
    source = os.environ if environment is None else environment
    transport = command_transport
    if transport is None:
        config = _resolve_store_config(source)
        transport = _runtime_transport(config) if config is not None else None
    return RuntimeTeamAuthority(
        transport,
        environment=source,
        now_ms=now_ms,
        random_bytes=random_bytes,
        inviter_owner_validator=inviter_owner_validator,
    )


__all__ = (
    "ATOMIC_MUTATION_SCRIPTS",
    "RuntimeTeamAuthority",
    "TEAM_ACCESS_LEVELS",
    "TEAM_AUTHORITY_SCHEMA_VERSION",
    "TEAM_INVITE_ID_BYTES",
    "TEAM_INVITE_TOKEN_BYTES",
    "TEAM_INVITE_TTL_MS",
    "build_invitation_record",
    "build_membership_record",
    "build_runtime_team_authority",
    "generate_invitation_id",
    "generate_invitation_token",
    "project_pending_invitation",
    "project_public_invitation",
    "project_team_member",
    "verify_invitation_token",
)
