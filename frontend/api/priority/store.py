"""Tenant-scoped durable cache and idempotency primitives for Priority semantics."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from .event_reference import derive_priority_hmac_key
from .semantic_thresholds import evaluate_semantic_confidence
from .semantic_types import SemanticAssessment, SemanticState


if TYPE_CHECKING:
    from .authority import PriorityMessageIdentity


RESULT_TTL_SECONDS = 30 * 24 * 60 * 60
NEGATIVE_TTL_SECONDS = 5 * 60
LEASE_TTL_SECONDS = 60
ATTEMPT_WINDOW_SECONDS = 24 * 60 * 60
MAX_ATTEMPTS_PER_WINDOW = 2
STORE_SCHEMA_VERSION = 1
NEW_INBOUND_INDEX_SCHEMA_VERSION = 1
NEW_INBOUND_INDEX_MAX_RECORDS = 64
NEW_INBOUND_INDEX_TTL_SECONDS = RESULT_TTL_SECONDS
NEW_INBOUND_INDEX_MAX_SERIALIZED_RECORD_BYTES = 2 * 1_024
NEW_INBOUND_INDEX_MAX_CONVERSATION_ID_CHARACTERS = 1_024
NEW_INBOUND_INDEX_MAX_TURN_ID_CHARACTERS = 512
NEW_INBOUND_INDEX_MAX_SCOPE_IDENTIFIER_CHARACTERS = 1_024
NEW_INBOUND_INDEX_MAX_VERSION_CHARACTERS = 256
NEW_INBOUND_INDEX_MAX_MODEL_CHARACTERS = 128
NEW_INBOUND_INDEX_MAX_OCCURRENCE = 9_007_199_254_740_991
NEW_INBOUND_INDEX_READ_BATCH_SIZE = 6
SEMANTIC_HYDRATION_RESULT_BATCH_SIZE = 3
NEW_INBOUND_DISMISSAL_TTL_SECONDS = NEW_INBOUND_INDEX_TTL_SECONDS
NEW_INBOUND_DISMISSAL_READ_BATCH_SIZE = NEW_INBOUND_INDEX_MAX_RECORDS
WORKFLOW_STORE_SCHEMA_VERSION = 1
WORKFLOW_TTL_SECONDS = RESULT_TTL_SECONDS
WORKFLOW_MAX_BATCH_IDENTITIES = 64
WORKFLOW_REDIS_READ_BATCH_SIZE = 16
WORKFLOW_MAX_SERIALIZED_RECORD_BYTES = 2 * 1_024
WORKFLOW_MAX_SAFE_INTEGER = 9_007_199_254_740_991

_KEY_PREFIX = "cuevion:priority:semantic:v1:"
_WORKFLOW_KEY_PREFIX = "cuevion:priority:workflow:v1:"
_SCOPE_HMAC_INFO = b"cuevion/priority/cache-scope/v1\x00"
_WORKFLOW_SCOPE_HMAC_INFO = b"cuevion/priority/workflow-scope/v1\x00"
_RECORD_HMAC_INFO = b"cuevion/priority/cache-record/v1\x00"
_NEW_INBOUND_INDEX_SCOPE_HMAC_INFO = (
    b"cuevion/priority/new-inbound-index-scope/v1\x00"
)
_NEW_INBOUND_INDEX_RECORD_HMAC_INFO = (
    b"cuevion/priority/new-inbound-index-record/v1\x00"
)
_NEW_INBOUND_DISMISSAL_HMAC_INFO = (
    b"cuevion/priority/new-inbound-dismissal/v1\x00"
)
_NEW_INBOUND_DISMISSAL_VALUE = "1"
_LEASE_TOKEN_BYTES = 32
_HEX_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_REDIS_NONNEGATIVE_INTEGER_SCORE_RE = re.compile(
    r"(?:0|[1-9][0-9]*)(?:\.0+)?"
)
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
_WORKFLOW_CORRUPT_SENTINEL = "__cuevion_priority_workflow_corrupt__"
_COMMIT_RESULT_SCRIPT = (
    "if redis.call('GET',KEYS[1])==ARGV[1] and "
    "redis.call('GET',KEYS[2])==ARGV[2] then "
    "redis.call('SET',KEYS[3],ARGV[3],'EX',ARGV[4]);"
    "redis.call('DEL',KEYS[1]);return 1 else return 0 end"
)
_COMMIT_NEW_INBOUND_RESULT_SCRIPT = (
    "if redis.call('GET',KEYS[1])~=ARGV[1] or "
    "redis.call('GET',KEYS[2])~=ARGV[2] then return 0 end;"
    "local function keyType(key) local value=redis.call('TYPE',key);"
    "if type(value)=='table' then return value['ok'] end;return value end;"
    "local recordsType=keyType(KEYS[4]);"
    "local occurrencesType=keyType(KEYS[5]);"
    "local freshnessType=keyType(KEYS[6]);"
    "local dismissalType=keyType(KEYS[7]);"
    "if (recordsType~='none' and recordsType~='hash') or "
    "(occurrencesType~='none' and occurrencesType~='zset') or "
    "(freshnessType~='none' and freshnessType~='zset') or "
    "(dismissalType~='none' and dismissalType~='string') then return -1 end;"
    "local dismissal=redis.call('GET',KEYS[7]);"
    "if dismissal and dismissal~=ARGV[12] then return -1 end;"
    "local prior=redis.call('ZSCORE',KEYS[5],ARGV[5]);"
    "local existing=redis.call('HGET',KEYS[4],ARGV[5]);"
    "if (prior and not existing) or (existing and not prior) then return -1 end;"
    "redis.call('HSET',KEYS[4],ARGV[5],ARGV[6]);"
    "redis.call('ZADD',KEYS[5],ARGV[7],ARGV[5]);"
    "redis.call('ZADD',KEYS[6],ARGV[8],ARGV[5]);"
    "local expired=redis.call('ZRANGEBYSCORE',KEYS[6],'-inf',ARGV[11]);"
    "for _,member in ipairs(expired) do "
    "redis.call('HDEL',KEYS[4],member);"
    "redis.call('ZREM',KEYS[5],member);"
    "redis.call('ZREM',KEYS[6],member);end;"
    "local excess=redis.call('ZCARD',KEYS[6])-tonumber(ARGV[10]);"
    "if excess>0 then "
    "local oldest=redis.call('ZRANGE',KEYS[6],0,excess-1);"
    "for _,member in ipairs(oldest) do "
    "redis.call('HDEL',KEYS[4],member);"
    "redis.call('ZREM',KEYS[5],member);"
    "redis.call('ZREM',KEYS[6],member);end;end;"
    "redis.call('EXPIRE',KEYS[4],ARGV[9]);"
    "redis.call('EXPIRE',KEYS[5],ARGV[9]);"
    "redis.call('EXPIRE',KEYS[6],ARGV[9]);"
    "if dismissal then redis.call('EXPIRE',KEYS[7],ARGV[9]);end;"
    "redis.call('SET',KEYS[3],ARGV[3],'EX',ARGV[4]);"
    "redis.call('DEL',KEYS[1]);return 1"
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
_DISMISS_NEW_INBOUND_SCRIPT = (
    "if redis.call('HGET',KEYS[1],ARGV[1])~=ARGV[2] then return 0 end;"
    "local occurrence=redis.call('ZSCORE',KEYS[2],ARGV[1]);"
    "if not occurrence or tonumber(occurrence)~=tonumber(ARGV[6]) then return 0 end;"
    "local freshness=redis.call('ZSCORE',KEYS[3],ARGV[1]);"
    "if not freshness or tonumber(freshness)~=tonumber(ARGV[7]) then return 0 end;"
    "if redis.call('GET',KEYS[4])~=ARGV[4] then return 0 end;"
    "local dismissalType=redis.call('TYPE',KEYS[5]);"
    "if type(dismissalType)=='table' then dismissalType=dismissalType['ok'] end;"
    "if dismissalType~='none' and dismissalType~='string' then return -1 end;"
    "local existing=redis.call('GET',KEYS[5]);"
    "if existing and existing~=ARGV[3] then return -1 end;"
    "redis.call('SET',KEYS[5],ARGV[3],'EX',ARGV[5]);return 1"
)
_READ_NEW_INBOUND_DISMISSALS_SCRIPT = (
    "local values=redis.call('MGET',unpack(KEYS));local states={};"
    "for index=1,#KEYS do local value=values[index];"
    "if value then if value~=ARGV[1] then return {-1} end;states[index]=1 "
    "else local keyType=redis.call('TYPE',KEYS[index]);"
    "if type(keyType)=='table' then keyType=keyType['ok'] end;"
    "if keyType~='none' then return {-1} end;states[index]=0 end;end;"
    "return states"
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
_WRITE_WORKFLOW_RECORD_SCRIPT = (
    "local existing=redis.call('GET',KEYS[1]);local record=nil;"
    "if existing then local ok,value=pcall(cjson.decode,existing);"
    "if not ok or type(value)~='table' then return ARGV[9] end;"
    "local count=0;for _ in pairs(value) do count=count+1 end;"
    "if count~=8 or value['schemaVersion']~=tonumber(ARGV[1]) or "
    "value['scopeDigest']~=ARGV[2] or value['identityDigest']~=ARGV[3] or "
    "type(value['manualPriority'])~='string' or "
    "(value['manualPriority']~='none' and value['manualPriority']~='priority' "
    "and value['manualPriority']~='removed') or "
    "type(value['cleared'])~='string' or "
    "(value['cleared']~='active' and value['cleared']~='cleared') or "
    "type(value['waiting'])~='string' or "
    "(value['waiting']~='absent' and value['waiting']~='waiting_on_other' "
    "and value['waiting']~='returned_reply') or "
    "type(value['version'])~='number' or value['version']<1 or "
    "value['version']%1~=0 or value['version']>=tonumber(ARGV[8]) or "
    "type(value['updatedAt'])~='number' or value['updatedAt']<0 or "
    "value['updatedAt']%1~=0 or value['updatedAt']>tonumber(ARGV[8]) "
    "then return ARGV[9] end;record=value;else record={"
    "schemaVersion=tonumber(ARGV[1]),scopeDigest=ARGV[2],identityDigest=ARGV[3],"
    "manualPriority='none',cleared='active',waiting='absent',version=0,updatedAt=0};end;"
    "local field=ARGV[4];local nextValue=ARGV[5];"
    "if field=='manualPriority' then "
    "if nextValue~='none' and nextValue~='priority' and nextValue~='removed' "
    "then return ARGV[9] end;record['manualPriority']=nextValue;"
    "elseif field=='cleared' then "
    "if nextValue~='active' and nextValue~='cleared' then return ARGV[9] end;"
    "record['cleared']=nextValue;elseif field=='waiting' then "
    "if nextValue~='absent' and nextValue~='waiting_on_other' "
    "and nextValue~='returned_reply' then return ARGV[9] end;"
    "record['waiting']=nextValue;else return ARGV[9] end;"
    "local clock=redis.call('TIME');local seconds=tonumber(clock[1]);"
    "local micros=tonumber(clock[2]);if not seconds or not micros then "
    "return ARGV[9] end;local updatedAt=seconds*1000+math.floor(micros/1000);"
    "if updatedAt<0 or updatedAt>tonumber(ARGV[8]) then return ARGV[9] end;"
    "record['version']=record['version']+1;record['updatedAt']=updatedAt;"
    "local encoded=cjson.encode(record);if string.len(encoded)>tonumber(ARGV[7]) "
    "then return ARGV[9] end;"
    "redis.call('SET',KEYS[1],encoded,'EX',ARGV[6]);return encoded"
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
class NewInboundIndexScope:
    workspace_id: str
    user_id: str
    mailbox_id: str
    provider: str
    mailbox_account_identity: str

    def canonical_bytes(self) -> bytes:
        values = (
            self.workspace_id,
            self.user_id,
            self.mailbox_id,
            self.provider,
            self.mailbox_account_identity,
        )
        if any(
            not _valid_index_identifier(
                value,
                NEW_INBOUND_INDEX_MAX_SCOPE_IDENTIFIER_CHARACTERS,
            )
            for value in values
        ) or self.provider not in {"google", "custom_imap"}:
            raise ValueError("invalid new-inbound index scope")
        return "\x00".join(values).encode("utf-8", errors="strict")


@dataclass(frozen=True, slots=True)
class NewInboundIndexEntry:
    conversation_id: str
    latest_turn_id: str
    semantic_version: str
    model_version: str
    occurred_at: int

    def __post_init__(self) -> None:
        if (
            not _valid_index_identifier(
                self.conversation_id,
                NEW_INBOUND_INDEX_MAX_CONVERSATION_ID_CHARACTERS,
            )
            or not _valid_index_identifier(
                self.latest_turn_id,
                NEW_INBOUND_INDEX_MAX_TURN_ID_CHARACTERS,
            )
            or not _valid_index_identifier(
                self.semantic_version,
                NEW_INBOUND_INDEX_MAX_VERSION_CHARACTERS,
            )
            or not _valid_index_identifier(
                self.model_version,
                NEW_INBOUND_INDEX_MAX_MODEL_CHARACTERS,
            )
            or type(self.occurred_at) is not int
            or not 0 <= self.occurred_at <= NEW_INBOUND_INDEX_MAX_OCCURRENCE
        ):
            raise ValueError("invalid new-inbound index entry")

    def to_cache_scope(self, index_scope: NewInboundIndexScope) -> SemanticCacheScope:
        if not isinstance(index_scope, NewInboundIndexScope):
            raise ValueError("invalid new-inbound index scope")
        return SemanticCacheScope(
            workspace_id=index_scope.workspace_id,
            user_id=index_scope.user_id,
            mailbox_id=index_scope.mailbox_id,
            provider=index_scope.provider,
            conversation_id=self.conversation_id,
            latest_turn_id=self.latest_turn_id,
            semantic_version=self.semantic_version,
            model_version=self.model_version,
        )


@dataclass(frozen=True, slots=True)
class CachedSemanticAssessment:
    assessment: SemanticAssessment
    effective_state: SemanticState
    assessed_at: int
    input_hash: str


CommandTransport = Callable[[list[object]], dict[str, object]]


class WorkflowStoreUnavailable(Exception):
    """Value-free failure for unavailable or malformed workflow storage."""

    __slots__ = ()

    def __str__(self) -> str:
        return "Priority workflow storage is unavailable"


@dataclass(frozen=True, slots=True)
class PriorityWorkflowScope:
    workspace_id: str
    user_id: str
    mailbox_id: str
    identity: PriorityMessageIdentity

    def canonical_bytes(self) -> bytes:
        values = (self.workspace_id, self.user_id, self.mailbox_id)
        try:
            identity_bytes = self.identity.canonical_bytes()
        except Exception:
            raise ValueError("invalid Priority workflow scope") from None
        if (
            any(
                type(value) is not str
                or not value
                or value != value.strip()
                or len(value) > 2_048
                or "\x00" in value
                or any(
                    ord(character) < 32 or ord(character) == 127
                    for character in value
                )
                for value in values
            )
            or type(identity_bytes) is not bytes
            or not 1 <= len(identity_bytes) <= 20_000
        ):
            raise ValueError("invalid Priority workflow scope")
        return (
            "\x00".join(values).encode("utf-8", errors="strict")
            + b"\x00"
            + identity_bytes
        )


@dataclass(frozen=True, slots=True)
class PriorityWorkflowRecord:
    manual_priority: str = "none"
    cleared: str = "active"
    waiting: str = "absent"
    version: int = 0
    updated_at: int | None = None

    def __post_init__(self) -> None:
        if (
            self.manual_priority not in {"none", "priority", "removed"}
            or self.cleared not in {"active", "cleared"}
            or self.waiting not in {"absent", "waiting_on_other", "returned_reply"}
            or type(self.version) is not int
            or not 0 <= self.version <= WORKFLOW_MAX_SAFE_INTEGER
            or (
                self.updated_at is not None
                and (
                    type(self.updated_at) is not int
                    or not 0 <= self.updated_at <= WORKFLOW_MAX_SAFE_INTEGER
                )
            )
            or (self.version == 0) != (self.updated_at is None)
        ):
            raise ValueError("invalid Priority workflow record")

    def to_wire_dict(
        self,
        scope: PriorityWorkflowScope,
    ) -> dict[str, object]:
        if not isinstance(scope, PriorityWorkflowScope):
            raise ValueError("invalid Priority workflow scope")
        return {
            "mailboxId": scope.mailbox_id,
            "identity": scope.identity.to_wire_dict(),
            "manualPriority": self.manual_priority,
            "cleared": self.cleared,
            "waiting": self.waiting,
            "version": self.version,
            "updatedAt": self.updated_at,
        }


def derive_workflow_scope_digest(
    secret: str,
    scope: PriorityWorkflowScope,
) -> str:
    if not isinstance(scope, PriorityWorkflowScope):
        raise ValueError("invalid Priority workflow scope")
    key = derive_priority_hmac_key(secret, _WORKFLOW_SCOPE_HMAC_INFO)
    return hmac.new(key, scope.canonical_bytes(), hashlib.sha256).hexdigest()


def _valid_index_identifier(value: object, maximum: int) -> bool:
    return (
        type(value) is str
        and value == value.strip()
        and 1 <= len(value) <= maximum
        and "\x00" not in value
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _parse_bounded_redis_score(value: object) -> int | None:
    if type(value) is int:
        parsed = value
    elif type(value) is float:
        if not math.isfinite(value) or not value.is_integer():
            return None
        parsed = int(value)
    elif (
        type(value) is str
        and _REDIS_NONNEGATIVE_INTEGER_SCORE_RE.fullmatch(value) is not None
    ):
        try:
            parsed = int(value.split(".", 1)[0])
        except ValueError:
            return None
    else:
        return None
    return parsed if 0 <= parsed <= NEW_INBOUND_INDEX_MAX_OCCURRENCE else None


def derive_scope_digest(secret: str, scope: SemanticCacheScope) -> str:
    key = derive_priority_hmac_key(secret, _SCOPE_HMAC_INFO)
    return hmac.new(key, scope.canonical_bytes(), hashlib.sha256).hexdigest()


def derive_new_inbound_index_scope_digest(
    secret: str,
    scope: NewInboundIndexScope,
) -> str:
    key = derive_priority_hmac_key(secret, _NEW_INBOUND_INDEX_SCOPE_HMAC_INFO)
    return hmac.new(key, scope.canonical_bytes(), hashlib.sha256).hexdigest()


def derive_new_inbound_dismissal_digest(
    secret: str,
    scope: NewInboundIndexScope,
    *,
    conversation_id: str,
    latest_turn_id: str,
) -> str:
    if (
        not isinstance(scope, NewInboundIndexScope)
        or not _valid_index_identifier(
            conversation_id,
            NEW_INBOUND_INDEX_MAX_CONVERSATION_ID_CHARACTERS,
        )
        or not _valid_index_identifier(
            latest_turn_id,
            NEW_INBOUND_INDEX_MAX_TURN_ID_CHARACTERS,
        )
    ):
        raise ValueError("invalid new-inbound dismissal identity")
    key = derive_priority_hmac_key(secret, _NEW_INBOUND_DISMISSAL_HMAC_INFO)
    identity = (
        scope.canonical_bytes()
        + b"\x00"
        + conversation_id.encode("utf-8", errors="strict")
        + b"\x00"
        + latest_turn_id.encode("utf-8", errors="strict")
    )
    return hmac.new(key, identity, hashlib.sha256).hexdigest()


def _derive_record_digest(secret: str, label: bytes, value: bytes) -> str:
    key = derive_priority_hmac_key(secret, _RECORD_HMAC_INFO)
    return hmac.new(key, label + b"\x00" + value, hashlib.sha256).hexdigest()


class PriorityWorkflowStore:
    """Exact-key workflow ledger with Redis-serialized, server-timed writes."""

    __slots__ = ("_transport", "_hmac_secret")

    def __init__(self, command_transport: CommandTransport, *, hmac_secret: str) -> None:
        if not callable(command_transport):
            raise ValueError("invalid workflow command transport")
        derive_priority_hmac_key(hmac_secret, _WORKFLOW_SCOPE_HMAC_INFO)
        self._transport = command_transport
        self._hmac_secret = hmac_secret

    def _command(self, command: list[object]) -> object:
        try:
            payload = self._transport(command)
        except Exception:
            raise WorkflowStoreUnavailable() from None
        if type(payload) is not dict or set(payload) != {"result"}:
            raise WorkflowStoreUnavailable()
        return payload["result"]

    def _digests(self, scope: PriorityWorkflowScope) -> tuple[str, str]:
        scope_digest = derive_workflow_scope_digest(self._hmac_secret, scope)
        identity_digest = _derive_record_digest(
            self._hmac_secret,
            b"workflow-identity",
            scope.canonical_bytes(),
        )
        return scope_digest, identity_digest

    def _key(self, scope: PriorityWorkflowScope) -> str:
        scope_digest, _identity_digest = self._digests(scope)
        return f"{_WORKFLOW_KEY_PREFIX}record:{scope_digest}"

    def read_records(
        self,
        scopes: tuple[PriorityWorkflowScope, ...],
    ) -> tuple[PriorityWorkflowRecord, ...]:
        if (
            type(scopes) is not tuple
            or len(scopes) > WORKFLOW_MAX_BATCH_IDENTITIES
            or any(not isinstance(scope, PriorityWorkflowScope) for scope in scopes)
        ):
            raise ValueError("invalid Priority workflow batch")
        records: list[PriorityWorkflowRecord] = []
        for start in range(0, len(scopes), WORKFLOW_REDIS_READ_BATCH_SIZE):
            batch = scopes[start : start + WORKFLOW_REDIS_READ_BATCH_SIZE]
            values = self._command(["MGET", *(self._key(scope) for scope in batch)])
            if type(values) is not list or len(values) != len(batch):
                raise WorkflowStoreUnavailable()
            for scope, value in zip(batch, values, strict=True):
                if value is None:
                    records.append(PriorityWorkflowRecord())
                    continue
                scope_digest, identity_digest = self._digests(scope)
                decoded = _decode_workflow_record(
                    value,
                    expected_scope_digest=scope_digest,
                    expected_identity_digest=identity_digest,
                )
                if decoded is None:
                    raise WorkflowStoreUnavailable()
                records.append(decoded)
        return tuple(records)

    def write_field(
        self,
        scope: PriorityWorkflowScope,
        *,
        field: str,
        value: str,
    ) -> PriorityWorkflowRecord:
        allowed = {
            "manualPriority": frozenset({"none", "priority", "removed"}),
            "cleared": frozenset({"active", "cleared"}),
            "waiting": frozenset(
                {"absent", "waiting_on_other", "returned_reply"}
            ),
        }
        if (
            not isinstance(scope, PriorityWorkflowScope)
            or field not in allowed
            or type(value) is not str
            or value not in allowed[field]
        ):
            raise ValueError("invalid Priority workflow write")
        scope_digest, identity_digest = self._digests(scope)
        result = self._command(
            [
                "EVAL",
                _WRITE_WORKFLOW_RECORD_SCRIPT,
                1,
                self._key(scope),
                WORKFLOW_STORE_SCHEMA_VERSION,
                scope_digest,
                identity_digest,
                field,
                value,
                WORKFLOW_TTL_SECONDS,
                WORKFLOW_MAX_SERIALIZED_RECORD_BYTES,
                WORKFLOW_MAX_SAFE_INTEGER,
                _WORKFLOW_CORRUPT_SENTINEL,
            ]
        )
        if result == _WORKFLOW_CORRUPT_SENTINEL:
            raise WorkflowStoreUnavailable()
        decoded = _decode_workflow_record(
            result,
            expected_scope_digest=scope_digest,
            expected_identity_digest=identity_digest,
        )
        if decoded is None:
            raise WorkflowStoreUnavailable()
        return decoded


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

    def _new_inbound_index_keys(
        self,
        scope: NewInboundIndexScope,
    ) -> dict[str, str]:
        digest = derive_new_inbound_index_scope_digest(
            self._hmac_secret,
            scope,
        )
        return {
            "digest": digest,
            "records": f"{_KEY_PREFIX}new-inbound-index:records:{digest}",
            "occurrences": f"{_KEY_PREFIX}new-inbound-index:occurrences:{digest}",
            "freshness": f"{_KEY_PREFIX}new-inbound-index:freshness:{digest}",
        }

    def _new_inbound_dismissal_key(
        self,
        scope: NewInboundIndexScope,
        *,
        conversation_id: str,
        latest_turn_id: str,
    ) -> str:
        digest = derive_new_inbound_dismissal_digest(
            self._hmac_secret,
            scope,
            conversation_id=conversation_id,
            latest_turn_id=latest_turn_id,
        )
        return f"{_KEY_PREFIX}new-inbound-dismissal:{digest}"

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

    def get_result_for_exact_scope(
        self,
        scope: SemanticCacheScope,
    ) -> CachedSemanticAssessment | None:
        """Read only the result bound to an already-proven exact scope.

        This lookup intentionally performs no current-pointer write, negative
        cache read, lease acquisition, or attempt consumption.  Callers must
        independently prove the provider source current before and after use.
        """
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
            expected_input_hash=None,
        )
        if record is None:
            raise SemanticStoreUnavailable()
        return record

    def get_results_for_hydration_scopes(
        self,
        scopes: tuple[SemanticCacheScope, ...],
    ) -> tuple[CachedSemanticAssessment | None, ...]:
        """Batch exact indexed result reads within the KV response envelope."""
        if (
            type(scopes) is not tuple
            or len(scopes) > NEW_INBOUND_INDEX_MAX_RECORDS
            or any(not isinstance(scope, SemanticCacheScope) for scope in scopes)
        ):
            raise ValueError("invalid semantic hydration scopes")
        results: list[CachedSemanticAssessment | None] = []
        for start in range(0, len(scopes), SEMANTIC_HYDRATION_RESULT_BATCH_SIZE):
            batch = scopes[start : start + SEMANTIC_HYDRATION_RESULT_BATCH_SIZE]
            keys = [self._keys(scope)["result"] for scope in batch]
            values = self._command(["MGET", *keys])
            if type(values) is not list or len(values) != len(batch):
                raise SemanticStoreUnavailable()
            for scope, value in zip(batch, values, strict=True):
                if value is None:
                    results.append(None)
                    continue
                scope_keys = self._keys(scope)
                conversation_digest, latest_turn_digest = self._record_digests(
                    scope
                )
                results.append(
                    _decode_result(
                        value,
                        expected_scope_digest=scope_keys["digest"],
                        expected_semantic_version=scope.semantic_version,
                        expected_model_version=scope.model_version,
                        expected_conversation_digest=conversation_digest,
                        expected_latest_turn_digest=latest_turn_digest,
                        expected_input_hash=None,
                    )
                )
        return tuple(results)

    def is_new_inbound_dismissed_exact(
        self,
        index_scope: NewInboundIndexScope,
        *,
        conversation_id: str,
        latest_turn_id: str,
    ) -> bool:
        """Read one exact-turn tombstone without semantic/model coupling."""

        if (
            not isinstance(index_scope, NewInboundIndexScope)
            or not _valid_index_identifier(
                conversation_id,
                NEW_INBOUND_INDEX_MAX_CONVERSATION_ID_CHARACTERS,
            )
            or not _valid_index_identifier(
                latest_turn_id,
                NEW_INBOUND_INDEX_MAX_TURN_ID_CHARACTERS,
            )
        ):
            raise ValueError("invalid new-inbound dismissal read")
        key = self._new_inbound_dismissal_key(
            index_scope,
            conversation_id=conversation_id,
            latest_turn_id=latest_turn_id,
        )
        values = self._command(
            [
                "EVAL",
                _READ_NEW_INBOUND_DISMISSALS_SCRIPT,
                1,
                key,
                _NEW_INBOUND_DISMISSAL_VALUE,
            ]
        )
        if (
            type(values) is not list
            or len(values) != 1
            or type(values[0]) is not int
            or values[0] not in (0, 1)
        ):
            raise SemanticStoreUnavailable()
        return values[0] == 1

    def get_new_inbound_dismissal_states(
        self,
        index_scope: NewInboundIndexScope,
        entries: tuple[NewInboundIndexEntry, ...],
    ) -> tuple[bool, ...]:
        """Batch exact-turn tombstone reads without touching semantic authority."""
        if (
            not isinstance(index_scope, NewInboundIndexScope)
            or type(entries) is not tuple
            or len(entries) > NEW_INBOUND_INDEX_MAX_RECORDS
            or any(not isinstance(entry, NewInboundIndexEntry) for entry in entries)
        ):
            raise ValueError("invalid new-inbound dismissal read")
        states: list[bool] = []
        for start in range(0, len(entries), NEW_INBOUND_DISMISSAL_READ_BATCH_SIZE):
            batch = entries[
                start : start + NEW_INBOUND_DISMISSAL_READ_BATCH_SIZE
            ]
            keys = [
                self._new_inbound_dismissal_key(
                    index_scope,
                    conversation_id=entry.conversation_id,
                    latest_turn_id=entry.latest_turn_id,
                )
                for entry in batch
            ]
            values = self._command(
                [
                    "EVAL",
                    _READ_NEW_INBOUND_DISMISSALS_SCRIPT,
                    len(keys),
                    *keys,
                    _NEW_INBOUND_DISMISSAL_VALUE,
                ]
            )
            if (
                type(values) is not list
                or len(values) != len(batch)
                or any(type(value) is not int or value not in (0, 1) for value in values)
            ):
                raise SemanticStoreUnavailable()
            for value in values:
                states.append(value == 1)
        return tuple(states)

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
        index_new_inbound: bool = False,
        new_inbound_mailbox_account_identity: str | None = None,
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
        if index_new_inbound:
            if new_inbound_mailbox_account_identity is None:
                raise ValueError("new-inbound mailbox account identity is required")
            index_scope = NewInboundIndexScope(
                workspace_id=scope.workspace_id,
                user_id=scope.user_id,
                mailbox_id=scope.mailbox_id,
                provider=scope.provider,
                mailbox_account_identity=new_inbound_mailbox_account_identity,
            )
            entry = NewInboundIndexEntry(
                conversation_id=scope.conversation_id,
                latest_turn_id=scope.latest_turn_id,
                semantic_version=scope.semantic_version,
                model_version=scope.model_version,
                occurred_at=occurred_at,
            )
            index_keys = self._new_inbound_index_keys(index_scope)
            index_record, conversation_digest = _encode_new_inbound_index_entry(
                secret=self._hmac_secret,
                index_scope=index_scope,
                entry=entry,
            )
            dismissal_key = self._new_inbound_dismissal_key(
                index_scope,
                conversation_id=entry.conversation_id,
                latest_turn_id=entry.latest_turn_id,
            )
            # The exact current pointer (plus the route's post-model provider
            # proof) is freshness authority. Provider occurrence timestamps are
            # metadata only: IMAP INTERNALDATE can tie or move backward.
            result = self._command(
                [
                    "EVAL",
                    _COMMIT_NEW_INBOUND_RESULT_SCRIPT,
                    7,
                    keys["lease"],
                    current_key,
                    keys["result"],
                    index_keys["records"],
                    index_keys["occurrences"],
                    index_keys["freshness"],
                    dismissal_key,
                    lease_token,
                    current_value,
                    record,
                    RESULT_TTL_SECONDS,
                    conversation_digest,
                    index_record,
                    occurred_at,
                    timestamp,
                    NEW_INBOUND_INDEX_TTL_SECONDS,
                    NEW_INBOUND_INDEX_MAX_RECORDS,
                    timestamp - NEW_INBOUND_INDEX_TTL_SECONDS,
                    _NEW_INBOUND_DISMISSAL_VALUE,
                ]
            )
            if type(result) is not int or type(result) is bool or result not in (0, 1):
                raise SemanticStoreUnavailable()
            return result == 1
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

    def read_new_inbound_index(
        self,
        scope: NewInboundIndexScope,
        *,
        semantic_version: str,
        model_version: str,
    ) -> tuple[NewInboundIndexEntry, ...]:
        if (
            not isinstance(scope, NewInboundIndexScope)
            or not _valid_index_identifier(
                semantic_version,
                NEW_INBOUND_INDEX_MAX_VERSION_CHARACTERS,
            )
            or not _valid_index_identifier(
                model_version,
                NEW_INBOUND_INDEX_MAX_MODEL_CHARACTERS,
            )
        ):
            raise ValueError("invalid new-inbound index read")
        keys = self._new_inbound_index_keys(scope)
        members = self._command(
            [
                "ZREVRANGE",
                keys["freshness"],
                0,
                NEW_INBOUND_INDEX_MAX_RECORDS - 1,
            ]
        )
        if (
            type(members) is not list
            or len(members) > NEW_INBOUND_INDEX_MAX_RECORDS
            or len(set(members)) != len(members)
            or any(
                type(member) is not str
                or _HEX_DIGEST_RE.fullmatch(member) is None
                for member in members
            )
        ):
            raise SemanticStoreUnavailable()
        entries: list[NewInboundIndexEntry] = []
        seen_conversations: set[str] = set()
        for start in range(0, len(members), NEW_INBOUND_INDEX_READ_BATCH_SIZE):
            member_batch = members[
                start : start + NEW_INBOUND_INDEX_READ_BATCH_SIZE
            ]
            values = self._command(
                ["HMGET", keys["records"], *member_batch]
            )
            if type(values) is not list or len(values) != len(member_batch):
                raise SemanticStoreUnavailable()
            for member, value in zip(member_batch, values, strict=True):
                entry = _decode_new_inbound_index_entry(
                    value,
                    secret=self._hmac_secret,
                    index_scope=scope,
                    expected_semantic_version=semantic_version,
                    expected_model_version=model_version,
                    expected_conversation_digest=member,
                )
                if entry is None or entry.conversation_id in seen_conversations:
                    continue
                seen_conversations.add(entry.conversation_id)
                entries.append(entry)
        return tuple(entries)

    def dismiss_new_inbound_exact(
        self,
        index_scope: NewInboundIndexScope,
        *,
        conversation_id: str,
        latest_turn_id: str,
        semantic_version: str,
        current: int,
    ) -> bool:
        """Persist a tombstone only for valid indexed exact-turn authority.

        The tombstone digest deliberately omits semantic and model versions:
        Done/Remove is authority over an unchanged provider turn, not over one
        model rendition of that turn.
        """
        if (
            not isinstance(index_scope, NewInboundIndexScope)
            or not _valid_index_identifier(
                conversation_id,
                NEW_INBOUND_INDEX_MAX_CONVERSATION_ID_CHARACTERS,
            )
            or not _valid_index_identifier(
                latest_turn_id,
                NEW_INBOUND_INDEX_MAX_TURN_ID_CHARACTERS,
            )
            or not _valid_index_identifier(
                semantic_version,
                NEW_INBOUND_INDEX_MAX_VERSION_CHARACTERS,
            )
            or type(current) is not int
            or current < 0
        ):
            raise ValueError("invalid new-inbound dismissal")

        index_keys = self._new_inbound_index_keys(index_scope)
        conversation_digest = _new_inbound_conversation_digest(
            self._hmac_secret,
            index_scope,
            conversation_id,
        )
        index_values = self._command(
            ["HMGET", index_keys["records"], conversation_digest]
        )
        if type(index_values) is not list or len(index_values) != 1:
            raise SemanticStoreUnavailable()
        raw_index_record = index_values[0]
        if raw_index_record is None:
            return False
        entry = _decode_new_inbound_index_entry(
            raw_index_record,
            secret=self._hmac_secret,
            index_scope=index_scope,
            expected_semantic_version=semantic_version,
            expected_model_version=None,
            expected_conversation_digest=conversation_digest,
        )
        if entry is None or entry.latest_turn_id != latest_turn_id:
            return False
        occurrence_score = self._command(
            ["ZSCORE", index_keys["occurrences"], conversation_digest]
        )
        freshness_score = self._command(
            ["ZSCORE", index_keys["freshness"], conversation_digest]
        )
        if occurrence_score is None or freshness_score is None:
            return False
        parsed_occurrence = _parse_bounded_redis_score(occurrence_score)
        parsed_freshness = _parse_bounded_redis_score(freshness_score)
        if parsed_occurrence is None or parsed_freshness is None:
            raise SemanticStoreUnavailable()
        if parsed_occurrence != entry.occurred_at:
            return False

        cache_scope = entry.to_cache_scope(index_scope)
        cache_keys = self._keys(cache_scope)
        raw_result = self._command(["GET", cache_keys["result"]])
        if raw_result is None:
            return False
        result_conversation_digest, result_latest_turn_digest = self._record_digests(
            cache_scope
        )
        cached = _decode_result(
            raw_result,
            expected_scope_digest=cache_keys["digest"],
            expected_semantic_version=cache_scope.semantic_version,
            expected_model_version=cache_scope.model_version,
            expected_conversation_digest=result_conversation_digest,
            expected_latest_turn_digest=result_latest_turn_digest,
            expected_input_hash=None,
        )
        if cached is None:
            raise SemanticStoreUnavailable()
        if (
            cached.assessed_at > current
            or current - cached.assessed_at > RESULT_TTL_SECONDS
            or parsed_freshness != cached.assessed_at
        ):
            return False

        tombstone_key = self._new_inbound_dismissal_key(
            index_scope,
            conversation_id=conversation_id,
            latest_turn_id=latest_turn_id,
        )
        result = self._command(
            [
                "EVAL",
                _DISMISS_NEW_INBOUND_SCRIPT,
                5,
                index_keys["records"],
                index_keys["occurrences"],
                index_keys["freshness"],
                cache_keys["result"],
                tombstone_key,
                conversation_digest,
                raw_index_record,
                _NEW_INBOUND_DISMISSAL_VALUE,
                raw_result,
                NEW_INBOUND_DISMISSAL_TTL_SECONDS,
                entry.occurred_at,
                parsed_freshness,
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


def _workflow_safe_integer(value: object, *, minimum: int) -> int | None:
    if type(value) is int:
        parsed = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        parsed = int(value)
    else:
        return None
    if not minimum <= parsed <= WORKFLOW_MAX_SAFE_INTEGER:
        return None
    return parsed


def _decode_workflow_record(
    value: object,
    *,
    expected_scope_digest: str,
    expected_identity_digest: str,
) -> PriorityWorkflowRecord | None:
    if type(value) is not str:
        return None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        return None
    if len(encoded) > WORKFLOW_MAX_SERIALIZED_RECORD_BYTES:
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
            "identityDigest",
            "manualPriority",
            "cleared",
            "waiting",
            "version",
            "updatedAt",
        }:
            return None
        version = _workflow_safe_integer(payload["version"], minimum=1)
        updated_at = _workflow_safe_integer(payload["updatedAt"], minimum=0)
        if (
            payload["schemaVersion"] != WORKFLOW_STORE_SCHEMA_VERSION
            or payload["scopeDigest"] != expected_scope_digest
            or payload["identityDigest"] != expected_identity_digest
            or version is None
            or updated_at is None
        ):
            return None
        return PriorityWorkflowRecord(
            manual_priority=payload["manualPriority"],
            cleared=payload["cleared"],
            waiting=payload["waiting"],
            version=version,
            updated_at=updated_at,
        )
    except Exception:
        return None


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
    expected_input_hash: str | None,
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
            or type(payload["inputHash"]) is not str
            or _HEX_DIGEST_RE.fullmatch(payload["inputHash"]) is None
            or (
                expected_input_hash is not None
                and payload["inputHash"] != expected_input_hash
            )
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


def _new_inbound_conversation_digest(
    secret: str,
    index_scope: NewInboundIndexScope,
    conversation_id: str,
) -> str:
    return _derive_record_digest(
        secret,
        b"new-inbound-index-conversation",
        index_scope.canonical_bytes()
        + b"\x00"
        + conversation_id.encode("utf-8", errors="strict"),
    )


def _new_inbound_index_record_mac(
    secret: str,
    index_scope: NewInboundIndexScope,
    canonical_payload: bytes,
) -> str:
    key = derive_priority_hmac_key(
        secret,
        _NEW_INBOUND_INDEX_RECORD_HMAC_INFO,
    )
    return hmac.new(
        key,
        index_scope.canonical_bytes() + b"\x00" + canonical_payload,
        hashlib.sha256,
    ).hexdigest()


def _encode_new_inbound_index_entry(
    *,
    secret: str,
    index_scope: NewInboundIndexScope,
    entry: NewInboundIndexEntry,
) -> tuple[str, str]:
    if (
        not isinstance(index_scope, NewInboundIndexScope)
        or not isinstance(entry, NewInboundIndexEntry)
    ):
        raise ValueError("invalid new-inbound index entry")
    scope_digest = derive_new_inbound_index_scope_digest(secret, index_scope)
    conversation_digest = _new_inbound_conversation_digest(
        secret,
        index_scope,
        entry.conversation_id,
    )
    unsigned = {
        "schemaVersion": NEW_INBOUND_INDEX_SCHEMA_VERSION,
        "scopeDigest": scope_digest,
        "conversationDigest": conversation_digest,
        "conversationId": entry.conversation_id,
        "latestTurnId": entry.latest_turn_id,
        "semanticVersion": entry.semantic_version,
        "modelVersion": entry.model_version,
        "occurredAt": entry.occurred_at,
    }
    canonical_payload = json.dumps(
        unsigned,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    value = json.dumps(
        {
            **unsigned,
            "recordMac": _new_inbound_index_record_mac(
                secret,
                index_scope,
                canonical_payload,
            ),
        },
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(value.encode("ascii")) > NEW_INBOUND_INDEX_MAX_SERIALIZED_RECORD_BYTES:
        raise ValueError("new-inbound index entry is too large")
    return value, conversation_digest


def _decode_new_inbound_index_entry(
    value: object,
    *,
    secret: str,
    index_scope: NewInboundIndexScope,
    expected_semantic_version: str,
    expected_model_version: str | None,
    expected_conversation_digest: str,
) -> NewInboundIndexEntry | None:
    if type(value) is not str:
        return None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        return None
    if len(encoded) > NEW_INBOUND_INDEX_MAX_SERIALIZED_RECORD_BYTES:
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
            "conversationDigest",
            "conversationId",
            "latestTurnId",
            "semanticVersion",
            "modelVersion",
            "occurredAt",
            "recordMac",
        }:
            return None
        entry = NewInboundIndexEntry(
            conversation_id=payload["conversationId"],
            latest_turn_id=payload["latestTurnId"],
            semantic_version=payload["semanticVersion"],
            model_version=payload["modelVersion"],
            occurred_at=payload["occurredAt"],
        )
        expected_scope_digest = derive_new_inbound_index_scope_digest(
            secret,
            index_scope,
        )
        computed_conversation_digest = _new_inbound_conversation_digest(
            secret,
            index_scope,
            entry.conversation_id,
        )
        unsigned = {
            key: payload[key]
            for key in payload
            if key != "recordMac"
        }
        canonical_payload = json.dumps(
            unsigned,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        if (
            payload["schemaVersion"] != NEW_INBOUND_INDEX_SCHEMA_VERSION
            or payload["scopeDigest"] != expected_scope_digest
            or payload["conversationDigest"] != expected_conversation_digest
            or payload["conversationDigest"] != computed_conversation_digest
            or entry.semantic_version != expected_semantic_version
            or (
                expected_model_version is not None
                and entry.model_version != expected_model_version
            )
            or type(payload["recordMac"]) is not str
            or _HEX_DIGEST_RE.fullmatch(payload["recordMac"]) is None
            or not hmac.compare_digest(
                payload["recordMac"],
                _new_inbound_index_record_mac(
                    secret,
                    index_scope,
                    canonical_payload,
                ),
            )
        ):
            return None
        return entry
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


def build_runtime_workflow_store(*, hmac_secret: str) -> PriorityWorkflowStore:
    from api.auth.session_store import build_kv_command_transport

    return PriorityWorkflowStore(
        build_kv_command_transport(),
        hmac_secret=hmac_secret,
    )
