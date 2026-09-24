"""Preview-only mailbox outbox consumer for Priority candidate transport.

The core consumer is bounded and lease-safe. It claims only one authenticated
tenant mailbox across source generations, resolves each event against the
latest durable mailbox state, plans an idempotent Priority action, and only then
marks the outbox row processed. Infrastructure or downstream uncertainty is
retried with a fixed safe error code; claim loss never triggers a second write.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from api.priority.candidate_projection import (
    PriorityCandidatePopulationAuthority,
    populate_priority_candidates,
)
from api.priority.candidate_store import (
    PriorityCandidateStore,
    build_runtime_candidate_store,
)
from api.priority.event_reference import resolve_priority_hmac_secret
from api.priority.mailbox_outbox import (
    PriorityMailboxOutboxAction,
    PriorityMailboxOutboxActionKind,
    plan_priority_mailbox_outbox_event,
)
from api.priority.store import (
    PriorityWorkflowStore,
    build_runtime_workflow_store,
)
from cuevion_mailbox.preview_active_write import preview_active_write_enabled
from cuevion_mailbox.repository_contract import (
    OutboxEvent,
    OutboxMailboxScope,
    OutboxMessageSnapshot,
)
from cuevion_mailbox.runtime import (
    ActiveWriteMailboxRepositories,
    build_active_write_mailbox_repositories,
)


OUTBOX_PRIORITY_MAX_BATCH = 20
OUTBOX_PRIORITY_LEASE_MILLIS = 60_000
OUTBOX_PRIORITY_RETRY_BASE_MILLIS = 5_000
OUTBOX_PRIORITY_RETRY_MAX_MILLIS = 300_000

_SAFE_PROCESSING_ERROR = "priority_processing_failed"

logger = logging.getLogger(__name__)


class PriorityMailboxOutboxConsumerResult(str, Enum):
    UPSERTED = "upserted"
    REMOVED = "removed"
    SUPERSEDED = "superseded"
    STALE_GENERATION = "stale_generation"
    INELIGIBLE = "ineligible"
    RETRIED = "retried"
    CLAIM_LOST = "claim_lost"
    ACK_UNAVAILABLE = "ack_unavailable"
    RETRY_UNAVAILABLE = "retry_unavailable"


@dataclass(frozen=True, slots=True)
class PriorityMailboxOutboxConsumerReport:
    claimed: int
    processed: int
    retried: int
    result_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        allowed = {value.value for value in PriorityMailboxOutboxConsumerResult}
        if (
            type(self.claimed) is not int
            or not 0 <= self.claimed <= OUTBOX_PRIORITY_MAX_BATCH
            or type(self.processed) is not int
            or not 0 <= self.processed <= self.claimed
            or type(self.retried) is not int
            or not 0 <= self.retried <= self.claimed
            or self.processed + self.retried > self.claimed
            or type(self.result_counts) is not tuple
            or tuple(sorted(self.result_counts)) != self.result_counts
            or len({code for code, _count in self.result_counts})
            != len(self.result_counts)
            or sum(count for _code, count in self.result_counts) != self.claimed
            or any(
                code not in allowed
                or type(count) is not int
                or count < 1
                for code, count in self.result_counts
            )
        ):
            raise ValueError("invalid Priority mailbox outbox consumer report")


class _OutboxReader(Protocol):
    def resolve_outbox_message(
        self,
        event: OutboxEvent,
    ) -> OutboxMessageSnapshot | None:
        ...


class _OutboxWriter(Protocol):
    def claim_outbox_batch_for_mailbox(
        self,
        mailbox_scope: OutboxMailboxScope,
        *,
        limit: int,
        now_millis: int,
        lease_millis: int,
    ) -> Sequence[OutboxEvent]:
        ...

    def mark_outbox_processed(
        self,
        event_id: str,
        *,
        claim_token: str,
        processed_at_millis: int,
    ) -> bool:
        ...

    def mark_outbox_retry(
        self,
        event_id: str,
        *,
        claim_token: str,
        now_millis: int,
        next_attempt_at_millis: int,
        safe_error_code: str,
    ) -> bool:
        ...


PriorityOutboxActionApplier = Callable[[PriorityMailboxOutboxAction], bool]


def _retry_delay_millis(attempt_count: int) -> int:
    if type(attempt_count) is not int or attempt_count < 1:
        raise ValueError("invalid outbox attempt count")
    exponent = min(attempt_count - 1, 6)
    return min(
        OUTBOX_PRIORITY_RETRY_MAX_MILLIS,
        OUTBOX_PRIORITY_RETRY_BASE_MILLIS * (2**exponent),
    )


def apply_priority_mailbox_outbox_action(
    authority: PriorityCandidatePopulationAuthority,
    action: PriorityMailboxOutboxAction,
    *,
    candidate_store: PriorityCandidateStore,
    workflow_store: PriorityWorkflowStore,
) -> bool:
    """Apply one already-planned action idempotently to Priority transport."""

    if (
        not isinstance(authority, PriorityCandidatePopulationAuthority)
        or not isinstance(action, PriorityMailboxOutboxAction)
        or not isinstance(candidate_store, PriorityCandidateStore)
        or not isinstance(workflow_store, PriorityWorkflowStore)
    ):
        raise ValueError("invalid Priority mailbox outbox action application")

    if action.kind in {
        PriorityMailboxOutboxActionKind.SUPERSEDED,
        PriorityMailboxOutboxActionKind.STALE_GENERATION,
        PriorityMailboxOutboxActionKind.INELIGIBLE,
    }:
        return True

    if action.kind is PriorityMailboxOutboxActionKind.REMOVE:
        if action.candidate_scope is None:
            raise ValueError("invalid Priority mailbox outbox removal")
        candidate_store.remove_candidate(action.candidate_scope)
        return True

    if action.kind is not PriorityMailboxOutboxActionKind.UPSERT:
        raise ValueError("invalid Priority mailbox outbox action")
    if action.candidate_scope is None or action.source is None:
        raise ValueError("invalid Priority mailbox outbox upsert")

    report = populate_priority_candidates(
        authority,
        [action.source],
        store=candidate_store,
        workflow_store=workflow_store,
    )
    return bool(
        report.attempted == 1
        and report.processed == 1
        and report.written == 1
        and not report.incomplete
    )


def consume_priority_mailbox_outbox(
    authority: PriorityCandidatePopulationAuthority,
    *,
    reader: _OutboxReader,
    writer: _OutboxWriter,
    apply_action: PriorityOutboxActionApplier,
    now_millis: int,
    limit: int = OUTBOX_PRIORITY_MAX_BATCH,
    lease_millis: int = OUTBOX_PRIORITY_LEASE_MILLIS,
) -> PriorityMailboxOutboxConsumerReport:
    """Claim and process one bounded exact-mailbox batch."""

    if (
        not isinstance(authority, PriorityCandidatePopulationAuthority)
        or authority.provider != "google"
        or not callable(apply_action)
        or type(now_millis) is not int
        or isinstance(now_millis, bool)
        or now_millis < 0
        or type(limit) is not int
        or isinstance(limit, bool)
        or not 1 <= limit <= OUTBOX_PRIORITY_MAX_BATCH
        or type(lease_millis) is not int
        or isinstance(lease_millis, bool)
        or not 1_000 <= lease_millis <= 300_000
    ):
        raise ValueError("invalid Priority mailbox outbox consumer")

    mailbox_scope = OutboxMailboxScope(
        workspace_id=authority.workspace_id,
        owner_user_id=authority.user_id,
        mailbox_id=authority.mailbox_id,
    )
    claimed_raw = writer.claim_outbox_batch_for_mailbox(
        mailbox_scope,
        limit=limit,
        now_millis=now_millis,
        lease_millis=lease_millis,
    )
    claimed = tuple(claimed_raw)
    if (
        len(claimed) > limit
        or any(
            type(event) is not OutboxEvent
            or event.scope.workspace_id != mailbox_scope.workspace_id
            or event.scope.owner_user_id != mailbox_scope.owner_user_id
            or event.scope.mailbox_id != mailbox_scope.mailbox_id
            for event in claimed
        )
        or len({event.event_id for event in claimed}) != len(claimed)
    ):
        raise RuntimeError("invalid claimed mailbox outbox batch")

    counts: dict[str, int] = {}
    processed = 0
    retried = 0

    def record(result: PriorityMailboxOutboxConsumerResult) -> None:
        counts[result.value] = counts.get(result.value, 0) + 1

    def retry(event: OutboxEvent) -> None:
        nonlocal retried
        try:
            marked = writer.mark_outbox_retry(
                event.event_id,
                claim_token=event.claim_token,
                now_millis=now_millis,
                next_attempt_at_millis=(
                    now_millis + _retry_delay_millis(event.attempt_count)
                ),
                safe_error_code=_SAFE_PROCESSING_ERROR,
            )
        except Exception:
            record(PriorityMailboxOutboxConsumerResult.RETRY_UNAVAILABLE)
            return
        if not marked:
            record(PriorityMailboxOutboxConsumerResult.CLAIM_LOST)
            return
        retried += 1
        record(PriorityMailboxOutboxConsumerResult.RETRIED)

    for event in claimed:
        try:
            snapshot = reader.resolve_outbox_message(event)
            action = plan_priority_mailbox_outbox_event(
                authority,
                event,
                snapshot,
            )
            applied = apply_action(action)
        except Exception:
            retry(event)
            continue
        if applied is not True:
            retry(event)
            continue

        try:
            acknowledged = writer.mark_outbox_processed(
                event.event_id,
                claim_token=event.claim_token,
                processed_at_millis=now_millis,
            )
        except Exception:
            record(PriorityMailboxOutboxConsumerResult.ACK_UNAVAILABLE)
            continue
        if not acknowledged:
            record(PriorityMailboxOutboxConsumerResult.CLAIM_LOST)
            continue

        processed += 1
        result = {
            PriorityMailboxOutboxActionKind.UPSERT:
                PriorityMailboxOutboxConsumerResult.UPSERTED,
            PriorityMailboxOutboxActionKind.REMOVE:
                PriorityMailboxOutboxConsumerResult.REMOVED,
            PriorityMailboxOutboxActionKind.SUPERSEDED:
                PriorityMailboxOutboxConsumerResult.SUPERSEDED,
            PriorityMailboxOutboxActionKind.STALE_GENERATION:
                PriorityMailboxOutboxConsumerResult.STALE_GENERATION,
            PriorityMailboxOutboxActionKind.INELIGIBLE:
                PriorityMailboxOutboxConsumerResult.INELIGIBLE,
        }[action.kind]
        record(result)

    return PriorityMailboxOutboxConsumerReport(
        claimed=len(claimed),
        processed=processed,
        retried=retried,
        result_counts=tuple(sorted(counts.items())),
    )


def run_preview_priority_mailbox_outbox_consumer(
    *,
    environment: Mapping[str, str],
    workspace_id: str,
    owner_user_id: str,
    mailbox_id: str,
    mailbox_account_identity: str,
    now_millis: int,
    limit: int = OUTBOX_PRIORITY_MAX_BATCH,
    repositories: ActiveWriteMailboxRepositories | None = None,
    candidate_store: PriorityCandidateStore | None = None,
    workflow_store: PriorityWorkflowStore | None = None,
    hmac_secret: str | None = None,
) -> PriorityMailboxOutboxConsumerReport:
    """Preview active_write runtime boundary. Production cannot activate this."""

    if not preview_active_write_enabled(environment):
        raise RuntimeError("preview active mailbox write is disabled")

    authority = PriorityCandidatePopulationAuthority(
        workspace_id=workspace_id,
        user_id=owner_user_id,
        mailbox_id=mailbox_id,
        mailbox_account_identity=mailbox_account_identity.casefold(),
        provider="google",
    )
    runtime_repositories = (
        build_active_write_mailbox_repositories(environment)
        if repositories is None
        else repositories
    )

    secret = hmac_secret
    if candidate_store is None or workflow_store is None:
        secret = secret or resolve_priority_hmac_secret()
    runtime_candidate_store = (
        build_runtime_candidate_store(hmac_secret=secret)
        if candidate_store is None
        else candidate_store
    )
    runtime_workflow_store = (
        build_runtime_workflow_store(hmac_secret=secret)
        if workflow_store is None
        else workflow_store
    )

    report = consume_priority_mailbox_outbox(
        authority,
        reader=runtime_repositories.reader,
        writer=runtime_repositories.writer,
        apply_action=lambda action: apply_priority_mailbox_outbox_action(
            authority,
            action,
            candidate_store=runtime_candidate_store,
            workflow_store=runtime_workflow_store,
        ),
        now_millis=now_millis,
        limit=limit,
    )
    logger.info(
        "Priority mailbox outbox consumer claimed=%s processed=%s retried=%s outcomes=%s",
        report.claimed,
        report.processed,
        report.retried,
        ",".join(f"{code}:{count}" for code, count in report.result_counts),
    )
    return report


__all__ = (
    "OUTBOX_PRIORITY_LEASE_MILLIS",
    "OUTBOX_PRIORITY_MAX_BATCH",
    "PriorityMailboxOutboxConsumerReport",
    "PriorityMailboxOutboxConsumerResult",
    "apply_priority_mailbox_outbox_action",
    "consume_priority_mailbox_outbox",
    "run_preview_priority_mailbox_outbox_consumer",
)
