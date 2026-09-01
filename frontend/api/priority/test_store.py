from __future__ import annotations

import base64
import inspect
import json
import unittest
from dataclasses import replace
from pathlib import Path

from . import store as store_module
from .semantic_types import (
    CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    SemanticAssessment,
    SemanticReasonCode,
    SemanticState,
    semantic_schema_version_for_provider,
)
from .store import (
    ATTEMPT_WINDOW_SECONDS,
    CUSTOM_IMAP_COMPATIBILITY_MAX_SERIALIZED_BYTES,
    CUSTOM_IMAP_COMPATIBILITY_TTL_SECONDS,
    CUSTOM_IMAP_V2_KEY_PREFIX,
    LEASE_TTL_SECONDS,
    NEGATIVE_TTL_SECONDS,
    NEW_INBOUND_DISMISSAL_READ_BATCH_SIZE,
    NEW_INBOUND_DISMISSAL_TTL_SECONDS,
    NEW_INBOUND_INDEX_MAX_CONVERSATION_ID_CHARACTERS,
    NEW_INBOUND_INDEX_MAX_RECORDS,
    NEW_INBOUND_INDEX_MAX_SERIALIZED_RECORD_BYTES,
    NEW_INBOUND_INDEX_MAX_TURN_ID_CHARACTERS,
    NEW_INBOUND_INDEX_READ_BATCH_SIZE,
    NEW_INBOUND_INDEX_TTL_SECONDS,
    RESULT_TTL_SECONDS,
    SEMANTIC_HYDRATION_RESULT_BATCH_SIZE,
    SEMANTIC_STORE_MODE_CUSTOM_IMAP_V2,
    CustomImapCompatibilityOutcome,
    CustomImapDismissalBridgeOutcome,
    CustomImapV1CompatibilityLocator,
    CustomImapV2DismissalAuthority,
    NewInboundIndexEntry,
    NewInboundIndexScope,
    SemanticAssessmentStore,
    SemanticCacheScope,
    SemanticStoreUnavailable,
    derive_custom_imap_compatibility_locator_digest,
    derive_new_inbound_dismissal_digest,
    derive_new_inbound_index_scope_digest,
)


def _account(prefix: str, byte: int) -> str:
    suffix = base64.urlsafe_b64encode(bytes([byte]) * 16).rstrip(b"=").decode("ascii")
    return prefix + suffix


SECRET = "priority-test-secret-with-more-than-thirty-two-bytes"


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.expirations: dict[str, int] = {}
        self.commands: list[list[object]] = []
        self.lua_type_replies_as_status_tables = True

    def _remove_index_member(self, keys: list[object], member: str) -> None:
        self.hashes.setdefault(keys[3], {}).pop(member, None)
        self.sorted_sets.setdefault(keys[4], {}).pop(member, None)
        self.sorted_sets.setdefault(keys[5], {}).pop(member, None)

    def _lua_type(self, key: object) -> object:
        kinds: list[str] = []
        if key in self.values:
            kinds.append("string")
        if key in self.hashes:
            kinds.append("hash")
        if key in self.sorted_sets:
            kinds.append("zset")
        value = kinds[0] if len(kinds) == 1 else ("none" if not kinds else "mixed")
        return {"ok": value} if self.lua_type_replies_as_status_tables else value

    def __call__(self, command: list[object]) -> dict[str, object]:
        self.commands.append(list(command))
        operation = command[0]
        if operation == "GET":
            return {"result": self.values.get(command[1])}
        if operation == "MGET":
            return {"result": [self.values.get(key) for key in command[1:]]}
        if operation == "HMGET":
            values = self.hashes.get(command[1], {})
            return {"result": [values.get(member) for member in command[2:]]}
        if operation == "ZSCORE":
            return {
                "result": self.sorted_sets.get(command[1], {}).get(command[2])
            }
        if operation == "ZREVRANGE":
            values = self.sorted_sets.get(command[1], {})
            ordered = sorted(
                values,
                key=lambda member: (values[member], member),
                reverse=True,
            )
            return {
                "result": ordered[int(command[2]) : int(command[3]) + 1]
            }
        if operation == "SET":
            key = command[1]
            if command[-1] == "NX" and key in self.values:
                return {"result": None}
            self.values[key] = command[2]
            return {"result": "OK"}
        if operation == "EVAL":
            script = command[1]
            key_count = command[2]
            keys = command[3 : 3 + key_count]
            args = command[3 + key_count :]
            if script in {
                store_module._READ_NEW_INBOUND_DISMISSALS_SCRIPT,
                store_module._READ_CUSTOM_IMAP_V2_DISMISSALS_SCRIPT,
            }:
                states: list[int] = []
                allowed_values = set(args)
                for key in keys:
                    key_type = self._lua_type(key)
                    actual_type = (
                        key_type.get("ok")
                        if type(key_type) is dict
                        else key_type
                    )
                    if actual_type == "none":
                        states.append(0)
                    elif (
                        actual_type == "string"
                        and self.values[key] in allowed_values
                        and (
                            script == store_module._READ_NEW_INBOUND_DISMISSALS_SCRIPT
                            or self.expirations.get(key, 1) > 0
                        )
                    ):
                        states.append(1)
                    else:
                        return {"result": [-1]}
                return {"result": states}
            if script == store_module._ATTEMPT_SCRIPT:
                current = self.values.get(keys[0])
                if current is None:
                    self.values[keys[0]] = "1"
                    return {"result": 1}
                if int(current) >= int(args[1]):
                    return {"result": 0}
                self.values[keys[0]] = str(int(current) + 1)
                return {"result": int(self.values[keys[0]])}
            if script == store_module._MARK_CURRENT_SCRIPT:
                current = self.values.get(keys[0])
                new_time = int(args[0])
                new_value = args[1]
                if current is not None:
                    old_time = int(current.split(":", 1)[0])
                    if old_time > new_time or (
                        old_time == new_time and current != new_value
                    ):
                        return {"result": 0}
                self.values[keys[0]] = new_value
                return {"result": 1}
            if script == store_module._COMMIT_RESULT_SCRIPT:
                if self.values.get(keys[0]) == args[0] and self.values.get(keys[1]) == args[1]:
                    self.values[keys[2]] = args[2]
                    self.values.pop(keys[0], None)
                    return {"result": 1}
                return {"result": 0}
            if script in {
                store_module._COMMIT_NEW_INBOUND_RESULT_SCRIPT,
                store_module._COMMIT_CUSTOM_IMAP_V2_NEW_INBOUND_RESULT_SCRIPT,
            }:
                if (
                    self.values.get(keys[0]) != args[0]
                    or self.values.get(keys[1]) != args[1]
                ):
                    return {"result": 0}
                expected_types = (
                    (keys[3], "hash"),
                    (keys[4], "zset"),
                    (keys[5], "zset"),
                    (keys[6], "string"),
                )
                for key, expected_type in expected_types:
                    type_reply = self._lua_type(key)
                    actual_type = (
                        type_reply.get("ok")
                        if type(type_reply) is dict
                        else type_reply
                    )
                    if actual_type not in {"none", expected_type}:
                        return {"result": -1}
                dismissal = self.values.get(keys[6])
                allowed_dismissals = (
                    {args[11], args[12]}
                    if script
                    == store_module._COMMIT_CUSTOM_IMAP_V2_NEW_INBOUND_RESULT_SCRIPT
                    else {args[11]}
                )
                if dismissal is not None and dismissal not in allowed_dismissals:
                    return {"result": -1}
                if (
                    dismissal is not None
                    and script
                    == store_module._COMMIT_CUSTOM_IMAP_V2_NEW_INBOUND_RESULT_SCRIPT
                    and self.expirations.get(keys[6], 1) <= 0
                ):
                    return {"result": -1}
                member = args[4]
                occurred_at = float(args[6])
                occurrences = self.sorted_sets.setdefault(keys[4], {})
                existing_occurrence = occurrences.get(member)
                existing_record = self.hashes.setdefault(keys[3], {}).get(member)
                if (existing_occurrence is None) != (existing_record is None):
                    return {"result": -1}
                self.hashes.setdefault(keys[3], {})[member] = args[5]
                occurrences[member] = occurred_at
                freshness = self.sorted_sets.setdefault(keys[5], {})
                freshness[member] = float(args[7])
                for expired_member, score in list(freshness.items()):
                    if score <= float(args[10]):
                        self._remove_index_member(keys, expired_member)
                ordered = sorted(
                    self.sorted_sets.setdefault(keys[5], {}),
                    key=lambda current: (
                        self.sorted_sets[keys[5]][current],
                        current,
                    ),
                )
                excess = len(ordered) - int(args[9])
                for oldest_member in ordered[: max(0, excess)]:
                    self._remove_index_member(keys, oldest_member)
                self.values[keys[2]] = args[2]
                if dismissal is not None and (
                    script == store_module._COMMIT_NEW_INBOUND_RESULT_SCRIPT
                    or dismissal == args[11]
                ):
                    self.expirations[keys[6]] = int(args[8])
                self.values.pop(keys[0], None)
                return {"result": 1}
            if script == store_module._COMMIT_NEGATIVE_SCRIPT:
                if self.values.get(keys[0]) == args[0]:
                    self.values[keys[1]] = args[1]
                    self.values.pop(keys[0], None)
                    return {"result": 1}
                return {"result": 0}
            if script == store_module._GET_RESULT_IF_CURRENT_SCRIPT:
                if self.values.get(keys[0]) != args[0]:
                    return {"result": args[1]}
                return {"result": self.values.get(keys[1])}
            if script in {
                store_module._DISMISS_NEW_INBOUND_SCRIPT,
                store_module._DISMISS_CUSTOM_IMAP_V2_NEW_INBOUND_SCRIPT,
            }:
                v2_script = (
                    script == store_module._DISMISS_CUSTOM_IMAP_V2_NEW_INBOUND_SCRIPT
                )
                result_arg = 4 if v2_script else 3
                ttl_arg = 5 if v2_script else 4
                occurrence_arg = 6 if v2_script else 5
                freshness_arg = 7 if v2_script else 6
                if (
                    self.hashes.get(keys[0], {}).get(args[0]) != args[1]
                    or self.sorted_sets.get(keys[1], {}).get(args[0])
                    != float(args[occurrence_arg])
                    or self.sorted_sets.get(keys[2], {}).get(args[0])
                    != float(args[freshness_arg])
                    or self.values.get(keys[3]) != args[result_arg]
                ):
                    return {"result": 0}
                dismissal_type = self._lua_type(keys[4])
                actual_dismissal_type = (
                    dismissal_type.get("ok")
                    if type(dismissal_type) is dict
                    else dismissal_type
                )
                if actual_dismissal_type not in {"none", "string"}:
                    return {"result": -1}
                existing = self.values.get(keys[4])
                allowed_existing = (
                    {args[2], args[3]} if v2_script else {args[2]}
                )
                if existing is not None and existing not in allowed_existing:
                    return {"result": -1}
                if (
                    v2_script
                    and existing is not None
                    and self.expirations.get(keys[4], 1) <= 0
                ):
                    return {"result": -1}
                self.values[keys[4]] = args[2]
                self.expirations[keys[4]] = int(args[ttl_arg])
                return {"result": 1}
            if script == store_module._RELEASE_LEASE_SCRIPT:
                if self.values.get(keys[0]) == args[0]:
                    self.values.pop(keys[0], None)
                    return {"result": 1}
                return {"result": 0}
        raise AssertionError(command)


def scope(latest: str = "message-1") -> SemanticCacheScope:
    return SemanticCacheScope(
        workspace_id=_account("wsp_", 1),
        user_id=_account("usr_", 2),
        mailbox_id="mailbox-1",
        provider="google",
        conversation_id="thread:mailbox-1|gmail:mailbox-1:thread-1",
        latest_turn_id=latest,
        semantic_version="priority-semantic-state-v1",
        model_version="test-model",
    )


ASSESSMENT = SemanticAssessment(
    state=SemanticState.RESOLVED,
    confidence=0.98,
    reason_code=SemanticReasonCode.COMPLETED_CONFIRMATION,
)

ACTIONABLE_ASSESSMENT = SemanticAssessment(
    state=SemanticState.NEEDS_USER_ACTION,
    confidence=0.99,
    reason_code=SemanticReasonCode.EXPLICIT_REQUEST,
)


def index_scope(
    *,
    workspace_id: str | None = None,
    user_id: str | None = None,
    mailbox_id: str = "mailbox-1",
    provider: str = "google",
    mailbox_account_identity: str = "primary@example.com",
) -> NewInboundIndexScope:
    current = scope()
    return NewInboundIndexScope(
        workspace_id=workspace_id or current.workspace_id,
        user_id=user_id or current.user_id,
        mailbox_id=mailbox_id,
        provider=provider,
        mailbox_account_identity=mailbox_account_identity,
    )


def custom_imap_scope(
    *,
    conversation_id: str = "imap:v2:rfc:conversation-1",
    latest_turn_id: str = "turn-1",
    semantic_version: str = CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
) -> SemanticCacheScope:
    current = scope(latest_turn_id)
    return replace(
        current,
        provider="custom_imap",
        conversation_id=conversation_id,
        semantic_version=semantic_version,
    )


def compatibility_locator(**changes: str) -> CustomImapV1CompatibilityLocator:
    values = {
        "workspace_id": _account("wsp_", 1),
        "user_id": _account("usr_", 2),
        "mailbox_id": "mailbox-1",
        "mailbox_account_identity": "primary@example.com",
        "provider": "custom_imap",
        "provider_folder": "INBOX",
        "uid_validity": "7",
        "imap_uid": "11",
    }
    values.update(changes)
    return CustomImapV1CompatibilityLocator(**values)


def compatibility_scope(**changes: str) -> SemanticCacheScope:
    values = {
        "workspace_id": _account("wsp_", 1),
        "user_id": _account("usr_", 2),
        "mailbox_id": "mailbox-1",
        "provider": "custom_imap",
        "conversation_id": "thread:mailbox-1|imap:rfc:mailbox-1:root",
        "latest_turn_id": "turn-1",
        "semantic_version": SEMANTIC_SCHEMA_VERSION,
        "model_version": "test-model",
    }
    values.update(changes)
    return SemanticCacheScope(**values)


def v2_dismissal_authority(**changes: str) -> CustomImapV2DismissalAuthority:
    locator = compatibility_locator()
    values = {
        "workspace_id": locator.workspace_id,
        "user_id": locator.user_id,
        "mailbox_id": locator.mailbox_id,
        "mailbox_account_identity": locator.mailbox_account_identity,
        "provider": "custom_imap",
        "conversation_id": "imap:v2:rfc:conversation-1",
        "latest_turn_id": "turn-v2-1",
    }
    values.update(changes)
    return CustomImapV2DismissalAuthority(**values)


class SemanticStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = MemoryRedis()
        self.store = SemanticAssessmentStore(self.redis, hmac_secret=SECRET)

    def test_lease_attempt_cap_atomic_commit_and_strict_cached_record(self):
        cache_scope = scope()
        self.assertTrue(self.store.mark_current_if_newer(cache_scope, occurred_at=10_000))
        lease = self.store.try_acquire_lease(
            cache_scope,
            random_bytes=lambda length: bytes([7]) * length,
        )
        self.assertIsNotNone(lease)
        self.assertIsNone(self.store.try_acquire_lease(cache_scope))
        self.assertTrue(self.store.consume_attempt(cache_scope))
        self.assertTrue(self.store.commit_result_if_lease_owned(
            cache_scope,
            lease_token=lease,
            assessment=ASSESSMENT,
            input_hash="a" * 64,
            occurred_at=10_000,
            assessed_at=20,
        ))

        cached = self.store.get_result(cache_scope, input_hash="a" * 64)
        self.assertEqual(cached.assessment, ASSESSMENT)
        self.assertEqual(cached.effective_state, SemanticState.RESOLVED)
        self.assertEqual(cached.assessed_at, 20)
        stored_payload = next(
            value for key, value in self.redis.values.items() if ":result:" in key
        )
        self.assertNotIn("thread-1", stored_payload)
        self.assertNotIn("message-1", stored_payload)
        self.assertIn("conversationDigest", stored_payload)
        self.assertIn("latestTurnDigest", stored_payload)
        self.assertIn("inputHash", stored_payload)
        self.assertNotIn("semanticMode", stored_payload)
        self.assertNotIn("priorityEffect", stored_payload)

    def test_max_two_attempts_per_24h_scope(self):
        cache_scope = scope()
        self.assertTrue(self.store.consume_attempt(cache_scope))
        self.assertTrue(self.store.consume_attempt(cache_scope))
        self.assertFalse(self.store.consume_attempt(cache_scope))
        eval_commands = [command for command in self.redis.commands if command[0] == "EVAL"]
        self.assertEqual(eval_commands[0][-2:], [store_module.ATTEMPT_WINDOW_SECONDS, 2])

    def test_newer_pointer_rejects_stale_commit_after_model_call(self):
        older = scope("message-old")
        newer = scope("message-new")
        self.assertTrue(self.store.mark_current_if_newer(older, occurred_at=10_000))
        lease = self.store.try_acquire_lease(
            older,
            random_bytes=lambda length: bytes([8]) * length,
        )
        self.assertTrue(self.store.mark_current_if_newer(newer, occurred_at=11_000))
        self.assertFalse(self.store.commit_result_if_lease_owned(
            older,
            lease_token=lease,
            assessment=ASSESSMENT,
            input_hash="b" * 64,
            occurred_at=10_000,
            assessed_at=21,
        ))
        self.assertIsNone(self.store.get_result(older, input_hash="b" * 64))

    def test_low_confidence_effective_state_is_persisted_as_uncertain(self):
        assessment = SemanticAssessment(
            state=SemanticState.RESOLVED,
            confidence=0.96,
            reason_code=SemanticReasonCode.COMPLETED_CONFIRMATION,
        )
        cache_scope = scope()
        self.store.mark_current_if_newer(cache_scope, occurred_at=10_000)
        lease = self.store.try_acquire_lease(
            cache_scope,
            random_bytes=lambda length: bytes([9]) * length,
        )
        self.assertTrue(self.store.commit_result_if_lease_owned(
            cache_scope,
            lease_token=lease,
            assessment=assessment,
            input_hash="c" * 64,
            occurred_at=10_000,
            assessed_at=22,
        ))
        cached = self.store.get_result(cache_scope, input_hash="c" * 64)
        self.assertEqual(cached.effective_state, SemanticState.UNCERTAIN)

    def test_cached_result_is_returned_only_if_exact_turn_is_atomically_current(self):
        older = scope("message-old")
        newer = scope("message-new")
        self.store.mark_current_if_newer(older, occurred_at=10_000)
        lease = self.store.try_acquire_lease(
            older,
            random_bytes=lambda length: bytes([10]) * length,
        )
        self.assertTrue(self.store.commit_result_if_lease_owned(
            older,
            lease_token=lease,
            assessment=ASSESSMENT,
            input_hash="d" * 64,
            occurred_at=10_000,
            assessed_at=23,
        ))
        self.store.mark_current_if_newer(newer, occurred_at=11_000)

        is_current, cached = self.store.get_result_if_current(
            older,
            input_hash="d" * 64,
            occurred_at=10_000,
        )
        self.assertFalse(is_current)
        self.assertIsNone(cached)

    def test_exact_scope_lookup_is_one_result_get_with_no_cache_mutation(self):
        cache_scope = scope("lookup-message")
        self.store.mark_current_if_newer(cache_scope, occurred_at=10_000)
        lease = self.store.try_acquire_lease(
            cache_scope,
            random_bytes=lambda length: bytes([14]) * length,
        )
        self.assertTrue(self.store.commit_result_if_lease_owned(
            cache_scope,
            lease_token=lease,
            assessment=ASSESSMENT,
            input_hash="f" * 64,
            occurred_at=10_000,
            assessed_at=25,
        ))

        self.redis.commands.clear()
        cached = self.store.get_result_for_exact_scope(cache_scope)
        self.assertEqual(cached.assessment, ASSESSMENT)
        self.assertEqual(cached.input_hash, "f" * 64)
        self.assertEqual(len(self.redis.commands), 1)
        self.assertEqual(self.redis.commands[0][0], "GET")
        self.assertIn(":result:", self.redis.commands[0][1])

        self.redis.commands.clear()
        self.assertIsNone(
            self.store.get_result_for_exact_scope(scope("different-message"))
        )
        self.assertEqual(len(self.redis.commands), 1)
        self.assertEqual(self.redis.commands[0][0], "GET")

    def test_lost_lease_cannot_write_negative_or_delete_new_owner(self):
        cache_scope = scope()
        old_lease = self.store.try_acquire_lease(
            cache_scope,
            random_bytes=lambda length: bytes([11]) * length,
        )
        lease_key = next(key for key in self.redis.values if ":lease:" in key)
        self.redis.values[lease_key] = "new-owner-token"

        self.assertFalse(self.store.commit_negative_if_lease_owned(
            cache_scope,
            lease_token=old_lease,
            code="provider_timeout",
        ))
        self.assertEqual(self.redis.values[lease_key], "new-owner-token")
        self.assertFalse(any(":negative:" in key for key in self.redis.values))

    def test_ttls_and_model_version_are_part_of_the_storage_contract(self):
        first = scope()
        second = replace(first, model_version="different-model")
        self.assertNotEqual(
            store_module.derive_scope_digest(SECRET, first),
            store_module.derive_scope_digest(SECRET, second),
        )
        lease = self.store.try_acquire_lease(
            first,
            random_bytes=lambda length: bytes([12]) * length,
        )
        self.assertEqual(self.redis.commands[-1][-3:], ["EX", 60, "NX"])
        self.assertTrue(self.store.commit_negative_if_lease_owned(
            first,
            lease_token=lease,
            code="provider_timeout",
        ))
        self.assertEqual(self.redis.commands[-1][-1], 300)

        result_scope = scope("result-ttl")
        self.store.mark_current_if_newer(result_scope, occurred_at=30_000)
        result_lease = self.store.try_acquire_lease(
            result_scope,
            random_bytes=lambda length: bytes([13]) * length,
        )
        self.assertTrue(self.store.commit_result_if_lease_owned(
            result_scope,
            lease_token=result_lease,
            assessment=ASSESSMENT,
            input_hash="e" * 64,
            occurred_at=30_000,
            assessed_at=24,
        ))
        self.assertEqual(self.redis.commands[-1][-1], 30 * 24 * 60 * 60)

    def _commit_indexed(
        self,
        cache_scope: SemanticCacheScope,
        *,
        occurred_at: int,
        assessed_at: int,
        token_byte: int = 20,
        account_identity: str = "primary@example.com",
    ) -> bool:
        self.store.set_current_exact(cache_scope, occurred_at=occurred_at)
        lease = self.store.try_acquire_lease(
            cache_scope,
            random_bytes=lambda length: bytes([token_byte]) * length,
        )
        self.assertIsNotNone(lease)
        return self.store.commit_result_if_lease_owned(
            cache_scope,
            lease_token=lease,
            assessment=ACTIONABLE_ASSESSMENT,
            input_hash="9" * 64,
            occurred_at=occurred_at,
            assessed_at=assessed_at,
            index_new_inbound=True,
            new_inbound_mailbox_account_identity=account_identity,
        )

    def test_new_inbound_commit_atomically_creates_content_free_bounded_index(self):
        cache_scope = scope("new-inbound-1")
        self.assertTrue(
            self._commit_indexed(
                cache_scope,
                occurred_at=10_000,
                assessed_at=1_000,
            )
        )

        entries = self.store.read_new_inbound_index(
            index_scope(),
            semantic_version=cache_scope.semantic_version,
            model_version=cache_scope.model_version,
        )
        self.assertEqual(
            entries,
            (
                NewInboundIndexEntry(
                    conversation_id=cache_scope.conversation_id,
                    latest_turn_id=cache_scope.latest_turn_id,
                    semantic_version=cache_scope.semantic_version,
                    model_version=cache_scope.model_version,
                    occurred_at=10_000,
                ),
            ),
        )
        serialized = next(iter(next(iter(self.redis.hashes.values())).values()))
        self.assertTrue(
            all(
                "primary@example.com" not in key
                for key in (
                    *self.redis.hashes,
                    *self.redis.sorted_sets,
                    *self.redis.values,
                )
            )
        )
        self.assertLessEqual(
            len(serialized.encode("utf-8")),
            NEW_INBOUND_INDEX_MAX_SERIALIZED_RECORD_BYTES,
        )
        self.assertNotIn("primary@example.com", serialized)
        for forbidden in (
            "needs_user_action",
            "explicit_request",
            "confidence",
            "body",
            "subject",
            "sender",
            "recipient",
            "Authorization",
            "cookie",
            "access_token",
            "refresh_token",
            "password",
            "MIME",
            "attachment",
        ):
            self.assertNotIn(forbidden, serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            set(payload),
            {
                "schemaVersion",
                "scopeDigest",
                "conversationDigest",
                "conversationId",
                "latestTurnId",
                "semanticVersion",
                "modelVersion",
                "occurredAt",
                "recordMac",
            },
        )
        commit = next(
            command
            for command in self.redis.commands
            if command[0] == "EVAL"
            and command[1] == store_module._COMMIT_NEW_INBOUND_RESULT_SCRIPT
        )
        self.assertTrue(self.redis.lua_type_replies_as_status_tables)
        self.assertIn("type(value)=='table'", commit[1])
        self.assertIn("value['ok']", commit[1])
        self.assertLess(len(json.dumps(commit).encode("utf-8")), 16_384)
        self.assertEqual(commit[-4], NEW_INBOUND_INDEX_TTL_SECONDS)
        self.assertEqual(commit[-3], NEW_INBOUND_INDEX_MAX_RECORDS)
        self.assertEqual(commit[-1], "1")

    def test_non_indexed_commit_and_cache_read_never_migrate_legacy_result(self):
        cache_scope = scope("legacy-shadow")
        self.store.set_current_exact(cache_scope, occurred_at=10_000)
        lease = self.store.try_acquire_lease(
            cache_scope,
            random_bytes=lambda length: bytes([21]) * length,
        )
        self.assertTrue(
            self.store.commit_result_if_lease_owned(
                cache_scope,
                lease_token=lease,
                assessment=ACTIONABLE_ASSESSMENT,
                input_hash="8" * 64,
                occurred_at=10_000,
                assessed_at=1_000,
            )
        )
        self.assertIsNotNone(
            self.store.get_results_for_hydration_scopes((cache_scope,))[0]
        )
        self.assertEqual(
            self.store.read_new_inbound_index(
                index_scope(),
                semantic_version=cache_scope.semantic_version,
                model_version=cache_scope.model_version,
            ),
            (),
        )
        self.assertEqual(self.redis.hashes, {})

    def test_index_is_idempotent_and_one_record_per_conversation(self):
        cache_scope = scope("same-turn")
        self.assertTrue(
            self._commit_indexed(
                cache_scope,
                occurred_at=20_000,
                assessed_at=2_000,
                token_byte=22,
            )
        )
        self.assertTrue(
            self._commit_indexed(
                cache_scope,
                occurred_at=20_000,
                assessed_at=2_000,
                token_byte=23,
            )
        )
        entries = self.store.read_new_inbound_index(
            index_scope(),
            semantic_version=cache_scope.semantic_version,
            model_version=cache_scope.model_version,
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].latest_turn_id, "same-turn")

    def test_current_pointer_rejects_stale_race_while_occurrence_is_metadata(self):
        stale = scope("turn-stale")
        current = replace(stale, latest_turn_id="turn-current")
        equal_occurrence = replace(stale, latest_turn_id="turn-equal")
        lower_occurrence = replace(stale, latest_turn_id="turn-lower-time")

        self.store.set_current_exact(stale, occurred_at=30_000)
        stale_lease = self.store.try_acquire_lease(
            stale,
            random_bytes=lambda length: bytes([24]) * length,
        )
        self.store.set_current_exact(current, occurred_at=31_000)
        current_lease = self.store.try_acquire_lease(
            current,
            random_bytes=lambda length: bytes([25]) * length,
        )
        self.assertTrue(
            self.store.commit_result_if_lease_owned(
                current,
                lease_token=current_lease,
                assessment=ACTIONABLE_ASSESSMENT,
                input_hash="5" * 64,
                occurred_at=31_000,
                assessed_at=3_001,
                index_new_inbound=True,
                new_inbound_mailbox_account_identity="primary@example.com",
            )
        )
        self.assertFalse(
            self.store.commit_result_if_lease_owned(
                stale,
                lease_token=stale_lease,
                assessment=ACTIONABLE_ASSESSMENT,
                input_hash="4" * 64,
                occurred_at=30_000,
                assessed_at=3_002,
                index_new_inbound=True,
                new_inbound_mailbox_account_identity="primary@example.com",
            )
        )

        self.assertTrue(
            self._commit_indexed(
                equal_occurrence,
                occurred_at=31_000,
                assessed_at=3_003,
                token_byte=26,
            )
        )
        self.assertTrue(
            self._commit_indexed(
                lower_occurrence,
                occurred_at=29_000,
                assessed_at=3_004,
                token_byte=27,
            )
        )
        entries = self.store.read_new_inbound_index(
            index_scope(),
            semantic_version=stale.semantic_version,
            model_version=stale.model_version,
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].latest_turn_id, "turn-lower-time")
        self.assertEqual(entries[0].occurred_at, 29_000)

    def test_index_prunes_deterministically_to_sixty_four_records(self):
        for index in range(NEW_INBOUND_INDEX_MAX_RECORDS + 3):
            cache_scope = replace(
                scope(f"turn-{index}"),
                conversation_id=f"conversation-{index}",
            )
            self.assertTrue(
                self._commit_indexed(
                    cache_scope,
                    occurred_at=40_000 + index,
                    assessed_at=4_000 + index,
                    token_byte=(index % 200) + 30,
                )
            )
        entries = self.store.read_new_inbound_index(
            index_scope(),
            semantic_version=scope().semantic_version,
            model_version=scope().model_version,
        )
        self.assertEqual(len(entries), NEW_INBOUND_INDEX_MAX_RECORDS)
        self.assertEqual(entries[0].conversation_id, "conversation-66")
        self.assertEqual(entries[-1].conversation_id, "conversation-3")
        self.assertNotIn(
            "conversation-0",
            {entry.conversation_id for entry in entries},
        )

    def test_index_prunes_records_older_than_the_thirty_day_window(self):
        old = replace(
            scope("expired-turn"),
            conversation_id="expired-conversation",
        )
        fresh = replace(
            scope("fresh-turn"),
            conversation_id="fresh-conversation",
        )
        self.assertTrue(
            self._commit_indexed(
                old,
                occurred_at=45_000,
                assessed_at=4_500,
                token_byte=97,
            )
        )
        self.assertTrue(
            self._commit_indexed(
                fresh,
                occurred_at=45_001,
                assessed_at=4_500 + NEW_INBOUND_INDEX_TTL_SECONDS + 1,
                token_byte=98,
            )
        )
        entries = self.store.read_new_inbound_index(
            index_scope(),
            semantic_version=scope().semantic_version,
            model_version=scope().model_version,
        )
        self.assertEqual(
            tuple(entry.conversation_id for entry in entries),
            ("fresh-conversation",),
        )

    def test_index_scope_binds_tenant_mailbox_provider_and_account_identity(self):
        base = index_scope()
        variants = (
            index_scope(workspace_id=_account("wsp_", 7)),
            index_scope(user_id=_account("usr_", 8)),
            index_scope(mailbox_id="mailbox-2"),
            index_scope(provider="custom_imap"),
            index_scope(mailbox_account_identity="replacement@example.com"),
        )
        base_digest = derive_new_inbound_index_scope_digest(SECRET, base)
        self.assertEqual(
            len(
                {
                    base_digest,
                    *(
                        derive_new_inbound_index_scope_digest(SECRET, variant)
                        for variant in variants
                    ),
                }
            ),
            len(variants) + 1,
        )
        self.assertNotIn("primary@example.com", base_digest)

    def test_wrong_versions_and_malformed_or_oversized_index_records_drop(self):
        cache_scope = scope("versioned-turn")
        self.assertTrue(
            self._commit_indexed(
                cache_scope,
                occurred_at=50_000,
                assessed_at=5_000,
                token_byte=28,
            )
        )
        self.assertEqual(
            self.store.read_new_inbound_index(
                index_scope(),
                semantic_version="different-semantic-version",
                model_version=cache_scope.model_version,
            ),
            (),
        )
        self.assertEqual(
            self.store.read_new_inbound_index(
                index_scope(),
                semantic_version=cache_scope.semantic_version,
                model_version="different-model",
            ),
            (),
        )
        record_hash = next(iter(self.redis.hashes.values()))
        member = next(iter(record_hash))
        record_hash[member] = "not-json"
        self.assertEqual(
            self.store.read_new_inbound_index(
                index_scope(),
                semantic_version=cache_scope.semantic_version,
                model_version=cache_scope.model_version,
            ),
            (),
        )
        with self.assertRaises(ValueError):
            NewInboundIndexEntry(
                conversation_id="x" * (
                    NEW_INBOUND_INDEX_MAX_CONVERSATION_ID_CHARACTERS + 1
                ),
                latest_turn_id="turn",
                semantic_version=cache_scope.semantic_version,
                model_version=cache_scope.model_version,
                occurred_at=1,
            )

    def test_index_and_result_reads_are_batched_below_kv_transport_caps(self):
        for index in range(13):
            cache_scope = replace(
                scope(f"batch-turn-{index}"),
                conversation_id=f"batch-conversation-{index}",
            )
            self.assertTrue(
                self._commit_indexed(
                    cache_scope,
                    occurred_at=60_000 + index,
                    assessed_at=6_000 + index,
                    token_byte=100 + index,
                )
            )
        self.redis.commands.clear()
        entries = self.store.read_new_inbound_index(
            index_scope(),
            semantic_version=scope().semantic_version,
            model_version=scope().model_version,
        )
        index_commands = list(self.redis.commands)
        self.assertEqual(len(entries), 13)
        self.assertEqual(index_commands[0][0], "ZREVRANGE")
        hmget_commands = [
            command for command in index_commands if command[0] == "HMGET"
        ]
        self.assertEqual(len(hmget_commands), 3)
        self.assertTrue(
            all(
                len(command) - 2 <= NEW_INBOUND_INDEX_READ_BATCH_SIZE
                for command in hmget_commands
            )
        )

        self.redis.commands.clear()
        index_current = index_scope()
        cached = self.store.get_results_for_hydration_scopes(
            tuple(entry.to_cache_scope(index_current) for entry in entries)
        )
        mget_commands = [
            command for command in self.redis.commands if command[0] == "MGET"
        ]
        self.assertEqual(len(cached), 13)
        self.assertTrue(all(record is not None for record in cached))
        self.assertEqual(len(mget_commands), 5)
        self.assertTrue(
            all(
                len(command) - 1 <= SEMANTIC_HYDRATION_RESULT_BATCH_SIZE
                for command in mget_commands
            )
        )
        for command in (*hmget_commands, *mget_commands):
            self.assertLess(len(json.dumps(command).encode("utf-8")), 16_384)

        worst_index_response = json.dumps(
            {
                "result": [
                    "\\" * NEW_INBOUND_INDEX_MAX_SERIALIZED_RECORD_BYTES
                    for _ in range(NEW_INBOUND_INDEX_READ_BATCH_SIZE)
                ]
            }
        ).encode("utf-8")
        worst_result_response = json.dumps(
            {
                "result": [
                    "\\" * 4_096
                    for _ in range(SEMANTIC_HYDRATION_RESULT_BATCH_SIZE)
                ]
            }
        ).encode("utf-8")
        self.assertLessEqual(len(worst_index_response), 32_768)
        self.assertLessEqual(len(worst_result_response), 32_768)

    def test_exact_turn_dismissal_is_opaque_idempotent_and_model_free(self):
        cache_scope = scope("dismissed-turn")
        self.assertTrue(
            self._commit_indexed(
                cache_scope,
                occurred_at=65_000,
                assessed_at=6_500,
                token_byte=113,
            )
        )
        current_index_scope = index_scope()
        self.redis.commands.clear()

        self.assertTrue(
            self.store.dismiss_new_inbound_exact(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
                semantic_version=cache_scope.semantic_version,
                current=6_500,
            )
        )
        first_commands = list(self.redis.commands)
        self.assertEqual(
            [command[0] for command in first_commands],
            ["HMGET", "ZSCORE", "ZSCORE", "GET", "EVAL"],
        )
        dismiss_command = first_commands[-1]
        self.assertEqual(dismiss_command[1], store_module._DISMISS_NEW_INBOUND_SCRIPT)
        self.assertEqual(dismiss_command[-3], NEW_INBOUND_DISMISSAL_TTL_SECONDS)
        self.assertLess(len(json.dumps(dismiss_command).encode("utf-8")), 16_384)
        worst_dismiss_command = [
            "EVAL",
            store_module._DISMISS_NEW_INBOUND_SCRIPT,
            5,
            *dismiss_command[3:8],
            "f" * 64,
            "\\" * NEW_INBOUND_INDEX_MAX_SERIALIZED_RECORD_BYTES,
            "1",
            "\\" * 4_096,
            NEW_INBOUND_DISMISSAL_TTL_SECONDS,
            store_module.NEW_INBOUND_INDEX_MAX_OCCURRENCE,
            store_module.NEW_INBOUND_INDEX_MAX_OCCURRENCE,
        ]
        self.assertLess(
            len(json.dumps(worst_dismiss_command).encode("utf-8")),
            16_384,
        )
        self.assertFalse(
            any(
                command[0] == "EVAL"
                and command[1]
                in {
                    store_module._ATTEMPT_SCRIPT,
                    store_module._MARK_CURRENT_SCRIPT,
                    store_module._COMMIT_RESULT_SCRIPT,
                    store_module._COMMIT_NEW_INBOUND_RESULT_SCRIPT,
                }
                for command in first_commands
            )
        )

        tombstones = {
            key: value
            for key, value in self.redis.values.items()
            if ":new-inbound-dismissal:" in key
        }
        self.assertEqual(len(tombstones), 1)
        tombstone_key, tombstone_value = next(iter(tombstones.items()))
        self.assertEqual(tombstone_value, "1")
        serialized_tombstone = f"{tombstone_key}\n{tombstone_value}"
        for forbidden in (
            cache_scope.conversation_id,
            cache_scope.latest_turn_id,
            cache_scope.semantic_version,
            cache_scope.model_version,
            "primary@example.com",
            "needs_user_action",
            "explicit_request",
            "classification",
            "confidence",
            "reason",
            "subject",
            "body",
            "sender",
            "recipient",
            "headers",
            "MIME",
            "attachment",
            "credentials",
            "access_token",
            "refresh_token",
            "password",
        ):
            self.assertNotIn(forbidden, serialized_tombstone)

        self.assertTrue(
            self.store.dismiss_new_inbound_exact(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
                semantic_version=cache_scope.semantic_version,
                current=6_500,
            )
        )
        self.assertEqual(
            len(
                [
                    key
                    for key in self.redis.values
                    if ":new-inbound-dismissal:" in key
                ]
            ),
            1,
        )

    def test_exact_turn_tombstone_probe_is_version_agnostic_and_fail_closed(self):
        cache_scope = scope("probe-dismissed-turn")
        self.assertTrue(
            self._commit_indexed(
                cache_scope,
                occurred_at=65_100,
                assessed_at=6_510,
                token_byte=124,
            )
        )
        current_index_scope = index_scope()
        self.assertFalse(
            self.store.is_new_inbound_dismissed_exact(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
            )
        )
        self.assertTrue(
            self.store.dismiss_new_inbound_exact(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
                semantic_version=cache_scope.semantic_version,
                current=6_510,
            )
        )
        self.assertTrue(
            self.store.is_new_inbound_dismissed_exact(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
            )
        )

        tombstone_key = next(
            key
            for key in self.redis.values
            if ":new-inbound-dismissal:" in key
        )
        self.redis.values[tombstone_key] = "unexpected"
        with self.assertRaises(SemanticStoreUnavailable):
            self.store.is_new_inbound_dismissed_exact(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
            )
        with self.assertRaises(ValueError):
            self.store.is_new_inbound_dismissed_exact(
                current_index_scope,
                conversation_id=" forged ",
                latest_turn_id=cache_scope.latest_turn_id,
            )

    def test_dismissal_survives_model_version_change_but_not_a_newer_turn(self):
        original = scope("exact-turn-a")
        self.assertTrue(
            self._commit_indexed(
                original,
                occurred_at=66_000,
                assessed_at=6_600,
                token_byte=118,
            )
        )
        current_index_scope = index_scope()
        self.assertTrue(
            self.store.dismiss_new_inbound_exact(
                current_index_scope,
                conversation_id=original.conversation_id,
                latest_turn_id=original.latest_turn_id,
                semantic_version=original.semantic_version,
                current=6_600,
            )
        )
        tombstone_key = next(
            key
            for key in self.redis.values
            if ":new-inbound-dismissal:" in key
        )
        self.redis.expirations[tombstone_key] = 1

        re_assessed = replace(original, model_version="replacement-model")
        self.assertTrue(
            self._commit_indexed(
                re_assessed,
                occurred_at=66_000,
                assessed_at=6_601,
                token_byte=119,
            )
        )
        self.assertEqual(
            self.redis.expirations[tombstone_key],
            NEW_INBOUND_DISMISSAL_TTL_SECONDS,
        )
        re_assessed_entry = self.store.read_new_inbound_index(
            current_index_scope,
            semantic_version=re_assessed.semantic_version,
            model_version=re_assessed.model_version,
        )
        self.assertEqual(
            self.store.get_new_inbound_dismissal_states(
                current_index_scope,
                re_assessed_entry,
            ),
            (True,),
        )

        newer = replace(re_assessed, latest_turn_id="exact-turn-b")
        self.assertTrue(
            self._commit_indexed(
                newer,
                occurred_at=66_001,
                assessed_at=6_602,
                token_byte=120,
            )
        )
        newer_entry = self.store.read_new_inbound_index(
            current_index_scope,
            semantic_version=newer.semantic_version,
            model_version=newer.model_version,
        )
        self.assertEqual(newer_entry[0].latest_turn_id, "exact-turn-b")
        self.assertEqual(
            self.store.get_new_inbound_dismissal_states(
                current_index_scope,
                newer_entry,
            ),
            (False,),
        )

    def test_dismissal_requires_exact_signed_index_and_valid_cache(self):
        cache_scope = scope("valid-dismiss-turn")
        self.assertTrue(
            self._commit_indexed(
                cache_scope,
                occurred_at=67_000,
                assessed_at=6_700,
                token_byte=121,
            )
        )
        current_index_scope = index_scope()
        for conversation_id, latest_turn_id, semantic_version in (
            (
                "forged-conversation",
                cache_scope.latest_turn_id,
                cache_scope.semantic_version,
            ),
            (
                cache_scope.conversation_id,
                "forged-turn",
                cache_scope.semantic_version,
            ),
            (
                cache_scope.conversation_id,
                cache_scope.latest_turn_id,
                "wrong-semantic-version",
            ),
        ):
            with self.subTest(
                conversation_id=conversation_id,
                latest_turn_id=latest_turn_id,
                semantic_version=semantic_version,
            ):
                self.assertFalse(
                    self.store.dismiss_new_inbound_exact(
                        current_index_scope,
                        conversation_id=conversation_id,
                        latest_turn_id=latest_turn_id,
                        semantic_version=semantic_version,
                        current=6_700,
                    )
                )
        self.assertFalse(
            any(":new-inbound-dismissal:" in key for key in self.redis.values)
        )

        for invalid_current in (6_699, 6_700 + NEW_INBOUND_INDEX_TTL_SECONDS + 1):
            with self.subTest(invalid_current=invalid_current):
                self.assertFalse(
                    self.store.dismiss_new_inbound_exact(
                        current_index_scope,
                        conversation_id=cache_scope.conversation_id,
                        latest_turn_id=cache_scope.latest_turn_id,
                        semantic_version=cache_scope.semantic_version,
                        current=invalid_current,
                    )
                )

        result_key = self.store._keys(cache_scope)["result"]
        raw_result = self.redis.values.pop(result_key)
        self.assertFalse(
            self.store.dismiss_new_inbound_exact(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
                semantic_version=cache_scope.semantic_version,
                current=6_700,
            )
        )
        self.redis.values[result_key] = "malformed-cache"
        with self.assertRaises(SemanticStoreUnavailable):
            self.store.dismiss_new_inbound_exact(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
                semantic_version=cache_scope.semantic_version,
                current=6_700,
            )
        self.redis.values[result_key] = raw_result
        tombstone_key = self.store._new_inbound_dismissal_key(
            current_index_scope,
            conversation_id=cache_scope.conversation_id,
            latest_turn_id=cache_scope.latest_turn_id,
        )
        self.redis.values[tombstone_key] = "unexpected"
        with self.assertRaises(SemanticStoreUnavailable):
            self.store.dismiss_new_inbound_exact(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
                semantic_version=cache_scope.semantic_version,
                current=6_700,
            )
        self.redis.values.pop(tombstone_key)
        self.redis.hashes[tombstone_key] = {"member": "1"}
        with self.assertRaises(SemanticStoreUnavailable):
            self.store.dismiss_new_inbound_exact(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
                semantic_version=cache_scope.semantic_version,
                current=6_700,
            )
        self.redis.hashes.pop(tombstone_key)
        self.assertFalse(
            any(":new-inbound-dismissal:" in key for key in self.redis.values)
        )

    def test_dismissal_rejects_orphaned_or_inconsistent_index_membership(self):
        cache_scope = scope("orphan-dismiss-turn")
        self.assertTrue(
            self._commit_indexed(
                cache_scope,
                occurred_at=67_500,
                assessed_at=6_750,
                token_byte=122,
            )
        )
        current_index_scope = index_scope()
        index_keys = self.store._new_inbound_index_keys(current_index_scope)
        member = next(iter(self.redis.hashes[index_keys["records"]]))
        for collection_name in ("occurrences", "freshness"):
            with self.subTest(collection_name=collection_name):
                collection = self.redis.sorted_sets[index_keys[collection_name]]
                original_score = collection.pop(member)
                self.assertFalse(
                    self.store.dismiss_new_inbound_exact(
                        current_index_scope,
                        conversation_id=cache_scope.conversation_id,
                        latest_turn_id=cache_scope.latest_turn_id,
                        semantic_version=cache_scope.semantic_version,
                        current=6_750,
                    )
                )
                collection[member] = original_score
        self.redis.sorted_sets[index_keys["occurrences"]][member] = 1.0
        self.assertFalse(
            self.store.dismiss_new_inbound_exact(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
                semantic_version=cache_scope.semantic_version,
                current=6_750,
            )
        )
        self.redis.sorted_sets[index_keys["occurrences"]][member] = 67_500.0
        self.redis.sorted_sets[index_keys["freshness"]][member] = 1.0
        self.assertFalse(
            self.store.dismiss_new_inbound_exact(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
                semantic_version=cache_scope.semantic_version,
                current=6_750,
            )
        )
        self.assertFalse(
            any(":new-inbound-dismissal:" in key for key in self.redis.values)
        )

    def test_dismissal_lua_rechecks_index_membership_after_python_validation(self):
        class RacingRedis(MemoryRedis):
            disrupt_dismiss = False

            def __call__(self, command: list[object]) -> dict[str, object]:
                if (
                    self.disrupt_dismiss
                    and command[0] == "EVAL"
                    and command[1] == store_module._DISMISS_NEW_INBOUND_SCRIPT
                ):
                    key_count = int(command[2])
                    keys = command[3 : 3 + key_count]
                    args = command[3 + key_count :]
                    self.sorted_sets.get(keys[2], {}).pop(args[0], None)
                return super().__call__(command)

        redis = RacingRedis()
        current_store = SemanticAssessmentStore(redis, hmac_secret=SECRET)
        cache_scope = scope("racing-dismiss-turn")
        current_store.set_current_exact(cache_scope, occurred_at=68_000)
        lease = current_store.try_acquire_lease(
            cache_scope,
            random_bytes=lambda length: bytes([124]) * length,
        )
        self.assertTrue(
            current_store.commit_result_if_lease_owned(
                cache_scope,
                lease_token=lease,
                assessment=ACTIONABLE_ASSESSMENT,
                input_hash="2" * 64,
                occurred_at=68_000,
                assessed_at=6_800,
                index_new_inbound=True,
                new_inbound_mailbox_account_identity="primary@example.com",
            )
        )
        redis.disrupt_dismiss = True
        self.assertFalse(
            current_store.dismiss_new_inbound_exact(
                index_scope(),
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
                semantic_version=cache_scope.semantic_version,
                current=6_800,
            )
        )
        self.assertFalse(
            any(":new-inbound-dismissal:" in key for key in redis.values)
        )

    def test_dismissal_digest_binds_tenant_account_and_exact_turn(self):
        base = index_scope()
        base_digest = derive_new_inbound_dismissal_digest(
            SECRET,
            base,
            conversation_id="conversation-1",
            latest_turn_id="turn-1",
        )
        variants = (
            (replace(base, workspace_id=_account("wsp_", 9)), "conversation-1", "turn-1"),
            (replace(base, user_id=_account("usr_", 10)), "conversation-1", "turn-1"),
            (replace(base, mailbox_id="mailbox-2"), "conversation-1", "turn-1"),
            (replace(base, provider="custom_imap"), "conversation-1", "turn-1"),
            (
                replace(base, mailbox_account_identity="replacement@example.com"),
                "conversation-1",
                "turn-1",
            ),
            (base, "conversation-2", "turn-1"),
            (base, "conversation-1", "turn-2"),
        )
        digests = {
            base_digest,
            *(
                derive_new_inbound_dismissal_digest(
                    SECRET,
                    current_scope,
                    conversation_id=conversation_id,
                    latest_turn_id=latest_turn_id,
                )
                for current_scope, conversation_id, latest_turn_id in variants
            ),
        }
        self.assertEqual(len(digests), len(variants) + 1)
        self.assertTrue(all(len(digest) == 64 for digest in digests))

    def test_dismissal_hydration_reads_are_one_bounded_batch_and_fail_closed(self):
        current_index_scope = index_scope()
        entries = tuple(
            NewInboundIndexEntry(
                conversation_id=f"conversation-{index}",
                latest_turn_id=f"turn-{index}",
                semantic_version=scope().semantic_version,
                model_version=scope().model_version,
                occurred_at=index,
            )
            for index in range(NEW_INBOUND_INDEX_MAX_RECORDS)
        )
        for index, entry in enumerate(entries):
            if index % 2 == 0:
                key = self.store._new_inbound_dismissal_key(
                    current_index_scope,
                    conversation_id=entry.conversation_id,
                    latest_turn_id=entry.latest_turn_id,
                )
                self.redis.values[key] = "1"
        self.redis.commands.clear()
        states = self.store.get_new_inbound_dismissal_states(
            current_index_scope,
            entries,
        )
        self.assertEqual(
            states,
            tuple(index % 2 == 0 for index in range(len(entries))),
        )
        self.assertEqual(len(self.redis.commands), 1)
        command = self.redis.commands[0]
        self.assertEqual(command[0], "EVAL")
        self.assertEqual(
            command[1],
            store_module._READ_NEW_INBOUND_DISMISSALS_SCRIPT,
        )
        self.assertIn("MGET", command[1])
        self.assertEqual(command[2], NEW_INBOUND_DISMISSAL_READ_BATCH_SIZE)
        self.assertLess(len(json.dumps(command).encode("utf-8")), 16_384)
        worst_response = json.dumps(
            {"result": [1] * NEW_INBOUND_DISMISSAL_READ_BATCH_SIZE}
        ).encode("utf-8")
        self.assertLessEqual(len(worst_response), 32_768)

        malformed_key = self.store._new_inbound_dismissal_key(
            current_index_scope,
            conversation_id=entries[1].conversation_id,
            latest_turn_id=entries[1].latest_turn_id,
        )
        self.redis.values[malformed_key] = "unexpected"
        with self.assertRaises(SemanticStoreUnavailable):
            self.store.get_new_inbound_dismissal_states(
                current_index_scope,
                entries,
            )
        self.redis.values.pop(malformed_key)
        self.redis.hashes[malformed_key] = {"member": "1"}
        with self.assertRaises(SemanticStoreUnavailable):
            self.store.get_new_inbound_dismissal_states(
                current_index_scope,
                entries,
            )

    def test_wrongtype_index_preflight_leaves_no_semantic_result(self):
        cache_scope = scope("wrongtype-turn")
        index_current = index_scope()
        index_keys = self.store._new_inbound_index_keys(index_current)
        self.redis.values[index_keys["records"]] = "wrong-type"
        self.store.set_current_exact(cache_scope, occurred_at=70_000)
        lease = self.store.try_acquire_lease(
            cache_scope,
            random_bytes=lambda length: bytes([114]) * length,
        )
        with self.assertRaises(store_module.SemanticStoreUnavailable):
            self.store.commit_result_if_lease_owned(
                cache_scope,
                lease_token=lease,
                assessment=ACTIONABLE_ASSESSMENT,
                input_hash="7" * 64,
                occurred_at=70_000,
                assessed_at=7_000,
                index_new_inbound=True,
                new_inbound_mailbox_account_identity="primary@example.com",
            )
        result_key = self.store._keys(cache_scope)["result"]
        self.assertNotIn(result_key, self.redis.values)
        self.assertFalse(self.redis.hashes)
        self.assertFalse(self.redis.sorted_sets)

    def test_tombstone_preflight_rejects_malformed_or_wrongtype_before_commit(self):
        for corruption in ("malformed", "wrongtype"):
            with self.subTest(corruption=corruption):
                redis = MemoryRedis()
                current_store = SemanticAssessmentStore(
                    redis,
                    hmac_secret=SECRET,
                )
                cache_scope = scope(f"tombstone-preflight-{corruption}")
                current_index_scope = index_scope()
                tombstone_key = current_store._new_inbound_dismissal_key(
                    current_index_scope,
                    conversation_id=cache_scope.conversation_id,
                    latest_turn_id=cache_scope.latest_turn_id,
                )
                if corruption == "malformed":
                    redis.values[tombstone_key] = "unexpected"
                else:
                    redis.hashes[tombstone_key] = {"member": "1"}
                current_store.set_current_exact(cache_scope, occurred_at=75_000)
                lease = current_store.try_acquire_lease(
                    cache_scope,
                    random_bytes=lambda length: bytes([123]) * length,
                )
                with self.assertRaises(SemanticStoreUnavailable):
                    current_store.commit_result_if_lease_owned(
                        cache_scope,
                        lease_token=lease,
                        assessment=ACTIONABLE_ASSESSMENT,
                        input_hash="3" * 64,
                        occurred_at=75_000,
                        assessed_at=7_500,
                        index_new_inbound=True,
                        new_inbound_mailbox_account_identity="primary@example.com",
                    )
                self.assertNotIn(
                    current_store._keys(cache_scope)["result"],
                    redis.values,
                )
                self.assertFalse(
                    any(
                        ":new-inbound-index:" in key
                        for key in (*redis.hashes, *redis.sorted_sets)
                    )
                )

    def test_valid_index_record_copied_under_wrong_member_is_rejected(self):
        cache_scope = scope("member-bound-turn")
        self.assertTrue(
            self._commit_indexed(
                cache_scope,
                occurred_at=80_000,
                assessed_at=8_000,
                token_byte=115,
            )
        )
        record_key, records = next(iter(self.redis.hashes.items()))
        original_member, record = next(iter(records.items()))
        freshness_key, freshness = next(
            (key, values)
            for key, values in self.redis.sorted_sets.items()
            if "freshness" in key
        )
        wrong_member = "f" * 64
        self.redis.hashes[record_key] = {wrong_member: record}
        self.redis.sorted_sets[freshness_key] = {wrong_member: 9_000.0}
        self.assertNotEqual(original_member, wrong_member)
        self.assertEqual(
            self.store.read_new_inbound_index(
                index_scope(),
                semantic_version=cache_scope.semantic_version,
                model_version=cache_scope.model_version,
            ),
            (),
        )

    def test_partial_index_corruption_cannot_let_a_new_result_commit(self):
        older = scope("consistent-old")
        newer = replace(older, latest_turn_id="blocked-newer")
        self.assertTrue(
            self._commit_indexed(
                older,
                occurred_at=90_000,
                assessed_at=9_000,
                token_byte=116,
            )
        )
        occurrences = next(
            values
            for key, values in self.redis.sorted_sets.items()
            if "occurrences" in key
        )
        occurrences.clear()
        self.store.set_current_exact(newer, occurred_at=91_000)
        lease = self.store.try_acquire_lease(
            newer,
            random_bytes=lambda length: bytes([117]) * length,
        )
        with self.assertRaises(store_module.SemanticStoreUnavailable):
            self.store.commit_result_if_lease_owned(
                newer,
                lease_token=lease,
                assessment=ACTIONABLE_ASSESSMENT,
                input_hash="6" * 64,
                occurred_at=91_000,
                assessed_at=9_001,
                index_new_inbound=True,
                new_inbound_mailbox_account_identity="primary@example.com",
            )
        self.assertNotIn(self.store._keys(newer)["result"], self.redis.values)


class CustomImapV2SemanticNamespaceTests(unittest.TestCase):
    ACCOUNT = "primary@example.com"

    def setUp(self) -> None:
        self.redis = MemoryRedis()
        self.legacy = SemanticAssessmentStore(self.redis, hmac_secret=SECRET)
        self.v2 = SemanticAssessmentStore(
            self.redis,
            hmac_secret=SECRET,
            mode=SEMANTIC_STORE_MODE_CUSTOM_IMAP_V2,
            mailbox_account_identity=self.ACCOUNT,
        )

    def test_legacy_google_contract_is_byte_stable(self):
        cache_scope = scope()
        self.assertEqual(SEMANTIC_SCHEMA_VERSION, "priority-semantic-state-v1")
        self.assertEqual(
            semantic_schema_version_for_provider("google"),
            "priority-semantic-state-v1",
        )
        self.assertEqual(
            self.legacy._keys(cache_scope)["result"],
            "cuevion:priority:semantic:v1:result:"
            "69863744edede234efa53b2f73cc7e431722ed79d466e56777441f597f005ceb",
        )
        self.legacy.set_current_exact(cache_scope, occurred_at=10_000)
        lease = self.legacy.try_acquire_lease(
            cache_scope,
            random_bytes=lambda length: bytes([1]) * length,
        )
        self.assertTrue(
            self.legacy.commit_result_if_lease_owned(
                cache_scope,
                lease_token=lease,
                assessment=ASSESSMENT,
                input_hash="a" * 64,
                occurred_at=10_000,
                assessed_at=20,
            )
        )
        self.assertEqual(
            self.redis.values[self.legacy._keys(cache_scope)["result"]],
            '{"assessedAt":20,"confidence":0.98,"conversationDigest":'
            '"79b10599e8192dc94384294df86fa961e67dafaafcb1ea40145979ccbeaad1b3",'
            '"effectiveSemanticState":"resolved","inputHash":"'
            + "a" * 64
            + '","latestTurnDigest":"06d953be3416889653f117358cd6b2dc433c314f1c7c6104240c3b24e2f19c30",'
            '"modelVersion":"test-model","reasonCode":"completed_confirmation",'
            '"schemaVersion":1,"scopeDigest":"69863744edede234efa53b2f73cc7e431722ed79d466e56777441f597f005ceb",'
            '"semanticVersion":"priority-semantic-state-v1","state":"resolved"}',
        )

    def test_legacy_custom_imap_keys_and_wire_record_are_byte_stable(self):
        cache_scope = custom_imap_scope(
            conversation_id="imap:rfc:example",
            latest_turn_id="message-1",
            semantic_version=SEMANTIC_SCHEMA_VERSION,
        )
        current_key, current_value = self.legacy._current_key_and_value(
            cache_scope,
            10_000,
        )
        self.assertEqual(
            self.legacy._keys(cache_scope)["result"],
            "cuevion:priority:semantic:v1:result:"
            "b5121091b72f174165d27d20f3828c2a6745d713956c7ec200c3259bd1c0f754",
        )
        self.assertEqual(
            (current_key, current_value),
            (
                "cuevion:priority:semantic:v1:current:"
                "ff5b6697ded0c56e5eec77b1f6ea9c2f487e18c9918e5597e631154856b50881",
                "10000:1599b6cd52db38a1f7fe0f294df440ca2e36c5d09b1f1b2881c175562f7a3437",
            ),
        )
        current_index_scope = index_scope(provider="custom_imap")
        self.assertEqual(
            self.legacy._new_inbound_index_keys(current_index_scope),
            {
                "digest": "4b77819825cc26f26564d723a7a41401decdc0192aa5eb4afdd6a74338d01099",
                "records": "cuevion:priority:semantic:v1:new-inbound-index:records:4b77819825cc26f26564d723a7a41401decdc0192aa5eb4afdd6a74338d01099",
                "occurrences": "cuevion:priority:semantic:v1:new-inbound-index:occurrences:4b77819825cc26f26564d723a7a41401decdc0192aa5eb4afdd6a74338d01099",
                "freshness": "cuevion:priority:semantic:v1:new-inbound-index:freshness:4b77819825cc26f26564d723a7a41401decdc0192aa5eb4afdd6a74338d01099",
            },
        )
        self.assertEqual(
            self.legacy._new_inbound_dismissal_key(
                current_index_scope,
                conversation_id=cache_scope.conversation_id,
                latest_turn_id=cache_scope.latest_turn_id,
            ),
            "cuevion:priority:semantic:v1:new-inbound-dismissal:"
            "9c2d9ead33b97f4c7e62f9a270c0835454fb85f25e16159ea2de0f5bc7bff862",
        )
        self.legacy.set_current_exact(cache_scope, occurred_at=10_000)
        lease = self.legacy.try_acquire_lease(
            cache_scope,
            random_bytes=lambda length: bytes([1]) * length,
        )
        self.assertTrue(
            self.legacy.commit_result_if_lease_owned(
                cache_scope,
                lease_token=lease,
                assessment=ASSESSMENT,
                input_hash="a" * 64,
                occurred_at=10_000,
                assessed_at=20,
            )
        )
        self.assertEqual(
            self.redis.values[self.legacy._keys(cache_scope)["result"]],
            '{"assessedAt":20,"confidence":0.98,"conversationDigest":'
            '"ff5b6697ded0c56e5eec77b1f6ea9c2f487e18c9918e5597e631154856b50881",'
            '"effectiveSemanticState":"resolved","inputHash":"'
            + "a" * 64
            + '","latestTurnDigest":"1599b6cd52db38a1f7fe0f294df440ca2e36c5d09b1f1b2881c175562f7a3437",'
            '"modelVersion":"test-model","reasonCode":"completed_confirmation",'
            '"schemaVersion":1,"scopeDigest":"b5121091b72f174165d27d20f3828c2a6745d713956c7ec200c3259bd1c0f754",'
            '"semanticVersion":"priority-semantic-state-v1","state":"resolved"}',
        )

    def test_custom_imap_v2_all_artifact_families_are_physically_isolated(self):
        legacy_scope = custom_imap_scope(
            conversation_id="imap:rfc:conversation-1",
            semantic_version=SEMANTIC_SCHEMA_VERSION,
        )
        v2_scope = custom_imap_scope()
        legacy_keys = self.legacy._keys(legacy_scope)
        v2_keys = self.v2._keys(v2_scope)
        for family in ("result", "negative", "lease", "attempts"):
            with self.subTest(family=family):
                self.assertNotEqual(legacy_keys[family], v2_keys[family])
                self.assertTrue(v2_keys[family].startswith(CUSTOM_IMAP_V2_KEY_PREFIX))
        self.assertNotEqual(
            self.legacy._current_key_and_value(legacy_scope, 1)[0],
            self.v2._current_key_and_value(v2_scope, 1)[0],
        )
        legacy_index = index_scope(provider="custom_imap")
        v2_index = index_scope(provider="custom_imap")
        legacy_index_keys = self.legacy._new_inbound_index_keys(legacy_index)
        v2_index_keys = self.v2._new_inbound_index_keys(v2_index)
        for family in ("records", "occurrences", "freshness"):
            with self.subTest(family=family):
                self.assertNotEqual(legacy_index_keys[family], v2_index_keys[family])
                self.assertTrue(
                    v2_index_keys[family].startswith(CUSTOM_IMAP_V2_KEY_PREFIX)
                )
        self.assertNotEqual(
            self.legacy._new_inbound_dismissal_key(
                legacy_index,
                conversation_id=legacy_scope.conversation_id,
                latest_turn_id=legacy_scope.latest_turn_id,
            ),
            self.v2._new_inbound_dismissal_key(
                v2_index,
                conversation_id=v2_scope.conversation_id,
                latest_turn_id=v2_scope.latest_turn_id,
            ),
        )

    def test_custom_imap_v2_is_account_scoped_while_legacy_remains_unchanged(self):
        cache_scope = custom_imap_scope()
        other_v2 = SemanticAssessmentStore(
            self.redis,
            hmac_secret=SECRET,
            mode=SEMANTIC_STORE_MODE_CUSTOM_IMAP_V2,
            mailbox_account_identity="other@example.com",
        )
        self.assertNotEqual(
            self.v2._keys(cache_scope)["result"],
            other_v2._keys(cache_scope)["result"],
        )
        self.assertNotEqual(
            self.v2._current_key_and_value(cache_scope, 1)[0],
            other_v2._current_key_and_value(cache_scope, 1)[0],
        )
        legacy_with_account = SemanticAssessmentStore(
            self.redis,
            hmac_secret=SECRET,
            mailbox_account_identity="ignored@example.com",
        )
        legacy_scope = custom_imap_scope(
            conversation_id="imap:rfc:conversation-1",
            semantic_version=SEMANTIC_SCHEMA_VERSION,
        )
        self.assertEqual(
            self.legacy._keys(legacy_scope),
            legacy_with_account._keys(legacy_scope),
        )
        self.assertEqual(
            self.legacy._current_key_and_value(legacy_scope, 1),
            legacy_with_account._current_key_and_value(legacy_scope, 1),
        )

    def test_custom_imap_v2_provider_version_and_authority_fail_closed(self):
        with self.assertRaises(ValueError):
            SemanticAssessmentStore(
                self.redis,
                hmac_secret=SECRET,
                mode=SEMANTIC_STORE_MODE_CUSTOM_IMAP_V2,
            )
        accepted = ("imap:v2:rfc:abc", "imap:v2:uid:1:2")
        for conversation_id in accepted:
            with self.subTest(accepted=conversation_id):
                self.assertIn("result", self.v2._keys(custom_imap_scope(
                    conversation_id=conversation_id
                )))
        rejected = (
            "imap:rfc:abc",
            "imap:uid:1:2",
            "thread:gmail",
            "",
            "imap:v2:rfc:" + "x" * 1_024,
        )
        for conversation_id in rejected:
            with self.subTest(rejected=conversation_id):
                with self.assertRaises(ValueError):
                    self.v2._keys(custom_imap_scope(
                        conversation_id=conversation_id
                    ))
        with self.assertRaises(ValueError):
            self.v2._keys(replace(custom_imap_scope(), provider="google"))
        with self.assertRaises(ValueError):
            self.v2._keys(replace(
                custom_imap_scope(),
                semantic_version=SEMANTIC_SCHEMA_VERSION,
            ))
        with self.assertRaises(ValueError):
            semantic_schema_version_for_provider("google", custom_imap_v2=True)
        with self.assertRaises(ValueError):
            self.v2._new_inbound_index_keys(index_scope(provider="google"))
        with self.assertRaises(ValueError):
            self.v2._new_inbound_index_keys(index_scope(
                provider="custom_imap",
                mailbox_account_identity="other@example.com",
            ))
        with self.assertRaises(ValueError):
            self.v2._new_inbound_dismissal_key(
                index_scope(provider="custom_imap"),
                conversation_id="imap:rfc:legacy",
                latest_turn_id="turn-1",
            )
        self.assertEqual(
            semantic_schema_version_for_provider(
                "custom_imap",
                custom_imap_v2=True,
            ),
            CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
        )

    def test_custom_imap_v2_result_reads_and_corruption_are_isolated(self):
        legacy_scope = custom_imap_scope(
            conversation_id="imap:rfc:conversation-1",
            semantic_version=SEMANTIC_SCHEMA_VERSION,
        )
        v2_scope = custom_imap_scope()
        self.legacy.set_current_exact(legacy_scope, occurred_at=10_000)
        legacy_lease = self.legacy.try_acquire_lease(
            legacy_scope,
            random_bytes=lambda length: bytes([2]) * length,
        )
        self.assertTrue(self.legacy.commit_result_if_lease_owned(
            legacy_scope,
            lease_token=legacy_lease,
            assessment=ASSESSMENT,
            input_hash="b" * 64,
            occurred_at=10_000,
            assessed_at=30,
        ))
        self.assertIsNone(self.v2.get_result_for_exact_scope(v2_scope))
        self.v2.set_current_exact(v2_scope, occurred_at=10_000)
        v2_lease = self.v2.try_acquire_lease(
            v2_scope,
            random_bytes=lambda length: bytes([3]) * length,
        )
        self.assertTrue(self.v2.commit_result_if_lease_owned(
            v2_scope,
            lease_token=v2_lease,
            assessment=ASSESSMENT,
            input_hash="b" * 64,
            occurred_at=10_000,
            assessed_at=30,
        ))
        v2_only_scope = custom_imap_scope(
            conversation_id="imap:v2:uid:isolated:2",
            latest_turn_id="turn-2",
        )
        self.v2.set_current_exact(v2_only_scope, occurred_at=11_000)
        v2_only_lease = self.v2.try_acquire_lease(
            v2_only_scope,
            random_bytes=lambda length: bytes([4]) * length,
        )
        self.assertTrue(self.v2.commit_result_if_lease_owned(
            v2_only_scope,
            lease_token=v2_only_lease,
            assessment=ASSESSMENT,
            input_hash="c" * 64,
            occurred_at=11_000,
            assessed_at=31,
        ))
        self.assertIsNone(self.legacy.get_result_for_exact_scope(replace(
            v2_only_scope,
            semantic_version=SEMANTIC_SCHEMA_VERSION,
        )))
        legacy_result_key = self.legacy._keys(legacy_scope)["result"]
        v2_result_key = self.v2._keys(v2_scope)["result"]
        legacy_bytes = self.redis.values[legacy_result_key]
        v2_bytes = self.redis.values[v2_result_key]
        self.assertIsNotNone(self.legacy.get_result_for_exact_scope(legacy_scope))
        self.assertIsNotNone(self.v2.get_result_for_exact_scope(v2_scope))
        self.redis.values[legacy_result_key] = "corrupt"
        self.assertIsNotNone(self.v2.get_result_for_exact_scope(v2_scope))
        self.redis.values[legacy_result_key] = legacy_bytes
        self.redis.values[v2_result_key] = "corrupt"
        self.assertIsNotNone(self.legacy.get_result_for_exact_scope(legacy_scope))
        self.assertNotEqual(legacy_bytes, v2_bytes)

    def test_custom_imap_v2_index_corruption_and_capacity_are_isolated(self):
        legacy_index = index_scope(provider="custom_imap")
        v2_index = index_scope(provider="custom_imap")
        legacy_keys = self.legacy._new_inbound_index_keys(legacy_index)
        v2_keys = self.v2._new_inbound_index_keys(v2_index)
        self.redis.values[legacy_keys["freshness"]] = "wrong-type"
        self.assertEqual(
            self.v2.read_new_inbound_index(
                v2_index,
                semantic_version=CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
                model_version="test-model",
            ),
            (),
        )
        self.redis.values.pop(legacy_keys["freshness"])
        self.redis.values[v2_keys["freshness"]] = "wrong-type"
        self.assertEqual(
            self.legacy.read_new_inbound_index(
                legacy_index,
                semantic_version=SEMANTIC_SCHEMA_VERSION,
                model_version="test-model",
            ),
            (),
        )
        self.assertEqual(NEW_INBOUND_INDEX_MAX_RECORDS, 64)
        self.assertEqual(NEW_INBOUND_INDEX_TTL_SECONDS, RESULT_TTL_SECONDS)
        self.assertEqual(NEW_INBOUND_DISMISSAL_TTL_SECONDS, RESULT_TTL_SECONDS)
        self.assertEqual(NEGATIVE_TTL_SECONDS, 300)
        self.assertEqual(LEASE_TTL_SECONDS, 60)
        self.assertEqual(ATTEMPT_WINDOW_SECONDS, 86_400)


class CustomImapCompatibilityUnitTests(unittest.TestCase):
    def test_locator_digest_binds_every_trusted_component_and_only_custom_imap(self):
        original = compatibility_locator()
        digest = derive_custom_imap_compatibility_locator_digest(SECRET, original)
        changes = {
            "workspace_id": _account("wsp_", 9),
            "user_id": _account("usr_", 9),
            "mailbox_id": "mailbox-2",
            "mailbox_account_identity": "other@example.com",
            "provider_folder": "Archive",
            "uid_validity": "8",
            "imap_uid": "12",
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                changed = replace(original, **{field: value})
                self.assertNotEqual(
                    digest,
                    derive_custom_imap_compatibility_locator_digest(
                        SECRET,
                        changed,
                    ),
                )
        self.assertIn(b"\x00custom_imap\x00", original.canonical_bytes())
        with self.assertRaises(ValueError):
            replace(original, provider="google")
        self.assertEqual(
            replace(original, imap_uid="4294967295").imap_uid,
            "4294967295",
        )
        for invalid_number in ("0", "01", "4294967296", "not-a-number"):
            with self.subTest(invalid_number=invalid_number):
                with self.assertRaises(ValueError):
                    replace(original, imap_uid=invalid_number)
        self.assertEqual(
            len(replace(original, provider_folder="f" * 16_384).provider_folder),
            16_384,
        )
        with self.assertRaises(ValueError):
            replace(original, provider_folder="f" * 16_385)

    def test_sidecar_is_strict_content_free_bounded_and_mac_protected(self):
        locator = compatibility_locator()
        encoded = store_module._encode_custom_imap_compatibility_sidecar(
            SECRET,
            locator,
            compatibility_scope(),
            validated_at=1_000,
        )
        self.assertLessEqual(
            len(encoded.encode("utf-8")),
            CUSTOM_IMAP_COMPATIBILITY_MAX_SERIALIZED_BYTES,
        )
        record = store_module._decode_custom_imap_compatibility_sidecar(
            encoded,
            secret=SECRET,
            locator=locator,
        )
        self.assertIsNotNone(record)
        self.assertEqual(
            set(record),
            {
                "schemaVersion",
                "scopeDigest",
                "locatorDigest",
                "legacyConversationId",
                "legacyLatestTurnId",
                "legacySemanticVersion",
                "legacyModelVersion",
                "validatedAt",
                "expiresAt",
                "mappingMac",
                "recordMac",
            },
        )
        self.assertTrue(
            set(record).isdisjoint(
                {
                    "body",
                    "html",
                    "subject",
                    "snippet",
                    "sender",
                    "recipient",
                    "accountEmail",
                    "modelInput",
                    "modelOutput",
                    "headers",
                }
            )
        )
        extra = {**record, "subject": "must not persist"}
        self.assertIsNone(
            store_module._decode_custom_imap_compatibility_sidecar(
                json.dumps(extra),
                secret=SECRET,
                locator=locator,
            )
        )
        mapping_tamper = {**record, "mappingMac": "0" * 40}
        self.assertIsNone(
            store_module._decode_custom_imap_compatibility_sidecar(
                json.dumps(mapping_tamper),
                secret=SECRET,
                locator=locator,
            )
        )
        record_tamper = {**record, "recordMac": "0" * 40}
        self.assertIsNone(
            store_module._decode_custom_imap_compatibility_sidecar(
                json.dumps(record_tamper),
                secret=SECRET,
                locator=locator,
            )
        )

    def test_mapping_identity_is_immutable_while_provenance_is_refreshable(self):
        locator = compatibility_locator()
        base = compatibility_scope()
        first = json.loads(store_module._encode_custom_imap_compatibility_sidecar(
            SECRET,
            locator,
            base,
            validated_at=1_000,
        ))
        refreshed = json.loads(store_module._encode_custom_imap_compatibility_sidecar(
            SECRET,
            locator,
            replace(base, model_version="new-model"),
            validated_at=2_000,
        ))
        semantic_refresh = json.loads(
            store_module._encode_custom_imap_compatibility_sidecar(
                SECRET,
                locator,
                base,
                validated_at=3_000,
            )
        )
        conversation_change = json.loads(
            store_module._encode_custom_imap_compatibility_sidecar(
                SECRET,
                locator,
                replace(base, conversation_id="thread:mailbox-1|imap:rfc:other"),
                validated_at=1_000,
            )
        )
        turn_change = json.loads(store_module._encode_custom_imap_compatibility_sidecar(
            SECRET,
            locator,
            replace(base, latest_turn_id="turn-2"),
            validated_at=1_000,
        ))
        self.assertEqual(first["mappingMac"], refreshed["mappingMac"])
        self.assertEqual(first["mappingMac"], semantic_refresh["mappingMac"])
        self.assertNotEqual(first["recordMac"], refreshed["recordMac"])
        self.assertNotEqual(first["mappingMac"], conversation_change["mappingMac"])
        self.assertNotEqual(first["mappingMac"], turn_change["mappingMac"])
        self.assertEqual(
            first["expiresAt"] - first["validatedAt"],
            CUSTOM_IMAP_COMPATIBILITY_TTL_SECONDS,
        )
        oversized = replace(
            base,
            conversation_id="c" * NEW_INBOUND_INDEX_MAX_CONVERSATION_ID_CHARACTERS,
            latest_turn_id="t" * NEW_INBOUND_INDEX_MAX_TURN_ID_CHARACTERS,
            model_version="m" * store_module.NEW_INBOUND_INDEX_MAX_MODEL_CHARACTERS,
        )
        with self.assertRaises(ValueError):
            store_module._encode_custom_imap_compatibility_sidecar(
                SECRET,
                locator,
                oversized,
                validated_at=1_000,
            )

    def test_marker_schema_reasons_stickiness_and_typed_outcomes_are_finite(self):
        locator = compatibility_locator()
        keys = store_module._custom_imap_compatibility_keys(SECRET, locator)
        for reason in store_module._CUSTOM_IMAP_COMPATIBILITY_MARKER_REASONS:
            marker = {
                "schemaVersion": store_module.CUSTOM_IMAP_COMPATIBILITY_SCHEMA_VERSION,
                "scopeDigest": keys["scope_digest"],
                "locatorDigest": keys["locator_digest"],
                "reason": reason,
                "validatedAt": 1_000,
                "expiresAt": 1_000 + CUSTOM_IMAP_COMPATIBILITY_TTL_SECONDS,
            }
            marker["recordMac"] = store_module._custom_imap_compatibility_marker_mac(
                SECRET,
                marker,
            )
            self.assertIsNotNone(
                store_module._decode_custom_imap_compatibility_marker(
                    json.dumps(marker),
                    secret=SECRET,
                    locator=locator,
                )
            )
        invalid = {**marker, "reason": "arbitrary_detail"}
        invalid["recordMac"] = store_module._custom_imap_compatibility_marker_mac(
            SECRET,
            invalid,
        )
        self.assertIsNone(
            store_module._decode_custom_imap_compatibility_marker(
                json.dumps(invalid),
                secret=SECRET,
                locator=locator,
            )
        )
        self.assertEqual(
            store_module._CUSTOM_IMAP_COMPATIBILITY_STICKY_REASONS,
            {
                "mapping_conflict",
                "sidecar_corrupt",
                "marker_corrupt",
                "record_too_large",
            },
        )
        self.assertNotIn(
            "sidecar_unavailable",
            store_module._CUSTOM_IMAP_COMPATIBILITY_STICKY_REASONS,
        )
        self.assertEqual(
            {outcome.value for outcome in CustomImapCompatibilityOutcome},
            {
                "sidecar_written",
                "sidecar_renewed",
                "sidecar_conflict",
                "compatibility_incomplete",
            },
        )
        outcomes: list[int] = [1, 2, 3, 4]
        outcome_store = SemanticAssessmentStore(
            lambda _command: {"result": outcomes.pop(0)},
            hmac_secret=SECRET,
        )
        expected = tuple(CustomImapCompatibilityOutcome)
        actual = tuple(
            outcome_store.record_custom_imap_v1_compatibility_mapping(
                locator,
                compatibility_scope(),
            )
            for _ in expected
        )
        self.assertEqual(actual, expected)

    def test_dismissal_bridge_contract_is_typed_isolated_and_finite(self):
        legacy = SemanticAssessmentStore(MemoryRedis(), hmac_secret=SECRET)
        google_scope = index_scope()
        google_key = legacy._new_inbound_dismissal_key(
            google_scope,
            conversation_id="conversation-1",
            latest_turn_id="turn-1",
        )
        self.assertEqual(store_module._NEW_INBOUND_DISMISSAL_VALUE, "1")
        self.assertEqual(NEW_INBOUND_DISMISSAL_TTL_SECONDS, 2_592_000)
        self.assertEqual(
            google_key,
            "cuevion:priority:semantic:v1:new-inbound-dismissal:"
            "164505ccf4bf3b685ac0c5447abc524da916c4502e26f6c2cac1b956b6f773ce",
        )
        self.assertEqual(
            store_module._CUSTOM_IMAP_V2_NATIVE_DISMISSAL_VALUE,
            "native_v2",
        )
        self.assertEqual(
            store_module._CUSTOM_IMAP_V2_BRIDGED_DISMISSAL_VALUE,
            "bridged_v1",
        )
        self.assertEqual(
            {outcome.value for outcome in CustomImapDismissalBridgeOutcome},
            {
                "bridged",
                "already_bridged",
                "native_v2_present",
                "legacy_not_dismissed",
                "sidecar_unavailable",
                "compatibility_incomplete",
                "claim_stale",
                "corrupt_state",
            },
        )
        parameters = tuple(inspect.signature(
            SemanticAssessmentStore.bridge_custom_imap_v1_dismissal_to_v2
        ).parameters)
        self.assertEqual(parameters, ("self", "locator", "v2_authority"))

    def test_v2_dismissal_values_and_authority_validation_fail_closed(self):
        redis = MemoryRedis()
        v2 = SemanticAssessmentStore(
            redis,
            hmac_secret=SECRET,
            mode=SEMANTIC_STORE_MODE_CUSTOM_IMAP_V2,
            mailbox_account_identity="primary@example.com",
        )
        authority = v2_dismissal_authority()
        key = v2._new_inbound_dismissal_key(
            authority.to_index_scope(),
            conversation_id=authority.conversation_id,
            latest_turn_id=authority.latest_turn_id,
        )
        for value in ("native_v2", "bridged_v1"):
            with self.subTest(value=value):
                redis.values[key] = value
                redis.expirations[key] = 100
                self.assertTrue(v2.is_new_inbound_dismissed_exact(
                    authority.to_index_scope(),
                    conversation_id=authority.conversation_id,
                    latest_turn_id=authority.latest_turn_id,
                ))
        redis.values[key] = "unknown"
        with self.assertRaises(SemanticStoreUnavailable):
            v2.is_new_inbound_dismissed_exact(
                authority.to_index_scope(),
                conversation_id=authority.conversation_id,
                latest_turn_id=authority.latest_turn_id,
            )
        for conversation_id in ("imap:v2:rfc:root", "imap:v2:uid:7:11"):
            self.assertEqual(
                v2_dismissal_authority(
                    conversation_id=conversation_id
                ).conversation_id,
                conversation_id,
            )
        for changes in (
            {"provider": "google"},
            {"conversation_id": "imap:rfc:legacy"},
        ):
            with self.assertRaises(ValueError):
                v2_dismissal_authority(**changes)

    def test_bridge_derives_legacy_authority_only_from_validated_sidecar(self):
        locator = compatibility_locator()
        legacy_scope = compatibility_scope()
        encoded = store_module._encode_custom_imap_compatibility_sidecar(
            SECRET,
            locator,
            legacy_scope,
            validated_at=1_000,
        )
        commands: list[list[object]] = []

        def transport(command: list[object]) -> dict[str, object]:
            commands.append(command)
            if command[1] == store_module._PREPARE_CUSTOM_IMAP_DISMISSAL_BRIDGE_SCRIPT:
                return {"result": [1, encoded]}
            return {"result": 4}

        bridge_store = SemanticAssessmentStore(transport, hmac_secret=SECRET)
        self.assertEqual(
            bridge_store.bridge_custom_imap_v1_dismissal_to_v2(
                locator,
                v2_dismissal_authority(),
            ),
            CustomImapDismissalBridgeOutcome.LEGACY_NOT_DISMISSED,
        )
        expected_legacy_key = bridge_store._new_inbound_dismissal_key(
            NewInboundIndexScope(
                workspace_id=locator.workspace_id,
                user_id=locator.user_id,
                mailbox_id=locator.mailbox_id,
                provider=locator.provider,
                mailbox_account_identity=locator.mailbox_account_identity,
            ),
            conversation_id=legacy_scope.conversation_id,
            latest_turn_id=legacy_scope.latest_turn_id,
        )
        self.assertEqual(commands[1][5], expected_legacy_key)
        self.assertEqual(commands[1][10], json.loads(encoded)["mappingMac"])
        self.assertTrue({
            "legacy_conversation_id",
            "legacy_latest_turn_id",
            "legacy_dismissal_key",
            "raw_sidecar",
        }.isdisjoint(inspect.signature(
            SemanticAssessmentStore.bridge_custom_imap_v1_dismissal_to_v2
        ).parameters))
        with self.assertRaises(ValueError):
            bridge_store.bridge_custom_imap_v1_dismissal_to_v2(
                locator,
                v2_dismissal_authority(mailbox_account_identity="other@example.com"),
            )

    def test_bridge_mapping_claim_ignores_provenance_but_changes_with_mapping(self):
        locator = compatibility_locator()
        base = compatibility_scope()
        first = json.loads(store_module._encode_custom_imap_compatibility_sidecar(
            SECRET,
            locator,
            base,
            validated_at=1_000,
        ))
        renewed = json.loads(store_module._encode_custom_imap_compatibility_sidecar(
            SECRET,
            locator,
            replace(base, model_version="renewed-model"),
            validated_at=2_000,
        ))
        changed = json.loads(store_module._encode_custom_imap_compatibility_sidecar(
            SECRET,
            locator,
            replace(base, latest_turn_id="changed-turn"),
            validated_at=2_000,
        ))
        self.assertEqual(first["mappingMac"], renewed["mappingMac"])
        self.assertNotEqual(first["recordMac"], renewed["recordMac"])
        self.assertNotEqual(first["mappingMac"], changed["mappingMac"])

    def test_dismissal_bridge_has_no_production_caller(self):
        priority_directory = Path(__file__).resolve().parent
        for production_file in (
            priority_directory / "semantic_route.py",
            priority_directory / "semantic-assessment.py",
        ):
            source = production_file.read_text(encoding="utf-8")
            self.assertNotIn("bridge_custom_imap_v1_dismissal_to_v2", source)
            self.assertNotIn("native_v2", source)
            self.assertNotIn("bridged_v1", source)


if __name__ == "__main__":
    unittest.main()
