from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

from . import candidate_recovery_store as recovery_module
from .authority import PriorityMessageIdentity
from .candidate_recovery_store import (
    RECOVERY_MAX_ATTEMPTS,
    RECOVERY_MAX_MAILBOX_RECORDS,
    RECOVERY_MAX_USER_RECORDS,
    PriorityCandidateRecoveryMailboxScope,
    PriorityCandidateRecoveryScope,
    PriorityCandidateRecoveryStore,
    RecoveryAckResult,
    RecoveryCapacityExceeded,
    RecoveryEnqueueResult,
    RecoveryRetryResult,
    RecoveryStoreUnavailable,
)


SECRET = "priority-recovery-test-secret-more-than-thirty-two-bytes"


def mailbox_scope(
    *,
    workspace_id: str = "workspace-1",
    user_id: str = "user-1",
    mailbox_id: str = "mailbox-1",
    account: str = "primary@example.com",
    provider: str = "google",
) -> PriorityCandidateRecoveryMailboxScope:
    return PriorityCandidateRecoveryMailboxScope(
        workspace_id=workspace_id,
        user_id=user_id,
        mailbox_id=mailbox_id,
        mailbox_account_identity=account,
        provider=provider,
    )


def google_scope(
    message_id: str = "gmail-message-1",
    *,
    mailbox: PriorityCandidateRecoveryMailboxScope | None = None,
) -> PriorityCandidateRecoveryScope:
    return PriorityCandidateRecoveryScope(
        mailbox or mailbox_scope(),
        PriorityMessageIdentity(
            provider="google",
            provider_message_id=message_id,
        ),
    )


def imap_scope() -> PriorityCandidateRecoveryScope:
    return PriorityCandidateRecoveryScope(
        mailbox_scope(
            mailbox_id="imap-mailbox",
            account="imap@example.com",
            provider="custom_imap",
        ),
        PriorityMessageIdentity(
            provider="custom_imap",
            provider_folder="INBOX",
            uid_validity="77",
            imap_uid="91",
        ),
    )


@unittest.skipUnless(
    shutil.which("redis-server") and shutil.which("redis-cli"),
    "disposable Redis executables are unavailable",
)
class CandidateRecoveryRealRedisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(
            prefix="cuevion-recovery-redis-"
        )
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
        self.commands: list[list[object]] = []
        self._transport(["FLUSHDB"])
        self.commands.clear()
        self.store = PriorityCandidateRecoveryStore(
            self._transport,
            hmac_secret=SECRET,
        )

    def _transport(self, command: list[object]) -> dict[str, object]:
        self.commands.append(list(command))
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

    def _now(self) -> int:
        seconds, micros = self._transport(["TIME"])["result"]
        return int(seconds) * 1_000 + int(micros) // 1_000

    def _enqueue(
        self,
        scope: PriorityCandidateRecoveryScope,
        *,
        workflow_version: int = 1,
        lifetime_ms: int = 24 * 60 * 60 * 1_000,
    ) -> RecoveryEnqueueResult:
        now = self._now()
        return self.store.enqueue(
            scope,
            workflow_version=workflow_version,
            authority_expires_at=now + lifetime_ms,
            authoritative_now=now,
        )

    def test_enqueue_record_is_strict_private_atomic_and_ttl_bounded(self) -> None:
        scope = google_scope("privacy-provider-message")
        self.assertIs(self._enqueue(scope), RecoveryEnqueueResult.QUEUED)
        record = self.store.read_record(scope)
        assert record is not None
        self.assertEqual(record.workflow_version, 1)
        self.assertEqual(record.attempt_count, 0)
        self.assertEqual(record.generation, 1)

        keys = self.store._scope_keys(scope)
        raw = self._transport(["GET", keys["record"]])["result"]
        payload = json.loads(raw)
        self.assertEqual(
            set(payload),
            {
                "schemaVersion",
                "mailboxScopeDigest",
                "identityDigest",
                "identity",
                "workflowVersion",
                "authorityExpiresAt",
                "enqueuedAt",
                "updatedAt",
                "attemptCount",
                "generation",
                "recordMac",
            },
        )
        ttl = self._transport(["PTTL", keys["record"]])["result"]
        remaining = record.authority_expires_at - self._now()
        self.assertGreater(ttl, 0)
        self.assertLessEqual(ttl, remaining + 100)
        for key in keys.values():
            if key == keys["member"]:
                continue
            self.assertNotIn("mailbox-1", key)
            self.assertNotIn("primary@example.com", key)
            self.assertNotIn("privacy-provider-message", key)
        self.assertTrue(all(command[0] != "SCAN" for command in self.commands))
        self.assertNotIn("SCAN", recovery_module._ENQUEUE_SCRIPT)

    def test_imap_record_contains_only_exact_identity_and_queue_metadata(self) -> None:
        scope = imap_scope()
        self._enqueue(scope)
        raw = self._transport(
            ["GET", self.store._scope_keys(scope)["record"]]
        )["result"]
        payload = json.loads(raw)
        self.assertEqual(
            payload["identity"],
            {
                "provider": "custom_imap",
                "providerFolder": "INBOX",
                "uidValidity": "77",
                "imapUid": "91",
            },
        )
        forbidden = {
            "sender",
            "subject",
            "snippet",
            "body",
            "html",
            "labels",
            "credentials",
        }
        self.assertTrue(forbidden.isdisjoint(payload))

    def test_record_persists_through_new_store_instance(self) -> None:
        scope = google_scope("persisted-across-store-instance")
        self._enqueue(scope, workflow_version=7)

        reconstructed = PriorityCandidateRecoveryStore(
            self._transport,
            hmac_secret=SECRET,
        )
        record = reconstructed.read_record(scope)

        assert record is not None
        self.assertEqual(record.scope, scope)
        self.assertEqual(record.workflow_version, 7)

    def test_claims_are_isolated_by_mailbox_scope(self) -> None:
        first_mailbox = mailbox_scope(
            mailbox_id="mailbox-first",
            account="first@example.com",
        )
        second_mailbox = mailbox_scope(
            mailbox_id="mailbox-second",
            account="second@example.com",
        )
        first = google_scope("first-message", mailbox=first_mailbox)
        second = google_scope("second-message", mailbox=second_mailbox)
        self._enqueue(first)
        self._enqueue(second)

        first_claims = self.store.claim_due(first_mailbox)

        self.assertEqual(
            tuple(claim.record.scope for claim in first_claims),
            (first,),
        )
        self.assertIsNotNone(self.store.read_record(second))

    def test_claims_are_isolated_by_provider_scope(self) -> None:
        google_mailbox = mailbox_scope(
            mailbox_id="shared-mailbox",
            account="shared@example.com",
            provider="google",
        )
        imap_mailbox = mailbox_scope(
            mailbox_id="shared-mailbox",
            account="shared@example.com",
            provider="custom_imap",
        )
        google = google_scope("google-message", mailbox=google_mailbox)
        imap = PriorityCandidateRecoveryScope(
            imap_mailbox,
            PriorityMessageIdentity(
                provider="custom_imap",
                provider_folder="INBOX",
                uid_validity="77",
                imap_uid="91",
            ),
        )
        self._enqueue(google)
        self._enqueue(imap)

        google_claims = self.store.claim_due(google_mailbox)

        self.assertEqual(
            tuple(claim.record.scope for claim in google_claims),
            (google,),
        )
        self.assertIsNotNone(self.store.read_record(imap))

    def test_provider_mismatch_scope_is_rejected(self) -> None:
        mismatched = PriorityCandidateRecoveryScope(
            mailbox_scope(provider="google"),
            PriorityMessageIdentity(
                provider="custom_imap",
                provider_folder="INBOX",
                uid_validity="77",
                imap_uid="91",
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "invalid Priority candidate recovery scope",
        ):
            mismatched.canonical_bytes()

    def test_imap_uidvalidity_namespaces_never_converge(self) -> None:
        mailbox = mailbox_scope(
            mailbox_id="imap-mailbox",
            account="imap@example.com",
            provider="custom_imap",
        )
        old = PriorityCandidateRecoveryScope(
            mailbox,
            PriorityMessageIdentity(
                provider="custom_imap",
                provider_folder="INBOX",
                uid_validity="77",
                imap_uid="91",
            ),
        )
        new = PriorityCandidateRecoveryScope(
            mailbox,
            PriorityMessageIdentity(
                provider="custom_imap",
                provider_folder="INBOX",
                uid_validity="78",
                imap_uid="91",
            ),
        )

        self.assertNotEqual(
            self.store._scope_keys(old)["member"],
            self.store._scope_keys(new)["member"],
        )
        self._enqueue(old)
        self._enqueue(new)
        self.assertEqual(self.store.read_record(old).scope, old)
        self.assertEqual(self.store.read_record(new).scope, new)
        self.assertEqual(
            {claim.record.scope for claim in self.store.claim_due(mailbox)},
            {old, new},
        )

    def test_repeated_enqueue_deduplicates_updates_generation_and_breaks_lease(self) -> None:
        scope = google_scope("dedupe")
        first_now = self._now()
        first_expiry = first_now + 10_000_000
        self.store.enqueue(
            scope,
            workflow_version=1,
            authority_expires_at=first_expiry,
            authoritative_now=first_now,
        )
        claim = self.store.claim_due(scope.mailbox_scope)[0]
        second_now = self._now()
        second_expiry = second_now + 20_000_000
        self.assertIs(
            self.store.enqueue(
                scope,
                workflow_version=2,
                authority_expires_at=second_expiry,
                authoritative_now=second_now,
            ),
            RecoveryEnqueueResult.UPDATED,
        )
        updated = self.store.read_record(scope)
        assert updated is not None
        self.assertEqual(updated.workflow_version, 2)
        self.assertEqual(updated.generation, 2)
        self.assertEqual(updated.attempt_count, 0)
        self.assertEqual(updated.enqueued_at, first_now)
        keys = self.store._scope_keys(scope)
        self.assertEqual(self._transport(["ZCARD", keys["due"]])["result"], 1)
        self.assertIsNone(self._transport(["GET", keys["lease"]])["result"])
        self.assertIs(self.store.ack(claim), RecoveryAckResult.CLAIM_LOST)
        self.assertIs(self.store.retry(claim), RecoveryRetryResult.CLAIM_LOST)
        self.assertEqual(self.store.read_record(scope), updated)

    def test_cancel_removes_record_indexes_and_lease_atomically(self) -> None:
        scope = google_scope("cancel")
        self._enqueue(scope)
        self.store.claim_due(scope.mailbox_scope)
        keys = self.store._scope_keys(scope)
        self.assertTrue(self.store.cancel(scope))
        self.assertFalse(self.store.cancel(scope))
        for key in ("record", "lease", "due", "expiry", "user"):
            self.assertEqual(self._transport(["EXISTS", keys[key]])["result"], 0)

    def test_claim_is_bounded_and_equal_scores_are_digest_lexicographic(self) -> None:
        scopes = tuple(google_scope(f"ordered-{index}") for index in range(10))
        for scope in scopes:
            self._enqueue(scope)
        keys = self.store._mailbox_keys(scopes[0].mailbox_scope)
        same_due = self._now()
        members = [self.store._scope_keys(scope)["member"] for scope in scopes]
        self._transport(
            ["ZADD", keys["due"], *sum(([same_due, member] for member in members), [])]
        )
        claims = self.store.claim_due(scopes[0].mailbox_scope)
        self.assertEqual(len(claims), 8)
        self.assertEqual(
            [claim.identity_digest for claim in claims],
            sorted(members)[:8],
        )
        self.assertTrue(
            all(
                claim.lease_expires_at - claim.claimed_at
                <= recovery_module.RECOVERY_LEASE_TTL_MILLISECONDS
                for claim in claims
            )
        )

    def test_ack_removes_record_all_indexes_and_lease(self) -> None:
        scope = google_scope("ack")
        self._enqueue(scope)
        claim = self.store.claim_due(scope.mailbox_scope)[0]
        self.assertIs(self.store.ack(claim), RecoveryAckResult.COMPLETED)
        self.assertIs(self.store.ack(claim), RecoveryAckResult.CLAIM_LOST)
        keys = self.store._scope_keys(scope)
        for key in ("record", "lease", "due", "expiry", "user"):
            self.assertEqual(self._transport(["EXISTS", keys[key]])["result"], 0)

    def test_retry_uses_fixed_schedule_and_same_generation(self) -> None:
        scope = google_scope("retry")
        self._enqueue(scope, lifetime_ms=60 * 24 * 60 * 60 * 1_000)
        keys = self.store._scope_keys(scope)
        expected_delays = (
            60_000,
            5 * 60_000,
            30 * 60_000,
            6 * 60 * 60_000,
            24 * 60 * 60_000,
            7 * 24 * 60 * 60_000,
            7 * 24 * 60 * 60_000,
        )
        generation = None
        for attempt, expected_delay in enumerate(expected_delays, start=1):
            self._transport(["ZADD", keys["due"], self._now(), keys["member"]])
            claim = self.store.claim_due(scope.mailbox_scope)[0]
            generation = generation or claim.record.generation
            self.assertIs(self.store.retry(claim), RecoveryRetryResult.RETRIED)
            retried = self.store.read_record(scope)
            assert retried is not None
            self.assertEqual(retried.attempt_count, attempt)
            self.assertEqual(retried.generation, generation)
            due = int(
                float(
                    self._transport(
                        ["ZSCORE", keys["due"], keys["member"]]
                    )["result"]
                )
            )
            self.assertEqual(due, retried.updated_at + expected_delay)
            self.assertLess(due, retried.authority_expires_at)

    def test_retry_ceiling_terminally_removes_thirty_second_attempt(self) -> None:
        scope = google_scope("retry-ceiling")
        self._enqueue(scope, lifetime_ms=180 * 24 * 60 * 60 * 1_000)
        keys = self.store._scope_keys(scope)
        for attempt in range(1, RECOVERY_MAX_ATTEMPTS + 1):
            self._transport(["ZADD", keys["due"], self._now(), keys["member"]])
            claim = self.store.claim_due(scope.mailbox_scope)[0]
            result = self.store.retry(claim)
            if attempt < RECOVERY_MAX_ATTEMPTS:
                self.assertIs(result, RecoveryRetryResult.RETRIED)
            else:
                self.assertIs(result, RecoveryRetryResult.ATTEMPTS_EXHAUSTED)
        self.assertIsNone(self.store.read_record(scope))

    def test_retry_never_schedules_at_or_after_authority_expiry(self) -> None:
        scope = google_scope("retry-expiry")
        self._enqueue(scope, lifetime_ms=30_000)
        claim = self.store.claim_due(scope.mailbox_scope)[0]
        self.assertIs(
            self.store.retry(claim),
            RecoveryRetryResult.AUTHORITY_EXPIRED,
        )
        self.assertIsNone(self.store.read_record(scope))

    def test_mailbox_capacity_has_no_partial_extra_state(self) -> None:
        mailbox = mailbox_scope()
        for index in range(RECOVERY_MAX_MAILBOX_RECORDS):
            self._enqueue(google_scope(f"mailbox-cap-{index}", mailbox=mailbox))
        extra = google_scope("mailbox-cap-extra", mailbox=mailbox)
        with self.assertRaises(RecoveryCapacityExceeded) as captured:
            self._enqueue(extra)
        self.assertEqual(captured.exception.scope_kind, "mailbox")
        self.assertIsNone(self.store.read_record(extra))
        keys = self.store._mailbox_keys(mailbox)
        extra_keys = self.store._scope_keys(extra)
        self.assertEqual(
            self._transport(["ZCARD", keys["due"]])["result"],
            RECOVERY_MAX_MAILBOX_RECORDS,
        )
        self.assertEqual(
            self._transport(["EXISTS", extra_keys["record"]])["result"],
            0,
        )
        for index_key in ("due", "expiry", "user"):
            self.assertIsNone(
                self._transport(
                    ["ZSCORE", extra_keys[index_key], extra_keys["member"]]
                )["result"]
            )

    def test_user_capacity_across_mailboxes_has_no_partial_extra_state(self) -> None:
        for mailbox_index in range(
            RECOVERY_MAX_USER_RECORDS // RECOVERY_MAX_MAILBOX_RECORDS
        ):
            mailbox = mailbox_scope(
                mailbox_id=f"user-cap-mailbox-{mailbox_index}",
                account=f"user-cap-{mailbox_index}@example.com",
            )
            for message_index in range(RECOVERY_MAX_MAILBOX_RECORDS):
                self._enqueue(
                    google_scope(
                        f"user-cap-{mailbox_index}-{message_index}",
                        mailbox=mailbox,
                    )
                )
        extra_mailbox = mailbox_scope(
            mailbox_id="user-cap-extra-mailbox",
            account="user-cap-extra@example.com",
        )
        extra = google_scope("user-cap-extra", mailbox=extra_mailbox)
        with self.assertRaises(RecoveryCapacityExceeded) as captured:
            self._enqueue(extra)
        self.assertEqual(captured.exception.scope_kind, "user")
        self.assertIsNone(self.store.read_record(extra))
        extra_keys = self.store._scope_keys(extra)
        user_key = extra_keys["user"]
        self.assertEqual(
            self._transport(["ZCARD", user_key])["result"],
            RECOVERY_MAX_USER_RECORDS,
        )
        self.assertEqual(
            self._transport(["EXISTS", extra_keys["record"]])["result"],
            0,
        )
        for index_key in ("due", "expiry", "user"):
            self.assertIsNone(
                self._transport(
                    ["ZSCORE", extra_keys[index_key], extra_keys["member"]]
                )["result"]
            )

    def test_expired_record_is_pruned_through_bounded_indexes(self) -> None:
        mailbox = mailbox_scope()
        expired = google_scope("expires", mailbox=mailbox)
        self._enqueue(expired, lifetime_ms=150)
        time.sleep(0.2)
        replacement = google_scope("after-expiry", mailbox=mailbox)
        self._enqueue(replacement)
        self.assertIsNone(self.store.read_record(expired))
        keys = self.store._mailbox_keys(mailbox)
        self.assertEqual(self._transport(["ZCARD", keys["due"]])["result"], 1)
        self.assertTrue(all(command[0] != "SCAN" for command in self.commands))

    def test_corrupt_due_record_fails_claim_closed_without_lease(self) -> None:
        scope = google_scope("corrupt")
        self._enqueue(scope)
        keys = self.store._scope_keys(scope)
        self._transport(["SET", keys["record"], "not-json", "KEEPTTL"])
        with self.assertRaises(RecoveryStoreUnavailable):
            self.store.claim_due(scope.mailbox_scope)
        self.assertIsNone(self._transport(["GET", keys["lease"]])["result"])


if __name__ == "__main__":
    unittest.main()
