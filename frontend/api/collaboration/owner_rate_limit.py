from __future__ import annotations

if __name__ != "api.collaboration.owner_rate_limit":
    raise ImportError(
        "Collaboration helpers must be imported as "
        "api.collaboration.owner_rate_limit"
    )

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from . import owner_request_security
from .redis_store import V2_KEY_PREFIX, _v2_eval


RATE_LIMIT_HMAC_ENV = "CUEVION_COLLAB_V2_RATE_LIMIT_HMAC_KEY"
RATE_LIMIT_BOOTSTRAP = "bootstrap"
RATE_LIMIT_READ = "read"
RATE_LIMIT_WRITE = "write"
RATE_LIMIT_CLASSES = frozenset(
    {RATE_LIMIT_BOOTSTRAP, RATE_LIMIT_READ, RATE_LIMIT_WRITE}
)

_DISTINCT_BASE64URL_SECRET_NAMES = (
    "CUEVION_COLLAB_V2_OWNER_CSRF_KEY",
    "CUEVION_COLLAB_V2_OWNER_CSRF_KEY_PREVIOUS",
    "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY",
    "CUEVION_COLLAB_INDEX_HMAC_KEY",
    "CUEVION_COLLAB_INDEX_HMAC_KEY_PREVIOUS",
)
_DISTINCT_PADDED_BASE64URL_SECRET_NAMES = ("MAILBOX_SECRET_ENCRYPTION_KEY",)
_DISTINCT_RAW_SECRET_NAMES = (
    "CUEVION_AUTH_SESSION_SECRET",
)
RATE_LIMIT_CONFIGURATION_NAMES = (
    RATE_LIMIT_HMAC_ENV,
    *_DISTINCT_BASE64URL_SECRET_NAMES,
    *_DISTINCT_PADDED_BASE64URL_SECRET_NAMES,
    *_DISTINCT_RAW_SECRET_NAMES,
)

_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CANONICAL_UINT_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_RATE_LIMIT_KEY_DOMAIN = "cuevion-collaboration-v2/owner-rate-limit-key/v1"
_CONFIGURATION_SENTINEL = object()
_MAX_RATE_LIMIT_RECORD_BYTES = 128
STATE_EXPIRY_GRACE_MS = 1000
_OWNER_RATE_LIMIT_EARLY_EXPIRY_TOLERANCE_MS = 100
_OWNER_RATE_LIMIT_TTL_OBSERVATION_ALLOWANCE_MS = 100


@dataclass(frozen=True, slots=True)
class OwnerRateLimitPolicy:
    name: str
    emission_interval_microseconds: int
    burst: int


_POLICIES = {
    RATE_LIMIT_BOOTSTRAP: OwnerRateLimitPolicy(
        RATE_LIMIT_BOOTSTRAP,
        5_000_000,
        4,
    ),
    RATE_LIMIT_READ: OwnerRateLimitPolicy(
        RATE_LIMIT_READ,
        500_000,
        30,
    ),
    RATE_LIMIT_WRITE: OwnerRateLimitPolicy(
        RATE_LIMIT_WRITE,
        2_000_000,
        10,
    ),
}


class OwnerRateLimitConfiguration:
    """Opaque immutable rate-limit key configuration."""

    __slots__ = ("_sentinel", "_hmac_key")

    def __new__(cls, *_args: object, **_kwargs: object):
        raise TypeError("OwnerRateLimitConfiguration is parser-minted")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("OwnerRateLimitConfiguration is immutable")

    def __delattr__(self, _name: str) -> None:
        raise TypeError("OwnerRateLimitConfiguration is immutable")

    def __repr__(self) -> str:
        return "<OwnerRateLimitConfiguration>"

    __str__ = __repr__

    def __reduce__(self) -> object:
        raise TypeError("OwnerRateLimitConfiguration is not serializable")

    def __reduce_ex__(self, _protocol: object) -> object:
        raise TypeError("OwnerRateLimitConfiguration is not serializable")


@dataclass(frozen=True, slots=True)
class OwnerRateLimitDecision:
    status: str
    retry_after_seconds: int | None = None


def owner_rate_limit_policy(rate_class: object) -> OwnerRateLimitPolicy | None:
    return _POLICIES.get(rate_class) if type(rate_class) is str else None


def _decode_secret(value: object, *, allow_padding: bool = False) -> bytes | None:
    if (
        type(value) is not str
        or not value
        or len(value) > 1024
    ):
        return None
    unpadded = value.rstrip("=") if allow_padding else value
    supplied_padding = len(value) - len(unpadded)
    required_padding = (-len(unpadded)) % 4
    if (
        _BASE64URL_RE.fullmatch(unpadded) is None
        or required_padding == 3
        or supplied_padding not in ({0, required_padding} if allow_padding else {0})
    ):
        return None
    try:
        decoded = base64.urlsafe_b64decode(
            (unpadded + ("=" * required_padding)).encode("ascii")
        )
    except (UnicodeEncodeError, ValueError):
        return None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    return (
        decoded
        if len(decoded) >= 32 and hmac.compare_digest(canonical, unpadded)
        else None
    )


def parse_owner_rate_limit_configuration(
    trusted_configuration: object,
) -> OwnerRateLimitConfiguration:
    if type(trusted_configuration) is not dict:
        raise ValueError("invalid owner rate-limit configuration")
    keys = tuple(dict.__iter__(trusted_configuration))
    if (
        any(type(key) is not str for key in keys)
        or RATE_LIMIT_HMAC_ENV not in keys
        or not set(keys).issubset(RATE_LIMIT_CONFIGURATION_NAMES)
    ):
        raise ValueError("invalid owner rate-limit configuration")
    snapshot = dict.copy(trusted_configuration)
    encoded_rate_key = dict.__getitem__(snapshot, RATE_LIMIT_HMAC_ENV)
    rate_key = _decode_secret(encoded_rate_key)
    if rate_key is None:
        raise ValueError("invalid owner rate-limit configuration")

    for name in _DISTINCT_BASE64URL_SECRET_NAMES:
        if name not in snapshot:
            continue
        other_key = _decode_secret(dict.__getitem__(snapshot, name))
        if other_key is None or hmac.compare_digest(rate_key, other_key):
            raise ValueError("invalid owner rate-limit configuration")

    for name in _DISTINCT_PADDED_BASE64URL_SECRET_NAMES:
        if name not in snapshot:
            continue
        other_key = _decode_secret(
            dict.__getitem__(snapshot, name),
            allow_padding=True,
        )
        if (
            other_key is None
            or len(other_key) != 32
            or hmac.compare_digest(rate_key, other_key)
        ):
            raise ValueError("invalid owner rate-limit configuration")

    for name in _DISTINCT_RAW_SECRET_NAMES:
        if name not in snapshot:
            continue
        raw_secret = dict.__getitem__(snapshot, name)
        if type(raw_secret) is not str:
            raise ValueError("invalid owner rate-limit configuration")
        try:
            raw_bytes = raw_secret.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise ValueError("invalid owner rate-limit configuration") from None
        if (
            raw_secret != raw_secret.strip()
            or not 32 <= len(raw_bytes) <= 4096
            or hmac.compare_digest(str(encoded_rate_key), raw_secret)
            or hmac.compare_digest(rate_key, raw_bytes)
        ):
            raise ValueError("invalid owner rate-limit configuration")

    configuration = object.__new__(OwnerRateLimitConfiguration)
    object.__setattr__(configuration, "_sentinel", _CONFIGURATION_SENTINEL)
    object.__setattr__(configuration, "_hmac_key", bytes(bytearray(rate_key)))
    return configuration


def _require_configuration(value: object) -> OwnerRateLimitConfiguration:
    if type(value) is not OwnerRateLimitConfiguration:
        raise ValueError("invalid owner rate-limit configuration")
    try:
        sentinel = object.__getattribute__(value, "_sentinel")
        hmac_key = object.__getattribute__(value, "_hmac_key")
    except Exception:
        raise ValueError("invalid owner rate-limit configuration") from None
    if (
        sentinel is not _CONFIGURATION_SENTINEL
        or type(hmac_key) is not bytes
        or len(hmac_key) < 32
    ):
        raise ValueError("invalid owner rate-limit configuration")
    return value


def build_owner_rate_limit_key(
    context: object,
    rate_class: object,
    configuration: object,
) -> str | None:
    policy = owner_rate_limit_policy(rate_class)
    try:
        parsed = _require_configuration(configuration)
        valid_context = owner_request_security._is_owner_context(context)
    except Exception:
        return None
    if policy is None or not valid_context:
        return None
    try:
        identity = json.dumps(
            {
                "domain": _RATE_LIMIT_KEY_DOMAIN,
                "ownerEmail": context.owner_email,
                "workspaceId": context.workspace_id,
            },
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        digest = hmac.new(parsed._hmac_key, identity, hashlib.sha256).hexdigest()
    except Exception:
        return None
    return f"{V2_KEY_PREFIX}:owner-rate:{policy.name}:{digest}"


_OWNER_RATE_LIMIT_LUA = (
    r"""
if #KEYS ~= 1 or #ARGV ~= 3 then
  return cjson.encode({status='malformed'})
end
local MAX_SAFE = 9007199254740991
local EARLY_EXPIRY_TOLERANCE_MS = """
    + str(_OWNER_RATE_LIMIT_EARLY_EXPIRY_TOLERANCE_MS)
    + r"""
local STATE_EXPIRY_GRACE_MS = """
    + str(STATE_EXPIRY_GRACE_MS)
    + r"""
local TTL_OBSERVATION_ALLOWANCE_MS = """
    + str(_OWNER_RATE_LIMIT_TTL_OBSERVATION_ALLOWANCE_MS)
    + r"""
local function keyCount(value)
  local count = 0
  for _, _ in pairs(value) do count = count + 1 end
  return count
end
local function canonicalUInt(value)
  if type(value) ~= 'string'
    or (value ~= '0' and not string.match(value, '^[1-9][0-9]*$')) then
    return nil
  end
  local parsed = tonumber(value)
  if not parsed or parsed < 0 or parsed > MAX_SAFE or parsed % 1 ~= 0 then
    return nil
  end
  if string.format('%.0f', parsed) ~= value then return nil end
  return parsed
end
local function uintText(value)
  return string.format('%.0f', value)
end
local interval = canonicalUInt(ARGV[1])
local burst = canonicalUInt(ARGV[2])
local maxRecordBytes = canonicalUInt(ARGV[3])
if not interval or interval < 1000 or not burst or burst < 1
  or not maxRecordBytes or maxRecordBytes < 32 or maxRecordBytes > 1024 then
  return cjson.encode({status='malformed'})
end
local timeOk, serverTime = pcall(redis.call, 'TIME')
if not timeOk or type(serverTime) ~= 'table' or #serverTime ~= 2 then
  return cjson.encode({status='unavailable'})
end
local seconds = canonicalUInt(serverTime[1])
local micros = canonicalUInt(serverTime[2])
if not seconds or not micros or micros > 999999 then
  return cjson.encode({status='unavailable'})
end
local now = (seconds * 1000000) + micros
if now > MAX_SAFE then return cjson.encode({status='unavailable'}) end
local maxDebt = interval * burst
if maxDebt > MAX_SAFE then return cjson.encode({status='malformed'}) end

local readOk, raw = pcall(redis.call, 'GET', KEYS[1])
if not readOk then return cjson.encode({status='unavailable'}) end
local tat = now
if raw then
  if type(raw) ~= 'string' or #raw > maxRecordBytes then
    return cjson.encode({status='malformed'})
  end
  local decodeOk, record = pcall(cjson.decode, raw)
  if not decodeOk or type(record) ~= 'table' or keyCount(record) ~= 2
    or record.v ~= '1' then
    return cjson.encode({status='malformed'})
  end
  local storedTat = canonicalUInt(record.tatUs)
  if not storedTat or storedTat > now + maxDebt then
    return cjson.encode({status='malformed'})
  end
  local canonicalA = '{"v":"1","tatUs":"' .. record.tatUs .. '"}'
  local canonicalB = '{"tatUs":"' .. record.tatUs .. '","v":"1"}'
  if raw ~= canonicalA and raw ~= canonicalB then
    return cjson.encode({status='malformed'})
  end
  local ttlOk, currentTtl = pcall(redis.call, 'PTTL', KEYS[1])
  local maxTtl = math.ceil(maxDebt / 1000)
    + STATE_EXPIRY_GRACE_MS
    + TTL_OBSERVATION_ALLOWANCE_MS
  local stateTtl = math.max(1, math.ceil(math.max(0, storedTat - now) / 1000))
  local minimumStateTtl = math.max(
    1,
    stateTtl - EARLY_EXPIRY_TOLERANCE_MS
  )
  if not ttlOk or type(currentTtl) ~= 'number' or currentTtl <= 0
    or currentTtl > maxTtl or currentTtl < minimumStateTtl then
    return cjson.encode({status='malformed'})
  end
  tat = math.max(storedTat, now)
end

local proposedTat = tat + interval
if proposedTat > MAX_SAFE then return cjson.encode({status='unavailable'}) end
local allowAt = proposedTat - maxDebt
if allowAt > now then
  local retryAfter = math.max(1, math.ceil((allowAt - now) / 1000000))
  return cjson.encode({status='limited', retryAfter=uintText(retryAfter)})
end
local ttlMs = math.max(1, math.ceil((proposedTat - now) / 1000))
  + STATE_EXPIRY_GRACE_MS
local candidate = cjson.encode({v='1', tatUs=uintText(proposedTat)})
if #candidate > maxRecordBytes then return cjson.encode({status='malformed'}) end
local writeOk = pcall(redis.call, 'SET', KEYS[1], candidate, 'PX', ttlMs)
if not writeOk then return cjson.encode({status='unavailable'}) end
return cjson.encode({status='allowed'})
"""
)


def consume_owner_rate_limit(
    context: object,
    rate_class: object,
    configuration: object,
    *,
    command_transport=None,
) -> OwnerRateLimitDecision:
    policy = owner_rate_limit_policy(rate_class)
    key = build_owner_rate_limit_key(context, rate_class, configuration)
    if policy is None or key is None:
        return OwnerRateLimitDecision("unavailable")
    result = _v2_eval(
        [
            "EVAL",
            _OWNER_RATE_LIMIT_LUA,
            1,
            key,
            str(policy.emission_interval_microseconds),
            str(policy.burst),
            str(_MAX_RATE_LIMIT_RECORD_BYTES),
        ],
        command_transport,
        response_shapes={
            "allowed": set(),
            "limited": {"retryAfter"},
            "malformed": set(),
            "unavailable": set(),
        },
    )
    if result.get("status") == "allowed":
        return OwnerRateLimitDecision("allowed")
    if result.get("status") == "limited":
        retry_after = result.get("retryAfter")
        if (
            type(retry_after) is str
            and _CANONICAL_UINT_RE.fullmatch(retry_after) is not None
        ):
            parsed_retry = int(retry_after)
            if 1 <= parsed_retry <= 60:
                return OwnerRateLimitDecision("limited", parsed_retry)
    return OwnerRateLimitDecision("unavailable")


__all__ = (
    "OwnerRateLimitConfiguration",
    "OwnerRateLimitDecision",
    "OwnerRateLimitPolicy",
    "RATE_LIMIT_BOOTSTRAP",
    "RATE_LIMIT_CLASSES",
    "RATE_LIMIT_CONFIGURATION_NAMES",
    "RATE_LIMIT_HMAC_ENV",
    "RATE_LIMIT_READ",
    "RATE_LIMIT_WRITE",
    "STATE_EXPIRY_GRACE_MS",
    "build_owner_rate_limit_key",
    "consume_owner_rate_limit",
    "owner_rate_limit_policy",
    "parse_owner_rate_limit_configuration",
)
