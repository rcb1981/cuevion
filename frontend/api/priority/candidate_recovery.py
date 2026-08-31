"""Workflow-authority synchronization and provider-free recovery consumption."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .candidate_recovery_store import (
    RECOVERY_MAX_CLAIM_RECORDS,
    PriorityCandidateRecoveryClaim,
    PriorityCandidateRecoveryMailboxScope,
    PriorityCandidateRecoveryScope,
    PriorityCandidateRecoveryStore,
    RecoveryAckResult,
    RecoveryCapacityExceeded,
    RecoveryEnqueueResult,
    RecoveryRetryResult,
    build_runtime_recovery_store,
)
from .candidate_reference_reconciliation import (
    CandidateReferenceReconciliationResult,
    reconcile_workflow_candidate_references,
    workflow_reference_expiries,
)
from .candidate_store import (
    PriorityCandidateScope,
    PriorityCandidateStore,
)
from .event_reference import resolve_priority_hmac_secret
from .store import (
    PriorityWorkflowRecord,
    PriorityWorkflowScope,
    PriorityWorkflowStore,
)


logger = logging.getLogger(__name__)


class RecoveryQueueSynchronizationResult(str, Enum):
    NOT_NEEDED = "recovery_not_needed"
    QUEUED = "recovery_queued"
    UPDATED = "recovery_updated"
    CANCELLED = "recovery_cancelled"
    UNCERTAIN = "recovery_not_synchronized"
    QUEUE_CAPACITY = "queue_capacity"
    QUEUE_UNAVAILABLE = "queue_unavailable"


_SYNCHRONIZATION_WARNING_RESULTS = frozenset(
    {
        RecoveryQueueSynchronizationResult.UNCERTAIN,
        RecoveryQueueSynchronizationResult.QUEUE_CAPACITY,
        RecoveryQueueSynchronizationResult.QUEUE_UNAVAILABLE,
    }
)


class CandidateRecoveryConsumerResult(str, Enum):
    CANDIDATE_ALREADY_PRESENT = "candidate_already_present"
    AUTHORITY_ABSENT = "authority_absent"
    AUTHORITY_NEUTRAL = "authority_neutral"
    AUTHORITY_EXPIRED = "authority_expired"
    PROVIDER_RECOVERY_PENDING = "provider_recovery_pending"
    PROVIDER_RECOVERED = "provider_recovered"
    PROVIDER_TERMINAL_ABSENT = "provider_terminal_absent"
    RECONCILIATION_FAILED = "reconciliation_failed"
    RETRY_EXHAUSTED = "retry_exhausted"
    CLAIM_LOST = "claim_lost"
    STORE_UNAVAILABLE = "store_unavailable"


class ProviderCandidateRecoveryResult(str, Enum):
    RECOVERED = "recovered"
    TERMINAL_ABSENT = "terminal_absent"
    RETRY = "retry"


ProviderCandidateRecoveryCallback = Callable[
    [PriorityCandidateRecoveryScope],
    ProviderCandidateRecoveryResult,
]


_CONSUMER_WARNING_RESULTS = frozenset(
    {
        CandidateRecoveryConsumerResult.RECONCILIATION_FAILED,
        CandidateRecoveryConsumerResult.RETRY_EXHAUSTED,
        CandidateRecoveryConsumerResult.CLAIM_LOST,
        CandidateRecoveryConsumerResult.STORE_UNAVAILABLE,
    }
)


@dataclass(frozen=True, slots=True)
class CandidateRecoveryConsumerReport:
    claimed: int
    completed: int
    rescheduled: int
    result_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        allowed = {result.value for result in CandidateRecoveryConsumerResult}
        if (
            type(self.claimed) is not int
            or not 0 <= self.claimed <= RECOVERY_MAX_CLAIM_RECORDS
            or type(self.completed) is not int
            or not 0 <= self.completed <= self.claimed
            or type(self.rescheduled) is not int
            or not 0 <= self.rescheduled <= self.claimed
            or self.completed + self.rescheduled > self.claimed
            or type(self.result_counts) is not tuple
            or tuple(sorted(self.result_counts)) != self.result_counts
            or len({code for code, _count in self.result_counts})
            != len(self.result_counts)
            or sum(count for _code, count in self.result_counts) != self.claimed
            or any(
                code not in allowed
                or type(count) is not int
                or not 1 <= count <= RECOVERY_MAX_CLAIM_RECORDS
                for code, count in self.result_counts
            )
        ):
            raise ValueError("invalid Priority candidate recovery report")


def _log_synchronization(outcome: RecoveryQueueSynchronizationResult) -> None:
    message = "Priority workflow recovery queue synchronization outcome=%s"
    if outcome in _SYNCHRONIZATION_WARNING_RESULTS:
        logger.warning(message, outcome.value)
    else:
        logger.info(message, outcome.value)


def _mailbox_scope_from_candidate(
    candidate_scope: PriorityCandidateScope,
) -> PriorityCandidateRecoveryMailboxScope:
    if not isinstance(candidate_scope, PriorityCandidateScope):
        raise ValueError("invalid Priority candidate recovery synchronization scope")
    return PriorityCandidateRecoveryMailboxScope(
        workspace_id=candidate_scope.workspace_id,
        user_id=candidate_scope.user_id,
        mailbox_id=candidate_scope.mailbox_id,
        mailbox_account_identity=candidate_scope.mailbox_account_identity.casefold(),
        provider=candidate_scope.provider,
    )


def _recovery_scope_from_candidate(
    candidate_scope: PriorityCandidateScope,
) -> PriorityCandidateRecoveryScope:
    return PriorityCandidateRecoveryScope(
        mailbox_scope=_mailbox_scope_from_candidate(candidate_scope),
        identity=candidate_scope.identity,
    )


def _authority_expiry(record: PriorityWorkflowRecord) -> int:
    try:
        return max(workflow_reference_expiries(record))
    except Exception:
        return 0


def synchronize_workflow_recovery_queue(
    recovery_store: PriorityCandidateRecoveryStore,
    candidate_scope: PriorityCandidateScope,
    workflow_record: PriorityWorkflowRecord,
    reconciliation: CandidateReferenceReconciliationResult,
) -> RecoveryQueueSynchronizationResult:
    """Synchronize one accepted workflow write without contacting a provider."""

    if (
        not isinstance(recovery_store, PriorityCandidateRecoveryStore)
        or not isinstance(candidate_scope, PriorityCandidateScope)
        or not isinstance(workflow_record, PriorityWorkflowRecord)
        or not isinstance(reconciliation, CandidateReferenceReconciliationResult)
        or workflow_record.version == 0
        or workflow_record.updated_at is None
    ):
        return RecoveryQueueSynchronizationResult.QUEUE_UNAVAILABLE
    if reconciliation in {
        CandidateReferenceReconciliationResult.CANDIDATE_INELIGIBLE,
        CandidateReferenceReconciliationResult.CAS_CONFLICT_EXHAUSTED,
        CandidateReferenceReconciliationResult.STORE_UNAVAILABLE,
        CandidateReferenceReconciliationResult.WORKFLOW_RECORD_ABSENT,
    }:
        return RecoveryQueueSynchronizationResult.UNCERTAIN

    try:
        recovery_scope = _recovery_scope_from_candidate(candidate_scope)
        if reconciliation is CandidateReferenceReconciliationResult.RECONCILED:
            return (
                RecoveryQueueSynchronizationResult.CANCELLED
                if recovery_store.cancel(recovery_scope)
                else RecoveryQueueSynchronizationResult.NOT_NEEDED
            )
        if reconciliation is not CandidateReferenceReconciliationResult.CANDIDATE_MISSING:
            return RecoveryQueueSynchronizationResult.UNCERTAIN
        authority_expires_at = _authority_expiry(workflow_record)
        if authority_expires_at <= workflow_record.updated_at:
            return (
                RecoveryQueueSynchronizationResult.CANCELLED
                if recovery_store.cancel(recovery_scope)
                else RecoveryQueueSynchronizationResult.NOT_NEEDED
            )
        enqueue = recovery_store.enqueue(
            recovery_scope,
            workflow_version=workflow_record.version,
            authority_expires_at=authority_expires_at,
            authoritative_now=workflow_record.updated_at,
        )
        if enqueue is RecoveryEnqueueResult.QUEUED:
            return RecoveryQueueSynchronizationResult.QUEUED
        if enqueue is RecoveryEnqueueResult.UPDATED:
            return RecoveryQueueSynchronizationResult.UPDATED
        return RecoveryQueueSynchronizationResult.NOT_NEEDED
    except RecoveryCapacityExceeded:
        return RecoveryQueueSynchronizationResult.QUEUE_CAPACITY
    except Exception:
        return RecoveryQueueSynchronizationResult.QUEUE_UNAVAILABLE


def synchronize_runtime_workflow_recovery_queue(
    candidate_scope: PriorityCandidateScope,
    workflow_record: PriorityWorkflowRecord,
    reconciliation: CandidateReferenceReconciliationResult,
    *,
    recovery_store: PriorityCandidateRecoveryStore | None = None,
    hmac_secret: str | None = None,
) -> RecoveryQueueSynchronizationResult:
    """Total workflow-route boundary. This function logs once and never raises."""

    try:
        if reconciliation in {
            CandidateReferenceReconciliationResult.CANDIDATE_INELIGIBLE,
            CandidateReferenceReconciliationResult.CAS_CONFLICT_EXHAUSTED,
            CandidateReferenceReconciliationResult.STORE_UNAVAILABLE,
            CandidateReferenceReconciliationResult.WORKFLOW_RECORD_ABSENT,
        }:
            outcome = RecoveryQueueSynchronizationResult.UNCERTAIN
        else:
            runtime_store = recovery_store
            if runtime_store is None:
                secret = hmac_secret or resolve_priority_hmac_secret()
                runtime_store = build_runtime_recovery_store(hmac_secret=secret)
            outcome = synchronize_workflow_recovery_queue(
                runtime_store,
                candidate_scope,
                workflow_record,
                reconciliation,
            )
    except Exception:
        outcome = RecoveryQueueSynchronizationResult.QUEUE_UNAVAILABLE
    _log_synchronization(outcome)
    return outcome


def _workflow_scope_for_recovery(
    recovery_scope: PriorityCandidateRecoveryScope,
) -> PriorityWorkflowScope:
    mailbox = recovery_scope.mailbox_scope
    return PriorityWorkflowScope(
        workspace_id=mailbox.workspace_id,
        user_id=mailbox.user_id,
        mailbox_id=mailbox.mailbox_id,
        identity=recovery_scope.identity,
    )


def _candidate_scope_for_recovery(
    recovery_scope: PriorityCandidateRecoveryScope,
) -> PriorityCandidateScope:
    mailbox = recovery_scope.mailbox_scope
    return PriorityCandidateScope(
        workspace_id=mailbox.workspace_id,
        user_id=mailbox.user_id,
        mailbox_id=mailbox.mailbox_id,
        mailbox_account_identity=mailbox.mailbox_account_identity,
        provider=mailbox.provider,
        identity=recovery_scope.identity,
    )


def _read_workflow(
    workflow_store: PriorityWorkflowStore,
    recovery_scope: PriorityCandidateRecoveryScope,
) -> PriorityWorkflowRecord | None:
    try:
        records = workflow_store.read_records(
            (_workflow_scope_for_recovery(recovery_scope),)
        )
    except Exception:
        return None
    return records[0] if len(records) == 1 else None


def _retry_result(
    recovery_store: PriorityCandidateRecoveryStore,
    claim,
) -> tuple[CandidateRecoveryConsumerResult, bool, bool]:
    """Return consumer result plus completed/rescheduled flags."""

    try:
        retried = recovery_store.retry(claim)
    except Exception:
        return CandidateRecoveryConsumerResult.STORE_UNAVAILABLE, False, False
    if retried is RecoveryRetryResult.RETRIED:
        return CandidateRecoveryConsumerResult.RECONCILIATION_FAILED, False, True
    if retried is RecoveryRetryResult.AUTHORITY_EXPIRED:
        return CandidateRecoveryConsumerResult.AUTHORITY_EXPIRED, True, False
    if retried is RecoveryRetryResult.ATTEMPTS_EXHAUSTED:
        return CandidateRecoveryConsumerResult.RETRY_EXHAUSTED, True, False
    return CandidateRecoveryConsumerResult.CLAIM_LOST, False, False


def _ack_result(
    recovery_store: PriorityCandidateRecoveryStore,
    claim,
    completed_result: CandidateRecoveryConsumerResult,
) -> tuple[CandidateRecoveryConsumerResult, bool, bool]:
    try:
        ack = recovery_store.ack(claim)
    except Exception:
        return CandidateRecoveryConsumerResult.STORE_UNAVAILABLE, False, False
    if ack is RecoveryAckResult.COMPLETED:
        return completed_result, True, False
    return CandidateRecoveryConsumerResult.CLAIM_LOST, False, False


def process_priority_candidate_recovery(
    mailbox_scope: PriorityCandidateRecoveryMailboxScope,
    *,
    recovery_store: PriorityCandidateRecoveryStore,
    candidate_store: PriorityCandidateStore,
    workflow_store: PriorityWorkflowStore,
    limit: int = RECOVERY_MAX_CLAIM_RECORDS,
    provider_recovery: ProviderCandidateRecoveryCallback | None = None,
) -> CandidateRecoveryConsumerReport:
    """Reconcile candidates, optionally recovering a missing provider record."""

    if (
        not isinstance(mailbox_scope, PriorityCandidateRecoveryMailboxScope)
        or not isinstance(recovery_store, PriorityCandidateRecoveryStore)
        or not isinstance(candidate_store, PriorityCandidateStore)
        or not isinstance(workflow_store, PriorityWorkflowStore)
        or type(limit) is not int
        or not 1 <= limit <= RECOVERY_MAX_CLAIM_RECORDS
        or (provider_recovery is not None and not callable(provider_recovery))
    ):
        raise ValueError("invalid Priority candidate recovery consumer")
    try:
        claims = recovery_store.claim_due(mailbox_scope, limit=limit)
    except Exception:
        logger.warning(
            "Priority candidate recovery consumer outcome=%s",
            CandidateRecoveryConsumerResult.STORE_UNAVAILABLE.value,
        )
        return CandidateRecoveryConsumerReport(0, 0, 0, ())
    if (
        type(claims) is not tuple
        or len(claims) > limit
        or any(
            not isinstance(claim, PriorityCandidateRecoveryClaim)
            or claim.record.scope.mailbox_scope != mailbox_scope
            for claim in claims
        )
        or len({claim.identity_digest for claim in claims}) != len(claims)
    ):
        logger.warning(
            "Priority candidate recovery consumer outcome=%s",
            CandidateRecoveryConsumerResult.STORE_UNAVAILABLE.value,
        )
        return CandidateRecoveryConsumerReport(0, 0, 0, ())

    counts: dict[str, int] = {}
    completed = 0
    rescheduled = 0

    def finish(
        result: CandidateRecoveryConsumerResult,
        is_completed: bool,
        is_rescheduled: bool,
    ) -> None:
        nonlocal completed, rescheduled
        counts[result.value] = counts.get(result.value, 0) + 1
        completed += int(is_completed)
        rescheduled += int(is_rescheduled)
        message = "Priority candidate recovery consumer outcome=%s"
        if result in _CONSUMER_WARNING_RESULTS:
            logger.warning(message, result.value)
        else:
            logger.info(message, result.value)

    for claim in claims:
        current = _read_workflow(workflow_store, claim.record.scope)
        if current is None:
            finish(*_retry_result(recovery_store, claim))
            continue
        if current.version == 0:
            finish(
                *_ack_result(
                    recovery_store,
                    claim,
                    CandidateRecoveryConsumerResult.AUTHORITY_ABSENT,
                )
            )
            continue
        if _authority_expiry(current) == 0:
            finish(
                *_ack_result(
                    recovery_store,
                    claim,
                    CandidateRecoveryConsumerResult.AUTHORITY_NEUTRAL,
                )
            )
            continue

        candidate_scope = _candidate_scope_for_recovery(claim.record.scope)
        try:
            candidate = candidate_store.read_candidate(candidate_scope)
        except Exception:
            finish(*_retry_result(recovery_store, claim))
            continue
        provider_recovered = False
        if candidate is None and provider_recovery is None:
            result, is_completed, is_rescheduled = _retry_result(
                recovery_store, claim
            )
            if result is CandidateRecoveryConsumerResult.RECONCILIATION_FAILED:
                result = CandidateRecoveryConsumerResult.PROVIDER_RECOVERY_PENDING
            finish(result, is_completed, is_rescheduled)
            continue

        if candidate is None:
            try:
                provider_result = provider_recovery(claim.record.scope)
            except Exception:
                provider_result = ProviderCandidateRecoveryResult.RETRY
            if not isinstance(
                provider_result,
                ProviderCandidateRecoveryResult,
            ):
                provider_result = ProviderCandidateRecoveryResult.RETRY

            latest = _read_workflow(workflow_store, claim.record.scope)
            if latest is None:
                finish(*_retry_result(recovery_store, claim))
                continue
            if latest.version == 0:
                finish(
                    *_ack_result(
                        recovery_store,
                        claim,
                        CandidateRecoveryConsumerResult.AUTHORITY_ABSENT,
                    )
                )
                continue
            if _authority_expiry(latest) == 0:
                finish(
                    *_ack_result(
                        recovery_store,
                        claim,
                        CandidateRecoveryConsumerResult.AUTHORITY_NEUTRAL,
                    )
                )
                continue
            if (
                provider_result
                is ProviderCandidateRecoveryResult.TERMINAL_ABSENT
            ):
                finish(
                    *_ack_result(
                        recovery_store,
                        claim,
                        CandidateRecoveryConsumerResult.PROVIDER_TERMINAL_ABSENT,
                    )
                )
                continue
            if provider_result is ProviderCandidateRecoveryResult.RETRY:
                result, is_completed, is_rescheduled = _retry_result(
                    recovery_store, claim
                )
                if (
                    result
                    is CandidateRecoveryConsumerResult.RECONCILIATION_FAILED
                ):
                    result = (
                        CandidateRecoveryConsumerResult.PROVIDER_RECOVERY_PENDING
                    )
                finish(result, is_completed, is_rescheduled)
                continue
            try:
                candidate = candidate_store.read_candidate(candidate_scope)
            except Exception:
                finish(*_retry_result(recovery_store, claim))
                continue
            if candidate is None:
                result, is_completed, is_rescheduled = _retry_result(
                    recovery_store, claim
                )
                if (
                    result
                    is CandidateRecoveryConsumerResult.RECONCILIATION_FAILED
                ):
                    result = (
                        CandidateRecoveryConsumerResult.PROVIDER_RECOVERY_PENDING
                    )
                finish(result, is_completed, is_rescheduled)
                continue
            provider_recovered = True
        else:
            latest = _read_workflow(workflow_store, claim.record.scope)

        if latest is None:
            finish(*_retry_result(recovery_store, claim))
            continue
        if latest.version == 0:
            finish(
                *_ack_result(
                    recovery_store,
                    claim,
                    CandidateRecoveryConsumerResult.AUTHORITY_ABSENT,
                )
            )
            continue
        if _authority_expiry(latest) == 0:
            finish(
                *_ack_result(
                    recovery_store,
                    claim,
                    CandidateRecoveryConsumerResult.AUTHORITY_NEUTRAL,
                )
            )
            continue
        try:
            reconciliation = reconcile_workflow_candidate_references(
                candidate_store,
                candidate_scope,
                latest,
            )
        except Exception:
            finish(*_retry_result(recovery_store, claim))
            continue
        if reconciliation is CandidateReferenceReconciliationResult.RECONCILED:
            finish(
                *_ack_result(
                    recovery_store,
                    claim,
                    (
                        CandidateRecoveryConsumerResult.PROVIDER_RECOVERED
                        if provider_recovered
                        else CandidateRecoveryConsumerResult.CANDIDATE_ALREADY_PRESENT
                    ),
                )
            )
            continue
        if reconciliation is CandidateReferenceReconciliationResult.CANDIDATE_MISSING:
            result, is_completed, is_rescheduled = _retry_result(
                recovery_store, claim
            )
            if result is CandidateRecoveryConsumerResult.RECONCILIATION_FAILED:
                result = CandidateRecoveryConsumerResult.PROVIDER_RECOVERY_PENDING
            finish(result, is_completed, is_rescheduled)
            continue
        finish(*_retry_result(recovery_store, claim))

    return CandidateRecoveryConsumerReport(
        claimed=len(claims),
        completed=completed,
        rescheduled=rescheduled,
        result_counts=tuple(sorted(counts.items())),
    )
