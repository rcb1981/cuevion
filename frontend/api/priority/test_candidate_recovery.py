from __future__ import annotations

import unittest
from unittest.mock import patch

from .authority import PriorityMessageIdentity
from .candidate_recovery import (
    CandidateRecoveryConsumerResult,
    RecoveryQueueSynchronizationResult,
    process_priority_candidate_recovery,
    synchronize_workflow_recovery_queue,
)
from .candidate_recovery_store import (
    PriorityCandidateRecoveryClaim,
    PriorityCandidateRecoveryMailboxScope,
    PriorityCandidateRecoveryRecord,
    PriorityCandidateRecoveryScope,
    PriorityCandidateRecoveryStore,
    RecoveryAckResult,
    RecoveryCapacityExceeded,
    RecoveryEnqueueResult,
    RecoveryRetryResult,
    RecoveryStoreUnavailable,
)
from .candidate_reference_reconciliation import (
    CandidateReferenceReconciliationResult,
)
from .candidate_store import PriorityCandidateScope, PriorityCandidateStore
from .store import PriorityWorkflowScope, PriorityWorkflowStore
from .test_candidate_store import MemoryRedis, snapshot
from .test_workflow_store import SECRET, WorkflowMemoryRedis


class FakeRecoveryStore(PriorityCandidateRecoveryStore):
    def __init__(self) -> None:
        self.claims: tuple[PriorityCandidateRecoveryClaim, ...] = ()
        self.records: dict[PriorityCandidateRecoveryScope, PriorityCandidateRecoveryRecord] = {}
        self.acked: list[PriorityCandidateRecoveryClaim] = []
        self.retried: list[PriorityCandidateRecoveryClaim] = []
        self.cancelled: list[PriorityCandidateRecoveryScope] = []
        self.enqueue_result = RecoveryEnqueueResult.QUEUED
        self.retry_result = RecoveryRetryResult.RETRIED
        self.ack_result = RecoveryAckResult.COMPLETED
        self.capacity: str | None = None
        self.unavailable = False

    def enqueue(
        self,
        scope,
        *,
        workflow_version,
        authority_expires_at,
        authoritative_now,
    ):
        if self.unavailable:
            raise RecoveryStoreUnavailable()
        if self.capacity is not None:
            raise RecoveryCapacityExceeded(self.capacity)
        existing = self.records.get(scope)
        self.records[scope] = PriorityCandidateRecoveryRecord(
            scope=scope,
            workflow_version=workflow_version,
            authority_expires_at=authority_expires_at,
            enqueued_at=(authoritative_now if existing is None else existing.enqueued_at),
            updated_at=authoritative_now,
            attempt_count=0,
            generation=1 if existing is None else existing.generation + 1,
        )
        return self.enqueue_result

    def cancel(self, scope):
        if self.unavailable:
            raise RecoveryStoreUnavailable()
        self.cancelled.append(scope)
        return self.records.pop(scope, None) is not None

    def claim_due(self, mailbox_scope, *, limit=8):
        if self.unavailable:
            raise RecoveryStoreUnavailable()
        return self.claims[:limit]

    def ack(self, claim):
        if self.unavailable:
            raise RecoveryStoreUnavailable()
        self.acked.append(claim)
        return self.ack_result

    def retry(self, claim):
        if self.unavailable:
            raise RecoveryStoreUnavailable()
        self.retried.append(claim)
        return self.retry_result


def recovery_mailbox() -> PriorityCandidateRecoveryMailboxScope:
    return PriorityCandidateRecoveryMailboxScope(
        workspace_id="workspace-1",
        user_id="user-1",
        mailbox_id="mailbox-1",
        mailbox_account_identity="primary@example.com",
        provider="google",
    )


def recovery_scope(
    message_id: str = "gmail-message-1",
) -> PriorityCandidateRecoveryScope:
    return PriorityCandidateRecoveryScope(
        recovery_mailbox(),
        PriorityMessageIdentity(
            provider="google",
            provider_message_id=message_id,
        ),
    )


def candidate_scope(
    message_id: str = "gmail-message-1",
) -> PriorityCandidateScope:
    return PriorityCandidateScope(
        workspace_id="workspace-1",
        user_id="user-1",
        mailbox_id="mailbox-1",
        mailbox_account_identity="primary@example.com",
        provider="google",
        identity=PriorityMessageIdentity(
            provider="google",
            provider_message_id=message_id,
        ),
    )


def workflow_scope(
    message_id: str = "gmail-message-1",
) -> PriorityWorkflowScope:
    return PriorityWorkflowScope(
        workspace_id="workspace-1",
        user_id="user-1",
        mailbox_id="mailbox-1",
        identity=PriorityMessageIdentity(
            provider="google",
            provider_message_id=message_id,
        ),
    )


def claim_for(record: PriorityCandidateRecoveryRecord) -> PriorityCandidateRecoveryClaim:
    return PriorityCandidateRecoveryClaim(
        record=record,
        identity_digest="a" * 64,
        lease_token="b" * 64,
        claimed_at=record.updated_at,
        lease_expires_at=min(record.updated_at + 90_000, record.authority_expires_at),
        raw_record="strict-opaque-record",
    )


class CandidateRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow_redis = WorkflowMemoryRedis()
        self.workflow_store = PriorityWorkflowStore(
            self.workflow_redis,
            hmac_secret=SECRET,
        )
        self.candidate_redis = MemoryRedis(current_ms=self.workflow_redis.clock_ms)
        self.candidate_store = PriorityCandidateStore(
            self.candidate_redis,
            hmac_secret=SECRET,
        )
        self.recovery_store = FakeRecoveryStore()

    def _write(self, *, field: str, value: str, message_id: str = "gmail-message-1"):
        return self.workflow_store.write_field(
            workflow_scope(message_id),
            field=field,
            value=value,
        )

    def _queue_claim(
        self,
        workflow_record,
        *,
        message_id: str = "gmail-message-1",
        workflow_version: int | None = None,
    ):
        expiry = max(
            workflow_record.manual_expires_at or 0,
            workflow_record.waiting_expires_at or 0,
        )
        record = PriorityCandidateRecoveryRecord(
            scope=recovery_scope(message_id),
            workflow_version=workflow_version or workflow_record.version,
            authority_expires_at=expiry,
            enqueued_at=workflow_record.updated_at,
            updated_at=workflow_record.updated_at,
            attempt_count=0,
            generation=1,
        )
        self.recovery_store.claims = (claim_for(record),)
        return record

    def _process(self):
        return process_priority_candidate_recovery(
            recovery_mailbox(),
            recovery_store=self.recovery_store,
            candidate_store=self.candidate_store,
            workflow_store=self.workflow_store,
        )

    def test_positive_missing_synchronization_enqueues_actual_expiry(self) -> None:
        written = self._write(field="waiting", value="waiting_on_other")
        outcome = synchronize_workflow_recovery_queue(
            self.recovery_store,
            candidate_scope(),
            written,
            CandidateReferenceReconciliationResult.CANDIDATE_MISSING,
        )
        self.assertIs(outcome, RecoveryQueueSynchronizationResult.QUEUED)
        queued = next(iter(self.recovery_store.records.values()))
        self.assertEqual(queued.authority_expires_at, written.waiting_expires_at)
        self.assertEqual(queued.workflow_version, written.version)

    def test_returned_reply_enqueues_and_repeated_positive_write_updates(self) -> None:
        first = self._write(field="waiting", value="returned_reply")
        first_outcome = synchronize_workflow_recovery_queue(
            self.recovery_store,
            candidate_scope(),
            first,
            CandidateReferenceReconciliationResult.CANDIDATE_MISSING,
        )
        self.recovery_store.enqueue_result = RecoveryEnqueueResult.UPDATED
        second = self._write(field="manualPriority", value="priority")
        second_outcome = synchronize_workflow_recovery_queue(
            self.recovery_store,
            candidate_scope(),
            second,
            CandidateReferenceReconciliationResult.CANDIDATE_MISSING,
        )
        queued = next(iter(self.recovery_store.records.values()))
        self.assertIs(first_outcome, RecoveryQueueSynchronizationResult.QUEUED)
        self.assertIs(second_outcome, RecoveryQueueSynchronizationResult.UPDATED)
        self.assertEqual(queued.generation, 2)
        self.assertEqual(queued.workflow_version, second.version)
        self.assertEqual(queued.authority_expires_at, second.manual_expires_at)

    def test_neutral_or_reconciled_write_cancels_only_exact_pending_scope(self) -> None:
        positive = self._write(field="manualPriority", value="priority")
        synchronize_workflow_recovery_queue(
            self.recovery_store,
            candidate_scope(),
            positive,
            CandidateReferenceReconciliationResult.CANDIDATE_MISSING,
        )
        removed = self._write(field="manualPriority", value="removed")
        outcome = synchronize_workflow_recovery_queue(
            self.recovery_store,
            candidate_scope(),
            removed,
            CandidateReferenceReconciliationResult.CANDIDATE_MISSING,
        )
        self.assertIs(outcome, RecoveryQueueSynchronizationResult.CANCELLED)
        self.assertFalse(self.recovery_store.records)

        synchronize_workflow_recovery_queue(
            self.recovery_store,
            candidate_scope(),
            self._write(field="manualPriority", value="priority"),
            CandidateReferenceReconciliationResult.CANDIDATE_MISSING,
        )
        reconciled = synchronize_workflow_recovery_queue(
            self.recovery_store,
            candidate_scope(),
            self._write(field="waiting", value="waiting_on_other"),
            CandidateReferenceReconciliationResult.RECONCILED,
        )
        self.assertIs(reconciled, RecoveryQueueSynchronizationResult.CANCELLED)

    def test_remaining_manual_authority_updates_instead_of_cancelling(self) -> None:
        manual = self._write(field="manualPriority", value="priority")
        synchronize_workflow_recovery_queue(
            self.recovery_store,
            candidate_scope(),
            manual,
            CandidateReferenceReconciliationResult.CANDIDATE_MISSING,
        )
        self._write(field="waiting", value="waiting_on_other")
        self.recovery_store.enqueue_result = RecoveryEnqueueResult.UPDATED
        absent = self._write(field="waiting", value="absent")
        outcome = synchronize_workflow_recovery_queue(
            self.recovery_store,
            candidate_scope(),
            absent,
            CandidateReferenceReconciliationResult.CANDIDATE_MISSING,
        )
        queued = next(iter(self.recovery_store.records.values()))
        self.assertIs(outcome, RecoveryQueueSynchronizationResult.UPDATED)
        self.assertEqual(queued.authority_expires_at, manual.manual_expires_at)

    def test_uncertain_capacity_and_unavailable_synchronization_fail_closed(self) -> None:
        written = self._write(field="manualPriority", value="priority")
        uncertain = synchronize_workflow_recovery_queue(
            self.recovery_store,
            candidate_scope(),
            written,
            CandidateReferenceReconciliationResult.CANDIDATE_INELIGIBLE,
        )
        self.recovery_store.capacity = "mailbox"
        capacity = synchronize_workflow_recovery_queue(
            self.recovery_store,
            candidate_scope(),
            written,
            CandidateReferenceReconciliationResult.CANDIDATE_MISSING,
        )
        self.recovery_store.capacity = None
        self.recovery_store.unavailable = True
        unavailable = synchronize_workflow_recovery_queue(
            self.recovery_store,
            candidate_scope(),
            written,
            CandidateReferenceReconciliationResult.CANDIDATE_MISSING,
        )
        self.assertIs(uncertain, RecoveryQueueSynchronizationResult.UNCERTAIN)
        self.assertIs(capacity, RecoveryQueueSynchronizationResult.QUEUE_CAPACITY)
        self.assertIs(unavailable, RecoveryQueueSynchronizationResult.QUEUE_UNAVAILABLE)

    def test_candidate_already_present_reconciles_current_authority_and_acks(self) -> None:
        written = self._write(field="manualPriority", value="priority")
        self._queue_claim(written)
        candidate = self.candidate_store.upsert_confirmed(
            candidate_scope(),
            snapshot(),
            expected_version=0,
        )
        with self.assertLogs("api.priority.candidate_recovery", level="INFO") as captured:
            report = self._process()
        self.assertEqual(report.claimed, 1)
        self.assertEqual(report.completed, 1)
        self.assertEqual(report.rescheduled, 0)
        self.assertEqual(
            report.result_counts,
            ((CandidateRecoveryConsumerResult.CANDIDATE_ALREADY_PRESENT.value, 1),),
        )
        self.assertEqual(len(self.recovery_store.acked), 1)
        observed = self.candidate_store.read_candidate(candidate_scope())
        assert observed is not None
        self.assertEqual(observed.version, candidate.version + 1)
        self.assertEqual(
            observed.positive_reference_expires_at("manual_priority"),
            written.manual_expires_at,
        )
        self.assertNotIn("gmail-message-1", "\n".join(captured.output))

    def test_candidate_still_missing_is_rescheduled_for_future_provider(self) -> None:
        written = self._write(field="waiting", value="returned_reply")
        self._queue_claim(written)
        report = self._process()
        self.assertEqual(report.claimed, 1)
        self.assertEqual(report.completed, 0)
        self.assertEqual(report.rescheduled, 1)
        self.assertEqual(
            report.result_counts,
            ((CandidateRecoveryConsumerResult.PROVIDER_RECOVERY_PENDING.value, 1),),
        )
        self.assertEqual(len(self.recovery_store.retried), 1)
        self.assertFalse(self.recovery_store.acked)

    def test_absent_neutral_and_expired_current_authority_are_terminal(self) -> None:
        cases = ("absent", "removed", "cleared", "expired")
        for case in cases:
            with self.subTest(case=case):
                self.setUp()
                if case == "absent":
                    record = PriorityCandidateRecoveryRecord(
                        scope=recovery_scope(),
                        workflow_version=1,
                        authority_expires_at=self.workflow_redis.clock_ms + 10_000,
                        enqueued_at=self.workflow_redis.clock_ms,
                        updated_at=self.workflow_redis.clock_ms,
                        attempt_count=0,
                        generation=1,
                    )
                else:
                    positive = self._write(field="manualPriority", value="priority")
                    record = self._queue_claim(positive)
                    if case == "removed":
                        self._write(field="manualPriority", value="removed")
                    elif case == "cleared":
                        self._write(field="cleared", value="cleared")
                    else:
                        self.workflow_redis.clock_ms = positive.manual_expires_at
                self.recovery_store.claims = (claim_for(record),)
                report = self._process()
                self.assertEqual(report.completed, 1)
                self.assertEqual(len(self.recovery_store.acked), 1)
                self.assertFalse(self.recovery_store.retried)

    def test_newer_positive_workflow_version_wins_over_queue_metadata(self) -> None:
        first = self._write(field="waiting", value="waiting_on_other")
        self._queue_claim(first, workflow_version=first.version)
        latest = self._write(field="manualPriority", value="priority")
        self.candidate_redis.current_ms = latest.updated_at
        self.candidate_store.upsert_confirmed(
            candidate_scope(),
            snapshot(),
            expected_version=0,
        )
        report = self._process()
        self.assertEqual(report.completed, 1)
        observed = self.candidate_store.read_candidate(candidate_scope())
        assert observed is not None
        self.assertEqual(
            observed.positive_reference_expires_at("manual_priority"),
            latest.manual_expires_at,
        )
        self.assertEqual(
            observed.positive_reference_expires_at("waiting"),
            first.waiting_expires_at,
        )

    def test_second_authority_reread_observes_newer_neutral_state(self) -> None:
        positive = self._write(field="manualPriority", value="priority")
        self._queue_claim(positive)
        self.candidate_store.upsert_confirmed(
            candidate_scope(),
            snapshot(),
            expected_version=0,
        )
        neutral = self._write(field="manualPriority", value="removed")
        with patch(
            "api.priority.candidate_recovery._read_workflow",
            side_effect=(positive, neutral),
        ), patch(
            "api.priority.candidate_recovery.reconcile_workflow_candidate_references"
        ) as reconcile:
            report = self._process()
        self.assertEqual(report.completed, 1)
        self.assertEqual(
            report.result_counts,
            ((CandidateRecoveryConsumerResult.AUTHORITY_NEUTRAL.value, 1),),
        )
        self.assertEqual(len(self.recovery_store.acked), 1)
        reconcile.assert_not_called()

    def test_reconciliation_failure_retries_and_claim_loss_preserves_newer_state(self) -> None:
        written = self._write(field="manualPriority", value="priority")
        self._queue_claim(written)
        self.candidate_store.upsert_confirmed(
            candidate_scope(),
            snapshot(),
            expected_version=0,
        )
        with patch(
            "api.priority.candidate_recovery.reconcile_workflow_candidate_references",
            return_value=CandidateReferenceReconciliationResult.STORE_UNAVAILABLE,
        ):
            report = self._process()
        self.assertEqual(report.rescheduled, 1)
        self.assertEqual(
            report.result_counts,
            ((CandidateRecoveryConsumerResult.RECONCILIATION_FAILED.value, 1),),
        )

        self.recovery_store.retry_result = RecoveryRetryResult.CLAIM_LOST
        with patch(
            "api.priority.candidate_recovery.reconcile_workflow_candidate_references",
            return_value=CandidateReferenceReconciliationResult.STORE_UNAVAILABLE,
        ):
            lost = self._process()
        self.assertEqual(
            lost.result_counts,
            ((CandidateRecoveryConsumerResult.CLAIM_LOST.value, 1),),
        )
        self.assertEqual(lost.completed, 0)

    def test_malformed_claim_batch_and_reconciliation_exception_are_bounded(self) -> None:
        self.recovery_store.claims = ("invalid-claim",)
        with self.assertLogs(
            "api.priority.candidate_recovery",
            level="WARNING",
        ) as captured:
            unavailable = self._process()
        self.assertEqual(unavailable.claimed, 0)
        self.assertEqual(unavailable.result_counts, ())
        self.assertIn("outcome=store_unavailable", "\n".join(captured.output))

        self.recovery_store = FakeRecoveryStore()
        written = self._write(field="manualPriority", value="priority")
        self._queue_claim(written)
        self.candidate_store.upsert_confirmed(
            candidate_scope(),
            snapshot(),
            expected_version=0,
        )
        with patch(
            "api.priority.candidate_recovery.reconcile_workflow_candidate_references",
            side_effect=RuntimeError("private-reconciliation-detail"),
        ):
            retried = self._process()
        self.assertEqual(retried.rescheduled, 1)
        self.assertEqual(
            retried.result_counts,
            ((CandidateRecoveryConsumerResult.RECONCILIATION_FAILED.value, 1),),
        )

    def test_logs_contain_only_fixed_results(self) -> None:
        sensitive_message = "privacy-provider-message-id"
        written = self._write(
            field="waiting",
            value="waiting_on_other",
            message_id=sensitive_message,
        )
        scope = recovery_scope(sensitive_message)
        record = PriorityCandidateRecoveryRecord(
            scope=scope,
            workflow_version=written.version,
            authority_expires_at=written.waiting_expires_at,
            enqueued_at=written.updated_at,
            updated_at=written.updated_at,
            attempt_count=0,
            generation=1,
        )
        self.recovery_store.claims = (
            PriorityCandidateRecoveryClaim(
                record=record,
                identity_digest="c" * 64,
                lease_token="d" * 64,
                claimed_at=record.updated_at,
                lease_expires_at=record.updated_at + 90_000,
                raw_record="privacy-redis-key-and-secret",
            ),
        )
        with self.assertLogs("api.priority.candidate_recovery", level="INFO") as captured:
            self._process()
        output = "\n".join(captured.output)
        self.assertEqual(
            output,
            "INFO:api.priority.candidate_recovery:"
            "Priority candidate recovery consumer outcome=provider_recovery_pending",
        )
        for value in (
            sensitive_message,
            "mailbox-1",
            "primary@example.com",
            "c" * 64,
            "d" * 64,
            "privacy-redis-key-and-secret",
            SECRET,
        ):
            self.assertNotIn(value, output)


if __name__ == "__main__":
    unittest.main()
