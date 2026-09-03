from __future__ import annotations

import json
import os
import base64
import binascii
import unittest
from unittest.mock import Mock, patch

from . import redis_store
from .models import encode_v2_wire_record, hash_v2_secret
from .guest_session import normalize_v2_guest_session_record
from .redis_store import (
    _atomic_exchange_v2_invite as _storage_atomic_exchange_v2_invite,
    build_v2_guest_session_key,
    build_v2_invite_key,
    build_v2_invite_token_key,
    build_v2_source_thread_key,
    build_v2_thread_key,
    build_v2_thread_invite_key,
    _create_v2_invite as _storage_create_v2_invite,
    _create_v2_thread_with_guest as _storage_create_v2_thread_with_guest,
    _create_v2_thread as create_v2_thread,
    _load_v2_invite_by_token as _storage_load_v2_invite_by_token,
    _load_v2_thread as load_v2_thread,
    _load_v2_thread_by_source as load_v2_thread_by_source,
    resolve_v2_index_hmac_key,
    resolve_v2_index_hmac_keys,
    _save_v2_thread_if_expected as _storage_save_v2_thread_if_expected,
    _revoke_v2_invite,
    _revoke_v2_guest_session as _storage_revoke_v2_guest_session,
    _update_v2_guest_session as _storage_update_v2_guest_session,
    _CREATE_V2_INVITE_LUA,
    _VALIDATE_V2_INVITE_GRAPH_LUA,
    _EXCHANGE_V2_INVITE_LUA,
    _REVOKE_V2_INVITE_LUA,
    _SAVE_V2_THREAD_CAS_LUA,
    _V2RecordResult,
    _perform_v2_rest_command,
    _v2_command,
    _v2_eval,
    _v2_json_from_wire,
    _v2_wire_json,
)
from .v2_stateful_test_store import StatefulV2Store as _StatefulV2Store

SEC = 1_800_000_000
MS = SEC * 1000
WORKSPACE_ID = "wsp_" + "W" * 22
OTHER_WORKSPACE_ID = "wsp_" + "X" * 22


class StatefulV2Store(_StatefulV2Store):
    """Keep the non-authoritative simulator aligned with canonical guest scope."""

    def _revoke_invite(self, keys, args):
        invite = self.get_json(keys[0])
        if invite is None:
            return {"status": "missing"}
        if redis_store.normalize_v2_invite_record(invite) is None:
            return {"status": "malformed"}
        if (
            invite.get("ownerEmail") != args[0]
            or invite.get("workspaceId") != args[1]
            or invite.get("mailboxId") != args[2]
            or invite.get("collaborationId") != args[3]
            or invite.get("inviteId") != args[4]
            or args[5] != args[0]
            or args[6] != "revoke_invite"
        ):
            return {"status": "forbidden"}
        if invite.get("v") != 2:
            return {"status": "malformed"}
        if invite.get("status") == "revoked":
            return {"status": "already_revoked"}
        if invite.get("activeSessionHash") and len(keys) < 2:
            return {"status": "retry"}
        now = int(args[7])
        if len(keys) > 1 and keys[1] in self.values:
            session = self.get_json(keys[1])
            if (
                normalize_v2_guest_session_record(session) is None
                or session.get("sessionHash") != invite.get("activeSessionHash")
                or session.get("inviteId") != invite.get("inviteId")
                or session.get("ownerEmail") != invite.get("ownerEmail")
                or session.get("workspaceId") != invite.get("workspaceId")
                or session.get("mailboxId") != invite.get("mailboxId")
                or session.get("collaborationId") != invite.get("collaborationId")
                or session.get("allowedActions") != ["read", "reply"]
                or session.get("visibility") != "shared_only"
                or session.get("expiresAt", 0) > invite.get("expiresAt", -1)
            ):
                return {"status": "malformed"}
            session.update(status="revoked", revokedAt=now)
            self.put_json(keys[1], session)
        invite.update(status="revoked", revokedAt=now, revokedBy=args[5])
        self.put_json(keys[0], invite)
        return {"status": "revoked_ok"}


def _seconds(value):
    return SEC + value if type(value) is int and value < SEC else value


def _milliseconds(value):
    return MS + value if type(value) is int and value < MS else value


def _canonical_session(record):
    result = dict(record)
    for field in ("createdAt", "lastUsedAt", "expiresAt", "revokedAt", "loggedOutAt"):
        if result.get(field) is not None:
            result[field] = _seconds(result[field])
    return result


def create_v2_invite(record, *, now, command_transport):
    return _storage_create_v2_invite(record, now=_seconds(now), command_transport=command_transport)


def load_v2_invite_by_token(raw_token, *, now, command_transport):
    return _storage_load_v2_invite_by_token(raw_token, now=_seconds(now), command_transport=command_transport)


def atomic_exchange_v2_invite(*, raw_token, invite_id, session_record, now, session_ttl, command_transport):
    return _storage_atomic_exchange_v2_invite(
        raw_token=raw_token, invite_id=invite_id,
        session_record=_canonical_session(session_record), now=_seconds(now),
        session_ttl=session_ttl, command_transport=command_transport,
    )


def update_v2_guest_session(session, *, normalizer, now, csrf_token_hash, touch_last_used, command_transport):
    return _storage_update_v2_guest_session(
        _canonical_session(session), normalizer=normalizer, now=_seconds(now),
        csrf_token_hash=csrf_token_hash, touch_last_used=touch_last_used,
        command_transport=command_transport,
    )


def revoke_v2_guest_session(raw_session_id, *, now, command_transport):
    return _storage_revoke_v2_guest_session(
        hash_v2_secret(raw_session_id),
        invite_id="B" * 22,
        owner_email="owner@example.com",
        workspace_id=WORKSPACE_ID,
        mailbox_id="mailbox-1",
        collaboration_id="A" * 22,
        now=_seconds(now),
        command_transport=command_transport,
    )


def save_v2_thread_if_expected(record, expected, *, command_transport):
    replacement = dict(record)
    replacement["createdAt"] = _milliseconds(replacement["createdAt"])
    replacement["updatedAt"] = _milliseconds(replacement["updatedAt"])
    replacement["messages"] = [
        {**message, "createdAt": _milliseconds(message["createdAt"])}
        for message in replacement["messages"]
    ]
    return _storage_save_v2_thread_if_expected(
        replacement, _milliseconds(expected), command_transport=command_transport
    )


def revoke_v2_invite(invite_id, *, owner_email, revoked_by, now, command_transport):
    return _revoke_v2_invite(
        invite_id,
        owner_email=owner_email,
        workspace_id=WORKSPACE_ID,
        mailbox_id="mailbox-1",
        collaboration_id="A" * 22,
        revoked_by=revoked_by,
        now=_seconds(now),
        command_transport=command_transport,
    )


def thread_record() -> dict:
    return {
        "v": 2, "collaborationId": "A" * 22,
        "ownerEmail": "owner@example.com", "workspaceId": WORKSPACE_ID,
        "mailboxId": "mailbox-1",
        "sourceRef": {"provider": "google", "providerMessageId": "gmail-1"},
        "sourceMessage": {
            "subject": "Review", "senderDisplay": "Sender",
            "fromDisplay": "sender@example.com", "timestamp": "today", "bodyText": "Body",
        },
        "state": "needs_review", "messages": [], "createdAt": MS + 100, "updatedAt": MS + 100,
    }


def invite_record() -> dict:
    return {
        "v": 2, "inviteId": "B" * 22, "tokenHash": "c" * 64,
        "ownerEmail": "owner@example.com", "workspaceId": WORKSPACE_ID,
        "mailboxId": "mailbox-1", "collaborationId": "A" * 22,
        "invitedEmail": "reviewer@example.com", "identityAssurance": "link_possession",
        "allowedActions": ["read", "reply"], "visibility": "shared_only",
        "createdBy": {"ownerEmail": "owner@example.com", "displayName": "Owner"},
        "createdAt": SEC + 100, "expiresAt": SEC + 200, "status": "active",
        "exchangedAt": None, "exchangeCount": 0, "revokedAt": None, "revokedBy": None,
    }


def invite_graph_linkage(record: dict, *, has_previous: bool = False) -> dict:
    return {
        "inviteId": record["inviteId"],
        "tokenHash": record["tokenHash"],
        "tokenPointer": record["inviteId"],
        "currentIdentityState": "present",
        "currentIdentityInviteId": record["inviteId"],
        "currentIdentityTokenHash": record["tokenHash"],
        "canonicalInviteId": record["inviteId"],
        "canonicalTokenHash": record["tokenHash"],
        "previousIdentityState": "absent" if has_previous else "not_configured",
    }


def wire_record(record: dict, kind: str) -> dict:
    encoded = encode_v2_wire_record(record, kind)
    if encoded is None:
        raise AssertionError("test fixture is not a typed v2 record")
    return encoded


def wire_json(record: dict, kind: str) -> str:
    return json.dumps(wire_record(record, kind), separators=(",", ":"), sort_keys=True)


class CollaborationV2RedisStoreTests(unittest.TestCase):
    def setUp(self):
        self.previous_hmac = os.environ.get("CUEVION_COLLAB_INDEX_HMAC_KEY")
        self.previous_rotation_hmac = os.environ.pop("CUEVION_COLLAB_INDEX_HMAC_KEY_PREVIOUS", None)
        os.environ["CUEVION_COLLAB_INDEX_HMAC_KEY"] = base64.urlsafe_b64encode(b"0123456789abcdef" * 2).decode("ascii").rstrip("=")

    def tearDown(self):
        if self.previous_hmac is None:
            os.environ.pop("CUEVION_COLLAB_INDEX_HMAC_KEY", None)
        else:
            os.environ["CUEVION_COLLAB_INDEX_HMAC_KEY"] = self.previous_hmac
        if self.previous_rotation_hmac is None:
            os.environ.pop("CUEVION_COLLAB_INDEX_HMAC_KEY_PREVIOUS", None)
        else:
            os.environ["CUEVION_COLLAB_INDEX_HMAC_KEY_PREVIOUS"] = self.previous_rotation_hmac

    def assert_atomic_guest_store_event(self, logger: Mock, stage: str) -> str:
        self.assertEqual(logger.call_count, 1)
        line = logger.call_args.args[0]
        self.assertEqual(
            json.loads(line),
            {
                "event": "cuevion_collaboration_atomic_guest_store_failure",
                "stage": stage,
                "internalSafeCode": "storage_protocol_error",
            },
        )
        self.assertLessEqual(len(line.encode("utf-8")), 192)
        return line

    def assert_atomic_guest_lua_malformed_events(
        self,
        logger: Mock,
        predicate: str,
    ) -> tuple[str, str]:
        self.assertEqual(logger.call_count, 2)
        d4_line, d5_line = (call.args[0] for call in logger.call_args_list)
        self.assertEqual(
            json.loads(d4_line),
            {
                "event": "cuevion_collaboration_atomic_guest_store_failure",
                "stage": "lua_malformed",
                "internalSafeCode": "storage_protocol_error",
            },
        )
        self.assertEqual(
            json.loads(d5_line),
            {
                "event": "cuevion_collaboration_atomic_guest_lua_malformed",
                "predicate": predicate,
            },
        )
        self.assertEqual(set(json.loads(d5_line)), {"event", "predicate"})
        self.assertLessEqual(
            len(d5_line.encode("utf-8")),
            redis_store._ATOMIC_GUEST_LUA_MALFORMED_EVENT_MAX_BYTES,
        )
        return d4_line, d5_line

    def run_atomic_guest_store(self, transport, *, logger_side_effect=None):
        commands = []

        def observed_transport(command):
            commands.append(command)
            return transport(command)

        logger = Mock(side_effect=logger_side_effect)
        invite = invite_record()
        with patch("builtins.print", logger):
            result = _storage_create_v2_thread_with_guest(
                thread_record(),
                invite,
                now=invite["createdAt"],
                command_transport=observed_transport,
            )
        return result, commands, logger

    def test_indexes_hash_owner_invitee_source_and_bearer_values(self):
        source_key = build_v2_source_thread_key(
            "owner@example.com", "mailbox-1", {"provider": "google", "providerMessageId": "gmail-1"}
        )
        invite_key = build_v2_thread_invite_key(
            "owner@example.com", "A" * 22, "reviewer@example.com"
        )
        raw_token = "raw-token-that-must-never-be-a-key"
        token_key = build_v2_invite_token_key(hash_v2_secret(raw_token))
        session_key = build_v2_guest_session_key(hash_v2_secret("raw-session"))
        combined = " ".join((source_key, invite_key, token_key, session_key))
        self.assertNotIn("owner@example.com", combined)
        self.assertNotIn("reviewer@example.com", combined)
        self.assertNotIn("raw-token", combined)
        self.assertNotIn("raw-session", combined)
        self.assertIsNotNone(resolve_v2_index_hmac_key())
        self.assertNotEqual(source_key, build_v2_thread_invite_key("owner@example.com", "A" * 22, None))
        collision_a = build_v2_source_thread_key("owner@example.com", "a:b", {"provider": "google", "providerMessageId": "c"})
        collision_b = build_v2_source_thread_key("owner@example.com", "a", {"provider": "google", "providerMessageId": "b:c"})
        self.assertNotEqual(collision_a, collision_b)
        self.assertNotIn("a:b", collision_a)

    def test_every_v2_key_family_uses_one_explicit_cluster_slot(self):
        keys = [
            build_v2_thread_key("A" * 22),
            build_v2_source_thread_key("owner@example.com", "mailbox-1", {"provider": "google", "providerMessageId": "gmail-1"}),
            build_v2_invite_key("I" * 22),
            build_v2_invite_token_key("a" * 64),
            build_v2_thread_invite_key("owner@example.com", "A" * 22, "reviewer@example.com"),
            build_v2_guest_session_key("b" * 64),
        ]

        def slot(key: str) -> int:
            tag = key[key.index("{") + 1 : key.index("}")]
            return binascii.crc_hqx(tag.encode("ascii"), 0) % 16384

        self.assertEqual(len({slot(key) for key in keys}), 1)
        self.assertTrue(all("{cuevion-collab-v2}" in key for key in keys))

    def test_previous_hmac_key_is_lazy_distinct_and_migrates_source_pointer(self):
        old_encoded = base64.urlsafe_b64encode(b"o" * 32).decode("ascii").rstrip("=")
        new_encoded = base64.urlsafe_b64encode(b"n" * 32).decode("ascii").rstrip("=")
        store = StatefulV2Store()
        os.environ["CUEVION_COLLAB_INDEX_HMAC_KEY"] = old_encoded
        first = create_v2_thread(thread_record(), command_transport=store)
        self.assertTrue(first["created"])
        old_source_key = build_v2_source_thread_key(
            "owner@example.com", "mailbox-1", thread_record()["sourceRef"]
        )
        os.environ["CUEVION_COLLAB_INDEX_HMAC_KEY"] = new_encoded
        os.environ["CUEVION_COLLAB_INDEX_HMAC_KEY_PREVIOUS"] = old_encoded
        current, previous = resolve_v2_index_hmac_keys()
        self.assertNotEqual(current, previous)
        new_source_key = build_v2_source_thread_key(
            "owner@example.com", "mailbox-1", thread_record()["sourceRef"], hmac_key=current
        )
        duplicate = create_v2_thread(thread_record(), command_transport=store)
        self.assertFalse(duplicate["created"])
        self.assertEqual(store.values[new_source_key], "A" * 22)
        self.assertNotIn(old_source_key, store.values)
        os.environ["CUEVION_COLLAB_INDEX_HMAC_KEY_PREVIOUS"] = new_encoded
        self.assertIsNone(resolve_v2_index_hmac_keys())

    def test_hmac_secret_requires_canonical_length_but_not_byte_distribution(self):
        calls = []
        os.environ.pop("CUEVION_COLLAB_INDEX_HMAC_KEY", None)
        result = create_v2_thread(thread_record(), command_transport=lambda command: calls.append(command))
        self.assertEqual(result["error"]["code"], "index_hmac_unavailable")
        self.assertEqual(calls, [])
        os.environ["CUEVION_COLLAB_INDEX_HMAC_KEY"] = "A" * 43
        self.assertEqual(resolve_v2_index_hmac_key(), b"\0" * 32)
        for invalid in (
            base64.urlsafe_b64encode(b"short").decode("ascii").rstrip("="),
            "A" * 43 + "=",
            "A" * 42 + "+",
            " A" * 22,
        ):
            self.assertIsNone(resolve_v2_index_hmac_key(invalid))

    def test_thread_creation_is_one_atomic_eval_and_duplicate_loads_canonical_record(self):
        commands = []
        result = create_v2_thread(
            thread_record(),
            command_transport=lambda command: (
                commands.append(command) or {"result": json.dumps({"status": "created"})}
            ),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(commands[0][0], "EVAL")
        self.assertEqual(commands[0][2], 2)

        duplicate_calls = []

        def duplicate_transport(command):
            duplicate_calls.append(command)
            if len(duplicate_calls) == 1:
                return {"result": json.dumps({"status": "duplicate", "collaborationId": "A" * 22})}
            if len(duplicate_calls) == 2:
                return {"result": json.dumps({"status": "found", "collaborationId": "A" * 22})}
            return {"result": wire_json(thread_record(), "thread")}

        duplicate = create_v2_thread(thread_record(), command_transport=duplicate_transport)
        self.assertEqual(duplicate["status"], "ok")
        self.assertFalse(duplicate["created"])
        self.assertEqual([command[0] for command in duplicate_calls], ["EVAL", "EVAL", "GET"])

        for forged_id in ("invalid canonical id", "B" * 22):
            forged_calls = []

            def forged_transport(command, returned_id=forged_id):
                forged_calls.append(command)
                if len(forged_calls) == 1:
                    return {"result": json.dumps({"status": "duplicate", "collaborationId": returned_id})}
                if len(forged_calls) == 2:
                    return {"result": json.dumps({"status": "found", "collaborationId": "A" * 22})}
                return {"result": wire_json(thread_record(), "thread")}

            rejected = create_v2_thread(
                {**thread_record(), "collaborationId": "C" * 22},
                command_transport=forged_transport,
            )
            self.assertEqual(rejected.get("error"), {"code": "storage_protocol_error"})

    def test_load_distinguishes_missing_malformed_and_unavailable(self):
        missing = load_v2_thread("A" * 22, command_transport=lambda _command: {"result": None})
        malformed = load_v2_thread("A" * 22, command_transport=lambda _command: {"result": "not-json"})
        unavailable = load_v2_thread(
            "A" * 22,
            command_transport=lambda _command: (_ for _ in ()).throw(RuntimeError("raw kv host secret")),
        )
        self.assertEqual(missing["status"], "missing")
        self.assertEqual(malformed["status"], "malformed")
        self.assertEqual(unavailable["error"], {"code": "storage_unavailable"})
        for result in (missing, malformed, unavailable):
            self.assertNotIn("record", result)

    def test_wire_helpers_fail_closed_on_deeply_nested_input(self):
        deeply_nested_raw = (
            '{"v":"2","messages":'
            + ("[" * 1200)
            + "null"
            + ("]" * 1200)
            + "}"
        )
        self.assertIsNone(_v2_json_from_wire(deeply_nested_raw, "thread"))

        nested = None
        for _ in range(1200):
            nested = [nested]
        self.assertIsNone(_v2_wire_json({"v": 2, "messages": nested}, "thread"))

    def test_record_bearing_successes_are_typed_and_nested_session_results_are_exact(self):
        created = create_v2_thread(
            thread_record(),
            command_transport=lambda _command: {"result": json.dumps({"status": "created"})},
        )
        self.assertIs(type(created), _V2RecordResult)

        session = {
            "v": 2, "sessionHash": hash_v2_secret("s" * 43), "csrfTokenHash": hash_v2_secret("c" * 43),
            "inviteId": "B" * 22, "ownerEmail": "owner@example.com", "workspaceId": WORKSPACE_ID,
            "mailboxId": "mailbox-1", "collaborationId": "A" * 22,
            "allowedActions": ["read", "reply"], "visibility": "shared_only", "identityAssurance": "link_possession",
            "guestDisplayName": "Guest", "createdAt": 101, "lastUsedAt": 101, "expiresAt": 150,
            "status": "active", "revokedAt": None, "loggedOutAt": None,
        }
        malformed_nested = {**_canonical_session(session), "unexpected": True}
        result = update_v2_guest_session(
            session, normalizer=normalize_v2_guest_session_record, now=102,
            csrf_token_hash=hash_v2_secret("n" * 43), touch_last_used=False,
            command_transport=lambda _command: {
                "result": json.dumps({"status": "updated", "session": malformed_nested})
            },
        )
        self.assertEqual(result["error"]["code"], "storage_protocol_error")
        self.assertNotIn("session", result)

    def test_equal_time_semantic_noop_never_dispatches_a_storage_command(self):
        session = {
            "v": 2, "sessionHash": hash_v2_secret("s" * 43),
            "csrfTokenHash": hash_v2_secret("c" * 43), "inviteId": "B" * 22,
            "ownerEmail": "owner@example.com", "workspaceId": WORKSPACE_ID,
            "mailboxId": "mailbox-1", "collaborationId": "A" * 22,
            "allowedActions": ["read", "reply"], "visibility": "shared_only",
            "identityAssurance": "link_possession", "guestDisplayName": "Guest",
            "createdAt": SEC + 101, "lastUsedAt": SEC + 101,
            "expiresAt": SEC + 150, "status": "active", "revokedAt": None,
            "loggedOutAt": None,
        }
        commands = []
        result = _storage_update_v2_guest_session(
            session,
            normalizer=normalize_v2_guest_session_record,
            now=session["lastUsedAt"],
            csrf_token_hash=session["csrfTokenHash"],
            touch_last_used=False,
            command_transport=lambda command: commands.append(command) or self.fail(
                "a semantic no-op must not dispatch Redis"
            ),
        )
        self.assertEqual(result, {"status": "unchanged"})
        self.assertEqual(commands, [])

    def test_atomic_guest_store_stage_allowlist_and_output_are_bounded(self):
        expected_stages = {
            "rest_empty_body",
            "rest_json_decode",
            "rest_response_shape",
            "command_payload_shape",
            "command_error_envelope",
            "command_result_envelope",
            "eval_json_decode",
            "eval_result_shape",
            "eval_status_shape",
            "lua_malformed",
            "existing_id",
            "existing_thread_reload",
            "existing_invite_normalization",
            "existing_invite_create",
        }
        self.assertEqual(
            redis_store._ATOMIC_GUEST_STORE_FAILURE_STAGES,
            expected_stages,
        )
        for stage in expected_stages:
            line = json.dumps(
                {
                    "event": "cuevion_collaboration_atomic_guest_store_failure",
                    "stage": stage,
                    "internalSafeCode": "storage_protocol_error",
                },
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            self.assertLessEqual(len(line.encode("utf-8")), 192)

    def test_atomic_guest_lua_malformed_predicate_allowlist_and_output_are_bounded(self):
        expected_predicates = frozenset(
            {
                "argv_shape",
                "key_count",
                "thread_decode",
                "thread_messages",
                "thread_valid",
                "thread_id_binding",
                "invite_decode",
                "invite_valid",
                "invite_status",
                "invite_created_at",
                "invite_ttl",
                "invite_id_binding",
                "invite_token_binding",
                "invite_owner_binding",
                "invite_workspace_binding",
                "invite_mailbox_binding",
                "invite_collaboration_binding",
            }
        )
        self.assertEqual(
            redis_store._ATOMIC_GUEST_LUA_MALFORMED_PREDICATES,
            expected_predicates,
        )
        self.assertEqual(
            redis_store._ATOMIC_GUEST_LUA_MALFORMED_EVENT_MAX_BYTES,
            128,
        )
        self.assertLess(
            redis_store._ATOMIC_GUEST_LUA_MALFORMED_EVENT_MAX_BYTES,
            192,
        )
        event_sizes = []
        for predicate in expected_predicates:
            with self.subTest(predicate=predicate):
                logger = Mock()
                observer = redis_store._new_atomic_guest_lua_malformed_observer()
                with patch("builtins.print", logger):
                    observer(predicate)
                self.assertEqual(logger.call_count, 1)
                line = logger.call_args.args[0]
                self.assertEqual(
                    json.loads(line),
                    {
                        "event": "cuevion_collaboration_atomic_guest_lua_malformed",
                        "predicate": predicate,
                    },
                )
                self.assertEqual(set(json.loads(line)), {"event", "predicate"})
                self.assertLessEqual(
                    len(line.encode("utf-8")),
                    redis_store._ATOMIC_GUEST_LUA_MALFORMED_EVENT_MAX_BYTES,
                )
                event_sizes.append(len(line.encode("utf-8")))
        self.assertEqual(max(event_sizes), 103)
        self.assertLessEqual(
            max(event_sizes) + 1,
            redis_store._ATOMIC_GUEST_LUA_MALFORMED_EVENT_MAX_BYTES,
        )

        logger = Mock()
        observer = redis_store._new_atomic_guest_lua_malformed_observer()
        with patch("builtins.print", logger):
            observer("thread_valid")
            observer("invite_valid")
        self.assertEqual(logger.call_count, 1)
        self.assertEqual(json.loads(logger.call_args.args[0])["predicate"], "thread_valid")

        for rejected in (
            None,
            1,
            ["thread_valid"],
            "",
            "PrivateArbitraryPredicateMarker",
        ):
            with self.subTest(rejected=rejected):
                logger = Mock()
                observer = redis_store._new_atomic_guest_lua_malformed_observer()
                with patch("builtins.print", logger):
                    observer(rejected)
                logger.assert_not_called()

    def test_atomic_guest_store_rest_protocol_stages_are_exact_and_secret_free(self):
        class Response:
            def __init__(self, raw: bytes):
                self.raw = raw

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return self.raw

        rest_url = "https://PrivateRestHostMarker.invalid"
        rest_token = "PrivateRestTokenMarker"
        command_secret = "PrivateRedisCommandMarker"
        for stage, raw in (
            ("rest_empty_body", b""),
            ("rest_json_decode", b'{"PrivateResponseBodyMarker":'),
            ("rest_response_shape", b'["PrivateResponseBodyMarker"]'),
        ):
            with self.subTest(stage=stage):
                logger = Mock()
                observer = (
                    redis_store._new_atomic_guest_store_protocol_failure_observer()
                )
                with patch.object(
                    redis_store,
                    "urlopen",
                    return_value=Response(raw),
                ) as request, patch("builtins.print", logger):
                    result = _perform_v2_rest_command(
                        {"rest_url": rest_url, "rest_token": rest_token},
                        ["GET", command_secret],
                        protocol_failure_observer=observer,
                    )
                self.assertEqual(
                    result,
                    {
                        "status": "unavailable",
                        "error": {"code": "storage_protocol_error"},
                    },
                )
                request.assert_called_once()
                line = self.assert_atomic_guest_store_event(logger, stage)
                for private_value in (
                    rest_url,
                    rest_token,
                    command_secret,
                    "PrivateResponseBodyMarker",
                    "Authorization",
                ):
                    self.assertNotIn(private_value, line)

    def test_atomic_guest_store_command_protocol_stages_are_exact(self):
        cases = (
            (
                "command_payload_shape",
                ["PrivatePayloadValue"],
            ),
            (
                "command_error_envelope",
                {
                    "status": "unavailable",
                    "error": {
                        "code": "storage_unavailable",
                        "private": "PrivateErrorEnvelopeValue",
                    },
                },
            ),
            (
                "command_result_envelope",
                {"result": None, "private": "PrivateResultEnvelopeValue"},
            ),
        )
        for stage, payload in cases:
            with self.subTest(stage=stage):
                logger = Mock()
                observer = (
                    redis_store._new_atomic_guest_store_protocol_failure_observer()
                )
                with patch("builtins.print", logger):
                    result = _v2_command(
                        ["GET", "PrivateCommandKey"],
                        command_transport=lambda _command, value=payload: value,
                        protocol_failure_observer=observer,
                    )
                self.assertEqual(
                    result,
                    {
                        "status": "unavailable",
                        "error": {"code": "storage_protocol_error"},
                    },
                )
                line = self.assert_atomic_guest_store_event(logger, stage)
                for private_value in (
                    "PrivatePayloadValue",
                    "PrivateErrorEnvelopeValue",
                    "PrivateResultEnvelopeValue",
                    "PrivateCommandKey",
                ):
                    self.assertNotIn(private_value, line)

    def test_atomic_guest_store_eval_protocol_stages_are_exact(self):
        cases = (
            ("eval_json_decode", "PrivateInvalidEvalJson{"),
            ("eval_result_shape", json.dumps(["PrivateEvalListValue"])),
            (
                "eval_status_shape",
                json.dumps({"status": "PrivateUnknownEvalStatus"}),
            ),
        )
        for stage, eval_result in cases:
            with self.subTest(stage=stage):
                logger = Mock()
                observer = (
                    redis_store._new_atomic_guest_store_protocol_failure_observer()
                )
                with patch("builtins.print", logger):
                    result = _v2_eval(
                        ["EVAL", "PrivateLuaValue", 0],
                        command_transport=lambda _command, value=eval_result: {
                            "result": value
                        },
                        response_shapes={"created": set()},
                        protocol_failure_observer=observer,
                    )
                self.assertEqual(
                    result,
                    {
                        "status": "unavailable",
                        "error": {"code": "storage_protocol_error"},
                    },
                )
                line = self.assert_atomic_guest_store_event(logger, stage)
                for private_value in (
                    "PrivateInvalidEvalJson",
                    "PrivateEvalListValue",
                    "PrivateUnknownEvalStatus",
                    "PrivateLuaValue",
                ):
                    self.assertNotIn(private_value, line)

    def test_atomic_guest_store_lua_malformed_is_accepted_then_mapped(self):
        result, commands, logger = self.run_atomic_guest_store(
            lambda _command: {
                "result": json.dumps(
                    {"status": "malformed", "predicate": "thread_valid"}
                )
            }
        )

        self.assertEqual(
            result,
            {
                "status": "malformed",
                "error": {"code": "storage_protocol_error"},
            },
        )
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], "EVAL")
        self.assertNotIn("predicate", result)
        self.assert_atomic_guest_lua_malformed_events(logger, "thread_valid")

    def test_atomic_guest_store_consumes_every_lua_malformed_predicate_internally(self):
        for predicate in redis_store._ATOMIC_GUEST_LUA_MALFORMED_PREDICATES:
            with self.subTest(predicate=predicate):
                result, commands, logger = self.run_atomic_guest_store(
                    lambda _command, value=predicate: {
                        "result": json.dumps(
                            {"status": "malformed", "predicate": value}
                        )
                    }
                )
                self.assertEqual(
                    result,
                    {
                        "status": "malformed",
                        "error": {"code": "storage_protocol_error"},
                    },
                )
                self.assertNotIn("predicate", result)
                self.assertEqual(len(commands), 1)
                self.assertEqual(commands[0][0], "EVAL")
                self.assert_atomic_guest_lua_malformed_events(logger, predicate)

    def test_atomic_guest_store_rejects_non_allowlisted_predicate_from_d5(self):
        private_marker = "PrivateArbitraryPredicateMarker"
        for predicate in (private_marker, "", None, ["thread_valid"]):
            with self.subTest(predicate=predicate):
                result, commands, logger = self.run_atomic_guest_store(
                    lambda _command, value=predicate: {
                        "result": json.dumps(
                            {"status": "malformed", "predicate": value}
                        )
                    }
                )
                self.assertEqual(
                    result,
                    {
                        "status": "malformed",
                        "error": {"code": "storage_protocol_error"},
                    },
                )
                self.assertEqual(len(commands), 1)
                line = self.assert_atomic_guest_store_event(logger, "lua_malformed")
                self.assertNotIn(private_marker, line)

    def test_atomic_guest_store_lua_malformed_response_shape_is_exact(self):
        private_marker = "PrivateMalformedShapeMarker"
        cases = (
            {"status": "malformed"},
            {
                "status": "malformed",
                "predicate": "thread_valid",
                "private": private_marker,
            },
        )
        for payload in cases:
            with self.subTest(fields=set(payload)):
                result, commands, logger = self.run_atomic_guest_store(
                    lambda _command, value=payload: {
                        "result": json.dumps(value)
                    }
                )
                self.assertEqual(
                    result,
                    {
                        "status": "unavailable",
                        "error": {"code": "storage_protocol_error"},
                    },
                )
                self.assertNotIn("predicate", result)
                self.assertEqual(len(commands), 1)
                line = self.assert_atomic_guest_store_event(
                    logger,
                    "eval_status_shape",
                )
                self.assertNotIn(private_marker, line)

    def test_atomic_guest_store_existing_convergence_stages_are_distinct(self):
        calls = 0

        def invalid_id_transport(_command):
            nonlocal calls
            calls += 1
            return {
                "result": json.dumps(
                    {
                        "status": "existing",
                        "collaborationId": "invalid-existing-id",
                    }
                    if calls == 1
                    else {"status": "missing"}
                )
            }

        result, commands, logger = self.run_atomic_guest_store(
            invalid_id_transport
        )
        self.assertEqual(result["error"], {"code": "storage_protocol_error"})
        self.assertEqual(len(commands), 2)
        self.assert_atomic_guest_store_event(logger, "existing_id")

        calls = 0

        def reload_failure_transport(_command):
            nonlocal calls
            calls += 1
            return {
                "result": json.dumps(
                    {
                        "status": "existing",
                        "collaborationId": "C" * 22,
                    }
                    if calls == 1
                    else {"status": "missing"}
                )
            }

        result, commands, logger = self.run_atomic_guest_store(
            reload_failure_transport
        )
        self.assertEqual(result["error"], {"code": "storage_protocol_error"})
        self.assertEqual(len(commands), 2)
        self.assert_atomic_guest_store_event(logger, "existing_thread_reload")

        existing_thread = {**thread_record(), "collaborationId": "C" * 22}

        def loaded_existing_transport(command):
            if command[0] == "GET":
                return {"result": wire_json(existing_thread, "thread")}
            if command[1] == redis_store._CREATE_V2_THREAD_WITH_GUEST_LUA:
                return {
                    "result": json.dumps(
                        {
                            "status": "existing",
                            "collaborationId": existing_thread["collaborationId"],
                        }
                    )
                }
            return {
                "result": json.dumps(
                    {
                        "status": "found",
                        "collaborationId": existing_thread["collaborationId"],
                    }
                )
            }

        normalize_invite = redis_store.normalize_v2_invite_record
        normalize_calls = 0

        def fail_converged_invite(value):
            nonlocal normalize_calls
            normalize_calls += 1
            return None if normalize_calls == 2 else normalize_invite(value)

        with patch.object(
            redis_store,
            "normalize_v2_invite_record",
            side_effect=fail_converged_invite,
        ):
            result, commands, logger = self.run_atomic_guest_store(
                loaded_existing_transport
            )
        self.assertEqual(result["error"], {"code": "storage_protocol_error"})
        self.assertEqual(len(commands), 3)
        self.assert_atomic_guest_store_event(
            logger, "existing_invite_normalization"
        )

        calls = 0

        def invite_create_failure_transport(command):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "result": json.dumps(
                        {
                            "status": "existing",
                            "collaborationId": existing_thread["collaborationId"],
                        }
                    )
                }
            if calls == 2:
                return {
                    "result": json.dumps(
                        {
                            "status": "found",
                            "collaborationId": existing_thread["collaborationId"],
                        }
                    )
                }
            if calls == 3:
                return {"result": wire_json(existing_thread, "thread")}
            return None

        result, commands, logger = self.run_atomic_guest_store(
            invite_create_failure_transport
        )
        self.assertEqual(result["error"], {"code": "storage_protocol_error"})
        self.assertEqual(len(commands), 4)
        self.assert_atomic_guest_store_event(logger, "existing_invite_create")

    def test_atomic_guest_store_created_and_existing_success_emit_no_event(self):
        result, commands, logger = self.run_atomic_guest_store(
            lambda _command: {"result": json.dumps({"status": "created"})}
        )
        self.assertIs(type(result), redis_store._V2ThreadInviteCreateResult)
        self.assertTrue(result.thread_created)
        self.assertTrue(result.invite_created)
        self.assertEqual(len(commands), 1)
        thread = thread_record()
        invite = invite_record()
        current_hmac, previous_hmac = resolve_v2_index_hmac_keys()
        self.assertIsNone(previous_hmac)
        expected_keys = [
            build_v2_thread_key(thread["collaborationId"]),
            build_v2_source_thread_key(
                thread["ownerEmail"],
                thread["mailboxId"],
                thread["sourceRef"],
                hmac_key=current_hmac,
            ),
            build_v2_invite_key(invite["inviteId"]),
            build_v2_invite_token_key(invite["tokenHash"]),
            build_v2_thread_invite_key(
                invite["ownerEmail"],
                invite["collaborationId"],
                invite.get("invitedEmail"),
                hmac_key=current_hmac,
            ),
            redis_store.build_v2_external_guest_index_key(
                thread["collaborationId"]
            ),
        ]
        self.assertEqual(
            commands[0],
            [
                "EVAL",
                redis_store._CREATE_V2_THREAD_WITH_GUEST_LUA,
                len(expected_keys),
                *expected_keys,
                _v2_wire_json(thread, "thread"),
                _v2_wire_json(invite, "invite"),
                thread["collaborationId"],
                invite["inviteId"],
                str(invite["expiresAt"] - invite["createdAt"]),
                str(invite["createdAt"]),
                str(redis_store.V2_THREAD_RETENTION_SECONDS),
                "0",
                redis_store.V2_THREAD_KEY_PREFIX,
                invite["tokenHash"],
            ],
        )
        logger.assert_not_called()

        existing_thread = {**thread_record(), "collaborationId": "C" * 22}
        calls = 0

        def existing_success_transport(command):
            nonlocal calls
            calls += 1
            if calls == 1:
                value = {
                    "status": "existing",
                    "collaborationId": existing_thread["collaborationId"],
                }
            elif calls == 2:
                value = {
                    "status": "found",
                    "collaborationId": existing_thread["collaborationId"],
                }
            elif calls == 3:
                return {"result": wire_json(existing_thread, "thread")}
            elif calls == 4:
                return {"result": None}
            else:
                value = {"status": "created"}
            return {"result": json.dumps(value)}

        result, commands, logger = self.run_atomic_guest_store(
            existing_success_transport
        )
        self.assertIs(type(result), redis_store._V2ThreadInviteCreateResult)
        self.assertFalse(result.thread_created)
        self.assertTrue(result.invite_created)
        self.assertEqual(len(commands), 5)
        logger.assert_not_called()

    def test_atomic_guest_store_other_failures_emit_no_event(self):
        result, commands, logger = self.run_atomic_guest_store(
            lambda _command: {"error": "PrivateRedisUnavailableMarker"}
        )
        self.assertEqual(result["error"], {"code": "storage_unavailable"})
        self.assertEqual(len(commands), 1)
        logger.assert_not_called()

        invite = invite_record()
        commands = []
        logger = Mock()
        with patch.object(
            redis_store,
            "resolve_v2_index_hmac_keys",
            return_value=None,
        ), patch("builtins.print", logger):
            result = _storage_create_v2_thread_with_guest(
                thread_record(),
                invite,
                now=invite["createdAt"],
                command_transport=lambda command: commands.append(command),
            )
        self.assertEqual(result["error"], {"code": "index_hmac_unavailable"})
        self.assertEqual(commands, [])
        logger.assert_not_called()

        logger = Mock()
        with patch("builtins.print", logger):
            result = _storage_create_v2_thread_with_guest(
                thread_record(),
                invite,
                now="invalid",
                command_transport=lambda _command: self.fail(
                    "invalid input must not dispatch Redis"
                ),
            )
        self.assertEqual(result["error"], {"code": "invalid_request"})
        logger.assert_not_called()

        expired_invite = {**invite, "expiresAt": invite["createdAt"]}
        logger = Mock()
        with patch.object(
            redis_store,
            "normalize_v2_invite_record",
            return_value=expired_invite,
        ), patch.object(
            redis_store,
            "_v2_wire_json",
            return_value="PrivateWireMarker",
        ), patch("builtins.print", logger):
            result = _storage_create_v2_thread_with_guest(
                thread_record(),
                invite,
                now=invite["createdAt"],
                command_transport=lambda _command: self.fail(
                    "expired input must not dispatch Redis"
                ),
            )
        self.assertEqual(result["error"], {"code": "invite_expired"})
        logger.assert_not_called()

        for status, code in (
            ("conflict", "invalid_request"),
            ("source_pointer_conflict", "source_changed"),
        ):
            with self.subTest(status=status):
                result, commands, logger = self.run_atomic_guest_store(
                    lambda _command, value=status: {
                        "result": json.dumps({"status": value})
                    }
                )
            self.assertEqual(result["error"], {"code": code})
            self.assertEqual(len(commands), 1)
            logger.assert_not_called()

        existing_thread = {**thread_record(), "collaborationId": "C" * 22}
        logger = Mock()
        commands = []
        with patch.object(
            redis_store,
            "_load_v2_thread_by_source",
            return_value={"status": "ok", "record": existing_thread},
        ), patch.object(
            redis_store,
            "_create_v2_invite",
            return_value={
                "status": "conflict",
                "error": {"code": "guest_capacity_reached"},
            },
        ), patch("builtins.print", logger):
            result = _storage_create_v2_thread_with_guest(
                thread_record(),
                invite,
                now=invite["createdAt"],
                command_transport=lambda command: commands.append(command)
                or {
                    "result": json.dumps(
                        {
                            "status": "existing",
                            "collaborationId": existing_thread["collaborationId"],
                        }
                    )
                },
            )
        self.assertEqual(
            result,
            {
                "status": "conflict",
                "error": {"code": "guest_capacity_reached"},
            },
        )
        self.assertEqual(len(commands), 1)
        logger.assert_not_called()

    def test_atomic_guest_store_logger_failure_is_behavior_neutral(self):
        result, commands, logger = self.run_atomic_guest_store(
            lambda _command: {"result": "PrivateInvalidEvalJson{"},
            logger_side_effect=RuntimeError("PrivateLoggerExceptionMarker"),
        )

        self.assertEqual(
            result,
            {
                "status": "unavailable",
                "error": {"code": "storage_protocol_error"},
            },
        )
        self.assertEqual(len(commands), 1)
        self.assertEqual(logger.call_count, 1)

    def test_atomic_guest_lua_malformed_logger_failure_preserves_result_and_d4(self):
        def fail_d5(line, **_kwargs):
            if json.loads(line).get("event") == (
                "cuevion_collaboration_atomic_guest_lua_malformed"
            ):
                raise RuntimeError("PrivateD5LoggerExceptionMarker")

        result, commands, logger = self.run_atomic_guest_store(
            lambda _command: {
                "result": json.dumps(
                    {"status": "malformed", "predicate": "thread_valid"}
                )
            },
            logger_side_effect=fail_d5,
        )

        self.assertEqual(
            result,
            {
                "status": "malformed",
                "error": {"code": "storage_protocol_error"},
            },
        )
        self.assertEqual(len(commands), 1)
        self.assertEqual(logger.call_count, 2)
        self.assertEqual(
            json.loads(logger.call_args_list[0].args[0]),
            {
                "event": "cuevion_collaboration_atomic_guest_store_failure",
                "stage": "lua_malformed",
                "internalSafeCode": "storage_protocol_error",
            },
        )
        self.assertEqual(
            json.loads(logger.call_args_list[1].args[0]),
            {
                "event": "cuevion_collaboration_atomic_guest_lua_malformed",
                "predicate": "thread_valid",
            },
        )
        self.assertNotIn(
            "PrivateD5LoggerExceptionMarker",
            repr(logger.call_args_list),
        )

    def test_atomic_guest_store_event_contains_no_request_or_storage_secrets(self):
        result, commands, logger = self.run_atomic_guest_store(
            lambda _command: {
                "result": json.dumps(
                    {"status": "malformed", "predicate": "thread_valid"}
                )
            }
        )
        self.assertEqual(result["error"], {"code": "storage_protocol_error"})
        d4_line, d5_line = self.assert_atomic_guest_lua_malformed_events(
            logger,
            "thread_valid",
        )
        self.assertEqual(set(json.loads(d4_line)), {"event", "stage", "internalSafeCode"})
        self.assertEqual(set(json.loads(d5_line)), {"event", "predicate"})
        lines = d4_line + d5_line
        thread = thread_record()
        invite = invite_record()
        for field in (
            "ownerEmail",
            "invitedEmail",
            "workspaceId",
            "mailboxId",
            "collaborationId",
            "inviteId",
            "sourceRef",
            "providerMessageId",
            "imapUid",
            "uidValidity",
            "threadKey",
            "sourceKey",
            "inviteKey",
            "tokenKey",
            "identityKey",
            "externalGuestIndexKey",
            "token",
            "tokenHash",
            "threadRecord",
            "inviteRecord",
            "wireJson",
            "luaScript",
            "restUrl",
            "restToken",
            "Authorization",
            "requestBody",
            "responseBody",
            "redisResult",
            "exception",
            "traceback",
        ):
            self.assertNotIn(json.dumps(field), lines)
        command = commands[0]
        private_values = (
            thread["ownerEmail"],
            invite["invitedEmail"],
            thread["workspaceId"],
            thread["mailboxId"],
            thread["collaborationId"],
            invite["inviteId"],
            thread["sourceRef"]["providerMessageId"],
            invite["tokenHash"],
            command[1],
            command[3],
            command[4],
            command[-1],
            "PrivateLoggerExceptionMarker",
        )
        for private_value in private_values:
            self.assertNotIn(private_value, lines)

    def test_strict_response_decoder_rejects_duplicate_keys_at_every_level(self):
        inner_responses = {
            "status": '{"status":"ok","status":"ok","record":{}}',
            "escaped_status": '{"status":"ok","\\u0073tatus":"ok","record":{}}',
            "error": '{"status":"ok","error":{},"error":{},"record":{}}',
            "record": '{"status":"ok","record":{},"record":{}}',
            "linkage": (
                '{"status":"ok","record":{"linkage":'
                '{"inviteId":"one","inviteId":"two"}}}'
            ),
            "invitation": (
                '{"status":"ok","record":{"invitation":'
                '{"status":"active","status":"revoked"}}}'
            ),
            "session": (
                '{"status":"ok","record":{"session":'
                '{"csrfTokenHash":"one","csrfTokenHash":"two"}}}'
            ),
            "deeply_nested": (
                '{"status":"ok","record":{"outer":{"inner":'
                '{"field":"one","field":"two"}}}}'
            ),
        }
        for field, raw in inner_responses.items():
            with self.subTest(inner_field=field):
                result = _v2_eval(
                    ["EVAL", "return", 0],
                    command_transport=lambda _command, value=raw: {"result": value},
                    response_shapes={"ok": {"record"}},
                )
                self.assertEqual(
                    result,
                    {"status": "unavailable", "error": {"code": "storage_protocol_error"}},
                )

        class Response:
            def __init__(self, raw: bytes):
                self.raw = raw

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return self.raw

        config = {"rest_url": "https://unused.invalid", "rest_token": "test"}
        for case, raw in {
            "outer_result": b'{"result":"one","result":"two"}',
            "nested_outer_result": b'{"result":{"field":"one","field":"two"}}',
            "malformed": b'{"result":',
        }.items():
            with self.subTest(outer_response=case), patch.object(
                redis_store, "urlopen", return_value=Response(raw)
            ):
                self.assertEqual(
                    _perform_v2_rest_command(config, ["GET", "unused"]),
                    {"status": "unavailable", "error": {"code": "storage_protocol_error"}},
                )

        propagated = _v2_eval(
            ["EVAL", "return", 0],
            command_transport=lambda _command: {
                "status": "unavailable",
                "error": {"code": "storage_protocol_error"},
            },
            response_shapes={"ok": set()},
        )
        self.assertEqual(
            propagated,
            {"status": "unavailable", "error": {"code": "storage_protocol_error"}},
        )

        malformed_outer_payloads = (
            None,
            [],
            {},
            {"result": None, "unexpected": True},
            {"result": None, "error": None},
            {"status": "ok", "result": None},
            {"status": "unavailable", "error": {"code": "unknown"}},
            {"status": "unavailable", "error": {"code": "storage_unavailable", "detail": "raw"}},
        )
        for payload in malformed_outer_payloads:
            with self.subTest(outer_shape=payload):
                self.assertEqual(
                    _v2_command(
                        ["GET", "unused"],
                        command_transport=lambda _command, value=payload: value,
                    ),
                    {"status": "unavailable", "error": {"code": "storage_protocol_error"}},
                )

        malformed_inner_payloads = (
            None,
            [],
            {},
            {"unexpected": True},
            {"status": "ok"},
            {"status": "ok", "record": {}, "unexpected": True},
        )
        for payload in malformed_inner_payloads:
            with self.subTest(inner_shape=payload):
                self.assertEqual(
                    _v2_eval(
                        ["EVAL", "return", 0],
                        command_transport=lambda _command, value=payload: {"result": value},
                        response_shapes={"ok": {"record"}},
                    ),
                    {"status": "unavailable", "error": {"code": "storage_protocol_error"}},
                )

    def test_source_index_load_revalidates_owner_mailbox_and_source(self):
        record = thread_record()
        calls = []

        def transport(command):
            calls.append(command)
            if len(calls) == 1:
                return {"result": json.dumps({"status": "found", "collaborationId": record["collaborationId"]})}
            return {"result": wire_json(record, "thread")}

        result = load_v2_thread_by_source(
            "owner@example.com", "mailbox-1", record["sourceRef"],
            workspace_id=record["workspaceId"],
            command_transport=transport,
        )
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("owner@example.com", calls[0][1])

        mismatched = {**record, "mailboxId": "mailbox-other"}
        mismatch_calls = []
        mismatch = load_v2_thread_by_source(
            "owner@example.com", "mailbox-1", record["sourceRef"],
            workspace_id=record["workspaceId"],
            command_transport=lambda command: (
                mismatch_calls.append(command)
                or {"result": json.dumps({"status": "found", "collaborationId": record["collaborationId"]}) if len(mismatch_calls) == 1 else wire_json(mismatched, "thread")}
            ),
        )
        self.assertEqual(mismatch["status"], "malformed")

    def test_invite_creation_sets_ttl_and_never_persists_raw_token(self):
        commands = []

        def create_transport(command):
            commands.append(command)
            if command[0] == "GET":
                return {"result": None}
            return {"result": json.dumps({"status": "created"})}

        result = create_v2_invite(
            invite_record(), now=100,
            command_transport=create_transport,
        )
        self.assertEqual(result["status"], "ok")
        command_text = repr(commands)
        self.assertIn("100", command_text)
        self.assertNotIn("a-raw-invite-token", command_text)
        self.assertIn("\"tokenHash\":\"" + "c" * 64, command_text)
        self.assertEqual([command[0] for command in commands], ["GET", "EVAL"])

        store = StatefulV2Store()
        first = create_v2_invite(invite_record(), now=100, command_transport=store)
        second_record = {**invite_record(), "inviteId": "D" * 22, "tokenHash": "d" * 64}

        def validated_store(command):
            if command[0] == "EVAL" and command[1] == _VALIDATE_V2_INVITE_GRAPH_LUA:
                return {
                    "result": json.dumps(
                        {
                            "status": "validated",
                            "invitation": wire_record(invite_record(), "invite"),
                            "linkage": invite_graph_linkage(invite_record()),
                        },
                        separators=(",", ":"),
                    )
                }
            return store(command)
        second = create_v2_invite(second_record, now=100, command_transport=validated_store)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(second["record"]["inviteId"], invite_record()["inviteId"])

        for case, validation_response in (
            (
                "extra_result_field",
                {
                    "status": "validated", "invitation": wire_record(invite_record(), "invite"),
                    "linkage": invite_graph_linkage(invite_record()), "unexpected": True,
                },
            ),
            (
                "extra_invitation_field",
                {
                    "status": "validated",
                    "invitation": {**wire_record(invite_record(), "invite"), "unexpected": True},
                    "linkage": invite_graph_linkage(invite_record()),
                },
            ),
            (
                "extra_linkage_field",
                {
                    "status": "validated",
                    "invitation": wire_record(invite_record(), "invite"),
                    "linkage": {
                        **invite_graph_linkage(invite_record()),
                        "unexpected": True,
                    },
                },
            ),
            ("unknown_status", {"status": "surprise"}),
        ):
            with self.subTest(validation_shape=case):
                def malformed_validation_transport(command):
                    if command[0] == "GET":
                        return {
                            "result": wire_json(invite_record(), "invite")
                        }
                    if command[1] == _CREATE_V2_INVITE_LUA:
                        return {
                            "result": json.dumps(
                                {"status": "duplicate", "inviteId": invite_record()["inviteId"]}
                            )
                        }
                    return {"result": json.dumps(validation_response)}

                rejected = create_v2_invite(
                    second_record,
                    now=100,
                    command_transport=malformed_validation_transport,
                )
                self.assertEqual(
                    rejected, {"status": "malformed", "error": {"code": "storage_protocol_error"}}
                )

    def test_token_lookup_hashes_raw_token_and_validates_record(self):
        raw_token = "secret-token"
        record = invite_record()
        record["tokenHash"] = hash_v2_secret(raw_token)
        commands = []

        def transport(command):
            commands.append(command)
            if ":invite-token:" in command[1]:
                return {"result": record["inviteId"]}
            return {"result": wire_json(record, "invite")}

        result = load_v2_invite_by_token(raw_token, now=101, command_transport=transport)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn(raw_token, repr(commands))
        self.assertIn(hash_v2_secret(raw_token), commands[0][1])

    def test_atomic_exchange_fails_closed_when_eval_is_unavailable(self):
        record = invite_record()
        raw_token = "secret-token"
        record["tokenHash"] = hash_v2_secret(raw_token)

        def transport(command):
            if command[0] == "GET":
                return {"result": wire_json(record, "invite")}
            return {"error": "EVAL unsupported: raw provider detail"}

        session = {
            "v": 2, "sessionHash": hash_v2_secret("session"), "csrfTokenHash": hash_v2_secret("csrf"),
            "inviteId": record["inviteId"], "ownerEmail": record["ownerEmail"],
            "workspaceId": record["workspaceId"], "mailboxId": record["mailboxId"],
            "collaborationId": record["collaborationId"], "allowedActions": ["read", "reply"],
            "visibility": "shared_only", "identityAssurance": "link_possession",
            "guestDisplayName": "Guest", "createdAt": 101, "lastUsedAt": 101,
            "expiresAt": 150, "status": "active", "revokedAt": None, "loggedOutAt": None,
        }
        result = atomic_exchange_v2_invite(
            raw_token=raw_token, invite_id=record["inviteId"], session_record=session,
            now=101, session_ttl=49, command_transport=transport,
        )
        self.assertEqual(result["error"], {"code": "atomic_exchange_unavailable"})
        self.assertNotIn("provider", repr(result))

    def test_stateful_exchange_is_single_use_and_replay_safe(self):
        store = StatefulV2Store()
        invite = invite_record()
        raw_token = "t" * 43
        invite["tokenHash"] = hash_v2_secret(raw_token)
        self.assertEqual(create_v2_invite(invite, now=100, command_transport=store)["status"], "ok")
        session = {
            "v": 2, "sessionHash": hash_v2_secret("s" * 43), "csrfTokenHash": hash_v2_secret("c" * 43),
            "inviteId": invite["inviteId"], "ownerEmail": invite["ownerEmail"], "workspaceId": invite["workspaceId"],
            "mailboxId": invite["mailboxId"], "collaborationId": invite["collaborationId"],
            "allowedActions": ["read", "reply"], "visibility": "shared_only", "identityAssurance": "link_possession",
            "guestDisplayName": "Guest", "createdAt": 101, "lastUsedAt": 101, "expiresAt": 150,
            "status": "active", "revokedAt": None, "loggedOutAt": None,
        }
        first = atomic_exchange_v2_invite(raw_token=raw_token, invite_id=invite["inviteId"], session_record=session, now=101, session_ttl=49, command_transport=store)
        second = atomic_exchange_v2_invite(raw_token=raw_token, invite_id=invite["inviteId"], session_record={**session, "sessionHash": hash_v2_secret("z" * 43)}, now=101, session_ttl=49, command_transport=store)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["error"]["code"], "invite_already_exchanged")
        csrf_first = update_v2_guest_session(session, normalizer=normalize_v2_guest_session_record, now=102, csrf_token_hash=hash_v2_secret("n" * 43), touch_last_used=False, command_transport=store)
        csrf_second = update_v2_guest_session(session, normalizer=normalize_v2_guest_session_record, now=102, csrf_token_hash=hash_v2_secret("m" * 43), touch_last_used=False, command_transport=store)
        self.assertEqual(csrf_first["status"], "updated")
        self.assertEqual(csrf_second["status"], "stale")
        logout_first = revoke_v2_guest_session("s" * 43, now=120, command_transport=store)
        logout_second = revoke_v2_guest_session("s" * 43, now=121, command_transport=store)
        self.assertEqual(logout_first["status"], "ok")
        self.assertEqual(logout_second["error"]["code"], "already_logged_out")
        eval_command = next(command for command in store.commands if command[0] == "EVAL" and command[1] == _EXCHANGE_V2_INVITE_LUA)
        self.assertNotIn(raw_token, repr(eval_command))
        self.assertNotIn("s" * 43, repr(eval_command))
        self.assertNotIn("c" * 43, repr(eval_command))
        for required in ("invite.tokenHash", "session.csrfTokenHash", "integerValue(session.expiresAt) > integerValue(invite.expiresAt)", "invite.activeSessionHash"):
            self.assertIn(required, _EXCHANGE_V2_INVITE_LUA)

    def test_stateful_cas_advances_once_and_rejects_scope_or_time_reuse(self):
        store = StatefulV2Store()
        thread = thread_record()
        self.assertEqual(create_v2_thread(thread, command_transport=store)["status"], "ok")
        advanced = {
            **thread,
            "updatedAt": 101,
            "messages": [{
                "id": "M" * 22, "authorKind": "owner", "authorDisplayName": "Owner",
                "text": "message", "visibility": "internal", "createdAt": 101,
            }],
        }
        self.assertEqual(save_v2_thread_if_expected(advanced, 100, command_transport=store)["status"], "ok")
        self.assertEqual(save_v2_thread_if_expected({**advanced, "updatedAt": 102}, 100, command_transport=store)["error"]["code"], "stale_thread")
        self.assertEqual(save_v2_thread_if_expected(advanced, 101, command_transport=store)["error"]["code"], "stale_thread")
        wrong_scope = {**advanced, "mailboxId": "mailbox-other", "updatedAt": 102}
        self.assertEqual(save_v2_thread_if_expected(wrong_scope, 101, command_transport=store)["error"]["code"], "storage_protocol_error")
        for required in ("integerValue(replacement.updatedAt) <= integerValue(current.updatedAt)", "current.ownerEmail ~= replacement.ownerEmail", "sourceEqual", "source_pointer_conflict", "#replacement.messages ~= #current.messages + 1"):
            self.assertIn(required, _SAVE_V2_THREAD_CAS_LUA)

    def test_stateful_duplicate_revalidates_source_scope_and_result_shapes(self):
        store = StatefulV2Store()
        thread = thread_record()
        self.assertTrue(create_v2_thread(thread, command_transport=store)["created"])
        self.assertFalse(create_v2_thread(thread, command_transport=store)["created"])
        source_key = build_v2_source_thread_key(thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"])
        store.values[source_key] = "Z" * 22
        injected = create_v2_thread(thread, command_transport=store)
        self.assertEqual(injected["error"]["code"], "stale_thread")
        store.values[source_key] = thread["collaborationId"]
        thread_key = next(key for key in store.values if ":thread:" in key and ":source-thread:" not in key)
        for field, value in (("ownerEmail", "other@example.com"), ("mailboxId", "mailbox-other"), ("sourceRef", {"provider": "google", "providerMessageId": "other"})):
            mismatched = thread_record()
            mismatched[field] = value
            if field == "ownerEmail":
                mismatched["workspaceId"] = value
            store.put_json(thread_key, mismatched)
            self.assertEqual(create_v2_thread(thread, command_transport=store)["error"]["code"], "source_pointer_conflict")
        store.put_json(thread_key, thread)
        malformed = create_v2_thread(thread, command_transport=lambda _command: {"result": json.dumps({"status": "created", "extra": "raw"})})
        self.assertEqual(malformed["error"]["code"], "storage_protocol_error")

    def test_non_authoritative_stale_pointer_cases_match_the_production_contract(self):
        thread = thread_record()
        source_key = build_v2_source_thread_key(
            thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"]
        )

        orphan_store = StatefulV2Store()
        orphan_store.values[source_key] = "Z" * 22
        repaired = create_v2_thread(thread, command_transport=orphan_store)
        self.assertTrue(repaired["created"])
        self.assertEqual(orphan_store.values[source_key], thread["collaborationId"])

        no_ttl_store = StatefulV2Store()
        target = {**thread, "collaborationId": "Z" * 22}
        target_key = build_v2_thread_key("Z" * 22)
        no_ttl_store.values[source_key] = "Z" * 22
        no_ttl_store.put_json(target_key, target)
        conflict = create_v2_thread(thread, command_transport=no_ttl_store)
        self.assertEqual(conflict["error"]["code"], "source_pointer_conflict")

        malformed_store = StatefulV2Store()
        malformed_store.values[source_key] = "Z" * 22
        malformed_store.values[target_key] = "not-json"
        malformed_store.ttls[source_key] = malformed_store.ttls[target_key] = 100
        malformed = create_v2_thread(thread, command_transport=malformed_store)
        self.assertEqual(malformed["error"]["code"], "source_pointer_conflict")

    def test_stateful_revocation_and_logout_are_idempotent(self):
        store = StatefulV2Store()
        invite = invite_record()
        create_v2_invite(invite, now=100, command_transport=store)
        first = revoke_v2_invite(invite["inviteId"], owner_email=invite["ownerEmail"], revoked_by=invite["ownerEmail"], now=110, command_transport=store)
        second = revoke_v2_invite(invite["inviteId"], owner_email=invite["ownerEmail"], revoked_by=invite["ownerEmail"], now=120, command_transport=store)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["error"]["code"], "already_revoked")
        stored = store.get_json(next(key for key in store.values if ":invite:" in key))
        self.assertEqual((stored["revokedAt"], stored["revokedBy"]), (SEC + 110, invite["ownerEmail"]))
        self.assertLess(
            _REVOKE_V2_INVITE_LUA.index("sessionOk, session = decodeWire"),
            _REVOKE_V2_INVITE_LUA.index("invite.status = 'revoked'"),
        )

    def test_revocation_binds_full_scope_and_never_partially_writes_bad_session(self):
        store = StatefulV2Store()
        invite = invite_record()
        raw_token = "r" * 43
        invite["tokenHash"] = hash_v2_secret(raw_token)
        create_v2_invite(invite, now=100, command_transport=store)
        for overrides in (
            {"workspace_id": OTHER_WORKSPACE_ID},
            {"mailbox_id": "mailbox-other"},
            {"collaboration_id": "Z" * 22},
        ):
            arguments = {
                "owner_email": invite["ownerEmail"],
                "workspace_id": invite["workspaceId"],
                "mailbox_id": invite["mailboxId"],
                "collaboration_id": invite["collaborationId"],
                "revoked_by": invite["ownerEmail"],
                "now": SEC + 101,
                "command_transport": store,
                **overrides,
            }
            denied = _revoke_v2_invite(invite["inviteId"], **arguments)
            self.assertNotEqual(denied["status"], "ok")

        session = {
            "v": 2, "sessionHash": hash_v2_secret("s" * 43), "csrfTokenHash": hash_v2_secret("c" * 43),
            "inviteId": invite["inviteId"], "ownerEmail": invite["ownerEmail"], "workspaceId": invite["workspaceId"],
            "mailboxId": invite["mailboxId"], "collaborationId": invite["collaborationId"],
            "allowedActions": ["read", "reply"], "visibility": "shared_only", "identityAssurance": "link_possession",
            "guestDisplayName": "Guest", "createdAt": 101, "lastUsedAt": 101, "expiresAt": 150,
            "status": "active", "revokedAt": None, "loggedOutAt": None,
        }
        exchanged = atomic_exchange_v2_invite(
            raw_token=raw_token, invite_id=invite["inviteId"], session_record=session,
            now=101, session_ttl=49, command_transport=store,
        )
        self.assertEqual(exchanged["status"], "ok")
        session_key = build_v2_guest_session_key(hash_v2_secret("s" * 43))
        canonical_session = store.get_json(session_key)
        for field, value in (
            ("allowedActions", ["read"]),
            ("visibility", "internal"),
            ("expiresAt", canonical_session["createdAt"] + 28_801),
        ):
            malformed = {**canonical_session, field: value}
            store.put_json(session_key, malformed)
            denied = _revoke_v2_invite(
                invite["inviteId"], owner_email=invite["ownerEmail"], workspace_id=invite["workspaceId"],
                mailbox_id=invite["mailboxId"], collaboration_id=invite["collaborationId"],
                revoked_by=invite["ownerEmail"], now=SEC + 102, command_transport=store,
            )
            self.assertEqual(denied["error"]["code"], "storage_protocol_error")
            self.assertEqual(store.get_json(build_v2_invite_key(invite["inviteId"]))["status"], "exchanged")
            store.put_json(session_key, canonical_session)

    def test_stateful_revocation_wins_exchange_race_order(self):
        store = StatefulV2Store()
        invite = invite_record()
        raw_token = "r" * 43
        invite["tokenHash"] = hash_v2_secret(raw_token)
        create_v2_invite(invite, now=100, command_transport=store)
        revoke_v2_invite(invite["inviteId"], owner_email=invite["ownerEmail"], revoked_by=invite["ownerEmail"], now=101, command_transport=store)
        session = {
            "v": 2, "sessionHash": hash_v2_secret("q" * 43), "csrfTokenHash": hash_v2_secret("p" * 43),
            "inviteId": invite["inviteId"], "ownerEmail": invite["ownerEmail"], "workspaceId": invite["workspaceId"],
            "mailboxId": invite["mailboxId"], "collaborationId": invite["collaborationId"],
            "allowedActions": ["read", "reply"], "visibility": "shared_only", "identityAssurance": "link_possession",
            "guestDisplayName": "Guest", "createdAt": 102, "lastUsedAt": 102, "expiresAt": 150,
            "status": "active", "revokedAt": None, "loggedOutAt": None,
        }
        result = atomic_exchange_v2_invite(raw_token=raw_token, invite_id=invite["inviteId"], session_record=session, now=102, session_ttl=48, command_transport=store)
        self.assertEqual(result["error"]["code"], "invite_revoked")

        store = StatefulV2Store()
        create_v2_invite(invite, now=100, command_transport=store)
        session["createdAt"] = session["lastUsedAt"] = 101
        self.assertEqual(atomic_exchange_v2_invite(raw_token=raw_token, invite_id=invite["inviteId"], session_record=session, now=101, session_ttl=49, command_transport=store)["status"], "ok")
        self.assertEqual(revoke_v2_invite(invite["inviteId"], owner_email=invite["ownerEmail"], revoked_by=invite["ownerEmail"], now=102, command_transport=store)["status"], "ok")
        session_key = build_v2_guest_session_key(session["sessionHash"])
        self.assertEqual(store.get_json(session_key)["status"], "revoked")

    def test_competing_exchange_winner_is_determined_only_by_order(self):
        for first_secret, second_secret in (("x" * 43, "y" * 43), ("y" * 43, "x" * 43)):
            store = StatefulV2Store()
            invite = invite_record()
            raw_token = "o" * 43
            invite["tokenHash"] = hash_v2_secret(raw_token)
            create_v2_invite(invite, now=100, command_transport=store)
            def session(secret):
                return {
                    "v": 2, "sessionHash": hash_v2_secret(secret), "csrfTokenHash": hash_v2_secret(secret[::-1]),
                    "inviteId": invite["inviteId"], "ownerEmail": invite["ownerEmail"], "workspaceId": invite["workspaceId"],
                    "mailboxId": invite["mailboxId"], "collaborationId": invite["collaborationId"],
                    "allowedActions": ["read", "reply"], "visibility": "shared_only", "identityAssurance": "link_possession",
                    "guestDisplayName": "Guest", "createdAt": 101, "lastUsedAt": 101, "expiresAt": 150,
                    "status": "active", "revokedAt": None, "loggedOutAt": None,
                }
            first = atomic_exchange_v2_invite(raw_token=raw_token, invite_id=invite["inviteId"], session_record=session(first_secret), now=101, session_ttl=49, command_transport=store)
            second = atomic_exchange_v2_invite(raw_token=raw_token, invite_id=invite["inviteId"], session_record=session(second_secret), now=101, session_ttl=49, command_transport=store)
            self.assertEqual(first["status"], "ok")
            self.assertEqual(second["error"]["code"], "invite_already_exchanged")

    def test_stateful_exchange_rejects_session_beyond_invite_and_corruption(self):
        store = StatefulV2Store()
        invite = invite_record()
        raw_token = "u" * 43
        invite["tokenHash"] = hash_v2_secret(raw_token)
        create_v2_invite(invite, now=100, command_transport=store)
        session = {
            "v": 2, "sessionHash": hash_v2_secret("v" * 43), "csrfTokenHash": hash_v2_secret("w" * 43),
            "inviteId": invite["inviteId"], "ownerEmail": invite["ownerEmail"], "workspaceId": invite["workspaceId"],
            "mailboxId": invite["mailboxId"], "collaborationId": invite["collaborationId"],
            "allowedActions": ["read", "reply"], "visibility": "shared_only", "identityAssurance": "link_possession",
            "guestDisplayName": "Guest", "createdAt": 101, "lastUsedAt": 101, "expiresAt": 201,
            "status": "active", "revokedAt": None, "loggedOutAt": None,
        }
        result = atomic_exchange_v2_invite(raw_token=raw_token, invite_id=invite["inviteId"], session_record=session, now=101, session_ttl=100, command_transport=store)
        self.assertEqual(result["error"]["code"], "storage_protocol_error")


if __name__ == "__main__":
    unittest.main()
