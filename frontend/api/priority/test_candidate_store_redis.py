from __future__ import annotations

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
from .candidate_store import CandidateStoreUnavailable, PriorityCandidateStore
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

    def _prepare_script_result(self, encoded: str) -> object:
        return self._transport(
            [
                "EVAL",
                candidate_module._PREPARE_CONFIRMED_SCRIPT,
                0,
                encoded,
                0,
                candidate_module.CANDIDATE_STORE_SCHEMA_VERSION,
                candidate_module.CANDIDATE_BASE_TTL_SECONDS,
                candidate_module.CANDIDATE_ABSOLUTE_TTL_SECONDS,
                candidate_module.CANDIDATE_MAX_SERIALIZED_RECORD_BYTES,
                candidate_module.CANDIDATE_MAX_SAFE_INTEGER,
                candidate_module._PREPARE_JSON_DECODE_INVALID_SENTINEL,
                candidate_module._PREPARE_ROOT_TYPE_INVALID_SENTINEL,
                candidate_module._PREPARE_SCHEMA_VERSION_INVALID_SENTINEL,
                candidate_module._PREPARE_PROVIDER_AUTHORITY_SHAPE_INVALID_SENTINEL,
                candidate_module._PREPARE_LABELS_COLLECTION_INVALID_SENTINEL,
                candidate_module._PREPARE_UNRESOLVED_ROUTING_NULL_INVALID_SENTINEL,
                candidate_module._PREPARE_ROUTING_STATE_INVALID_SENTINEL,
                candidate_module._PREPARE_READY_ROUTING_SHAPE_INVALID_SENTINEL,
                candidate_module._PREPARE_NOISE_REASONS_COLLECTION_INVALID_SENTINEL,
                candidate_module._PREPARE_REFERENCE_INVALID_SENTINEL,
                candidate_module._PREPARE_TEMPORAL_INVALID_SENTINEL,
                candidate_module._PREPARE_SIZE_INVALID_SENTINEL,
            ]
        )["result"]

    def _assert_provider_source_round_trip(self, authority, source):
        scope, intended = project_priority_candidate(authority, source)
        template = candidate_module._template_payload(
            SECRET,
            scope,
            intended,
            candidate_module._empty_references(),
        )
        semantic_wire = json.loads(candidate_module._encode_wire(template))
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
        self.assertEqual(self.store.read_candidate(scope), committed)
        repeated = self.store.upsert_confirmed(
            scope,
            intended,
            expected_version=1,
        )
        self.assertEqual(repeated.version, 2)
        self.assertEqual(repeated.snapshot, intended)
        self.assertEqual(self.store.read_candidate(scope), repeated)
        return scope, intended, template, semantic_wire

    def test_exact_gmail_provider_source_prepare_commit_and_repeat(self) -> None:
        source = gmail_source()
        scope, intended, template, semantic_wire = (
            self._assert_provider_source_round_trip(gmail_authority(), source)
        )
        self.assertIs(type(template["schemaVersion"]), int)
        self.assertEqual(intended.routing_state, "unresolved")
        self.assertIsNone(intended.routing)
        self.assertIsNone(template["routing"])
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
        scope, intended, template, semantic_wire = (
            self._assert_provider_source_round_trip(imap_authority(), source)
        )
        self.assertIs(type(template["schemaVersion"]), int)
        self.assertEqual(intended.routing_state, "unresolved")
        self.assertIsNone(intended.routing)
        self.assertIsNone(template["routing"])
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
        scope, intended, template, semantic_wire = (
            self._assert_provider_source_round_trip(imap_authority(), source)
        )
        self.assertIs(type(template["schemaVersion"]), int)
        self.assertEqual(intended.routing_state, "unresolved")
        self.assertIsNone(intended.routing)
        self.assertIsNone(template["routing"])
        self.assertEqual(intended.provider_authority.labels, ())
        self.assertIsNone(semantic_wire["providerAuthority"]["labels"])
        self.assertIsNone(intended.conversation.provider_thread_id)
        self.assertIsNone(intended.conversation.rfc_root_message_id)
        self.assertIsNone(intended.conversation.rfc_message_id)
        self.assertIs(type(intended.render.unread), bool)
        self.assertIs(type(intended.render.flagged), bool)
        self.assertEqual(scope.identity.uid_validity, source["uidValidity"])
        self.assertEqual(scope.identity.imap_uid, source["imapUid"])

    def test_prepare_lua_predicates_return_exact_fixed_sentinels(self) -> None:
        scope = google_scope(message_id="real-predicate-stage")
        base = candidate_module._template_payload(
            SECRET,
            scope,
            snapshot(),
            candidate_module._empty_references(),
        )

        def encoded_with(change) -> str:
            payload = json.loads(candidate_module._encode_wire(base))
            change(payload)
            return json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )

        ready = candidate_module._routing_to_wire(ready_routing())
        assert ready is not None
        ready_with_empty_reasons = dict(ready)
        ready_with_empty_reasons["noiseReasons"] = []
        cases = (
            (
                "{",
                candidate_module._PREPARE_JSON_DECODE_INVALID_SENTINEL,
            ),
            (
                "null",
                candidate_module._PREPARE_ROOT_TYPE_INVALID_SENTINEL,
            ),
            (
                encoded_with(lambda payload: payload.__setitem__("schemaVersion", 1)),
                candidate_module._PREPARE_SCHEMA_VERSION_INVALID_SENTINEL,
            ),
            (
                encoded_with(lambda payload: payload["providerAuthority"].pop("folder")),
                candidate_module._PREPARE_PROVIDER_AUTHORITY_SHAPE_INVALID_SENTINEL,
            ),
            (
                encoded_with(
                    lambda payload: payload["providerAuthority"].__setitem__(
                        "labels", []
                    )
                ),
                candidate_module._PREPARE_LABELS_COLLECTION_INVALID_SENTINEL,
            ),
            (
                encoded_with(lambda payload: payload.__setitem__("routing", {})),
                candidate_module._PREPARE_UNRESOLVED_ROUTING_NULL_INVALID_SENTINEL,
            ),
            (
                encoded_with(
                    lambda payload: payload.__setitem__("routingState", "invalid")
                ),
                candidate_module._PREPARE_ROUTING_STATE_INVALID_SENTINEL,
            ),
            (
                encoded_with(
                    lambda payload: (
                        payload.__setitem__("routingState", "ready"),
                        payload.__setitem__("routing", {}),
                    )
                ),
                candidate_module._PREPARE_READY_ROUTING_SHAPE_INVALID_SENTINEL,
            ),
            (
                encoded_with(
                    lambda payload: (
                        payload.__setitem__("routingState", "ready"),
                        payload.__setitem__("routing", ready_with_empty_reasons),
                    )
                ),
                candidate_module._PREPARE_NOISE_REASONS_COLLECTION_INVALID_SENTINEL,
            ),
        )
        for encoded, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self._prepare_script_result(encoded), expected)

    def test_json_null_remains_cjson_null_in_canonical_positions(self) -> None:
        encoded = json.dumps(
            {
                "providerAuthority": {"labels": None},
                "routing": None,
                "readyRouting": {"noiseReasons": None},
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        script = (
            "local value=cjson.decode(ARGV[1]);"
            "return {type(value['providerAuthority']['labels']),"
            "tostring(value['providerAuthority']['labels']==cjson.null),"
            "type(value['routing']),tostring(value['routing']==cjson.null),"
            "type(value['readyRouting']['noiseReasons']),"
            "tostring(value['readyRouting']['noiseReasons']==cjson.null)}"
        )
        result = self._transport(["EVAL", script, 0, encoded])["result"]
        self.assertEqual(
            result,
            ["userdata", "true", "userdata", "true", "userdata", "true"],
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
        self.assertEqual(
            raised.exception.stage,
            "store_prepare_noise_reasons_collection_invalid",
        )
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
        template = candidate_module._encode_wire(
            candidate_module._template_payload(
                SECRET,
                scope,
                bounded,
                candidate_module._empty_references(),
            )
        )
        self.assertLessEqual(
            len(template.encode("ascii")),
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

        original = candidate_module._template_payload

        def invalid_schema(*args, **kwargs):
            payload = original(*args, **kwargs)
            payload["schemaVersion"] = 1
            return payload

        self._transport(["FLUSHDB"])
        with patch.object(
            candidate_module,
            "_template_payload",
            side_effect=invalid_schema,
        ):
            with self.assertRaises(CandidateStoreUnavailable) as schema:
                self.store.upsert_confirmed(scope, base, expected_version=0)
        self.assertEqual(
            schema.exception.stage,
            "store_prepare_schema_version_invalid",
        )

        def invalid_reference(*args, **kwargs):
            payload = original(*args, **kwargs)
            payload["positiveReferences"]["manual_priority"] = 0.5
            return payload

        with patch.object(
            candidate_module,
            "_template_payload",
            side_effect=invalid_reference,
        ):
            with self.assertRaises(CandidateStoreUnavailable) as reference:
                self.store.upsert_confirmed(scope, base, expected_version=0)
        self.assertEqual(
            reference.exception.stage,
            "store_prepare_reference_invalid",
        )

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


if __name__ == "__main__":
    unittest.main()
