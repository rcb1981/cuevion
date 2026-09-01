from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from . import store as store_module
from .semantic_types import (
    CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    SemanticAssessment,
    SemanticReasonCode,
    SemanticState,
)
from .store import (
    CUSTOM_IMAP_COMPATIBILITY_KEY_PREFIX,
    CUSTOM_IMAP_COMPATIBILITY_TTL_SECONDS,
    CUSTOM_IMAP_V2_KEY_PREFIX,
    NEW_INBOUND_INDEX_MAX_RECORDS,
    SEMANTIC_STORE_MODE_CUSTOM_IMAP_V2,
    CustomImapCompatibilityOutcome,
    CustomImapDismissalBridgeOutcome,
    CustomImapV1CompatibilityLocator,
    CustomImapV2DismissalAuthority,
    NewInboundIndexScope,
    SemanticAssessmentStore,
    SemanticCacheScope,
    SemanticStoreUnavailable,
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


def _compatibility_locator(**changes: str) -> CustomImapV1CompatibilityLocator:
    values = {
        "workspace_id": "wsp_real_redis",
        "user_id": "usr_real_redis",
        "mailbox_id": "mailbox-real-redis",
        "mailbox_account_identity": ACCOUNT,
        "provider": "custom_imap",
        "provider_folder": "INBOX",
        "uid_validity": "7",
        "imap_uid": "11",
    }
    values.update(changes)
    return CustomImapV1CompatibilityLocator(**values)


def _v2_authority(**changes: str) -> CustomImapV2DismissalAuthority:
    values = {
        "workspace_id": "wsp_real_redis",
        "user_id": "usr_real_redis",
        "mailbox_id": "mailbox-real-redis",
        "mailbox_account_identity": ACCOUNT,
        "provider": "custom_imap",
        "conversation_id": "imap:v2:rfc:bridge",
        "latest_turn_id": "v2-turn-1",
    }
    values.update(changes)
    return CustomImapV2DismissalAuthority(**values)


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

    def _bridge_mapping_scope(
        self,
        suffix: str,
        *,
        model_version: str = "test-model",
    ) -> SemanticCacheScope:
        return SemanticCacheScope(
            workspace_id="wsp_real_redis",
            user_id="usr_real_redis",
            mailbox_id="mailbox-real-redis",
            provider="custom_imap",
            conversation_id=f"thread:mailbox-real-redis|imap:rfc:{suffix}",
            latest_turn_id=f"legacy-turn-{suffix}",
            semantic_version=SEMANTIC_SCHEMA_VERSION,
            model_version=model_version,
        )

    def _bridge_keys(
        self,
        legacy_scope: SemanticCacheScope,
        authority: CustomImapV2DismissalAuthority,
    ) -> tuple[str, str]:
        legacy_key = self.legacy._new_inbound_dismissal_key(
            _index_scope(),
            conversation_id=legacy_scope.conversation_id,
            latest_turn_id=legacy_scope.latest_turn_id,
        )
        v2_key = self.v2._new_inbound_dismissal_key(
            authority.to_index_scope(),
            conversation_id=authority.conversation_id,
            latest_turn_id=authority.latest_turn_id,
        )
        return legacy_key, v2_key

    def _install_bridge_authority(
        self,
        suffix: str,
        *,
        legacy_ttl_ms: int = 120_000,
        model_version: str = "test-model",
    ) -> tuple[
        CustomImapV1CompatibilityLocator,
        SemanticCacheScope,
        CustomImapV2DismissalAuthority,
        str,
        str,
    ]:
        locator = _compatibility_locator(imap_uid=str(100 + int(suffix)))
        legacy_scope = self._bridge_mapping_scope(
            suffix,
            model_version=model_version,
        )
        authority = _v2_authority(
            conversation_id=f"imap:v2:rfc:bridge-{suffix}",
            latest_turn_id=f"v2-turn-{suffix}",
        )
        self.assertEqual(
            self.legacy.record_custom_imap_v1_compatibility_mapping(
                locator,
                legacy_scope,
            ),
            CustomImapCompatibilityOutcome.SIDECAR_WRITTEN,
        )
        legacy_key, v2_key = self._bridge_keys(legacy_scope, authority)
        self.assertEqual(
            self._transport(
                ["SET", legacy_key, "1", "PX", legacy_ttl_ms]
            )["result"],
            "OK",
        )
        return locator, legacy_scope, authority, legacy_key, v2_key

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

    def test_bridge_uses_exact_legacy_expiry_and_never_extends(self):
        locator, _scope, authority, legacy_key, v2_key = (
            self._install_bridge_authority("1")
        )
        legacy_expiry = self._transport(["PEXPIRETIME", legacy_key])["result"]
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.BRIDGED,
        )
        self.assertEqual(self._transport(["GET", v2_key])["result"], "bridged_v1")
        self.assertEqual(
            self._transport(["PEXPIRETIME", v2_key])["result"],
            legacy_expiry,
        )
        first_expiry = self._transport(["PEXPIRETIME", v2_key])["result"]
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.ALREADY_BRIDGED,
        )
        self.assertEqual(
            self._transport(["PEXPIRETIME", v2_key])["result"],
            first_expiry,
        )

        self.assertEqual(
            self._transport(
                ["SET", v2_key, "bridged_v1", "PXAT", legacy_expiry + 10_000]
            )["result"],
            "OK",
        )
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.ALREADY_BRIDGED,
        )
        self.assertEqual(
            self._transport(["PEXPIRETIME", v2_key])["result"],
            legacy_expiry,
        )

        shorter_expiry = legacy_expiry - 10_000
        self.assertEqual(
            self._transport(
                ["SET", v2_key, "bridged_v1", "PXAT", shorter_expiry]
            )["result"],
            "OK",
        )
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.ALREADY_BRIDGED,
        )
        self.assertEqual(
            self._transport(["PEXPIRETIME", v2_key])["result"],
            shorter_expiry,
        )

    def test_bridge_requires_live_exact_legacy_dismissal(self):
        locator, _scope, authority, legacy_key, v2_key = (
            self._install_bridge_authority("2")
        )
        self.assertEqual(self._transport(["DEL", legacy_key])["result"], 1)
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.LEGACY_NOT_DISMISSED,
        )
        self.assertIsNone(self._transport(["GET", v2_key])["result"])

        locator, _scope, authority, legacy_key, v2_key = (
            self._install_bridge_authority("3")
        )
        self.assertEqual(self._transport(["PEXPIREAT", legacy_key, 1])["result"], 1)
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.LEGACY_NOT_DISMISSED,
        )
        self.assertIsNone(self._transport(["GET", v2_key])["result"])

        locator, _scope, authority, legacy_key, v2_key = (
            self._install_bridge_authority("4")
        )
        self.assertEqual(
            self._transport(["SET", legacy_key, "invalid", "PX", 60_000])["result"],
            "OK",
        )
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.CORRUPT_STATE,
        )
        self.assertIsNone(self._transport(["GET", v2_key])["result"])

    def test_bridge_sidecar_and_marker_states_fail_closed(self):
        missing_locator = _compatibility_locator(imap_uid="105")
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(
                missing_locator,
                _v2_authority(conversation_id="imap:v2:rfc:missing"),
            ),
            CustomImapDismissalBridgeOutcome.SIDECAR_UNAVAILABLE,
        )
        marker_locator = _compatibility_locator(imap_uid="121")
        self.legacy.record_custom_imap_v1_compatibility_unavailable(marker_locator)
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(
                marker_locator,
                _v2_authority(conversation_id="imap:v2:rfc:marked"),
            ),
            CustomImapDismissalBridgeOutcome.COMPATIBILITY_INCOMPLETE,
        )
        malformed_marker_locator = _compatibility_locator(imap_uid="122")
        malformed_marker_keys = store_module._custom_imap_compatibility_keys(
            SECRET,
            malformed_marker_locator,
        )
        self.assertEqual(
            self._transport(
                ["SET", malformed_marker_keys["incomplete"], "not-json"]
            )["result"],
            "OK",
        )
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(
                malformed_marker_locator,
                _v2_authority(conversation_id="imap:v2:rfc:bad-marker"),
            ),
            CustomImapDismissalBridgeOutcome.CORRUPT_STATE,
        )

        locator, _scope, authority, _legacy_key, v2_key = (
            self._install_bridge_authority("6")
        )
        keys = store_module._custom_imap_compatibility_keys(SECRET, locator)
        self.assertEqual(
            self._transport(["SET", keys["sidecar"], "not-json"])["result"],
            "OK",
        )
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.CORRUPT_STATE,
        )
        self.assertIsNone(self._transport(["GET", v2_key])["result"])

        locator, _scope, authority, _legacy_key, v2_key = (
            self._install_bridge_authority("7")
        )
        keys = store_module._custom_imap_compatibility_keys(SECRET, locator)
        tampered = json.loads(self._transport(["GET", keys["sidecar"]])["result"])
        tampered["mappingMac"] = "0" * 40
        self.assertEqual(
            self._transport(
                [
                    "SET",
                    keys["sidecar"],
                    json.dumps(tampered, separators=(",", ":"), sort_keys=True),
                ]
            )["result"],
            "OK",
        )
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.CORRUPT_STATE,
        )
        self.assertIsNone(self._transport(["GET", v2_key])["result"])

        locator, _scope, authority, _legacy_key, v2_key = (
            self._install_bridge_authority("8")
        )
        self.legacy.record_custom_imap_v1_compatibility_unavailable(locator)
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.COMPATIBILITY_INCOMPLETE,
        )
        self.assertIsNone(self._transport(["GET", v2_key])["result"])

        locator, scope, authority, _legacy_key, v2_key = (
            self._install_bridge_authority("9")
        )
        self.assertEqual(
            self.legacy.record_custom_imap_v1_compatibility_mapping(
                locator,
                replace(scope, latest_turn_id="conflicting-turn"),
            ),
            CustomImapCompatibilityOutcome.SIDECAR_CONFLICT,
        )
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.COMPATIBILITY_INCOMPLETE,
        )
        self.assertIsNone(self._transport(["GET", v2_key])["result"])

    def test_bridge_revalidates_marker_and_immutable_claim_after_preparation(self):
        def bridge_with_interruption(
            suffix: str,
            action,
        ) -> tuple[CustomImapDismissalBridgeOutcome, str]:
            locator, scope, authority, _legacy_key, v2_key = (
                self._install_bridge_authority(suffix)
            )
            interrupted = False

            def transport(command: list[object]) -> dict[str, object]:
                nonlocal interrupted
                if (
                    not interrupted
                    and len(command) > 1
                    and command[1] == store_module._CUSTOM_IMAP_DISMISSAL_BRIDGE_SCRIPT
                ):
                    interrupted = True
                    action(locator, scope)
                return self._transport(command)

            racing_store = SemanticAssessmentStore(transport, hmac_secret=SECRET)
            return (
                racing_store.bridge_custom_imap_v1_dismissal_to_v2(
                    locator,
                    authority,
                ),
                v2_key,
            )

        outcome, v2_key = bridge_with_interruption(
            "10",
            lambda locator, _scope: (
                self.legacy.record_custom_imap_v1_compatibility_unavailable(locator)
            ),
        )
        self.assertEqual(
            outcome,
            CustomImapDismissalBridgeOutcome.COMPATIBILITY_INCOMPLETE,
        )
        self.assertIsNone(self._transport(["GET", v2_key])["result"])

        outcome, v2_key = bridge_with_interruption(
            "11",
            lambda locator, scope: self.legacy.record_custom_imap_v1_compatibility_mapping(
                locator,
                replace(scope, model_version="renewed-provenance"),
            ),
        )
        self.assertEqual(outcome, CustomImapDismissalBridgeOutcome.BRIDGED)
        self.assertEqual(self._transport(["GET", v2_key])["result"], "bridged_v1")

        def replace_mapping(locator, scope):
            keys = store_module._custom_imap_compatibility_keys(SECRET, locator)
            now = int(self._transport(["TIME"])["result"][0])
            alternate = store_module._encode_custom_imap_compatibility_sidecar(
                SECRET,
                locator,
                replace(scope, latest_turn_id="new-mapping"),
                validated_at=now,
            )
            self._transport([
                "SET",
                keys["sidecar"],
                alternate,
                "EX",
                CUSTOM_IMAP_COMPATIBILITY_TTL_SECONDS,
            ])

        outcome, v2_key = bridge_with_interruption("12", replace_mapping)
        self.assertEqual(outcome, CustomImapDismissalBridgeOutcome.CLAIM_STALE)
        self.assertIsNone(self._transport(["GET", v2_key])["result"])

    def test_native_v2_precedence_and_corrupt_v2_state(self):
        locator, _scope, authority, _legacy_key, v2_key = (
            self._install_bridge_authority("13")
        )
        self.assertEqual(
            self._transport(["SET", v2_key, "native_v2", "PX", 90_000])["result"],
            "OK",
        )
        native_expiry = self._transport(["PEXPIRETIME", v2_key])["result"]
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.NATIVE_V2_PRESENT,
        )
        self.assertEqual(self._transport(["GET", v2_key])["result"], "native_v2")
        self.assertEqual(
            self._transport(["PEXPIRETIME", v2_key])["result"],
            native_expiry,
        )

        locator, _scope, authority, _legacy_key, v2_key = (
            self._install_bridge_authority("14")
        )
        v2_scope = _cache_scope(
            authority.conversation_id,
            authority.latest_turn_id,
            CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
        )
        self._commit(self.v2, v2_scope, occurred_at=10, assessed_at=20, indexed=True)
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.BRIDGED,
        )
        self.assertTrue(self.v2.dismiss_new_inbound_exact(
            authority.to_index_scope(),
            conversation_id=authority.conversation_id,
            latest_turn_id=authority.latest_turn_id,
            semantic_version=CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
            current=20,
        ))
        self.assertEqual(self._transport(["GET", v2_key])["result"], "native_v2")
        self.assertGreaterEqual(
            self._transport(["TTL", v2_key])["result"],
            2_592_000 - 2,
        )
        self.assertEqual(
            self._transport(["SET", v2_key, "unknown", "PX", 60_000])["result"],
            "OK",
        )
        with self.assertRaises(SemanticStoreUnavailable):
            self.v2.dismiss_new_inbound_exact(
                authority.to_index_scope(),
                conversation_id=authority.conversation_id,
                latest_turn_id=authority.latest_turn_id,
                semantic_version=CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
                current=20,
            )

        for value, expiry in (("unknown", 60_000), ("bridged_v1", None)):
            locator, _scope, authority, _legacy_key, v2_key = (
                self._install_bridge_authority(str(15 if value == "unknown" else 16))
            )
            command: list[object] = ["SET", v2_key, value]
            if expiry is not None:
                command.extend(["PX", expiry])
            self.assertEqual(self._transport(command)["result"], "OK")
            self.assertEqual(
                self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
                CustomImapDismissalBridgeOutcome.CORRUPT_STATE,
            )
        locator, _scope, authority, _legacy_key, v2_key = (
            self._install_bridge_authority("17")
        )
        self.assertEqual(
            self._transport(["HSET", v2_key, "field", "value"])["result"],
            1,
        )
        self.assertEqual(
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
            CustomImapDismissalBridgeOutcome.CORRUPT_STATE,
        )

    def test_concurrent_bridges_and_native_action_converge(self):
        locator, _scope, authority, _legacy_key, v2_key = (
            self._install_bridge_authority("18")
        )
        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(
                lambda _number: self.legacy.bridge_custom_imap_v1_dismissal_to_v2(
                    locator,
                    authority,
                ),
                range(16),
            ))
        self.assertEqual(outcomes.count(CustomImapDismissalBridgeOutcome.BRIDGED), 1)
        self.assertEqual(
            outcomes.count(CustomImapDismissalBridgeOutcome.ALREADY_BRIDGED),
            15,
        )
        first_expiry = self._transport(["PEXPIRETIME", v2_key])["result"]
        for _attempt in range(3):
            self.assertEqual(
                self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority),
                CustomImapDismissalBridgeOutcome.ALREADY_BRIDGED,
            )
            next_expiry = self._transport(["PEXPIRETIME", v2_key])["result"]
            self.assertLessEqual(next_expiry, first_expiry)
            first_expiry = next_expiry

        locator, _scope, authority, _legacy_key, v2_key = (
            self._install_bridge_authority("19")
        )
        v2_scope = _cache_scope(
            authority.conversation_id,
            authority.latest_turn_id,
            CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
        )
        self._commit(self.v2, v2_scope, occurred_at=10, assessed_at=20, indexed=True)

        def native_action() -> bool:
            return self.v2.dismiss_new_inbound_exact(
                authority.to_index_scope(),
                conversation_id=authority.conversation_id,
                latest_turn_id=authority.latest_turn_id,
                semantic_version=CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION,
                current=20,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            bridge_future = executor.submit(
                self.legacy.bridge_custom_imap_v1_dismissal_to_v2,
                locator,
                authority,
            )
            native_future = executor.submit(native_action)
        self.assertIn(
            bridge_future.result(),
            {
                CustomImapDismissalBridgeOutcome.BRIDGED,
                CustomImapDismissalBridgeOutcome.NATIVE_V2_PRESENT,
            },
        )
        self.assertTrue(native_future.result())
        self.assertEqual(self._transport(["GET", v2_key])["result"], "native_v2")

    def test_bridge_validation_and_legacy_script_bytes_remain_frozen(self):
        locator = _compatibility_locator(imap_uid="120")
        authority = _v2_authority()
        with self.assertRaises(ValueError):
            self.legacy.bridge_custom_imap_v1_dismissal_to_v2(
                locator,
                replace(authority, mailbox_account_identity="other@example.com"),
            )
        for changes in (
            {"provider": "google"},
            {"conversation_id": "imap:rfc:legacy"},
        ):
            with self.assertRaises(ValueError):
                _v2_authority(**changes)
        expected_hashes = {
            "commit": "e13f9228df08a1a1b1b7aa1e3b8b43a1bfd8089f35586fe0784f40480f19472f",
            "dismiss": "94f6f5a53cc38f0dcf9712c31fcedcc5e4e9ed654152b5e38d37cd5b7e2f4a30",
            "read": "7c1038da50bc84e30b9e5ce76c0c2be8b5654c18315d3f0314785284ca87475a",
        }
        self.assertEqual(
            hashlib.sha256(
                store_module._COMMIT_NEW_INBOUND_RESULT_SCRIPT.encode()
            ).hexdigest(),
            expected_hashes["commit"],
        )
        self.assertEqual(
            hashlib.sha256(
                store_module._DISMISS_NEW_INBOUND_SCRIPT.encode()
            ).hexdigest(),
            expected_hashes["dismiss"],
        )
        self.assertEqual(
            hashlib.sha256(
                store_module._READ_NEW_INBOUND_DISMISSALS_SCRIPT.encode()
            ).hexdigest(),
            expected_hashes["read"],
        )
        gmail_scope = _index_scope(provider="google")
        gmail_key = self.legacy._new_inbound_dismissal_key(
            gmail_scope,
            conversation_id="thread:gmail",
            latest_turn_id="message-1",
        )
        self.assertTrue(gmail_key.startswith(
            "cuevion:priority:semantic:v1:new-inbound-dismissal:"
        ))
        self.assertEqual(
            self._transport(["SET", gmail_key, "1", "EX", 2_592_000])["result"],
            "OK",
        )
        gmail_expiry = self._transport(["PEXPIRETIME", gmail_key])["result"]
        locator, _scope, authority, legacy_key, v2_key = (
            self._install_bridge_authority("21")
        )
        self.legacy.bridge_custom_imap_v1_dismissal_to_v2(locator, authority)
        self.assertEqual(self._transport(["GET", gmail_key])["result"], "1")
        self.assertEqual(
            self._transport(["PEXPIRETIME", gmail_key])["result"],
            gmail_expiry,
        )
        self.assertEqual(self._transport(["GET", legacy_key])["result"], "1")
        self.assertEqual(self._transport(["GET", v2_key])["result"], "bridged_v1")

    def _compatibility_artifacts(
        self,
        locator: CustomImapV1CompatibilityLocator,
    ) -> tuple[dict[str, str], object, object]:
        keys = store_module._custom_imap_compatibility_keys(SECRET, locator)
        return (
            keys,
            self._transport(["GET", keys["sidecar"]])["result"],
            self._transport(["GET", keys["incomplete"]])["result"],
        )

    def test_compatibility_write_uses_server_time_full_ttl_and_refreshes_provenance(self):
        locator = _compatibility_locator()
        scope = _cache_scope(
            "thread:mailbox-real-redis|imap:rfc:root",
            "turn-1",
            SEMANTIC_SCHEMA_VERSION,
        )
        before = int(self._transport(["TIME"])["result"][0])
        self.assertEqual(
            self.legacy.record_custom_imap_v1_compatibility_mapping(locator, scope),
            CustomImapCompatibilityOutcome.SIDECAR_WRITTEN,
        )
        after = int(self._transport(["TIME"])["result"][0])
        keys, raw, marker = self._compatibility_artifacts(locator)
        self.assertIsNone(marker)
        decoded = store_module._decode_custom_imap_compatibility_sidecar(
            raw,
            secret=SECRET,
            locator=locator,
        )
        self.assertIsNotNone(decoded)
        self.assertLessEqual(before, decoded["validatedAt"])
        self.assertLessEqual(decoded["validatedAt"], after)
        self.assertEqual(
            decoded["expiresAt"] - decoded["validatedAt"],
            CUSTOM_IMAP_COMPATIBILITY_TTL_SECONDS,
        )
        ttl = self._transport(["TTL", keys["sidecar"]])["result"]
        self.assertGreaterEqual(ttl, CUSTOM_IMAP_COMPATIBILITY_TTL_SECONDS - 2)
        self.assertLessEqual(ttl, CUSTOM_IMAP_COMPATIBILITY_TTL_SECONDS)

        self.assertEqual(self._transport(["EXPIRE", keys["sidecar"], 30])["result"], 1)
        self.assertEqual(
            self.legacy.record_custom_imap_v1_compatibility_mapping(locator, scope),
            CustomImapCompatibilityOutcome.SIDECAR_RENEWED,
        )
        self.assertGreaterEqual(
            self._transport(["TTL", keys["sidecar"]])["result"],
            CUSTOM_IMAP_COMPATIBILITY_TTL_SECONDS - 2,
        )
        renewed = json.loads(self._transport(["GET", keys["sidecar"]])["result"])
        refreshed_scope = replace(scope, model_version="replacement-model")
        self.assertEqual(
            self.legacy.record_custom_imap_v1_compatibility_mapping(
                locator,
                refreshed_scope,
            ),
            CustomImapCompatibilityOutcome.SIDECAR_RENEWED,
        )
        refreshed = json.loads(self._transport(["GET", keys["sidecar"]])["result"])
        self.assertEqual(renewed["mappingMac"], refreshed["mappingMac"])
        self.assertEqual(refreshed["legacyModelVersion"], "replacement-model")
        self.assertNotEqual(renewed["recordMac"], refreshed["recordMac"])

    def test_compatibility_conflicts_are_immutable_and_sticky(self):
        scope = _cache_scope(
            "thread:mailbox-real-redis|imap:rfc:first",
            "turn-1",
            SEMANTIC_SCHEMA_VERSION,
        )
        cases = (
            (
                _compatibility_locator(imap_uid="21"),
                replace(scope, conversation_id="thread:mailbox-real-redis|imap:rfc:other"),
            ),
            (_compatibility_locator(imap_uid="22"), replace(scope, latest_turn_id="turn-2")),
        )
        for locator, conflicting in cases:
            self.assertEqual(
                self.legacy.record_custom_imap_v1_compatibility_mapping(
                    locator,
                    scope,
                ),
                CustomImapCompatibilityOutcome.SIDECAR_WRITTEN,
            )
            keys, original, _marker = self._compatibility_artifacts(locator)
            self.assertEqual(
                self.legacy.record_custom_imap_v1_compatibility_mapping(
                    locator,
                    conflicting,
                ),
                CustomImapCompatibilityOutcome.SIDECAR_CONFLICT,
            )
            self.assertEqual(
                self._transport(["GET", keys["sidecar"]])["result"],
                original,
            )
            marker = store_module._decode_custom_imap_compatibility_marker(
                self._transport(["GET", keys["incomplete"]])["result"],
                secret=SECRET,
                locator=locator,
            )
            self.assertEqual(marker["reason"], "mapping_conflict")
            self.assertEqual(
                self.legacy.record_custom_imap_v1_compatibility_mapping(
                    locator,
                    scope,
                ),
                CustomImapCompatibilityOutcome.COMPATIBILITY_INCOMPLETE,
            )
            retained = store_module._decode_custom_imap_compatibility_marker(
                self._transport(["GET", keys["incomplete"]])["result"],
                secret=SECRET,
                locator=locator,
            )
            self.assertEqual(retained["reason"], "mapping_conflict")

    def test_compatibility_corruption_unavailable_and_oversize_fail_closed(self):
        locator = _compatibility_locator()
        scope = _cache_scope(
            "thread:mailbox-real-redis|imap:rfc:corrupt",
            "turn-1",
            SEMANTIC_SCHEMA_VERSION,
        )
        keys = store_module._custom_imap_compatibility_keys(SECRET, locator)
        self.legacy.record_custom_imap_v1_compatibility_unavailable(locator)
        marker = store_module._decode_custom_imap_compatibility_marker(
            self._transport(["GET", keys["incomplete"]])["result"],
            secret=SECRET,
            locator=locator,
        )
        self.assertEqual(marker["reason"], "sidecar_unavailable")
        self.assertEqual(
            self.legacy.record_custom_imap_v1_compatibility_mapping(locator, scope),
            CustomImapCompatibilityOutcome.SIDECAR_WRITTEN,
        )
        self.assertIsNone(self._transport(["GET", keys["incomplete"]])["result"])

        self.assertEqual(self._transport(["SET", keys["sidecar"], "not-json"])["result"], "OK")
        self.assertEqual(
            self.legacy.record_custom_imap_v1_compatibility_mapping(locator, scope),
            CustomImapCompatibilityOutcome.COMPATIBILITY_INCOMPLETE,
        )
        self.assertEqual(self._transport(["GET", keys["sidecar"]])["result"], "not-json")
        marker = store_module._decode_custom_imap_compatibility_marker(
            self._transport(["GET", keys["incomplete"]])["result"],
            secret=SECRET,
            locator=locator,
        )
        self.assertEqual(marker["reason"], "sidecar_corrupt")

        malformed_locator = _compatibility_locator(imap_uid="12")
        malformed_keys = store_module._custom_imap_compatibility_keys(
            SECRET,
            malformed_locator,
        )
        self.assertEqual(
            self._transport(["SET", malformed_keys["incomplete"], "bad-marker"])["result"],
            "OK",
        )
        self.assertEqual(
            self.legacy.record_custom_imap_v1_compatibility_mapping(
                malformed_locator,
                scope,
            ),
            CustomImapCompatibilityOutcome.COMPATIBILITY_INCOMPLETE,
        )
        malformed = store_module._decode_custom_imap_compatibility_marker(
            self._transport(["GET", malformed_keys["incomplete"]])["result"],
            secret=SECRET,
            locator=malformed_locator,
        )
        self.assertEqual(malformed["reason"], "marker_corrupt")

        large_locator = _compatibility_locator(imap_uid="13")
        large_scope = replace(
            scope,
            conversation_id="c" * store_module.NEW_INBOUND_INDEX_MAX_CONVERSATION_ID_CHARACTERS,
            latest_turn_id="t" * store_module.NEW_INBOUND_INDEX_MAX_TURN_ID_CHARACTERS,
            model_version="m" * store_module.NEW_INBOUND_INDEX_MAX_MODEL_CHARACTERS,
        )
        self.assertEqual(
            self.legacy.record_custom_imap_v1_compatibility_mapping(
                large_locator,
                large_scope,
            ),
            CustomImapCompatibilityOutcome.COMPATIBILITY_INCOMPLETE,
        )
        large_keys = store_module._custom_imap_compatibility_keys(SECRET, large_locator)
        self.assertIsNone(self._transport(["GET", large_keys["sidecar"]])["result"])
        large_marker = store_module._decode_custom_imap_compatibility_marker(
            self._transport(["GET", large_keys["incomplete"]])["result"],
            secret=SECRET,
            locator=large_locator,
        )
        self.assertEqual(large_marker["reason"], "record_too_large")

    def test_compatibility_concurrency_converges_without_last_write_wins(self):
        locator = _compatibility_locator()
        scope = _cache_scope(
            "thread:mailbox-real-redis|imap:rfc:concurrent",
            "turn-1",
            SEMANTIC_SCHEMA_VERSION,
        )
        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(
                lambda _number: self.legacy.record_custom_imap_v1_compatibility_mapping(
                    locator,
                    scope,
                ),
                range(16),
            ))
        self.assertEqual(outcomes.count(CustomImapCompatibilityOutcome.SIDECAR_WRITTEN), 1)
        self.assertEqual(outcomes.count(CustomImapCompatibilityOutcome.SIDECAR_RENEWED), 15)

        conflict_locator = _compatibility_locator(imap_uid="14")
        first = replace(scope, conversation_id="thread:mailbox-real-redis|imap:rfc:a")
        second = replace(scope, conversation_id="thread:mailbox-real-redis|imap:rfc:b")
        with ThreadPoolExecutor(max_workers=2) as executor:
            conflicting_outcomes = list(executor.map(
                lambda candidate: self.legacy.record_custom_imap_v1_compatibility_mapping(
                    conflict_locator,
                    candidate,
                ),
                (first, second),
            ))
        self.assertIn(CustomImapCompatibilityOutcome.SIDECAR_WRITTEN, conflicting_outcomes)
        self.assertIn(CustomImapCompatibilityOutcome.SIDECAR_CONFLICT, conflicting_outcomes)
        conflict_keys = store_module._custom_imap_compatibility_keys(
            SECRET,
            conflict_locator,
        )
        stored = json.loads(self._transport(["GET", conflict_keys["sidecar"]])["result"])
        self.assertIn(stored["legacyConversationId"], {first.conversation_id, second.conversation_id})
        marker = store_module._decode_custom_imap_compatibility_marker(
            self._transport(["GET", conflict_keys["incomplete"]])["result"],
            secret=SECRET,
            locator=conflict_locator,
        )
        self.assertEqual(marker["reason"], "mapping_conflict")

    def test_compatibility_namespace_does_not_mutate_legacy_semantic_keys(self):
        locator = _compatibility_locator()
        scope = _cache_scope(
            "thread:mailbox-real-redis|imap:rfc:isolated",
            "turn-1",
            SEMANTIC_SCHEMA_VERSION,
        )
        self._commit(
            self.legacy,
            scope,
            occurred_at=10,
            assessed_at=20,
            indexed=True,
        )
        legacy_keys = self._transport(["KEYS", "cuevion:priority:semantic:v1:*"])["result"]

        def snapshot(key: str) -> tuple[str, object]:
            key_type = self._transport(["TYPE", key])["result"]
            command = {
                "string": ["GET", key],
                "hash": ["HGETALL", key],
                "zset": ["ZRANGE", key, 0, -1, "WITHSCORES"],
            }[key_type]
            return key_type, self._transport(command)["result"]

        legacy_values = {
            key: snapshot(key)
            for key in legacy_keys
        }
        self.assertEqual(
            self.legacy.record_custom_imap_v1_compatibility_mapping(locator, scope),
            CustomImapCompatibilityOutcome.SIDECAR_WRITTEN,
        )
        self.assertEqual(
            {
                key: snapshot(key)
                for key in legacy_keys
            },
            legacy_values,
        )
        compatibility_keys = self._transport(
            ["KEYS", f"{CUSTOM_IMAP_COMPATIBILITY_KEY_PREFIX}*"]
        )["result"]
        self.assertEqual(len(compatibility_keys), 1)


if __name__ == "__main__":
    unittest.main()
