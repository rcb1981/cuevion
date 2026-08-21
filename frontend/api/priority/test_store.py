from __future__ import annotations

import base64
import unittest
from dataclasses import replace

from . import store as store_module
from .semantic_types import (
    SemanticAssessment,
    SemanticReasonCode,
    SemanticState,
)
from .store import SemanticAssessmentStore, SemanticCacheScope


def _account(prefix: str, byte: int) -> str:
    suffix = base64.urlsafe_b64encode(bytes([byte]) * 16).rstrip(b"=").decode("ascii")
    return prefix + suffix


SECRET = "priority-test-secret-with-more-than-thirty-two-bytes"


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.commands: list[list[object]] = []

    def __call__(self, command: list[object]) -> dict[str, object]:
        self.commands.append(list(command))
        operation = command[0]
        if operation == "GET":
            return {"result": self.values.get(command[1])}
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


if __name__ == "__main__":
    unittest.main()
