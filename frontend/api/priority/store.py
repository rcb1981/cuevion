"""Tenant-scoped durable cache and idempotency primitives for Priority semantics."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from .event_reference import derive_priority_hmac_key
from .semantic_thresholds import evaluate_semantic_confidence
from .semantic_types import SemanticAssessment, SemanticState


RESULT_TTL_SECONDS = 30 * 24 * 60 * 60
NEGATIVE_TTL_SECONDS = 5 * 60
LEASE_TTL_SECONDS = 60
ATTEMPT_WINDOW_SECONDS = 24 * 60 * 60
MAX_ATTEMPTS_PER_WINDOW = 2
STORE_SCHEMA_VERSION = 1

_KEY_PREFIX = "cuevion:priority:semantic:v1:"
_SCOPE_HMAC_INFO = b"cuevion/priority/cache-scope/v1\x00"
_RECORD_HMAC_INFO = b"cuevion/priority/cache-record/v1\x00"
_LEASE_TOKEN_BYTES = 32
_HEX_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_NEGATIVE_CODES = frozenset(
    {
        "configuration_invalid",
        "provider_unavailable",
        "provider_timeout",
        "provider_rate_limited",
        "provider_response_invalid",
        "semantic_unavailable",
    }
)
_CURRENT_MISMATCH_SENTINEL = "__cuevion_priority_current_mismatch__"
_COMMIT_RESULT_SCRIPT = (
    "if redis.call('GET',KEYS[1])==ARGV[1] and "
    "redis.call('GET',KEYS[2])==ARGV[2] then "
    "redis.call('SET',KEYS[3],ARGV[3],'EX',ARGV[4]);"
    "redis.call('DEL',KEYS[1]);return 1 else return 0 end"
)
_RELEASE_LEASE_SCRIPT = (
    "if redis.call('GET',KEYS[1])==ARGV[1] then "
    "return redis.call('DEL',KEYS[1]) else return 0 end"
)
_COMMIT_NEGATIVE_SCRIPT = (
    "if redis.call('GET',KEYS[1])==ARGV[1] then "
    "redis.call('SET',KEYS[2],ARGV[2],'EX',ARGV[3]);"
    "redis.call('DEL',KEYS[1]);return 1 else return 0 end"
)
_GET_RESULT_IF_CURRENT_SCRIPT = (
    "if redis.call('GET',KEYS[1])~=ARGV[1] then return ARGV[2] end;"
    "return redis.call('GET',KEYS[2])"
)
_ATTEMPT_SCRIPT = (
    "local value=redis.call('GET',KEYS[1]);"
    "if not value then redis.call('SET',KEYS[1],'1','EX',ARGV[1]);return 1 end;"
    "local count=tonumber(value);"
    "if not count or count<1 then return -1 end;"
    "if count>=tonumber(ARGV[2]) then return 0 end;"
    "return redis.call('INCR',KEYS[1])"
)
_MARK_CURRENT_SCRIPT = (
    "local existing=redis.call('GET',KEYS[1]);"
    "if existing then "
    "local separator=string.find(existing,':',1,true);"
    "if not separator then return -1 end;"
    "local old_time=tonumber(string.sub(existing,1,separator-1));"
    "local new_time=tonumber(ARGV[1]);"
    "if not old_time or not new_time then return -1 end;"
    "if old_time>new_time then return 0 end;"
    "if old_time==new_time and existing~=ARGV[2] then return 0 end;"
    "end;"
    "redis.call('SET',KEYS[1],ARGV[2],'EX',ARGV[3]);return 1"
)


class SemanticStoreUnavailable(Exception):
    """Value-free failure for unavailable or malformed durable storage."""

    __slots__ = ()

    def __str__(self) -> str:
        return "semantic assessment storage is unavailable"


@dataclass(frozen=True, slots=True)
class SemanticCacheScope:
    workspace_id: str
    user_id: str
    mailbox_id: str
    provider: str
    conversation_id: str
    latest_turn_id: str
    semantic_version: str
    model_version: str

    def canonical_bytes(self) -> bytes:
        values = (
            self.workspace_id,
            self.user_id,
            self.mailbox_id,
            self.provider,
            self.conversation_id,
            self.latest_turn_id,
            self.semantic_version,
            self.model_version,
        )
        if any(
            type(value) is not str
            or not value
            or value != value.strip()
            or len(value) > 2_048
            or "\x00" in value
            for value in values
        ):
            raise ValueError("invalid semantic cache scope")
        return "\x00".join(values).encode("utf-8", errors="strict")


@dataclass(frozen=True, slots=True)
class CachedSemanticAssessment:
    assessment: SemanticAssessment
    effective_state: SemanticState
    assessed_at: int
    input_hash: str


CommandTransport = Callable[[list[object]], dict[str, object]]


def derive_scope_digest(secret: str, scope: SemanticCacheScope) -> str:
    key = derive_priority_hmac_key(secret, _SCOPE_HMAC_INFO)
    return hmac.new(key, scope.canonical_bytes(), hashlib.sha256).hexdigest()


def _derive_record_digest(secret: str, label: bytes, value: bytes) -> str:
    key = derive_priority_hmac_key(secret, _RECORD_HMAC_INFO)
    return hmac.new(key, label + b"\x00" + value, hashlib.sha256).hexdigest()


class SemanticAssessmentStore:
    """Strict Redis-command store with no raw provider identity in its keys."""

    __slots__ = ("_transport", "_hmac_secret")

    def __init__(self, command_transport: CommandTransport, *, hmac_secret: str) -> None:
        if not callable(command_transport):
            raise ValueError("invalid semantic command transport")
        # Derive once so invalid secret configuration fails before any I/O.
        derive_priority_hmac_key(hmac_secret, _SCOPE_HMAC_INFO)
        self._transport = command_transport
        self._hmac_secret = hmac_secret

    def _command(self, command: list[object]) -> object:
        try:
            payload = self._transport(command)
        except Exception:
            raise SemanticStoreUnavailable() from None
        if type(payload) is not dict or set(payload) != {"result"}:
            raise SemanticStoreUnavailable()
        return payload["result"]

    def _keys(self, scope: SemanticCacheScope) -> dict[str, str]:
        digest = derive_scope_digest(self._hmac_secret, scope)
        return {
            "digest": digest,
            "result": f"{_KEY_PREFIX}result:{digest}",
            "lease": f"{_KEY_PREFIX}lease:{digest}",
            "negative": f"{_KEY_PREFIX}negative:{digest}",
            "attempts": f"{_KEY_PREFIX}attempts:{digest}",
        }

    def _record_digests(self, scope: SemanticCacheScope) -> tuple[str, str]:
        tenant_prefix = "\x00".join(
            (
                scope.workspace_id,
                scope.user_id,
                scope.mailbox_id,
                scope.provider,
            )
        ).encode("utf-8")
        conversation_digest = _derive_record_digest(
            self._hmac_secret,
            b"conversation",
            tenant_prefix + b"\x00" + scope.conversation_id.encode("utf-8"),
        )
        latest_turn_digest = _derive_record_digest(
            self._hmac_secret,
            b"latest-turn",
            tenant_prefix
            + b"\x00"
            + scope.conversation_id.encode("utf-8")
            + b"\x00"
            + scope.latest_turn_id.encode("utf-8"),
        )
        return conversation_digest, latest_turn_digest

    def _current_key_and_value(
        self,
        scope: SemanticCacheScope,
        occurred_at: int,
    ) -> tuple[str, str]:
        if type(occurred_at) is not int or occurred_at < 0:
            raise ValueError("invalid semantic occurrence time")
        conversation_digest, latest_turn_digest = self._record_digests(scope)
        return (
            f"{_KEY_PREFIX}current:{conversation_digest}",
            f"{occurred_at}:{latest_turn_digest}",
        )

    def mark_current_if_newer(
        self,
        scope: SemanticCacheScope,
        *,
        occurred_at: int,
    ) -> bool:
        key, value = self._current_key_and_value(scope, occurred_at)
        result = self._command(
            [
                "EVAL",
                _MARK_CURRENT_SCRIPT,
                1,
                key,
                occurred_at,
                value,
                RESULT_TTL_SECONDS,
            ]
        )
        if type(result) is not int or type(result) is bool or result not in (0, 1):
            raise SemanticStoreUnavailable()
        return result == 1

    def set_current_exact(
        self,
        scope: SemanticCacheScope,
        *,
        occurred_at: int,
    ) -> None:
        """Set provider-proven current identity without comparing clock domains."""
        key, value = self._current_key_and_value(scope, occurred_at)
        result = self._command(
            ["SET", key, value, "EX", RESULT_TTL_SECONDS]
        )
        if result != "OK":
            raise SemanticStoreUnavailable()

    def get_result(
        self,
        scope: SemanticCacheScope,
        *,
        input_hash: str,
    ) -> CachedSemanticAssessment | None:
        keys = self._keys(scope)
        value = self._command(["GET", keys["result"]])
        if value is None:
            return None
        conversation_digest, latest_turn_digest = self._record_digests(scope)
        record = _decode_result(
            value,
            expected_scope_digest=keys["digest"],
            expected_semantic_version=scope.semantic_version,
            expected_model_version=scope.model_version,
            expected_conversation_digest=conversation_digest,
            expected_latest_turn_digest=latest_turn_digest,
            expected_input_hash=input_hash,
        )
        if record is None:
            raise SemanticStoreUnavailable()
        return record

    def get_result_if_current(
        self,
        scope: SemanticCacheScope,
        *,
        input_hash: str,
        occurred_at: int,
    ) -> tuple[bool, CachedSemanticAssessment | None]:
        """Atomically read a result only while this exact turn is current."""
        keys = self._keys(scope)
        current_key, current_value = self._current_key_and_value(scope, occurred_at)
        value = self._command(
            [
                "EVAL",
                _GET_RESULT_IF_CURRENT_SCRIPT,
                2,
                current_key,
                keys["result"],
                current_value,
                _CURRENT_MISMATCH_SENTINEL,
            ]
        )
        if value == _CURRENT_MISMATCH_SENTINEL:
            return False, None
        if value is None:
            return True, None
        conversation_digest, latest_turn_digest = self._record_digests(scope)
        record = _decode_result(
            value,
            expected_scope_digest=keys["digest"],
            expected_semantic_version=scope.semantic_version,
            expected_model_version=scope.model_version,
            expected_conversation_digest=conversation_digest,
            expected_latest_turn_digest=latest_turn_digest,
            expected_input_hash=input_hash,
        )
        if record is None:
            raise SemanticStoreUnavailable()
        return True, record

    def get_negative(self, scope: SemanticCacheScope) -> str | None:
        value = self._command(["GET", self._keys(scope)["negative"]])
        if value is None:
            return None
        if type(value) is not str or value not in _NEGATIVE_CODES:
            raise SemanticStoreUnavailable()
        return value

    def set_negative(self, scope: SemanticCacheScope, code: str) -> None:
        safe_code = code if code in _NEGATIVE_CODES else "semantic_unavailable"
        result = self._command(
            [
                "SET",
                self._keys(scope)["negative"],
                safe_code,
                "EX",
                NEGATIVE_TTL_SECONDS,
            ]
        )
        if result != "OK":
            raise SemanticStoreUnavailable()

    def commit_negative_if_lease_owned(
        self,
        scope: SemanticCacheScope,
        *,
        lease_token: str,
        code: str,
    ) -> bool:
        safe_code = code if code in _NEGATIVE_CODES else "semantic_unavailable"
        keys = self._keys(scope)
        result = self._command(
            [
                "EVAL",
                _COMMIT_NEGATIVE_SCRIPT,
                2,
                keys["lease"],
                keys["negative"],
                lease_token,
                safe_code,
                NEGATIVE_TTL_SECONDS,
            ]
        )
        if type(result) is not int or type(result) is bool or result not in (0, 1):
            raise SemanticStoreUnavailable()
        return result == 1

    def try_acquire_lease(
        self,
        scope: SemanticCacheScope,
        *,
        random_bytes: Callable[[int], bytes] = secrets.token_bytes,
    ) -> str | None:
        try:
            token_bytes = random_bytes(_LEASE_TOKEN_BYTES)
        except Exception:
            raise SemanticStoreUnavailable() from None
        if type(token_bytes) is not bytes or len(token_bytes) != _LEASE_TOKEN_BYTES:
            raise SemanticStoreUnavailable()
        token = base64.urlsafe_b64encode(token_bytes).rstrip(b"=").decode("ascii")
        result = self._command(
            [
                "SET",
                self._keys(scope)["lease"],
                token,
                "EX",
                LEASE_TTL_SECONDS,
                "NX",
            ]
        )
        if result == "OK":
            return token
        if result is None:
            return None
        raise SemanticStoreUnavailable()

    def consume_attempt(self, scope: SemanticCacheScope) -> bool:
        result = self._command(
            [
                "EVAL",
                _ATTEMPT_SCRIPT,
                1,
                self._keys(scope)["attempts"],
                ATTEMPT_WINDOW_SECONDS,
                MAX_ATTEMPTS_PER_WINDOW,
            ]
        )
        if type(result) is not int or type(result) is bool:
            raise SemanticStoreUnavailable()
        if result in (1, 2):
            return True
        if result == 0:
            return False
        raise SemanticStoreUnavailable()

    def commit_result_if_lease_owned(
        self,
        scope: SemanticCacheScope,
        *,
        lease_token: str,
        assessment: SemanticAssessment,
        input_hash: str,
        occurred_at: int,
        assessed_at: int | None = None,
    ) -> bool:
        timestamp = int(time.time()) if assessed_at is None else assessed_at
        confidence_result = evaluate_semantic_confidence(assessment)
        conversation_digest, latest_turn_digest = self._record_digests(scope)
        record = _encode_result(
            scope_digest=self._keys(scope)["digest"],
            semantic_version=scope.semantic_version,
            model_version=scope.model_version,
            conversation_digest=conversation_digest,
            latest_turn_digest=latest_turn_digest,
            input_hash=input_hash,
            assessment=assessment,
            effective_state=confidence_result.effective_state,
            assessed_at=timestamp,
        )
        keys = self._keys(scope)
        current_key, current_value = self._current_key_and_value(scope, occurred_at)
        result = self._command(
            [
                "EVAL",
                _COMMIT_RESULT_SCRIPT,
                3,
                keys["lease"],
                current_key,
                keys["result"],
                lease_token,
                current_value,
                record,
                RESULT_TTL_SECONDS,
            ]
        )
        if type(result) is not int or type(result) is bool or result not in (0, 1):
            raise SemanticStoreUnavailable()
        return result == 1

    def release_lease(self, scope: SemanticCacheScope, lease_token: str) -> bool:
        result = self._command(
            [
                "EVAL",
                _RELEASE_LEASE_SCRIPT,
                1,
                self._keys(scope)["lease"],
                lease_token,
            ]
        )
        if type(result) is not int or type(result) is bool or result not in (0, 1):
            raise SemanticStoreUnavailable()
        return result == 1


def _encode_result(
    *,
    scope_digest: str,
    semantic_version: str,
    model_version: str,
    conversation_digest: str,
    latest_turn_digest: str,
    input_hash: str,
    assessment: SemanticAssessment,
    effective_state: SemanticState,
    assessed_at: int,
) -> str:
    if (
        not isinstance(assessment, SemanticAssessment)
        or not isinstance(effective_state, SemanticState)
        or type(assessed_at) is not int
        or assessed_at < 0
        or type(semantic_version) is not str
        or not semantic_version
        or type(model_version) is not str
        or not model_version
        or any(
            type(value) is not str or _HEX_DIGEST_RE.fullmatch(value) is None
            for value in (
                scope_digest,
                conversation_digest,
                latest_turn_digest,
                input_hash,
            )
        )
    ):
        raise ValueError("invalid semantic assessment result")
    expected_effective = evaluate_semantic_confidence(assessment).effective_state
    if effective_state is not expected_effective:
        raise ValueError("invalid semantic effective state")
    return json.dumps(
        {
            "schemaVersion": STORE_SCHEMA_VERSION,
            "scopeDigest": scope_digest,
            "semanticVersion": semantic_version,
            "modelVersion": model_version,
            "conversationDigest": conversation_digest,
            "latestTurnDigest": latest_turn_digest,
            "inputHash": input_hash,
            **assessment.to_wire_dict(),
            "effectiveSemanticState": effective_state.value,
            "assessedAt": assessed_at,
        },
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_result(
    value: object,
    *,
    expected_scope_digest: str,
    expected_semantic_version: str,
    expected_model_version: str,
    expected_conversation_digest: str,
    expected_latest_turn_digest: str,
    expected_input_hash: str,
) -> CachedSemanticAssessment | None:
    if type(value) is not str or len(value) > 4_096:
        return None
    try:
        payload = json.loads(
            value,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
        if type(payload) is not dict or set(payload) != {
            "schemaVersion",
            "scopeDigest",
            "semanticVersion",
            "modelVersion",
            "conversationDigest",
            "latestTurnDigest",
            "inputHash",
            "state",
            "confidence",
            "reasonCode",
            "effectiveSemanticState",
            "assessedAt",
        }:
            return None
        if (
            payload["schemaVersion"] != STORE_SCHEMA_VERSION
            or payload["scopeDigest"] != expected_scope_digest
            or payload["semanticVersion"] != expected_semantic_version
            or payload["modelVersion"] != expected_model_version
            or payload["conversationDigest"] != expected_conversation_digest
            or payload["latestTurnDigest"] != expected_latest_turn_digest
            or payload["inputHash"] != expected_input_hash
            or type(payload["assessedAt"]) is not int
            or payload["assessedAt"] < 0
        ):
            return None
        assessment = SemanticAssessment.from_wire_dict(
            {
                "state": payload["state"],
                "confidence": payload["confidence"],
                "reasonCode": payload["reasonCode"],
            }
        )
        effective_state = SemanticState(payload["effectiveSemanticState"])
        if effective_state is not evaluate_semantic_confidence(assessment).effective_state:
            return None
        return CachedSemanticAssessment(
            assessment=assessment,
            effective_state=effective_state,
            assessed_at=payload["assessedAt"],
            input_hash=payload["inputHash"],
        )
    except Exception:
        return None


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str):
    raise ValueError("invalid JSON constant")


def build_runtime_semantic_store(*, hmac_secret: str) -> SemanticAssessmentStore:
    from api.auth.session_store import build_kv_command_transport

    return SemanticAssessmentStore(
        build_kv_command_transport(),
        hmac_secret=hmac_secret,
    )
