"""Durable, bounded exact-identity recovery queue for Priority candidates.

The queue contains workflow authority metadata and exact provider identity only.
It deliberately contains no candidate render content and performs no provider I/O.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Literal

from .authority import PriorityMessageIdentity, parse_priority_message_identity
from .event_reference import derive_priority_hmac_key


RECOVERY_STORE_SCHEMA_VERSION = 1
RECOVERY_MAX_MAILBOX_RECORDS = 64
RECOVERY_MAX_USER_RECORDS = 256
RECOVERY_MAX_CLAIM_RECORDS = 8
RECOVERY_MAX_ATTEMPTS = 32
RECOVERY_LEASE_TTL_MILLISECONDS = 90 * 1_000
RECOVERY_INDEX_TTL_MILLISECONDS = 180 * 24 * 60 * 60 * 1_000
RECOVERY_MAX_SERIALIZED_RECORD_BYTES = 24 * 1_024
RECOVERY_MAX_SAFE_INTEGER = 9_007_199_254_740_991
RECOVERY_MAX_IDENTITY_CANONICAL_BYTES = 2_048

_KEY_PREFIX = "cuevion:priority:recovery:v1:"
_MAILBOX_SCOPE_HMAC_INFO = b"cuevion/priority/recovery-mailbox-scope/v1\x00"
_USER_SCOPE_HMAC_INFO = b"cuevion/priority/recovery-user-scope/v1\x00"
_IDENTITY_HMAC_INFO = b"cuevion/priority/recovery-identity/v1\x00"
_RECORD_MAC_HMAC_INFO = b"cuevion/priority/recovery-record-mac/v1\x00"
_HEX_DIGEST_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_MISSING_SENTINEL = "__cuevion_priority_recovery_missing__"
_INVALID_SENTINEL = "__cuevion_priority_recovery_invalid__"
_CONFLICT_SENTINEL = "__cuevion_priority_recovery_conflict__"
_EXPIRED_SENTINEL = "__cuevion_priority_recovery_expired__"
_MAILBOX_CAPACITY_SENTINEL = "__cuevion_priority_recovery_mailbox_capacity__"
_USER_CAPACITY_SENTINEL = "__cuevion_priority_recovery_user_capacity__"
_CLAIM_LOST_SENTINEL = "__cuevion_priority_recovery_claim_lost__"

CommandTransport = Callable[[list[object]], dict[str, object]]


class RecoveryStoreUnavailable(Exception):
    """A value-free failure for unavailable or malformed recovery storage."""

    __slots__ = ()

    def __str__(self) -> str:
        return "Priority candidate recovery storage is unavailable"


class RecoveryCapacityExceeded(Exception):
    """A bounded queue capacity failure with no tenant data."""

    __slots__ = ("scope_kind",)

    def __init__(self, scope_kind: str) -> None:
        self.scope_kind = scope_kind if scope_kind in {"mailbox", "user"} else "user"
        Exception.__init__(self)

    def __str__(self) -> str:
        return "Priority candidate recovery capacity is exceeded"


class RecoveryEnqueueResult(str, Enum):
    QUEUED = "recovery_queued"
    UPDATED = "recovery_updated"
    EXPIRED = "authority_expired"


class RecoveryAckResult(str, Enum):
    COMPLETED = "recovery_completed"
    CLAIM_LOST = "claim_lost"


class RecoveryRetryResult(str, Enum):
    RETRIED = "recovery_retried"
    AUTHORITY_EXPIRED = "authority_expired"
    ATTEMPTS_EXHAUSTED = "retry_exhausted"
    CLAIM_LOST = "claim_lost"


def _valid_identifier(value: object, maximum_bytes: int) -> bool:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or "\x00" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum_bytes
    except UnicodeEncodeError:
        return False


@dataclass(frozen=True, slots=True)
class PriorityCandidateRecoveryMailboxScope:
    workspace_id: str
    user_id: str
    mailbox_id: str
    mailbox_account_identity: str
    provider: Literal["google", "custom_imap"]

    def canonical_bytes(self) -> bytes:
        values = (
            self.workspace_id,
            self.user_id,
            self.mailbox_id,
            self.mailbox_account_identity,
            self.provider,
        )
        if (
            any(not _valid_identifier(value, 1_024) for value in values)
            or self.provider not in {"google", "custom_imap"}
            or self.mailbox_account_identity
            != self.mailbox_account_identity.casefold()
        ):
            raise ValueError("invalid Priority candidate recovery mailbox scope")
        return "\x00".join(values).encode("utf-8", errors="strict")

    def user_canonical_bytes(self) -> bytes:
        self.canonical_bytes()
        return "\x00".join((self.workspace_id, self.user_id)).encode(
            "utf-8", errors="strict"
        )


@dataclass(frozen=True, slots=True)
class PriorityCandidateRecoveryScope:
    mailbox_scope: PriorityCandidateRecoveryMailboxScope
    identity: PriorityMessageIdentity

    def canonical_bytes(self) -> bytes:
        if not isinstance(self.mailbox_scope, PriorityCandidateRecoveryMailboxScope):
            raise ValueError("invalid Priority candidate recovery scope")
        mailbox_bytes = self.mailbox_scope.canonical_bytes()
        try:
            identity_bytes = self.identity.canonical_bytes()
        except Exception:
            raise ValueError("invalid Priority candidate recovery scope") from None
        if (
            self.identity.provider != self.mailbox_scope.provider
            or not 1 <= len(identity_bytes) <= RECOVERY_MAX_IDENTITY_CANONICAL_BYTES
        ):
            raise ValueError("invalid Priority candidate recovery scope")
        return mailbox_bytes + b"\x00" + identity_bytes


@dataclass(frozen=True, slots=True)
class PriorityCandidateRecoveryRecord:
    scope: PriorityCandidateRecoveryScope
    workflow_version: int
    authority_expires_at: int
    enqueued_at: int
    updated_at: int
    attempt_count: int
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.scope, PriorityCandidateRecoveryScope):
            raise ValueError("invalid Priority candidate recovery record")
        self.scope.canonical_bytes()
        if (
            type(self.workflow_version) is not int
            or not 1 <= self.workflow_version <= RECOVERY_MAX_SAFE_INTEGER
            or type(self.authority_expires_at) is not int
            or not 1 <= self.authority_expires_at <= RECOVERY_MAX_SAFE_INTEGER
            or type(self.enqueued_at) is not int
            or not 0 <= self.enqueued_at < self.authority_expires_at
            or type(self.updated_at) is not int
            or not self.enqueued_at <= self.updated_at < self.authority_expires_at
            or type(self.attempt_count) is not int
            or not 0 <= self.attempt_count < RECOVERY_MAX_ATTEMPTS
            or type(self.generation) is not int
            or not 1 <= self.generation <= RECOVERY_MAX_SAFE_INTEGER
        ):
            raise ValueError("invalid Priority candidate recovery record")


@dataclass(frozen=True, slots=True)
class PriorityCandidateRecoveryClaim:
    record: PriorityCandidateRecoveryRecord
    identity_digest: str
    lease_token: str
    claimed_at: int
    lease_expires_at: int
    raw_record: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.record, PriorityCandidateRecoveryRecord)
            or type(self.identity_digest) is not str
            or _HEX_DIGEST_RE.fullmatch(self.identity_digest) is None
            or type(self.lease_token) is not str
            or _HEX_DIGEST_RE.fullmatch(self.lease_token) is None
            or type(self.claimed_at) is not int
            or not 0 <= self.claimed_at < self.record.authority_expires_at
            or type(self.lease_expires_at) is not int
            or not self.claimed_at < self.lease_expires_at
            <= self.record.authority_expires_at
            or type(self.raw_record) is not str
        ):
            raise ValueError("invalid Priority candidate recovery claim")


def _digest(secret: str, purpose: bytes, value: bytes) -> str:
    key = derive_priority_hmac_key(secret, purpose)
    return hmac.new(key, value, hashlib.sha256).hexdigest()


def derive_recovery_mailbox_scope_digest(
    secret: str,
    scope: PriorityCandidateRecoveryMailboxScope,
) -> str:
    if not isinstance(scope, PriorityCandidateRecoveryMailboxScope):
        raise ValueError("invalid Priority candidate recovery mailbox scope")
    return _digest(secret, _MAILBOX_SCOPE_HMAC_INFO, scope.canonical_bytes())


def derive_recovery_user_scope_digest(
    secret: str,
    scope: PriorityCandidateRecoveryMailboxScope,
) -> str:
    if not isinstance(scope, PriorityCandidateRecoveryMailboxScope):
        raise ValueError("invalid Priority candidate recovery mailbox scope")
    return _digest(secret, _USER_SCOPE_HMAC_INFO, scope.user_canonical_bytes())


def derive_recovery_identity_digest(
    secret: str,
    scope: PriorityCandidateRecoveryScope,
) -> str:
    if not isinstance(scope, PriorityCandidateRecoveryScope):
        raise ValueError("invalid Priority candidate recovery scope")
    return _digest(secret, _IDENTITY_HMAC_INFO, scope.canonical_bytes())


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str):
    raise ValueError("invalid JSON constant")


_ROOT_FIELDS = frozenset(
    {
        "schemaVersion",
        "mailboxScopeDigest",
        "identityDigest",
        "identity",
        "workflowVersion",
        "authorityExpiresAt",
        "enqueuedAt",
        "updatedAt",
        "attemptCount",
        "generation",
        "recordMac",
    }
)


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _record_payload_without_mac(
    secret: str,
    record: PriorityCandidateRecoveryRecord,
) -> dict[str, object]:
    mailbox_digest = derive_recovery_mailbox_scope_digest(
        secret, record.scope.mailbox_scope
    )
    identity_digest = derive_recovery_identity_digest(secret, record.scope)
    return {
        "schemaVersion": RECOVERY_STORE_SCHEMA_VERSION,
        "mailboxScopeDigest": mailbox_digest,
        "identityDigest": identity_digest,
        "identity": record.scope.identity.to_wire_dict(),
        "workflowVersion": record.workflow_version,
        "authorityExpiresAt": record.authority_expires_at,
        "enqueuedAt": record.enqueued_at,
        "updatedAt": record.updated_at,
        "attemptCount": record.attempt_count,
        "generation": record.generation,
    }


def _record_mac(secret: str, payload: dict[str, object]) -> str:
    key = derive_priority_hmac_key(secret, _RECORD_MAC_HMAC_INFO)
    return hmac.new(
        key,
        _canonical_json(payload).encode("ascii", errors="strict"),
        hashlib.sha256,
    ).hexdigest()


def _encode_record(secret: str, record: PriorityCandidateRecoveryRecord) -> str:
    payload = _record_payload_without_mac(secret, record)
    payload["recordMac"] = _record_mac(secret, payload)
    encoded = _canonical_json(payload)
    if len(encoded.encode("ascii")) > RECOVERY_MAX_SERIALIZED_RECORD_BYTES:
        raise ValueError("invalid Priority candidate recovery record")
    return encoded


def _decode_record(
    value: object,
    *,
    secret: str,
    expected_mailbox_scope: PriorityCandidateRecoveryMailboxScope,
    expected_identity_digest: str | None = None,
) -> PriorityCandidateRecoveryRecord | None:
    if type(value) is not str:
        return None
    try:
        if len(value.encode("utf-8", errors="strict")) > RECOVERY_MAX_SERIALIZED_RECORD_BYTES:
            return None
        payload = json.loads(
            value,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except Exception:
        return None
    if (
        type(payload) is not dict
        or set(payload) != _ROOT_FIELDS
        or type(payload["schemaVersion"]) is not int
        or payload["schemaVersion"] != RECOVERY_STORE_SCHEMA_VERSION
        or type(payload["mailboxScopeDigest"]) is not str
        or _HEX_DIGEST_RE.fullmatch(payload["mailboxScopeDigest"]) is None
        or type(payload["identityDigest"]) is not str
        or _HEX_DIGEST_RE.fullmatch(payload["identityDigest"]) is None
        or type(payload["recordMac"]) is not str
        or _HEX_DIGEST_RE.fullmatch(payload["recordMac"]) is None
    ):
        return None
    integer_fields = (
        "workflowVersion",
        "authorityExpiresAt",
        "enqueuedAt",
        "updatedAt",
        "attemptCount",
        "generation",
    )
    if any(
        type(payload[field]) is not int
        or not 0 <= payload[field] <= RECOVERY_MAX_SAFE_INTEGER
        for field in integer_fields
    ):
        return None
    try:
        identity = parse_priority_message_identity(
            payload["identity"], expected_provider=expected_mailbox_scope.provider
        )
        scope = PriorityCandidateRecoveryScope(expected_mailbox_scope, identity)
        scope.canonical_bytes()
        record = PriorityCandidateRecoveryRecord(
            scope=scope,
            workflow_version=payload["workflowVersion"],
            authority_expires_at=payload["authorityExpiresAt"],
            enqueued_at=payload["enqueuedAt"],
            updated_at=payload["updatedAt"],
            attempt_count=payload["attemptCount"],
            generation=payload["generation"],
        )
        mailbox_digest = derive_recovery_mailbox_scope_digest(
            secret, expected_mailbox_scope
        )
        identity_digest = derive_recovery_identity_digest(secret, scope)
        mac_payload = dict(payload)
        supplied_mac = mac_payload.pop("recordMac")
        if (
            not hmac.compare_digest(payload["mailboxScopeDigest"], mailbox_digest)
            or not hmac.compare_digest(payload["identityDigest"], identity_digest)
            or (
                expected_identity_digest is not None
                and not hmac.compare_digest(expected_identity_digest, identity_digest)
            )
            or not hmac.compare_digest(supplied_mac, _record_mac(secret, mac_payload))
        ):
            return None
    except Exception:
        return None
    return record


def _safe_redis_integer(value: object) -> int | None:
    if type(value) is int:
        parsed = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        parsed = int(value)
    elif type(value) is str and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.0+)?", value):
        try:
            parsed = int(value.split(".", 1)[0])
        except ValueError:
            return None
    else:
        return None
    return parsed if 0 <= parsed <= RECOVERY_MAX_SAFE_INTEGER else None


_KEY_TYPE_HELPER = r"""
local function keyType(key)
  local value=redis.call('TYPE',key)
  if type(value)=='table' then return value['ok'] end
  return value
end
"""

_PRUNE_HELPER = r"""
local function validDigest(value)
  return type(value)=='string' and string.len(value)==64 and
    string.match(value,'^[0-9a-f]+$')~=nil
end
local function pruneExpired(now,maximum)
  local expired=redis.call('ZRANGEBYSCORE',KEYS[4],'-inf',now,
    'LIMIT',0,maximum+1)
  if #expired>maximum then return false end
  for _,member in ipairs(expired) do
    if not validDigest(member) then return false end
  end
  for _,member in ipairs(expired) do
    redis.call('DEL',ARGV[1]..member,ARGV[2]..member)
    redis.call('ZREM',KEYS[3],member)
    redis.call('ZREM',KEYS[4],member)
    redis.call('ZREM',KEYS[5],member)
  end
  redis.call('ZREMRANGEBYSCORE',KEYS[5],'-inf',now)
  return true
end
"""

_ENQUEUE_SCRIPT = _KEY_TYPE_HELPER + _PRUNE_HELPER + r"""
local expected={{KEYS[1],'string'},{KEYS[2],'string'},{KEYS[3],'zset'},
  {KEYS[4],'zset'},{KEYS[5],'zset'}}
for _,item in ipairs(expected) do
  local actual=keyType(item[1])
  if actual~='none' and actual~=item[2] then return ARGV[15] end
end
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2])
if not seconds or not micros then return ARGV[15] end
local now=seconds*1000+math.floor(micros/1000)
if now<0 or now>tonumber(ARGV[10]) or
  not pruneExpired(now,tonumber(ARGV[8])) then return ARGV[15] end
local dueMembers=redis.call('ZRANGE',KEYS[3],0,tonumber(ARGV[8]))
if #dueMembers>tonumber(ARGV[8]) then return ARGV[15] end
for _,member in ipairs(dueMembers) do
  if not validDigest(member) or not redis.call('ZSCORE',KEYS[4],member) then
    return ARGV[15]
  end
end
local current=redis.call('GET',KEYS[1])
if ARGV[3]==ARGV[11] then
  if current then return ARGV[12] end
else
  if not current or current~=ARGV[3] then return ARGV[12] end
end
local due=redis.call('ZSCORE',KEYS[3],ARGV[5])
local expiry=redis.call('ZSCORE',KEYS[4],ARGV[5])
local user=redis.call('ZSCORE',KEYS[5],ARGV[5])
if current then
  if not due or not expiry or not user or
    tonumber(expiry)~=tonumber(user) then return ARGV[15] end
elseif due or expiry or user then return ARGV[15] end
local authority=tonumber(ARGV[6]);local updated=tonumber(ARGV[7])
if not authority or authority%1~=0 or authority<1 or
  authority>tonumber(ARGV[10]) or not updated or updated%1~=0 or
  updated<0 or updated>now then return ARGV[15] end
if authority<=now then
  redis.call('DEL',KEYS[1],KEYS[2])
  redis.call('ZREM',KEYS[3],ARGV[5]);redis.call('ZREM',KEYS[4],ARGV[5])
  redis.call('ZREM',KEYS[5],ARGV[5])
  return ARGV[13]
end
if not current and redis.call('ZCARD',KEYS[3])>=tonumber(ARGV[8]) then
  return ARGV[14]
end
if not current and redis.call('ZCARD',KEYS[5])>=tonumber(ARGV[9]) then
  return ARGV[16]
end
redis.call('SET',KEYS[1],ARGV[4]);redis.call('PEXPIREAT',KEYS[1],authority)
redis.call('DEL',KEYS[2])
redis.call('ZADD',KEYS[3],now,ARGV[5])
redis.call('ZADD',KEYS[4],authority,ARGV[5])
redis.call('ZADD',KEYS[5],authority,ARGV[5])
for index=3,5 do redis.call('PEXPIRE',KEYS[index],ARGV[17]) end
return current and 2 or 1
"""

_READ_EXACT_SCRIPT = _KEY_TYPE_HELPER + r"""
local expected={{KEYS[1],'string'},{KEYS[2],'string'},{KEYS[3],'zset'},
  {KEYS[4],'zset'},{KEYS[5],'zset'}}
for _,item in ipairs(expected) do
  local actual=keyType(item[1])
  if actual~='none' and actual~=item[2] then return {ARGV[3]} end
end
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2])
if not seconds or not micros then return {ARGV[3]} end
local now=seconds*1000+math.floor(micros/1000)
local current=redis.call('GET',KEYS[1])
local due=redis.call('ZSCORE',KEYS[3],ARGV[1])
local expiry=redis.call('ZSCORE',KEYS[4],ARGV[1])
local user=redis.call('ZSCORE',KEYS[5],ARGV[1])
if not current then
  if not due and not expiry and not user then return {now,ARGV[2]} end
  if expiry and tonumber(expiry)<=now then
    redis.call('DEL',KEYS[2]);redis.call('ZREM',KEYS[3],ARGV[1])
    redis.call('ZREM',KEYS[4],ARGV[1]);redis.call('ZREM',KEYS[5],ARGV[1])
    return {now,ARGV[2]}
  end
  return {ARGV[3]}
end
if not due or not expiry or not user or tonumber(expiry)~=tonumber(user) or
  tonumber(expiry)<=now then return {ARGV[3]} end
return {now,current,due,expiry}
"""

_CANCEL_SCRIPT = _KEY_TYPE_HELPER + r"""
local expected={{KEYS[1],'string'},{KEYS[2],'string'},{KEYS[3],'zset'},
  {KEYS[4],'zset'},{KEYS[5],'zset'}}
for _,item in ipairs(expected) do
  local actual=keyType(item[1])
  if actual~='none' and actual~=item[2] then return -1 end
end
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2]);if not seconds or not micros then return -1 end
local now=seconds*1000+math.floor(micros/1000)
local current=redis.call('GET',KEYS[1])
if ARGV[2]==ARGV[3] then
  if current then return 2 end
else
  if not current or current~=ARGV[2] then return 2 end
end
local due=redis.call('ZSCORE',KEYS[3],ARGV[1])
local expiry=redis.call('ZSCORE',KEYS[4],ARGV[1])
local user=redis.call('ZSCORE',KEYS[5],ARGV[1])
if current then
  if not due or not expiry or not user or
    tonumber(expiry)~=tonumber(user) then return -1 end
elseif (due or expiry or user) and
  (not expiry or tonumber(expiry)>now) then return -1 end
redis.call('DEL',KEYS[1],KEYS[2]);redis.call('ZREM',KEYS[3],ARGV[1])
redis.call('ZREM',KEYS[4],ARGV[1]);redis.call('ZREM',KEYS[5],ARGV[1])
if redis.call('ZCARD',KEYS[3])==0 then redis.call('DEL',KEYS[3],KEYS[4]) end
if redis.call('ZCARD',KEYS[5])==0 then redis.call('DEL',KEYS[5]) end
return current and 1 or 0
"""

_PEEK_DUE_SCRIPT = _KEY_TYPE_HELPER + r"""
local function validDigest(value)
  return type(value)=='string' and string.len(value)==64 and
    string.match(value,'^[0-9a-f]+$')~=nil
end
for index=1,3 do
  local actual=keyType(KEYS[index])
  if actual~='none' and actual~='zset' then return {ARGV[5]} end
end
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2]);if not seconds or not micros then return {ARGV[5]} end
local now=seconds*1000+math.floor(micros/1000)
local expired=redis.call('ZRANGEBYSCORE',KEYS[2],'-inf',now,
  'LIMIT',0,tonumber(ARGV[4])+1)
if #expired>tonumber(ARGV[4]) then return {ARGV[5]} end
for _,member in ipairs(expired) do
  if not validDigest(member) then return {ARGV[5]} end
end
for _,member in ipairs(expired) do
  redis.call('DEL',ARGV[1]..member,ARGV[2]..member)
  redis.call('ZREM',KEYS[1],member);redis.call('ZREM',KEYS[2],member)
  redis.call('ZREM',KEYS[3],member)
end
redis.call('ZREMRANGEBYSCORE',KEYS[3],'-inf',now)
local members=redis.call('ZRANGEBYSCORE',KEYS[1],'-inf',now,'WITHSCORES',
  'LIMIT',0,tonumber(ARGV[4]))
local result={now};local selected=0
for index=1,#members,2 do
  if selected>=tonumber(ARGV[3]) then break end
  local member=members[index];local due=members[index+1]
  if not validDigest(member) then return {ARGV[5]} end
  if not redis.call('GET',ARGV[2]..member) then
    local value=redis.call('GET',ARGV[1]..member)
    local expiry=redis.call('ZSCORE',KEYS[2],member)
    local user=redis.call('ZSCORE',KEYS[3],member)
    if not value or not expiry or not user or
      tonumber(expiry)~=tonumber(user) or tonumber(expiry)<=now then
      return {ARGV[5]}
    end
    result[#result+1]=member;result[#result+1]=value
    result[#result+1]=due;result[#result+1]=expiry
    selected=selected+1
  end
end
return result
"""

_CLAIM_SCRIPT = _KEY_TYPE_HELPER + r"""
for index=1,3 do
  local actual=keyType(KEYS[index])
  if actual~='none' and actual~='zset' then return {ARGV[2]} end
end
local count=tonumber(ARGV[1])
if not count or count<1 or count>8 or #KEYS~=3+count*2 then return {ARGV[2]} end
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2]);if not seconds or not micros then return {ARGV[2]} end
local now=seconds*1000+math.floor(micros/1000)
local leaseExpiries={}
for index=1,count do
  local offset=3+(index-1)*5
  local member=ARGV[offset+1];local expectedRaw=ARGV[offset+2]
  local expectedDue=tonumber(ARGV[offset+3]);local expectedExpiry=tonumber(ARGV[offset+4])
  local token=ARGV[offset+5];local recordKey=KEYS[3+(index-1)*2+1]
  local leaseKey=KEYS[3+(index-1)*2+2]
  local due=redis.call('ZSCORE',KEYS[1],member)
  local expiry=redis.call('ZSCORE',KEYS[2],member)
  local user=redis.call('ZSCORE',KEYS[3],member)
  if redis.call('GET',recordKey)~=expectedRaw or redis.call('GET',leaseKey) or
    not due or not expiry or not user or tonumber(due)~=expectedDue or
    tonumber(due)>now or tonumber(expiry)~=expectedExpiry or
    tonumber(user)~=expectedExpiry or expectedExpiry<=now or
    type(token)~='string' or string.len(token)~=64 or
    not string.match(token,'^[0-9a-f]+$') then return {ARGV[3]} end
  local leaseExpiry=math.min(now+tonumber(ARGV[#ARGV]),expectedExpiry)
  if leaseExpiry<=now then return {ARGV[3]} end
  leaseExpiries[index]=leaseExpiry
end
for index=1,count do
  local offset=3+(index-1)*5;local member=ARGV[offset+1]
  local token=ARGV[offset+5];local leaseKey=KEYS[3+(index-1)*2+2]
  local leaseExpiry=leaseExpiries[index]
  redis.call('SET',leaseKey,token,'PX',leaseExpiry-now)
  redis.call('ZADD',KEYS[1],leaseExpiry,member)
end
local result={now};for _,value in ipairs(leaseExpiries) do result[#result+1]=value end
return result
"""

_ACK_SCRIPT = _KEY_TYPE_HELPER + r"""
local expected={{KEYS[1],'string'},{KEYS[2],'string'},{KEYS[3],'zset'},
  {KEYS[4],'zset'},{KEYS[5],'zset'}}
for _,item in ipairs(expected) do
  local actual=keyType(item[1]);if actual~='none' and actual~=item[2] then return -1 end
end
local current=redis.call('GET',KEYS[1]);local lease=redis.call('GET',KEYS[2])
if not current or current~=ARGV[1] or not lease or lease~=ARGV[2] then return 0 end
local ok,record=pcall(cjson.decode,current)
if not ok or type(record)~='table' or record['generation']~=tonumber(ARGV[4]) then
  return -1
end
local expiry=redis.call('ZSCORE',KEYS[4],ARGV[3])
local user=redis.call('ZSCORE',KEYS[5],ARGV[3])
if not redis.call('ZSCORE',KEYS[3],ARGV[3]) or not expiry or not user or
  tonumber(expiry)~=tonumber(user) then return -1 end
redis.call('DEL',KEYS[1],KEYS[2]);redis.call('ZREM',KEYS[3],ARGV[3])
redis.call('ZREM',KEYS[4],ARGV[3]);redis.call('ZREM',KEYS[5],ARGV[3])
if redis.call('ZCARD',KEYS[3])==0 then redis.call('DEL',KEYS[3],KEYS[4]) end
if redis.call('ZCARD',KEYS[5])==0 then redis.call('DEL',KEYS[5]) end
return 1
"""

_READ_CLAIM_TIME_SCRIPT = _KEY_TYPE_HELPER + r"""
local expected={{KEYS[1],'string'},{KEYS[2],'string'},{KEYS[3],'zset'},
  {KEYS[4],'zset'},{KEYS[5],'zset'}}
for _,item in ipairs(expected) do
  local actual=keyType(item[1]);if actual~='none' and actual~=item[2] then
    return {ARGV[5]}
  end
end
local current=redis.call('GET',KEYS[1]);local lease=redis.call('GET',KEYS[2])
if not current or current~=ARGV[1] or not lease or lease~=ARGV[2] then
  return {ARGV[4]}
end
local ok,record=pcall(cjson.decode,current)
if not ok or type(record)~='table' or record['generation']~=tonumber(ARGV[3]) then
  return {ARGV[5]}
end
local expiry=redis.call('ZSCORE',KEYS[4],ARGV[6])
local user=redis.call('ZSCORE',KEYS[5],ARGV[6])
if not redis.call('ZSCORE',KEYS[3],ARGV[6]) or not expiry or not user or
  tonumber(expiry)~=tonumber(user) then return {ARGV[5]} end
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2]);if not seconds or not micros then return {ARGV[5]} end
local now=seconds*1000+math.floor(micros/1000)
if tonumber(expiry)<=now then return {ARGV[4]} end
return {now,expiry}
"""

_RETRY_SCRIPT = _KEY_TYPE_HELPER + r"""
local expected={{KEYS[1],'string'},{KEYS[2],'string'},{KEYS[3],'zset'},
  {KEYS[4],'zset'},{KEYS[5],'zset'}}
for _,item in ipairs(expected) do
  local actual=keyType(item[1]);if actual~='none' and actual~=item[2] then return -1 end
end
local current=redis.call('GET',KEYS[1]);local lease=redis.call('GET',KEYS[2])
if not current or current~=ARGV[1] or not lease or lease~=ARGV[2] then return 0 end
local oldOk,oldRecord=pcall(cjson.decode,current)
local newOk,newRecord=pcall(cjson.decode,ARGV[3])
if not oldOk or not newOk or type(oldRecord)~='table' or type(newRecord)~='table' or
  oldRecord['generation']~=tonumber(ARGV[6]) or
  newRecord['generation']~=oldRecord['generation'] or
  newRecord['attemptCount']~=oldRecord['attemptCount']+1 or
  newRecord['workflowVersion']~=oldRecord['workflowVersion'] or
  newRecord['authorityExpiresAt']~=oldRecord['authorityExpiresAt'] or
  newRecord['enqueuedAt']~=oldRecord['enqueuedAt'] or
  newRecord['mailboxScopeDigest']~=oldRecord['mailboxScopeDigest'] or
  newRecord['identityDigest']~=oldRecord['identityDigest'] then return -1 end
local expiry=redis.call('ZSCORE',KEYS[4],ARGV[4])
local user=redis.call('ZSCORE',KEYS[5],ARGV[4])
if not redis.call('ZSCORE',KEYS[3],ARGV[4]) or not expiry or not user or
  tonumber(expiry)~=tonumber(user) or tonumber(expiry)~=oldRecord['authorityExpiresAt'] then
  return -1
end
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2]);if not seconds or not micros then return -1 end
local now=seconds*1000+math.floor(micros/1000);local nextDue=tonumber(ARGV[5])
if newRecord['updatedAt']>now or not nextDue or nextDue%1~=0 or
  nextDue<=now or nextDue>=tonumber(expiry) then return -1 end
redis.call('SET',KEYS[1],ARGV[3]);redis.call('PEXPIREAT',KEYS[1],expiry)
redis.call('DEL',KEYS[2]);redis.call('ZADD',KEYS[3],nextDue,ARGV[4])
return 1
"""


def _retry_delay_milliseconds(attempt: int) -> int:
    if type(attempt) is not int or attempt < 1:
        raise ValueError("invalid Priority candidate recovery attempt")
    seconds = {
        1: 60,
        2: 5 * 60,
        3: 30 * 60,
        4: 6 * 60 * 60,
        5: 24 * 60 * 60,
    }.get(attempt, 7 * 24 * 60 * 60)
    return seconds * 1_000


class PriorityCandidateRecoveryStore:
    """Strict Redis queue with exact records and bounded indexed access."""

    __slots__ = ("_transport", "_hmac_secret")

    def __init__(self, command_transport: CommandTransport, *, hmac_secret: str) -> None:
        if not callable(command_transport):
            raise ValueError("invalid Priority candidate recovery command transport")
        for purpose in (
            _MAILBOX_SCOPE_HMAC_INFO,
            _USER_SCOPE_HMAC_INFO,
            _IDENTITY_HMAC_INFO,
            _RECORD_MAC_HMAC_INFO,
        ):
            derive_priority_hmac_key(hmac_secret, purpose)
        self._transport = command_transport
        self._hmac_secret = hmac_secret

    def _command(self, command: list[object]) -> object:
        try:
            payload = self._transport(command)
        except Exception:
            raise RecoveryStoreUnavailable() from None
        if type(payload) is not dict or set(payload) != {"result"}:
            raise RecoveryStoreUnavailable()
        return payload["result"]

    def _mailbox_keys(
        self, scope: PriorityCandidateRecoveryMailboxScope
    ) -> dict[str, str]:
        mailbox_digest = derive_recovery_mailbox_scope_digest(
            self._hmac_secret, scope
        )
        user_digest = derive_recovery_user_scope_digest(self._hmac_secret, scope)
        return {
            "due": f"{_KEY_PREFIX}due:{mailbox_digest}",
            "expiry": f"{_KEY_PREFIX}expiry:{mailbox_digest}",
            "user": f"{_KEY_PREFIX}user:{user_digest}",
        }

    def _scope_keys(self, scope: PriorityCandidateRecoveryScope) -> dict[str, str]:
        identity_digest = derive_recovery_identity_digest(self._hmac_secret, scope)
        keys = self._mailbox_keys(scope.mailbox_scope)
        keys.update(
            {
                "member": identity_digest,
                "record": f"{_KEY_PREFIX}record:{identity_digest}",
                "lease": f"{_KEY_PREFIX}lease:{identity_digest}",
            }
        )
        return keys

    def _read_raw(
        self, scope: PriorityCandidateRecoveryScope
    ) -> tuple[PriorityCandidateRecoveryRecord | None, str | None, int]:
        if not isinstance(scope, PriorityCandidateRecoveryScope):
            raise ValueError("invalid Priority candidate recovery scope")
        keys = self._scope_keys(scope)
        result = self._command(
            [
                "EVAL",
                _READ_EXACT_SCRIPT,
                5,
                keys["record"],
                keys["lease"],
                keys["due"],
                keys["expiry"],
                keys["user"],
                keys["member"],
                _MISSING_SENTINEL,
                _INVALID_SENTINEL,
            ]
        )
        if type(result) is not list or not result:
            raise RecoveryStoreUnavailable()
        if result == [_INVALID_SENTINEL]:
            raise RecoveryStoreUnavailable()
        now = _safe_redis_integer(result[0])
        if now is None:
            raise RecoveryStoreUnavailable()
        if len(result) == 2 and result[1] == _MISSING_SENTINEL:
            return None, None, now
        if len(result) != 4 or type(result[1]) is not str:
            raise RecoveryStoreUnavailable()
        due = _safe_redis_integer(result[2])
        expiry = _safe_redis_integer(result[3])
        record = _decode_record(
            result[1],
            secret=self._hmac_secret,
            expected_mailbox_scope=scope.mailbox_scope,
            expected_identity_digest=keys["member"],
        )
        if (
            record is None
            or record.scope != scope
            or due is None
            or expiry is None
            or expiry != record.authority_expires_at
            or not record.updated_at <= now < expiry
        ):
            raise RecoveryStoreUnavailable()
        return record, result[1], now

    def read_record(
        self, scope: PriorityCandidateRecoveryScope
    ) -> PriorityCandidateRecoveryRecord | None:
        return self._read_raw(scope)[0]

    def enqueue(
        self,
        scope: PriorityCandidateRecoveryScope,
        *,
        workflow_version: int,
        authority_expires_at: int,
        authoritative_now: int,
    ) -> RecoveryEnqueueResult:
        if (
            not isinstance(scope, PriorityCandidateRecoveryScope)
            or type(workflow_version) is not int
            or not 1 <= workflow_version <= RECOVERY_MAX_SAFE_INTEGER
            or type(authority_expires_at) is not int
            or not 1 <= authority_expires_at <= RECOVERY_MAX_SAFE_INTEGER
            or type(authoritative_now) is not int
            or not 0 <= authoritative_now <= RECOVERY_MAX_SAFE_INTEGER
        ):
            raise ValueError("invalid Priority candidate recovery enqueue")
        for _attempt in range(2):
            existing, expected_raw, observed_now = self._read_raw(scope)
            if authority_expires_at <= observed_now:
                self.cancel(scope)
                return RecoveryEnqueueResult.EXPIRED
            if existing is not None and workflow_version < existing.workflow_version:
                raise RecoveryStoreUnavailable()
            generation = 1 if existing is None else existing.generation + 1
            if generation > RECOVERY_MAX_SAFE_INTEGER:
                raise RecoveryStoreUnavailable()
            record = PriorityCandidateRecoveryRecord(
                scope=scope,
                workflow_version=workflow_version,
                authority_expires_at=authority_expires_at,
                enqueued_at=(
                    authoritative_now if existing is None else existing.enqueued_at
                ),
                updated_at=authoritative_now,
                attempt_count=0,
                generation=generation,
            )
            prepared = _encode_record(self._hmac_secret, record)
            keys = self._scope_keys(scope)
            result = self._command(
                [
                    "EVAL",
                    _ENQUEUE_SCRIPT,
                    5,
                    keys["record"],
                    keys["lease"],
                    keys["due"],
                    keys["expiry"],
                    keys["user"],
                    f"{_KEY_PREFIX}record:",
                    f"{_KEY_PREFIX}lease:",
                    expected_raw if expected_raw is not None else _MISSING_SENTINEL,
                    prepared,
                    keys["member"],
                    authority_expires_at,
                    authoritative_now,
                    RECOVERY_MAX_MAILBOX_RECORDS,
                    RECOVERY_MAX_USER_RECORDS,
                    RECOVERY_MAX_SAFE_INTEGER,
                    _MISSING_SENTINEL,
                    _CONFLICT_SENTINEL,
                    _EXPIRED_SENTINEL,
                    _MAILBOX_CAPACITY_SENTINEL,
                    _INVALID_SENTINEL,
                    _USER_CAPACITY_SENTINEL,
                    RECOVERY_INDEX_TTL_MILLISECONDS,
                ]
            )
            if result == _CONFLICT_SENTINEL:
                continue
            if result == _EXPIRED_SENTINEL:
                return RecoveryEnqueueResult.EXPIRED
            if result == _MAILBOX_CAPACITY_SENTINEL:
                raise RecoveryCapacityExceeded("mailbox")
            if result == _USER_CAPACITY_SENTINEL:
                raise RecoveryCapacityExceeded("user")
            if result == _INVALID_SENTINEL or type(result) is not int or type(result) is bool:
                raise RecoveryStoreUnavailable()
            if result == 1:
                return RecoveryEnqueueResult.QUEUED
            if result == 2:
                return RecoveryEnqueueResult.UPDATED
            raise RecoveryStoreUnavailable()
        raise RecoveryStoreUnavailable()

    def cancel(self, scope: PriorityCandidateRecoveryScope) -> bool:
        if not isinstance(scope, PriorityCandidateRecoveryScope):
            raise ValueError("invalid Priority candidate recovery cancel")
        for _attempt in range(2):
            _record, expected_raw, _now = self._read_raw(scope)
            keys = self._scope_keys(scope)
            result = self._command(
                [
                    "EVAL",
                    _CANCEL_SCRIPT,
                    5,
                    keys["record"],
                    keys["lease"],
                    keys["due"],
                    keys["expiry"],
                    keys["user"],
                    keys["member"],
                    expected_raw if expected_raw is not None else _MISSING_SENTINEL,
                    _MISSING_SENTINEL,
                ]
            )
            if result == 2:
                continue
            if result == -1 or type(result) is not int or type(result) is bool:
                raise RecoveryStoreUnavailable()
            if result in {0, 1}:
                return result == 1
            raise RecoveryStoreUnavailable()
        raise RecoveryStoreUnavailable()

    def claim_due(
        self,
        mailbox_scope: PriorityCandidateRecoveryMailboxScope,
        *,
        limit: int = RECOVERY_MAX_CLAIM_RECORDS,
    ) -> tuple[PriorityCandidateRecoveryClaim, ...]:
        if (
            not isinstance(mailbox_scope, PriorityCandidateRecoveryMailboxScope)
            or type(limit) is not int
            or not 1 <= limit <= RECOVERY_MAX_CLAIM_RECORDS
        ):
            raise ValueError("invalid Priority candidate recovery claim")
        mailbox_keys = self._mailbox_keys(mailbox_scope)
        for _attempt in range(2):
            peeked = self._command(
                [
                    "EVAL",
                    _PEEK_DUE_SCRIPT,
                    3,
                    mailbox_keys["due"],
                    mailbox_keys["expiry"],
                    mailbox_keys["user"],
                    f"{_KEY_PREFIX}record:",
                    f"{_KEY_PREFIX}lease:",
                    limit,
                    RECOVERY_MAX_MAILBOX_RECORDS,
                    _INVALID_SENTINEL,
                ]
            )
            if type(peeked) is not list or not peeked:
                raise RecoveryStoreUnavailable()
            if peeked == [_INVALID_SENTINEL]:
                raise RecoveryStoreUnavailable()
            peeked_at = _safe_redis_integer(peeked[0])
            if peeked_at is None or (len(peeked) - 1) % 4 != 0:
                raise RecoveryStoreUnavailable()
            count = (len(peeked) - 1) // 4
            if count == 0:
                return ()
            if count > limit:
                raise RecoveryStoreUnavailable()
            prepared: list[tuple[str, str, int, int, PriorityCandidateRecoveryRecord, str]] = []
            for index in range(count):
                member, raw, due_value, expiry_value = peeked[1 + index * 4 : 5 + index * 4]
                due = _safe_redis_integer(due_value)
                expiry = _safe_redis_integer(expiry_value)
                if (
                    type(member) is not str
                    or _HEX_DIGEST_RE.fullmatch(member) is None
                    or type(raw) is not str
                    or due is None
                    or expiry is None
                    or due > peeked_at
                ):
                    raise RecoveryStoreUnavailable()
                record = _decode_record(
                    raw,
                    secret=self._hmac_secret,
                    expected_mailbox_scope=mailbox_scope,
                    expected_identity_digest=member,
                )
                if record is None or record.authority_expires_at != expiry:
                    raise RecoveryStoreUnavailable()
                prepared.append((member, raw, due, expiry, record, secrets.token_hex(32)))
            keys: list[object] = [
                mailbox_keys["due"],
                mailbox_keys["expiry"],
                mailbox_keys["user"],
            ]
            args: list[object] = [count, _INVALID_SENTINEL, _CONFLICT_SENTINEL]
            for member, raw, due, expiry, record, token in prepared:
                scope_keys = self._scope_keys(record.scope)
                keys.extend((scope_keys["record"], scope_keys["lease"]))
                args.extend((member, raw, due, expiry, token))
            args.append(RECOVERY_LEASE_TTL_MILLISECONDS)
            claimed = self._command(
                ["EVAL", _CLAIM_SCRIPT, len(keys), *keys, *args]
            )
            if claimed == [_CONFLICT_SENTINEL]:
                continue
            if type(claimed) is not list or len(claimed) != count + 1:
                raise RecoveryStoreUnavailable()
            claimed_at = _safe_redis_integer(claimed[0])
            lease_expiries = tuple(
                _safe_redis_integer(value) for value in claimed[1:]
            )
            if claimed_at is None or any(value is None for value in lease_expiries):
                raise RecoveryStoreUnavailable()
            return tuple(
                PriorityCandidateRecoveryClaim(
                    record=record,
                    identity_digest=member,
                    lease_token=token,
                    claimed_at=claimed_at,
                    lease_expires_at=lease_expiry,
                    raw_record=raw,
                )
                for (member, raw, _due, _expiry, record, token), lease_expiry in zip(
                    prepared, lease_expiries, strict=True
                )
            )
        return ()

    def ack(
        self, claim: PriorityCandidateRecoveryClaim
    ) -> RecoveryAckResult:
        if not isinstance(claim, PriorityCandidateRecoveryClaim):
            raise ValueError("invalid Priority candidate recovery acknowledgement")
        keys = self._scope_keys(claim.record.scope)
        result = self._command(
            [
                "EVAL",
                _ACK_SCRIPT,
                5,
                keys["record"],
                keys["lease"],
                keys["due"],
                keys["expiry"],
                keys["user"],
                claim.raw_record,
                claim.lease_token,
                claim.identity_digest,
                claim.record.generation,
            ]
        )
        if result == 1:
            return RecoveryAckResult.COMPLETED
        if result == 0:
            return RecoveryAckResult.CLAIM_LOST
        raise RecoveryStoreUnavailable()

    def retry(
        self, claim: PriorityCandidateRecoveryClaim
    ) -> RecoveryRetryResult:
        if not isinstance(claim, PriorityCandidateRecoveryClaim):
            raise ValueError("invalid Priority candidate recovery retry")
        keys = self._scope_keys(claim.record.scope)
        timing = self._command(
            [
                "EVAL",
                _READ_CLAIM_TIME_SCRIPT,
                5,
                keys["record"],
                keys["lease"],
                keys["due"],
                keys["expiry"],
                keys["user"],
                claim.raw_record,
                claim.lease_token,
                claim.record.generation,
                _CLAIM_LOST_SENTINEL,
                _INVALID_SENTINEL,
                claim.identity_digest,
            ]
        )
        if timing == [_CLAIM_LOST_SENTINEL]:
            return RecoveryRetryResult.CLAIM_LOST
        if timing == [_INVALID_SENTINEL] or type(timing) is not list or len(timing) != 2:
            raise RecoveryStoreUnavailable()
        now = _safe_redis_integer(timing[0])
        expiry = _safe_redis_integer(timing[1])
        if now is None or expiry is None or expiry != claim.record.authority_expires_at:
            raise RecoveryStoreUnavailable()
        next_attempt = claim.record.attempt_count + 1
        if next_attempt >= RECOVERY_MAX_ATTEMPTS:
            ack = self.ack(claim)
            return (
                RecoveryRetryResult.ATTEMPTS_EXHAUSTED
                if ack is RecoveryAckResult.COMPLETED
                else RecoveryRetryResult.CLAIM_LOST
            )
        next_due = now + _retry_delay_milliseconds(next_attempt)
        if next_due >= expiry:
            ack = self.ack(claim)
            return (
                RecoveryRetryResult.AUTHORITY_EXPIRED
                if ack is RecoveryAckResult.COMPLETED
                else RecoveryRetryResult.CLAIM_LOST
            )
        prepared_record = replace(
            claim.record,
            updated_at=now,
            attempt_count=next_attempt,
        )
        prepared = _encode_record(self._hmac_secret, prepared_record)
        result = self._command(
            [
                "EVAL",
                _RETRY_SCRIPT,
                5,
                keys["record"],
                keys["lease"],
                keys["due"],
                keys["expiry"],
                keys["user"],
                claim.raw_record,
                claim.lease_token,
                prepared,
                claim.identity_digest,
                next_due,
                claim.record.generation,
            ]
        )
        if result == 1:
            return RecoveryRetryResult.RETRIED
        if result == 0:
            return RecoveryRetryResult.CLAIM_LOST
        raise RecoveryStoreUnavailable()


def build_runtime_recovery_store(
    *, hmac_secret: str
) -> PriorityCandidateRecoveryStore:
    from api.auth.session_store import build_kv_command_transport

    return PriorityCandidateRecoveryStore(
        build_kv_command_transport(),
        hmac_secret=hmac_secret,
    )
