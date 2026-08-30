"""Workflow-authoritative references for already-existing Priority candidates."""

from __future__ import annotations

from enum import Enum

from .candidate_store import (
    CandidateReferenceRejected,
    CandidateStoreUnavailable,
    CandidateVersionConflict,
    PriorityCandidateRecord,
    PriorityCandidateScope,
    PriorityCandidateStore,
)
from .store import (
    PriorityWorkflowRecord,
    PriorityWorkflowScope,
    PriorityWorkflowStore,
    WorkflowStoreUnavailable,
)


class CandidateReferenceReconciliationResult(str, Enum):
    """Fixed, content-free outcomes for secondary candidate persistence."""

    RECONCILED = "candidate_reference_reconciled"
    CANDIDATE_MISSING = "candidate_missing"
    WORKFLOW_RECORD_ABSENT = "workflow_record_absent"
    CANDIDATE_INELIGIBLE = "candidate_ineligible"
    CAS_CONFLICT_EXHAUSTED = "cas_conflict_exhausted"
    STORE_UNAVAILABLE = "store_unavailable"


RECONCILIATION_FAILURE_RESULTS = frozenset(
    {
        CandidateReferenceReconciliationResult.CANDIDATE_INELIGIBLE,
        CandidateReferenceReconciliationResult.CAS_CONFLICT_EXHAUSTED,
        CandidateReferenceReconciliationResult.STORE_UNAVAILABLE,
    }
)


def workflow_reference_expiries(
    record: PriorityWorkflowRecord,
) -> tuple[int, int, int]:
    """Map one persisted workflow record to its three absolute expiries."""

    if not isinstance(record, PriorityWorkflowRecord) or record.version == 0:
        raise ValueError("persisted Priority workflow record required")
    if (
        record.manual_expires_at is None
        or record.waiting_expires_at is None
    ):
        raise ValueError("persisted Priority workflow record required")
    if record.cleared == "cleared" or record.manual_priority == "removed":
        return 0, 0, 0
    manual = (
        record.manual_expires_at
        if record.manual_priority == "priority"
        else 0
    )
    waiting = (
        record.waiting_expires_at
        if record.waiting == "waiting_on_other"
        else 0
    )
    returned_reply = (
        record.waiting_expires_at
        if record.waiting == "returned_reply"
        else 0
    )
    return manual, waiting, returned_reply


def reconcile_workflow_candidate_references(
    candidate_store: PriorityCandidateStore,
    candidate_scope: PriorityCandidateScope,
    workflow_record: PriorityWorkflowRecord,
) -> CandidateReferenceReconciliationResult:
    """Reconcile one exact candidate, with at most one CAS retry."""

    if (
        not isinstance(candidate_store, PriorityCandidateStore)
        or not isinstance(candidate_scope, PriorityCandidateScope)
        or not isinstance(workflow_record, PriorityWorkflowRecord)
    ):
        return CandidateReferenceReconciliationResult.STORE_UNAVAILABLE
    if workflow_record.version == 0:
        return CandidateReferenceReconciliationResult.WORKFLOW_RECORD_ABSENT
    try:
        manual, waiting, returned_reply = workflow_reference_expiries(
            workflow_record
        )
    except Exception:
        return CandidateReferenceReconciliationResult.STORE_UNAVAILABLE

    for attempt in range(2):
        try:
            candidate: PriorityCandidateRecord | None = (
                candidate_store.read_candidate(candidate_scope)
            )
        except Exception:
            return CandidateReferenceReconciliationResult.STORE_UNAVAILABLE
        if candidate is None:
            return CandidateReferenceReconciliationResult.CANDIDATE_MISSING
        try:
            reconciled = candidate_store.reconcile_workflow_positive_references(
                candidate_scope,
                manual_priority_expires_at=manual,
                waiting_expires_at=waiting,
                returned_reply_expires_at=returned_reply,
                expected_version=candidate.version,
            )
        except CandidateVersionConflict:
            if attempt == 0:
                continue
            return CandidateReferenceReconciliationResult.CAS_CONFLICT_EXHAUSTED
        except CandidateReferenceRejected:
            return CandidateReferenceReconciliationResult.CANDIDATE_INELIGIBLE
        except CandidateStoreUnavailable:
            return CandidateReferenceReconciliationResult.STORE_UNAVAILABLE
        except Exception:
            return CandidateReferenceReconciliationResult.STORE_UNAVAILABLE
        if reconciled is None:
            return CandidateReferenceReconciliationResult.CANDIDATE_MISSING
        return CandidateReferenceReconciliationResult.RECONCILED
    return CandidateReferenceReconciliationResult.CAS_CONFLICT_EXHAUSTED


def reconcile_candidate_from_workflow_store(
    candidate_store: PriorityCandidateStore,
    workflow_store: PriorityWorkflowStore,
    candidate_scope: PriorityCandidateScope,
) -> CandidateReferenceReconciliationResult:
    """Exact-read workflow authority for one provider-confirmed candidate."""

    if (
        not isinstance(workflow_store, PriorityWorkflowStore)
        or not isinstance(candidate_scope, PriorityCandidateScope)
    ):
        return CandidateReferenceReconciliationResult.STORE_UNAVAILABLE
    try:
        workflow_scope = PriorityWorkflowScope(
            workspace_id=candidate_scope.workspace_id,
            user_id=candidate_scope.user_id,
            mailbox_id=candidate_scope.mailbox_id,
            identity=candidate_scope.identity,
        )
        records = workflow_store.read_records((workflow_scope,))
    except (WorkflowStoreUnavailable, TypeError, ValueError, OverflowError):
        return CandidateReferenceReconciliationResult.STORE_UNAVAILABLE
    except Exception:
        return CandidateReferenceReconciliationResult.STORE_UNAVAILABLE
    if len(records) != 1:
        return CandidateReferenceReconciliationResult.STORE_UNAVAILABLE
    return reconcile_workflow_candidate_references(
        candidate_store,
        candidate_scope,
        records[0],
    )
