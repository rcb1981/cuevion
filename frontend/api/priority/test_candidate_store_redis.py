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
from .test_candidate_projection import gmail_authority, gmail_source
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
        self.assertEqual(raised.exception.stage, "store_script_rejected")
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
