from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from . import authorization, guest_session, mutations, redis_store, source_message
from .models import (
    build_v2_guest_thread_dto,
    decode_v2_wire_record,
    encode_v2_wire_record,
    hash_v2_secret,
    normalize_v2_email,
    normalize_v2_source_message,
    normalize_v2_thread_record,
)

SEC = 1_800_000_000
MS = SEC * 1000
MAX_RAW_RECORD_BYTES = 262_144
TTL_OBSERVATION_TOLERANCE_SECONDS = 1
PTTL_OBSERVATION_TOLERANCE_MS = 2_000
PTTL_MEASUREMENT_JITTER_MS = 100


def thread_record() -> dict:
    return {
        "v": 2,
        "collaborationId": "A" * 22,
        "ownerEmail": "owner@example.com",
        "workspaceId": "owner@example.com",
        "mailboxId": "mailbox-1",
        "sourceRef": {"provider": "google", "providerMessageId": "gmail-1"},
        "sourceMessage": {
            "subject": "Review",
            "senderDisplay": "Sender",
            "fromDisplay": "sender@example.com",
            "timestamp": "today",
            "bodyText": "Body\nwith a line break",
        },
        "state": "needs_review",
        "messages": [],
        "createdAt": MS + 100,
        "updatedAt": MS + 100,
    }


def custom_imap_thread_record() -> dict:
    return {
        **thread_record(),
        "sourceRef": {
            "provider": "custom_imap",
            "folder": "INBOX",
            "uidValidity": "77",
            "imapUid": "9",
        },
    }


def invite_record(raw_token: str = "t" * 43) -> dict:
    return {
        "v": 2,
        "inviteId": "I" * 22,
        "tokenHash": hash_v2_secret(raw_token),
        "ownerEmail": "owner@example.com",
        "workspaceId": "owner@example.com",
        "mailboxId": "mailbox-1",
        "collaborationId": "A" * 22,
        "identityAssurance": "link_possession",
        "allowedActions": ["read", "reply"],
        "visibility": "shared_only",
        "createdBy": {"ownerEmail": "owner@example.com", "displayName": "Owner"},
        "createdAt": SEC + 100,
        "expiresAt": SEC + 200,
        "status": "active",
        "exchangedAt": None,
        "exchangeCount": 0,
        "revokedAt": None,
        "revokedBy": None,
    }


def session_record(secret: str) -> dict:
    return {
        "v": 2,
        "sessionHash": hash_v2_secret(secret),
        "csrfTokenHash": hash_v2_secret("c" * 43),
        "inviteId": "I" * 22,
        "ownerEmail": "owner@example.com",
        "workspaceId": "owner@example.com",
        "mailboxId": "mailbox-1",
        "collaborationId": "A" * 22,
        "allowedActions": ["read", "reply"],
        "visibility": "shared_only",
        "identityAssurance": "link_possession",
        "guestDisplayName": "Guest",
        "createdAt": SEC + 101,
        "lastUsedAt": SEC + 101,
        "expiresAt": SEC + 150,
        "status": "active",
        "revokedAt": None,
        "loggedOutAt": None,
    }


def revoke_guest_session(session: dict, *, now: int, command_transport) -> dict:
    return redis_store._revoke_v2_guest_session(
        session["sessionHash"],
        invite_id=session["inviteId"],
        owner_email=session["ownerEmail"],
        workspace_id=session["workspaceId"],
        mailbox_id=session["mailboxId"],
        collaboration_id=session["collaborationId"],
        now=now,
        command_transport=command_transport,
    )


def message_record(index: int = 1, *, text: str = "message", created_at: int = MS + 101) -> dict:
    return {
        "id": f"{index:022d}",
        "authorKind": "owner",
        "authorDisplayName": "Owner",
        "text": text,
        "visibility": "internal",
        "createdAt": created_at,
    }


def compact_json(value: dict) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def wire_json(value: dict, record_kind: str) -> str:
    encoded = encode_v2_wire_record(value, record_kind)
    if encoded is None:
        raise AssertionError(f"invalid typed {record_kind} fixture")
    return compact_json(encoded)


def typed_wire_json(raw: str, record_kind: str) -> dict:
    decoded = decode_v2_wire_record(json.loads(raw), record_kind)
    if decoded is None:
        raise AssertionError(f"invalid stored {record_kind} wire record")
    return decoded


def wire_thread_with_messages(thread: dict, messages: object) -> str:
    wire = json.loads(wire_json({**thread, "messages": []}, "thread"))

    def convert(value):
        if isinstance(value, dict):
            converted = {key: convert(entry) for key, entry in value.items()}
            if type(converted.get("createdAt")) is int:
                converted["createdAt"] = str(converted["createdAt"])
            return converted
        if isinstance(value, list):
            return [convert(entry) for entry in value]
        return value

    wire["messages"] = convert(messages)
    return compact_json(wire)


def pad_json(raw: str, size: int) -> str:
    if len(raw.encode("utf-8")) > size:
        raise AssertionError(f"JSON is already larger than requested size: {len(raw)} > {size}")
    return raw + (" " * (size - len(raw.encode("utf-8"))))


def wire_lexical_variants(raw: str) -> tuple[tuple[str, str], ...]:
    marker = '"v":"2"'
    if raw.count(marker) != 1:
        raise AssertionError("wire fixture must contain one canonical version field")
    variants = [
        (f"numeric_{token}", raw.replace(marker, f'"v":{token}', 1))
        for token in ("2", "2.0", "2e0", "-0", "02", "true", "false")
    ]
    variants.extend(
        (
            f"quoted_{token}",
            raw.replace(marker, f'"v":"{token}"', 1),
        )
        for token in ("2.0", "2e0", "-0", "02", "+2")
    )
    variants.extend(
        (
            ("duplicate_literal", raw.replace(marker, marker + ',"v":"2"', 1)),
            ("duplicate_escaped", raw.replace(marker, marker + ',"\\u0076":"2"', 1)),
        )
    )
    return tuple(variants)


class _RespClient:
    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    def command(self, command: list):
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

    def transport(self, command: list) -> dict:
        return {"result": self.command(command)}

    @classmethod
    def _read(cls, stream):
        prefix = stream.read(1)
        line = stream.readline()
        if not prefix or not line.endswith(b"\r\n"):
            raise RuntimeError("truncated Redis response")
        value = line[:-2]
        if prefix == b"+":
            return value.decode("utf-8")
        if prefix == b"-":
            raise RuntimeError(value.decode("utf-8", "replace"))
        if prefix == b":":
            return int(value)
        if prefix == b"$":
            length = int(value)
            if length == -1:
                return None
            data = stream.read(length)
            if stream.read(2) != b"\r\n":
                raise RuntimeError("truncated Redis bulk response")
            return data.decode("utf-8")
        if prefix == b"*":
            return [cls._read(stream) for _ in range(int(value))]
        raise RuntimeError("unknown Redis response")


class _PttlSample(tuple):
    def __new__(cls, values, sampled_at):
        sample = super().__new__(cls, values)
        sample.sampled_at = tuple(sampled_at)
        return sample


class ProductionLuaRedisIntegrationTests(unittest.TestCase):
    """Authoritative only when an installed local Redis/Valkey server is available."""

    @classmethod
    def setUpClass(cls):
        cls.server = "/usr/local/bin/redis-server"
        if not os.path.isfile(cls.server) or not os.access(cls.server, os.X_OK):
            raise RuntimeError("authoritative Lua tests require /usr/local/bin/redis-server")
        cls.tempdir = tempfile.TemporaryDirectory(prefix="cuevion-v2-redis-", dir="/tmp")
        cls.socket_path = os.path.join(cls.tempdir.name, "redis.sock")
        cls.process = subprocess.Popen(
            [
                cls.server,
                "--port", "0",
                "--unixsocket", cls.socket_path,
                "--unixsocketperm", "700",
                "--dir", cls.tempdir.name,
                "--save", "",
                "--appendonly", "no",
                "--protected-mode", "yes",
                "--loglevel", "warning",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 5
        while not os.path.exists(cls.socket_path) and cls.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not os.path.exists(cls.socket_path):
            cls.process.terminate()
            cls.process.wait(timeout=5)
            cls.tempdir.cleanup()
            raise RuntimeError("isolated /usr/local/bin/redis-server failed to start")
        cls.client = _RespClient(cls.socket_path)

    @classmethod
    def tearDownClass(cls):
        try:
            if getattr(cls, "process", None) is not None and cls.process.poll() is None:
                cls.process.terminate()
                try:
                    cls.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    cls.process.kill()
                    cls.process.wait(timeout=5)
        finally:
            if getattr(cls, "tempdir", None) is not None:
                cls.tempdir.cleanup()

    def setUp(self):
        self.client.command(["FLUSHALL"])
        encoded_key = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")
        self.environment = patch.dict(
            os.environ,
            {redis_store.V2_INDEX_HMAC_ENV: encoded_key},
            clear=False,
        )
        self.environment.start()
        os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)

    def tearDown(self):
        self.environment.stop()

    def _source_key(self, thread: dict, *, hmac_key: bytes | None = None) -> str:
        key = redis_store.build_v2_source_thread_key(
            thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"], hmac_key=hmac_key
        )
        self.assertIsNotNone(key)
        return key

    def _thread_key(self, collaboration_id: str) -> str:
        key = redis_store.build_v2_thread_key(collaboration_id)
        self.assertIsNotNone(key)
        return key

    def _invite_keys(self, invite: dict, *, hmac_key: bytes | None = None) -> tuple[str, str, str]:
        invite_key = redis_store.build_v2_invite_key(invite["inviteId"])
        token_key = redis_store.build_v2_invite_token_key(invite["tokenHash"])
        identity_key = redis_store.build_v2_thread_invite_key(
            invite["ownerEmail"], invite["collaborationId"], invite.get("invitedEmail"), hmac_key=hmac_key
        )
        self.assertIsNotNone(invite_key)
        self.assertIsNotNone(token_key)
        self.assertIsNotNone(identity_key)
        return invite_key, token_key, identity_key

    def _put_invitation_graph(self, invite: dict, identity_key: str, *, ttl: int = 90):
        """Corruption-only graph seeding for deliberately inconsistent links."""
        invite_key = redis_store.build_v2_invite_key(invite["inviteId"])
        token_key = redis_store.build_v2_invite_token_key(invite["tokenHash"])
        self.assertIsNotNone(invite_key)
        self.assertIsNotNone(token_key)
        self.client.command(["SET", invite_key, wire_json(invite, "invite"), "EX", ttl])
        self.client.command(["SET", token_key, invite["inviteId"], "EX", ttl])
        self.client.command(["SET", identity_key, wire_json(invite, "invite"), "EX", ttl])
        return invite_key, token_key, identity_key

    def _duplicate_invite_proposal(
        self,
        canonical: dict,
        *,
        invite_id: str = "J" * 22,
        raw_token: str = "u" * 43,
    ) -> dict:
        return {
            **canonical,
            "inviteId": invite_id,
            "tokenHash": hash_v2_secret(raw_token),
        }

    def _session_key(self, session: dict) -> str:
        key = redis_store.build_v2_guest_session_key(session["sessionHash"])
        self.assertIsNotNone(key)
        return key

    @staticmethod
    def _sample_pttls(client: _RespClient, *keys: str) -> _PttlSample:
        values = []
        sampled_at = []
        for key in keys:
            values.append(client.command(["PTTL", key]))
            sampled_at.append(time.monotonic())
        return _PttlSample(values, sampled_at)

    def _pttls(self, *keys: str) -> _PttlSample:
        return self._sample_pttls(self.client, *keys)

    def _assert_retention_pair(self, thread_key: str, source_key: str):
        thread_ttl = self.client.command(["TTL", thread_key])
        source_ttl = self.client.command(["TTL", source_key])
        retention = redis_store.V2_THREAD_RETENTION_SECONDS
        self.assertGreater(thread_ttl, 0)
        self.assertGreater(source_ttl, 0)
        self.assertLessEqual(abs(thread_ttl - source_ttl), TTL_OBSERVATION_TOLERANCE_SECONDS)
        self.assertGreaterEqual(thread_ttl, retention - TTL_OBSERVATION_TOLERANCE_SECONDS)
        self.assertGreaterEqual(source_ttl, retention - TTL_OBSERVATION_TOLERANCE_SECONDS)
        self.assertLessEqual(thread_ttl, retention)
        self.assertLessEqual(source_ttl, retention)

    def _assert_ttl_ceiling(self, key: str, ceiling_seconds: int):
        pttl = self.client.command(["PTTL", key])
        self.assertGreater(pttl, 0)
        self.assertLessEqual(pttl, ceiling_seconds * 1000)
        self.assertGreaterEqual(pttl, ceiling_seconds * 1000 - PTTL_OBSERVATION_TOLERANCE_MS)

    def _assert_ttls_not_refreshed(self, before: tuple[int, ...], after: tuple[int, ...]):
        self.assertTrue(before, "TTL no-refresh assertion requires a nonempty key snapshot")
        self.assertIsInstance(before, _PttlSample)
        self.assertIsInstance(after, _PttlSample)
        self.assertEqual(len(before), len(after))
        for index, (previous, current) in enumerate(zip(before, after)):
            if previous < 0:
                self.assertEqual(current, previous)
            else:
                elapsed_ms = max(
                    0,
                    int((after.sampled_at[index] - before.sampled_at[index]) * 1000) + 1,
                )
                self.assertLessEqual(current, previous)
                self.assertGreaterEqual(
                    current,
                    previous - elapsed_ms - PTTL_MEASUREMENT_JITTER_MS,
                )

    def _snapshot_v2_state(self) -> tuple[tuple[str, ...], tuple[str, ...], tuple[int, ...]]:
        keys = tuple(sorted(self.client.command(["KEYS", f"{redis_store.V2_KEY_PREFIX}:*"])))
        self.assertTrue(keys, "v2 no-write assertion captured an empty keyset")
        values = tuple(self.client.command(["GET", key]) for key in keys)
        pttls = self._pttls(*keys)
        return keys, values, pttls

    def _assert_v2_state_unchanged(
        self,
        before: tuple[tuple[str, ...], tuple[str, ...], tuple[int, ...]],
    ) -> None:
        before_keys, before_values, before_pttls = before
        after_keys = tuple(sorted(self.client.command(["KEYS", f"{redis_store.V2_KEY_PREFIX}:*"])))
        self.assertEqual(after_keys, before_keys)
        self.assertEqual(
            tuple(self.client.command(["GET", key]) for key in after_keys),
            before_values,
        )
        self._assert_ttls_not_refreshed(before_pttls, self._pttls(*after_keys))

    def _snapshot_v2_typed_state(self):
        keys = tuple(sorted(self.client.command(["KEYS", f"{redis_store.V2_KEY_PREFIX}:*"])))
        self.assertTrue(keys, "typed no-write assertion captured an empty keyset")
        entries = []
        for key in keys:
            kind = self.client.command(["TYPE", key])
            if kind == "string":
                raw = self.client.command(["GET", key])
            elif kind == "list":
                raw = tuple(self.client.command(["LRANGE", key, 0, -1]))
            elif kind == "set":
                raw = tuple(sorted(self.client.command(["SMEMBERS", key])))
            elif kind == "hash":
                flattened = self.client.command(["HGETALL", key])
                raw = tuple(sorted(zip(flattened[0::2], flattened[1::2])))
            else:
                raise AssertionError(f"unsupported test Redis type {kind!r}")
            entries.append((key, kind, raw))
        return tuple(entries), self._pttls(*keys)

    def _assert_v2_typed_state_unchanged(self, before) -> None:
        before_entries, before_pttls = before
        after_entries, after_pttls = self._snapshot_v2_typed_state()
        self.assertEqual(after_entries, before_entries)
        self._assert_ttls_not_refreshed(before_pttls, after_pttls)

    def _corrupt_key_type(self, key: str, kind: str, *, ttl_ms: int = 60_000) -> None:
        """Install an intentionally corrupted wrong-type security key."""
        self.client.command(["DEL", key])
        if kind == "list":
            self.client.command(["RPUSH", key, "wrong-list-value"])
        elif kind == "set":
            self.client.command(["SADD", key, "wrong-set-value"])
        elif kind == "hash":
            self.client.command(["HSET", key, "wrong-field", "wrong-hash-value"])
        elif kind == "integer_string":
            self.client.command(["SET", key, "7"])
        else:
            raise AssertionError(f"unsupported corruption type {kind}")
        self.client.command(["PEXPIRE", key, ttl_ms])

    def _transport_mutating_eval(self, script: str, mutate):
        def transport(command):
            if command[0] == "EVAL" and command[1] == script:
                changed = list(command)
                mutate(changed, 3 + int(changed[2]))
                command = changed
            return self.client.transport(command)

        return transport

    def _put_thread(self, thread: dict, source_key: str, *, ttl: int = 120, raw: str | None = None):
        thread_key = self._thread_key(thread["collaborationId"])
        if raw is None:
            created = redis_store._create_v2_thread(
                thread, command_transport=self.client.transport
            )
            self.assertEqual(created.get("status"), "ok", created)
            self.assertTrue(created.get("created"), created)
            self.assertEqual(self._source_key(thread), source_key)
            self.client.command(["EXPIRE", thread_key, ttl])
            self.client.command(["EXPIRE", source_key, ttl])
        else:
            # Corruption/boundary setup: these exact raw bytes cannot be emitted
            # by the production normalizer and are the subject under test.
            self.client.command(["SET", thread_key, raw, "EX", ttl])
            self.client.command(["SET", source_key, thread["collaborationId"], "EX", ttl])
        return thread_key

    def _create_exchanged_invitation(
        self,
        *,
        raw_token: str = "t" * 43,
        secret: str = "s" * 43,
        invite: dict | None = None,
        session: dict | None = None,
    ) -> tuple[dict, dict, tuple[str, str, str], str]:
        invite = invite or invite_record(raw_token)
        session = session or session_record(secret)
        created = redis_store._create_v2_invite(
            invite, now=invite["createdAt"], command_transport=self.client.transport
        )
        self.assertEqual(created["status"], "ok")
        exchanged = redis_store._atomic_exchange_v2_invite(
            raw_token=raw_token,
            invite_id=invite["inviteId"],
            session_record=session,
            now=session["createdAt"],
            session_ttl=session["expiresAt"] - session["createdAt"],
            command_transport=self.client.transport,
        )
        self.assertEqual(exchanged, {"status": "ok"})
        return invite, session, self._invite_keys(invite), self._session_key(session)

    def _guest_mutation_capability(
        self,
        *,
        raw_session_id: str = "s" * 43,
        now: int = SEC + 102,
        client: _RespClient | None = None,
    ):
        headers = [
            ("Origin", "https://app.cuevion.test"),
            ("Content-Type", "application/json"),
            (guest_session.CSRF_HEADER_NAME, "c" * 43),
            (
                "Cookie",
                f"{guest_session.GUEST_SESSION_COOKIE_NAME}={raw_session_id}",
            ),
        ]
        with patch.dict(
            os.environ,
            {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.test"},
            clear=False,
        ):
            resolved = guest_session.resolve_guest_v2_mutation_context(
                "POST",
                headers,
                now=now,
                command_transport=(client or self.client).transport,
            )
        self.assertEqual(resolved.get("status"), "ok", resolved)
        return resolved["context"]

    def _guest_replacement(self, thread: dict, *, created_at: int = MS + 101) -> dict:
        message = {
            "id": "G" * 22,
            "authorKind": "guest",
            "authorDisplayName": "Guest",
            "text": "Guest reply",
            "visibility": "shared",
            "createdAt": created_at,
        }
        return {**thread, "messages": [*thread["messages"], message], "updatedAt": created_at}

    def test_python_and_production_lua_wire_schema_decisions_are_identical(self):
        normalizers = {
            "thread": redis_store.normalize_v2_thread_record,
            "invite": redis_store.normalize_v2_invite_record,
            "session": guest_session.normalize_v2_guest_session_record,
        }

        def valid_raw(value: dict, kind: str) -> str:
            return wire_json(value, kind)

        def mutated_raw(value: dict, kind: str, mutate) -> str:
            wire = json.loads(wire_json(value, kind))
            mutate(wire)
            return compact_json(wire)

        base_thread = thread_record()
        base_invite = invite_record()
        base_session = session_record("s" * 43)
        aggregate_multibyte_thread = {
            **base_thread,
            "messages": [
                message_record(index, text="é" * 8192)
                for index in range(1, 16)
            ],
        }
        aggregate_over_limit_thread = {
            **base_thread,
            "messages": [
                message_record(index, text="é" * 8192)
                for index in range(1, 17)
            ],
        }
        aggregate_wire = encode_v2_wire_record(
            aggregate_multibyte_thread, "thread"
        )
        aggregate_over_wire = encode_v2_wire_record(
            aggregate_over_limit_thread, "thread"
        )
        self.assertIsNotNone(aggregate_wire)
        self.assertIsNotNone(aggregate_over_wire)
        aggregate_raw = compact_json(aggregate_wire)
        aggregate_over_raw = compact_json(aggregate_over_wire)
        self.assertLessEqual(len(aggregate_raw.encode("utf-8")), MAX_RAW_RECORD_BYTES)
        self.assertGreater(
            len(aggregate_over_raw.encode("utf-8")), MAX_RAW_RECORD_BYTES
        )
        canonical_thread_raw = valid_raw(base_thread, "thread")
        escaped_messages_raw = canonical_thread_raw.replace(
            '"messages":[]', '"\\u006dessages":[]', 1
        )
        fully_escaped_messages_raw = canonical_thread_raw.replace(
            '"messages":[]', '"\\u006d\\u0065ssages":[]', 1
        )
        cases: list[tuple[str, str, str, bool]] = [
            ("valid_thread", "thread", canonical_thread_raw, True),
            (
                "deeply_nested_thread_fails_closed",
                "thread",
                '{"v":"2","messages":'
                + ("[" * 1200)
                + "null"
                + ("]" * 1200)
                + "}",
                False,
            ),
            ("escaped_top_level_messages", "thread", escaped_messages_raw, True),
            (
                "fully_escaped_top_level_messages",
                "thread",
                fully_escaped_messages_raw,
                True,
            ),
            (
                "duplicate_escaped_messages_alias",
                "thread",
                canonical_thread_raw.replace(
                    '"messages":[]',
                    '"messages":[],"\\u006dessages":[]',
                    1,
                ),
                False,
            ),
            (
                "escaped_messages_nonarray",
                "thread",
                canonical_thread_raw.replace(
                    '"messages":[]', '"\\u006dessages":{}', 1
                ),
                False,
            ),
            ("valid_invite", "invite", valid_raw(base_invite, "invite"), True),
            ("valid_session", "session", valid_raw(base_session, "session"), True),
            (
                "aggregate_multibyte_thread_under_utf8_limit",
                "thread",
                aggregate_raw,
                True,
            ),
            (
                "aggregate_multibyte_thread_over_utf8_limit",
                "thread",
                aggregate_over_raw,
                False,
            ),
            (
                "minimum_opaque_id",
                "thread",
                mutated_raw(base_thread, "thread", lambda value: value.__setitem__("collaborationId", "a" * 22)),
                True,
            ),
            (
                "maximum_opaque_id",
                "thread",
                mutated_raw(base_thread, "thread", lambda value: value.__setitem__("collaborationId", "a" * 128)),
                True,
            ),
            (
                "empty_opaque_id",
                "thread",
                mutated_raw(base_thread, "thread", lambda value: value.__setitem__("collaborationId", "")),
                False,
            ),
            (
                "short_opaque_id",
                "thread",
                mutated_raw(base_thread, "thread", lambda value: value.__setitem__("collaborationId", "a" * 21)),
                False,
            ),
            (
                "long_opaque_id",
                "thread",
                mutated_raw(base_thread, "thread", lambda value: value.__setitem__("collaborationId", "a" * 129)),
                False,
            ),
            (
                "unicode_opaque_id",
                "thread",
                mutated_raw(base_thread, "thread", lambda value: value.__setitem__("collaborationId", "a" * 21 + "é")),
                False,
            ),
            (
                "whitespace_opaque_id",
                "thread",
                mutated_raw(base_thread, "thread", lambda value: value.__setitem__("collaborationId", "a" * 21 + " ")),
                False,
            ),
            (
                "internal_space_provider_id",
                "thread",
                mutated_raw(
                    base_thread,
                    "thread",
                    lambda value: value["sourceRef"].__setitem__("providerMessageId", "gmail 1"),
                ),
                True,
            ),
            (
                "leading_space_provider_id",
                "thread",
                mutated_raw(
                    base_thread,
                    "thread",
                    lambda value: value["sourceRef"].__setitem__("providerMessageId", " gmail-1"),
                ),
                False,
            ),
            (
                "trailing_space_provider_id",
                "thread",
                mutated_raw(
                    base_thread,
                    "thread",
                    lambda value: value["sourceRef"].__setitem__("providerMessageId", "gmail-1 "),
                ),
                False,
            ),
            (
                "multibyte_display_at_byte_limit",
                "invite",
                mutated_raw(
                    base_invite,
                    "invite",
                    lambda value: value["createdBy"].__setitem__("displayName", "é" * 128),
                ),
                True,
            ),
            (
                "multibyte_display_over_byte_limit",
                "invite",
                mutated_raw(
                    base_invite,
                    "invite",
                    lambda value: value["createdBy"].__setitem__("displayName", "é" * 129),
                ),
                False,
            ),
            (
                "leading_nbsp_display",
                "invite",
                mutated_raw(
                    base_invite,
                    "invite",
                    lambda value: value["createdBy"].__setitem__("displayName", "\u00a0Owner"),
                ),
                False,
            ),
            (
                "trailing_ogham_space_display",
                "invite",
                mutated_raw(
                    base_invite,
                    "invite",
                    lambda value: value["createdBy"].__setitem__("displayName", "Owner\u1680"),
                ),
                False,
            ),
            (
                "internal_nbsp_display",
                "invite",
                mutated_raw(
                    base_invite,
                    "invite",
                    lambda value: value["createdBy"].__setitem__("displayName", "Owner\u00a0Name"),
                ),
                True,
            ),
            (
                "unassigned_unicode_display",
                "invite",
                mutated_raw(
                    base_invite,
                    "invite",
                    lambda value: value["createdBy"].__setitem__(
                        "displayName", "Owner\U0001bca4"
                    ),
                ),
                True,
            ),
            (
                "leading_em_space_guest_display",
                "session",
                mutated_raw(
                    base_session,
                    "session",
                    lambda value: value.__setitem__("guestDisplayName", "\u2003Guest"),
                ),
                False,
            ),
            (
                "trailing_narrow_nbsp_guest_display",
                "session",
                mutated_raw(
                    base_session,
                    "session",
                    lambda value: value.__setitem__("guestDisplayName", "Guest\u202f"),
                ),
                False,
            ),
            (
                "internal_ideographic_space_guest_display",
                "session",
                mutated_raw(
                    base_session,
                    "session",
                    lambda value: value.__setitem__("guestDisplayName", "Guest\u3000Name"),
                ),
                True,
            ),
            (
                "multibyte_message_at_byte_limit",
                "thread",
                valid_raw(
                    {**base_thread, "messages": [message_record(text="é" * 8192)]},
                    "thread",
                ),
                True,
            ),
            (
                "multibyte_message_over_byte_limit",
                "thread",
                mutated_raw(
                    {**base_thread, "messages": [message_record()]},
                    "thread",
                    lambda value: value["messages"][0].__setitem__("text", "é" * 8193),
                ),
                False,
            ),
            ("absent_invite_optionals", "invite", valid_raw(base_invite, "invite"), True),
            (
                "explicit_null_invited_email",
                "invite",
                mutated_raw(base_invite, "invite", lambda value: value.__setitem__("invitedEmail", None)),
                False,
            ),
            (
                "explicit_null_active_session",
                "invite",
                mutated_raw(base_invite, "invite", lambda value: value.__setitem__("activeSessionHash", None)),
                False,
            ),
            (
                "extra_thread_field",
                "thread",
                mutated_raw(base_thread, "thread", lambda value: value.__setitem__("unexpected", True)),
                False,
            ),
            (
                "malformed_created_by",
                "invite",
                mutated_raw(base_invite, "invite", lambda value: value["createdBy"].pop("displayName")),
                False,
            ),
            (
                "missing_message_visibility",
                "thread",
                mutated_raw(
                    {**base_thread, "messages": [message_record()]},
                    "thread",
                    lambda value: value["messages"][0].pop("visibility"),
                ),
                False,
            ),
            (
                "object_thread_state",
                "thread",
                mutated_raw(
                    base_thread,
                    "thread",
                    lambda value: value.__setitem__("state", {}),
                ),
                False,
            ),
            (
                "object_source_provider",
                "thread",
                mutated_raw(
                    base_thread,
                    "thread",
                    lambda value: value["sourceRef"].__setitem__("provider", {}),
                ),
                False,
            ),
            (
                "object_message_author_kind",
                "thread",
                mutated_raw(
                    {**base_thread, "messages": [message_record()]},
                    "thread",
                    lambda value: value["messages"][0].__setitem__("authorKind", {}),
                ),
                False,
            ),
            (
                "object_message_visibility",
                "thread",
                mutated_raw(
                    {**base_thread, "messages": [message_record()]},
                    "thread",
                    lambda value: value["messages"][0].__setitem__("visibility", {}),
                ),
                False,
            ),
            (
                "object_invite_status",
                "invite",
                mutated_raw(
                    base_invite,
                    "invite",
                    lambda value: value.__setitem__("status", {}),
                ),
                False,
            ),
            (
                "object_invite_identity_assurance",
                "invite",
                mutated_raw(
                    base_invite,
                    "invite",
                    lambda value: value.__setitem__("identityAssurance", {}),
                ),
                False,
            ),
            (
                "object_invite_visibility",
                "invite",
                mutated_raw(
                    base_invite,
                    "invite",
                    lambda value: value.__setitem__("visibility", {}),
                ),
                False,
            ),
            (
                "object_invite_actions",
                "invite",
                mutated_raw(
                    base_invite,
                    "invite",
                    lambda value: value.__setitem__("allowedActions", {}),
                ),
                False,
            ),
            (
                "object_session_status",
                "session",
                mutated_raw(
                    base_session,
                    "session",
                    lambda value: value.__setitem__("status", {}),
                ),
                False,
            ),
            (
                "object_session_identity_assurance",
                "session",
                mutated_raw(
                    base_session,
                    "session",
                    lambda value: value.__setitem__("identityAssurance", {}),
                ),
                False,
            ),
            (
                "object_session_visibility",
                "session",
                mutated_raw(
                    base_session,
                    "session",
                    lambda value: value.__setitem__("visibility", {}),
                ),
                False,
            ),
            (
                "object_session_actions",
                "session",
                mutated_raw(
                    base_session,
                    "session",
                    lambda value: value.__setitem__("allowedActions", {}),
                ),
                False,
            ),
        ]

        for token in ("2", "2.0", "2e0", "-0", "02", "true", "false"):
            raw = wire_json(base_thread, "thread").replace('"v":"2"', f'"v":{token}', 1)
            cases.append((f"raw_version_token_{token}", "thread", raw, False))
        for token in ("2.0", "2e0", "-0", "02", "+2"):
            raw = wire_json(base_thread, "thread").replace(
                '"v":"2"', f'"v":"{token}"', 1
            )
            cases.append((f"quoted_version_token_{token}", "thread", raw, False))
        numeric_fields = (
            ("thread_updated_at_number", "thread", base_thread, '"updatedAt":"1800000000100"', '"updatedAt":1800000000100'),
            (
                "message_created_at_number",
                "thread",
                {**base_thread, "messages": [message_record()]},
                '"createdAt":"1800000000101"',
                '"createdAt":1800000000101',
            ),
            ("invite_exchange_count_number", "invite", base_invite, '"exchangeCount":"0"', '"exchangeCount":0'),
            ("session_created_at_number", "session", base_session, '"createdAt":"1800000101"', '"createdAt":1800000101'),
        )
        for label, kind, value, canonical, replacement in numeric_fields:
            cases.append((label, kind, wire_json(value, kind).replace(canonical, replacement, 1), False))
        quoted_integer_fields = (
            (
                "thread_updated_at",
                "thread",
                base_thread,
                '"updatedAt":"1800000000100"',
                "1800000000100",
            ),
            (
                "message_created_at",
                "thread",
                {**base_thread, "messages": [message_record()]},
                '"createdAt":"1800000000101"',
                "1800000000101",
            ),
            (
                "invite_exchange_count",
                "invite",
                base_invite,
                '"exchangeCount":"0"',
                "0",
            ),
            (
                "session_created_at",
                "session",
                base_session,
                '"createdAt":"1800000101"',
                "1800000101",
            ),
        )
        for label, kind, value, marker, canonical_value in quoted_integer_fields:
            spellings = (
                canonical_value + ".0",
                canonical_value + "e0",
                "-0",
                "0" + canonical_value,
                "+" + canonical_value,
            )
            for spelling in spellings:
                cases.append(
                    (
                        f"{label}_quoted_{spelling}",
                        kind,
                        wire_json(value, kind).replace(
                            marker,
                            marker.replace(canonical_value, spelling),
                            1,
                        ),
                        False,
                    )
                )

        for kind, value in (("thread", base_thread), ("invite", base_invite), ("session", base_session)):
            raw = wire_json(value, kind)
            cases.extend(
                (
                    (
                        f"duplicate_literal_key_{kind}",
                        kind,
                        raw.replace('"v":"2"', '"v":"2","v":"2"', 1),
                        False,
                    ),
                    (
                        f"duplicate_escaped_key_{kind}",
                        kind,
                        raw.replace('"v":"2"', '"v":"2","\\u0076":"2"', 1),
                        False,
                    ),
                )
            )

        revoked_invite = {
            **base_invite,
            "status": "revoked",
            "revokedAt": SEC + 110,
            "revokedBy": base_invite["ownerEmail"],
        }
        revoked_session = {**base_session, "status": "revoked", "revokedAt": SEC + 102}
        logged_out_session = {**base_session, "status": "logged_out", "loggedOutAt": SEC + 102}
        equal_revoked_session = {
            **base_session,
            "status": "revoked",
            "revokedAt": base_session["lastUsedAt"],
        }
        equal_logged_out_session = {
            **base_session,
            "status": "logged_out",
            "loggedOutAt": base_session["lastUsedAt"],
        }
        equal_created_revoked_invite = {
            **base_invite,
            "status": "revoked",
            "revokedAt": base_invite["createdAt"],
            "revokedBy": base_invite["ownerEmail"],
        }
        equal_exchanged_revoked_invite = {
            **base_invite,
            "status": "revoked",
            "exchangedAt": SEC + 110,
            "exchangeCount": 1,
            "activeSessionHash": base_session["sessionHash"],
            "revokedAt": SEC + 110,
            "revokedBy": base_invite["ownerEmail"],
        }
        cases.extend(
            (
                ("valid_revoked_invite", "invite", valid_raw(revoked_invite, "invite"), True),
                (
                    "alternate_revoked_by",
                    "invite",
                    mutated_raw(
                        revoked_invite,
                        "invite",
                        lambda value: value.__setitem__("revokedBy", "attacker@example.com"),
                    ),
                    False,
                ),
                ("valid_revoked_session", "session", valid_raw(revoked_session, "session"), True),
                ("valid_logged_out_session", "session", valid_raw(logged_out_session, "session"), True),
                (
                    "equal_last_used_revoked_session",
                    "session",
                    valid_raw(equal_revoked_session, "session"),
                    False,
                ),
                (
                    "equal_last_used_logged_out_session",
                    "session",
                    valid_raw(equal_logged_out_session, "session"),
                    False,
                ),
                (
                    "equal_created_revoked_invite",
                    "invite",
                    valid_raw(equal_created_revoked_invite, "invite"),
                    False,
                ),
                (
                    "equal_exchanged_revoked_invite",
                    "invite",
                    valid_raw(equal_exchanged_revoked_invite, "invite"),
                    False,
                ),
            )
        )

        for label, kind, raw, expected in cases:
            with self.subTest(case=label, kind=kind):
                typed = redis_store._v2_json_from_wire(raw, kind)
                python_valid = typed is not None and normalizers[kind](typed) is not None
                lua_result = json.loads(
                    self.client.command(
                        ["EVAL", redis_store._VALIDATE_V2_WIRE_RECORD_LUA, 0, kind, raw]
                    )
                )
                lua_valid = lua_result == {"status": "valid"}
                self.assertEqual(python_valid, expected)
                self.assertEqual(lua_valid, expected)
                self.assertEqual(python_valid, lua_valid)

        canonical_bytes = wire_json(base_invite, "invite").encode("utf-8")
        for label, invalid_utf8 in (
            ("invalid_lead", b"\xff"),
            ("overlong", b"\xc0\xaf"),
            ("truncated", b"\xe2\x82"),
            ("surrogate", b"\xed\xa0\x80"),
            ("above_unicode_max", b"\xf4\x90\x80\x80"),
        ):
            with self.subTest(case=f"malformed_utf8_{label}", kind="invite"):
                raw_bytes = canonical_bytes.replace(b'"Owner"', b'"' + invalid_utf8 + b'Owner"', 1)
                with self.assertRaises(UnicodeDecodeError):
                    raw_bytes.decode("utf-8")
                lua_result = json.loads(
                    self.client.command(
                        [
                            "EVAL",
                            redis_store._VALIDATE_V2_WIRE_RECORD_LUA,
                            0,
                            "invite",
                            raw_bytes,
                        ]
                    )
                )
                self.assertEqual(lua_result, {"status": "malformed"})

    def test_escaped_messages_key_is_accepted_by_every_thread_array_gate(self):
        def escaped_messages(raw: str) -> str:
            self.assertEqual(raw.count('"messages":'), 1)
            return raw.replace('"messages":', '"\\u006dessages":', 1)

        thread = thread_record()
        thread_key = self._thread_key(thread["collaborationId"])
        source_key = self._source_key(thread)

        def mutate_create(command, argument_start):
            command[argument_start] = escaped_messages(command[argument_start])

        created = redis_store._create_v2_thread(
            thread,
            command_transport=self._transport_mutating_eval(
                redis_store._CREATE_V2_THREAD_LUA, mutate_create
            ),
        )
        self.assertEqual(created.get("status"), "ok", created)
        self.assertTrue(created["created"])
        self.assertIn('"\\u006dessages":[]', self.client.command(["GET", thread_key]))

        duplicate = redis_store._create_v2_thread(
            thread, command_transport=self.client.transport
        )
        self.assertEqual(duplicate.get("status"), "ok", duplicate)
        self.assertFalse(duplicate["created"])

        loaded = redis_store._load_v2_thread_by_source(
            thread["ownerEmail"],
            thread["mailboxId"],
            thread["sourceRef"],
            command_transport=self.client.transport,
        )
        self.assertEqual(loaded.get("record"), thread)

        replacement = {
            **thread,
            "messages": [message_record()],
            "updatedAt": MS + 101,
        }

        def mutate_cas(command, argument_start):
            command[argument_start + 1] = escaped_messages(command[argument_start + 1])

        saved = redis_store._save_v2_thread_if_expected(
            replacement,
            thread["updatedAt"],
            command_transport=self._transport_mutating_eval(
                redis_store._SAVE_V2_THREAD_CAS_LUA, mutate_cas
            ),
        )
        self.assertEqual(saved.get("status"), "ok", saved)
        self.assertIn('"\\u006dessages":[', self.client.command(["GET", thread_key]))

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_thread(thread, command_transport=self.client.transport)
        existing_raw = self.client.command(["GET", thread_key])
        remaining = self.client.command(["PTTL", thread_key])
        self.client.command(
            ["SET", thread_key, escaped_messages(existing_raw), "PX", remaining]
        )
        self._create_exchanged_invitation()
        capability = self._guest_mutation_capability()
        guest_replacement = self._guest_replacement(thread)

        def mutate_append(command, argument_start):
            command[argument_start + 1] = escaped_messages(command[argument_start + 1])

        appended = redis_store._append_v2_guest_reply_if_expected(
            guest_replacement,
            thread["updatedAt"],
            session_context=capability,
            now=SEC + 102,
            command_transport=self._transport_mutating_eval(
                redis_store._APPEND_V2_GUEST_REPLY_LUA, mutate_append
            ),
        )
        self.assertEqual(appended.get("status"), "ok", appended)
        self.assertEqual(self.client.command(["GET", source_key]), thread["collaborationId"])
        self.assertIn('"\\u006dessages":[', self.client.command(["GET", thread_key]))

    def test_every_thread_lua_script_rejects_numeric_and_duplicate_wire_corpus_without_writes(self):
        thread = thread_record()
        thread_raw = wire_json(thread, "thread")
        replacement = {
            **thread,
            "messages": [message_record()],
            "updatedAt": MS + 101,
        }
        guest_replacement = self._guest_replacement(thread)

        for label, malformed_raw in wire_lexical_variants(thread_raw):
            with self.subTest(script="create_thread", variant=label):
                self.client.command(["FLUSHALL"])
                redis_store._create_v2_thread(thread, command_transport=self.client.transport)
                before = self._snapshot_v2_state()

                def mutate_create(command, argument_start, raw=malformed_raw):
                    command[argument_start] = raw

                rejected = redis_store._create_v2_thread(
                    thread,
                    command_transport=self._transport_mutating_eval(
                        redis_store._CREATE_V2_THREAD_LUA, mutate_create
                    ),
                )
                self.assertNotEqual(rejected.get("status"), "ok", rejected)
                self._assert_v2_state_unchanged(before)

            with self.subTest(script="save_thread_cas", variant=label):
                self.client.command(["FLUSHALL"])
                redis_store._create_v2_thread(thread, command_transport=self.client.transport)
                before = self._snapshot_v2_state()
                malformed_replacement = dict(wire_lexical_variants(wire_json(replacement, "thread")))[label]

                def mutate_cas(command, argument_start, raw=malformed_replacement):
                    command[argument_start + 1] = raw

                rejected = redis_store._save_v2_thread_if_expected(
                    replacement,
                    thread["updatedAt"],
                    command_transport=self._transport_mutating_eval(
                        redis_store._SAVE_V2_THREAD_CAS_LUA, mutate_cas
                    ),
                )
                self.assertNotEqual(rejected.get("status"), "ok", rejected)
                self._assert_v2_state_unchanged(before)

            with self.subTest(script="guest_append", variant=label):
                self.client.command(["FLUSHALL"])
                redis_store._create_v2_thread(thread, command_transport=self.client.transport)
                self._create_exchanged_invitation()
                capability = self._guest_mutation_capability()
                before = self._snapshot_v2_state()
                malformed_guest = dict(
                    wire_lexical_variants(wire_json(guest_replacement, "thread"))
                )[label]

                def mutate_append(command, argument_start, raw=malformed_guest):
                    command[argument_start + 1] = raw

                rejected = redis_store._append_v2_guest_reply_if_expected(
                    guest_replacement,
                    thread["updatedAt"],
                    session_context=capability,
                    now=SEC + 102,
                    command_transport=self._transport_mutating_eval(
                        redis_store._APPEND_V2_GUEST_REPLY_LUA, mutate_append
                    ),
                )
                self.assertNotEqual(rejected.get("status"), "ok", rejected)
                self._assert_v2_state_unchanged(before)

            with self.subTest(script="load_source", variant=label):
                self.client.command(["FLUSHALL"])
                source_key = self._source_key(thread)
                thread_key = self._put_thread(thread, source_key)
                remaining = self.client.command(["PTTL", thread_key])
                self.client.command(["SET", thread_key, malformed_raw, "PX", remaining])
                before = self._snapshot_v2_state()
                rejected = redis_store._load_v2_thread_by_source(
                    thread["ownerEmail"],
                    thread["mailboxId"],
                    thread["sourceRef"],
                    command_transport=self.client.transport,
                )
                self.assertNotEqual(rejected.get("status"), "ok", rejected)
                self._assert_v2_state_unchanged(before)

    def test_every_invite_and_session_lua_script_rejects_lexical_corpus_without_writes(self):
        raw_token = "t" * 43
        invite = invite_record(raw_token)
        proposal = self._duplicate_invite_proposal(invite)
        session = session_record("s" * 43)
        invite_variants = dict(wire_lexical_variants(wire_json(proposal, "invite")))
        canonical_invite_variants = dict(
            wire_lexical_variants(wire_json(invite, "invite"))
        )
        session_variants = dict(wire_lexical_variants(wire_json(session, "session")))

        for label in invite_variants:
            with self.subTest(script="create_invite", variant=label):
                self.client.command(["FLUSHALL"])
                redis_store._create_v2_invite(
                    invite,
                    now=invite["createdAt"],
                    command_transport=self.client.transport,
                )
                before = self._snapshot_v2_state()

                def mutate_create(command, argument_start, raw=invite_variants[label]):
                    command[argument_start] = raw

                rejected = redis_store._create_v2_invite(
                    proposal,
                    now=proposal["createdAt"],
                    command_transport=self._transport_mutating_eval(
                        redis_store._CREATE_V2_INVITE_LUA, mutate_create
                    ),
                )
                self.assertNotEqual(rejected.get("status"), "ok", rejected)
                self._assert_v2_state_unchanged(before)

            with self.subTest(script="validate_invite_graph", variant=label):
                self.client.command(["FLUSHALL"])
                redis_store._create_v2_invite(
                    invite,
                    now=invite["createdAt"],
                    command_transport=self.client.transport,
                )
                before = self._snapshot_v2_state()

                def mutate_validate(
                    command,
                    argument_start,
                    raw=canonical_invite_variants[label],
                ):
                    command[argument_start] = raw

                rejected = redis_store._create_v2_invite(
                    proposal,
                    now=proposal["createdAt"],
                    command_transport=self._transport_mutating_eval(
                        redis_store._VALIDATE_V2_INVITE_GRAPH_LUA, mutate_validate
                    ),
                )
                self.assertNotEqual(rejected.get("status"), "ok", rejected)
                self._assert_v2_state_unchanged(before)

            with self.subTest(script="exchange_invite", variant=label):
                self.client.command(["FLUSHALL"])
                redis_store._create_v2_invite(
                    invite,
                    now=invite["createdAt"],
                    command_transport=self.client.transport,
                )
                before = self._snapshot_v2_state()

                def mutate_exchange(command, argument_start, raw=session_variants[label]):
                    command[argument_start + 3] = raw

                rejected = redis_store._atomic_exchange_v2_invite(
                    raw_token=raw_token,
                    invite_id=invite["inviteId"],
                    session_record=session,
                    now=session["createdAt"],
                    session_ttl=session["expiresAt"] - session["createdAt"],
                    command_transport=self._transport_mutating_eval(
                        redis_store._EXCHANGE_V2_INVITE_LUA, mutate_exchange
                    ),
                )
                self.assertNotEqual(rejected.get("status"), "ok", rejected)
                self._assert_v2_state_unchanged(before)
                self.assertIsNone(self.client.command(["GET", self._session_key(session)]))

            for script_name, operation in (
                (
                    "update_session",
                    lambda: redis_store._update_v2_guest_session(
                        session,
                        normalizer=guest_session.normalize_v2_guest_session_record,
                        now=SEC + 102,
                        csrf_token_hash=hash_v2_secret("d" * 43),
                        command_transport=self.client.transport,
                    ),
                ),
                (
                    "revoke_invite",
                    lambda: redis_store._revoke_v2_invite(
                        invite["inviteId"],
                        owner_email=invite["ownerEmail"],
                        workspace_id=invite["workspaceId"],
                        mailbox_id=invite["mailboxId"],
                        collaboration_id=invite["collaborationId"],
                        revoked_by=invite["ownerEmail"],
                        now=SEC + 102,
                        command_transport=self.client.transport,
                    ),
                ),
                (
                    "revoke_session",
                    lambda: revoke_guest_session(
                        session,
                        now=SEC + 102,
                        command_transport=self.client.transport,
                    ),
                ),
            ):
                with self.subTest(script=script_name, variant=label):
                    self.client.command(["FLUSHALL"])
                    _, _, _, session_key = self._create_exchanged_invitation()
                    remaining = self.client.command(["PTTL", session_key])
                    self.client.command(
                        ["SET", session_key, session_variants[label], "PX", remaining]
                    )
                    before = self._snapshot_v2_state()
                    rejected = operation()
                    self.assertNotEqual(rejected.get("status"), "ok", rejected)
                    self._assert_v2_state_unchanged(before)

    def test_secondary_lua_decode_sites_reject_numeric_and_escaped_duplicate_records(self):
        labels = ("numeric_2.0", "duplicate_escaped")

        def malformed(raw: str, label: str) -> str:
            return dict(wire_lexical_variants(raw))[label]

        def replace_preserving_ttl(key: str, raw: str) -> None:
            remaining = self.client.command(["PTTL", key])
            self.assertGreater(remaining, 0)
            self.client.command(["SET", key, raw, "PX", remaining])

        thread = thread_record()
        replacement = {
            **thread,
            "messages": [message_record()],
            "updatedAt": MS + 101,
        }
        guest_replacement = self._guest_replacement(thread)

        for label in labels:
            for script_name, operation in (
                (
                    "create_thread_target",
                    lambda: redis_store._create_v2_thread(
                        thread, command_transport=self.client.transport
                    ),
                ),
                (
                    "save_thread_current",
                    lambda: redis_store._save_v2_thread_if_expected(
                        replacement,
                        thread["updatedAt"],
                        command_transport=self.client.transport,
                    ),
                ),
            ):
                with self.subTest(script=script_name, variant=label):
                    self.client.command(["FLUSHALL"])
                    redis_store._create_v2_thread(
                        thread, command_transport=self.client.transport
                    )
                    thread_key = self._thread_key(thread["collaborationId"])
                    replace_preserving_ttl(
                        thread_key, malformed(wire_json(thread, "thread"), label)
                    )
                    before = self._snapshot_v2_state()
                    rejected = operation()
                    self.assertNotEqual(rejected.get("status"), "ok", rejected)
                    self._assert_v2_state_unchanged(before)

            for target in ("thread", "invite", "session"):
                with self.subTest(script="guest_append", target=target, variant=label):
                    self.client.command(["FLUSHALL"])
                    redis_store._create_v2_thread(
                        thread, command_transport=self.client.transport
                    )
                    _, _, invite_keys, session_key = self._create_exchanged_invitation()
                    capability = self._guest_mutation_capability()
                    target_key, kind = {
                        "thread": (self._thread_key(thread["collaborationId"]), "thread"),
                        "invite": (invite_keys[0], "invite"),
                        "session": (session_key, "session"),
                    }[target]
                    current_raw = self.client.command(["GET", target_key])
                    replace_preserving_ttl(
                        target_key, malformed(current_raw, label)
                    )
                    before = self._snapshot_v2_state()
                    rejected = redis_store._append_v2_guest_reply_if_expected(
                        guest_replacement,
                        thread["updatedAt"],
                        session_context=capability,
                        now=SEC + 102,
                        command_transport=self.client.transport,
                    )
                    self.assertNotEqual(rejected.get("status"), "ok", rejected)
                    self._assert_v2_state_unchanged(before)

            with self.subTest(script="load_source_expected_source", variant=label):
                self.client.command(["FLUSHALL"])
                source_key = self._source_key(thread)
                self._put_thread(thread, source_key)
                source_raw = compact_json(thread["sourceRef"])
                if label == "numeric_2.0":
                    malformed_source = source_raw[:-1] + ',"unexpected":2.0}'
                else:
                    malformed_source = source_raw.replace(
                        '"provider":"google"',
                        '"provider":"google","\\u0070rovider":"google"',
                        1,
                    )
                before = self._snapshot_v2_state()

                def mutate_source(command, argument_start, raw=malformed_source):
                    command[argument_start + 3] = raw

                rejected = redis_store._load_v2_thread_by_source(
                    thread["ownerEmail"],
                    thread["mailboxId"],
                    thread["sourceRef"],
                    command_transport=self._transport_mutating_eval(
                        redis_store._LOAD_AND_MIGRATE_V2_SOURCE_LUA, mutate_source
                    ),
                )
                self.assertNotEqual(rejected.get("status"), "ok", rejected)
                self._assert_v2_state_unchanged(before)

    def test_every_numeric_script_argument_requires_canonical_decimal_syntax(self):
        def assert_rejected(
            label: str,
            script: str,
            argument_index: int,
            malformed: str,
            operation,
        ) -> None:
            with self.subTest(
                path=label,
                argument_index=argument_index,
                malformed=malformed,
            ):
                before = self._snapshot_v2_state()

                def mutate(command, argument_start):
                    command[argument_start + argument_index] = malformed

                rejected = operation(
                    self._transport_mutating_eval(script, mutate)
                )
                self.assertNotIn(
                    rejected.get("status"), {"ok", "updated"}, rejected
                )
                self._assert_v2_state_unchanged(before)

        thread = thread_record()
        replacement = {
            **thread,
            "messages": [message_record()],
            "updatedAt": MS + 101,
        }
        redis_store._create_v2_thread(
            thread, command_transport=self.client.transport
        )
        self._create_exchanged_invitation()
        capability = self._guest_mutation_capability(now=SEC + 102)

        assert_rejected(
            "thread_create_ttl",
            redis_store._CREATE_V2_THREAD_LUA,
            2,
            "2.0",
            lambda transport: redis_store._create_v2_thread(
                thread, command_transport=transport
            ),
        )
        for label, index, malformed in (
            ("thread_cas_expected_revision", 0, "2e0"),
            ("thread_cas_ttl", 2, "-0"),
        ):
            assert_rejected(
                label,
                redis_store._SAVE_V2_THREAD_CAS_LUA,
                index,
                malformed,
                lambda transport: redis_store._save_v2_thread_if_expected(
                    replacement,
                    thread["updatedAt"],
                    command_transport=transport,
                ),
            )
        for label, index, malformed in (
            ("guest_append_expected_revision", 0, "02"),
            ("guest_append_ttl", 2, "+2"),
            ("guest_append_now", 3, "2.0"),
        ):
            assert_rejected(
                label,
                redis_store._APPEND_V2_GUEST_REPLY_LUA,
                index,
                malformed,
                lambda transport: redis_store._append_v2_guest_reply_if_expected(
                    self._guest_replacement(thread),
                    thread["updatedAt"],
                    session_context=capability,
                    now=SEC + 103,
                    command_transport=transport,
                ),
            )

        self.client.command(["FLUSHALL"])
        invite = invite_record("t" * 43)
        proposal = self._duplicate_invite_proposal(invite)
        created = redis_store._create_v2_invite(
            invite,
            now=invite["createdAt"],
            command_transport=self.client.transport,
        )
        self.assertEqual(created.get("status"), "ok", created)
        for label, index, malformed in (
            ("invite_create_ttl", 1, "2e0"),
            ("invite_create_now", 2, "-0"),
        ):
            assert_rejected(
                label,
                redis_store._CREATE_V2_INVITE_LUA,
                index,
                malformed,
                lambda transport: redis_store._create_v2_invite(
                    proposal,
                    now=proposal["createdAt"],
                    command_transport=transport,
                ),
            )
        assert_rejected(
            "invite_graph_now",
            redis_store._VALIDATE_V2_INVITE_GRAPH_LUA,
            2,
            "02",
            lambda transport: redis_store._create_v2_invite(
                proposal,
                now=proposal["createdAt"],
                command_transport=transport,
            ),
        )

        session = session_record("s" * 43)
        for label, index, malformed in (
            ("invite_exchange_now", 1, "+2"),
            ("invite_exchange_ttl", 2, "2.0"),
            ("invite_exchange_session_ttl", 6, "2e0"),
            ("invite_exchange_session_expiry", 13, "-0"),
        ):
            assert_rejected(
                label,
                redis_store._EXCHANGE_V2_INVITE_LUA,
                index,
                malformed,
                lambda transport: redis_store._atomic_exchange_v2_invite(
                    raw_token="t" * 43,
                    invite_id=invite["inviteId"],
                    session_record=session,
                    now=session["createdAt"],
                    session_ttl=session["expiresAt"] - session["createdAt"],
                    command_transport=transport,
                ),
            )

        self.client.command(["FLUSHALL"])
        invite, session, _, _ = self._create_exchanged_invitation()
        for label, index, malformed in (
            ("session_update_now", 0, "02"),
            ("session_update_ttl", 2, "+2"),
            ("session_update_expected_audit", 11, "2.0"),
        ):
            assert_rejected(
                label,
                redis_store._UPDATE_V2_SESSION_LUA,
                index,
                malformed,
                lambda transport: redis_store._update_v2_guest_session(
                    session,
                    normalizer=guest_session.normalize_v2_guest_session_record,
                    now=SEC + 102,
                    csrf_token_hash=hash_v2_secret("m" * 43),
                    command_transport=transport,
                ),
            )
        assert_rejected(
            "invite_revoke_now",
            redis_store._REVOKE_V2_INVITE_LUA,
            7,
            "2e0",
            lambda transport: redis_store._revoke_v2_invite(
                invite["inviteId"],
                owner_email=invite["ownerEmail"],
                workspace_id=invite["workspaceId"],
                mailbox_id=invite["mailboxId"],
                collaboration_id=invite["collaborationId"],
                revoked_by=invite["ownerEmail"],
                now=SEC + 102,
                command_transport=transport,
            ),
        )
        assert_rejected(
            "session_revoke_now",
            redis_store._REVOKE_V2_SESSION_LUA,
            0,
            "-0",
            lambda transport: revoke_guest_session(
                session,
                now=SEC + 102,
                command_transport=transport,
            ),
        )

    def test_secondary_invite_and_session_decode_sites_reject_malformed_records(self):
        labels = ("numeric_2.0", "duplicate_escaped")

        def malformed(raw: str, label: str) -> str:
            return dict(wire_lexical_variants(raw))[label]

        def replace_preserving_ttl(key: str, raw: str) -> None:
            remaining = self.client.command(["PTTL", key])
            self.assertGreater(remaining, 0)
            self.client.command(["SET", key, raw, "PX", remaining])

        for label in labels:
            invite = invite_record("t" * 43)
            proposal = self._duplicate_invite_proposal(invite)
            for target in ("current_identity", "canonical_invite"):
                with self.subTest(script="create_invite", target=target, variant=label):
                    self.client.command(["FLUSHALL"])
                    redis_store._create_v2_invite(
                        invite,
                        now=invite["createdAt"],
                        command_transport=self.client.transport,
                    )
                    invite_keys = self._invite_keys(invite)
                    target_key = invite_keys[2] if target == "current_identity" else invite_keys[0]
                    replace_preserving_ttl(
                        target_key, malformed(wire_json(invite, "invite"), label)
                    )
                    before = self._snapshot_v2_state()
                    rejected = redis_store._create_v2_invite(
                        proposal,
                        now=proposal["createdAt"],
                        command_transport=self.client.transport,
                    )
                    self.assertNotEqual(rejected.get("status"), "ok", rejected)
                    self._assert_v2_state_unchanged(before)

            old_secret = b"x" * 32
            new_secret = b"y" * 32
            old_encoded = base64.urlsafe_b64encode(old_secret).decode("ascii").rstrip("=")
            new_encoded = base64.urlsafe_b64encode(new_secret).decode("ascii").rstrip("=")
            with self.subTest(script="create_invite", target="previous_identity", variant=label), patch.dict(
                os.environ, {}, clear=False
            ):
                self.client.command(["FLUSHALL"])
                os.environ[redis_store.V2_INDEX_HMAC_ENV] = old_encoded
                os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
                redis_store._create_v2_invite(
                    invite,
                    now=invite["createdAt"],
                    command_transport=self.client.transport,
                )
                previous_identity = self._invite_keys(invite, hmac_key=old_secret)[2]
                replace_preserving_ttl(
                    previous_identity, malformed(wire_json(invite, "invite"), label)
                )
                os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
                os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
                before = self._snapshot_v2_state()
                rejected = redis_store._create_v2_invite(
                    proposal,
                    now=proposal["createdAt"],
                    command_transport=self.client.transport,
                )
                self.assertNotEqual(rejected.get("status"), "ok", rejected)
                self._assert_v2_state_unchanged(before)

            for target in ("current_identity", "canonical_invite"):
                with self.subTest(
                    script="validate_invite_graph", target=target, variant=label
                ):
                    self.client.command(["FLUSHALL"])
                    redis_store._create_v2_invite(
                        invite,
                        now=invite["createdAt"],
                        command_transport=self.client.transport,
                    )
                    invite_keys = self._invite_keys(invite)
                    target_key = invite_keys[2] if target == "current_identity" else invite_keys[0]
                    injected = None

                    def inject_after_duplicate(command, key=target_key):
                        nonlocal injected
                        response = self.client.transport(command)
                        if (
                            injected is None
                            and command[0] == "EVAL"
                            and command[1] == redis_store._CREATE_V2_INVITE_LUA
                            and json.loads(response["result"]).get("status") == "duplicate"
                        ):
                            replace_preserving_ttl(
                                key, malformed(wire_json(invite, "invite"), label)
                            )
                            injected = self._snapshot_v2_state()
                        return response

                    rejected = redis_store._create_v2_invite(
                        proposal,
                        now=proposal["createdAt"],
                        command_transport=inject_after_duplicate,
                    )
                    self.assertIsNotNone(injected)
                    self.assertNotEqual(rejected.get("status"), "ok", rejected)
                    self._assert_v2_state_unchanged(injected)

            session = session_record("s" * 43)
            with self.subTest(script="exchange_invite", target="stored_invite", variant=label):
                self.client.command(["FLUSHALL"])
                redis_store._create_v2_invite(
                    invite,
                    now=invite["createdAt"],
                    command_transport=self.client.transport,
                )
                invite_key = self._invite_keys(invite)[0]
                replace_preserving_ttl(
                    invite_key, malformed(wire_json(invite, "invite"), label)
                )
                before = self._snapshot_v2_state()
                rejected = redis_store._atomic_exchange_v2_invite(
                    raw_token="t" * 43,
                    invite_id=invite["inviteId"],
                    session_record=session,
                    now=session["createdAt"],
                    session_ttl=session["expiresAt"] - session["createdAt"],
                    command_transport=self.client.transport,
                )
                self.assertNotEqual(rejected.get("status"), "ok", rejected)
                self._assert_v2_state_unchanged(before)

            for script_name, operation in (
                (
                    "update_session",
                    lambda: redis_store._update_v2_guest_session(
                        session,
                        normalizer=guest_session.normalize_v2_guest_session_record,
                        now=SEC + 102,
                        csrf_token_hash=hash_v2_secret("d" * 43),
                        command_transport=self.client.transport,
                    ),
                ),
                (
                    "revoke_session",
                    lambda: revoke_guest_session(
                        session,
                        now=SEC + 102,
                        command_transport=self.client.transport,
                    ),
                ),
            ):
                with self.subTest(script=script_name, target="linked_invite", variant=label):
                    self.client.command(["FLUSHALL"])
                    _, _, invite_keys, _ = self._create_exchanged_invitation()
                    stored_invite_raw = self.client.command(["GET", invite_keys[0]])
                    replace_preserving_ttl(
                        invite_keys[0], malformed(stored_invite_raw, label)
                    )
                    before = self._snapshot_v2_state()
                    rejected = operation()
                    self.assertNotEqual(rejected.get("status"), "ok", rejected)
                    self._assert_v2_state_unchanged(before)

            with self.subTest(script="revoke_invite", target="stored_invite", variant=label):
                self.client.command(["FLUSHALL"])
                _, _, invite_keys, _ = self._create_exchanged_invitation()
                stored_raw = self.client.command(["GET", invite_keys[0]])
                malformed_invite = malformed(stored_raw, label)
                injected = None

                def inject_before_revoke_eval(command):
                    nonlocal injected
                    if (
                        injected is None
                        and command[0] == "EVAL"
                        and command[1] == redis_store._REVOKE_V2_INVITE_LUA
                    ):
                        replace_preserving_ttl(invite_keys[0], malformed_invite)
                        injected = self._snapshot_v2_state()
                    return self.client.transport(command)

                rejected = redis_store._revoke_v2_invite(
                    invite["inviteId"],
                    owner_email=invite["ownerEmail"],
                    workspace_id=invite["workspaceId"],
                    mailbox_id=invite["mailboxId"],
                    collaboration_id=invite["collaborationId"],
                    revoked_by=invite["ownerEmail"],
                    now=SEC + 102,
                    command_transport=inject_before_revoke_eval,
                )
                self.assertIsNotNone(injected)
                self.assertNotEqual(rejected.get("status"), "ok", rejected)
                self._assert_v2_state_unchanged(injected)

    def test_real_thread_source_ttls_stay_synchronized_and_failures_do_not_refresh(self):
        thread = thread_record()
        thread_key = self._thread_key(thread["collaborationId"])
        source_key = self._source_key(thread)

        created = redis_store._create_v2_thread(thread, command_transport=self.client.transport)
        self.assertTrue(created["created"])
        self._assert_retention_pair(thread_key, source_key)

        self.client.command(["PEXPIRE", thread_key, 120_000])
        self.client.command(["PEXPIRE", source_key, 120_000])
        duplicate = redis_store._create_v2_thread(thread, command_transport=self.client.transport)
        self.assertFalse(duplicate["created"])
        self._assert_retention_pair(thread_key, source_key)

        self.client.command(["PEXPIRE", thread_key, 120_000])
        self.client.command(["PEXPIRE", source_key, 120_000])
        replacement = {
            **thread,
            "messages": [message_record()],
            "updatedAt": thread["updatedAt"] + 1,
        }
        saved = redis_store._save_v2_thread_if_expected(
            replacement, thread["updatedAt"], command_transport=self.client.transport
        )
        self.assertEqual(saved["status"], "ok")
        self._assert_retention_pair(thread_key, source_key)

        self.client.command(["FLUSHALL"])
        self.client.command(["SET", source_key, "Z" * 22, "EX", 120])
        repaired = redis_store._create_v2_thread(thread, command_transport=self.client.transport)
        self.assertTrue(repaired["created"])
        self.assertEqual(self.client.command(["GET", source_key]), thread["collaborationId"])
        self._assert_retention_pair(thread_key, source_key)

        self.client.command(["FLUSHALL"])
        conflicting = {**thread, "collaborationId": "Z" * 22, "mailboxId": "mailbox-other"}
        conflicting_key = self._thread_key(conflicting["collaborationId"])
        self.client.command(["SET", conflicting_key, wire_json(conflicting, "thread"), "EX", 120])
        self.client.command(["SET", source_key, conflicting["collaborationId"], "EX", 120])
        before_raw = (
            self.client.command(["GET", conflicting_key]),
            self.client.command(["GET", source_key]),
        )
        before_ttls = self._pttls(conflicting_key, source_key)
        conflict = redis_store._create_v2_thread(thread, command_transport=self.client.transport)
        self.assertEqual(conflict.get("error"), {"code": "source_pointer_conflict"})
        self.assertIsNone(self.client.command(["GET", thread_key]))
        self.assertEqual(
            (self.client.command(["GET", conflicting_key]), self.client.command(["GET", source_key])),
            before_raw,
        )
        self._assert_ttls_not_refreshed(before_ttls, self._pttls(conflicting_key, source_key))

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_thread(thread, command_transport=self.client.transport)
        self.client.command(["PEXPIRE", thread_key, 120_000])
        self.client.command(["SET", source_key, "Z" * 22, "PX", 120_000])
        before_raw = (self.client.command(["GET", thread_key]), self.client.command(["GET", source_key]))
        before_ttls = self._pttls(thread_key, source_key)
        failed_cas = redis_store._save_v2_thread_if_expected(
            replacement, thread["updatedAt"], command_transport=self.client.transport
        )
        self.assertEqual(failed_cas.get("error"), {"code": "storage_protocol_error"})
        self.assertEqual(
            (self.client.command(["GET", thread_key]), self.client.command(["GET", source_key])),
            before_raw,
        )
        self._assert_ttls_not_refreshed(before_ttls, self._pttls(thread_key, source_key))

    def test_real_invitation_and_session_ttls_never_outlive_absolute_expiry(self):
        raw_token = "t" * 43
        invite = invite_record(raw_token)
        invite_keys = self._invite_keys(invite)
        created = redis_store._create_v2_invite(
            invite, now=invite["createdAt"], command_transport=self.client.transport
        )
        self.assertTrue(created["created"])
        for key in invite_keys:
            self._assert_ttl_ceiling(key, invite["expiresAt"] - invite["createdAt"])

        session = session_record("s" * 43)
        exchanged = redis_store._atomic_exchange_v2_invite(
            raw_token=raw_token,
            invite_id=invite["inviteId"],
            session_record=session,
            now=session["createdAt"],
            session_ttl=session["expiresAt"] - session["createdAt"],
            command_transport=self.client.transport,
        )
        self.assertEqual(exchanged, {"status": "ok"})
        self._assert_ttl_ceiling(invite_keys[0], invite["expiresAt"] - session["createdAt"])
        for key in invite_keys[1:]:
            self._assert_ttl_ceiling(key, invite["expiresAt"] - invite["createdAt"])
        session_key = self._session_key(session)
        session_remaining = session["expiresAt"] - session["createdAt"]
        self._assert_ttl_ceiling(session_key, session_remaining)
        self.assertLessEqual(self.client.command(["PTTL", session_key]), 28_800_000)
        self.assertLessEqual(
            self.client.command(["PTTL", session_key]),
            (invite["expiresAt"] - session["createdAt"]) * 1000,
        )

        self.client.command(["FLUSHALL"])
        long_invite = {
            **invite_record("u" * 43),
            "inviteId": "J" * 22,
            "tokenHash": hash_v2_secret("u" * 43),
            "expiresAt": SEC + 100 + 86_400,
        }
        long_session = {
            **session_record("v" * 43),
            "inviteId": long_invite["inviteId"],
            "sessionHash": hash_v2_secret("v" * 43),
            "createdAt": SEC + 101,
            "lastUsedAt": SEC + 101,
            "expiresAt": SEC + 101 + 28_800,
        }
        redis_store._create_v2_invite(
            long_invite, now=long_invite["createdAt"], command_transport=self.client.transport
        )
        long_exchange = redis_store._atomic_exchange_v2_invite(
            raw_token="u" * 43,
            invite_id=long_invite["inviteId"],
            session_record=long_session,
            now=long_session["createdAt"],
            session_ttl=28_800,
            command_transport=self.client.transport,
        )
        self.assertEqual(long_exchange, {"status": "ok"})
        self._assert_ttl_ceiling(self._session_key(long_session), 28_800)

        self.client.command(["FLUSHALL"])
        expired_invite = invite_record("x" * 43)

        def expire_create_argument(command, argument_start):
            changed = json.loads(command[argument_start])
            changed["expiresAt"] = changed["createdAt"]
            command[argument_start] = compact_json(changed)
            command[argument_start + 1] = "0"

        expired_create = redis_store._create_v2_invite(
            expired_invite,
            now=expired_invite["createdAt"],
            command_transport=self._transport_mutating_eval(
                redis_store._CREATE_V2_INVITE_LUA, expire_create_argument
            ),
        )
        self.assertEqual(expired_create.get("error"), {"code": "storage_protocol_error"})
        for key in self._invite_keys(expired_invite):
            self.assertEqual(self.client.command(["PTTL", key]), -2)

        redis_store._create_v2_invite(
            expired_invite, now=expired_invite["createdAt"], command_transport=self.client.transport
        )
        expired_session = {
            **session_record("y" * 43),
            "sessionHash": hash_v2_secret("y" * 43),
            "createdAt": SEC + 101,
            "lastUsedAt": SEC + 101,
            "expiresAt": SEC + 101,
        }
        invite_raw_before = self.client.command(["GET", self._invite_keys(expired_invite)[0]])
        expired_exchange = redis_store._atomic_exchange_v2_invite(
            raw_token="x" * 43,
            invite_id=expired_invite["inviteId"],
            session_record=expired_session,
            now=SEC + 101,
            session_ttl=1,
            command_transport=self.client.transport,
        )
        self.assertEqual(expired_exchange.get("error"), {"code": "storage_protocol_error"})
        self.assertIsNone(self.client.command(["GET", self._session_key(expired_session)]))
        self.assertEqual(
            self.client.command(["GET", self._invite_keys(expired_invite)[0]]), invite_raw_before
        )

    def test_real_invite_raw_size_boundaries_gate_migration_and_post_duplicate_validation(self):
        old_secret = b"o" * 32
        new_secret = b"n" * 32
        old_encoded = base64.urlsafe_b64encode(old_secret).decode("ascii").rstrip("=")
        new_encoded = base64.urlsafe_b64encode(new_secret).decode("ascii").rstrip("=")
        canonical = invite_record("t" * 43)
        proposal = self._duplicate_invite_proposal(canonical)
        canonical_primary = redis_store.build_v2_invite_key(canonical["inviteId"])
        canonical_token = redis_store.build_v2_invite_token_key(canonical["tokenHash"])
        proposed_primary, proposed_token, _ = self._invite_keys(proposal, hmac_key=new_secret)
        old_identity = self._invite_keys(canonical, hmac_key=old_secret)[2]
        new_identity = self._invite_keys(canonical, hmac_key=new_secret)[2]

        with patch.dict(os.environ, {}, clear=False):
            for size in (16_383, 16_384, 16_385):
                with self.subTest(path="migration", raw_bytes=size):
                    self.client.command(["FLUSHALL"])
                    os.environ[redis_store.V2_INDEX_HMAC_ENV] = old_encoded
                    os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
                    created = redis_store._create_v2_invite(
                        canonical,
                        now=canonical["createdAt"],
                        command_transport=self.client.transport,
                    )
                    self.assertTrue(created["created"])
                    padded = pad_json(wire_json(canonical, "invite"), size)
                    self.client.command(["SET", canonical_primary, padded, "PX", 60_000])
                    self.client.command(["SET", old_identity, padded, "PX", 55_000])
                    self.client.command(
                        ["SET", canonical_token, canonical["inviteId"], "PX", 50_000]
                    )
                    unchanged_keys = (canonical_primary, canonical_token)
                    unchanged_values = tuple(
                        self.client.command(["GET", key]) for key in unchanged_keys
                    )
                    unchanged_ttls = self._pttls(*unchanged_keys)
                    old_identity_pttl = self.client.command(["PTTL", old_identity])

                    os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
                    os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
                    before = self._snapshot_v2_state() if size > 16_384 else None
                    resolved = redis_store._create_v2_invite(
                        proposal,
                        now=proposal["createdAt"],
                        command_transport=self.client.transport,
                    )

                    if size <= 16_384:
                        self.assertEqual(resolved.get("status"), "ok", resolved)
                        self.assertFalse(resolved["created"])
                        self.assertEqual(resolved["record"], canonical)
                        self.assertIsNone(self.client.command(["GET", old_identity]))
                        self.assertEqual(self.client.command(["GET", new_identity]), padded)
                        new_identity_pttl = self.client.command(["PTTL", new_identity])
                        self.assertGreater(new_identity_pttl, 0)
                        self.assertLessEqual(new_identity_pttl, old_identity_pttl)
                        self.assertEqual(
                            tuple(self.client.command(["GET", key]) for key in unchanged_keys),
                            unchanged_values,
                        )
                        self._assert_ttls_not_refreshed(
                            unchanged_ttls, self._pttls(*unchanged_keys)
                        )
                    else:
                        self.assertNotEqual(resolved.get("status"), "ok", resolved)
                        self._assert_v2_state_unchanged(before)
                        self.assertIsNone(self.client.command(["GET", new_identity]))
                    self.assertIsNone(self.client.command(["GET", proposed_primary]))
                    self.assertIsNone(self.client.command(["GET", proposed_token]))

            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
            for size in (16_383, 16_384, 16_385):
                with self.subTest(path="post_duplicate_validation", raw_bytes=size):
                    self.client.command(["FLUSHALL"])
                    created = redis_store._create_v2_invite(
                        canonical,
                        now=canonical["createdAt"],
                        command_transport=self.client.transport,
                    )
                    self.assertTrue(created["created"])
                    identity = self._invite_keys(canonical, hmac_key=new_secret)[2]
                    protected = (canonical_primary, canonical_token, identity)
                    padded = pad_json(wire_json(canonical, "invite"), size)
                    injected = False
                    injected_values = None
                    injected_ttls = None

                    def inject_after_duplicate(command):
                        nonlocal injected, injected_values, injected_ttls
                        response = self.client.transport(command)
                        if (
                            not injected
                            and command[0] == "EVAL"
                            and command[1] == redis_store._CREATE_V2_INVITE_LUA
                            and json.loads(response["result"]).get("status") == "duplicate"
                        ):
                            injected = True
                            for key in (canonical_primary, identity):
                                remaining = self.client.command(["PTTL", key])
                                self.client.command(["SET", key, padded, "PX", remaining])
                            injected_values = tuple(
                                self.client.command(["GET", key]) for key in protected
                            )
                            injected_ttls = self._pttls(*protected)
                        return response

                    resolved = redis_store._create_v2_invite(
                        proposal,
                        now=proposal["createdAt"],
                        command_transport=inject_after_duplicate,
                    )
                    self.assertTrue(injected)
                    if size <= 16_384:
                        self.assertEqual(resolved.get("status"), "ok", resolved)
                        self.assertFalse(resolved["created"])
                    else:
                        self.assertNotEqual(resolved.get("status"), "ok", resolved)
                    self.assertEqual(
                        tuple(self.client.command(["GET", key]) for key in protected),
                        injected_values,
                    )
                    self._assert_ttls_not_refreshed(
                        injected_ttls, self._pttls(*protected)
                    )
                    self.assertIsNone(self.client.command(["GET", proposed_primary]))
                    self.assertIsNone(self.client.command(["GET", proposed_token]))

    def test_real_invite_current_and_previous_hmac_duplicates_validate_complete_graph(self):
        old_secret = b"o" * 32
        new_secret = b"n" * 32
        old_encoded = base64.urlsafe_b64encode(old_secret).decode("ascii").rstrip("=")
        new_encoded = base64.urlsafe_b64encode(new_secret).decode("ascii").rstrip("=")
        canonical = invite_record("t" * 43)
        proposal = self._duplicate_invite_proposal(canonical)

        with patch.dict(os.environ, {}, clear=False):
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
            created = redis_store._create_v2_invite(
                canonical, now=canonical["createdAt"], command_transport=self.client.transport
            )
            self.assertTrue(created["created"])
            canonical_keys = self._invite_keys(canonical, hmac_key=new_secret)
            before_values = tuple(self.client.command(["GET", key]) for key in canonical_keys)
            before_ttls = self._pttls(*canonical_keys)
            duplicate = redis_store._create_v2_invite(
                proposal, now=proposal["createdAt"], command_transport=self.client.transport
            )
            self.assertFalse(duplicate["created"])
            self.assertEqual(duplicate["record"], canonical)
            self.assertEqual(
                tuple(self.client.command(["GET", key]) for key in canonical_keys), before_values
            )
            self._assert_ttls_not_refreshed(before_ttls, self._pttls(*canonical_keys))
            self.assertIsNone(self.client.command(["GET", self._invite_keys(proposal)[0]]))
            self.assertIsNone(self.client.command(["GET", self._invite_keys(proposal)[1]]))

            def replace_both(mutator):
                def arrange(client, primary, _token, identity):
                    changed = json.loads(wire_json(canonical, "invite"))
                    mutator(changed)
                    raw = compact_json(changed)
                    client.command(["SET", primary, raw, "PX", 60_000])
                    client.command(["SET", identity, raw, "PX", 60_000])
                return arrange

            def replace_token_hash(value):
                value["tokenHash"] = "f" * 64

            def replace_creator_display(value):
                value["createdBy"]["displayName"] = "Other owner display"

            def replace_creator_owner(value):
                value["ownerEmail"] = "other@example.com"
                value["workspaceId"] = "other@example.com"
                value["createdBy"]["ownerEmail"] = "other@example.com"

            def replace_created_at(value):
                value["createdAt"] = SEC + 99

            def replace_expiry(value):
                value["expiresAt"] = SEC + 199

            def extend_expiry(value):
                value["expiresAt"] = SEC + 201

            def replace_actions(value):
                value["allowedActions"] = ["read"]

            def replace_visibility(value):
                value["visibility"] = "internal"

            def replace_assurance(value):
                value["identityAssurance"] = "email"

            def replace_status(value):
                value["status"] = "expired"

            def add_field(value):
                value["unexpected"] = "replaced"

            def replace_pointer(client, _primary, token, _identity):
                client.command(["SET", token, "K" * 22, "PX", 60_000])

            def replace_identity(client, _primary, _token, identity):
                changed = self._duplicate_invite_proposal(
                    canonical, invite_id="K" * 22, raw_token="v" * 43
                )
                client.command(["SET", identity, wire_json(changed, "invite"), "PX", 60_000])

            def replace_malformed(client, primary, _token, _identity):
                client.command(["SET", primary, "not-json", "PX", 60_000])

            post_eval_replacements = {
                "token_hash": replace_both(replace_token_hash),
                "created_by_display": replace_both(replace_creator_display),
                "created_by_owner": replace_both(replace_creator_owner),
                "created_at": replace_both(replace_created_at),
                "expires_at": replace_both(replace_expiry),
                "extended_expiry": replace_both(extend_expiry),
                "actions": replace_both(replace_actions),
                "visibility": replace_both(replace_visibility),
                "assurance": replace_both(replace_assurance),
                "status": replace_both(replace_status),
                "token_pointer": replace_pointer,
                "identity_index": replace_identity,
                "extra_field": replace_both(add_field),
                "malformed": replace_malformed,
            }
            for graph_mode in ("current", "hmac_migration"):
                for case, arrange in post_eval_replacements.items():
                    with self.subTest(graph_mode=graph_mode, post_eval_replacement=case):
                        self.client.command(["FLUSHALL"])
                        if graph_mode == "hmac_migration":
                            os.environ[redis_store.V2_INDEX_HMAC_ENV] = old_encoded
                            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
                        else:
                            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
                            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
                        redis_store._create_v2_invite(
                            canonical,
                            now=canonical["createdAt"],
                            command_transport=self.client.transport,
                        )
                        old_identity = self._invite_keys(canonical, hmac_key=old_secret)[2]
                        if graph_mode == "hmac_migration":
                            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
                            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
                        primary, token, identity = self._invite_keys(
                            canonical, hmac_key=new_secret
                        )
                        protected = (
                            primary, token, identity, old_identity,
                            *self._invite_keys(proposal, hmac_key=new_secret)[:2],
                        )
                        injected_values = []
                        injected_ttls = []
                        observed_commands = []
                        injected = False

                        def replace_after_create_eval(command):
                            nonlocal injected
                            observed_commands.append(command)
                            response = self.client.transport(command)
                            if (
                                not injected
                                and command[0] == "EVAL"
                                and command[1] == redis_store._CREATE_V2_INVITE_LUA
                                and json.loads(response["result"]).get("status") == "duplicate"
                            ):
                                injected = True
                                replacement_client = _RespClient(self.socket_path)
                                arrange(replacement_client, primary, token, identity)
                                injected_values.append(
                                    tuple(replacement_client.command(["GET", key]) for key in protected)
                                )
                                injected_ttls.append(
                                    self._sample_pttls(replacement_client, *protected)
                                )
                            return response

                        rejected = redis_store._create_v2_invite(
                            proposal,
                            now=proposal["createdAt"],
                            command_transport=replace_after_create_eval,
                        )
                        self.assertTrue(injected)
                        self.assertNotEqual(rejected.get("status"), "ok", rejected)
                        self.assertNotIn("record", rejected)
                        self.assertEqual(
                            tuple(self.client.command(["GET", key]) for key in protected),
                            injected_values[0],
                        )
                        self._assert_ttls_not_refreshed(
                            injected_ttls[0], self._pttls(*protected)
                        )
                        self.assertNotIn("t" * 43, repr(observed_commands))
                        self.assertNotIn("u" * 43, repr(observed_commands))

            self.client.command(["FLUSHALL"])
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = old_encoded
            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
            redis_store._create_v2_invite(
                canonical, now=canonical["createdAt"], command_transport=self.client.transport
            )
            old_identity = self._invite_keys(canonical, hmac_key=old_secret)[2]
            new_identity = self._invite_keys(canonical, hmac_key=new_secret)[2]
            canonical_primary = redis_store.build_v2_invite_key(canonical["inviteId"])
            canonical_token = redis_store.build_v2_invite_token_key(canonical["tokenHash"])
            protected_values = (
                self.client.command(["GET", canonical_primary]),
                self.client.command(["GET", canonical_token]),
            )
            old_identity_ttl = self.client.command(["PTTL", old_identity])

            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
            migrated = redis_store._create_v2_invite(
                proposal, now=proposal["createdAt"], command_transport=self.client.transport
            )
            self.assertFalse(migrated["created"])
            self.assertEqual(migrated["record"], canonical)
            self.assertIsNone(self.client.command(["GET", old_identity]))
            self.assertEqual(typed_wire_json(self.client.command(["GET", new_identity]), "invite"), canonical)
            self.assertGreater(self.client.command(["PTTL", new_identity]), 0)
            self.assertLessEqual(self.client.command(["PTTL", new_identity]), old_identity_ttl)
            self.assertEqual(
                (
                    self.client.command(["GET", canonical_primary]),
                    self.client.command(["GET", canonical_token]),
                ),
                protected_values,
            )
            self.assertIsNone(self.client.command(["GET", self._invite_keys(proposal)[0]]))
            self.assertIsNone(self.client.command(["GET", self._invite_keys(proposal)[1]]))

            self.client.command(["FLUSHALL"])
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = old_encoded
            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
            redis_store._create_v2_invite(
                canonical, now=canonical["createdAt"], command_transport=self.client.transport
            )
            old_raw = self.client.command(["GET", old_identity])
            old_pttl = self.client.command(["PTTL", old_identity])
            self.client.command(["SET", new_identity, old_raw, "PX", old_pttl])
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
            current_before = self.client.command(["GET", new_identity])
            current_ttl_before = self._pttls(new_identity)
            same = redis_store._create_v2_invite(
                proposal, now=proposal["createdAt"], command_transport=self.client.transport
            )
            self.assertFalse(same["created"])
            self.assertEqual(same["record"], canonical)
            self.assertEqual(self.client.command(["GET", new_identity]), current_before)
            self._assert_ttls_not_refreshed(current_ttl_before, self._pttls(new_identity))
            self.assertIsNone(self.client.command(["GET", old_identity]))

    def test_real_invite_hmac_conflicts_and_scope_mismatches_never_write_or_refresh(self):
        old_secret = b"p" * 32
        new_secret = b"c" * 32
        old_encoded = base64.urlsafe_b64encode(old_secret).decode("ascii").rstrip("=")
        new_encoded = base64.urlsafe_b64encode(new_secret).decode("ascii").rstrip("=")
        canonical = invite_record("t" * 43)
        proposal = self._duplicate_invite_proposal(canonical)
        old_identity = self._invite_keys(canonical, hmac_key=old_secret)[2]
        new_identity = self._invite_keys(canonical, hmac_key=new_secret)[2]

        with patch.dict(os.environ, {}, clear=False):
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded

            current_invite = canonical
            previous_invite = self._duplicate_invite_proposal(
                canonical, invite_id="K" * 22, raw_token="v" * 43
            )
            self._put_invitation_graph(current_invite, new_identity)
            self._put_invitation_graph(previous_invite, old_identity)
            proposed_keys = self._invite_keys(proposal, hmac_key=new_secret)[:2]
            protected = (
                *self._invite_keys(current_invite, hmac_key=new_secret)[:2],
                *self._invite_keys(previous_invite, hmac_key=old_secret)[:2],
                new_identity,
                old_identity,
                *proposed_keys,
            )
            before_values = tuple(self.client.command(["GET", key]) for key in protected)
            before_ttls = self._pttls(*protected)
            conflict = redis_store._create_v2_invite(
                proposal, now=proposal["createdAt"], command_transport=self.client.transport
            )
            self.assertEqual(conflict.get("error"), {"code": "invalid_request"})
            self.assertEqual(
                tuple(self.client.command(["GET", key]) for key in protected), before_values
            )
            self._assert_ttls_not_refreshed(before_ttls, self._pttls(*protected))

            corruptions = {
                "wrong_owner": lambda value: value.update(
                    ownerEmail="other@example.com",
                    workspaceId="other@example.com",
                    createdBy={"ownerEmail": "other@example.com", "displayName": "Other"},
                ),
                "wrong_workspace": lambda value: value.__setitem__("workspaceId", "other@example.com"),
                "wrong_mailbox": lambda value: value.__setitem__("mailboxId", "mailbox-other"),
                "wrong_collaboration": lambda value: value.__setitem__("collaborationId", "B" * 22),
                "wrong_invitee": lambda value: value.__setitem__("invitedEmail", "other@example.com"),
                "wrong_actions": lambda value: value.__setitem__("allowedActions", ["read"]),
                "wrong_visibility": lambda value: value.__setitem__("visibility", "internal"),
                "wrong_assurance": lambda value: value.__setitem__("identityAssurance", "email"),
                "wrong_token_hash": lambda value: value.__setitem__("tokenHash", "f" * 64),
            }
            for case, corrupt in corruptions.items():
                with self.subTest(case=case):
                    self.client.command(["FLUSHALL"])
                    changed = json.loads(wire_json(canonical, "invite"))
                    corrupt(changed)
                    canonical_primary, canonical_token, _ = self._put_invitation_graph(
                        canonical, old_identity
                    )
                    self.client.command(["SET", canonical_primary, compact_json(changed), "EX", 90])
                    protected = (
                        canonical_primary,
                        canonical_token,
                        old_identity,
                        new_identity,
                        *self._invite_keys(proposal, hmac_key=new_secret)[:2],
                    )
                    before_values = tuple(self.client.command(["GET", key]) for key in protected)
                    before_ttls = self._pttls(*protected)
                    rejected = redis_store._create_v2_invite(
                        proposal, now=proposal["createdAt"], command_transport=self.client.transport
                    )
                    self.assertEqual(rejected.get("error"), {"code": "invalid_request"})
                    self.assertEqual(
                        tuple(self.client.command(["GET", key]) for key in protected), before_values
                    )
                    self._assert_ttls_not_refreshed(before_ttls, self._pttls(*protected))

    def test_real_invite_hmac_missing_malformed_and_terminal_links_fail_closed(self):
        old_secret = b"q" * 32
        new_secret = b"d" * 32
        old_encoded = base64.urlsafe_b64encode(old_secret).decode("ascii").rstrip("=")
        new_encoded = base64.urlsafe_b64encode(new_secret).decode("ascii").rstrip("=")
        canonical = invite_record("t" * 43)
        proposal = self._duplicate_invite_proposal(canonical)
        old_identity = self._invite_keys(canonical, hmac_key=old_secret)[2]
        new_identity = self._invite_keys(canonical, hmac_key=new_secret)[2]

        def missing_primary(primary, _token, _identity):
            self.client.command(["DEL", primary])

        def malformed_primary(primary, _token, _identity):
            self.client.command(["SET", primary, "not-json", "EX", 90])

        def missing_token(_primary, token, _identity):
            self.client.command(["DEL", token])

        def wrong_token_pointer(_primary, token, _identity):
            self.client.command(["SET", token, "K" * 22, "EX", 90])

        def extra_primary(primary, _token, _identity):
            changed = {**canonical, "unexpected": True}
            self.client.command(["SET", primary, wire_json(changed, "invite"), "EX", 90])

        def malformed_created_by(primary, _token, _identity):
            changed = {**canonical, "createdBy": {"ownerEmail": canonical["ownerEmail"], "displayName": ""}}
            self.client.command(["SET", primary, wire_json(changed, "invite"), "EX", 90])

        def expired_primary(primary, _token, _identity):
            changed = {**canonical, "status": "expired"}
            self.client.command(["SET", primary, wire_json(changed, "invite"), "EX", 90])

        def revoked_primary(primary, _token, _identity):
            changed = {
                **canonical,
                "status": "revoked",
                "revokedAt": SEC + 101,
                "revokedBy": canonical["ownerEmail"],
            }
            self.client.command(["SET", primary, wire_json(changed, "invite"), "EX", 90])

        def exchanged_primary(primary, _token, _identity):
            changed = {
                **canonical,
                "status": "exchanged",
                "exchangeCount": 1,
                "exchangedAt": SEC + 101,
                "activeSessionHash": "f" * 64,
            }
            self.client.command(["SET", primary, wire_json(changed, "invite"), "EX", 90])

        def malformed_identity(_primary, _token, identity):
            self.client.command(["SET", identity, "not-json", "EX", 90])

        def identity_to_unlinked_invitation(_primary, _token, identity):
            changed = {
                **canonical,
                "inviteId": "K" * 22,
                "tokenHash": hash_v2_secret("v" * 43),
            }
            self.client.command(["SET", identity, wire_json(changed, "invite"), "EX", 90])

        cases = {
            "missing_primary": missing_primary,
            "malformed_primary": malformed_primary,
            "missing_token_pointer": missing_token,
            "wrong_token_pointer": wrong_token_pointer,
            "extra_invitation_fields": extra_primary,
            "malformed_created_by": malformed_created_by,
            "expired_invitation": expired_primary,
            "revoked_invitation": revoked_primary,
            "exchanged_invitation": exchanged_primary,
            "malformed_identity_index": malformed_identity,
            "identity_to_unlinked_invitation": identity_to_unlinked_invitation,
        }

        with patch.dict(os.environ, {}, clear=False):
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
            for case, arrange in cases.items():
                with self.subTest(case=case):
                    self.client.command(["FLUSHALL"])
                    primary, token, identity = self._put_invitation_graph(canonical, old_identity)
                    arrange(primary, token, identity)
                    protected = (
                        primary,
                        token,
                        identity,
                        new_identity,
                        *self._invite_keys(proposal, hmac_key=new_secret)[:2],
                    )
                    before_values = tuple(self.client.command(["GET", key]) for key in protected)
                    before_ttls = self._pttls(*protected)
                    rejected = redis_store._create_v2_invite(
                        proposal, now=proposal["createdAt"], command_transport=self.client.transport
                    )
                    self.assertEqual(rejected.get("error"), {"code": "invalid_request"})
                    self.assertEqual(
                        tuple(self.client.command(["GET", key]) for key in protected), before_values
                    )
                    self._assert_ttls_not_refreshed(before_ttls, self._pttls(*protected))

            self.client.command(["FLUSHALL"])
            primary, token, _ = self._put_invitation_graph(canonical, new_identity)
            self.client.command(["DEL", new_identity])
            protected = (primary, token, new_identity)
            before_values = tuple(self.client.command(["GET", key]) for key in protected)
            before_ttls = self._pttls(*protected)
            missing_index = redis_store._create_v2_invite(
                canonical, now=canonical["createdAt"], command_transport=self.client.transport
            )
            self.assertEqual(missing_index.get("error"), {"code": "invalid_request"})
            self.assertEqual(
                tuple(self.client.command(["GET", key]) for key in protected), before_values
            )
            self._assert_ttls_not_refreshed(before_ttls, self._pttls(*protected))

    def test_real_invite_previous_hmac_migration_race_returns_one_canonical_invitation(self):
        old_secret = b"r" * 32
        new_secret = b"e" * 32
        old_encoded = base64.urlsafe_b64encode(old_secret).decode("ascii").rstrip("=")
        new_encoded = base64.urlsafe_b64encode(new_secret).decode("ascii").rstrip("=")
        canonical = invite_record("t" * 43)
        proposal = self._duplicate_invite_proposal(canonical)
        old_identity = self._invite_keys(canonical, hmac_key=old_secret)[2]
        new_identity = self._invite_keys(canonical, hmac_key=new_secret)[2]

        with patch.dict(os.environ, {}, clear=False):
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = old_encoded
            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
            redis_store._create_v2_invite(
                canonical, now=canonical["createdAt"], command_transport=self.client.transport
            )
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
            barrier = threading.Barrier(2)

            def migrate(_index):
                client = _RespClient(self.socket_path)
                waited = False

                def transport(command):
                    nonlocal waited
                    if command[0] == "EVAL" and command[1] == redis_store._CREATE_V2_INVITE_LUA and not waited:
                        waited = True
                        barrier.wait(timeout=5)
                    return client.transport(command)

                return redis_store._create_v2_invite(
                    proposal, now=proposal["createdAt"], command_transport=transport
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(migrate, range(2)))
            self.assertTrue(all(result.get("status") == "ok" for result in results), results)
            self.assertTrue(all(result.get("created") is False for result in results), results)
            self.assertEqual({result["record"]["inviteId"] for result in results}, {canonical["inviteId"]})
            self.assertIsNone(self.client.command(["GET", old_identity]))
            self.assertEqual(typed_wire_json(self.client.command(["GET", new_identity]), "invite"), canonical)
            self.assertEqual(
                self.client.command(["KEYS", f"{redis_store.V2_KEY_PREFIX}:invite:*"]),
                [redis_store.build_v2_invite_key(canonical["inviteId"])],
            )
            self.assertIsNone(self.client.command(["GET", self._invite_keys(proposal)[0]]))
            self.assertIsNone(self.client.command(["GET", self._invite_keys(proposal)[1]]))

    def test_real_exchange_rejects_every_supplied_session_schema_corruption_without_writes(self):
        raw_token = "e" * 43
        invite = {
            **invite_record(raw_token),
            "expiresAt": SEC + 100 + 86_400,
        }
        canonical = session_record("q" * 43)
        corruptions = (
            ("missing_required_field", lambda value: value.pop("guestDisplayName")),
            ("extra_field", lambda value: value.__setitem__("unexpected", True)),
            ("incorrect_version", lambda value: value.__setitem__("v", 3)),
            ("incorrect_invitation_id", lambda value: value.__setitem__("inviteId", "J" * 22)),
            ("incorrect_collaboration_id", lambda value: value.__setitem__("collaborationId", "B" * 22)),
            ("wrong_owner", lambda value: value.__setitem__("ownerEmail", "attacker@example.com")),
            ("wrong_workspace", lambda value: value.__setitem__("workspaceId", "attacker@example.com")),
            ("wrong_mailbox", lambda value: value.__setitem__("mailboxId", "mailbox-other")),
            ("incorrect_session_hash", lambda value: value.__setitem__("sessionHash", hash_v2_secret("r" * 43))),
            ("incorrect_csrf_hash", lambda value: value.__setitem__("csrfTokenHash", hash_v2_secret("d" * 43))),
            ("actions_wrong_order", lambda value: value.__setitem__("allowedActions", ["reply", "read"])),
            ("unsupported_actions", lambda value: value.__setitem__("allowedActions", ["read", "delete"])),
            ("wrong_visibility", lambda value: value.__setitem__("visibility", "internal")),
            ("wrong_status", lambda value: value.__setitem__("status", "revoked")),
            ("missing_created_at", lambda value: value.pop("createdAt")),
            ("created_at_noncanonical_string", lambda value: value.__setitem__("createdAt", "0" + str(SEC + 101))),
            ("created_at_float", lambda value: value.__setitem__("createdAt", SEC + 101.5)),
            ("created_at_unsafe", lambda value: value.__setitem__("createdAt", 2**53)),
            ("expiry_before_creation", lambda value: value.__setitem__("expiresAt", SEC + 100)),
            ("lifetime_above_eight_hours", lambda value: value.__setitem__("expiresAt", SEC + 101 + 28_801)),
            ("expiry_after_invitation", lambda value: value.__setitem__("expiresAt", invite["expiresAt"] + 1)),
            ("unexpected_audit_timestamp", lambda value: value.__setitem__("loggedOutAt", SEC + 101)),
        )

        for label, corrupt in corruptions:
            with self.subTest(corruption=label):
                self.client.command(["FLUSHALL"])
                created = redis_store._create_v2_invite(
                    invite, now=invite["createdAt"], command_transport=self.client.transport
                )
                self.assertEqual(created["status"], "ok")
                invite_keys = self._invite_keys(invite)
                before_values = tuple(self.client.command(["GET", key]) for key in invite_keys)
                before_ttls = self._pttls(*invite_keys)
                corrupted = json.loads(wire_json(canonical, "session"))
                corrupt(corrupted)

                def replace_session(command, argument_start, value=corrupted):
                    command[argument_start + 3] = compact_json(value)

                rejected = redis_store._atomic_exchange_v2_invite(
                    raw_token=raw_token,
                    invite_id=invite["inviteId"],
                    session_record=canonical,
                    now=canonical["createdAt"],
                    session_ttl=canonical["expiresAt"] - canonical["createdAt"],
                    command_transport=self._transport_mutating_eval(
                        redis_store._EXCHANGE_V2_INVITE_LUA, replace_session
                    ),
                )
                self.assertEqual(rejected.get("error"), {"code": "storage_protocol_error"})
                after_values = tuple(self.client.command(["GET", key]) for key in invite_keys)
                self.assertEqual(after_values, before_values)
                stored_invite = typed_wire_json(after_values[0], "invite")
                self.assertEqual(stored_invite["status"], "active")
                self.assertEqual(stored_invite["exchangeCount"], 0)
                self.assertNotIn("activeSessionHash", stored_invite)
                self.assertIsNone(self.client.command(["GET", self._session_key(canonical)]))
                self.assertEqual(
                    self.client.command(["KEYS", f"{redis_store.V2_KEY_PREFIX}:guest-session:*"]), []
                )
                self._assert_ttls_not_refreshed(before_ttls, self._pttls(*invite_keys))

    def test_real_exchange_race_replay_and_ambiguous_retry_leave_only_one_session(self):
        raw_token = "t" * 43
        invite = invite_record(raw_token)
        created = redis_store._create_v2_invite(
            invite, now=invite["createdAt"], command_transport=self.client.transport
        )
        self.assertEqual(created["status"], "ok")
        sessions = (session_record("s" * 43), session_record("z" * 43))
        barrier = threading.Barrier(2)

        def exchange(session):
            independent_client = _RespClient(self.socket_path)
            barrier.wait()
            return redis_store._atomic_exchange_v2_invite(
                raw_token=raw_token,
                invite_id=invite["inviteId"],
                session_record=session,
                now=session["createdAt"],
                session_ttl=session["expiresAt"] - session["createdAt"],
                command_transport=independent_client.transport,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(exchange, sessions))
        self.assertEqual(sum(result == {"status": "ok"} for result in results), 1)
        self.assertEqual(
            sum(result.get("error") == {"code": "invite_already_exchanged"} for result in results), 1
        )

        session_keys = tuple(self._session_key(session) for session in sessions)
        existing = tuple(self.client.command(["GET", key]) is not None for key in session_keys)
        self.assertEqual(sum(existing), 1)
        winning_index = existing.index(True)
        losing_index = 1 - winning_index
        self.assertIsNone(self.client.command(["GET", session_keys[losing_index]]))
        stored_invite = typed_wire_json(self.client.command(["GET", self._invite_keys(invite)[0]]), "invite")
        self.assertEqual(stored_invite["activeSessionHash"], sessions[winning_index]["sessionHash"])
        self.assertEqual(stored_invite["exchangeCount"], 1)

        for session in sessions:
            replay = redis_store._atomic_exchange_v2_invite(
                raw_token=raw_token,
                invite_id=invite["inviteId"],
                session_record=session,
                now=session["createdAt"] + 1,
                session_ttl=session["expiresAt"] - session["createdAt"] - 1,
                command_transport=self.client.transport,
            )
            self.assertEqual(replay.get("error"), {"code": "invite_already_exchanged"})
        self.assertEqual(
            tuple(self.client.command(["GET", key]) is not None for key in session_keys), existing
        )
        self.assertEqual(
            len(self.client.command(["KEYS", f"{redis_store.V2_KEY_PREFIX}:guest-session:*"])), 1
        )

        self.client.command(["FLUSHALL"])
        ambiguous_token = "a" * 43
        ambiguous_invite = {
            **invite_record(ambiguous_token),
            "inviteId": "K" * 22,
            "tokenHash": hash_v2_secret(ambiguous_token),
        }
        ambiguous_session = {
            **session_record("b" * 43),
            "inviteId": ambiguous_invite["inviteId"],
            "sessionHash": hash_v2_secret("b" * 43),
        }
        redis_store._create_v2_invite(
            ambiguous_invite,
            now=ambiguous_invite["createdAt"],
            command_transport=self.client.transport,
        )

        def commit_exchange_then_drop(command):
            if command[0] == "EVAL" and command[1] == redis_store._EXCHANGE_V2_INVITE_LUA:
                self.client.command(command)
                return {"error": "simulated response loss"}
            return self.client.transport(command)

        lost = redis_store._atomic_exchange_v2_invite(
            raw_token=ambiguous_token,
            invite_id=ambiguous_invite["inviteId"],
            session_record=ambiguous_session,
            now=ambiguous_session["createdAt"],
            session_ttl=ambiguous_session["expiresAt"] - ambiguous_session["createdAt"],
            command_transport=commit_exchange_then_drop,
        )
        self.assertEqual(lost.get("error"), {"code": "atomic_exchange_unavailable"})
        retried = redis_store._atomic_exchange_v2_invite(
            raw_token=ambiguous_token,
            invite_id=ambiguous_invite["inviteId"],
            session_record=ambiguous_session,
            now=ambiguous_session["createdAt"],
            session_ttl=ambiguous_session["expiresAt"] - ambiguous_session["createdAt"],
            command_transport=self.client.transport,
        )
        self.assertEqual(retried.get("error"), {"code": "invite_already_exchanged"})
        ambiguous_key = self._session_key(ambiguous_session)
        self.assertIsNotNone(self.client.command(["GET", ambiguous_key]))
        self.assertEqual(
            self.client.command(["KEYS", f"{redis_store.V2_KEY_PREFIX}:guest-session:*"]),
            [ambiguous_key],
        )

    def test_real_revocation_scope_mismatches_preserve_linked_state_and_ttls(self):
        mutations = (
            ("wrong_owner", 1, "attacker@example.com"),
            ("wrong_workspace", 2, "attacker@example.com"),
            ("wrong_mailbox", 3, "mailbox-other"),
            ("wrong_collaboration", 4, "B" * 22),
            ("wrong_invitation", 5, "J" * 22),
            ("different_revocation_actor", 6, "attacker@example.com"),
            ("display_name_revocation_actor", 6, "Owner"),
            ("noncanonical_revocation_actor", 6, "Owner@example.com"),
            ("wrong_action", 7, "delete_invite"),
        )
        for label, argument_number, value in mutations:
            with self.subTest(mismatch=label):
                self.client.command(["FLUSHALL"])
                invite, session, invite_keys, session_key = self._create_exchanged_invitation()
                protected_keys = (invite_keys[0], session_key)
                before_values = tuple(self.client.command(["GET", key]) for key in protected_keys)
                before_ttls = self._pttls(*protected_keys)

                def mutate_scope(command, argument_start, index=argument_number, replacement=value):
                    command[argument_start + index - 1] = replacement

                denied = redis_store._revoke_v2_invite(
                    invite["inviteId"],
                    owner_email=invite["ownerEmail"],
                    workspace_id=invite["workspaceId"],
                    mailbox_id=invite["mailboxId"],
                    collaboration_id=invite["collaborationId"],
                    revoked_by=invite["ownerEmail"],
                    now=SEC + 102,
                    command_transport=self._transport_mutating_eval(
                        redis_store._REVOKE_V2_INVITE_LUA, mutate_scope
                    ),
                )
                self.assertEqual(denied.get("error"), {"code": "forbidden"})
                after_values = tuple(self.client.command(["GET", key]) for key in protected_keys)
                self.assertEqual(after_values, before_values)
                stored_invite = typed_wire_json(after_values[0], "invite")
                stored_session = typed_wire_json(after_values[1], "session")
                self.assertEqual(stored_invite["status"], "exchanged")
                self.assertIsNone(stored_invite["revokedAt"])
                self.assertIsNone(stored_invite["revokedBy"])
                self.assertEqual(stored_session["status"], "active")
                self.assertIsNone(stored_session["revokedAt"])
                self._assert_ttls_not_refreshed(before_ttls, self._pttls(*protected_keys))

    def test_real_revocation_atomically_updates_invitation_and_linked_session(self):
        invite, session, invite_keys, session_key = self._create_exchanged_invitation()
        result = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 102,
            command_transport=self.client.transport,
        )
        self.assertEqual(result, {"status": "ok"})
        stored_invite = typed_wire_json(self.client.command(["GET", invite_keys[0]]), "invite")
        stored_session = typed_wire_json(self.client.command(["GET", session_key]), "session")
        self.assertEqual(stored_invite["status"], "revoked")
        self.assertEqual(stored_invite["revokedAt"], SEC + 102)
        self.assertEqual(stored_invite["revokedBy"], invite["ownerEmail"])
        self.assertEqual(stored_invite["activeSessionHash"], session["sessionHash"])
        self.assertEqual(stored_session["status"], "revoked")
        self.assertEqual(stored_session["revokedAt"], SEC + 102)
        self.assertEqual(stored_session["ownerEmail"], invite["ownerEmail"])
        self.assertEqual(stored_session["sessionHash"], stored_invite["activeSessionHash"])
        self.assertEqual(self.client.command(["GET", invite_keys[1]]), invite["inviteId"])
        self.assertEqual(typed_wire_json(self.client.command(["GET", invite_keys[2]]), "invite")["inviteId"], invite["inviteId"])
        self._assert_ttl_ceiling(invite_keys[0], invite["expiresAt"] - (SEC + 102))
        self._assert_ttl_ceiling(session_key, session["expiresAt"] - (SEC + 102))

    def test_revocation_actor_preflight_repeat_and_malformed_storage_are_owner_bound(self):
        invite, _, invite_keys, _ = self._create_exchanged_invitation()
        for actor in ("attacker@example.com", "Owner", "Owner@example.com"):
            with self.subTest(path="wrapper_preflight", actor=actor):
                before = self._snapshot_v2_state()
                observed = []

                def observe(command):
                    observed.append(command)
                    return self.client.transport(command)

                denied = redis_store._revoke_v2_invite(
                    invite["inviteId"],
                    owner_email=invite["ownerEmail"],
                    workspace_id=invite["workspaceId"],
                    mailbox_id=invite["mailboxId"],
                    collaboration_id=invite["collaborationId"],
                    revoked_by=actor,
                    now=SEC + 102,
                    command_transport=observe,
                )
                self.assertEqual(
                    denied,
                    {"status": "malformed", "error": {"code": "invalid_request"}},
                )
                self.assertEqual(observed, [])
                self._assert_v2_state_unchanged(before)

        first = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 102,
            command_transport=self.client.transport,
        )
        self.assertEqual(first, {"status": "ok"})
        before_repeat = self._snapshot_v2_state()
        repeated = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by="attacker@example.com",
            now=SEC + 103,
            command_transport=self.client.transport,
        )
        self.assertEqual(
            repeated,
            {"status": "malformed", "error": {"code": "invalid_request"}},
        )
        self._assert_v2_state_unchanged(before_repeat)
        stored = typed_wire_json(self.client.command(["GET", invite_keys[0]]), "invite")
        self.assertEqual(stored["revokedBy"], invite["ownerEmail"])

        malformed = json.loads(self.client.command(["GET", invite_keys[0]]))
        malformed["revokedBy"] = "attacker@example.com"
        remaining = self.client.command(["PTTL", invite_keys[0]])
        self.client.command(["SET", invite_keys[0], compact_json(malformed), "PX", remaining])
        before_malformed = self._snapshot_v2_state()
        rejected = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 103,
            command_transport=self.client.transport,
        )
        self.assertEqual(rejected.get("error"), {"code": "storage_protocol_error"})
        self._assert_v2_state_unchanged(before_malformed)

    def test_real_repeated_revocation_preserves_first_audit_and_does_not_extend_ttls(self):
        invite, session, invite_keys, session_key = self._create_exchanged_invitation()
        first = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 102,
            command_transport=self.client.transport,
        )
        self.assertEqual(first, {"status": "ok"})
        protected_keys = (invite_keys[0], session_key)
        first_values = tuple(self.client.command(["GET", key]) for key in protected_keys)
        first_ttls = self._pttls(*protected_keys)
        second = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 103,
            command_transport=self.client.transport,
        )
        self.assertEqual(second, {"status": "already_revoked", "error": {"code": "already_revoked"}})
        second_values = tuple(self.client.command(["GET", key]) for key in protected_keys)
        self.assertEqual(second_values, first_values)
        stored_invite = typed_wire_json(second_values[0], "invite")
        stored_session = typed_wire_json(second_values[1], "session")
        self.assertEqual((stored_invite["revokedAt"], stored_invite["revokedBy"]), (SEC + 102, invite["ownerEmail"]))
        self.assertEqual(stored_session["revokedAt"], SEC + 102)
        self._assert_ttls_not_refreshed(first_ttls, self._pttls(*protected_keys))

    def test_real_revocation_rejects_every_malformed_linked_session_without_writes(self):
        corruptions = (
            ("missing_session", None),
            ("wrong_version", lambda value: value.__setitem__("v", 3)),
            ("wrong_invitation", lambda value: value.__setitem__("inviteId", "J" * 22)),
            ("wrong_collaboration", lambda value: value.__setitem__("collaborationId", "B" * 22)),
            ("wrong_owner", lambda value: value.__setitem__("ownerEmail", "attacker@example.com")),
            ("wrong_workspace", lambda value: value.__setitem__("workspaceId", "attacker@example.com")),
            ("wrong_mailbox", lambda value: value.__setitem__("mailboxId", "mailbox-other")),
            ("wrong_actions", lambda value: value.__setitem__("allowedActions", ["reply", "read"])),
            ("wrong_visibility", lambda value: value.__setitem__("visibility", "internal")),
            ("invalid_status", lambda value: value.__setitem__("status", "unknown")),
            (
                "excessive_lifetime",
                lambda value: value.__setitem__(
                    "expiresAt", str(int(value["createdAt"]) + 28_801)
                ),
            ),
            (
                "malformed_timestamp",
                lambda value: value.__setitem__("createdAt", "0" + value["createdAt"]),
            ),
            ("extra_field", lambda value: value.__setitem__("unexpected", True)),
        )
        for label, corrupt in corruptions:
            with self.subTest(corruption=label):
                self.client.command(["FLUSHALL"])
                invite, session, invite_keys, session_key = self._create_exchanged_invitation()
                invite_raw = self.client.command(["GET", invite_keys[0]])
                if corrupt is None:
                    self.client.command(["DEL", session_key])
                    session_raw = None
                else:
                    malformed = json.loads(wire_json(session, "session"))
                    corrupt(malformed)
                    session_raw = compact_json(malformed)
                    self.client.command(["SET", session_key, session_raw, "EX", 40])
                protected_keys = (invite_keys[0], session_key)
                before_ttls = self._pttls(*protected_keys)
                rejected = redis_store._revoke_v2_invite(
                    invite["inviteId"],
                    owner_email=invite["ownerEmail"],
                    workspace_id=invite["workspaceId"],
                    mailbox_id=invite["mailboxId"],
                    collaboration_id=invite["collaborationId"],
                    revoked_by=invite["ownerEmail"],
                    now=SEC + 102,
                    command_transport=self.client.transport,
                )
                self.assertEqual(rejected.get("error"), {"code": "storage_protocol_error"})
                self.assertEqual(self.client.command(["GET", invite_keys[0]]), invite_raw)
                self.assertEqual(self.client.command(["GET", session_key]), session_raw)
                stored_invite = typed_wire_json(invite_raw, "invite")
                self.assertEqual(stored_invite["status"], "exchanged")
                self.assertIsNone(stored_invite["revokedAt"])
                self.assertIsNone(stored_invite["revokedBy"])
                self._assert_ttls_not_refreshed(before_ttls, self._pttls(*protected_keys))

    def test_terminal_transitions_are_strict_and_only_equal_repeats_are_idempotent(self):
        invite, session, _, _ = self._create_exchanged_invitation()
        before_logout = self._snapshot_v2_state()
        equal_logout = revoke_guest_session(
            session,
            now=session["lastUsedAt"],
            command_transport=self.client.transport,
        )
        self.assertEqual(equal_logout.get("error"), {"code": "storage_protocol_error"})
        self._assert_v2_state_unchanged(before_logout)

        def force_equal_invite_revoke(command, argument_start):
            command[argument_start + 7] = str(session["lastUsedAt"])

        forced_equal = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 102,
            command_transport=self._transport_mutating_eval(
                redis_store._REVOKE_V2_INVITE_LUA, force_equal_invite_revoke
            ),
        )
        self.assertEqual(forced_equal.get("error"), {"code": "storage_protocol_error"})
        self._assert_v2_state_unchanged(before_logout)

        first_logout = revoke_guest_session(
            session, now=SEC + 102, command_transport=self.client.transport
        )
        self.assertEqual(first_logout, {"status": "ok"})
        after_logout = self._snapshot_v2_state()
        repeated_logout = revoke_guest_session(
            session, now=SEC + 102, command_transport=self.client.transport
        )
        self.assertEqual(
            repeated_logout,
            {"status": "already_logged_out", "error": {"code": "already_logged_out"}},
        )
        self._assert_v2_state_unchanged(after_logout)

        equal_after_terminal = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 102,
            command_transport=self.client.transport,
        )
        self.assertEqual(
            equal_after_terminal.get("error"), {"code": "storage_protocol_error"}
        )
        self._assert_v2_state_unchanged(after_logout)

        later_invite_revoke = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 103,
            command_transport=self.client.transport,
        )
        self.assertEqual(later_invite_revoke, {"status": "ok"})
        after_invite_revoke = self._snapshot_v2_state()
        repeated_invite_revoke = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 103,
            command_transport=self.client.transport,
        )
        self.assertEqual(
            repeated_invite_revoke,
            {"status": "already_revoked", "error": {"code": "already_revoked"}},
        )
        self._assert_v2_state_unchanged(after_invite_revoke)

    def test_session_creation_must_exactly_equal_invite_exchange_for_every_graph_consumer(self):
        for skewed_created_at in (SEC + 100, SEC + 102):
            for operation in (
                "guest_append",
                "session_update",
                "owner_revocation",
                "guest_logout",
                "python_bootstrap",
            ):
                with self.subTest(
                    operation=operation,
                    skewed_created_at=skewed_created_at,
                ):
                    self.client.command(["FLUSHALL"])
                    thread = thread_record()
                    if operation == "guest_append":
                        redis_store._create_v2_thread(
                            thread, command_transport=self.client.transport
                        )
                    invite, session, invite_keys, session_key = (
                        self._create_exchanged_invitation()
                    )
                    capability = (
                        self._guest_mutation_capability()
                        if operation == "guest_append"
                        else None
                    )
                    stored_invite = typed_wire_json(
                        self.client.command(["GET", invite_keys[0]]), "invite"
                    )
                    self.assertEqual(stored_invite["exchangedAt"], SEC + 101)
                    skewed_session = {
                        **session,
                        "createdAt": skewed_created_at,
                        "lastUsedAt": skewed_created_at,
                    }
                    self.assertIsNotNone(
                        guest_session.normalize_v2_guest_session_record(skewed_session)
                    )
                    remaining = self.client.command(["PTTL", session_key])
                    self.client.command(
                        [
                            "SET",
                            session_key,
                            wire_json(skewed_session, "session"),
                            "PX",
                            remaining,
                        ]
                    )
                    before = self._snapshot_v2_state()

                    if operation == "guest_append":
                        rejected = redis_store._append_v2_guest_reply_if_expected(
                            self._guest_replacement(thread),
                            thread["updatedAt"],
                            session_context=capability,
                            now=SEC + 103,
                            command_transport=self.client.transport,
                        )
                    elif operation == "session_update":
                        rejected = redis_store._update_v2_guest_session(
                            skewed_session,
                            normalizer=guest_session.normalize_v2_guest_session_record,
                            now=SEC + 103,
                            csrf_token_hash=hash_v2_secret("d" * 43),
                            command_transport=self.client.transport,
                        )
                    elif operation == "owner_revocation":
                        rejected = redis_store._revoke_v2_invite(
                            invite["inviteId"],
                            owner_email=invite["ownerEmail"],
                            workspace_id=invite["workspaceId"],
                            mailbox_id=invite["mailboxId"],
                            collaboration_id=invite["collaborationId"],
                            revoked_by=invite["ownerEmail"],
                            now=SEC + 103,
                            command_transport=self.client.transport,
                        )
                    elif operation == "guest_logout":
                        rejected = revoke_guest_session(
                            skewed_session,
                            now=SEC + 103,
                            command_transport=self.client.transport,
                        )
                    else:
                        rejected = guest_session._bootstrap_v2_guest_session_read_only(
                            "s" * 43,
                            now=SEC + 103,
                            command_transport=self.client.transport,
                        )

                    self.assertNotEqual(rejected.get("status"), "ok", rejected)
                    self._assert_v2_state_unchanged(before)

    def test_real_first_terminal_state_wins_for_logout_and_owner_revocation(self):
        invite, session, invite_keys, session_key = self._create_exchanged_invitation()
        self.client.command(["PEXPIRE", invite_keys[0], 60_000])
        self.client.command(["PEXPIRE", session_key, 30_000])
        before_ttls = self._pttls(invite_keys[0], session_key)
        active_revocation = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 102,
            command_transport=self.client.transport,
        )
        self.assertEqual(active_revocation, {"status": "ok"})
        active_invite = typed_wire_json(self.client.command(["GET", invite_keys[0]]), "invite")
        active_session = typed_wire_json(self.client.command(["GET", session_key]), "session")
        self.assertEqual((active_invite["status"], active_session["status"]), ("revoked", "revoked"))
        self.assertEqual((active_invite["revokedAt"], active_invite["revokedBy"]), (SEC + 102, invite["ownerEmail"]))
        self.assertEqual(active_session["revokedAt"], SEC + 102)
        self.assertIsNone(active_session["loggedOutAt"])
        self.assertIsNotNone(guest_session.normalize_v2_guest_session_record(active_session))
        self._assert_ttls_not_refreshed(before_ttls, self._pttls(invite_keys[0], session_key))

        self.client.command(["FLUSHALL"])
        invite, session, invite_keys, session_key = self._create_exchanged_invitation()
        logout = revoke_guest_session(
            session, now=SEC + 103, command_transport=self.client.transport
        )
        self.assertEqual(logout, {"status": "ok"})
        logged_out_raw = self.client.command(["GET", session_key])
        logged_out = typed_wire_json(logged_out_raw, "session")
        self.assertEqual(logged_out["status"], "logged_out")
        self.assertEqual(logged_out["loggedOutAt"], SEC + 103)
        self.assertIsNone(logged_out["revokedAt"])
        self.assertIsNotNone(guest_session.normalize_v2_guest_session_record(logged_out))
        logout_ttl = self._pttls(session_key)

        revoke_after_logout = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 104,
            command_transport=self.client.transport,
        )
        self.assertEqual(revoke_after_logout, {"status": "ok"})
        self.assertEqual(self.client.command(["GET", session_key]), logged_out_raw)
        preserved_logout = typed_wire_json(self.client.command(["GET", session_key]), "session")
        self.assertEqual(preserved_logout["status"], "logged_out")
        self.assertEqual(preserved_logout["loggedOutAt"], SEC + 103)
        self.assertIsNone(preserved_logout["revokedAt"])
        self.assertIsNotNone(guest_session.normalize_v2_guest_session_record(preserved_logout))
        self._assert_ttls_not_refreshed(logout_ttl, self._pttls(session_key))
        self.assertEqual(typed_wire_json(self.client.command(["GET", invite_keys[0]]), "invite")["status"], "revoked")

        repeated_logout = revoke_guest_session(
            session, now=SEC + 105, command_transport=self.client.transport
        )
        repeated_revocation = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 106,
            command_transport=self.client.transport,
        )
        self.assertEqual(repeated_logout.get("error"), {"code": "already_logged_out"})
        self.assertEqual(repeated_revocation.get("error"), {"code": "already_revoked"})
        self.assertEqual(self.client.command(["GET", session_key]), logged_out_raw)

        self.client.command(["FLUSHALL"])
        invite, session, invite_keys, session_key = self._create_exchanged_invitation()
        first_revocation = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 102,
            command_transport=self.client.transport,
        )
        self.assertEqual(first_revocation, {"status": "ok"})
        revoked_raw = self.client.command(["GET", session_key])
        revoked = typed_wire_json(revoked_raw, "session")
        revoked_ttl = self._pttls(session_key)
        repeated_revocation = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 103,
            command_transport=self.client.transport,
        )
        logout_after_revocation = revoke_guest_session(
            session, now=SEC + 104, command_transport=self.client.transport
        )
        self.assertEqual(repeated_revocation.get("error"), {"code": "already_revoked"})
        self.assertEqual(logout_after_revocation.get("error"), {"code": "already_logged_out"})
        self.assertEqual(self.client.command(["GET", session_key]), revoked_raw)
        self.assertEqual(revoked["status"], "revoked")
        self.assertEqual(revoked["revokedAt"], SEC + 102)
        self.assertIsNone(revoked["loggedOutAt"])
        self.assertIsNotNone(guest_session.normalize_v2_guest_session_record(revoked))
        self._assert_ttls_not_refreshed(revoked_ttl, self._pttls(session_key))

    def test_real_mixed_terminal_session_blocks_owner_revocation_without_partial_writes(self):
        invite, session, invite_keys, session_key = self._create_exchanged_invitation()
        malformed = {
            **session,
            "status": "logged_out",
            "loggedOutAt": SEC + 102,
            "revokedAt": SEC + 102,
        }
        malformed_raw = wire_json(malformed, "session")
        self.client.command(["SET", session_key, malformed_raw, "EX", 40])
        invite_raw = self.client.command(["GET", invite_keys[0]])
        before_ttls = self._pttls(invite_keys[0], session_key)
        rejected = redis_store._revoke_v2_invite(
            invite["inviteId"],
            owner_email=invite["ownerEmail"],
            workspace_id=invite["workspaceId"],
            mailbox_id=invite["mailboxId"],
            collaboration_id=invite["collaborationId"],
            revoked_by=invite["ownerEmail"],
            now=SEC + 103,
            command_transport=self.client.transport,
        )
        self.assertEqual(rejected.get("error"), {"code": "storage_protocol_error"})
        self.assertEqual(self.client.command(["GET", invite_keys[0]]), invite_raw)
        self.assertEqual(self.client.command(["GET", session_key]), malformed_raw)
        self._assert_ttls_not_refreshed(before_ttls, self._pttls(invite_keys[0], session_key))

    def test_real_previous_hmac_source_index_migrates_to_current_without_duplicate_thread(self):
        old_secret = b"o" * 32
        new_secret = b"n" * 32
        old_encoded = base64.urlsafe_b64encode(old_secret).decode("ascii").rstrip("=")
        new_encoded = base64.urlsafe_b64encode(new_secret).decode("ascii").rstrip("=")
        thread = thread_record()
        thread_key = self._thread_key(thread["collaborationId"])
        old_source_key = self._source_key(thread, hmac_key=old_secret)
        new_source_key = self._source_key(thread, hmac_key=new_secret)

        with patch.dict(os.environ, {}, clear=False):
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = old_encoded
            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
            created = redis_store._create_v2_thread(thread, command_transport=self.client.transport)
            self.assertTrue(created["created"])
            self.assertEqual(self.client.command(["GET", old_source_key]), thread["collaborationId"])
            self.assertIsNone(self.client.command(["GET", new_source_key]))

            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
            migrated = redis_store._create_v2_thread(thread, command_transport=self.client.transport)
            self.assertFalse(migrated["created"])
            self.assertEqual(migrated["record"]["collaborationId"], thread["collaborationId"])
            self.assertEqual(self.client.command(["GET", new_source_key]), thread["collaborationId"])
            self.assertIsNone(self.client.command(["GET", old_source_key]))
            self.assertEqual(
                self.client.command(["KEYS", f"{redis_store.V2_THREAD_KEY_PREFIX}*"]), [thread_key]
            )
            self._assert_retention_pair(thread_key, new_source_key)

            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
            current_only = redis_store._create_v2_thread(thread, command_transport=self.client.transport)
            self.assertFalse(current_only["created"])
            self.assertEqual(current_only["record"]["collaborationId"], thread["collaborationId"])
            self.assertEqual(self.client.command(["GET", new_source_key]), thread["collaborationId"])
            self.assertIsNone(self.client.command(["GET", old_source_key]))

    def test_real_conflicting_current_previous_hmac_indexes_fail_closed_without_writes(self):
        old_secret = b"p" * 32
        new_secret = b"c" * 32
        old_encoded = base64.urlsafe_b64encode(old_secret).decode("ascii").rstrip("=")
        new_encoded = base64.urlsafe_b64encode(new_secret).decode("ascii").rstrip("=")
        thread_a = thread_record()
        thread_b = {**thread_a, "collaborationId": "B" * 22}
        proposed = {**thread_a, "collaborationId": "C" * 22}
        key_a = self._thread_key(thread_a["collaborationId"])
        key_b = self._thread_key(thread_b["collaborationId"])
        key_c = self._thread_key(proposed["collaborationId"])
        current_source_key = self._source_key(thread_a, hmac_key=new_secret)
        previous_source_key = self._source_key(thread_a, hmac_key=old_secret)

        with patch.dict(os.environ, {}, clear=False):
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
            self.client.command(["SET", key_a, wire_json(thread_a, "thread"), "EX", 120])
            self.client.command(["SET", key_b, wire_json(thread_b, "thread"), "EX", 120])
            self.client.command(["SET", current_source_key, thread_a["collaborationId"], "EX", 120])
            self.client.command(["SET", previous_source_key, thread_b["collaborationId"], "EX", 120])
            protected_keys = (key_a, key_b, current_source_key, previous_source_key)
            before_values = tuple(self.client.command(["GET", key]) for key in protected_keys)
            before_ttls = self._pttls(*protected_keys)
            conflict = redis_store._create_v2_thread(proposed, command_transport=self.client.transport)
            self.assertEqual(conflict.get("error"), {"code": "source_pointer_conflict"})
            self.assertIsNone(self.client.command(["GET", key_c]))
            self.assertEqual(
                tuple(self.client.command(["GET", key]) for key in protected_keys), before_values
            )
            self._assert_ttls_not_refreshed(before_ttls, self._pttls(*protected_keys))

    def test_real_hmac_rotation_configuration_rules_fail_closed_before_redis(self):
        encoded = base64.urlsafe_b64encode(b"k" * 32).decode("ascii").rstrip("=")
        thread = thread_record()
        commands = []

        with patch.dict(os.environ, {}, clear=False):
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = encoded
            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = encoded
            identical = redis_store._create_v2_thread(
                thread, command_transport=lambda command: commands.append(command)
            )
            self.assertEqual(
                identical, {"status": "unavailable", "error": {"code": "index_hmac_unavailable"}}
            )
            self.assertEqual(commands, [])

            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = "invalid previous key"
            invalid = redis_store._create_v2_thread(
                thread, command_transport=lambda command: commands.append(command)
            )
            self.assertEqual(
                invalid, {"status": "unavailable", "error": {"code": "index_hmac_unavailable"}}
            )
            self.assertEqual(commands, [])

    def test_real_orphan_pointer_repair_race_resolves_both_clients_to_one_thread(self):
        thread = thread_record()
        source_key = self._source_key(thread)
        thread_key = self._thread_key(thread["collaborationId"])
        self.client.command(["SET", source_key, "Z" * 22, "EX", 120])
        barrier = threading.Barrier(2)

        def create(_):
            independent_client = _RespClient(self.socket_path)
            barrier.wait()
            return redis_store._create_v2_thread(thread, command_transport=independent_client.transport)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual(sum(result.get("created") is True for result in results), 1)
        self.assertEqual(sum(result.get("created") is False for result in results), 1)
        self.assertTrue(all(result.get("record", {}).get("collaborationId") == thread["collaborationId"] for result in results))
        self.assertEqual(self.client.command(["GET", source_key]), thread["collaborationId"])
        self.assertEqual(self.client.command(["KEYS", f"{redis_store.V2_THREAD_KEY_PREFIX}*"]), [thread_key])
        self._assert_retention_pair(thread_key, source_key)

    def test_real_orphan_pointer_without_expiry_is_atomically_replaced(self):
        thread = thread_record()
        source_key = self._source_key(thread)
        thread_key = self._thread_key(thread["collaborationId"])
        self.client.command(["SET", source_key, "Z" * 22])
        self.assertEqual(self.client.command(["PTTL", source_key]), -1)
        repaired = redis_store._create_v2_thread(thread, command_transport=self.client.transport)
        self.assertTrue(repaired["created"])
        self.assertEqual(self.client.command(["GET", source_key]), thread["collaborationId"])
        self.assertIsNotNone(self.client.command(["GET", thread_key]))
        self._assert_retention_pair(thread_key, source_key)

    def test_real_orphan_repair_rejects_malformed_or_conflicting_existing_targets(self):
        proposed = thread_record()
        source_key = self._source_key(proposed)
        proposed_key = self._thread_key(proposed["collaborationId"])
        target_id = "Z" * 22
        target_key = self._thread_key(target_id)
        targets = (
            ("malformed", "not-json"),
            (
                "wrong_owner",
                wire_json({**proposed, "collaborationId": target_id, "ownerEmail": "other@example.com", "workspaceId": "other@example.com"}, "thread"),
            ),
            (
                "wrong_workspace",
                wire_json({**proposed, "collaborationId": target_id, "workspaceId": "other@example.com"}, "thread"),
            ),
            (
                "wrong_mailbox",
                wire_json({**proposed, "collaborationId": target_id, "mailboxId": "mailbox-other"}, "thread"),
            ),
            (
                "wrong_source",
                wire_json({**proposed, "collaborationId": target_id, "sourceRef": {"provider": "google", "providerMessageId": "gmail-other"}}, "thread"),
            ),
            (
                "wrong_collaboration_identity",
                wire_json({**proposed, "collaborationId": "Y" * 22}, "thread"),
            ),
        )
        for label, target_raw in targets:
            with self.subTest(target=label):
                self.client.command(["FLUSHALL"])
                self.client.command(["SET", source_key, target_id, "EX", 120])
                self.client.command(["SET", target_key, target_raw, "EX", 120])
                protected_keys = (source_key, target_key)
                before_values = tuple(self.client.command(["GET", key]) for key in protected_keys)
                before_ttls = self._pttls(*protected_keys)
                rejected = redis_store._create_v2_thread(
                    proposed, command_transport=self.client.transport
                )
                self.assertEqual(rejected.get("error"), {"code": "source_pointer_conflict"})
                self.assertIsNone(self.client.command(["GET", proposed_key]))
                self.assertEqual(
                    tuple(self.client.command(["GET", key]) for key in protected_keys), before_values
                )
                self._assert_ttls_not_refreshed(before_ttls, self._pttls(*protected_keys))

    def test_real_previous_hmac_orphan_repair_race_creates_only_current_canonical_index(self):
        old_secret = b"r" * 32
        new_secret = b"m" * 32
        old_encoded = base64.urlsafe_b64encode(old_secret).decode("ascii").rstrip("=")
        new_encoded = base64.urlsafe_b64encode(new_secret).decode("ascii").rstrip("=")
        thread = thread_record()
        thread_key = self._thread_key(thread["collaborationId"])
        previous_source_key = self._source_key(thread, hmac_key=old_secret)
        current_source_key = self._source_key(thread, hmac_key=new_secret)

        with patch.dict(os.environ, {}, clear=False):
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
            self.client.command(["SET", previous_source_key, "Z" * 22, "EX", 120])
            barrier = threading.Barrier(2)

            def create(_):
                independent_client = _RespClient(self.socket_path)
                barrier.wait()
                return redis_store._create_v2_thread(thread, command_transport=independent_client.transport)

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(create, range(2)))
            self.assertEqual(sum(result.get("created") is True for result in results), 1)
            self.assertEqual(sum(result.get("created") is False for result in results), 1)
            self.assertTrue(all(result.get("record", {}).get("collaborationId") == thread["collaborationId"] for result in results))
            self.assertEqual(self.client.command(["GET", current_source_key]), thread["collaborationId"])
            self.assertIsNone(self.client.command(["GET", previous_source_key]))
            self.assertEqual(self.client.command(["KEYS", f"{redis_store.V2_THREAD_KEY_PREFIX}*"]), [thread_key])
            self._assert_retention_pair(thread_key, current_source_key)

    def test_real_cas_enforces_aggregate_raw_record_limit_without_partial_writes(self):
        current = thread_record()
        source_key = self._source_key(current)
        thread_key = self._thread_key(current["collaborationId"])
        replacement = {
            **current,
            "messages": [message_record()],
            "updatedAt": current["updatedAt"] + 1,
        }

        current_boundary = pad_json(wire_json(current, "thread"), MAX_RAW_RECORD_BYTES - 1)
        replacement_boundary = pad_json(wire_json(replacement, "thread"), MAX_RAW_RECORD_BYTES - 1)
        self._put_thread(current, source_key, raw=current_boundary)

        def boundary_replacement(command, argument_start):
            command[argument_start + 1] = replacement_boundary

        boundary = redis_store._save_v2_thread_if_expected(
            replacement,
            current["updatedAt"],
            command_transport=self._transport_mutating_eval(
                redis_store._SAVE_V2_THREAD_CAS_LUA, boundary_replacement
            ),
        )
        self.assertEqual(boundary["status"], "ok")
        stored_boundary = self.client.command(["GET", thread_key])
        self.assertEqual(len(stored_boundary.encode("utf-8")), MAX_RAW_RECORD_BYTES - 1)
        self._assert_retention_pair(thread_key, source_key)

        self.client.command(["FLUSHALL"])
        oversized_current = pad_json(wire_json(current, "thread"), MAX_RAW_RECORD_BYTES + 1)
        self._put_thread(current, source_key, raw=oversized_current)
        before_raw = (self.client.command(["GET", thread_key]), self.client.command(["GET", source_key]))
        before_ttls = self._pttls(thread_key, source_key)
        rejected_current = redis_store._save_v2_thread_if_expected(
            replacement, current["updatedAt"], command_transport=self.client.transport
        )
        self.assertEqual(rejected_current.get("error"), {"code": "storage_protocol_error"})
        self.assertEqual(
            (self.client.command(["GET", thread_key]), self.client.command(["GET", source_key])),
            before_raw,
        )
        self._assert_ttls_not_refreshed(before_ttls, self._pttls(thread_key, source_key))

        self.client.command(["FLUSHALL"])
        self._put_thread(current, source_key)
        oversized_replacement = pad_json(wire_json(replacement, "thread"), MAX_RAW_RECORD_BYTES + 1)
        before_raw = (self.client.command(["GET", thread_key]), self.client.command(["GET", source_key]))
        before_ttls = self._pttls(thread_key, source_key)

        def replace_with_oversized(command, argument_start):
            command[argument_start + 1] = oversized_replacement

        rejected_replacement = redis_store._save_v2_thread_if_expected(
            replacement,
            current["updatedAt"],
            command_transport=self._transport_mutating_eval(
                redis_store._SAVE_V2_THREAD_CAS_LUA, replace_with_oversized
            ),
        )
        self.assertEqual(rejected_replacement.get("error"), {"code": "storage_protocol_error"})
        self.assertEqual(
            (self.client.command(["GET", thread_key]), self.client.command(["GET", source_key])),
            before_raw,
        )
        self._assert_ttls_not_refreshed(before_ttls, self._pttls(thread_key, source_key))

    def test_real_cas_enforces_dense_message_arrays_and_count_boundaries(self):
        base = thread_record()
        source_key = self._source_key(base)
        thread_key = self._thread_key(base["collaborationId"])

        empty_to_dense = {**base, "messages": [message_record()], "updatedAt": base["updatedAt"] + 1}
        self._put_thread(base, source_key)
        valid_empty = redis_store._save_v2_thread_if_expected(
            empty_to_dense, base["updatedAt"], command_transport=self.client.transport
        )
        self.assertEqual(valid_empty["status"], "ok")

        dense_current = {**base, "messages": [message_record()], "updatedAt": MS + 200}
        dense_replacement = {
            **dense_current,
            "messages": [*dense_current["messages"], message_record(2, created_at=MS + 201)],
            "updatedAt": MS + 201,
        }
        self.client.command(["FLUSHALL"])
        self._put_thread(dense_current, source_key)
        valid_dense = redis_store._save_v2_thread_if_expected(
            dense_replacement, dense_current["updatedAt"], command_transport=self.client.transport
        )
        self.assertEqual(valid_dense["status"], "ok")

        # JSON arrays cannot encode holes or string keys.  Redis cjson serializes
        # such Lua tables as JSON objects/null-filled arrays; both representable
        # forms must be rejected by the production raw-array/schema checks.
        malformed_messages = (
            {},
            {"1": message_record()},
            {"1": message_record(), "extra": message_record(2)},
            {"1": message_record(), "3": message_record(3)},
            [message_record(), None, message_record(3)],
        )
        for messages in malformed_messages:
            with self.subTest(messages=messages):
                self.client.command(["FLUSHALL"])
                raw = wire_thread_with_messages(base, messages)
                self.client.command(["SET", thread_key, raw, "EX", 120])
                self.client.command(["SET", source_key, base["collaborationId"], "EX", 120])
                before_ttls = self._pttls(thread_key, source_key)
                result = redis_store._save_v2_thread_if_expected(
                    empty_to_dense, base["updatedAt"], command_transport=self.client.transport
                )
                self.assertEqual(result.get("error"), {"code": "storage_protocol_error"})
                self.assertEqual(self.client.command(["GET", thread_key]), raw)
                self.assertEqual(self.client.command(["GET", source_key]), base["collaborationId"])
                self._assert_ttls_not_refreshed(before_ttls, self._pttls(thread_key, source_key))

        messages_499 = [message_record(index) for index in range(1, 500)]
        current_499 = {**base, "messages": messages_499, "updatedAt": MS + 700}
        replacement_500 = {
            **current_499,
            "messages": [*messages_499, message_record(500)],
            "updatedAt": MS + 701,
        }
        self.client.command(["FLUSHALL"])
        self._put_thread(current_499, source_key)
        boundary = redis_store._save_v2_thread_if_expected(
            replacement_500, current_499["updatedAt"], command_transport=self.client.transport
        )
        self.assertEqual(boundary["status"], "ok")
        self.assertEqual(len(typed_wire_json(self.client.command(["GET", thread_key]), "thread")["messages"]), 500)

        replacement_501 = {
            **replacement_500,
            "messages": [*replacement_500["messages"], message_record(501)],
            "updatedAt": MS + 702,
        }
        before_raw = self.client.command(["GET", thread_key])
        before_ttls = self._pttls(thread_key, source_key)

        def inject_501(command, argument_start):
            command[argument_start + 1] = wire_json(replacement_501, "thread")

        over_boundary = redis_store._save_v2_thread_if_expected(
            {**replacement_500, "updatedAt": MS + 702},
            replacement_500["updatedAt"],
            command_transport=self._transport_mutating_eval(
                redis_store._SAVE_V2_THREAD_CAS_LUA, inject_501
            ),
        )
        self.assertEqual(over_boundary.get("error"), {"code": "storage_protocol_error"})
        self.assertEqual(self.client.command(["GET", thread_key]), before_raw)
        self._assert_ttls_not_refreshed(before_ttls, self._pttls(thread_key, source_key))

    def test_real_cas_enforces_timestamp_ordering_and_safe_integers(self):
        base = {**thread_record(), "updatedAt": MS + 200}
        source_key = self._source_key(base)
        thread_key = self._thread_key(base["collaborationId"])

        for label, replacement_updated_at in (("equal", MS + 200), ("decreasing", MS + 199)):
            with self.subTest(label=label):
                self.client.command(["FLUSHALL"])
                self._put_thread(base, source_key)
                replacement = {
                    **base,
                    "messages": [message_record(created_at=replacement_updated_at)],
                    "updatedAt": replacement_updated_at,
                }
                before_raw = self.client.command(["GET", thread_key])
                before_ttls = self._pttls(thread_key, source_key)
                result = redis_store._save_v2_thread_if_expected(
                    replacement, base["updatedAt"], command_transport=self.client.transport
                )
                self.assertEqual(result.get("error"), {"code": "stale_thread"})
                self.assertEqual(self.client.command(["GET", thread_key]), before_raw)
                self._assert_ttls_not_refreshed(before_ttls, self._pttls(thread_key, source_key))

        self.client.command(["FLUSHALL"])
        self._put_thread(base, source_key)
        advancing = {
            **base,
            "messages": [message_record(created_at=MS + 201)],
            "updatedAt": MS + 201,
        }
        advanced = redis_store._save_v2_thread_if_expected(
            advancing, base["updatedAt"], command_transport=self.client.transport
        )
        self.assertEqual(advanced["status"], "ok")
        stored = typed_wire_json(self.client.command(["GET", thread_key]), "thread")
        self.assertEqual(stored["updatedAt"], MS + 201)

        stale_replacement = {
            **advancing,
            "messages": [*advancing["messages"], message_record(2, created_at=MS + 202)],
            "updatedAt": MS + 202,
        }
        before_raw = self.client.command(["GET", thread_key])
        before_ttls = self._pttls(thread_key, source_key)
        stale = redis_store._save_v2_thread_if_expected(
            stale_replacement, base["updatedAt"], command_transport=self.client.transport
        )
        self.assertEqual(stale.get("error"), {"code": "stale_thread"})
        self.assertEqual(self.client.command(["GET", thread_key]), before_raw)
        self._assert_ttls_not_refreshed(before_ttls, self._pttls(thread_key, source_key))

        for label, unsafe_timestamp in (("float", MS + 201.5), ("unsafe", 2**53)):
            with self.subTest(label=label):
                self.client.command(["FLUSHALL"])
                self._put_thread(base, source_key)
                before_raw = self.client.command(["GET", thread_key])
                before_ttls = self._pttls(thread_key, source_key)

                def inject_timestamp(command, argument_start, value=unsafe_timestamp):
                    changed = json.loads(command[argument_start + 1])
                    changed["updatedAt"] = value
                    command[argument_start + 1] = compact_json(changed)

                rejected = redis_store._save_v2_thread_if_expected(
                    advancing,
                    base["updatedAt"],
                    command_transport=self._transport_mutating_eval(
                        redis_store._SAVE_V2_THREAD_CAS_LUA, inject_timestamp
                    ),
                )
                self.assertEqual(rejected.get("error"), {"code": "storage_protocol_error"})
                self.assertEqual(self.client.command(["GET", thread_key]), before_raw)
                self._assert_ttls_not_refreshed(before_ttls, self._pttls(thread_key, source_key))

    def test_exact_production_create_and_cas_scripts_preserve_pointer_and_ttls(self):
        thread = thread_record()
        created = redis_store._create_v2_thread(thread, command_transport=self.client.transport)
        self.assertEqual(created["status"], "ok")
        replacement = {
            **thread,
            "messages": [{
                "id": "M" * 22,
                "authorKind": "owner",
                "authorDisplayName": "Owner",
                "text": "message",
                "visibility": "internal",
                "createdAt": MS + 101,
            }],
            "updatedAt": MS + 101,
        }
        saved = redis_store._save_v2_thread_if_expected(
            replacement, MS + 100, command_transport=self.client.transport
        )
        self.assertEqual(saved["status"], "ok")
        thread_key = redis_store.build_v2_thread_key(thread["collaborationId"])
        source_key = redis_store.build_v2_source_thread_key(
            thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"]
        )
        self.assertEqual(self.client.command(["GET", source_key]), thread["collaborationId"])
        self.assertGreater(self.client.command(["TTL", thread_key]), 0)
        self.assertGreater(self.client.command(["TTL", source_key]), 0)

    def test_real_competing_cas_and_ambiguous_retry_have_one_commit(self):
        thread = thread_record()
        redis_store._create_v2_thread(thread, command_transport=self.client.transport)
        barrier = threading.Barrier(2)

        def contender(marker: str):
            replacement = {
                **thread,
                "messages": [{
                    "id": marker * 22,
                    "authorKind": "owner",
                    "authorDisplayName": "Owner",
                    "text": marker,
                    "visibility": "internal",
                    "createdAt": MS + 101,
                }],
                "updatedAt": MS + 101,
            }
            barrier.wait()
            return redis_store._save_v2_thread_if_expected(
                replacement, MS + 100, command_transport=self.client.transport
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(contender, ("B", "C")))
        self.assertEqual(sum(result.get("status") == "ok" for result in results), 1)

        stored = typed_wire_json(self.client.command(["GET", redis_store.build_v2_thread_key("A" * 22)]), "thread")
        committed = {**stored, "updatedAt": MS + 102, "messages": [*stored["messages"], {
            "id": "D" * 22,
            "authorKind": "owner",
            "authorDisplayName": "Owner",
            "text": "ambiguous",
            "visibility": "internal",
            "createdAt": MS + 102,
        }]}

        def commit_then_drop(command):
            self.client.command(command)
            return {"error": "simulated response loss"}

        first = redis_store._save_v2_thread_if_expected(
            committed, MS + 101, command_transport=commit_then_drop
        )
        retry = redis_store._save_v2_thread_if_expected(
            committed, MS + 101, command_transport=self.client.transport
        )
        self.assertEqual(first["status"], "unavailable")
        self.assertEqual(retry["status"], "conflict")
        final = typed_wire_json(self.client.command(["GET", redis_store.build_v2_thread_key("A" * 22)]), "thread")
        self.assertEqual(len(final["messages"]), 2)

    def test_real_exchange_exchange_and_exchange_revoke_races_have_one_winner(self):
        raw_token = "t" * 43
        invite = invite_record(raw_token)
        redis_store._create_v2_invite(invite, now=SEC + 100, command_transport=self.client.transport)
        barrier = threading.Barrier(2)

        def exchange(secret: str):
            session = session_record(secret)
            barrier.wait()
            return redis_store._atomic_exchange_v2_invite(
                raw_token=raw_token,
                invite_id=invite["inviteId"],
                session_record=session,
                now=SEC + 101,
                session_ttl=49,
                command_transport=self.client.transport,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            exchange_results = list(pool.map(exchange, ("s" * 43, "z" * 43)))
        self.assertEqual(sum(result.get("status") == "ok" for result in exchange_results), 1)

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_invite(invite, now=SEC + 100, command_transport=self.client.transport)
        eval_barrier = threading.Barrier(2)

        def racing_transport(command):
            if command[0] == "EVAL":
                eval_barrier.wait()
            return self.client.transport(command)

        session = session_record("s" * 43)
        with ThreadPoolExecutor(max_workers=2) as pool:
            exchange_future = pool.submit(
                redis_store._atomic_exchange_v2_invite,
                raw_token=raw_token,
                invite_id=invite["inviteId"],
                session_record=session,
                now=SEC + 101,
                session_ttl=49,
                command_transport=racing_transport,
            )
            revoke_future = pool.submit(
                redis_store._revoke_v2_invite,
                invite["inviteId"],
                owner_email=invite["ownerEmail"],
                workspace_id=invite["workspaceId"],
                mailbox_id=invite["mailboxId"],
                collaboration_id=invite["collaborationId"],
                revoked_by=invite["ownerEmail"],
                now=SEC + 101,
                command_transport=racing_transport,
            )
            race_results = [exchange_future.result(), revoke_future.result()]
        self.assertEqual(sum(result.get("status") == "ok" for result in race_results), 1)
        final_invite = typed_wire_json(self.client.command(["GET", redis_store.build_v2_invite_key(invite["inviteId"])]), "invite")
        session_key = redis_store.build_v2_guest_session_key(session["sessionHash"])
        if final_invite["status"] == "revoked":
            self.assertIsNone(self.client.command(["GET", session_key]))
        else:
            self.assertEqual(final_invite["status"], "exchanged")
            self.assertIsNotNone(self.client.command(["GET", session_key]))

    def test_exchange_and_revocation_lua_revalidate_raced_records_without_partial_writes(self):
        raw_token = "t" * 43
        canonical = invite_record(raw_token)
        invite_key = redis_store.build_v2_invite_key(canonical["inviteId"])
        invalid_variants = []
        for field, value in (
            ("createdAt", None),
            ("createdAt", "0" + str(SEC + 100)),
            ("createdAt", SEC + 100.5),
            ("createdAt", 2**53),
            ("createdAt", str(SEC + 201)),
            ("expiresAt", str(SEC + 100 + 86_401)),
            ("createdBy", {"ownerEmail": "attacker@example.com", "displayName": "Owner"}),
            ("createdBy", {"ownerEmail": "owner@example.com", "displayName": "Owner", "extra": True}),
        ):
            changed = json.loads(wire_json(canonical, "invite"))
            if value is None:
                changed.pop(field)
            else:
                changed[field] = value
            invalid_variants.append(changed)
        malformed_creator = json.loads(wire_json(canonical, "invite"))
        malformed_creator["createdBy"] = "Owner"
        invalid_variants.append(malformed_creator)
        extra = {**json.loads(wire_json(canonical, "invite")), "unexpected": "field"}
        invalid_variants.append(extra)

        for replacement in invalid_variants:
            with self.subTest(replacement=replacement):
                self.client.command(["FLUSHALL"])
                redis_store._create_v2_invite(canonical, now=SEC + 100, command_transport=self.client.transport)
                swapped = False

                def swap_before_eval(command):
                    nonlocal swapped
                    if command[0] == "EVAL" and command[1] == redis_store._EXCHANGE_V2_INVITE_LUA and not swapped:
                        swapped = True
                        self.client.command(["SET", invite_key, compact_json(replacement), "EX", 100])
                    return self.client.transport(command)

                session = session_record("s" * 43)
                result = redis_store._atomic_exchange_v2_invite(
                    raw_token=raw_token, invite_id=canonical["inviteId"],
                    session_record=session, now=SEC + 101, session_ttl=49,
                    command_transport=swap_before_eval,
                )
                self.assertEqual(result.get("error"), {"code": "storage_protocol_error"})
                self.assertIsNone(self.client.command(["GET", redis_store.build_v2_guest_session_key(session["sessionHash"])]))

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_invite(canonical, now=SEC + 100, command_transport=self.client.transport)
        session = session_record("s" * 43)
        redis_store._atomic_exchange_v2_invite(
            raw_token=raw_token, invite_id=canonical["inviteId"], session_record=session,
            now=SEC + 101, session_ttl=49, command_transport=self.client.transport,
        )
        session_key = redis_store.build_v2_guest_session_key(session["sessionHash"])
        malformed_session = json.loads(wire_json(session, "session"))
        malformed_session["allowedActions"] = ["read"]

        def corrupt_session_before_revoke(command):
            if command[0] == "EVAL" and command[1] == redis_store._REVOKE_V2_INVITE_LUA:
                self.client.command(["SET", session_key, compact_json(malformed_session), "EX", 40])
            return self.client.transport(command)

        revoked = redis_store._revoke_v2_invite(
            canonical["inviteId"], owner_email=canonical["ownerEmail"],
            workspace_id=canonical["workspaceId"], mailbox_id=canonical["mailboxId"],
            collaboration_id=canonical["collaborationId"], revoked_by=canonical["ownerEmail"],
            now=SEC + 102, command_transport=corrupt_session_before_revoke,
        )
        self.assertEqual(revoked.get("error"), {"code": "storage_protocol_error"})
        unchanged = typed_wire_json(self.client.command(["GET", invite_key]), "invite")
        self.assertEqual(unchanged["status"], "exchanged")

    def test_cas_lua_rejects_pointer_and_dense_array_corruption(self):
        thread = thread_record()
        redis_store._create_v2_thread(thread, command_transport=self.client.transport)
        thread_key = redis_store.build_v2_thread_key(thread["collaborationId"])
        source_key = redis_store.build_v2_source_thread_key(
            thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"]
        )
        replacement = {**thread, "updatedAt": MS + 101, "messages": [{
            "id": "M" * 22, "authorKind": "owner", "authorDisplayName": "Owner",
            "text": "message", "visibility": "internal", "createdAt": MS + 101,
        }]}
        for pointer in (None, "Z" * 22, "not-an-opaque-id"):
            if pointer is None:
                self.client.command(["DEL", source_key])
            else:
                self.client.command(["SET", source_key, pointer, "EX", 100])
            result = redis_store._save_v2_thread_if_expected(
                replacement, MS + 100, command_transport=self.client.transport
            )
            self.assertEqual(result.get("error"), {"code": "storage_protocol_error"})
            self.client.command(["SET", source_key, thread["collaborationId"], "EX", 100])

        def object_messages(command):
            if command[0] == "EVAL" and command[1] == redis_store._SAVE_V2_THREAD_CAS_LUA:
                changed = list(command)
                argument_start = 3 + changed[2]
                raw = json.loads(changed[argument_start + 1])
                raw["messages"] = {"1": replacement["messages"][0]}
                changed[argument_start + 1] = json.dumps(raw, separators=(",", ":"))
                command = changed
            return self.client.transport(command)

        result = redis_store._save_v2_thread_if_expected(
            replacement, MS + 100, command_transport=object_messages
        )
        self.assertEqual(result.get("error"), {"code": "storage_protocol_error"})
        current = typed_wire_json(self.client.command(["GET", thread_key]), "thread")
        self.assertEqual(current["messages"], [])

    def test_stale_source_pointer_repair_is_atomic_under_real_competing_clients(self):
        base = thread_record()
        source_key = redis_store.build_v2_source_thread_key(
            base["ownerEmail"], base["mailboxId"], base["sourceRef"]
        )
        self.client.command(["SET", source_key, "Z" * 22])
        repaired = redis_store._create_v2_thread(base, command_transport=self.client.transport)
        self.assertTrue(repaired["created"])
        self.assertEqual(self.client.command(["GET", source_key]), base["collaborationId"])
        duplicate = redis_store._create_v2_thread(base, command_transport=self.client.transport)
        self.assertFalse(duplicate["created"])

        for target_value in (
            "not-json",
            wire_json(
                {**base, "collaborationId": "Z" * 22, "mailboxId": "mailbox-other"},
                "thread",
            ),
        ):
            self.client.command(["FLUSHALL"])
            self.client.command(["SET", source_key, "Z" * 22, "EX", 100])
            target_key = redis_store.build_v2_thread_key("Z" * 22)
            self.client.command(["SET", target_key, target_value, "EX", 100])
            conflict = redis_store._create_v2_thread(base, command_transport=self.client.transport)
            self.assertEqual(conflict.get("error"), {"code": "source_pointer_conflict"})

        self.client.command(["FLUSHALL"])
        target = {**base, "collaborationId": "Z" * 22}
        self.client.command(["SET", source_key, "Z" * 22])
        self.client.command([
            "SET", redis_store.build_v2_thread_key("Z" * 22),
            wire_json(target, "thread"), "EX", 100,
        ])
        no_expiry = redis_store._create_v2_thread(base, command_transport=self.client.transport)
        self.assertEqual(no_expiry.get("error"), {"code": "source_pointer_conflict"})

        self.client.command(["FLUSHALL"])
        self.client.command(["SET", source_key, "Z" * 22])
        contenders = [base, {**base, "collaborationId": "B" * 22}]
        barrier = threading.Barrier(2)

        def create(record):
            barrier.wait()
            return redis_store._create_v2_thread(record, command_transport=self.client.transport)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, contenders))
        self.assertEqual(sum(result.get("created") is True for result in results), 1)
        canonical_id = self.client.command(["GET", source_key])
        self.assertIn(canonical_id, {"A" * 22, "B" * 22})
        self.assertIsNotNone(self.client.command(["GET", redis_store.build_v2_thread_key(canonical_id)]))

    def test_real_guest_append_revalidates_capability_and_preserves_atomic_state(self):
        thread = thread_record()
        thread_key = self._thread_key(thread["collaborationId"])
        source_key = self._source_key(thread)
        redis_store._create_v2_thread(thread, command_transport=self.client.transport)
        invite, session, invite_keys, session_key = self._create_exchanged_invitation()
        capability = self._guest_mutation_capability()
        invite_raw = self.client.command(["GET", invite_keys[0]])
        session_raw = self.client.command(["GET", session_key])
        capability_ttls = self._pttls(invite_keys[0], session_key)

        with patch.object(mutations.time, "time", return_value=SEC + 102), patch.object(
            mutations.time, "time_ns", return_value=(SEC + 102) * 1_000_000_000
        ):
            saved = mutations.append_guest_v2_reply(
                capability, "Guest reply", command_transport=self.client.transport
            )
        self.assertEqual(saved.get("status"), "ok", saved)
        stored_thread = typed_wire_json(self.client.command(["GET", thread_key]), "thread")
        self.assertEqual(stored_thread["updatedAt"], (SEC + 102) * 1000)
        self.assertEqual(len(stored_thread["messages"]), 1)
        self.assertEqual(
            {
                "authorKind": stored_thread["messages"][0]["authorKind"],
                "authorDisplayName": stored_thread["messages"][0]["authorDisplayName"],
                "text": stored_thread["messages"][0]["text"],
                "visibility": stored_thread["messages"][0]["visibility"],
            },
            {
                "authorKind": "guest",
                "authorDisplayName": "Guest",
                "text": "Guest reply",
                "visibility": "shared",
            },
        )
        self._assert_retention_pair(thread_key, source_key)
        self.assertEqual(self.client.command(["GET", invite_keys[0]]), invite_raw)
        self.assertEqual(self.client.command(["GET", session_key]), session_raw)
        self._assert_ttls_not_refreshed(
            capability_ttls, self._pttls(invite_keys[0], session_key)
        )

        replacement = self._guest_replacement(thread)

        def arrange(case: str):
            self.client.command(["FLUSHALL"])
            redis_store._create_v2_thread(thread, command_transport=self.client.transport)
            current_invite, current_session, keys, current_session_key = (
                self._create_exchanged_invitation()
            )
            current_capability = self._guest_mutation_capability()
            expected = thread["updatedAt"]
            current_now = SEC + 102
            transport = self.client.transport
            if case == "stale_revision":
                expected -= 1
            elif case == "missing_source_pointer":
                self.client.command(["DEL", source_key])
            elif case == "conflicting_source_pointer":
                self.client.command(["SET", source_key, "Z" * 22, "EX", 120])
            elif case == "revoked_invitation":
                revoked = typed_wire_json(self.client.command(["GET", keys[0]]), "invite")
                revoked.update(status="revoked", revokedAt=SEC + 102, revokedBy=revoked["ownerEmail"])
                self.client.command(["SET", keys[0], wire_json(revoked, "invite"), "EX", 90])
            elif case == "expired_invitation":
                current_now = current_invite["expiresAt"]
            elif case == "replacement_invitation":
                replacement_invite = typed_wire_json(self.client.command(["GET", keys[0]]), "invite")
                replacement_invite["activeSessionHash"] = "f" * 64
                self.client.command(["SET", keys[0], wire_json(replacement_invite, "invite"), "EX", 90])
            elif case == "malformed_invitation":
                malformed = typed_wire_json(self.client.command(["GET", keys[0]]), "invite")
                malformed["unexpected"] = True
                self.client.command(["SET", keys[0], wire_json(malformed, "invite"), "EX", 90])
            elif case == "revoked_session":
                revoked = dict(current_session, status="revoked", revokedAt=SEC + 102)
                self.client.command(["SET", current_session_key, wire_json(revoked, "session"), "EX", 40])
            elif case == "logged_out_session":
                logged_out = dict(current_session, status="logged_out", loggedOutAt=SEC + 102)
                self.client.command(["SET", current_session_key, wire_json(logged_out, "session"), "EX", 40])
            elif case == "expired_session":
                current_now = current_session["expiresAt"]
            elif case == "wrong_session_linkage":
                wrong = dict(current_session, collaborationId="B" * 22)
                self.client.command(["SET", current_session_key, wire_json(wrong, "session"), "EX", 40])
            elif case == "malformed_session":
                malformed = dict(current_session, unexpected=True)
                self.client.command(["SET", current_session_key, wire_json(malformed, "session"), "EX", 40])
            elif case in {"sparse_messages", "non_append", "oversized"}:
                def mutate(command, argument_start):
                    value = json.loads(command[argument_start + 1])
                    if case == "sparse_messages":
                        value["messages"] = {"1": value["messages"][0]}
                        command[argument_start + 1] = compact_json(value)
                    elif case == "non_append":
                        value["messages"].append(value["messages"][0])
                        command[argument_start + 1] = compact_json(value)
                    else:
                        command[argument_start + 1] = pad_json(
                            compact_json(value), MAX_RAW_RECORD_BYTES + 1
                        )

                transport = self._transport_mutating_eval(
                    redis_store._APPEND_V2_GUEST_REPLY_LUA, mutate
                )
            return current_capability, expected, current_now, transport, keys, current_session_key

        expected_codes = {
            "stale_revision": "stale_thread",
            "missing_source_pointer": "storage_protocol_error",
            "conflicting_source_pointer": "storage_protocol_error",
            "revoked_invitation": "session_revoked",
            "expired_invitation": "session_expired",
            "replacement_invitation": "session_revoked",
            "malformed_invitation": "storage_protocol_error",
            "revoked_session": "session_revoked",
            "logged_out_session": "session_revoked",
            "expired_session": "session_expired",
            "wrong_session_linkage": "storage_protocol_error",
            "malformed_session": "storage_protocol_error",
            "sparse_messages": "storage_protocol_error",
            "non_append": "storage_protocol_error",
            "oversized": "storage_protocol_error",
        }
        for case, expected_code in expected_codes.items():
            with self.subTest(case=case):
                cap, expected, now, transport, keys, current_session_key = arrange(case)
                observed_keys = (thread_key, source_key, keys[0], current_session_key)
                raw_before = tuple(self.client.command(["GET", key]) for key in observed_keys)
                ttls_before = self._pttls(*observed_keys)
                result = redis_store._append_v2_guest_reply_if_expected(
                    replacement,
                    expected,
                    session_context=cap,
                    now=now,
                    command_transport=transport,
                )
                self.assertEqual(result.get("error"), {"code": expected_code}, result)
                self.assertEqual(
                    tuple(self.client.command(["GET", key]) for key in observed_keys), raw_before
                )
                self._assert_ttls_not_refreshed(ttls_before, self._pttls(*observed_keys))

    def test_guest_append_rejects_every_nonappend_thread_mutation_without_any_graph_write(self):
        prior = message_record(created_at=MS + 100)
        current = {**thread_record(), "messages": [prior]}
        replacement = self._guest_replacement(current, created_at=MS + 101)
        mutations_by_field = (
            ("v", lambda value: value.__setitem__("v", "3")),
            (
                "collaborationId",
                lambda value: value.__setitem__("collaborationId", "B" * 22),
            ),
            (
                "ownerEmail",
                lambda value: value.__setitem__("ownerEmail", "attacker@example.com"),
            ),
            (
                "workspaceId",
                lambda value: value.__setitem__("workspaceId", "attacker@example.com"),
            ),
            ("mailboxId", lambda value: value.__setitem__("mailboxId", "mailbox-other")),
            ("state", lambda value: value.__setitem__("state", "resolved")),
            (
                "createdAt",
                lambda value: value.__setitem__("createdAt", str(MS + 99)),
            ),
            (
                "sourceRef.providerMessageId",
                lambda value: value["sourceRef"].__setitem__(
                    "providerMessageId", "gmail-other"
                ),
            ),
            (
                "sourceMessage.subject",
                lambda value: value["sourceMessage"].__setitem__("subject", "Other"),
            ),
            (
                "sourceMessage.senderDisplay",
                lambda value: value["sourceMessage"].__setitem__(
                    "senderDisplay", "Other sender"
                ),
            ),
            (
                "sourceMessage.fromDisplay",
                lambda value: value["sourceMessage"].__setitem__(
                    "fromDisplay", "other@example.com"
                ),
            ),
            (
                "sourceMessage.timestamp",
                lambda value: value["sourceMessage"].__setitem__("timestamp", "tomorrow"),
            ),
            (
                "sourceMessage.bodyText",
                lambda value: value["sourceMessage"].__setitem__("bodyText", "Other body"),
            ),
            (
                "priorMessage.id",
                lambda value: value["messages"][0].__setitem__("id", "P" * 22),
            ),
            (
                "priorMessage.authorKind",
                lambda value: value["messages"][0].__setitem__("authorKind", "internal"),
            ),
            (
                "priorMessage.authorDisplayName",
                lambda value: value["messages"][0].__setitem__(
                    "authorDisplayName", "Another owner"
                ),
            ),
            (
                "priorMessage.text",
                lambda value: value["messages"][0].__setitem__("text", "rewritten"),
            ),
            (
                "priorMessage.visibility",
                lambda value: value["messages"][0].__setitem__("visibility", "shared"),
            ),
            (
                "priorMessage.createdAt",
                lambda value: value["messages"][0].__setitem__(
                    "createdAt", str(MS + 99)
                ),
            ),
        )

        for label, mutate_replacement in mutations_by_field:
            with self.subTest(field=label):
                self.client.command(["FLUSHALL"])
                redis_store._create_v2_thread(
                    current, command_transport=self.client.transport
                )
                _, _, invite_keys, session_key = self._create_exchanged_invitation()
                capability = self._guest_mutation_capability()
                thread_key = self._thread_key(current["collaborationId"])
                source_key = self._source_key(current)
                protected = (thread_key, source_key, invite_keys[0], session_key)
                before_values = tuple(
                    self.client.command(["GET", key]) for key in protected
                )
                before_ttls = self._pttls(*protected)

                def mutate_eval(command, argument_start, mutation=mutate_replacement):
                    raw_replacement = json.loads(command[argument_start + 1])
                    mutation(raw_replacement)
                    command[argument_start + 1] = compact_json(raw_replacement)

                rejected = redis_store._append_v2_guest_reply_if_expected(
                    replacement,
                    current["updatedAt"],
                    session_context=capability,
                    now=SEC + 102,
                    command_transport=self._transport_mutating_eval(
                        redis_store._APPEND_V2_GUEST_REPLY_LUA, mutate_eval
                    ),
                )
                self.assertIn(
                    rejected.get("error"),
                    ({"code": "storage_protocol_error"}, {"code": "forbidden"}),
                    rejected,
                )
                self.assertEqual(
                    tuple(self.client.command(["GET", key]) for key in protected),
                    before_values,
                )
                self._assert_ttls_not_refreshed(
                    before_ttls, self._pttls(*protected)
                )

    def test_real_guest_append_revocation_logout_races_and_ambiguous_retry_are_coherent(self):
        thread = thread_record()
        thread_key = self._thread_key(thread["collaborationId"])
        replacement = self._guest_replacement(thread)

        def run_race(kind: str):
            self.client.command(["FLUSHALL"])
            redis_store._create_v2_thread(thread, command_transport=self.client.transport)
            invite, session, invite_keys, session_key = self._create_exchanged_invitation()
            capability = self._guest_mutation_capability()
            barrier = threading.Barrier(2)
            append_client = _RespClient(self.socket_path)
            invalidation_client = _RespClient(self.socket_path)

            def transport_for(client, script):
                def transport(command):
                    if command[0] == "EVAL" and command[1] == script:
                        barrier.wait(timeout=5)
                    return client.transport(command)

                return transport

            def append():
                return redis_store._append_v2_guest_reply_if_expected(
                    replacement,
                    thread["updatedAt"],
                    session_context=capability,
                    now=SEC + 103,
                    command_transport=transport_for(
                        append_client, redis_store._APPEND_V2_GUEST_REPLY_LUA
                    ),
                )

            if kind == "invitation_revocation":
                def invalidate():
                    return redis_store._revoke_v2_invite(
                        invite["inviteId"],
                        owner_email=invite["ownerEmail"],
                        workspace_id=invite["workspaceId"],
                        mailbox_id=invite["mailboxId"],
                        collaboration_id=invite["collaborationId"],
                        revoked_by=invite["ownerEmail"],
                        now=SEC + 103,
                        command_transport=transport_for(
                            invalidation_client, redis_store._REVOKE_V2_INVITE_LUA
                        ),
                    )
            else:
                def invalidate():
                    return revoke_guest_session(
                        session,
                        now=SEC + 103,
                        command_transport=transport_for(
                            invalidation_client, redis_store._REVOKE_V2_SESSION_LUA
                        ),
                    )

            with ThreadPoolExecutor(max_workers=2) as pool:
                append_future = pool.submit(append)
                invalidation_future = pool.submit(invalidate)
                append_result = append_future.result(timeout=10)
                invalidation_result = invalidation_future.result(timeout=10)
            self.assertEqual(invalidation_result.get("status"), "ok", invalidation_result)
            current_thread = typed_wire_json(self.client.command(["GET", thread_key]), "thread")
            self.assertIn(len(current_thread["messages"]), {0, 1})
            if current_thread["messages"]:
                self.assertEqual(append_result.get("status"), "ok", append_result)
            else:
                self.assertEqual(
                    append_result.get("error"), {"code": "session_revoked"}, append_result
                )
            stored_session = typed_wire_json(self.client.command(["GET", session_key]), "session")
            if kind == "invitation_revocation":
                stored_invite = typed_wire_json(self.client.command(["GET", invite_keys[0]]), "invite")
                self.assertEqual((stored_invite["status"], stored_session["status"]), ("revoked", "revoked"))
            else:
                self.assertEqual(stored_session["status"], "logged_out")

        run_race("invitation_revocation")
        run_race("logout")

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_thread(thread, command_transport=self.client.transport)
        self._create_exchanged_invitation()
        capability = self._guest_mutation_capability()
        delivered = False

        def lose_first_response(command):
            nonlocal delivered
            if command[0] == "EVAL" and command[1] == redis_store._APPEND_V2_GUEST_REPLY_LUA and not delivered:
                delivered = True
                self.client.command(command)
                return {"error": "response_lost"}
            return self.client.transport(command)

        ambiguous = redis_store._append_v2_guest_reply_if_expected(
            replacement,
            thread["updatedAt"],
            session_context=capability,
            now=SEC + 103,
            command_transport=lose_first_response,
        )
        self.assertEqual(ambiguous.get("error"), {"code": "storage_unavailable"})
        retry = redis_store._append_v2_guest_reply_if_expected(
            replacement,
            thread["updatedAt"],
            session_context=capability,
            now=SEC + 103,
            command_transport=self.client.transport,
        )
        self.assertEqual(retry.get("error"), {"code": "stale_thread"})
        self.assertEqual(typed_wire_json(self.client.command(["GET", thread_key]), "thread")["messages"], replacement["messages"])

    def test_real_distinct_id_create_race_returns_one_canonical_thread_and_revalidates_duplicate(self):
        first = thread_record()
        second = {**first, "collaborationId": "B" * 22}
        source_key = self._source_key(first)
        barrier = threading.Barrier(2)

        def create(record):
            client = _RespClient(self.socket_path)

            def transport(command):
                if command[0] == "EVAL" and command[1] == redis_store._CREATE_V2_THREAD_LUA:
                    barrier.wait(timeout=5)
                return client.transport(command)

            return redis_store._create_v2_thread(record, command_transport=transport)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, (first, second)))
        self.assertEqual(sum(result.get("created") is True for result in results), 1)
        self.assertEqual(sum(result.get("created") is False for result in results), 1)
        canonical_id = self.client.command(["GET", source_key])
        self.assertIn(canonical_id, {first["collaborationId"], second["collaborationId"]})
        self.assertEqual({result["record"]["collaborationId"] for result in results}, {canonical_id})
        canonical_key = self._thread_key(canonical_id)
        losing_id = second["collaborationId"] if canonical_id == first["collaborationId"] else first["collaborationId"]
        self.assertIsNone(self.client.command(["GET", self._thread_key(losing_id)]))
        self.assertEqual(
            self.client.command(["KEYS", f"{redis_store.V2_THREAD_KEY_PREFIX}*"]),
            [canonical_key],
        )
        self._assert_retention_pair(canonical_key, source_key)
        retry = redis_store._create_v2_thread(
            second if losing_id == second["collaborationId"] else first,
            command_transport=self.client.transport,
        )
        self.assertFalse(retry.get("created"), retry)
        self.assertEqual(retry["record"]["collaborationId"], canonical_id)

        def forge_duplicate(command):
            payload = self.client.transport(command)
            if command[0] == "EVAL" and command[1] == redis_store._CREATE_V2_THREAD_LUA:
                decoded = json.loads(payload["result"])
                if decoded.get("status") == "duplicate":
                    decoded["collaborationId"] = "invalid id"
                    payload = {"result": compact_json(decoded)}
            return payload

        forged = redis_store._create_v2_thread(first, command_transport=forge_duplicate)
        self.assertEqual(forged.get("error"), {"code": "storage_protocol_error"})
        self.assertEqual(self.client.command(["GET", source_key]), canonical_id)

        self.client.command(["FLUSHALL"])
        ambiguous_record = {**first, "collaborationId": "C" * 22}
        response_lost = False

        def lose_created_response(command):
            nonlocal response_lost
            if command[0] == "EVAL" and command[1] == redis_store._CREATE_V2_THREAD_LUA and not response_lost:
                response_lost = True
                self.client.command(command)
                return {"error": "response_lost"}
            return self.client.transport(command)

        ambiguous = redis_store._create_v2_thread(
            ambiguous_record, command_transport=lose_created_response
        )
        self.assertEqual(ambiguous.get("error"), {"code": "storage_unavailable"})
        recovered = redis_store._create_v2_thread(
            ambiguous_record, command_transport=self.client.transport
        )
        self.assertFalse(recovered.get("created"), recovered)
        self.assertEqual(recovered["record"], ambiguous_record)
        self.assertEqual(self.client.command(["GET", source_key]), ambiguous_record["collaborationId"])

    def test_real_source_load_and_hmac_migration_cover_success_conflict_and_ttls(self):
        old_secret = b"l" * 32
        new_secret = b"m" * 32
        old_encoded = base64.urlsafe_b64encode(old_secret).decode("ascii").rstrip("=")
        new_encoded = base64.urlsafe_b64encode(new_secret).decode("ascii").rstrip("=")
        thread = thread_record()
        thread_key = self._thread_key(thread["collaborationId"])
        current_key = self._source_key(thread, hmac_key=new_secret)
        previous_key = self._source_key(thread, hmac_key=old_secret)

        with patch.dict(os.environ, {}, clear=False):
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
            self._put_thread(thread, current_key, ttl=120)
            before = self._pttls(thread_key, current_key)
            current = redis_store._load_v2_thread_by_source(
                thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"],
                command_transport=self.client.transport,
            )
            self.assertEqual(current.get("record"), thread)
            self._assert_ttls_not_refreshed(before, self._pttls(thread_key, current_key))

            self.client.command(["FLUSHALL"])
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = old_encoded
            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
            previous_generation = redis_store._create_v2_thread(
                thread, command_transport=self.client.transport
            )
            self.assertTrue(previous_generation.get("created"), previous_generation)
            self.client.command(["PEXPIRE", thread_key, 110_000])
            self.client.command(["PEXPIRE", previous_key, 90_000])
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
            migrated = redis_store._load_v2_thread_by_source(
                thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"],
                command_transport=self.client.transport,
            )
            self.assertEqual(migrated.get("record"), thread)
            self.assertEqual(self.client.command(["GET", current_key]), thread["collaborationId"])
            self.assertIsNone(self.client.command(["GET", previous_key]))
            self.assertLessEqual(self.client.command(["PTTL", current_key]), 90_000)
            self.assertGreater(self.client.command(["PTTL", current_key]), 0)
            self.assertEqual(
                self.client.command(["KEYS", f"{redis_store.V2_THREAD_KEY_PREFIX}*"]),
                [thread_key],
            )

            # Corruption/race fixture: both generation pointers are deliberately
            # present so the production migration script must coalesce them.
            self.client.command(["SET", previous_key, thread["collaborationId"], "PX", 70_000])
            same = redis_store._load_v2_thread_by_source(
                thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"],
                command_transport=self.client.transport,
            )
            self.assertEqual(same.get("record"), thread)
            self.assertEqual(self.client.command(["GET", current_key]), thread["collaborationId"])
            self.assertIsNone(self.client.command(["GET", previous_key]))
            self.assertLessEqual(self.client.command(["PTTL", current_key]), 70_000)

            def arrange_failure(case: str):
                self.client.command(["FLUSHALL"])
                candidate = dict(thread)
                pointer = thread["collaborationId"]
                if case == "conflicting_indexes":
                    other = {**thread, "collaborationId": "B" * 22}
                    self.client.command(["SET", thread_key, wire_json(thread, "thread"), "EX", 120])
                    self.client.command(["SET", self._thread_key(other["collaborationId"]), wire_json(other, "thread"), "EX", 120])
                    self.client.command(["SET", current_key, thread["collaborationId"], "EX", 120])
                    self.client.command(["SET", previous_key, other["collaborationId"], "EX", 120])
                    return
                if case == "malformed_pointer":
                    pointer = "not-an-opaque-id"
                elif case == "missing_target":
                    candidate = None
                elif case == "malformed_target":
                    candidate = "not-json"
                elif case == "wrong_owner":
                    candidate["ownerEmail"] = "other@example.com"
                    candidate["workspaceId"] = "other@example.com"
                elif case == "wrong_workspace":
                    candidate["workspaceId"] = "other@example.com"
                elif case == "wrong_mailbox":
                    candidate["mailboxId"] = "mailbox-other"
                elif case == "wrong_source":
                    candidate["sourceRef"] = {"provider": "google", "providerMessageId": "gmail-other"}
                self.client.command(["SET", current_key, pointer, "EX", 120])
                if candidate is not None:
                    raw = candidate if isinstance(candidate, str) else wire_json(candidate, "thread")
                    self.client.command(["SET", thread_key, raw, "EX", 120])

            for case in (
                "conflicting_indexes", "malformed_pointer", "missing_target", "malformed_target",
                "wrong_owner", "wrong_workspace", "wrong_mailbox", "wrong_source",
            ):
                with self.subTest(case=case):
                    arrange_failure(case)
                    before = self._snapshot_v2_state()
                    rejected = redis_store._load_v2_thread_by_source(
                        thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"],
                        command_transport=self.client.transport,
                    )
                    self.assertEqual(rejected.get("error"), {"code": "source_pointer_conflict"})
                    self._assert_v2_state_unchanged(before)

    def test_equal_time_session_noop_dispatches_no_eval_or_set_and_rotation_fails_closed(self):
        invite, session, invite_keys, session_key = self._create_exchanged_invitation()
        before = self._snapshot_v2_state()

        bootstrap_commands = []

        def capture_bootstrap(command):
            bootstrap_commands.append(command)
            return self.client.transport(command)

        bootstrap = guest_session._bootstrap_v2_guest_session_read_only(
            "s" * 43,
            now=session["lastUsedAt"],
            command_transport=capture_bootstrap,
        )
        self.assertEqual(bootstrap.get("status"), "ok", bootstrap)
        self.assertNotIn("csrfToken", bootstrap)
        self.assertFalse(hasattr(guest_session, "bootstrap_v2_guest_session"))
        self.assertFalse(
            any(command[0] in {"EVAL", "SET"} for command in bootstrap_commands),
            bootstrap_commands,
        )
        self._assert_v2_state_unchanged(before)

        noop_commands = []
        noop = redis_store._update_v2_guest_session(
            session,
            normalizer=guest_session.normalize_v2_guest_session_record,
            now=session["lastUsedAt"],
            csrf_token_hash=session["csrfTokenHash"],
            touch_last_used=False,
            command_transport=lambda command: noop_commands.append(command)
            or self.client.transport(command),
        )
        self.assertEqual(noop, {"status": "unchanged"})
        self.assertEqual(noop_commands, [])
        self._assert_v2_state_unchanged(before)

        rotated_hash = hash_v2_secret("d" * 43)
        rotation_commands = []
        wrapped = redis_store._update_v2_guest_session(
            session,
            normalizer=guest_session.normalize_v2_guest_session_record,
            now=session["lastUsedAt"],
            csrf_token_hash=rotated_hash,
            touch_last_used=False,
            command_transport=lambda command: rotation_commands.append(command)
            or self.client.transport(command),
        )
        self.assertEqual(
            wrapped,
            {"status": "malformed", "error": {"code": "invalid_request"}},
        )
        self.assertEqual(rotation_commands, [])
        self._assert_v2_state_unchanged(before)

        direct = json.loads(
            self.client.command(
                [
                    "EVAL",
                    redis_store._UPDATE_V2_SESSION_LUA,
                    2,
                    session_key,
                    invite_keys[0],
                    str(session["lastUsedAt"]),
                    rotated_hash,
                    str(session["expiresAt"] - session["lastUsedAt"]),
                    "0",
                    session["csrfTokenHash"],
                    session["sessionHash"],
                    session["inviteId"],
                    session["ownerEmail"],
                    session["workspaceId"],
                    session["mailboxId"],
                    session["collaborationId"],
                    str(session["lastUsedAt"]),
                    wire_json(session, "session"),
                ]
            )
        )
        self.assertEqual(direct, {"status": "malformed"})
        self._assert_v2_state_unchanged(before)

    def test_session_update_binds_the_full_expected_record_before_any_write(self):
        _, session, _, session_key = self._create_exchanged_invitation()

        for label, caller in (
            (
                "revoked",
                {**session, "status": "revoked", "revokedAt": SEC + 102},
            ),
            (
                "logged_out",
                {**session, "status": "logged_out", "loggedOutAt": SEC + 102},
            ),
            ("expired", {**session, "status": "expired"}),
            ("active_with_audit", {**session, "revokedAt": SEC + 102}),
        ):
            with self.subTest(preflight=label):
                commands = []

                def capture(command):
                    commands.append(command)
                    return self.client.transport(command)

                before = self._snapshot_v2_state()
                rejected = redis_store._update_v2_guest_session(
                    caller,
                    normalizer=guest_session.normalize_v2_guest_session_record,
                    now=SEC + 103,
                    csrf_token_hash=hash_v2_secret("d" * 43),
                    command_transport=capture,
                )
                self.assertEqual(
                    rejected,
                    {"status": "malformed", "error": {"code": "invalid_request"}},
                )
                self.assertFalse(
                    any(command[0] == "EVAL" for command in commands), commands
                )
                self._assert_v2_state_unchanged(before)

        for label, caller in (
            ("created_at", {**session, "createdAt": SEC + 100}),
            ("guest_display_name", {**session, "guestDisplayName": "Other guest"}),
        ):
            with self.subTest(expected_record=label):
                before = self._snapshot_v2_state()
                rejected = redis_store._update_v2_guest_session(
                    caller,
                    normalizer=guest_session.normalize_v2_guest_session_record,
                    now=SEC + 103,
                    csrf_token_hash=hash_v2_secret("e" * 43),
                    command_transport=self.client.transport,
                )
                self.assertEqual(rejected, {"status": "stale"})
                self._assert_v2_state_unchanged(before)

        expected_wire_variants = dict(
            wire_lexical_variants(wire_json(session, "session"))
        )
        for label in ("numeric_2.0", "duplicate_escaped"):
            with self.subTest(expected_wire=label):
                before = self._snapshot_v2_state()

                def mutate_expected_wire(
                    command,
                    argument_start,
                    raw=expected_wire_variants[label],
                ):
                    command[argument_start + 12] = raw

                rejected = redis_store._update_v2_guest_session(
                    session,
                    normalizer=guest_session.normalize_v2_guest_session_record,
                    now=SEC + 103,
                    csrf_token_hash=hash_v2_secret("f" * 43),
                    command_transport=self._transport_mutating_eval(
                        redis_store._UPDATE_V2_SESSION_LUA,
                        mutate_expected_wire,
                    ),
                )
                self.assertEqual(rejected.get("status"), "malformed", rejected)
                self._assert_v2_state_unchanged(before)

        altered_stored = {**session, "guestDisplayName": "Stored other guest"}
        remaining = self.client.command(["PTTL", session_key])
        self.client.command(
            ["SET", session_key, wire_json(altered_stored, "session"), "PX", remaining]
        )
        before = self._snapshot_v2_state()
        stale = redis_store._update_v2_guest_session(
            session,
            normalizer=guest_session.normalize_v2_guest_session_record,
            now=SEC + 103,
            csrf_token_hash=hash_v2_secret("f" * 43),
            command_transport=self.client.transport,
        )
        self.assertEqual(stale, {"status": "stale"})
        self._assert_v2_state_unchanged(before)

    def test_session_mutation_chronology_rejections_never_write_or_refresh_ttls(self):
        cases = (
            ("update_before_created", "update", None, SEC + 102, SEC + 100),
            ("update_before_last_used", "update", SEC + 103, SEC + 104, SEC + 102),
            ("logout_before_last_used", "logout", SEC + 103, SEC + 104, SEC + 102),
            (
                "owner_revocation_before_last_used",
                "owner_revocation",
                SEC + 103,
                SEC + 104,
                SEC + 102,
            ),
        )
        for label, operation, advance_at, wrapper_now, forced_now in cases:
            with self.subTest(case=label):
                self.client.command(["FLUSHALL"])
                invite, session, _, _ = self._create_exchanged_invitation()
                if advance_at is not None:
                    advanced = redis_store._update_v2_guest_session(
                        session,
                        normalizer=guest_session.normalize_v2_guest_session_record,
                        now=advance_at,
                        csrf_token_hash=hash_v2_secret("g" * 43),
                        command_transport=self.client.transport,
                    )
                    self.assertEqual(advanced.get("status"), "updated", advanced)
                    session = advanced["record"]

                script = {
                    "update": redis_store._UPDATE_V2_SESSION_LUA,
                    "logout": redis_store._REVOKE_V2_SESSION_LUA,
                    "owner_revocation": redis_store._REVOKE_V2_INVITE_LUA,
                }[operation]
                now_index = 7 if operation == "owner_revocation" else 0

                def force_backward_clock(command, argument_start):
                    command[argument_start + now_index] = str(forced_now)

                transport = self._transport_mutating_eval(
                    script, force_backward_clock
                )
                before = self._snapshot_v2_state()
                if operation == "update":
                    rejected = redis_store._update_v2_guest_session(
                        session,
                        normalizer=guest_session.normalize_v2_guest_session_record,
                        now=wrapper_now,
                        csrf_token_hash=hash_v2_secret("h" * 43),
                        command_transport=transport,
                    )
                elif operation == "logout":
                    rejected = revoke_guest_session(
                        session,
                        now=wrapper_now,
                        command_transport=transport,
                    )
                else:
                    rejected = redis_store._revoke_v2_invite(
                        invite["inviteId"],
                        owner_email=invite["ownerEmail"],
                        workspace_id=invite["workspaceId"],
                        mailbox_id=invite["mailboxId"],
                        collaboration_id=invite["collaborationId"],
                        revoked_by=invite["ownerEmail"],
                        now=wrapper_now,
                        command_transport=transport,
                    )
                self.assertEqual(rejected.get("status"), "malformed", rejected)
                self._assert_v2_state_unchanged(before)

    def test_session_update_never_extends_a_shorter_existing_pttl(self):
        _, session, invite_keys, session_key = self._create_exchanged_invitation()
        self.client.command(["PEXPIRE", session_key, 5_000])
        session_ttl_before = self._pttls(session_key)
        invite_ttl_before = self._pttls(invite_keys[0])
        invite_raw = self.client.command(["GET", invite_keys[0]])

        updated = redis_store._update_v2_guest_session(
            session,
            normalizer=guest_session.normalize_v2_guest_session_record,
            now=SEC + 102,
            csrf_token_hash=hash_v2_secret("i" * 43),
            command_transport=self.client.transport,
        )
        self.assertEqual(updated.get("status"), "updated", updated)
        session_ttl_after = self._pttls(session_key)
        self.assertGreater(session_ttl_after[0], 0)
        self._assert_ttls_not_refreshed(session_ttl_before, session_ttl_after)
        self.assertEqual(self.client.command(["GET", invite_keys[0]]), invite_raw)
        self._assert_ttls_not_refreshed(
            invite_ttl_before, self._pttls(invite_keys[0])
        )

    def test_independent_session_updates_with_different_clocks_are_atomic(self):
        _, session, invite_keys, session_key = self._create_exchanged_invitation()
        invite_raw = self.client.command(["GET", invite_keys[0]])
        invite_ttl_before = self._pttls(invite_keys[0])
        session_ttl_before = self._pttls(session_key)
        barrier = threading.Barrier(2)
        jobs = ((SEC + 102, "j" * 43), (SEC + 103, "k" * 43))

        def rotate(job):
            now, secret = job
            client = _RespClient(self.socket_path)

            def transport(command):
                if (
                    command[0] == "EVAL"
                    and command[1] == redis_store._UPDATE_V2_SESSION_LUA
                ):
                    barrier.wait(timeout=5)
                return client.transport(command)

            return redis_store._update_v2_guest_session(
                session,
                normalizer=guest_session.normalize_v2_guest_session_record,
                now=now,
                csrf_token_hash=hash_v2_secret(secret),
                command_transport=transport,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(rotate, jobs))
        self.assertEqual(sum(result.get("status") == "updated" for result in results), 1)
        self.assertEqual(sum(result.get("status") == "stale" for result in results), 1)
        winner_index = next(
            index for index, result in enumerate(results) if result.get("status") == "updated"
        )
        final_session = typed_wire_json(
            self.client.command(["GET", session_key]), "session"
        )
        self.assertEqual(final_session["lastUsedAt"], jobs[winner_index][0])
        self.assertEqual(
            final_session["csrfTokenHash"], hash_v2_secret(jobs[winner_index][1])
        )
        session_ttl_after = self._pttls(session_key)
        self.assertGreater(session_ttl_after[0], 0)
        self.assertLessEqual(session_ttl_after[0], session_ttl_before[0])
        self.assertEqual(self.client.command(["GET", invite_keys[0]]), invite_raw)
        self._assert_ttls_not_refreshed(
            invite_ttl_before, self._pttls(invite_keys[0])
        )

    def test_real_guest_session_update_covers_validation_rotation_and_revocation_races(self):
        invite, session, invite_keys, session_key = self._create_exchanged_invitation()
        rotated_hash = hash_v2_secret("d" * 43)
        updated = redis_store._update_v2_guest_session(
            session,
            normalizer=guest_session.normalize_v2_guest_session_record,
            now=SEC + 102,
            csrf_token_hash=rotated_hash,
            command_transport=self.client.transport,
        )
        self.assertEqual(updated.get("status"), "updated", updated)
        self.assertEqual(updated["record"]["csrfTokenHash"], rotated_hash)
        self.assertEqual(updated["record"]["lastUsedAt"], SEC + 102)
        self._assert_ttl_ceiling(session_key, session["expiresAt"] - (SEC + 102))
        stored_invite = typed_wire_json(self.client.command(["GET", invite_keys[0]]), "invite")
        self.assertEqual(stored_invite["activeSessionHash"], session["sessionHash"])

        before_raw = self.client.command(["GET", session_key])
        before_ttl = self._pttls(session_key)
        stale_input = dict(updated["record"])
        stale_input["csrfTokenHash"] = session["csrfTokenHash"]
        stale = redis_store._update_v2_guest_session(
            stale_input,
            normalizer=guest_session.normalize_v2_guest_session_record,
            now=SEC + 103,
            csrf_token_hash=hash_v2_secret("e" * 43),
            command_transport=self.client.transport,
        )
        self.assertEqual(stale, {"status": "stale"})
        self.assertEqual(self.client.command(["GET", session_key]), before_raw)
        self._assert_ttls_not_refreshed(before_ttl, self._pttls(session_key))

        cases = {
            "wrong_session_hash": lambda value: value.__setitem__("sessionHash", "f" * 64),
            "wrong_invitation_id": lambda value: value.__setitem__("inviteId", "J" * 22),
            "wrong_collaboration_scope": lambda value: value.__setitem__("collaborationId", "B" * 22),
            "revoked_session": lambda value: value.update(
                status="revoked", revokedAt=str(SEC + 102)
            ),
            "logged_out_session": lambda value: value.update(
                status="logged_out", loggedOutAt=str(SEC + 102)
            ),
            "malformed_schema": lambda value: value.pop("guestDisplayName"),
            "extra_fields": lambda value: value.__setitem__("unexpected", True),
            "invalid_timestamps": lambda value: value.__setitem__("lastUsedAt", "invalid"),
        }
        for case, corrupt in cases.items():
            with self.subTest(case=case):
                self.client.command(["FLUSHALL"])
                _, canonical, _, key = self._create_exchanged_invitation()
                changed = json.loads(wire_json(canonical, "session"))
                corrupt(changed)
                injected_raw = compact_json(changed)
                self.client.command(["SET", key, injected_raw, "PX", 40_000])
                ttl_before = self._pttls(key)
                rejected = redis_store._update_v2_guest_session(
                    canonical,
                    normalizer=guest_session.normalize_v2_guest_session_record,
                    now=SEC + 103,
                    csrf_token_hash=hash_v2_secret("g" * 43),
                    command_transport=self.client.transport,
                )
                expected_status = "revoked" if case in {"revoked_session", "logged_out_session"} else "malformed"
                self.assertEqual(rejected.get("status"), expected_status, rejected)
                self.assertEqual(self.client.command(["GET", key]), injected_raw)
                self._assert_ttls_not_refreshed(ttl_before, self._pttls(key))

        self.client.command(["FLUSHALL"])
        _, expiring, _, expiring_key = self._create_exchanged_invitation()
        expiring_raw = self.client.command(["GET", expiring_key])
        expiring_ttl = self._pttls(expiring_key)

        def expire_during_eval(command, argument_start):
            command[argument_start] = str(expiring["expiresAt"])

        expired = redis_store._update_v2_guest_session(
            expiring,
            normalizer=guest_session.normalize_v2_guest_session_record,
            now=SEC + 103,
            csrf_token_hash=hash_v2_secret("h" * 43),
            command_transport=self._transport_mutating_eval(
                redis_store._UPDATE_V2_SESSION_LUA, expire_during_eval
            ),
        )
        self.assertEqual(expired, {"status": "expired"})
        self.assertEqual(self.client.command(["GET", expiring_key]), expiring_raw)
        self._assert_ttls_not_refreshed(expiring_ttl, self._pttls(expiring_key))

        self.client.command(["FLUSHALL"])
        race_invite, race_session, race_invite_keys, race_key = self._create_exchanged_invitation()
        barrier = threading.Barrier(2)

        def rotate(new_secret: str):
            client = _RespClient(self.socket_path)

            def transport(command):
                if command[0] == "EVAL" and command[1] == redis_store._UPDATE_V2_SESSION_LUA:
                    barrier.wait(timeout=5)
                return client.transport(command)

            return redis_store._update_v2_guest_session(
                race_session,
                normalizer=guest_session.normalize_v2_guest_session_record,
                now=SEC + 103,
                csrf_token_hash=hash_v2_secret(new_secret),
                command_transport=transport,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            rotation_results = list(pool.map(rotate, ("i" * 43, "j" * 43)))
        self.assertEqual(sum(result.get("status") == "updated" for result in rotation_results), 1)
        self.assertEqual(sum(result.get("status") == "stale" for result in rotation_results), 1)
        stored = typed_wire_json(self.client.command(["GET", race_key]), "session")
        self.assertIn(stored["csrfTokenHash"], {hash_v2_secret("i" * 43), hash_v2_secret("j" * 43)})
        self.assertEqual(stored["status"], "active")
        self._assert_ttl_ceiling(race_key, race_session["expiresAt"] - (SEC + 103))
        self.assertEqual(
            typed_wire_json(self.client.command(["GET", race_invite_keys[0]]), "invite")["activeSessionHash"],
            stored["sessionHash"],
        )

        self.client.command(["FLUSHALL"])
        race_invite, race_session, _, race_key = self._create_exchanged_invitation()
        barrier = threading.Barrier(2)
        update_client = _RespClient(self.socket_path)
        revoke_client = _RespClient(self.socket_path)

        def update_transport(command):
            if command[0] == "EVAL" and command[1] == redis_store._UPDATE_V2_SESSION_LUA:
                barrier.wait(timeout=5)
            return update_client.transport(command)

        def revoke_transport(command):
            if command[0] == "EVAL" and command[1] == redis_store._REVOKE_V2_INVITE_LUA:
                barrier.wait(timeout=5)
            return revoke_client.transport(command)

        with ThreadPoolExecutor(max_workers=2) as pool:
            update_future = pool.submit(
                redis_store._update_v2_guest_session,
                race_session,
                normalizer=guest_session.normalize_v2_guest_session_record,
                now=SEC + 103,
                csrf_token_hash=hash_v2_secret("k" * 43),
                command_transport=update_transport,
            )
            revoke_future = pool.submit(
                redis_store._revoke_v2_invite,
                race_invite["inviteId"],
                owner_email=race_invite["ownerEmail"],
                workspace_id=race_invite["workspaceId"],
                mailbox_id=race_invite["mailboxId"],
                collaboration_id=race_invite["collaborationId"],
                revoked_by=race_invite["ownerEmail"],
                now=SEC + 104,
                command_transport=revoke_transport,
            )
            update_result = update_future.result(timeout=10)
            revoke_result = revoke_future.result(timeout=10)
        self.assertEqual(revoke_result.get("status"), "ok", revoke_result)
        self.assertIn(update_result.get("status"), {"updated", "revoked"}, update_result)
        final_session = typed_wire_json(self.client.command(["GET", race_key]), "session")
        self.assertEqual(final_session["status"], "revoked")
        if update_result.get("status") == "updated":
            self.assertEqual(final_session["csrfTokenHash"], hash_v2_secret("k" * 43))
        else:
            self.assertEqual(final_session["csrfTokenHash"], race_session["csrfTokenHash"])

    def test_public_request_bound_logout_reaches_private_lua_once(self):
        _, session, invite_keys, session_key = self._create_exchanged_invitation()
        context = self._guest_mutation_capability(now=SEC + 102)
        commands = []

        def capture(command):
            commands.append(command)
            return self.client.transport(command)

        with patch.object(
            guest_session,
            "_revoke_v2_guest_session",
            wraps=guest_session._revoke_v2_guest_session,
        ) as storage_logout:
            result = guest_session.logout_v2_guest_session(
                context,
                now=SEC + 103,
                command_transport=capture,
            )
        self.assertEqual(result, {"status": "ok", "error": None})
        storage_logout.assert_called_once()
        self.assertTrue(
            any(
                command[0] == "EVAL"
                and command[1] == redis_store._REVOKE_V2_SESSION_LUA
                for command in commands
            ),
            commands,
        )
        self.assertNotIn("s" * 43, repr(commands))
        stored = typed_wire_json(self.client.command(["GET", session_key]), "session")
        self.assertEqual(stored["status"], "logged_out")
        self.assertEqual(stored["loggedOutAt"], SEC + 103)
        self.assertEqual(
            typed_wire_json(
                self.client.command(["GET", invite_keys[0]]), "invite"
            )["status"],
            "exchanged",
        )

    def test_public_logout_rejections_never_invoke_storage_or_change_redis(self):
        _, session, _, _ = self._create_exchanged_invitation()
        valid_context = self._guest_mutation_capability(now=SEC + 102)
        read_context, _, read_error = guest_session._resolve_guest_read_access(
            "s" * 43,
            now=SEC + 102,
            command_transport=self.client.transport,
        )
        self.assertIsNone(read_error)
        self.assertTrue(guest_session._is_guest_read_capability(read_context))
        internal_result = authorization.resolve_internal_collaboration_context(
            [],
            "mailbox-1",
            collaboration_id="A" * 22,
            required_action="reply",
            user_resolver=lambda _headers: (
                {"email": "owner@example.com", "name": "Owner"},
                None,
            ),
            mailbox_resolver=lambda _headers, mailbox_id: {
                "status": "ok",
                "user": {"email": "owner@example.com"},
                "inbox": {"id": mailbox_id, "provider": "google"},
            },
            thread_loader=lambda _collaboration_id: {
                "status": "ok",
                "record": thread_record(),
            },
        )
        self.assertEqual(internal_result.get("status"), "ok", internal_result)
        forged_mapping = {
            field: getattr(valid_context, field)
            for field in (
                "session_hash", "invite_id", "owner_email", "workspace_id",
                "mailbox_id", "collaboration_id", "guest_display_name",
                "expires_at", "created_at", "last_used_at",
            )
        }
        for case, forbidden in (
            ("forged_mapping", forged_mapping),
            ("guest_read_capability", read_context),
            ("internal_capability", internal_result["context"]),
            ("raw_session_id", "s" * 43),
        ):
            with self.subTest(context=case):
                before = self._snapshot_v2_state()
                with patch.object(
                    guest_session,
                    "_revoke_v2_guest_session",
                    wraps=guest_session._revoke_v2_guest_session,
                ) as storage_logout:
                    rejected = guest_session.logout_v2_guest_session(
                        forbidden,
                        now=SEC + 103,
                        command_transport=self.client.transport,
                    )
                self.assertEqual(
                    rejected,
                    {"status": "malformed", "error": {"code": "invalid_request"}},
                )
                storage_logout.assert_not_called()
                self._assert_v2_state_unchanged(before)

        headers = [
            ("Origin", "https://app.cuevion.test"),
            ("Content-Type", "application/json"),
            (guest_session.CSRF_HEADER_NAME, "c" * 43),
            (
                "Cookie",
                f"{guest_session.GUEST_SESSION_COOKIE_NAME}={'s' * 43}",
            ),
        ]
        request_cases = [
            (
                "invalid_origin",
                [("Origin", "https://evil.test"), *headers[1:]],
            ),
            (
                "invalid_content_type",
                [headers[0], ("Content-Type", "text/plain"), *headers[2:]],
            ),
            (
                "malformed_csrf",
                [*headers[:2], (guest_session.CSRF_HEADER_NAME, "bad token"), headers[3]],
            ),
            (
                "combined_cookie",
                [*headers[:3], ("Cookie", f"{headers[3][1]}, other=value")],
            ),
        ]
        request_cases.extend(
            (
                f"duplicate_{name.lower()}",
                [*headers, (name.lower(), headers[index][1])],
            )
            for index, name in enumerate(
                ("Origin", "Content-Type", guest_session.CSRF_HEADER_NAME, "Cookie")
            )
        )
        with patch.dict(
            os.environ,
            {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.test"},
            clear=False,
        ):
            for case, changed_headers in request_cases:
                with self.subTest(request=case):
                    before = self._snapshot_v2_state()
                    with patch.object(
                        guest_session,
                        "_revoke_v2_guest_session",
                        wraps=guest_session._revoke_v2_guest_session,
                    ) as storage_logout:
                        resolved = guest_session.resolve_guest_v2_mutation_context(
                            "POST",
                            changed_headers,
                            now=SEC + 102,
                            command_transport=self.client.transport,
                        )
                        self.assertNotEqual(resolved.get("status"), "ok", resolved)
                        rejected = guest_session.logout_v2_guest_session(
                            resolved.get("context"),
                            now=SEC + 103,
                            command_transport=self.client.transport,
                        )
                    self.assertEqual(
                        rejected,
                        {"status": "malformed", "error": {"code": "invalid_request"}},
                    )
                    storage_logout.assert_not_called()
                    self._assert_v2_state_unchanged(before)

        before = self._snapshot_v2_state()
        raw_update_commands = []
        raw_update = redis_store._update_v2_guest_session(
            "s" * 43,
            normalizer=guest_session.normalize_v2_guest_session_record,
            now=SEC + 103,
            csrf_token_hash=hash_v2_secret("d" * 43),
            command_transport=lambda command: raw_update_commands.append(command)
            or self.client.transport(command),
        )
        self.assertEqual(
            raw_update,
            {"status": "malformed", "error": {"code": "invalid_request"}},
        )
        self.assertEqual(raw_update_commands, [])
        self._assert_v2_state_unchanged(before)

    def test_real_guest_logout_covers_idempotence_validation_and_rotation_race(self):
        invite, session, invite_keys, session_key = self._create_exchanged_invitation()
        invite_raw = self.client.command(["GET", invite_keys[0]])
        session_raw = self.client.command(["GET", session_key])
        initial_ttls = self._pttls(invite_keys[0], session_key)
        for case, overrides in (
            ("wrong_invite", {"inviteId": "J" * 22}),
            ("wrong_owner", {"ownerEmail": "other@example.com", "workspaceId": "other@example.com"}),
            ("wrong_mailbox", {"mailboxId": "mailbox-other"}),
            ("wrong_collaboration", {"collaborationId": "B" * 22}),
        ):
            with self.subTest(capability_scope=case):
                rejected_scope = revoke_guest_session(
                    {**session, **overrides},
                    now=SEC + 102,
                    command_transport=self.client.transport,
                )
                self.assertNotEqual(rejected_scope.get("status"), "ok", rejected_scope)
                self.assertEqual(self.client.command(["GET", session_key]), session_raw)
                self.assertEqual(self.client.command(["GET", invite_keys[0]]), invite_raw)
        self._assert_ttls_not_refreshed(
            initial_ttls, self._pttls(invite_keys[0], session_key)
        )

        logout_commands = []
        def capture_logout(command):
            logout_commands.append(command)
            return self.client.transport(command)
        first = revoke_guest_session(
            session, now=SEC + 103, command_transport=capture_logout
        )
        self.assertEqual(first, {"status": "ok"})
        self.assertTrue(any(command[0] == "EVAL" for command in logout_commands))
        self.assertNotIn("s" * 43, repr(logout_commands))
        logged_out_raw = self.client.command(["GET", session_key])
        logged_out = typed_wire_json(logged_out_raw, "session")
        self.assertEqual(logged_out["status"], "logged_out")
        self.assertEqual(logged_out["loggedOutAt"], SEC + 103)
        self.assertIsNone(logged_out["revokedAt"])
        self.assertEqual(self.client.command(["GET", invite_keys[0]]), invite_raw)
        self._assert_ttl_ceiling(session_key, session["expiresAt"] - (SEC + 103))
        first_ttl = self._pttls(session_key)
        repeated = revoke_guest_session(
            session, now=SEC + 104, command_transport=self.client.transport
        )
        self.assertEqual(
            repeated,
            {"status": "already_logged_out", "error": {"code": "already_logged_out"}},
        )
        self.assertEqual(self.client.command(["GET", session_key]), logged_out_raw)
        self._assert_ttls_not_refreshed(first_ttl, self._pttls(session_key))

        self.client.command(["FLUSHALL"])
        _, revoked_session, _, revoked_key = self._create_exchanged_invitation()
        revoked = dict(revoked_session, status="revoked", revokedAt=SEC + 102)
        revoked_raw = wire_json(revoked, "session")
        self.client.command(["SET", revoked_key, revoked_raw, "EX", 40])
        revoked_ttl = self._pttls(revoked_key)
        already_revoked = revoke_guest_session(
            revoked_session, now=SEC + 103, command_transport=self.client.transport
        )
        self.assertEqual(
            already_revoked,
            {"status": "already_logged_out", "error": {"code": "already_logged_out"}},
        )
        self.assertEqual(self.client.command(["GET", revoked_key]), revoked_raw)
        self._assert_ttls_not_refreshed(revoked_ttl, self._pttls(revoked_key))

        corruptions = {
            "wrong_session_hash": lambda value: value.__setitem__("sessionHash", "f" * 64),
            "wrong_invitation_id": lambda value: value.__setitem__("inviteId", "J" * 22),
            "wrong_collaboration_scope": lambda value: value.__setitem__("collaborationId", "B" * 22),
            "malformed_session": lambda value: value.pop("guestDisplayName"),
            "extra_fields": lambda value: value.__setitem__("unexpected", True),
        }
        for case, corrupt in corruptions.items():
            with self.subTest(case=case):
                self.client.command(["FLUSHALL"])
                _, canonical, _, key = self._create_exchanged_invitation()
                changed = json.loads(wire_json(canonical, "session"))
                corrupt(changed)
                injected_raw = compact_json(changed)
                ttl_at_eval = []

                def race_transport(command):
                    if command[0] == "EVAL" and command[1] == redis_store._REVOKE_V2_SESSION_LUA:
                        self.client.command(["SET", key, injected_raw, "PX", 40_000])
                        ttl_at_eval.append(self.client.command(["PTTL", key]))
                    return self.client.transport(command)

                rejected = revoke_guest_session(
                    canonical, now=SEC + 103, command_transport=race_transport
                )
                self.assertEqual(
                    rejected,
                    {"status": "malformed", "error": {"code": "storage_protocol_error"}},
                )
                self.assertEqual(self.client.command(["GET", key]), injected_raw)
                self.assertTrue(ttl_at_eval)
                self.assertLessEqual(self.client.command(["PTTL", key]), ttl_at_eval[0])

        self.client.command(["FLUSHALL"])
        _, expiring, _, expiring_key = self._create_exchanged_invitation()
        expiring_raw = self.client.command(["GET", expiring_key])
        expiring_ttl = self._pttls(expiring_key)

        def expire_logout(command, argument_start):
            command[argument_start] = str(expiring["expiresAt"])

        expired = revoke_guest_session(
            expiring,
            now=SEC + 103,
            command_transport=self._transport_mutating_eval(
                redis_store._REVOKE_V2_SESSION_LUA, expire_logout
            ),
        )
        self.assertEqual(
            expired, {"status": "expired", "error": {"code": "session_expired"}}
        )
        self.assertEqual(self.client.command(["GET", expiring_key]), expiring_raw)
        self._assert_ttls_not_refreshed(expiring_ttl, self._pttls(expiring_key))

        self.client.command(["FLUSHALL"])
        race_invite, race_session, race_invite_keys, race_key = self._create_exchanged_invitation()
        barrier = threading.Barrier(2)
        logout_client = _RespClient(self.socket_path)
        update_client = _RespClient(self.socket_path)

        def logout_transport(command):
            if command[0] == "EVAL" and command[1] == redis_store._REVOKE_V2_SESSION_LUA:
                barrier.wait(timeout=5)
            return logout_client.transport(command)

        def update_transport(command):
            if command[0] == "EVAL" and command[1] == redis_store._UPDATE_V2_SESSION_LUA:
                barrier.wait(timeout=5)
            return update_client.transport(command)

        with ThreadPoolExecutor(max_workers=2) as pool:
            logout_future = pool.submit(
                revoke_guest_session,
                race_session,
                now=SEC + 103,
                command_transport=logout_transport,
            )
            update_future = pool.submit(
                redis_store._update_v2_guest_session,
                race_session,
                normalizer=guest_session.normalize_v2_guest_session_record,
                now=SEC + 103,
                csrf_token_hash=hash_v2_secret("z" * 43),
                command_transport=update_transport,
            )
            logout_result = logout_future.result(timeout=10)
            update_result = update_future.result(timeout=10)
        self.assertIn(logout_result.get("status"), {"ok", "malformed"}, logout_result)
        final_session = typed_wire_json(self.client.command(["GET", race_key]), "session")
        if logout_result.get("status") == "ok":
            self.assertEqual(update_result.get("status"), "revoked", update_result)
            self.assertEqual(final_session["status"], "logged_out")
            self.assertEqual(final_session["loggedOutAt"], SEC + 103)
            self.assertEqual(final_session["csrfTokenHash"], race_session["csrfTokenHash"])
        else:
            self.assertEqual(
                logout_result.get("error"), {"code": "storage_protocol_error"}
            )
            self.assertEqual(update_result.get("status"), "updated", update_result)
            self.assertEqual(final_session["status"], "active")
            self.assertIsNone(final_session["loggedOutAt"])
            self.assertEqual(final_session["lastUsedAt"], SEC + 103)
            self.assertEqual(final_session["csrfTokenHash"], hash_v2_secret("z" * 43))
        final_invite = typed_wire_json(self.client.command(["GET", race_invite_keys[0]]), "invite")
        self.assertEqual(final_invite["status"], "exchanged")
        self.assertEqual(final_invite["activeSessionHash"], final_session["sessionHash"])

    def test_custom_imap_source_ref_is_differentially_enforced_by_every_thread_lua_path(self):
        canonical = custom_imap_thread_record()
        self.assertEqual(normalize_v2_thread_record(canonical), canonical)
        created = redis_store._create_v2_thread(
            canonical, command_transport=self.client.transport
        )
        self.assertEqual(created.get("status"), "ok", created)
        loaded = redis_store._load_v2_thread_by_source(
            canonical["ownerEmail"],
            canonical["mailboxId"],
            canonical["sourceRef"],
            command_transport=self.client.transport,
        )
        self.assertEqual(loaded.get("record"), canonical)

        owner_replacement = {
            **canonical,
            "messages": [message_record()],
            "updatedAt": canonical["updatedAt"] + 1,
        }
        saved = redis_store._save_v2_thread_if_expected(
            owner_replacement,
            canonical["updatedAt"],
            command_transport=self.client.transport,
        )
        self.assertEqual(saved.get("status"), "ok", saved)
        self._create_exchanged_invitation()
        capability = self._guest_mutation_capability(now=SEC + 102)
        guest_replacement = self._guest_replacement(
            owner_replacement, created_at=MS + 102
        )
        appended = redis_store._append_v2_guest_reply_if_expected(
            guest_replacement,
            owner_replacement["updatedAt"],
            session_context=capability,
            now=SEC + 102,
            command_transport=self.client.transport,
        )
        self.assertEqual(appended.get("status"), "ok", appended)
        reloaded = redis_store._load_v2_thread(canonical["collaborationId"], command_transport=self.client.transport)
        self.assertEqual(reloaded["record"]["sourceRef"], canonical["sourceRef"])

        malformed_refs = (
            ("wrong_provider", {**canonical["sourceRef"], "provider": "google"}),
            ("wrong_folder", {**canonical["sourceRef"], "folder": "Archive"}),
            ("missing_provider", {key: value for key, value in canonical["sourceRef"].items() if key != "provider"}),
            ("missing_folder", {key: value for key, value in canonical["sourceRef"].items() if key != "folder"}),
            ("missing_uidvalidity", {key: value for key, value in canonical["sourceRef"].items() if key != "uidValidity"}),
            ("missing_uid", {key: value for key, value in canonical["sourceRef"].items() if key != "imapUid"}),
            ("extra_field", {**canonical["sourceRef"], "extra": "x"}),
            ("uidvalidity_number", {**canonical["sourceRef"], "uidValidity": 77}),
            ("uidvalidity_zero", {**canonical["sourceRef"], "uidValidity": "0"}),
            ("uidvalidity_leading_zero", {**canonical["sourceRef"], "uidValidity": "077"}),
            ("uidvalidity_sign", {**canonical["sourceRef"], "uidValidity": "+77"}),
            ("uidvalidity_space", {**canonical["sourceRef"], "uidValidity": "77 "}),
            ("uidvalidity_unicode", {**canonical["sourceRef"], "uidValidity": "\uff17\uff17"}),
            ("uidvalidity_oversized", {**canonical["sourceRef"], "uidValidity": "1" * 21}),
            ("uid_number", {**canonical["sourceRef"], "imapUid": 9}),
            ("uid_zero", {**canonical["sourceRef"], "imapUid": "0"}),
            ("uid_leading_zero", {**canonical["sourceRef"], "imapUid": "09"}),
            ("uid_sign", {**canonical["sourceRef"], "imapUid": "-9"}),
            ("uid_control", {**canonical["sourceRef"], "imapUid": "9\n"}),
            ("uid_oversized", {**canonical["sourceRef"], "imapUid": "9" * 21}),
        )

        canonical_for_failures = reloaded["record"]
        cas_candidate = {
            **canonical_for_failures,
            "messages": [
                *canonical_for_failures["messages"],
                message_record(
                    3,
                    text="next owner message",
                    created_at=canonical_for_failures["updatedAt"] + 1,
                ),
            ],
            "updatedAt": canonical_for_failures["updatedAt"] + 1,
        }
        guest_candidate = self._guest_replacement(
            canonical_for_failures,
            created_at=canonical_for_failures["updatedAt"] + 1,
        )

        def replace_source_json(command, argument_start, argument_index, source_ref):
            wire = json.loads(command[argument_start + argument_index])
            wire["sourceRef"] = source_ref
            command[argument_start + argument_index] = compact_json(wire)

        for label, malformed_ref in malformed_refs:
            with self.subTest(source_ref=label):
                self.assertIsNone(
                    normalize_v2_thread_record(
                        {**canonical_for_failures, "sourceRef": malformed_ref}
                    )
                )

                proposal = {
                    **canonical_for_failures,
                    "collaborationId": "B" * 22,
                }
                before = self._snapshot_v2_state()
                rejected_create = redis_store._create_v2_thread(
                    proposal,
                    command_transport=self._transport_mutating_eval(
                        redis_store._CREATE_V2_THREAD_LUA,
                        lambda command, start, ref=malformed_ref: replace_source_json(
                            command, start, 0, ref
                        ),
                    ),
                )
                self.assertEqual(
                    rejected_create,
                    {"status": "conflict", "error": {"code": "source_pointer_conflict"}},
                )
                self._assert_v2_state_unchanged(before)

                before = self._snapshot_v2_state()
                rejected_load = redis_store._load_v2_thread_by_source(
                    canonical["ownerEmail"],
                    canonical["mailboxId"],
                    canonical["sourceRef"],
                    command_transport=self._transport_mutating_eval(
                        redis_store._LOAD_AND_MIGRATE_V2_SOURCE_LUA,
                        lambda command, start, ref=malformed_ref: command.__setitem__(
                            start + 3,
                            json.dumps(ref, separators=(",", ":"), sort_keys=True),
                        ),
                    ),
                )
                self.assertEqual(
                    rejected_load,
                    {"status": "malformed", "error": {"code": "source_pointer_conflict"}},
                )
                self._assert_v2_state_unchanged(before)

                before = self._snapshot_v2_state()
                rejected_cas = redis_store._save_v2_thread_if_expected(
                    cas_candidate,
                    canonical_for_failures["updatedAt"],
                    command_transport=self._transport_mutating_eval(
                        redis_store._SAVE_V2_THREAD_CAS_LUA,
                        lambda command, start, ref=malformed_ref: replace_source_json(
                            command, start, 1, ref
                        ),
                    ),
                )
                self.assertEqual(
                    rejected_cas,
                    {"status": "malformed", "error": {"code": "storage_protocol_error"}},
                )
                self._assert_v2_state_unchanged(before)

                before = self._snapshot_v2_state()
                rejected_guest = redis_store._append_v2_guest_reply_if_expected(
                    guest_candidate,
                    canonical_for_failures["updatedAt"],
                    session_context=capability,
                    now=SEC + 103,
                    command_transport=self._transport_mutating_eval(
                        redis_store._APPEND_V2_GUEST_REPLY_LUA,
                        lambda command, start, ref=malformed_ref: replace_source_json(
                            command, start, 1, ref
                        ),
                    ),
                )
                self.assertEqual(
                    rejected_guest,
                    {"status": "malformed", "error": {"code": "storage_protocol_error"}},
                )
                self._assert_v2_state_unchanged(before)

        for label, different_ref in (
            (
                "different_uidvalidity",
                {**canonical["sourceRef"], "uidValidity": "78"},
            ),
            (
                "different_imap_uid",
                {**canonical["sourceRef"], "imapUid": "10"},
            ),
        ):
            with self.subTest(source_equality=label):
                proposal = {
                    **canonical_for_failures,
                    "collaborationId": "B" * 22,
                }
                before = self._snapshot_v2_state()
                rejected_create = redis_store._create_v2_thread(
                    proposal,
                    command_transport=self._transport_mutating_eval(
                        redis_store._CREATE_V2_THREAD_LUA,
                        lambda command, start, ref=different_ref: replace_source_json(
                            command, start, 0, ref
                        ),
                    ),
                )
                self.assertEqual(
                    rejected_create,
                    {"status": "conflict", "error": {"code": "source_pointer_conflict"}},
                )
                self._assert_v2_state_unchanged(before)

                before = self._snapshot_v2_state()
                rejected_load = redis_store._load_v2_thread_by_source(
                    canonical["ownerEmail"],
                    canonical["mailboxId"],
                    canonical["sourceRef"],
                    command_transport=self._transport_mutating_eval(
                        redis_store._LOAD_AND_MIGRATE_V2_SOURCE_LUA,
                        lambda command, start, ref=different_ref: command.__setitem__(
                            start + 3,
                            json.dumps(ref, separators=(",", ":"), sort_keys=True),
                        ),
                    ),
                )
                self.assertEqual(
                    rejected_load,
                    {"status": "malformed", "error": {"code": "source_pointer_conflict"}},
                )
                self._assert_v2_state_unchanged(before)

                before = self._snapshot_v2_state()
                rejected_cas = redis_store._save_v2_thread_if_expected(
                    cas_candidate,
                    canonical_for_failures["updatedAt"],
                    command_transport=self._transport_mutating_eval(
                        redis_store._SAVE_V2_THREAD_CAS_LUA,
                        lambda command, start, ref=different_ref: replace_source_json(
                            command, start, 1, ref
                        ),
                    ),
                )
                self.assertEqual(
                    rejected_cas,
                    {"status": "malformed", "error": {"code": "storage_protocol_error"}},
                )
                self._assert_v2_state_unchanged(before)

                before = self._snapshot_v2_state()
                rejected_guest = redis_store._append_v2_guest_reply_if_expected(
                    guest_candidate,
                    canonical_for_failures["updatedAt"],
                    session_context=capability,
                    now=SEC + 103,
                    command_transport=self._transport_mutating_eval(
                        redis_store._APPEND_V2_GUEST_REPLY_LUA,
                        lambda command, start, ref=different_ref: replace_source_json(
                            command, start, 1, ref
                        ),
                    ),
                )
                self.assertEqual(
                    rejected_guest,
                    {"status": "forbidden", "error": {"code": "forbidden"}},
                )
                self._assert_v2_state_unchanged(before)

        for scope_index, wrong_scope in ((1, "other@example.com"), (2, "mailbox-other")):
            before = self._snapshot_v2_state()
            scoped = redis_store._load_v2_thread_by_source(
                canonical["ownerEmail"],
                canonical["mailboxId"],
                canonical["sourceRef"],
                command_transport=self._transport_mutating_eval(
                    redis_store._LOAD_AND_MIGRATE_V2_SOURCE_LUA,
                    lambda command, start, index=scope_index, value=wrong_scope: command.__setitem__(
                        start + index, value
                    ),
                ),
            )
            self.assertEqual(scoped.get("error"), {"code": "source_pointer_conflict"})
            self._assert_v2_state_unchanged(before)

        old_secret = b"i" * 32
        new_secret = b"j" * 32
        old_encoded = base64.urlsafe_b64encode(old_secret).decode("ascii").rstrip("=")
        new_encoded = base64.urlsafe_b64encode(new_secret).decode("ascii").rstrip("=")
        self.client.command(["FLUSHALL"])
        with patch.dict(os.environ, {}, clear=False):
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = old_encoded
            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
            migrated_seed = redis_store._create_v2_thread(
                canonical, command_transport=self.client.transport
            )
            self.assertTrue(migrated_seed.get("created"), migrated_seed)
            old_key = self._source_key(canonical, hmac_key=old_secret)
            old_pttl = self.client.command(["PTTL", old_key])
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
            migrated = redis_store._load_v2_thread_by_source(
                canonical["ownerEmail"], canonical["mailboxId"], canonical["sourceRef"],
                command_transport=self.client.transport,
            )
            self.assertEqual(migrated.get("record"), canonical)
            new_key = self._source_key(canonical, hmac_key=new_secret)
            self.assertIsNone(self.client.command(["GET", old_key]))
            self.assertEqual(self.client.command(["GET", new_key]), canonical["collaborationId"])
            self.assertLessEqual(self.client.command(["PTTL", new_key]), old_pttl)

    def test_mime_confidentiality_survives_production_redis_persistence_and_guest_projection(self):
        nested = (
            b"From: Sender <sender@example.com>\r\nSubject: Nested MIME\r\n"
            b"Content-Type: multipart/mixed; boundary=outer\r\n\r\n"
            b"--outer\r\nContent-Type: multipart/alternative; boundary=visible\r\n\r\n"
            b"--visible\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nVisible body\r\n"
            b"--visible\r\nContent-Type: text/html\r\n\r\n<b>hidden alternative html</b>\r\n--visible--\r\n"
            b"--outer\r\nContent-Type: message/rfc822\r\n\r\n"
            b"From: Secret <secret@example.com>\r\nContent-Type: text/plain\r\n\r\nsecret forwarded body\r\n"
            b"--outer\r\nContent-Type: multipart/mixed; boundary=attached\r\n"
            b"Content-Disposition: attachment\r\n\r\n"
            b"--attached\r\nContent-Type: text/plain\r\n\r\nsecret attached subtree\r\n--attached--\r\n"
            b"--outer--\r\n"
        )
        malformed = (
            b"From: Sender <sender@example.com>\r\nSubject: Malformed\r\n"
            b"Content-Type: multipart/mixed\r\n\r\nsecret malformed subtree"
        )

        def authorize(_headers, mailbox_id, *, required_action):
            return authorization.resolve_internal_collaboration_context(
                [],
                mailbox_id,
                required_action=required_action,
                user_resolver=lambda _raw: (
                    {"email": "owner@example.com", "name": "Owner"},
                    None,
                ),
                mailbox_resolver=lambda _raw, resolved_id: {
                    "status": "ok",
                    "user": {"email": "owner@example.com"},
                    "inbox": {"id": resolved_id, "provider": "google"},
                },
            )

        cases = (
            (
                "nested",
                nested,
                "A" * 22,
                "gmail-nested",
                "Visible body",
                (
                    "hidden alternative html",
                    "secret forwarded body",
                    "secret attached subtree",
                ),
            ),
            (
                "malformed",
                malformed,
                "B" * 22,
                "gmail-malformed",
                "",
                ("secret malformed subtree",),
            ),
        )
        for label, raw_message, collaboration_id, provider_id, expected_body, secrets in cases:
            with self.subTest(case=label):
                self.client.command(["FLUSHALL"])
                extracted_result = source_message.resolve_source_message(
                    {},
                    {
                        "mailboxId": "mailbox-1",
                        "sourceRef": {"providerMessageId": provider_id},
                    },
                    authorization_resolver=authorize,
                    google_fetcher=lambda *_args, raw=raw_message: {
                        "status": "ok",
                        "rawMessage": raw,
                    },
                )
                self.assertEqual(extracted_result.get("status"), "ok", extracted_result)
                extracted = extracted_result["source"]["sourceMessage"]
                self.assertEqual(extracted["bodyText"], expected_body)
                normalized_source = normalize_v2_source_message(extracted)
                self.assertIsNotNone(normalized_source)
                normalized_thread = normalize_v2_thread_record(
                    {
                        "v": 2,
                        "collaborationId": collaboration_id,
                        "ownerEmail": "owner@example.com",
                        "workspaceId": "owner@example.com",
                        "mailboxId": "mailbox-1",
                        "sourceRef": extracted_result["source"]["sourceRef"],
                        "sourceMessage": normalized_source,
                        "state": "needs_review",
                        "messages": [],
                        "createdAt": MS + 100,
                        "updatedAt": MS + 100,
                    }
                )
                self.assertIsNotNone(normalized_thread)
                created = redis_store._create_v2_thread(
                    normalized_thread, command_transport=self.client.transport
                )
                self.assertTrue(created.get("created"), created)
                thread_key = self._thread_key(collaboration_id)
                stored_wire = self.client.command(["GET", thread_key])
                loaded = redis_store._load_v2_thread(
                    collaboration_id, command_transport=self.client.transport
                )
                self.assertEqual(loaded.get("record"), normalized_thread)
                guest_dto = build_v2_guest_thread_dto(loaded["record"])
                self.assertIsNotNone(guest_dto)
                layers = (
                    extracted_result,
                    extracted,
                    normalized_source,
                    normalized_thread,
                    stored_wire,
                    loaded,
                    guest_dto,
                )
                for secret in secrets:
                    for layer in layers:
                        self.assertNotIn(secret, repr(layer))

    def test_email_bound_invitation_full_real_redis_lifecycle_and_hmac_migration(self):
        raw_token = "t" * 43
        canonical = {
            **invite_record(raw_token),
            "invitedEmail": "reviewer@example.com",
        }
        proposal = self._duplicate_invite_proposal(canonical)
        self.assertEqual(
            normalize_v2_email("Reviewer@Example.COM"), "reviewer@example.com"
        )

        old_secret = b"e" * 32
        new_secret = b"f" * 32
        old_encoded = base64.urlsafe_b64encode(old_secret).decode("ascii").rstrip("=")
        new_encoded = base64.urlsafe_b64encode(new_secret).decode("ascii").rstrip("=")
        with patch.dict(os.environ, {}, clear=False):
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = old_encoded
            os.environ.pop(redis_store.V2_INDEX_HMAC_PREVIOUS_ENV, None)
            created = redis_store._create_v2_invite(
                canonical,
                now=canonical["createdAt"],
                command_transport=self.client.transport,
            )
            self.assertTrue(created.get("created"), created)
            old_keys = self._invite_keys(canonical, hmac_key=old_secret)
            stored = typed_wire_json(self.client.command(["GET", old_keys[0]]), "invite")
            self.assertEqual(stored["invitedEmail"], "reviewer@example.com")
            self.assertEqual(
                typed_wire_json(self.client.command(["GET", old_keys[2]]), "invite"),
                stored,
            )

            before_duplicate = self._snapshot_v2_state()
            duplicate = redis_store._create_v2_invite(
                proposal,
                now=proposal["createdAt"],
                command_transport=self.client.transport,
            )
            self.assertEqual(duplicate.get("record"), canonical)
            self.assertFalse(duplicate.get("created"), duplicate)
            self._assert_v2_state_unchanged(before_duplicate)

            old_identity_pttl = self.client.command(["PTTL", old_keys[2]])
            os.environ[redis_store.V2_INDEX_HMAC_ENV] = new_encoded
            os.environ[redis_store.V2_INDEX_HMAC_PREVIOUS_ENV] = old_encoded
            migrated = redis_store._create_v2_invite(
                proposal,
                now=proposal["createdAt"],
                command_transport=self.client.transport,
            )
            self.assertFalse(migrated.get("created"), migrated)
            self.assertEqual(migrated.get("record"), canonical)
            new_keys = self._invite_keys(canonical, hmac_key=new_secret)
            self.assertIsNone(self.client.command(["GET", old_keys[2]]))
            migrated_identity = typed_wire_json(
                self.client.command(["GET", new_keys[2]]), "invite"
            )
            self.assertEqual(migrated_identity["invitedEmail"], "reviewer@example.com")
            self.assertLessEqual(
                self.client.command(["PTTL", new_keys[2]]), old_identity_pttl
            )

            session = session_record("s" * 43)
            before_wrong_email = self._snapshot_v2_state()

            def wrong_invited_email(command, argument_start):
                command[argument_start + 11] = "other@example.com"

            rejected_exchange = redis_store._atomic_exchange_v2_invite(
                raw_token=raw_token,
                invite_id=canonical["inviteId"],
                session_record=session,
                now=session["createdAt"],
                session_ttl=session["expiresAt"] - session["createdAt"],
                command_transport=self._transport_mutating_eval(
                    redis_store._EXCHANGE_V2_INVITE_LUA, wrong_invited_email
                ),
            )
            self.assertEqual(
                rejected_exchange,
                {"status": "malformed", "error": {"code": "storage_protocol_error"}},
            )
            self._assert_v2_state_unchanged(before_wrong_email)

            exchanged = redis_store._atomic_exchange_v2_invite(
                raw_token=raw_token,
                invite_id=canonical["inviteId"],
                session_record=session,
                now=session["createdAt"],
                session_ttl=session["expiresAt"] - session["createdAt"],
                command_transport=self.client.transport,
            )
            self.assertEqual(exchanged, {"status": "ok"})
            session_key = self._session_key(session)
            stored_session = typed_wire_json(
                self.client.command(["GET", session_key]), "session"
            )
            stored_invite = typed_wire_json(
                self.client.command(["GET", new_keys[0]]), "invite"
            )
            self.assertEqual(stored_invite["status"], "exchanged")
            self.assertEqual(stored_invite["activeSessionHash"], session["sessionHash"])
            self.assertEqual(stored_session["inviteId"], stored_invite["inviteId"])
            self.assertEqual(stored_session["collaborationId"], stored_invite["collaborationId"])

            bootstrap = guest_session._bootstrap_v2_guest_session_read_only(
                "s" * 43,
                now=SEC + 102,
                command_transport=self.client.transport,
            )
            self.assertEqual(bootstrap.get("status"), "ok", bootstrap)
            self.assertNotIn("invitedEmail", repr(bootstrap))
            self.assertNotIn("reviewer@example.com", repr(bootstrap))
            read_capability, linked_session, access_error = guest_session._resolve_guest_read_access(
                "s" * 43,
                now=SEC + 102,
                command_transport=self.client.transport,
            )
            self.assertIsNone(access_error)
            self.assertIsNotNone(read_capability)
            self.assertEqual(linked_session, session)

            revoked = redis_store._revoke_v2_invite(
                canonical["inviteId"],
                owner_email=canonical["ownerEmail"],
                workspace_id=canonical["workspaceId"],
                mailbox_id=canonical["mailboxId"],
                collaboration_id=canonical["collaborationId"],
                revoked_by=canonical["ownerEmail"],
                now=SEC + 102,
                command_transport=self.client.transport,
            )
            self.assertEqual(revoked, {"status": "ok"})
            revoked_invite = typed_wire_json(
                self.client.command(["GET", new_keys[0]]), "invite"
            )
            revoked_session = typed_wire_json(
                self.client.command(["GET", session_key]), "session"
            )
            self.assertEqual(revoked_invite["status"], "revoked")
            self.assertEqual(revoked_session["status"], "revoked")

            before_repeat = self._snapshot_v2_state()
            repeated = redis_store._revoke_v2_invite(
                canonical["inviteId"],
                owner_email=canonical["ownerEmail"],
                workspace_id=canonical["workspaceId"],
                mailbox_id=canonical["mailboxId"],
                collaboration_id=canonical["collaborationId"],
                revoked_by=canonical["ownerEmail"],
                now=SEC + 103,
                command_transport=self.client.transport,
            )
            self.assertEqual(
                repeated,
                {"status": "already_revoked", "error": {"code": "already_revoked"}},
            )
            self._assert_v2_state_unchanged(before_repeat)

            unauthorized = guest_session._bootstrap_v2_guest_session_read_only(
                "z" * 43,
                now=SEC + 103,
                command_transport=self.client.transport,
            )
            self.assertNotEqual(unauthorized.get("status"), "ok")
            self.assertNotIn("invitedEmail", repr(unauthorized))
            self.assertNotIn("reviewer@example.com", repr(unauthorized))

            for invalid_email in (
                "Reviewer@Example.com",
                " reviewer@example.com",
                "reviewer@example.com ",
                "reviewer@@example.com",
                "r\u00e9viewer@example.com",
                "reviewer@example",
            ):
                with self.subTest(invalid_email=invalid_email):
                    invalid = {
                        **invite_record("u" * 43),
                        "inviteId": "K" * 22,
                        "invitedEmail": invalid_email,
                    }
                    observed = []

                    def observe(command):
                        observed.append(command)
                        return self.client.transport(command)

                    before_invalid = self._snapshot_v2_state()
                    rejected = redis_store._create_v2_invite(
                        invalid,
                        now=invalid["createdAt"],
                        command_transport=observe,
                    )
                    self.assertEqual(
                        rejected,
                        {"status": "malformed", "error": {"code": "invalid_request"}},
                    )
                    self.assertEqual(observed, [])
                    self._assert_v2_state_unchanged(before_invalid)

    def test_wrong_redis_types_and_missing_security_keys_never_overwrite_or_refresh(self):
        def assert_unchanged(label, expected, invoke):
            with self.subTest(case=label):
                before = self._snapshot_v2_typed_state()
                result = invoke()
                self.assertEqual(result, expected)
                self._assert_v2_typed_state_unchanged(before)

        base = thread_record()
        replacement = {
            **base,
            "messages": [message_record()],
            "updatedAt": base["updatedAt"] + 1,
        }

        self._corrupt_key_type(self._thread_key(base["collaborationId"]), "list")
        assert_unchanged(
            "create_thread_wrong_thread_list",
            {"status": "conflict", "error": {"code": "stale_thread"}},
            lambda: redis_store._create_v2_thread(
                base, command_transport=self.client.transport
            ),
        )

        self.client.command(["FLUSHALL"])
        # Corruption fixture: a valid opaque source pointer targets a missing
        # thread while the proposed primary key has an incompatible Redis type.
        self.client.command(["SET", self._source_key(base), "Z" * 22, "PX", 90_000])
        self._corrupt_key_type(self._thread_key(base["collaborationId"]), "list")
        assert_unchanged(
            "create_thread_stale_source_and_wrong_proposed_key",
            {"status": "conflict", "error": {"code": "stale_thread"}},
            lambda: redis_store._create_v2_thread(
                base, command_transport=self.client.transport
            ),
        )

        self.client.command(["FLUSHALL"])
        self._corrupt_key_type(self._source_key(base), "set")
        assert_unchanged(
            "create_thread_wrong_source_set",
            {"status": "unavailable", "error": {"code": "storage_unavailable"}},
            lambda: redis_store._create_v2_thread(
                base, command_transport=self.client.transport
            ),
        )

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_thread(base, command_transport=self.client.transport)
        self._corrupt_key_type(self._thread_key(base["collaborationId"]), "hash")
        assert_unchanged(
            "source_load_wrong_thread_hash",
            {"status": "unavailable", "error": {"code": "storage_unavailable"}},
            lambda: redis_store._load_v2_thread_by_source(
                base["ownerEmail"], base["mailboxId"], base["sourceRef"],
                command_transport=self.client.transport,
            ),
        )

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_thread(base, command_transport=self.client.transport)
        self._corrupt_key_type(self._source_key(base), "list")
        assert_unchanged(
            "source_load_wrong_source_list",
            {"status": "unavailable", "error": {"code": "storage_unavailable"}},
            lambda: redis_store._load_v2_thread_by_source(
                base["ownerEmail"], base["mailboxId"], base["sourceRef"],
                command_transport=self.client.transport,
            ),
        )

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_thread(base, command_transport=self.client.transport)
        self._corrupt_key_type(self._thread_key(base["collaborationId"]), "integer_string")
        assert_unchanged(
            "cas_integer_like_thread_string",
            {"status": "malformed", "error": {"code": "storage_protocol_error"}},
            lambda: redis_store._save_v2_thread_if_expected(
                replacement, base["updatedAt"], command_transport=self.client.transport
            ),
        )

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_thread(base, command_transport=self.client.transport)
        self._corrupt_key_type(self._source_key(base), "set")
        assert_unchanged(
            "cas_wrong_source_set",
            {"status": "unavailable", "error": {"code": "storage_unavailable"}},
            lambda: redis_store._save_v2_thread_if_expected(
                replacement, base["updatedAt"], command_transport=self.client.transport
            ),
        )

        def prepare_guest_graph():
            self.client.command(["FLUSHALL"])
            redis_store._create_v2_thread(base, command_transport=self.client.transport)
            invite, session, invite_keys, session_key = self._create_exchanged_invitation()
            capability = self._guest_mutation_capability(now=SEC + 102)
            guest_replacement = self._guest_replacement(base, created_at=MS + 101)
            return invite, session, invite_keys, session_key, capability, guest_replacement

        invite, session, invite_keys, session_key, capability, guest_replacement = prepare_guest_graph()
        self._corrupt_key_type(invite_keys[0], "hash")
        assert_unchanged(
            "guest_append_wrong_invitation_hash",
            {"status": "malformed", "error": {"code": "storage_protocol_error"}},
            lambda: redis_store._append_v2_guest_reply_if_expected(
                guest_replacement,
                base["updatedAt"],
                session_context=capability,
                now=SEC + 102,
                command_transport=self.client.transport,
            ),
        )

        invite, session, invite_keys, session_key, capability, guest_replacement = prepare_guest_graph()
        self._corrupt_key_type(self._thread_key(base["collaborationId"]), "list")
        assert_unchanged(
            "guest_append_wrong_thread_list",
            {"status": "malformed", "error": {"code": "storage_protocol_error"}},
            lambda: redis_store._append_v2_guest_reply_if_expected(
                guest_replacement,
                base["updatedAt"],
                session_context=capability,
                now=SEC + 102,
                command_transport=self.client.transport,
            ),
        )

        invite, session, invite_keys, session_key, capability, guest_replacement = prepare_guest_graph()
        self._corrupt_key_type(self._source_key(base), "set")
        assert_unchanged(
            "guest_append_wrong_source_set",
            {"status": "unavailable", "error": {"code": "storage_unavailable"}},
            lambda: redis_store._append_v2_guest_reply_if_expected(
                guest_replacement,
                base["updatedAt"],
                session_context=capability,
                now=SEC + 102,
                command_transport=self.client.transport,
            ),
        )

        invite, session, invite_keys, session_key, capability, guest_replacement = prepare_guest_graph()
        self._corrupt_key_type(session_key, "set")
        assert_unchanged(
            "guest_append_wrong_session_set",
            {"status": "malformed", "error": {"code": "storage_protocol_error"}},
            lambda: redis_store._append_v2_guest_reply_if_expected(
                guest_replacement,
                base["updatedAt"],
                session_context=capability,
                now=SEC + 102,
                command_transport=self.client.transport,
            ),
        )

        self.client.command(["FLUSHALL"])
        active_invite = invite_record()
        identity_key = self._invite_keys(active_invite)[2]
        self._corrupt_key_type(identity_key, "integer_string")
        assert_unchanged(
            "create_invite_integer_like_identity_index",
            {"status": "conflict", "error": {"code": "invalid_request"}},
            lambda: redis_store._create_v2_invite(
                active_invite,
                now=active_invite["createdAt"],
                command_transport=self.client.transport,
            ),
        )

        self.client.command(["FLUSHALL"])
        self._corrupt_key_type(self._invite_keys(active_invite)[0], "list")
        assert_unchanged(
            "create_invite_wrong_primary_list",
            {"status": "conflict", "error": {"code": "invalid_request"}},
            lambda: redis_store._create_v2_invite(
                active_invite,
                now=active_invite["createdAt"],
                command_transport=self.client.transport,
            ),
        )

        self.client.command(["FLUSHALL"])
        self._corrupt_key_type(self._invite_keys(active_invite)[1], "hash")
        assert_unchanged(
            "create_invite_wrong_token_hash",
            {"status": "conflict", "error": {"code": "invalid_request"}},
            lambda: redis_store._create_v2_invite(
                active_invite,
                now=active_invite["createdAt"],
                command_transport=self.client.transport,
            ),
        )

        for linkage_name, key_index, wrong_kind in (
            ("current_identity", 2, "list"),
            ("canonical_invitation", 0, "hash"),
            ("canonical_token", 1, "set"),
        ):
            with self.subTest(case=f"validate_invite_graph_wrong_{linkage_name}"):
                self.client.command(["FLUSHALL"])
                canonical_invite = invite_record()
                duplicate_proposal = self._duplicate_invite_proposal(canonical_invite)
                seeded = redis_store._create_v2_invite(
                    canonical_invite,
                    now=canonical_invite["createdAt"],
                    command_transport=self.client.transport,
                )
                self.assertTrue(seeded.get("created"), seeded)
                corrupted_key = self._invite_keys(canonical_invite)[key_index]
                injected_snapshots = []

                def inject_after_duplicate(command, key=corrupted_key, kind=wrong_kind):
                    response = self.client.transport(command)
                    if (
                        command[0] == "EVAL"
                        and command[1] == redis_store._CREATE_V2_INVITE_LUA
                        and json.loads(response["result"]).get("status") == "duplicate"
                    ):
                        self._corrupt_key_type(key, kind)
                        injected_snapshots.append(self._snapshot_v2_typed_state())
                    return response

                rejected = redis_store._create_v2_invite(
                    duplicate_proposal,
                    now=duplicate_proposal["createdAt"],
                    command_transport=inject_after_duplicate,
                )
                self.assertEqual(
                    rejected,
                    {"status": "malformed", "error": {"code": "storage_protocol_error"}},
                )
                self.assertEqual(len(injected_snapshots), 1)
                self._assert_v2_typed_state_unchanged(injected_snapshots[0])

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_invite(
            active_invite,
            now=active_invite["createdAt"],
            command_transport=self.client.transport,
        )
        self._corrupt_key_type(self._invite_keys(active_invite)[1], "list")
        active_session = session_record("s" * 43)
        assert_unchanged(
            "exchange_wrong_token_index_list",
            {"status": "missing", "error": {"code": "invite_not_found"}},
            lambda: redis_store._atomic_exchange_v2_invite(
                raw_token="t" * 43,
                invite_id=active_invite["inviteId"],
                session_record=active_session,
                now=active_session["createdAt"],
                session_ttl=active_session["expiresAt"] - active_session["createdAt"],
                command_transport=self.client.transport,
            ),
        )

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_invite(
            active_invite,
            now=active_invite["createdAt"],
            command_transport=self.client.transport,
        )
        occupied_session_key = self._session_key(active_session)
        self._corrupt_key_type(occupied_session_key, "set")
        assert_unchanged(
            "exchange_wrong_session_set",
            {"status": "conflict", "error": {"code": "atomic_exchange_unavailable"}},
            lambda: redis_store._atomic_exchange_v2_invite(
                raw_token="t" * 43,
                invite_id=active_invite["inviteId"],
                session_record=active_session,
                now=active_session["createdAt"],
                session_ttl=active_session["expiresAt"] - active_session["createdAt"],
                command_transport=self.client.transport,
            ),
        )

        invite, session, invite_keys, session_key, capability, guest_replacement = prepare_guest_graph()
        self._corrupt_key_type(session_key, "list")
        assert_unchanged(
            "session_update_wrong_session_list",
            {"status": "malformed"},
            lambda: redis_store._update_v2_guest_session(
                session,
                normalizer=guest_session.normalize_v2_guest_session_record,
                now=SEC + 102,
                csrf_token_hash=hash_v2_secret("z" * 43),
                command_transport=self.client.transport,
            ),
        )

        invite, session, invite_keys, session_key, capability, guest_replacement = prepare_guest_graph()
        self._corrupt_key_type(invite_keys[0], "hash")
        assert_unchanged(
            "session_update_wrong_invitation_hash",
            {"status": "malformed"},
            lambda: redis_store._update_v2_guest_session(
                session,
                normalizer=guest_session.normalize_v2_guest_session_record,
                now=SEC + 102,
                csrf_token_hash=hash_v2_secret("z" * 43),
                command_transport=self.client.transport,
            ),
        )

        invite, session, invite_keys, session_key, capability, guest_replacement = prepare_guest_graph()
        self._corrupt_key_type(session_key, "hash")
        assert_unchanged(
            "invite_revoke_wrong_linked_session_hash",
            {"status": "malformed", "error": {"code": "storage_protocol_error"}},
            lambda: redis_store._revoke_v2_invite(
                invite["inviteId"],
                owner_email=invite["ownerEmail"],
                workspace_id=invite["workspaceId"],
                mailbox_id=invite["mailboxId"],
                collaboration_id=invite["collaborationId"],
                revoked_by=invite["ownerEmail"],
                now=SEC + 102,
                command_transport=self.client.transport,
            ),
        )

        invite, session, invite_keys, session_key, capability, guest_replacement = prepare_guest_graph()
        self._corrupt_key_type(invite_keys[0], "set")
        assert_unchanged(
            "session_revoke_wrong_invitation_set",
            {"status": "malformed", "error": {"code": "storage_protocol_error"}},
            lambda: revoke_guest_session(
                session, now=SEC + 102, command_transport=self.client.transport
            ),
        )

        invite, session, invite_keys, session_key, capability, guest_replacement = prepare_guest_graph()
        self._corrupt_key_type(session_key, "list")
        assert_unchanged(
            "session_revoke_wrong_session_list",
            {"status": "malformed", "error": {"code": "storage_protocol_error"}},
            lambda: revoke_guest_session(
                session, now=SEC + 102, command_transport=self.client.transport
            ),
        )

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_thread(base, command_transport=self.client.transport)
        self.client.command(["DEL", self._thread_key(base["collaborationId"])])
        assert_unchanged(
            "cas_missing_thread",
            {"status": "missing", "error": {"code": "collaboration_not_found"}},
            lambda: redis_store._save_v2_thread_if_expected(
                replacement, base["updatedAt"], command_transport=self.client.transport
            ),
        )

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_thread(base, command_transport=self.client.transport)
        self.client.command(["DEL", self._source_key(base)])
        assert_unchanged(
            "cas_missing_source_index",
            {"status": "malformed", "error": {"code": "storage_protocol_error"}},
            lambda: redis_store._save_v2_thread_if_expected(
                replacement, base["updatedAt"], command_transport=self.client.transport
            ),
        )

        invite, session, invite_keys, session_key, capability, guest_replacement = prepare_guest_graph()
        self.client.command(["DEL", invite_keys[0]])
        assert_unchanged(
            "guest_append_missing_invitation",
            {"status": "revoked", "error": {"code": "session_revoked"}},
            lambda: redis_store._append_v2_guest_reply_if_expected(
                guest_replacement,
                base["updatedAt"],
                session_context=capability,
                now=SEC + 102,
                command_transport=self.client.transport,
            ),
        )

        invite, session, invite_keys, session_key, capability, guest_replacement = prepare_guest_graph()
        self.client.command(["DEL", session_key])
        assert_unchanged(
            "guest_append_missing_session",
            {"status": "revoked", "error": {"code": "session_revoked"}},
            lambda: redis_store._append_v2_guest_reply_if_expected(
                guest_replacement,
                base["updatedAt"],
                session_context=capability,
                now=SEC + 102,
                command_transport=self.client.transport,
            ),
        )

        self.client.command(["FLUSHALL"])
        redis_store._create_v2_invite(
            active_invite,
            now=active_invite["createdAt"],
            command_transport=self.client.transport,
        )
        self.client.command(["DEL", self._invite_keys(active_invite)[1]])
        assert_unchanged(
            "exchange_missing_token_index",
            {"status": "missing", "error": {"code": "invite_not_found"}},
            lambda: redis_store._atomic_exchange_v2_invite(
                raw_token="t" * 43,
                invite_id=active_invite["inviteId"],
                session_record=active_session,
                now=active_session["createdAt"],
                session_ttl=active_session["expiresAt"] - active_session["createdAt"],
                command_transport=self.client.transport,
            ),
        )


if __name__ == "__main__":
    unittest.main()
