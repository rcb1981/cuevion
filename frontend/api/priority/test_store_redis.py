from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace

from .semantic_types import (
    CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    SemanticAssessment,
    SemanticReasonCode,
    SemanticState,
)
from .store import (
    CUSTOM_IMAP_V2_KEY_PREFIX,
    NEW_INBOUND_INDEX_MAX_RECORDS,
    SEMANTIC_STORE_MODE_CUSTOM_IMAP_V2,
    NewInboundIndexScope,
    SemanticAssessmentStore,
    SemanticCacheScope,
)


SECRET = "priority-real-redis-test-secret-more-than-thirty-two-bytes"
ACCOUNT = "primary@example.com"
ASSESSMENT = SemanticAssessment(
    state=SemanticState.NEEDS_USER_ACTION,
    confidence=0.99,
    reason_code=SemanticReasonCode.EXPLICIT_REQUEST,
)


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _cache_scope(
    conversation_id: str,
    latest_turn_id: str,
    semantic_version: str,
    *,
    provider: str = "custom_imap",
) -> SemanticCacheScope:
    return SemanticCacheScope(
        workspace_id="wsp_real_redis",
        user_id="usr_real_redis",
        mailbox_id="mailbox-real-redis",
        provider=provider,
        conversation_id=conversation_id,
        latest_turn_id=latest_turn_id,
        semantic_version=semantic_version,
        model_version="test-model",
    )


def _index_scope(*, provider: str = "custom_imap") -> NewInboundIndexScope:
    return NewInboundIndexScope(
        workspace_id="wsp_real_redis",
        user_id="usr_real_redis",
        mailbox_id="mailbox-real-redis",
        provider=provider,
        mailbox_account_identity=ACCOUNT,
    )


@unittest.skipUnless(
    shutil.which("redis-server") and shutil.which("redis-cli"),
    "disposable Redis executables are unavailable",
)
class SemanticStoreRealRedisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary_directory = tempfile.TemporaryDirectory(
            prefix="cuevion-semantic-redis-"
        )
        cls._port = _available_port()
        cls._process = subprocess.Popen(
            [
                "redis-server",
                "--port",
                str(cls._port),
                "--bind",
                "127.0.0.1",
                "--protected-mode",
                "yes",
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                cls._temporary_directory.name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _attempt in range(100):
            completed = subprocess.run(
                ["redis-cli", "-p", str(cls._port), "PING"],
                capture_output=True,
                text=True,
            )
            if completed.returncode == 0 and completed.stdout.strip() == "PONG":
                break
            if cls._process.poll() is not None:
                raise RuntimeError("disposable Redis did not start")
            time.sleep(0.05)
        else:
            raise RuntimeError("disposable Redis startup timed out")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._process.terminate()
        try:
            cls._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls._process.kill()
            cls._process.wait(timeout=5)
        cls._temporary_directory.cleanup()

    def setUp(self) -> None:
        self.assertEqual(self._transport(["FLUSHDB"])["result"], "OK")
        self.legacy = SemanticAssessmentStore(self._transport, hmac_secret=SECRET)
        self.v2 = SemanticAssessmentStore(
            self._transport,
            hmac_secret=SECRET,
            mode=SEMANTIC_STORE_MODE_CUSTOM_IMAP_V2,
            mailbox_account_identity=ACCOUNT,
        )

    def _transport(self, command: list[object]) -> dict[str, object]:
        completed = subprocess.run(
            [
                "redis-cli",
                "-p",
                str(self._port),
                "--json",
                *(str(value) for value in command),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return {"result": json.loads(completed.stdout)}

    def _commit(
        self,
        store: SemanticAssessmentStore,
        cache_scope: SemanticCacheScope,
        *,
        occurred_at: int,
        assessed_at: int,
        indexed: bool = False,
        token_byte: int = 1,
    ) -> None:
        store.set_current_exact(cache_scope, occurred_at=occurred_at)
        lease = store.try_acquire_lease(
            cache_scope,
            random_bytes=lambda length: bytes([token_byte]) * length,
        )
        self.assertIsNotNone(lease)
        self.assertTrue(
            store.commit_result_if_lease_owned(
                cache_scope,
                lease_token=lease,
                assessment=ASSESSMENT,
                input_hash="a" * 64,
                occurred_at=occurred_at,
                assessed_at=assessed_at,
                index_new_inbound=indexed,
                new_inbound_mailbox_account_identity=ACCOUNT if indexed else None,
            )
        )

    def test_results_and_current_pointers_coexist_without_cross_mutation(self):
        legacy_scope = _cache_scope(
            "imap:rfc:coexist",
            "turn-1",
            SEMANTIC_SCHEMA_VERSION,
        )
        v2_scope = _cache_scope(
            "imap:v2:rfc:coexist",
            "turn-1",
            CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
        )
        self._commit(self.v2, v2_scope, occurred_at=10, assessed_at=20, token_byte=2)
        v2_result_key = self.v2._keys(v2_scope)["result"]
        v2_pointer_key = self.v2._current_key_and_value(v2_scope, 10)[0]
        v2_result_bytes = self._transport(["GET", v2_result_key])["result"]
        v2_pointer_bytes = self._transport(["GET", v2_pointer_key])["result"]
        self._commit(self.legacy, legacy_scope, occurred_at=10, assessed_at=20)
        self.assertEqual(self._transport(["GET", v2_result_key])["result"], v2_result_bytes)
        self.assertEqual(self._transport(["GET", v2_pointer_key])["result"], v2_pointer_bytes)
        legacy_result_key = self.legacy._keys(legacy_scope)["result"]
        legacy_pointer_key = self.legacy._current_key_and_value(legacy_scope, 10)[0]
        legacy_result_bytes = self._transport(["GET", legacy_result_key])["result"]
        legacy_pointer_bytes = self._transport(["GET", legacy_pointer_key])["result"]
        self.v2.set_negative(v2_scope, "provider_timeout")
        self.assertEqual(
            self._transport(["GET", legacy_result_key])["result"],
            legacy_result_bytes,
        )
        self.assertEqual(
            self._transport(["GET", legacy_pointer_key])["result"],
            legacy_pointer_bytes,
        )

    def test_result_and_pointer_corruption_is_contained_by_namespace(self):
        legacy_scope = _cache_scope(
            "imap:rfc:corrupt",
            "turn-1",
            SEMANTIC_SCHEMA_VERSION,
        )
        v2_scope = _cache_scope(
            "imap:v2:rfc:corrupt",
            "turn-1",
            CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
        )
        self._commit(self.legacy, legacy_scope, occurred_at=10, assessed_at=20)
        self._commit(self.v2, v2_scope, occurred_at=10, assessed_at=20, token_byte=2)
        legacy_result = self.legacy._keys(legacy_scope)["result"]
        v2_result = self.v2._keys(v2_scope)["result"]
        v2_result_bytes = self._transport(["GET", v2_result])["result"]
        self.assertEqual(self._transport(["SET", legacy_result, "corrupt"])["result"], "OK")
        self.assertIsNotNone(self.v2.get_result_for_exact_scope(v2_scope))
        self._commit(self.legacy, legacy_scope, occurred_at=10, assessed_at=20, token_byte=3)
        self.assertEqual(self._transport(["SET", v2_result, "corrupt"])["result"], "OK")
        self.assertIsNotNone(self.legacy.get_result_for_exact_scope(legacy_scope))
        self.assertEqual(
            self._transport(["SET", v2_result, v2_result_bytes])["result"],
            "OK",
        )
        legacy_pointer = self.legacy._current_key_and_value(legacy_scope, 10)[0]
        v2_pointer = self.v2._current_key_and_value(v2_scope, 10)[0]
        self.assertEqual(self._transport(["SET", legacy_pointer, "corrupt"])["result"], "OK")
        self.v2.set_current_exact(v2_scope, occurred_at=10)
        current, _ = self.v2.get_result_if_current(
            v2_scope,
            input_hash="a" * 64,
            occurred_at=10,
        )
        self.assertTrue(current)
        self.assertEqual(self._transport(["SET", v2_pointer, "corrupt"])["result"], "OK")
        self.legacy.set_current_exact(legacy_scope, occurred_at=10)
        current, _ = self.legacy.get_result_if_current(
            legacy_scope,
            input_hash="a" * 64,
            occurred_at=10,
        )
        self.assertTrue(current)

    def test_indexes_and_dismissals_coexist_and_corruption_is_contained(self):
        legacy_scope = _cache_scope(
            "imap:rfc:index",
            "turn-1",
            SEMANTIC_SCHEMA_VERSION,
        )
        v2_scope = _cache_scope(
            "imap:v2:rfc:index",
            "turn-1",
            CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
        )
        self._commit(
            self.legacy,
            legacy_scope,
            occurred_at=10,
            assessed_at=20,
            indexed=True,
        )
        self._commit(
            self.v2,
            v2_scope,
            occurred_at=10,
            assessed_at=20,
            indexed=True,
            token_byte=2,
        )
        legacy_index = _index_scope()
        v2_index = _index_scope()
        self.assertEqual(len(self.legacy.read_new_inbound_index(
            legacy_index,
            semantic_version=SEMANTIC_SCHEMA_VERSION,
            model_version="test-model",
        )), 1)
        self.assertEqual(len(self.v2.read_new_inbound_index(
            v2_index,
            semantic_version=CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
            model_version="test-model",
        )), 1)
        self.assertTrue(self.legacy.dismiss_new_inbound_exact(
            legacy_index,
            conversation_id=legacy_scope.conversation_id,
            latest_turn_id=legacy_scope.latest_turn_id,
            semantic_version=SEMANTIC_SCHEMA_VERSION,
            current=20,
        ))
        self.assertTrue(self.v2.dismiss_new_inbound_exact(
            v2_index,
            conversation_id=v2_scope.conversation_id,
            latest_turn_id=v2_scope.latest_turn_id,
            semantic_version=CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
            current=20,
        ))
        self.assertTrue(self.legacy.is_new_inbound_dismissed_exact(
            legacy_index,
            conversation_id=legacy_scope.conversation_id,
            latest_turn_id=legacy_scope.latest_turn_id,
        ))
        self.assertTrue(self.v2.is_new_inbound_dismissed_exact(
            v2_index,
            conversation_id=v2_scope.conversation_id,
            latest_turn_id=v2_scope.latest_turn_id,
        ))
        legacy_keys = self.legacy._new_inbound_index_keys(legacy_index)
        v2_keys = self.v2._new_inbound_index_keys(v2_index)
        legacy_members = self._transport(
            ["ZREVRANGE", legacy_keys["freshness"], 0, -1]
        )["result"]
        self.assertEqual(len(legacy_members), 1)
        legacy_freshness = self._transport(
            ["ZSCORE", legacy_keys["freshness"], legacy_members[0]]
        )["result"]
        self.assertEqual(
            self._transport(["SET", legacy_keys["freshness"], "wrong-type"])["result"],
            "OK",
        )
        self.assertEqual(len(self.v2.read_new_inbound_index(
            v2_index,
            semantic_version=CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
            model_version="test-model",
        )), 1)
        self.assertEqual(self._transport(["DEL", legacy_keys["freshness"]])["result"], 1)
        self.assertEqual(
            self._transport(
                [
                    "ZADD",
                    legacy_keys["freshness"],
                    legacy_freshness,
                    legacy_members[0],
                ]
            )["result"],
            1,
        )
        self.assertEqual(
            self._transport(["SET", v2_keys["freshness"], "wrong-type"])["result"],
            "OK",
        )
        self.assertEqual(len(self.legacy.read_new_inbound_index(
            legacy_index,
            semantic_version=SEMANTIC_SCHEMA_VERSION,
            model_version="test-model",
        )), 1)

    def test_index_capacities_are_independent(self):
        legacy_index = _index_scope()
        v2_index = _index_scope()
        for number in range(NEW_INBOUND_INDEX_MAX_RECORDS + 1):
            self._commit(
                self.legacy,
                _cache_scope(
                    f"imap:rfc:capacity-{number}",
                    f"legacy-turn-{number}",
                    SEMANTIC_SCHEMA_VERSION,
                ),
                occurred_at=number + 1,
                assessed_at=number + 1_000,
                indexed=True,
                token_byte=(number % 250) + 1,
            )
        for number in range(3):
            self._commit(
                self.v2,
                _cache_scope(
                    f"imap:v2:uid:capacity:{number}",
                    f"v2-turn-{number}",
                    CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
                ),
                occurred_at=number + 1,
                assessed_at=number + 2_000,
                indexed=True,
                token_byte=number + 1,
            )
        self.assertEqual(len(self.legacy.read_new_inbound_index(
            legacy_index,
            semantic_version=SEMANTIC_SCHEMA_VERSION,
            model_version="test-model",
        )), NEW_INBOUND_INDEX_MAX_RECORDS)
        self.assertEqual(len(self.v2.read_new_inbound_index(
            v2_index,
            semantic_version=CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
            model_version="test-model",
        )), 3)
        for number in range(3, NEW_INBOUND_INDEX_MAX_RECORDS + 1):
            self._commit(
                self.v2,
                _cache_scope(
                    f"imap:v2:uid:capacity:{number}",
                    f"v2-turn-{number}",
                    CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
                ),
                occurred_at=number + 1,
                assessed_at=number + 2_000,
                indexed=True,
                token_byte=(number % 250) + 1,
            )
        self.assertEqual(len(self.v2.read_new_inbound_index(
            v2_index,
            semantic_version=CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
            model_version="test-model",
        )), NEW_INBOUND_INDEX_MAX_RECORDS)
        self.assertEqual(len(self.legacy.read_new_inbound_index(
            legacy_index,
            semantic_version=SEMANTIC_SCHEMA_VERSION,
            model_version="test-model",
        )), NEW_INBOUND_INDEX_MAX_RECORDS)

    def test_rollback_legacy_reader_and_gmail_ignore_v2_namespace(self):
        v2_scope = _cache_scope(
            "imap:v2:rfc:rollback",
            "turn-1",
            CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
        )
        self._commit(
            self.v2,
            v2_scope,
            occurred_at=10,
            assessed_at=20,
            indexed=True,
        )
        v2_index = _index_scope()
        self.assertTrue(self.v2.dismiss_new_inbound_exact(
            v2_index,
            conversation_id=v2_scope.conversation_id,
            latest_turn_id=v2_scope.latest_turn_id,
            semantic_version=CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
            current=20,
        ))
        v2_keys = self._transport(["KEYS", f"{CUSTOM_IMAP_V2_KEY_PREFIX}*"])["result"]
        self.assertGreaterEqual(len(v2_keys), 6)
        legacy_equivalent = replace(
            v2_scope,
            semantic_version=SEMANTIC_SCHEMA_VERSION,
        )
        self.assertIsNone(self.legacy.get_result_for_exact_scope(legacy_equivalent))
        legacy_derived = {
            *self.legacy._keys(legacy_equivalent).values(),
            *self.legacy._new_inbound_index_keys(v2_index).values(),
            self.legacy._current_key_and_value(legacy_equivalent, 10)[0],
            self.legacy._new_inbound_dismissal_key(
                v2_index,
                conversation_id=v2_scope.conversation_id,
                latest_turn_id=v2_scope.latest_turn_id,
            ),
        }
        self.assertTrue(legacy_derived.isdisjoint(v2_keys))
        gmail_scope = _cache_scope(
            "thread:gmail",
            "message-1",
            SEMANTIC_SCHEMA_VERSION,
            provider="google",
        )
        gmail_index = _index_scope(provider="google")
        gmail_keys = {
            *self.legacy._keys(gmail_scope).values(),
            *self.legacy._new_inbound_index_keys(gmail_index).values(),
            self.legacy._current_key_and_value(gmail_scope, 10)[0],
            self.legacy._new_inbound_dismissal_key(
                gmail_index,
                conversation_id=gmail_scope.conversation_id,
                latest_turn_id=gmail_scope.latest_turn_id,
            ),
        }
        self.assertTrue(all(
            not key.startswith(CUSTOM_IMAP_V2_KEY_PREFIX)
            for key in gmail_keys
        ))
        with self.assertRaises(ValueError):
            self.v2._keys(gmail_scope)


if __name__ == "__main__":
    unittest.main()
