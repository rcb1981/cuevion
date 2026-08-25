"""Operator-only Collaboration v2 KV compatibility probe.

Run from the frontend serverless project root with
``python -m tools.collaboration_kv_probe``.
The default is a zero-network dry run. Remote execution is deliberately guarded
and is intended only for a separately authorized operator task.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import os
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

from api.collaboration import models, owner_rate_limit, redis_store


PROBE_VERSION = "1"
PROBE_HASH_TAG = redis_store.V2_CLUSTER_HASH_TAG
PROBE_KEY_BASE = f"{redis_store.V2_KEY_PREFIX}:probe"
MAX_PROBE_TTL_SECONDS = 120
MAX_PROBE_TTL_MILLISECONDS = MAX_PROBE_TTL_SECONDS * 1000
MAX_PROBE_KEYS = 32
MAX_REMOTE_COMMANDS = 160
MAX_REMOTE_EVAL_CALLS = 96
MAX_CONCURRENCY = 8
REMOTE_START_CUTOFF_SECONDS = 40
REMOTE_RUNTIME_TARGET_SECONDS = 60
REMOTE_CONFIRM_ENV = "CUEVION_COLLAB_KV_PROBE_CONFIRM"
REMOTE_CONFIRM_VALUE = "EXECUTE_EPHEMERAL_COLLAB_V2_KV_PROBE"
KV_URL_ENV = "KV_REST_API_URL"
KV_TOKEN_ENV = "KV_REST_API_TOKEN"
MAX_KEY_BYTES = 196

OWNER_READ_CASES = (
    "transport_sanity",
    "lua_cjson",
    "redis_time",
    "owner_rate_limit",
    "owner_rate_limit_competing_clients",
)
OWNER_WRITE_CASES = (
    "atomic_create",
    "create_competing_clients",
    "owner_append_idempotency",
    "owner_append_competing_clients",
)

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
_SUFFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
_CANONICAL_UINT_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_ALLOWED_COMMANDS = frozenset({"SET", "GET", "PTTL", "TIME", "EVAL", "DEL"})
_CJSON_PROBE_LUA = (
    "if #KEYS ~= 0 or #ARGV ~= 1 then return 'invalid' end "
    "local ok,value=pcall(cjson.decode,ARGV[1]); "
    "if not ok or type(value)~='table' then return 'invalid' end; "
    "return cjson.encode({status='ok',value=value.value})"
)
_APPROVED_EVAL_SCRIPTS = frozenset(
    {
        _CJSON_PROBE_LUA,
        owner_rate_limit._OWNER_RATE_LIMIT_LUA,
        redis_store._CREATE_V2_THREAD_LUA,
        redis_store._APPEND_V2_OWNER_IDEMPOTENT_LUA,
    }
)
_DANGEROUS_COMMANDS = frozenset(
    {
        "ACL",
        "BGSAVE",
        "BGREWRITEAOF",
        "CLIENT",
        "CLUSTER",
        "COMMAND",
        "CONFIG",
        "DBSIZE",
        "DEBUG",
        "EVALSHA",
        "FCALL",
        "FLUSHALL",
        "FLUSHDB",
        "FUNCTION",
        "KEYS",
        "LASTSAVE",
        "LATENCY",
        "MIGRATE",
        "MONITOR",
        "MOVE",
        "RANDOMKEY",
        "RENAME",
        "RENAMENX",
        "REPLICAOF",
        "RESTORE",
        "SAVE",
        "SCAN",
        "SCRIPT",
        "SHUTDOWN",
        "SLAVEOF",
        "SLOWLOG",
        "SWAPDB",
        "SYNC",
    }
)


class ProbeError(Exception):
    """Value-free failure suitable for redacted operator output."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else "probe_failed"
        super().__init__(self.code)


def _canonical_run_id(value: object) -> str:
    if type(value) is not str or _RUN_ID_RE.fullmatch(value) is None:
        raise ProbeError("invalid_run_id")
    try:
        decoded = base64.urlsafe_b64decode((value + "==").encode("ascii"))
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    except Exception:
        raise ProbeError("invalid_run_id") from None
    if len(decoded) != 16 or canonical != value:
        raise ProbeError("invalid_run_id")
    return value


def generate_run_id() -> str:
    value = base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode("ascii")
    return _canonical_run_id(value)


@dataclass(slots=True)
class ProbeNamespace:
    run_id: str
    _keys: set[str] = field(default_factory=set, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.run_id = _canonical_run_id(self.run_id)

    @property
    def prefix(self) -> str:
        return f"{PROBE_KEY_BASE}:{self.run_id}:"

    @property
    def thread_prefix(self) -> str:
        return self.prefix + "thread:"

    def key(self, suffix: object) -> str:
        if type(suffix) is not str or _SUFFIX_RE.fullmatch(suffix) is None:
            raise ProbeError("invalid_probe_key")
        key = self.prefix + suffix
        self.validate(key, require_registered=False)
        with self._lock:
            if key not in self._keys and len(self._keys) >= MAX_PROBE_KEYS:
                raise ProbeError("probe_key_limit_exceeded")
            self._keys.add(key)
        return key

    def validate(self, key: object, *, require_registered: bool = True) -> str:
        if (
            type(key) is not str
            or not key.startswith(self.prefix)
            or key.count(PROBE_HASH_TAG) != 1
            or not key.startswith(f"cuevion:collab:v2:{PROBE_HASH_TAG}:probe:")
            or len(key.encode("utf-8")) > MAX_KEY_BYTES
        ):
            raise ProbeError("invalid_probe_key")
        suffix = key[len(self.prefix) :]
        if _SUFFIX_RE.fullmatch(suffix) is None:
            raise ProbeError("invalid_probe_key")
        if require_registered:
            with self._lock:
                if key not in self._keys:
                    raise ProbeError("unregistered_probe_key")
        return key

    def registered_keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._keys))


@dataclass(slots=True)
class ProbeBudget:
    started_at: float
    remote: bool
    command_count: int = 0
    eval_count: int = 0
    result_types: dict[str, set[str]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reserve(self, command_name: str) -> None:
        with self._lock:
            if self.remote and time.monotonic() - self.started_at > REMOTE_START_CUTOFF_SECONDS:
                raise ProbeError("remote_runtime_cutoff")
            if self.command_count >= MAX_REMOTE_COMMANDS:
                raise ProbeError("command_limit_exceeded")
            if command_name == "EVAL" and self.eval_count >= MAX_REMOTE_EVAL_CALLS:
                raise ProbeError("eval_limit_exceeded")
            self.command_count += 1
            if command_name == "EVAL":
                self.eval_count += 1

    def record_result(self, command_name: str, value: object) -> None:
        type_name = "null" if value is None else type(value).__name__
        with self._lock:
            self.result_types.setdefault(command_name, set()).add(type_name)


class ProbeCommandPolicy:
    """Validate the complete command before it reaches any transport."""

    def __init__(self, namespace: ProbeNamespace) -> None:
        self._namespace = namespace

    @staticmethod
    def _positive_uint(value: object, *, maximum: int) -> int:
        if type(value) is int and not isinstance(value, bool):
            parsed = value
        elif type(value) is str and _CANONICAL_UINT_RE.fullmatch(value) is not None:
            parsed = int(value)
        else:
            raise ProbeError("invalid_command")
        if not 1 <= parsed <= maximum:
            raise ProbeError("unsafe_ttl")
        return parsed

    def validate(self, command: object) -> list[object]:
        if type(command) is not list or not command or type(command[0]) is not str:
            raise ProbeError("invalid_command")
        name = command[0]
        if name in _DANGEROUS_COMMANDS:
            raise ProbeError("destructive_command_rejected")
        if name not in _ALLOWED_COMMANDS:
            raise ProbeError("unapproved_command")
        if name == "SET":
            if len(command) != 5 or command[3] != "PX":
                raise ProbeError("persistent_set_rejected")
            self._namespace.validate(command[1])
            if type(command[2]) is not str or len(command[2].encode("utf-8")) > 4096:
                raise ProbeError("invalid_command")
            self._positive_uint(command[4], maximum=MAX_PROBE_TTL_MILLISECONDS)
        elif name in {"GET", "PTTL"}:
            if len(command) != 2:
                raise ProbeError("invalid_command")
            self._namespace.validate(command[1])
        elif name == "TIME":
            if len(command) != 1:
                raise ProbeError("invalid_command")
        elif name == "DEL":
            if not 2 <= len(command) <= MAX_PROBE_KEYS + 1:
                raise ProbeError("invalid_command")
            for key in command[1:]:
                self._namespace.validate(key)
        elif name == "EVAL":
            self._validate_eval(command)
        return command

    def _validate_eval(self, command: list[object]) -> None:
        if len(command) < 4 or type(command[1]) is not str or command[1] not in _APPROVED_EVAL_SCRIPTS:
            raise ProbeError("unapproved_eval_script")
        key_count = command[2]
        if type(key_count) is not int or isinstance(key_count, bool) or not 0 <= key_count <= 4:
            raise ProbeError("invalid_command")
        if len(command) < 3 + key_count:
            raise ProbeError("invalid_command")
        keys = command[3 : 3 + key_count]
        args = command[3 + key_count :]
        for key in keys:
            self._namespace.validate(key)

        script = command[1]
        if script == _CJSON_PROBE_LUA:
            if key_count != 0 or len(args) != 1 or args[0] != '{"value":"probe"}':
                raise ProbeError("invalid_command")
            return
        if script == owner_rate_limit._OWNER_RATE_LIMIT_LUA:
            if key_count != 1 or len(args) != 3:
                raise ProbeError("invalid_command")
            interval = self._positive_uint(args[0], maximum=20_000_000)
            burst = self._positive_uint(args[1], maximum=30)
            if (interval, burst) not in {
                (5_000_000, 4),
                (500_000, 30),
                (2_000_000, 10),
                (10_000, 1),
            }:
                raise ProbeError("invalid_command")
            if math.ceil((interval * burst) / 1000) > MAX_PROBE_TTL_MILLISECONDS:
                raise ProbeError("unsafe_ttl")
            if args[2] != "128":
                raise ProbeError("invalid_command")
            return
        if script == redis_store._CREATE_V2_THREAD_LUA:
            if key_count not in {2, 3} or len(args) != 4:
                raise ProbeError("invalid_command")
            self._positive_uint(args[2], maximum=MAX_PROBE_TTL_SECONDS)
            if args[3] != self._namespace.thread_prefix:
                raise ProbeError("invalid_probe_key")
            return
        if script == redis_store._APPEND_V2_OWNER_IDEMPOTENT_LUA:
            if key_count not in {3, 4} or len(args) != 14:
                raise ProbeError("invalid_command")
            thread_ttl = self._positive_uint(args[2], maximum=MAX_PROBE_TTL_SECONDS)
            idempotency_ttl = self._positive_uint(args[3], maximum=MAX_PROBE_TTL_SECONDS)
            if idempotency_ttl > thread_ttl:
                raise ProbeError("unsafe_ttl")
            return
        raise ProbeError("unapproved_eval_script")


RawTransport = Callable[[list[object]], dict[str, object]]


class SafeProbeTransport:
    """Typed operations over a policy-checked, budgeted raw transport."""

    def __init__(
        self,
        namespace: ProbeNamespace,
        budget: ProbeBudget,
        raw_transport: RawTransport,
    ) -> None:
        self._namespace = namespace
        self._budget = budget
        self._raw_transport = raw_transport
        self._policy = ProbeCommandPolicy(namespace)

    def _dispatch(self, command: list[object]) -> object:
        validated = self._policy.validate(command)
        name = str(validated[0])
        self._budget.reserve(name)
        try:
            payload = self._raw_transport(validated)
        except Exception:
            raise ProbeError("transport_unavailable") from None
        if type(payload) is not dict or set(payload) != {"result"}:
            raise ProbeError("response_shape_invalid")
        result = payload["result"]
        self._budget.record_result(name, result)
        return result

    def set_px(self, key: str, value: str, ttl_milliseconds: int) -> str:
        result = self._dispatch(["SET", key, value, "PX", ttl_milliseconds])
        if result != "OK":
            raise ProbeError("set_response_invalid")
        return result

    def get(self, key: str) -> str | None:
        result = self._dispatch(["GET", key])
        if result is not None and type(result) is not str:
            raise ProbeError("get_response_invalid")
        return result

    def pttl(self, key: str) -> int:
        result = self._dispatch(["PTTL", key])
        if type(result) is not int or isinstance(result, bool):
            raise ProbeError("pttl_response_invalid")
        return result

    def redis_time(self) -> tuple[str, str]:
        result = self._dispatch(["TIME"])
        if (
            type(result) is not list
            or len(result) != 2
            or any(type(value) is not str or _CANONICAL_UINT_RE.fullmatch(value) is None for value in result)
            or int(result[1]) > 999_999
        ):
            raise ProbeError("time_response_invalid")
        return result[0], result[1]

    def eval(self, script: str, keys: list[str], args: list[object]) -> object:
        return self._dispatch(["EVAL", script, len(keys), *keys, *args])

    def cleanup(self, keys: tuple[str, ...]) -> int:
        if not keys:
            return 0
        result = self._dispatch(["DEL", *keys])
        if type(result) is not int or isinstance(result, bool) or not 0 <= result <= len(keys):
            raise ProbeError("cleanup_response_invalid")
        return result


def _eval_object(
    transport: SafeProbeTransport,
    script: str,
    keys: list[str],
    args: list[object],
) -> dict[str, object]:
    result = transport.eval(script, keys, args)
    if type(result) is not str or len(result.encode("utf-8")) > 524_288:
        raise ProbeError("eval_response_invalid")
    try:
        parsed = json.loads(result)
    except (ValueError, RecursionError):
        raise ProbeError("eval_response_invalid") from None
    if type(parsed) is not dict or type(parsed.get("status")) is not str:
        raise ProbeError("eval_response_invalid")
    return parsed


def _expect_status(value: dict[str, object], status: str, fields: set[str] | None = None) -> None:
    expected = {"status", *(fields or set())}
    if value.get("status") != status or set(value) != expected:
        raise ProbeError("script_result_mismatch")


def _assert_live_ttl(transport: SafeProbeTransport, key: str) -> int:
    ttl = transport.pttl(key)
    if not 1 <= ttl <= MAX_PROBE_TTL_MILLISECONDS:
        raise ProbeError("ttl_out_of_bounds")
    return ttl


def _thread_fixture(collaboration_id: str, *, updated_at: int = 1_800_000_000_000) -> dict:
    record = {
        "v": 2,
        "collaborationId": collaboration_id,
        "ownerEmail": "probe@synthetic.invalid",
        "workspaceId": "wsp_" + ("p" * 22),
        "mailboxId": "probe-mailbox",
        "sourceRef": {"provider": "google", "providerMessageId": "synthetic-probe-source"},
        "sourceMessage": {
            "subject": "Synthetic probe",
            "senderDisplay": "Synthetic sender",
            "fromDisplay": "probe@synthetic.invalid",
            "timestamp": "synthetic",
            "bodyText": "Synthetic probe body",
        },
        "state": "needs_review",
        "messages": [],
        "createdAt": 1_800_000_000_000,
        "updatedAt": updated_at,
    }
    normalized = models.normalize_v2_thread_record(record)
    if normalized is None:
        raise ProbeError("synthetic_fixture_invalid")
    return normalized


def _wire_thread(thread: dict) -> str:
    raw = redis_store._v2_wire_json(thread, "thread")
    if type(raw) is not str:
        raise ProbeError("synthetic_fixture_invalid")
    return raw


@dataclass(frozen=True, slots=True)
class CreatedThread:
    thread: dict
    thread_key: str
    source_key: str


class CompatibilityProbe:
    def __init__(
        self,
        namespace: ProbeNamespace,
        transport_factory: Callable[[], SafeProbeTransport],
        budget: ProbeBudget,
    ) -> None:
        self.namespace = namespace
        self._transport_factory = transport_factory
        self.transport = transport_factory()
        self.budget = budget
        self.results: list[dict[str, str]] = []

    def _case(self, name: str, category: str, callback: Callable[[], None]) -> bool:
        try:
            callback()
        except ProbeError as error:
            self.results.append({"name": name, "category": category, "status": "FAIL", "code": error.code})
            return False
        except Exception:
            self.results.append({"name": name, "category": category, "status": "FAIL", "code": "unexpected_failure"})
            return False
        self.results.append({"name": name, "category": category, "status": "PASS", "code": "ok"})
        return True

    def run(self) -> tuple[bool, bool, str]:
        read_ok = True
        read_ok &= self._case("transport_sanity", "owner_read", self._transport_sanity)
        if read_ok:
            read_ok &= self._case("lua_cjson", "owner_read", self._lua_cjson)
        if read_ok:
            read_ok &= self._case("redis_time", "owner_read", self._redis_time)
        if read_ok:
            read_ok &= self._case("owner_rate_limit", "owner_read", self._rate_limit)
        if read_ok:
            read_ok &= self._case(
                "owner_rate_limit_competing_clients",
                "owner_read",
                self._rate_limit_race,
            )

        write_ok = False
        if read_ok:
            write_ok = self._case("atomic_create", "owner_write", self._atomic_create)
            if write_ok:
                write_ok &= self._case(
                    "create_competing_clients",
                    "owner_write",
                    self._create_race,
                )
            if write_ok:
                write_ok &= self._case(
                    "owner_append_idempotency",
                    "owner_write",
                    self._append_idempotency,
                )
            if write_ok:
                write_ok &= self._case(
                    "owner_append_competing_clients",
                    "owner_write",
                    self._append_race,
                )

        ttl_status = "not_checked"
        try:
            self._final_ttl_audit()
            ttl_status = "bounded_or_expired"
        except ProbeError as error:
            ttl_status = error.code
            read_ok = False
            write_ok = False
            self.results.append(
                {"name": "final_ttl_audit", "category": "safety", "status": "FAIL", "code": error.code}
            )
        return read_ok, write_ok, ttl_status

    def _transport_sanity(self) -> None:
        key = self.namespace.key("transport:value")
        missing = self.namespace.key("transport:missing")
        self.transport.set_px(key, "synthetic", 30_000)
        _assert_live_ttl(self.transport, key)
        if self.transport.get(key) != "synthetic" or self.transport.get(missing) is not None:
            raise ProbeError("transport_result_mismatch")

    def _lua_cjson(self) -> None:
        result = _eval_object(
            self.transport,
            _CJSON_PROBE_LUA,
            [],
            ['{"value":"probe"}'],
        )
        if result != {"status": "ok", "value": "probe"}:
            raise ProbeError("cjson_result_mismatch")

    def _redis_time(self) -> None:
        self.transport.redis_time()

    @staticmethod
    def _rate_args(rate_class: str) -> list[str]:
        policy = owner_rate_limit.owner_rate_limit_policy(rate_class)
        if policy is None:
            raise ProbeError("rate_policy_unavailable")
        return [str(policy.emission_interval_microseconds), str(policy.burst), "128"]

    def _rate(self, key: str, rate_class: str) -> dict[str, object]:
        return _eval_object(
            self.transport,
            owner_rate_limit._OWNER_RATE_LIMIT_LUA,
            [key],
            self._rate_args(rate_class),
        )

    def _rate_limit(self) -> None:
        key = self.namespace.key("rate:read")
        for _ in range(30):
            _expect_status(self._rate(key, owner_rate_limit.RATE_LIMIT_READ), "allowed")
        limited = self._rate(key, owner_rate_limit.RATE_LIMIT_READ)
        _expect_status(limited, "limited", {"retryAfter"})
        retry_after = limited.get("retryAfter")
        if type(retry_after) is not str or retry_after != "1":
            raise ProbeError("rate_retry_invalid")
        _assert_live_ttl(self.transport, key)
        time.sleep(0.60)
        _expect_status(self._rate(key, owner_rate_limit.RATE_LIMIT_READ), "allowed")

        malformed_key = self.namespace.key("rate:malformed")
        self.transport.set_px(malformed_key, "not-json", 1_000)
        _assert_live_ttl(self.transport, malformed_key)
        _expect_status(self._rate(malformed_key, owner_rate_limit.RATE_LIMIT_READ), "malformed")
        if self.transport.get(malformed_key) != "not-json":
            raise ProbeError("malformed_rate_state_changed")

        expiry_key = self.namespace.key("rate:expiry")
        result = _eval_object(
            self.transport,
            owner_rate_limit._OWNER_RATE_LIMIT_LUA,
            [expiry_key],
            ["10000", "1", "128"],
        )
        _expect_status(result, "allowed")
        _assert_live_ttl(self.transport, expiry_key)
        time.sleep(0.06)
        if self.transport.get(expiry_key) is not None:
            raise ProbeError("rate_key_did_not_expire")

    def _run_concurrent(
        self,
        workers: int,
        callback: Callable[[SafeProbeTransport], dict[str, object]],
    ) -> list[dict[str, object]]:
        if not 2 <= workers <= MAX_CONCURRENCY:
            raise ProbeError("invalid_concurrency")
        barrier = threading.Barrier(workers)

        def invoke() -> dict[str, object]:
            transport = self._transport_factory()
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                raise ProbeError("race_barrier_failed") from None
            return callback(transport)

        results: list[dict[str, object]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(invoke) for _ in range(workers)]
            for future in futures:
                try:
                    value = future.result(timeout=25)
                except Exception:
                    raise ProbeError("race_incomplete") from None
                if type(value) is not dict:
                    raise ProbeError("race_result_invalid")
                results.append(value)
        return results

    def _rate_limit_race(self) -> None:
        key = self.namespace.key("rate:race")

        def consume(transport: SafeProbeTransport) -> dict[str, object]:
            return _eval_object(
                transport,
                owner_rate_limit._OWNER_RATE_LIMIT_LUA,
                [key],
                self._rate_args(owner_rate_limit.RATE_LIMIT_BOOTSTRAP),
            )

        results = self._run_concurrent(8, consume)
        for result in results:
            if result.get("status") == "allowed":
                _expect_status(result, "allowed")
            elif result.get("status") == "limited":
                _expect_status(result, "limited", {"retryAfter"})
                retry_after = result.get("retryAfter")
                if (
                    type(retry_after) is not str
                    or _CANONICAL_UINT_RE.fullmatch(retry_after) is None
                    or not 1 <= int(retry_after) <= 60
                ):
                    raise ProbeError("rate_retry_invalid")
            else:
                raise ProbeError("rate_race_mismatch")
        statuses = [result.get("status") for result in results]
        if statuses.count("allowed") != 4 or statuses.count("limited") != 4:
            raise ProbeError("rate_race_mismatch")
        _assert_live_ttl(self.transport, key)

    def _create_eval(
        self,
        thread: dict,
        source_key: str,
        *,
        ttl_seconds: int = 60,
        transport: SafeProbeTransport | None = None,
    ) -> tuple[dict[str, object], str]:
        selected = self.transport if transport is None else transport
        thread_key = self.namespace.key("thread:" + thread["collaborationId"])
        result = _eval_object(
            selected,
            redis_store._CREATE_V2_THREAD_LUA,
            [thread_key, source_key],
            [_wire_thread(thread), thread["collaborationId"], str(ttl_seconds), self.namespace.thread_prefix],
        )
        return result, thread_key

    def _create_fresh(self, label: str, collaboration_id: str) -> CreatedThread:
        thread = _thread_fixture(collaboration_id)
        source_key = self.namespace.key("source:" + label)
        result, thread_key = self._create_eval(thread, source_key)
        _expect_status(result, "created")
        _assert_live_ttl(self.transport, thread_key)
        _assert_live_ttl(self.transport, source_key)
        return CreatedThread(thread, thread_key, source_key)

    def _atomic_create(self) -> None:
        created = self._create_fresh("main", "A" * 22)
        duplicate, _ = self._create_eval(created.thread, created.source_key)
        _expect_status(duplicate, "duplicate", {"collaborationId"})
        if duplicate.get("collaborationId") != created.thread["collaborationId"]:
            raise ProbeError("duplicate_create_mismatch")
        _assert_live_ttl(self.transport, created.thread_key)
        _assert_live_ttl(self.transport, created.source_key)

        conflict_key = self.namespace.key("thread:" + ("B" * 22))
        conflict_source = self.namespace.key("source:conflict")
        self.transport.set_px(conflict_key, "occupied", 30_000)
        _assert_live_ttl(self.transport, conflict_key)
        conflict_thread = _thread_fixture("B" * 22)
        conflict = _eval_object(
            self.transport,
            redis_store._CREATE_V2_THREAD_LUA,
            [conflict_key, conflict_source],
            [_wire_thread(conflict_thread), conflict_thread["collaborationId"], "60", self.namespace.thread_prefix],
        )
        _expect_status(conflict, "conflict")
        if self.transport.get(conflict_source) is not None:
            raise ProbeError("create_conflict_wrote_pointer")

        malformed_thread = {**_thread_fixture("C" * 22), "state": "invalid"}
        malformed = _eval_object(
            self.transport,
            redis_store._CREATE_V2_THREAD_LUA,
            [self.namespace.key("thread:" + ("C" * 22)), self.namespace.key("source:malformed-create")],
            [json.dumps(malformed_thread, separators=(",", ":"), sort_keys=True), "C" * 22, "60", self.namespace.thread_prefix],
        )
        _expect_status(malformed, "malformed")

    def _create_race(self) -> None:
        source_key = self.namespace.key("source:create-race")
        first = _thread_fixture("D" * 22)
        second = _thread_fixture("E" * 22)

        def create(thread: dict) -> Callable[[SafeProbeTransport], dict[str, object]]:
            def invoke(transport: SafeProbeTransport) -> dict[str, object]:
                result, _ = self._create_eval(thread, source_key, transport=transport)
                return result
            return invoke

        callbacks = (create(first), create(second))
        barrier = threading.Barrier(2)

        def invoke(callback):
            transport = self._transport_factory()
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                raise ProbeError("race_barrier_failed") from None
            return callback(transport)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(invoke, callback) for callback in callbacks]
            try:
                results = [future.result(timeout=25) for future in futures]
            except Exception:
                raise ProbeError("race_incomplete") from None
        statuses = [result.get("status") for result in results]
        if statuses.count("created") != 1 or statuses.count("duplicate") != 1:
            raise ProbeError("create_race_mismatch")
        for result in results:
            if result.get("status") == "created":
                _expect_status(result, "created")
            else:
                _expect_status(result, "duplicate", {"collaborationId"})
        winner = self.transport.get(source_key)
        if winner not in {first["collaborationId"], second["collaborationId"]}:
            raise ProbeError("create_pointer_mismatch")
        winner_key = self.namespace.key("thread:" + str(winner))
        loser_id = second["collaborationId"] if winner == first["collaborationId"] else first["collaborationId"]
        loser_key = self.namespace.key("thread:" + loser_id)
        if self.transport.get(winner_key) is None or self.transport.get(loser_key) is not None:
            raise ProbeError("create_race_state_mismatch")
        _assert_live_ttl(self.transport, winner_key)
        _assert_live_ttl(self.transport, source_key)

    @staticmethod
    def _append_replacement(thread: dict, message_id: str, text: str) -> tuple[dict, dict]:
        created_at = thread["updatedAt"] + 1
        message = {
            "id": message_id,
            "authorKind": "owner",
            "authorDisplayName": "Synthetic operator",
            "text": text,
            "visibility": "shared",
            "createdAt": created_at,
        }
        replacement = models.normalize_v2_thread_record(
            {**thread, "messages": [*thread["messages"], message], "updatedAt": created_at}
        )
        if replacement is None:
            raise ProbeError("synthetic_fixture_invalid")
        return replacement, message

    def _append_command(
        self,
        created: CreatedThread,
        idempotency_key: str,
        *,
        message_id: str,
        text: str,
        fingerprint: str,
        transport: SafeProbeTransport | None = None,
    ) -> dict[str, object]:
        selected = self.transport if transport is None else transport
        replacement, message = self._append_replacement(created.thread, message_id, text)
        record = json.dumps(
            {
                "action": "reply",
                "collaborationId": created.thread["collaborationId"],
                "fingerprint": fingerprint,
                "messageId": message["id"],
                "updatedAt": str(message["createdAt"]),
                "v": "1",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return _eval_object(
            selected,
            redis_store._APPEND_V2_OWNER_IDEMPOTENT_LUA,
            [created.thread_key, created.source_key, idempotency_key],
            [
                str(created.thread["updatedAt"]),
                _wire_thread(replacement),
                "60",
                "60",
                fingerprint,
                record,
                created.thread["collaborationId"],
                created.thread["ownerEmail"],
                created.thread["workspaceId"],
                created.thread["mailboxId"],
                "reply",
                "shared",
                message["authorDisplayName"],
                text,
            ],
        )

    @staticmethod
    def _fingerprint(label: str) -> str:
        return hashlib.sha256(("synthetic-probe:" + label).encode("ascii")).hexdigest()

    def _append_idempotency(self) -> None:
        created = CreatedThread(
            _thread_fixture("A" * 22),
            self.namespace.key("thread:" + ("A" * 22)),
            self.namespace.key("source:main"),
        )
        idempotency_key = self.namespace.key("idempotency:main")
        fingerprint = self._fingerprint("main")
        saved = self._append_command(
            created,
            idempotency_key,
            message_id="M" * 22,
            text="Synthetic append",
            fingerprint=fingerprint,
        )
        _expect_status(saved, "saved", {"message", "updatedAt"})
        before = tuple(_assert_live_ttl(self.transport, key) for key in (created.thread_key, created.source_key, idempotency_key))
        recovered = self._append_command(
            created,
            idempotency_key,
            message_id="M" * 22,
            text="Synthetic append",
            fingerprint=fingerprint,
        )
        _expect_status(recovered, "recovered", {"message", "updatedAt"})
        if recovered.get("message") != saved.get("message") or recovered.get("updatedAt") != saved.get("updatedAt"):
            raise ProbeError("idempotency_recovery_mismatch")
        after = tuple(_assert_live_ttl(self.transport, key) for key in (created.thread_key, created.source_key, idempotency_key))
        if any(later > earlier for earlier, later in zip(before, after)):
            raise ProbeError("recovery_refreshed_ttl")

        conflict = self._append_command(
            created,
            idempotency_key,
            message_id="M" * 22,
            text="Different synthetic append",
            fingerprint=self._fingerprint("conflict"),
        )
        _expect_status(conflict, "idempotency_conflict")

        stale_key = self.namespace.key("idempotency:stale")
        stale = self._append_command(
            created,
            stale_key,
            message_id="N" * 22,
            text="Stale synthetic append",
            fingerprint=self._fingerprint("stale"),
        )
        _expect_status(stale, "stale")

        lost = self._create_fresh("lost", "F" * 22)
        lost_key = self.namespace.key("idempotency:lost")
        ignored = self._append_command(
            lost,
            lost_key,
            message_id="O" * 22,
            text="Lost response append",
            fingerprint=self._fingerprint("lost"),
        )
        _expect_status(ignored, "saved", {"message", "updatedAt"})
        recovered_lost = self._append_command(
            lost,
            lost_key,
            message_id="O" * 22,
            text="Lost response append",
            fingerprint=self._fingerprint("lost"),
        )
        _expect_status(recovered_lost, "recovered", {"message", "updatedAt"})
        if recovered_lost.get("message") != ignored.get("message"):
            raise ProbeError("lost_response_recovery_mismatch")

        malformed = self._create_fresh("malformed-append", "G" * 22)
        malformed_key = self.namespace.key("idempotency:malformed")
        self.transport.set_px(malformed_key, "not-json", 30_000)
        _assert_live_ttl(self.transport, malformed_key)
        malformed_result = self._append_command(
            malformed,
            malformed_key,
            message_id="P" * 22,
            text="Malformed record append",
            fingerprint=self._fingerprint("malformed"),
        )
        _expect_status(malformed_result, "idempotency_malformed")
        if self.transport.get(malformed_key) != "not-json":
            raise ProbeError("malformed_idempotency_changed")

    def _append_race(self) -> None:
        created = self._create_fresh("append-race", "H" * 22)
        idempotency_key = self.namespace.key("idempotency:race")
        fingerprint = self._fingerprint("race")

        def append(transport: SafeProbeTransport) -> dict[str, object]:
            return self._append_command(
                created,
                idempotency_key,
                message_id="Q" * 22,
                text="Concurrent synthetic append",
                fingerprint=fingerprint,
                transport=transport,
            )

        results = self._run_concurrent(6, append)
        for result in results:
            if result.get("status") == "saved":
                _expect_status(result, "saved", {"message", "updatedAt"})
            elif result.get("status") == "recovered":
                _expect_status(result, "recovered", {"message", "updatedAt"})
            else:
                raise ProbeError("append_race_mismatch")
        statuses = [result.get("status") for result in results]
        if statuses.count("saved") != 1 or statuses.count("recovered") != 5:
            raise ProbeError("append_race_mismatch")
        messages = [json.dumps(result.get("message"), sort_keys=True) for result in results]
        if len(set(messages)) != 1:
            raise ProbeError("append_race_result_mismatch")
        _assert_live_ttl(self.transport, created.thread_key)
        _assert_live_ttl(self.transport, created.source_key)
        _assert_live_ttl(self.transport, idempotency_key)

    def _final_ttl_audit(self) -> None:
        for key in self.namespace.registered_keys():
            ttl = self.transport.pttl(key)
            if ttl == -2:
                continue
            if not 1 <= ttl <= MAX_PROBE_TTL_MILLISECONDS:
                raise ProbeError("ttl_out_of_bounds")


class _RespClient:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    def transport(self, command: list[object]) -> dict[str, object]:
        return {"result": self.command(command)}

    def command(self, command: list[object]) -> object:
        payload = [item if isinstance(item, bytes) else str(item).encode("utf-8") for item in command]
        encoded = [f"*{len(payload)}\r\n".encode("ascii")]
        for item in payload:
            encoded.extend((f"${len(item)}\r\n".encode("ascii"), item, b"\r\n"))
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(self.socket_path)
            connection.sendall(b"".join(encoded))
            stream = connection.makefile("rb")
            return self._read(stream)

    @classmethod
    def _read(cls, stream) -> object:
        prefix = stream.read(1)
        line = stream.readline()
        if not prefix or not line.endswith(b"\r\n"):
            raise ProbeError("local_redis_response_invalid")
        value = line[:-2]
        if prefix == b"+":
            return value.decode("utf-8")
        if prefix == b"-":
            raise ProbeError("local_redis_error")
        if prefix == b":":
            return int(value)
        if prefix == b"$":
            length = int(value)
            if length == -1:
                return None
            data = stream.read(length)
            if stream.read(2) != b"\r\n":
                raise ProbeError("local_redis_response_invalid")
            return data.decode("utf-8")
        if prefix == b"*":
            return [cls._read(stream) for _ in range(int(value))]
        raise ProbeError("local_redis_response_invalid")


class LocalRedisServer:
    """Fresh, isolated Redis process used only by explicit local mode."""

    def __init__(self, executable: str = "/usr/local/bin/redis-server") -> None:
        self.executable = executable
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._process: subprocess.Popen | None = None
        self.socket_path = ""

    def __enter__(self) -> "LocalRedisServer":
        if not os.path.isfile(self.executable) or not os.access(self.executable, os.X_OK):
            raise ProbeError("local_redis_unavailable")
        self._tempdir = tempfile.TemporaryDirectory(prefix="cuevion-kv-probe-", dir="/tmp")
        self.socket_path = os.path.join(self._tempdir.name, "redis.sock")
        self._process = subprocess.Popen(
            [
                self.executable,
                "--port",
                "0",
                "--unixsocket",
                self.socket_path,
                "--unixsocketperm",
                "700",
                "--dir",
                self._tempdir.name,
                "--save",
                "",
                "--appendonly",
                "no",
                "--protected-mode",
                "yes",
                "--loglevel",
                "warning",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 5
        while (
            not os.path.exists(self.socket_path)
            and self._process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        if not os.path.exists(self.socket_path):
            self.__exit__(None, None, None)
            raise ProbeError("local_redis_unavailable")
        return self

    def transport(self) -> RawTransport:
        if not self.socket_path:
            raise ProbeError("local_redis_unavailable")
        return _RespClient(self.socket_path).transport

    def __exit__(self, _type, _value, _traceback) -> None:
        try:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
        finally:
            if self._tempdir is not None:
                self._tempdir.cleanup()


@dataclass(frozen=True, slots=True)
class RemoteConfiguration:
    rest_url: str
    rest_token: str = field(repr=False)


def resolve_remote_configuration(environment: Mapping[str, str]) -> RemoteConfiguration:
    try:
        raw_url = environment[KV_URL_ENV]
        token = environment[KV_TOKEN_ENV]
        parsed = urlsplit(raw_url)
    except Exception:
        raise ProbeError("remote_configuration_invalid") from None
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
        or parsed.path not in {"", "/"}
    ):
        raise ProbeError("remote_configuration_invalid")
    return RemoteConfiguration(raw_url.rstrip("/"), token)


def build_remote_transport(configuration: RemoteConfiguration) -> RawTransport:
    config = {"rest_url": configuration.rest_url, "rest_token": configuration.rest_token}

    def perform(command: list[object]) -> dict[str, object]:
        result = redis_store._perform_v2_rest_command(config, command)
        return result

    return perform


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        value = result.stdout.strip()
    except Exception:
        value = ""
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else "unknown"


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _dry_run_report(namespace: ProbeNamespace) -> dict[str, object]:
    return {
        "probeVersion": PROBE_VERSION,
        "gitCommit": _git_commit(),
        "mode": "dry_run",
        "runId": namespace.run_id,
        "namespace": namespace.prefix,
        "networkOperations": 0,
        "kvWrites": 0,
        "credentials": "not_read",
        "plannedOwnerReadCases": list(OWNER_READ_CASES),
        "plannedOwnerWriteCases": list(OWNER_WRITE_CASES),
        "limits": _reported_limits(),
        "ownerReadVerdict": "INCONCLUSIVE",
        "ownerWriteVerdict": "INCONCLUSIVE",
}


def _reported_limits() -> dict[str, int]:
    return {
        "maximumTtlSeconds": MAX_PROBE_TTL_SECONDS,
        "maximumKeys": MAX_PROBE_KEYS,
        "maximumCommands": MAX_REMOTE_COMMANDS,
        "maximumEvalCalls": MAX_REMOTE_EVAL_CALLS,
        "maximumConcurrency": MAX_CONCURRENCY,
        "remoteRuntimeTargetSeconds": REMOTE_RUNTIME_TARGET_SECONDS,
    }


def _run_report(
    mode: str,
    namespace: ProbeNamespace,
    raw_transport_factory: Callable[[], RawTransport],
) -> dict[str, object]:
    started_utc = _utc_now()
    started_monotonic = time.monotonic()
    budget = ProbeBudget(started_monotonic, remote=mode == "remote")

    def safe_factory() -> SafeProbeTransport:
        return SafeProbeTransport(namespace, budget, raw_transport_factory())

    probe = CompatibilityProbe(namespace, safe_factory, budget)
    read_ok = False
    write_ok = False
    ttl_status = "not_checked"
    cleanup_status = "ttl_only"
    try:
        read_ok, write_ok, ttl_status = probe.run()
    finally:
        try:
            safe_factory().cleanup(namespace.registered_keys())
            cleanup_status = "explicit_cleanup_succeeded_with_ttl_fallback"
        except ProbeError:
            cleanup_status = "explicit_cleanup_inconclusive_ttl_fallback_active"
            read_ok = False
            write_ok = False

    failures = [result["name"] for result in probe.results if result["status"] != "PASS"]
    if cleanup_status.startswith("explicit_cleanup_inconclusive"):
        failures.append("cleanup")
    return {
        "probeVersion": PROBE_VERSION,
        "gitCommit": _git_commit(),
        "mode": mode,
        "runId": namespace.run_id,
        "utcStart": started_utc,
        "utcEnd": _utc_now(),
        "transportResults": probe.results,
        "ownerReadVerdict": "OWNER_READ_KV_COMPATIBLE" if read_ok else "INCOMPATIBLE",
        "ownerWriteVerdict": (
            "OWNER_WRITE_KV_COMPATIBLE"
            if write_ok
            else "INCOMPATIBLE" if read_ok else "INCONCLUSIVE"
        ),
        "failedTests": failures,
        "responseTypes": {
            name: sorted(values) for name, values in sorted(budget.result_types.items())
        },
        "keyCount": len(namespace.registered_keys()),
        "commandCount": budget.command_count,
        "evalCount": budget.eval_count,
        "maximumConcurrency": MAX_CONCURRENCY,
        "limits": _reported_limits(),
        "ttlStatus": ttl_status,
        "cleanupStatus": cleanup_status,
    }


def _safe_error_report(mode: str, run_id: str, code: str) -> dict[str, object]:
    return {
        "probeVersion": PROBE_VERSION,
        "gitCommit": _git_commit(),
        "mode": mode,
        "runId": run_id,
        "ownerReadVerdict": "INCONCLUSIVE",
        "ownerWriteVerdict": "INCONCLUSIVE",
        "failedTests": [code],
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collaboration v2 KV compatibility probe")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--local", action="store_true", help="run against a fresh local Redis")
    modes.add_argument(
        "--execute-remote",
        action="store_true",
        help="execute against explicitly supplied remote KV credentials",
    )
    return parser


def main(argv: list[str] | None = None, *, environment: Mapping[str, str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argv)
    run_id = generate_run_id()
    namespace = ProbeNamespace(run_id)
    mode = "dry_run"
    try:
        if not arguments.local and not arguments.execute_remote:
            print(json.dumps(_dry_run_report(namespace), sort_keys=True))
            return 0

        if arguments.local:
            mode = "local"
            with LocalRedisServer() as server:
                report = _run_report(mode, namespace, server.transport)
        else:
            mode = "remote"
            source = os.environ if environment is None else environment
            try:
                confirmation = source.get(REMOTE_CONFIRM_ENV)
            except Exception:
                raise ProbeError("remote_not_armed") from None
            if confirmation != REMOTE_CONFIRM_VALUE:
                raise ProbeError("remote_not_armed")
            configuration = resolve_remote_configuration(source)
            report = _run_report(
                mode,
                namespace,
                lambda: build_remote_transport(configuration),
            )
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ownerWriteVerdict"] == "OWNER_WRITE_KV_COMPATIBLE" else 2
    except ProbeError as error:
        print(json.dumps(_safe_error_report(mode, run_id, error.code), sort_keys=True))
        return 2
    except Exception:
        print(json.dumps(_safe_error_report(mode, run_id, "unexpected_failure"), sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
