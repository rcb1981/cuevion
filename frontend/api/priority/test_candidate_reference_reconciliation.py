from __future__ import annotations

import json
import unittest

from .candidate_reference_reconciliation import (
    CandidateReferenceReconciliationResult,
    reconcile_candidate_from_workflow_store,
    reconcile_workflow_candidate_references,
    workflow_reference_expiries,
)
from . import candidate_store as candidate_store_module
from .candidate_store import PriorityCandidateStore
from .store import PriorityWorkflowRecord, PriorityWorkflowStore
from .test_candidate_store import DAY_SECONDS, MemoryRedis, SECRET, google_scope, snapshot
from .test_workflow_store import WorkflowMemoryRedis, gmail_scope


def workflow_record(
    *,
    manual_priority: str = "none",
    cleared: str = "active",
    waiting: str = "absent",
    manual_expires_at: int = 1_800_000_000_000 + 10 * DAY_SECONDS * 1_000,
    waiting_expires_at: int = 1_800_000_000_000 + 7 * DAY_SECONDS * 1_000,
    version: int = 1,
) -> PriorityWorkflowRecord:
    return PriorityWorkflowRecord(
        manual_priority=manual_priority,
        cleared=cleared,
        waiting=waiting,
        version=version,
        updated_at=1_800_000_000_000,
        manual_expires_at=manual_expires_at,
        cleared_expires_at=1_800_000_000_000 + 10 * DAY_SECONDS * 1_000,
        waiting_expires_at=waiting_expires_at,
    )


class CandidateReferencePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = MemoryRedis()
        self.store = PriorityCandidateStore(self.redis, hmac_secret=SECRET)
        self.scope = google_scope(message_id="workflow-policy")
        self.store.upsert_confirmed(self.scope, snapshot(), expected_version=0)

    def reconcile(self, record: PriorityWorkflowRecord):
        result = reconcile_workflow_candidate_references(
            self.store,
            self.scope,
            record,
        )
        self.assertEqual(
            result,
            CandidateReferenceReconciliationResult.RECONCILED,
        )
        candidate = self.store.read_candidate(self.scope)
        assert candidate is not None
        return candidate

    def workflow_values(self, candidate) -> tuple[int, int, int]:
        return tuple(
            candidate.positive_reference_expires_at(kind)
            for kind in ("manual_priority", "waiting", "returned_reply")
        )

    def test_manual_waiting_returned_absent_and_none_map_exactly(self) -> None:
        manual_expiry = self.redis.current_ms + 6 * DAY_SECONDS * 1_000
        waiting_expiry = self.redis.current_ms + 4 * DAY_SECONDS * 1_000
        manual = self.reconcile(
            workflow_record(
                manual_priority="priority",
                waiting="absent",
                manual_expires_at=manual_expiry,
                waiting_expires_at=waiting_expiry,
            )
        )
        self.assertEqual(self.workflow_values(manual), (manual_expiry, 0, 0))

        waiting = self.reconcile(
            workflow_record(
                manual_priority="none",
                waiting="waiting_on_other",
                manual_expires_at=manual_expiry,
                waiting_expires_at=waiting_expiry,
            )
        )
        self.assertEqual(self.workflow_values(waiting), (0, waiting_expiry, 0))

        returned = self.reconcile(
            workflow_record(
                waiting="returned_reply",
                waiting_expires_at=waiting_expiry,
            )
        )
        self.assertEqual(self.workflow_values(returned), (0, 0, waiting_expiry))

        absent = self.reconcile(workflow_record())
        self.assertEqual(self.workflow_values(absent), (0, 0, 0))

    def test_removed_and_cleared_clear_all_then_active_rederives_survivors(self) -> None:
        manual_expiry = self.redis.current_ms + 8 * DAY_SECONDS * 1_000
        waiting_expiry = self.redis.current_ms + 5 * DAY_SECONDS * 1_000
        seeded = self.reconcile(
            workflow_record(
                manual_priority="priority",
                waiting="waiting_on_other",
                manual_expires_at=manual_expiry,
                waiting_expires_at=waiting_expiry,
            )
        )
        self.assertEqual(
            self.workflow_values(seeded),
            (manual_expiry, waiting_expiry, 0),
        )
        removed = self.reconcile(
            workflow_record(
                manual_priority="removed",
                waiting="returned_reply",
                waiting_expires_at=waiting_expiry,
            )
        )
        self.assertEqual(self.workflow_values(removed), (0, 0, 0))
        cleared = self.reconcile(
            workflow_record(
                manual_priority="priority",
                cleared="cleared",
                waiting="returned_reply",
                manual_expires_at=manual_expiry,
                waiting_expires_at=waiting_expiry,
            )
        )
        self.assertEqual(self.workflow_values(cleared), (0, 0, 0))
        active = self.reconcile(
            workflow_record(
                manual_priority="priority",
                cleared="active",
                waiting="returned_reply",
                manual_expires_at=manual_expiry,
                waiting_expires_at=waiting_expiry,
            )
        )
        self.assertEqual(
            self.workflow_values(active),
            (manual_expiry, 0, waiting_expiry),
        )

    def test_authoritative_expiry_stale_and_candidate_absolute_cap(self) -> None:
        initial = self.store.read_candidate(self.scope)
        assert initial is not None
        provider_times = (
            initial.provider_observed_at,
            initial.provider_validated_at,
            initial.base_expires_at,
            initial.absolute_expires_at,
        )
        nearly_expired = self.redis.current_ms + 1_234
        candidate = self.reconcile(
            workflow_record(
                manual_priority="priority",
                manual_expires_at=nearly_expired,
            )
        )
        self.assertEqual(
            candidate.positive_reference_expires_at("manual_priority"),
            nearly_expired,
        )
        candidate = self.reconcile(
            workflow_record(
                manual_priority="priority",
                manual_expires_at=candidate.absolute_expires_at + DAY_SECONDS * 1_000,
            )
        )
        self.assertEqual(
            candidate.positive_reference_expires_at("manual_priority"),
            candidate.absolute_expires_at,
        )
        candidate = self.reconcile(
            workflow_record(
                manual_priority="priority",
                manual_expires_at=self.redis.current_ms - 1,
            )
        )
        self.assertEqual(
            candidate.positive_reference_expires_at("manual_priority"),
            0,
        )
        self.assertEqual(
            (
                candidate.provider_observed_at,
                candidate.provider_validated_at,
                candidate.base_expires_at,
                candidate.absolute_expires_at,
            ),
            provider_times,
        )
        record_key = self.store._scope_keys(self.scope)["record"]
        expiry_before_read = self.redis.expires_at[record_key]
        self.store.read_candidate(self.scope)
        self.assertEqual(self.redis.expires_at[record_key], expiry_before_read)

    def test_mapping_rejects_absent_record_and_results_are_content_free(self) -> None:
        with self.assertRaises(ValueError):
            workflow_reference_expiries(PriorityWorkflowRecord())
        values = {result.value for result in CandidateReferenceReconciliationResult}
        self.assertEqual(
            values,
            {
                "candidate_reference_reconciled",
                "candidate_missing",
                "workflow_record_absent",
                "candidate_ineligible",
                "cas_conflict_exhausted",
                "store_unavailable",
            },
        )


class CandidateReferenceBoundaryTests(unittest.TestCase):
    def test_missing_candidate_and_absent_workflow_are_clean_noops(self) -> None:
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        scope = google_scope(message_id="missing-workflow-candidate")
        missing = reconcile_workflow_candidate_references(
            store,
            scope,
            workflow_record(manual_priority="priority"),
        )
        self.assertEqual(
            missing,
            CandidateReferenceReconciliationResult.CANDIDATE_MISSING,
        )
        self.assertEqual(redis.values, {})
        self.assertFalse(any(command[0] == "SCAN" for command in redis.commands))

        store.upsert_confirmed(scope, snapshot(), expected_version=0)
        version = store.read_candidate(scope).version
        absent = reconcile_workflow_candidate_references(
            store,
            scope,
            PriorityWorkflowRecord(),
        )
        self.assertEqual(
            absent,
            CandidateReferenceReconciliationResult.WORKFLOW_RECORD_ABSENT,
        )
        self.assertEqual(store.read_candidate(scope).version, version)

    def test_ineligible_grace_candidate_fails_closed_without_mutation(self) -> None:
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        scope = google_scope(message_id="workflow-ineligible")
        candidate = store.upsert_confirmed(scope, snapshot(), expected_version=0)
        grace = store.mark_provider_validation_failure(
            scope,
            expected_version=candidate.version,
        )
        assert grace is not None
        result = reconcile_workflow_candidate_references(
            store,
            scope,
            workflow_record(manual_priority="priority"),
        )
        self.assertEqual(
            result,
            CandidateReferenceReconciliationResult.CANDIDATE_INELIGIBLE,
        )
        self.assertEqual(store.read_candidate(scope), grace)

    def test_exact_workflow_read_reconciles_without_enumeration(self) -> None:
        candidate_redis = MemoryRedis(current_ms=1_700_000_000_000)
        candidate_store = PriorityCandidateStore(candidate_redis, hmac_secret=SECRET)
        candidate_scope = google_scope(
            message_id="gmail-message-1",
            workspace_id="workspace-1",
            user_id="user-1",
            mailbox_id="mailbox-1",
            account="owner@example.test",
        )
        candidate_store.upsert_confirmed(
            candidate_scope,
            snapshot(),
            expected_version=0,
        )
        workflow_redis = WorkflowMemoryRedis()
        workflow_store = PriorityWorkflowStore(workflow_redis, hmac_secret=SECRET)
        initial_version = candidate_store.read_candidate(candidate_scope).version
        absent = reconcile_candidate_from_workflow_store(
            candidate_store,
            workflow_store,
            candidate_scope,
        )
        self.assertEqual(
            absent,
            CandidateReferenceReconciliationResult.WORKFLOW_RECORD_ABSENT,
        )
        self.assertEqual(
            candidate_store.read_candidate(candidate_scope).version,
            initial_version,
        )
        accepted = workflow_store.write_field(
            gmail_scope(),
            field="manualPriority",
            value="priority",
        )
        result = reconcile_candidate_from_workflow_store(
            candidate_store,
            workflow_store,
            candidate_scope,
        )
        self.assertEqual(
            result,
            CandidateReferenceReconciliationResult.RECONCILED,
        )
        candidate = candidate_store.read_candidate(candidate_scope)
        assert candidate is not None
        self.assertEqual(
            candidate.positive_reference_expires_at("manual_priority"),
            min(accepted.manual_expires_at, candidate.absolute_expires_at),
        )
        self.assertEqual(len(workflow_redis.commands), 3)
        self.assertTrue(all(command[0] == "EVAL" for command in workflow_redis.commands))

    def test_one_conflict_retries_once_and_persistent_conflict_stops(self) -> None:
        class RacingRedis(MemoryRedis):
            def __init__(self, *, persistent: bool) -> None:
                super().__init__()
                self.persistent = persistent
                self.conflicts = 0

            def __call__(self, command: list[object]) -> dict[str, object]:
                if (
                    command[0] == "EVAL"
                    and command[1]
                    == candidate_store_module._RECONCILE_WORKFLOW_REFERENCES_SCRIPT
                    and (self.persistent or self.conflicts == 0)
                ):
                    self._expire()
                    self.commands.append(list(command))
                    key_count = int(command[2])
                    keys = command[3 : 3 + key_count]
                    payload = json.loads(self.values[keys[0]])
                    payload["version"] += 1
                    payload["updatedAt"] = self.current_ms
                    self.values[keys[0]] = self._encode(payload)
                    self.conflicts += 1
                    return {
                        "result": candidate_store_module._CONFLICT_SENTINEL
                    }
                return super().__call__(command)

        redis = RacingRedis(persistent=False)
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        scope = google_scope(message_id="cas-once")
        store.upsert_confirmed(scope, snapshot(), expected_version=0)
        result = reconcile_workflow_candidate_references(
            store,
            scope,
            workflow_record(manual_priority="priority"),
        )
        self.assertEqual(
            result,
            CandidateReferenceReconciliationResult.RECONCILED,
        )
        self.assertEqual(redis.conflicts, 1)
        valid = store.read_candidate(scope)
        assert valid is not None
        self.assertEqual(valid.version, 3)

        persistent_redis = RacingRedis(persistent=True)
        persistent_store = PriorityCandidateStore(
            persistent_redis,
            hmac_secret=SECRET,
        )
        persistent_scope = google_scope(message_id="cas-persistent")
        initial = persistent_store.upsert_confirmed(
            persistent_scope,
            snapshot(),
            expected_version=0,
        )
        exhausted = reconcile_workflow_candidate_references(
            persistent_store,
            persistent_scope,
            workflow_record(waiting="waiting_on_other"),
        )
        self.assertEqual(
            exhausted,
            CandidateReferenceReconciliationResult.CAS_CONFLICT_EXHAUSTED,
        )
        self.assertEqual(persistent_redis.conflicts, 2)
        still_valid = persistent_store.read_candidate(persistent_scope)
        assert still_valid is not None
        self.assertEqual(still_valid.version, initial.version + 2)
        self.assertEqual(
            still_valid.positive_reference_expires_at("waiting"),
            0,
        )
        self.assertEqual(
            still_valid.provider_observed_at,
            initial.provider_observed_at,
        )


if __name__ == "__main__":
    unittest.main()
