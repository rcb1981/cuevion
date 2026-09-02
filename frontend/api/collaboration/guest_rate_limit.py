from __future__ import annotations

if __name__ != "api.collaboration.guest_rate_limit":
    raise ImportError(
        "Collaboration helpers must be imported as "
        "api.collaboration.guest_rate_limit"
    )

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass

from .guest_session import GUEST_CSRF_HMAC_ENV, is_v2_guest_bearer
from .redis_store import V2_KEY_PREFIX, _v2_eval


RATE_LIMIT_HMAC_ENV = "CUEVION_COLLAB_V2_RATE_LIMIT_HMAC_KEY"
RATE_LIMIT_EXCHANGE = "exchange"
RATE_LIMIT_BOOTSTRAP = "bootstrap"
RATE_LIMIT_READ = "read"
RATE_LIMIT_REPLY = "reply"
RATE_LIMIT_LOGOUT = "logout"
RATE_LIMIT_CLASSES = frozenset(
    {
        RATE_LIMIT_EXCHANGE,
        RATE_LIMIT_BOOTSTRAP,
        RATE_LIMIT_READ,
        RATE_LIMIT_REPLY,
        RATE_LIMIT_LOGOUT,
    }
)
_DISTINCT_SECRET_NAMES = (
    GUEST_CSRF_HMAC_ENV,
    "CUEVION_COLLAB_V2_OWNER_CSRF_KEY",
    "CUEVION_COLLAB_V2_OWNER_CSRF_KEY_PREVIOUS",
    "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY",
    "CUEVION_COLLAB_INDEX_HMAC_KEY",
    "CUEVION_COLLAB_INDEX_HMAC_KEY_PREVIOUS",
)
RATE_LIMIT_CONFIGURATION_NAMES = (
    RATE_LIMIT_HMAC_ENV,
    *_DISTINCT_SECRET_NAMES,
)
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CANONICAL_UINT_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_RATE_LIMIT_KEY_DOMAIN = b"cuevion/collaboration-v2/guest-rate-limit/v1\x00"
_CONFIGURATION_SENTINEL = object()
WINDOW_SECONDS = 60
_MAX_RECORD_BYTES = 160


@dataclass(frozen=True, slots=True)
class GuestRateLimitPolicy:
    name: str
    global_limit: int
    scoped_limit: int


_POLICIES = {
    RATE_LIMIT_EXCHANGE: GuestRateLimitPolicy(RATE_LIMIT_EXCHANGE, 120, 6),
    RATE_LIMIT_BOOTSTRAP: GuestRateLimitPolicy(RATE_LIMIT_BOOTSTRAP, 600, 12),
    RATE_LIMIT_READ: GuestRateLimitPolicy(RATE_LIMIT_READ, 3000, 240),
    RATE_LIMIT_REPLY: GuestRateLimitPolicy(RATE_LIMIT_REPLY, 600, 30),
    RATE_LIMIT_LOGOUT: GuestRateLimitPolicy(RATE_LIMIT_LOGOUT, 600, 12),
}


class GuestRateLimitConfiguration:
    __slots__ = ("_sentinel", "_hmac_key")

    def __new__(cls, *_args: object, **_kwargs: object):
        raise TypeError("GuestRateLimitConfiguration is parser-minted")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("GuestRateLimitConfiguration is immutable")

    def __delattr__(self, _name: str) -> None:
        raise TypeError("GuestRateLimitConfiguration is immutable")

    def __repr__(self) -> str:
        return "<GuestRateLimitConfiguration>"

    __str__ = __repr__

    def __reduce__(self) -> object:
        raise TypeError("GuestRateLimitConfiguration is not serializable")

    def __reduce_ex__(self, _protocol: object) -> object:
        raise TypeError("GuestRateLimitConfiguration is not serializable")


@dataclass(frozen=True, slots=True)
class GuestRateLimitDecision:
    status: str
    retry_after_seconds: int | None = None


def guest_rate_limit_policy(rate_class: object) -> GuestRateLimitPolicy | None:
    return _POLICIES.get(rate_class) if type(rate_class) is str else None


def _decode_secret(value: object) -> bytes | None:
    if type(value) is not str or not value or len(value) > 1024:
        return None
    if _BASE64URL_RE.fullmatch(value) is None:
        return None
    padding = (-len(value)) % 4
    if padding == 3:
        return None
    try:
        decoded = base64.urlsafe_b64decode(
            (value + ("=" * padding)).encode("ascii")
        )
    except (UnicodeEncodeError, ValueError):
        return None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    return (
        decoded
        if len(decoded) >= 32 and hmac.compare_digest(canonical, value)
        else None
    )


def parse_guest_rate_limit_configuration(
    trusted_configuration: object,
) -> GuestRateLimitConfiguration:
    if type(trusted_configuration) is not dict:
        raise ValueError("invalid guest rate-limit configuration")
    keys = tuple(dict.__iter__(trusted_configuration))
    if (
        any(type(key) is not str for key in keys)
        or RATE_LIMIT_HMAC_ENV not in keys
        or not set(keys).issubset(RATE_LIMIT_CONFIGURATION_NAMES)
    ):
        raise ValueError("invalid guest rate-limit configuration")
    snapshot = dict.copy(trusted_configuration)
    rate_key = _decode_secret(dict.__getitem__(snapshot, RATE_LIMIT_HMAC_ENV))
    if rate_key is None:
        raise ValueError("invalid guest rate-limit configuration")
    for name in _DISTINCT_SECRET_NAMES:
        if name not in snapshot:
            continue
        other_key = _decode_secret(dict.__getitem__(snapshot, name))
        if other_key is None or hmac.compare_digest(rate_key, other_key):
            raise ValueError("invalid guest rate-limit configuration")
    configuration = object.__new__(GuestRateLimitConfiguration)
    object.__setattr__(configuration, "_sentinel", _CONFIGURATION_SENTINEL)
    object.__setattr__(configuration, "_hmac_key", bytes(bytearray(rate_key)))
    return configuration


def _require_configuration(value: object) -> GuestRateLimitConfiguration:
    if type(value) is not GuestRateLimitConfiguration:
        raise ValueError("invalid guest rate-limit configuration")
    try:
        sentinel = object.__getattribute__(value, "_sentinel")
        hmac_key = object.__getattribute__(value, "_hmac_key")
    except Exception:
        raise ValueError("invalid guest rate-limit configuration") from None
    if (
        sentinel is not _CONFIGURATION_SENTINEL
        or type(hmac_key) is not bytes
        or len(hmac_key) < 32
    ):
        raise ValueError("invalid guest rate-limit configuration")
    return value


def _rate_digest(key: bytes, rate_class: str, scope: str) -> str:
    payload = json.dumps(
        {"operation": rate_class, "scope": scope},
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hmac.new(key, _RATE_LIMIT_KEY_DOMAIN + payload, hashlib.sha256).hexdigest()


def build_guest_rate_limit_keys(
    raw_bearer: object,
    rate_class: object,
    configuration: object,
) -> tuple[str, str] | None:
    policy = guest_rate_limit_policy(rate_class)
    if policy is None or not is_v2_guest_bearer(raw_bearer):
        return None
    try:
        parsed = _require_configuration(configuration)
        global_digest = _rate_digest(parsed._hmac_key, policy.name, "global")
        scoped_digest = _rate_digest(
            parsed._hmac_key,
            policy.name,
            "bearer:" + raw_bearer,
        )
    except Exception:
        return None
    return (
        f"{V2_KEY_PREFIX}:guest-rate:{policy.name}:global:{global_digest}",
        f"{V2_KEY_PREFIX}:guest-rate:{policy.name}:scope:{scoped_digest}",
    )


_GUEST_RATE_LIMIT_LUA = r"""
if #KEYS ~= 2 or #ARGV ~= 4 then
  return cjson.encode({status='malformed'})
end
local function canonicalUInt(value)
  if type(value) ~= 'string'
    or (value ~= '0' and not string.match(value, '^[1-9][0-9]*$')) then
    return nil
  end
  local parsed = tonumber(value)
  if not parsed or parsed < 0 or parsed > 9007199254740991 or parsed % 1 ~= 0 then
    return nil
  end
  if string.format('%.0f', parsed) ~= value then return nil end
  return parsed
end
local function keyCount(value)
  local count = 0
  for _, _ in pairs(value) do count = count + 1 end
  return count
end
local globalLimit = canonicalUInt(ARGV[1])
local scopedLimit = canonicalUInt(ARGV[2])
local windowSeconds = canonicalUInt(ARGV[3])
local maximumBytes = canonicalUInt(ARGV[4])
if not globalLimit or globalLimit < 1 or not scopedLimit or scopedLimit < 1
  or not windowSeconds or windowSeconds < 1 or windowSeconds > 60
  or not maximumBytes or maximumBytes < 32 or maximumBytes > 1024 then
  return cjson.encode({status='malformed'})
end
local timeOk, serverTime = pcall(redis.call, 'TIME')
if not timeOk or type(serverTime) ~= 'table' or #serverTime ~= 2 then
  return cjson.encode({status='unavailable'})
end
local seconds = canonicalUInt(serverTime[1])
if not seconds then return cjson.encode({status='unavailable'}) end
local window = math.floor(seconds / windowSeconds)
local retryAfter = windowSeconds - (seconds % windowSeconds)
local ttlMs = (retryAfter * 1000) + 1000
local function loadState(key)
  local readOk, raw = pcall(redis.call, 'GET', key)
  if not readOk then return nil, 'unavailable' end
  if not raw then return 0, nil end
  if type(raw) ~= 'string' or #raw > maximumBytes then return nil, 'malformed' end
  local decodeOk, record = pcall(cjson.decode, raw)
  if not decodeOk or type(record) ~= 'table' or keyCount(record) ~= 3
    or record.v ~= '1' then return nil, 'malformed' end
  local storedWindow = canonicalUInt(record.window)
  local storedCount = canonicalUInt(record.count)
  if not storedWindow or not storedCount or storedCount < 1
    or storedWindow > window or storedWindow + 1 < window then
    return nil, 'malformed'
  end
  local ttlOk, currentTtl = pcall(redis.call, 'PTTL', key)
  if not ttlOk or type(currentTtl) ~= 'number' or currentTtl <= 0
    or currentTtl > (windowSeconds * 1000) + 1000 then
    return nil, 'malformed'
  end
  if storedWindow ~= window then return 0, nil end
  return storedCount, nil
end
local globalCount, globalError = loadState(KEYS[1])
if globalError then return cjson.encode({status=globalError}) end
local scopedCount, scopedError = loadState(KEYS[2])
if scopedError then return cjson.encode({status=scopedError}) end
if globalCount >= globalLimit or scopedCount >= scopedLimit then
  return cjson.encode({status='limited', retryAfter=string.format('%.0f', retryAfter)})
end
local globalRecord = cjson.encode({v='1', window=string.format('%.0f', window), count=string.format('%.0f', globalCount + 1)})
local scopedRecord = cjson.encode({v='1', window=string.format('%.0f', window), count=string.format('%.0f', scopedCount + 1)})
if #globalRecord > maximumBytes or #scopedRecord > maximumBytes then
  return cjson.encode({status='malformed'})
end
local globalWriteOk = pcall(redis.call, 'SET', KEYS[1], globalRecord, 'PX', ttlMs)
if not globalWriteOk then return cjson.encode({status='unavailable'}) end
local scopedWriteOk = pcall(redis.call, 'SET', KEYS[2], scopedRecord, 'PX', ttlMs)
if not scopedWriteOk then return cjson.encode({status='unavailable'}) end
return cjson.encode({status='allowed'})
""".strip()


def consume_guest_rate_limit(
    raw_bearer: object,
    rate_class: object,
    configuration: object,
    *,
    command_transport=None,
) -> GuestRateLimitDecision:
    policy = guest_rate_limit_policy(rate_class)
    keys = build_guest_rate_limit_keys(raw_bearer, rate_class, configuration)
    if policy is None or keys is None:
        return GuestRateLimitDecision("unavailable")
    result = _v2_eval(
        [
            "EVAL",
            _GUEST_RATE_LIMIT_LUA,
            2,
            keys[0],
            keys[1],
            str(policy.global_limit),
            str(policy.scoped_limit),
            str(WINDOW_SECONDS),
            str(_MAX_RECORD_BYTES),
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
        return GuestRateLimitDecision("allowed")
    if result.get("status") == "limited":
        retry_after = result.get("retryAfter")
        if type(retry_after) is str and _CANONICAL_UINT_RE.fullmatch(retry_after):
            parsed_retry = int(retry_after)
            if 1 <= parsed_retry <= 60:
                return GuestRateLimitDecision("limited", parsed_retry)
    return GuestRateLimitDecision("unavailable")


__all__ = (
    "GuestRateLimitConfiguration",
    "GuestRateLimitDecision",
    "GuestRateLimitPolicy",
    "RATE_LIMIT_BOOTSTRAP",
    "RATE_LIMIT_CLASSES",
    "RATE_LIMIT_CONFIGURATION_NAMES",
    "RATE_LIMIT_EXCHANGE",
    "RATE_LIMIT_HMAC_ENV",
    "RATE_LIMIT_LOGOUT",
    "RATE_LIMIT_READ",
    "RATE_LIMIT_REPLY",
    "WINDOW_SECONDS",
    "build_guest_rate_limit_keys",
    "consume_guest_rate_limit",
    "guest_rate_limit_policy",
    "parse_guest_rate_limit_configuration",
)
