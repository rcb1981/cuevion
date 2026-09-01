from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from . import candidate_store as candidate_module
from .candidate_projection import populate_priority_candidates, project_priority_candidate
from .candidate_store import (
    CandidateCapacityExceeded,
    CandidateStoreUnavailable,
    CandidateVersionConflict,
    PriorityCandidateStore,
)
from .test_candidate_projection import (
    gmail_authority,
    gmail_source,
    imap_authority,
    imap_source,
)
from .test_candidate_store import (
    SECRET,
    google_scope,
    imap_scope,
    imap_v2_snapshot,
    ready_routing,
    snapshot,
)


@unittest.skipUnless(
    shutil.which("redis-server") and shutil.which("redis-cli"),
    "disposable Redis executables are unavailable",
)
class CandidateRealRedisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="cuevion-candidate-redis-")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls._port = probe.getsockname()[1]
        cls._process = subprocess.Popen(
            [
                "redis-server",
                "--port",
                str(cls._port),
                "--bind",
                "127.0.0.1",
                "--save",
                "",
                "--appendonly",
                "no",
                "--protected-mode",
                "yes",
                "--dir",
                cls._temporary.name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _attempt in range(100):
            result = subprocess.run(
                ["redis-cli", "-p", str(cls._port), "PING"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip() == "PONG":
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
        cls._temporary.cleanup()

    def setUp(self) -> None:
        self._transport(["FLUSHDB"])
        self.store = PriorityCandidateStore(self._transport, hmac_secret=SECRET)

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

    def _v2_store(self) -> PriorityCandidateStore:
        return PriorityCandidateStore(
            self._transport,
            hmac_secret=SECRET,
            storage_namespace="custom_imap_v2",
        )

    def _physical_state(
        self,
        store: PriorityCandidateStore,
        scope,
    ) -> tuple[object, ...]:
        keys = store._scope_keys(scope)
        return (
            self._transport(["GET", keys["record"]])["result"],
            *(
                self._transport(
                    ["ZRANGE", keys[index_name], 0, -1, "WITHSCORES"]
                )["result"]
                for index_name in (
                    "mailbox_index",
                    "user_index",
                    "namespace_index",
                )
            ),
        )

    def _prepare_script_result(
        self,
        *,
        expected_version: object = 0,
        references: tuple[object, ...] = (0, 0, 0, 0, 0, 0),
        maximum: object = candidate_module.CANDIDATE_MAX_SAFE_INTEGER,
    ) -> object:
        return self._transport(
            [
                "EVAL",
                candidate_module._PREPARE_CONFIRMED_SCRIPT,
                0,
                expected_version,
                candidate_module.CANDIDATE_BASE_TTL_SECONDS,
                candidate_module.CANDIDATE_ABSOLUTE_TTL_SECONDS,
                maximum,
                *references,
                candidate_module._PREPARE_REFERENCE_INVALID_SENTINEL,
                candidate_module._PREPARE_TEMPORAL_INVALID_SENTINEL,
            ]
        )["result"]

    def _assert_provider_source_round_trip(self, authority, source):
        scope, intended = project_priority_candidate(authority, source)
        prepared, prepared_record = self.store._prepare_confirmed(
            scope,
            intended,
            candidate_module._empty_references(),
            expected_version=0,
        )
        self.assertEqual(prepared_record.snapshot, intended)
        committed = self.store._commit_confirmed(
            scope,
            intended,
            mode="normal",
            expected_raw=candidate_module._MISSING_SENTINEL,
            prepared=prepared,
            prepared_record=prepared_record,
            expected_existing_expiry=0,
        )
        self.assertEqual(committed, prepared_record)
        raw = self._transport(
            ["GET", self.store._scope_keys(scope)["record"]]
        )["result"]
        self.assertEqual(raw, prepared)
        self.assertEqual(
            raw,
            candidate_module._encode_wire(
                candidate_module._record_to_wire(SECRET, prepared_record)
            ),
        )
        self.assertEqual(self.store.read_candidate(scope), committed)
        repeated = self.store.upsert_confirmed(
            scope,
            intended,
            expected_version=1,
        )
        self.assertEqual(repeated.version, 2)
        self.assertEqual(repeated.snapshot, intended)
        self.assertEqual(self.store.read_candidate(scope), repeated)
        return scope, intended, json.loads(prepared)

    def test_exact_gmail_provider_source_prepare_commit_and_repeat(self) -> None:
        source = gmail_source()
        scope, intended, semantic_wire = (
            self._assert_provider_source_round_trip(gmail_authority(), source)
        )
        self.assertIs(type(semantic_wire["schemaVersion"]), int)
        self.assertEqual(intended.routing_state, "unresolved")
        self.assertIsNone(intended.routing)
        self.assertIsNone(semantic_wire["routing"])
        self.assertIs(type(intended.provider_authority.labels), tuple)
        self.assertTrue(intended.provider_authority.labels)
        self.assertIs(type(semantic_wire["providerAuthority"]["labels"]), list)
        self.assertIsNone(intended.conversation.rfc_root_message_id)
        self.assertIsNone(intended.conversation.rfc_message_id)
        self.assertIs(type(intended.conversation.provider_thread_id), str)
        self.assertIs(type(intended.render.unread), bool)
        self.assertIs(type(intended.render.flagged), bool)
        self.assertEqual(scope.identity.provider_message_id, source["providerMessageId"])

    def test_exact_imap_rfc_provider_source_prepare_commit_and_repeat(self) -> None:
        source = imap_source()
        scope, intended, semantic_wire = (
            self._assert_provider_source_round_trip(imap_authority(), source)
        )
        self.assertIs(type(semantic_wire["schemaVersion"]), int)
        self.assertEqual(intended.routing_state, "unresolved")
        self.assertIsNone(intended.routing)
        self.assertIsNone(semantic_wire["routing"])
        self.assertEqual(intended.provider_authority.labels, ())
        self.assertIsNone(semantic_wire["providerAuthority"]["labels"])
        self.assertIsNone(intended.conversation.provider_thread_id)
        self.assertIs(type(intended.conversation.rfc_root_message_id), str)
        self.assertIs(type(intended.conversation.rfc_message_id), str)
        self.assertIs(type(intended.render.unread), bool)
        self.assertIs(type(intended.render.flagged), bool)
        self.assertEqual(scope.identity.uid_validity, source["uidValidity"])
        self.assertEqual(scope.identity.imap_uid, source["imapUid"])

    def test_exact_imap_uid_provider_source_prepare_commit_and_repeat(self) -> None:
        source = imap_source(
            imapUid="124",
            conversationId="imap:uid:mailbox-1:INBOX:456:124",
            authorityKind="imap_uid",
            rfcRootMessageId=None,
            rfcMessageId=None,
        )
        scope, intended, semantic_wire = (
            self._assert_provider_source_round_trip(imap_authority(), source)
        )
        self.assertIs(type(semantic_wire["schemaVersion"]), int)
        self.assertEqual(intended.routing_state, "unresolved")
        self.assertIsNone(intended.routing)
        self.assertIsNone(semantic_wire["routing"])
        self.assertEqual(intended.provider_authority.labels, ())
        self.assertIsNone(semantic_wire["providerAuthority"]["labels"])
        self.assertIsNone(intended.conversation.provider_thread_id)
        self.assertIsNone(intended.conversation.rfc_root_message_id)
        self.assertIsNone(intended.conversation.rfc_message_id)
        self.assertIs(type(intended.render.unread), bool)
        self.assertIs(type(intended.render.flagged), bool)
        self.assertEqual(scope.identity.uid_validity, source["uidValidity"])
        self.assertEqual(scope.identity.imap_uid, source["imapUid"])

    def test_prepare_lua_returns_only_bounded_server_metadata(self) -> None:
        self.assertNotIn("cjson", candidate_module._PREPARE_CONFIRMED_SCRIPT)
        before = int(time.time() * 1_000)
        result = self._prepare_script_result(
            references=(
                0,
                before - 1,
                before + 60_000,
                candidate_module.CANDIDATE_MAX_SAFE_INTEGER,
                0,
                0,
            )
        )
        after = int(time.time() * 1_000)
        self.assertIs(type(result), list)
        self.assertEqual(len(result), 10)
        self.assertTrue(all(type(value) is int for value in result))
        now, base, absolute, version, *references = result
        self.assertLessEqual(before, now)
        self.assertLessEqual(now, after)
        self.assertEqual(
            base,
            now + candidate_module.CANDIDATE_BASE_TTL_SECONDS * 1_000,
        )
        self.assertEqual(
            absolute,
            now + candidate_module.CANDIDATE_ABSOLUTE_TTL_SECONDS * 1_000,
        )
        self.assertEqual(version, 1)
        self.assertEqual(references[:2], [0, 0])
        self.assertEqual(references[2], before + 60_000)
        self.assertEqual(references[3], absolute)
        self.assertEqual(
            self._prepare_script_result(
                references=(0.5, 0, 0, 0, 0, 0)
            ),
            candidate_module._PREPARE_REFERENCE_INVALID_SENTINEL,
        )
        self.assertEqual(
            self._prepare_script_result(
                expected_version=candidate_module.CANDIDATE_MAX_SAFE_INTEGER
            ),
            candidate_module._PREPARE_TEMPORAL_INVALID_SENTINEL,
        )

    def test_nested_provider_values_are_python_canonical_despite_cjson_rules(self) -> None:
        scope = google_scope(message_id="real-python-canonical")
        intended = snapshot(
            routing_state="ready",
            routing=replace(
                ready_routing(),
                noise_reasons=("automated_sender_evidence",),
            ),
        )
        written = self.store.upsert_confirmed(
            scope,
            intended,
            expected_version=0,
        )
        raw = self._transport(
            ["GET", self.store._scope_keys(scope)["record"]]
        )["result"]
        expected = candidate_module._encode_wire(
            candidate_module._record_to_wire(SECRET, written)
        )
        self.assertEqual(raw, expected)
        self.assertEqual(
            json.loads(raw)["providerAuthority"]["labels"],
            list(intended.provider_authority.labels),
        )
        self.assertEqual(
            json.loads(raw)["routing"]["noiseReasons"],
            ["automated_sender_evidence"],
        )

    def test_unresolved_ready_and_repeated_writes_round_trip_exactly(self) -> None:
        cases = (
            (
                "gmail",
                google_scope(message_id="real-gmail"),
                snapshot(snippet="Café Gmail"),
            ),
            (
                "imap",
                imap_scope(uid="501"),
                snapshot(provider="custom_imap", snippet="Café IMAP"),
            ),
            (
                "ready",
                google_scope(message_id="real-ready"),
                snapshot(
                    routing_state="ready",
                    routing=ready_routing(),
                    snippet="Café ready",
                ),
            ),
            (
                "ready-nonempty",
                google_scope(message_id="real-ready-nonempty"),
                snapshot(
                    routing_state="ready",
                    routing=replace(
                        ready_routing(),
                        noise_reasons=("automated_sender_evidence",),
                    ),
                ),
            ),
        )
        for name, scope, intended in cases:
            with self.subTest(name=name):
                written = self.store.upsert_confirmed(
                    scope,
                    intended,
                    expected_version=0,
                )
                self.assertEqual(self.store.read_candidate(scope), written)
                self.assertEqual(written.snapshot, intended)
                raw = self._transport(
                    ["GET", self.store._scope_keys(scope)["record"]]
                )["result"]
                payload = json.loads(raw)
                if name == "imap":
                    self.assertIsNone(payload["providerAuthority"]["labels"])
                if name == "ready":
                    self.assertIsNone(payload["routing"]["noiseReasons"])
                if name == "ready-nonempty":
                    self.assertEqual(
                        payload["routing"]["noiseReasons"],
                        ["automated_sender_evidence"],
                    )

        repeat_scope = cases[0][1]
        repeat_snapshot = cases[0][2]
        keys = self.store._scope_keys(repeat_scope)
        before = tuple(
            self._transport(["ZCARD", keys[name]])["result"]
            for name in ("mailbox_index", "user_index", "namespace_index")
        )
        second = self.store.upsert_confirmed(
            repeat_scope,
            repeat_snapshot,
            expected_version=1,
        )
        after = tuple(
            self._transport(["ZCARD", keys[name]])["result"]
            for name in ("mailbox_index", "user_index", "namespace_index")
        )
        self.assertEqual(second.version, 2)
        self.assertEqual(after, before)

    def test_ambiguous_empty_array_is_rejected_before_any_commit(self) -> None:
        scope = google_scope(message_id="real-precommit")
        intended = snapshot(routing_state="ready", routing=ready_routing())
        original = candidate_module._routing_to_wire

        def ambiguous(value):
            result = original(value)
            assert result is not None
            result["noiseReasons"] = []
            return result

        with patch.object(candidate_module, "_routing_to_wire", side_effect=ambiguous):
            with self.assertRaises(CandidateStoreUnavailable) as raised:
                self.store.upsert_confirmed(scope, intended, expected_version=0)
        self.assertEqual(raised.exception.stage, "store_prepare_canonical_invalid")
        keys = self.store._scope_keys(scope)
        self.assertIsNone(self._transport(["GET", keys["record"]])["result"])
        self.assertEqual(self._transport(["TTL", keys["record"]])["result"], -2)
        for index_name in ("mailbox_index", "user_index", "namespace_index"):
            self.assertIsNone(
                self._transport(
                    ["ZSCORE", keys[index_name], keys["member"]]
                )["result"]
            )
            self.assertEqual(
                self._transport(["EXISTS", keys[index_name]])["result"],
                0,
            )
            self.assertEqual(
                self._transport(["TTL", keys[index_name]])["result"],
                -2,
            )

    def test_prepare_size_boundary_and_fixed_rejection_stages(self) -> None:
        scope = google_scope()
        base = snapshot()
        bounded = replace(
            base,
            conversation=replace(
                base.conversation,
                conversation_id="c" * 1_024,
                authority_kind="a" * 64,
                provider_thread_id="t" * 256,
            ),
            render=replace(
                base.render,
                sender_display="d" * 256,
                sender_address="a" * 320,
                subject="s" * 580,
                snippet="p" * candidate_module.CANDIDATE_MAX_SNIPPET_BYTES,
            ),
        )
        now = 1_800_000_000_000
        final_record = candidate_module.PriorityCandidateRecord(
            scope=scope,
            snapshot=bounded,
            provider_observed_at=now,
            provider_validated_at=now,
            base_expires_at=(
                now + candidate_module.CANDIDATE_BASE_TTL_SECONDS * 1_000
            ),
            absolute_expires_at=(
                now + candidate_module.CANDIDATE_ABSOLUTE_TTL_SECONDS * 1_000
            ),
            grace_expires_at=0,
            positive_references=candidate_module._empty_references(),
            state="provider_confirmed",
            version=1,
            updated_at=now,
        )
        final_payload = candidate_module._record_to_wire(SECRET, final_record)
        final_encoded = json.dumps(
            final_payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        zero_placeholder_payload = dict(final_payload)
        zero_placeholder_payload.update(
            {
                "providerObservedAt": 0,
                "providerValidatedAt": 0,
                "baseExpiresAt": 0,
                "absoluteExpiresAt": 0,
                "version": 0,
                "updatedAt": 0,
            }
        )
        zero_placeholder = json.dumps(
            zero_placeholder_payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        self.assertLessEqual(
            len(zero_placeholder.encode("ascii")),
            candidate_module.CANDIDATE_MAX_SERIALIZED_RECORD_BYTES,
        )
        self.assertGreater(
            len(final_encoded.encode("ascii")),
            candidate_module.CANDIDATE_MAX_SERIALIZED_RECORD_BYTES,
        )
        with self.assertRaises(CandidateStoreUnavailable) as too_large:
            self.store.upsert_confirmed(scope, bounded, expected_version=0)
        self.assertEqual(
            too_large.exception.stage,
            "store_prepare_size_invalid",
        )
        self.assertIsNone(
            self._transport(["GET", self.store._scope_keys(scope)["record"]])[
                "result"
            ]
        )

        safely_bounded = replace(
            bounded,
            render=replace(bounded.render, subject="s" * 500),
        )
        accepted = self.store.upsert_confirmed(
            scope,
            safely_bounded,
            expected_version=0,
        )
        self.assertEqual(accepted.snapshot, safely_bounded)

        with self.assertRaises(CandidateStoreUnavailable) as temporal:
            self.store._prepare_confirmed(
                scope,
                base,
                candidate_module._empty_references(),
                expected_version=candidate_module.CANDIDATE_MAX_SAFE_INTEGER,
            )
        self.assertEqual(
            temporal.exception.stage,
            "store_prepare_temporal_invalid",
        )

    def test_existing_mutations_preserve_canonical_empty_collection(self) -> None:
        scope = google_scope(message_id="real-ready-mutations")
        intended = snapshot(routing_state="ready", routing=ready_routing())
        first = self.store.upsert_confirmed(scope, intended, expected_version=0)
        referenced = self.store.set_positive_reference(
            scope,
            reference_kind="waiting",
            remaining_lifetime_seconds=60,
            expected_version=first.version,
        )
        self.assertIsNotNone(referenced)
        degraded = self.store.mark_provider_validation_failure(
            scope,
            expected_version=referenced.version,
        )
        self.assertIsNotNone(degraded)
        self.assertEqual(self.store.read_candidate(scope), degraded)
        refreshed = self.store.upsert_confirmed(
            scope,
            intended,
            expected_version=degraded.version,
        )
        self.assertEqual(refreshed.version, 4)
        self.assertGreater(
            refreshed.positive_reference_expires_at("waiting"),
            0,
        )
        self.assertEqual(self.store.read_candidate(scope), refreshed)
        raw = self._transport(
            ["GET", self.store._scope_keys(scope)["record"]]
        )["result"]
        self.assertIsNone(json.loads(raw)["routing"]["noiseReasons"])

    def test_workflow_references_commit_atomically_with_python_canonical_wire(
        self,
    ) -> None:
        scope = google_scope(message_id="real-workflow-references")
        record = self.store.upsert_confirmed(
            scope,
            snapshot(routing_state="ready", routing=ready_routing()),
            expected_version=0,
        )
        for kind, lifetime in (
            ("semantic_promotion", 61),
            ("collaboration_priority", 62),
            ("assigned_review", 63),
        ):
            updated = self.store.set_positive_reference(
                scope,
                reference_kind=kind,
                remaining_lifetime_seconds=lifetime,
                expected_version=record.version,
            )
            assert updated is not None
            record = updated
        preserved = {
            kind: record.positive_reference_expires_at(kind)
            for kind in (
                "semantic_promotion",
                "collaboration_priority",
                "assigned_review",
            )
        }
        previous_version = record.version
        before = int(time.time() * 1_000)
        reconciled = self.store.reconcile_workflow_positive_references(
            scope,
            manual_priority_expires_at=record.absolute_expires_at + 60_000,
            waiting_expires_at=before + 45_000,
            returned_reply_expires_at=before + 30_000,
            expected_version=record.version,
        )
        after = int(time.time() * 1_000)
        assert reconciled is not None
        self.assertEqual(reconciled.version, previous_version + 1)
        self.assertEqual(
            reconciled.positive_reference_expires_at("manual_priority"),
            reconciled.absolute_expires_at,
        )
        self.assertEqual(
            reconciled.positive_reference_expires_at("waiting"),
            before + 45_000,
        )
        self.assertEqual(
            reconciled.positive_reference_expires_at("returned_reply"),
            before + 30_000,
        )
        self.assertEqual(
            {
                kind: reconciled.positive_reference_expires_at(kind)
                for kind in preserved
            },
            preserved,
        )
        keys = self.store._scope_keys(scope)
        raw = self._transport(["GET", keys["record"]])["result"]
        self.assertEqual(
            raw,
            candidate_module._encode_wire(
                candidate_module._record_to_wire(SECRET, reconciled)
            ),
        )
        scores = tuple(
            int(self._transport(["ZSCORE", keys[name], keys["member"]])["result"])
            for name in ("mailbox_index", "user_index", "namespace_index")
        )
        self.assertEqual(scores, (reconciled.logical_expires_at(),) * 3)
        record_ttl = self._transport(["PTTL", keys["record"]])["result"]
        self.assertGreater(
            record_ttl,
            reconciled.logical_expires_at() - after - 2_000,
        )
        self.assertLessEqual(
            record_ttl,
            reconciled.logical_expires_at() - before + 2_000,
        )

        with self.assertRaises(CandidateVersionConflict):
            self.store.reconcile_workflow_positive_references(
                scope,
                manual_priority_expires_at=0,
                waiting_expires_at=0,
                returned_reply_expires_at=0,
                expected_version=previous_version,
            )
        self.assertEqual(self._transport(["GET", keys["record"]])["result"], raw)

        self._transport(
            [
                "ZADD",
                keys["mailbox_index"],
                reconciled.logical_expires_at() + 1,
                keys["member"],
            ]
        )
        with self.assertRaises(CandidateStoreUnavailable):
            self.store.reconcile_workflow_positive_references(
                scope,
                manual_priority_expires_at=0,
                waiting_expires_at=0,
                returned_reply_expires_at=0,
                expected_version=reconciled.version,
            )
        self.assertEqual(self._transport(["GET", keys["record"]])["result"], raw)
        self.assertEqual(
            json.loads(raw)["positiveReferences"]["manual_priority"],
            reconciled.absolute_expires_at,
        )
        self.assertNotIn(
            "cjson.encode",
            candidate_module._RECONCILE_WORKFLOW_REFERENCES_SCRIPT,
        )

    def test_provider_population_repairs_only_exact_malformed_v2(self) -> None:
        authority = gmail_authority()
        source = gmail_source(
            providerMessageId="real-repair-target",
            providerThreadId="real-repair-thread",
        )
        other = gmail_source(
            providerMessageId="real-repair-other",
            providerThreadId="real-repair-other-thread",
        )
        initial = populate_priority_candidates(
            authority,
            [source, other],
            store=self.store,
        )
        self.assertEqual(initial.written, 2)
        scope, _ = project_priority_candidate(authority, source)
        other_scope, _ = project_priority_candidate(authority, other)
        keys = self.store._scope_keys(scope)
        other_before = self.store.read_candidate(other_scope)
        raw = self._transport(["GET", keys["record"]])["result"]
        malformed = json.loads(raw)
        malformed["routingState"] = "ready"
        malformed["routing"] = {
            "signal": None,
            "uiSignal": "REPLY",
            "internalClassification": "reply",
            "category": "reply",
            "finalVisibility": None,
            "action": None,
            "v7FinalPriority": None,
            "noiseDisposition": "none",
            "noiseConfidence": "low",
            "noiseReasons": {},
            "classifierVersion": "test-classifier-v1",
            "routingVersion": "test-routing-v1",
        }
        encoded = json.dumps(
            malformed,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        self._transport(["SET", keys["record"], encoded, "KEEPTTL"])

        report = populate_priority_candidates(
            authority,
            [source],
            store=self.store,
        )
        repaired = self.store.read_candidate(scope)
        self.assertEqual(report.written, 1)
        self.assertFalse(report.incomplete)
        self.assertEqual(repaired.version, 1)
        self.assertEqual(repaired.snapshot.routing_state, "unresolved")
        self.assertIsNone(repaired.snapshot.routing)
        self.assertTrue(
            all(item.expires_at == 0 for item in repaired.positive_references)
        )
        self.assertEqual(self.store.read_candidate(other_scope), other_before)
        for index_name in ("mailbox_index", "user_index", "namespace_index"):
            self.assertEqual(
                int(self._transport(
                    ["ZSCORE", keys[index_name], keys["member"]]
                )["result"]),
                repaired.logical_expires_at(),
            )

    def test_repair_commit_index_and_expiry_sentinels_are_distinct(self) -> None:
        source_scope = google_scope(message_id="repair-source-invalid")
        intended = snapshot()
        self.store.upsert_confirmed(source_scope, intended, expected_version=0)
        source_keys = self.store._scope_keys(source_scope)
        self._transport(["SET", source_keys["record"], "not-json", "KEEPTTL"])
        with self.assertRaises(CandidateStoreUnavailable) as source:
            self.store.replace_malformed_confirmed(source_scope, intended)
        self.assertEqual(source.exception.stage, "store_repair_source_invalid")
        self.assertEqual(
            self._transport(["GET", source_keys["record"]])["result"],
            "not-json",
        )

        reference_scope = google_scope(message_id="repair-reference-invalid")
        self.store.upsert_confirmed(reference_scope, intended, expected_version=0)
        reference_keys = self.store._scope_keys(reference_scope)
        raw = self._transport(["GET", reference_keys["record"]])["result"]
        malformed = json.loads(raw)
        malformed["routingState"] = "ready"
        malformed["routing"] = {
            "signal": None,
            "uiSignal": "REPLY",
            "internalClassification": "reply",
            "category": "reply",
            "finalVisibility": None,
            "action": None,
            "v7FinalPriority": None,
            "noiseDisposition": "none",
            "noiseConfidence": "low",
            "noiseReasons": {},
            "classifierVersion": "test-classifier-v1",
            "routingVersion": "test-routing-v1",
        }
        malformed["positiveReferences"]["manual_priority"] = malformed[
            "absoluteExpiresAt"
        ]
        malformed_wire = json.dumps(
            malformed,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        self._transport(
            ["SET", reference_keys["record"], malformed_wire, "KEEPTTL"]
        )
        with self.assertRaises(CandidateStoreUnavailable) as reference:
            self.store.replace_malformed_confirmed(reference_scope, intended)
        self.assertEqual(
            reference.exception.stage,
            "store_repair_reference_proof_invalid",
        )
        self.assertEqual(
            self._transport(["GET", reference_keys["record"]])["result"],
            malformed_wire,
        )

        index_scope = google_scope(message_id="commit-index-invalid")
        index_record = self.store.upsert_confirmed(
            index_scope,
            intended,
            expected_version=0,
        )
        index_keys = self.store._scope_keys(index_scope)
        mutated = False

        def mutate_index_before_commit(command: list[object]) -> dict[str, object]:
            nonlocal mutated
            if (
                not mutated
                and command[0] == "EVAL"
                and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
            ):
                mutated = True
                args = command[10:]
                self._transport(
                    [
                        "ZADD",
                        command[4],
                        int(args[19]) + 1,
                        args[4],
                    ]
                )
            return self._transport(command)

        with self.assertRaises(CandidateStoreUnavailable) as index:
            PriorityCandidateStore(
                mutate_index_before_commit,
                hmac_secret=SECRET,
            ).upsert_confirmed(
                index_scope,
                intended,
                expected_version=index_record.version,
            )
        self.assertEqual(index.exception.stage, "store_commit_index_invalid")
        self.assertEqual(
            self._transport(["GET", index_keys["record"]])["result"],
            candidate_module._encode_wire(
                candidate_module._record_to_wire(SECRET, index_record)
            ),
        )

        expiry_scope = google_scope(message_id="commit-expiry-invalid")
        observed = int(time.time() * 1_000) - 60 * 24 * 60 * 60 * 1_000
        expired_record = candidate_module.PriorityCandidateRecord(
            scope=expiry_scope,
            snapshot=intended,
            provider_observed_at=observed,
            provider_validated_at=observed,
            base_expires_at=(
                observed
                + candidate_module.CANDIDATE_BASE_TTL_SECONDS * 1_000
            ),
            absolute_expires_at=(
                observed
                + candidate_module.CANDIDATE_ABSOLUTE_TTL_SECONDS * 1_000
            ),
            grace_expires_at=0,
            positive_references=candidate_module._empty_references(),
            state="provider_confirmed",
            version=1,
            updated_at=observed,
        )
        expired_wire = candidate_module._encode_wire(
            candidate_module._record_to_wire(SECRET, expired_record)
        )
        with self.assertRaises(CandidateStoreUnavailable) as expiry:
            self.store._commit_confirmed(
                expiry_scope,
                intended,
                mode="normal",
                expected_raw=candidate_module._MISSING_SENTINEL,
                prepared=expired_wire,
                prepared_record=expired_record,
                expected_existing_expiry=0,
            )
        self.assertEqual(expiry.exception.stage, "store_commit_expiry_invalid")

    def test_ambiguous_commit_acknowledgement_uses_one_exact_read(self) -> None:
        commands: list[list[object]] = []

        def ambiguous(command: list[object]) -> dict[str, object]:
            commands.append(command)
            result = self._transport(command)
            if (
                command[0] == "EVAL"
                and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
            ):
                return {"unexpected": "content-free"}
            return result

        store = PriorityCandidateStore(ambiguous, hmac_secret=SECRET)
        scope = google_scope(message_id="real-ack")
        accepted = store.upsert_confirmed(scope, snapshot(), expected_version=0)
        self.assertEqual(
            sum(
                command[0] == "EVAL"
                and command[1] == candidate_module._READ_ONE_SCRIPT
                for command in commands
            ),
            1,
        )
        self.assertEqual(accepted, store.read_candidate(scope))
        self.assertEqual(
            sum(
                command[0] == "EVAL"
                and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                for command in commands
            ),
            1,
        )

    def test_custom_imap_v2_records_updates_and_indexes_are_physically_isolated(
        self,
    ) -> None:
        legacy = self.store
        v2 = self._v2_store()
        first = imap_scope(uid="7101")
        legacy_first = legacy.upsert_confirmed(
            first,
            snapshot(provider="custom_imap"),
            expected_version=0,
        )
        self.assertIsNone(v2.read_candidate(first))
        v2_first = v2.upsert_confirmed(
            first,
            imap_v2_snapshot(),
            expected_version=0,
        )
        v2_before_legacy_update = self._physical_state(v2, first)
        legacy_updated = legacy.upsert_confirmed(
            first,
            snapshot(provider="custom_imap", snippet="legacy update"),
            expected_version=legacy_first.version,
        )
        self.assertEqual(self._physical_state(v2, first), v2_before_legacy_update)
        self.assertEqual(v2.read_candidate(first), v2_first)
        self.assertEqual(legacy_updated.version, 2)

        second = imap_scope(uid="7102")
        v2_second = v2.upsert_confirmed(
            second,
            replace(
                imap_v2_snapshot(),
                conversation=replace(
                    imap_v2_snapshot().conversation,
                    conversation_id="imap:v2:rfc:mailbox-imap:second%40example.test",
                ),
            ),
            expected_version=0,
        )
        self.assertIsNone(legacy.read_candidate(second))
        legacy_second = legacy.upsert_confirmed(
            second,
            snapshot(provider="custom_imap"),
            expected_version=0,
        )
        legacy_before_v2_update = self._physical_state(legacy, second)
        v2_updated = v2.upsert_confirmed(
            second,
            replace(
                v2_second.snapshot,
                render=replace(v2_second.snapshot.render, snippet="v2 update"),
            ),
            expected_version=v2_second.version,
        )
        self.assertEqual(
            self._physical_state(legacy, second),
            legacy_before_v2_update,
        )
        self.assertEqual(legacy.read_candidate(second), legacy_second)
        self.assertEqual(v2_updated.version, 2)
        self.assertCountEqual(
            legacy.read_mailbox_page(first.mailbox_scope()).records,
            (legacy_updated, legacy_second),
        )
        self.assertCountEqual(
            v2.read_mailbox_page(first.mailbox_scope()).records,
            (v2_first, v2_updated),
        )

    def test_custom_imap_v2_and_legacy_corruption_are_contained(self) -> None:
        legacy = self.store
        v2 = self._v2_store()
        scope = imap_scope(uid="7201")
        legacy_record = legacy.upsert_confirmed(
            scope,
            snapshot(provider="custom_imap"),
            expected_version=0,
        )
        v2_record = v2.upsert_confirmed(
            scope,
            imap_v2_snapshot(),
            expected_version=0,
        )
        legacy_keys = legacy._scope_keys(scope)
        self._transport(["SET", legacy_keys["record"], "{"])
        self._transport(["SET", legacy_keys["mailbox_index"], "wrong-type"])
        self.assertEqual(v2.read_candidate(scope), v2_record)
        self.assertEqual(
            v2.read_mailbox_page(scope.mailbox_scope()).records,
            (v2_record,),
        )
        v2_updated = v2.upsert_confirmed(
            scope,
            replace(
                v2_record.snapshot,
                render=replace(v2_record.snapshot.render, snippet="safe"),
            ),
            expected_version=v2_record.version,
        )
        self.assertEqual(v2_updated.version, 2)

        isolated = imap_scope(uid="7202", mailbox_id="mailbox-imap-isolated")
        isolated_legacy = legacy.upsert_confirmed(
            isolated,
            snapshot(provider="custom_imap"),
            expected_version=0,
        )
        isolated_v2 = v2.upsert_confirmed(
            isolated,
            replace(
                imap_v2_snapshot(),
                conversation=replace(
                    imap_v2_snapshot().conversation,
                    conversation_id="imap:v2:rfc:mailbox-imap:isolated%40example.test",
                ),
            ),
            expected_version=0,
        )
        v2_keys = v2._scope_keys(isolated)
        self._transport(["SET", v2_keys["record"], "{"])
        self._transport(["SET", v2_keys["mailbox_index"], "wrong-type"])
        self.assertEqual(legacy.read_candidate(isolated), isolated_legacy)
        self.assertIn(
            isolated_legacy,
            legacy.read_mailbox_page(isolated.mailbox_scope()).records,
        )
        self.assertEqual(legacy_record.version, 1)
        self.assertEqual(isolated_v2.version, 1)

    def test_custom_imap_v2_mailbox_and_user_capacity_are_independent(self) -> None:
        legacy = self.store
        v2 = self._v2_store()
        with patch.object(candidate_module, "CANDIDATE_MAX_MAILBOX_RECORDS", 2):
            mailbox_scopes = tuple(
                imap_scope(uid=str(7300 + index)) for index in range(3)
            )
            for scope in mailbox_scopes[:2]:
                legacy.upsert_confirmed(
                    scope,
                    snapshot(provider="custom_imap"),
                    expected_version=0,
                )
            with self.assertRaises(CandidateCapacityExceeded) as legacy_full:
                legacy.upsert_confirmed(
                    mailbox_scopes[2],
                    snapshot(provider="custom_imap"),
                    expected_version=0,
                )
            self.assertEqual(legacy_full.exception.scope_kind, "mailbox")
            v2.upsert_confirmed(
                mailbox_scopes[2],
                replace(
                    imap_v2_snapshot(),
                    conversation=replace(
                        imap_v2_snapshot().conversation,
                        conversation_id="imap:v2:rfc:mailbox-imap:capacity-2",
                    ),
                ),
                expected_version=0,
            )
            v2.upsert_confirmed(
                mailbox_scopes[0],
                imap_v2_snapshot(),
                expected_version=0,
            )
            with self.assertRaises(CandidateCapacityExceeded) as v2_full:
                v2.upsert_confirmed(
                    mailbox_scopes[1],
                    replace(
                        imap_v2_snapshot(),
                        conversation=replace(
                            imap_v2_snapshot().conversation,
                            conversation_id="imap:v2:rfc:mailbox-imap:capacity-1",
                        ),
                    ),
                    expected_version=0,
                )
            self.assertEqual(v2_full.exception.scope_kind, "mailbox")

        self._transport(["FLUSHDB"])
        with (
            patch.object(candidate_module, "CANDIDATE_MAX_MAILBOX_RECORDS", 10),
            patch.object(candidate_module, "CANDIDATE_MAX_USER_RECORDS", 2),
        ):
            user_scopes = tuple(
                imap_scope(uid=str(7400 + index), mailbox_id=f"user-mailbox-{index}")
                for index in range(3)
            )
            for scope in user_scopes[:2]:
                legacy.upsert_confirmed(
                    scope,
                    snapshot(provider="custom_imap"),
                    expected_version=0,
                )
            with self.assertRaises(CandidateCapacityExceeded) as legacy_user_full:
                legacy.upsert_confirmed(
                    user_scopes[2],
                    snapshot(provider="custom_imap"),
                    expected_version=0,
                )
            self.assertEqual(legacy_user_full.exception.scope_kind, "user")
            for index, scope in enumerate((user_scopes[2], user_scopes[0])):
                intended = imap_v2_snapshot()
                v2.upsert_confirmed(
                    scope,
                    replace(
                        intended,
                        conversation=replace(
                            intended.conversation,
                            conversation_id=f"imap:v2:rfc:mailbox-imap:user-capacity-{index}",
                        ),
                    ),
                    expected_version=0,
                )
            with self.assertRaises(CandidateCapacityExceeded) as v2_user_full:
                v2.upsert_confirmed(
                    user_scopes[1],
                    replace(
                        imap_v2_snapshot(),
                        conversation=replace(
                            imap_v2_snapshot().conversation,
                            conversation_id="imap:v2:rfc:mailbox-imap:user-capacity-full",
                        ),
                    ),
                    expected_version=0,
                )
            self.assertEqual(v2_user_full.exception.scope_kind, "user")

    def test_custom_imap_v2_concurrent_upserts_preserve_exact_cas(self) -> None:
        v2 = self._v2_store()
        scope = imap_scope(uid="7501")
        initial = v2.upsert_confirmed(
            scope,
            imap_v2_snapshot(),
            expected_version=0,
        )

        def update(snippet: str):
            return v2.upsert_confirmed(
                scope,
                replace(
                    initial.snapshot,
                    render=replace(initial.snapshot.render, snippet=snippet),
                ),
                expected_version=initial.version,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(update, snippet)
                for snippet in ("first", "second")
            ]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except CandidateVersionConflict as error:
                    outcomes.append(error)
        records = [
            outcome for outcome in outcomes if not isinstance(outcome, Exception)
        ]
        conflicts = [
            outcome
            for outcome in outcomes
            if isinstance(outcome, CandidateVersionConflict)
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(records[0].version, 2)
        self.assertEqual(v2.read_candidate(scope), records[0])
        keys = v2._scope_keys(scope)
        expected_score = records[0].logical_expires_at()
        for index_name in ("mailbox_index", "user_index", "namespace_index"):
            self.assertEqual(
                int(
                    float(
                        self._transport(
                            ["ZSCORE", keys[index_name], keys["member"]]
                        )["result"]
                    )
                ),
                expected_score,
            )

    def test_rollback_style_legacy_reader_and_google_ignore_v2_namespace(self) -> None:
        legacy = self.store
        v2 = self._v2_store()
        scope = imap_scope(uid="7601")
        v2.upsert_confirmed(scope, imap_v2_snapshot(), expected_version=0)
        self.assertIsNone(legacy.read_candidate(scope))
        self.assertEqual(
            legacy.read_mailbox_page(scope.mailbox_scope()).records,
            (),
        )
        legacy_keys = legacy._scope_keys(scope)
        for key_name in ("record", "mailbox_index", "user_index", "namespace_index"):
            self.assertEqual(
                self._transport(["EXISTS", legacy_keys[key_name]])["result"],
                0,
            )

        google = google_scope(message_id="google-physical-regression")
        google_record = legacy.upsert_confirmed(
            google,
            snapshot(),
            expected_version=0,
        )
        google_keys = legacy._scope_keys(google)
        self.assertTrue(
            google_keys["record"].startswith(candidate_module._CANDIDATE_KEY_PREFIX)
        )
        self.assertFalse(
            google_keys["record"].startswith(
                candidate_module._CUSTOM_IMAP_V2_CANDIDATE_KEY_PREFIX
            )
        )
        self.assertEqual(legacy.read_candidate(google), google_record)
        for index_name in ("mailbox_index", "user_index", "namespace_index"):
            self.assertTrue(
                google_keys[index_name].startswith(
                    candidate_module._CANDIDATE_KEY_PREFIX
                )
            )


if __name__ == "__main__":
    unittest.main()
