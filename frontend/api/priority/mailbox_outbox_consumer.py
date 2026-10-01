"""Mailbox outbox consumer for Priority candidate transport.

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

from api.priority.authority import PriorityMessageIdentity
from api.priority.candidate_projection import (
    MAX_CURRENT_WINDOW_CANDIDATES,
    PriorityCandidatePopulationAuthority,
    populate_priority_candidates,
)
from api.priority.candidate_store import (
    CANDIDATE_MAX_MAILBOX_RECORDS,
    CANDIDATE_MAX_PAGE_RECORDS,
    PriorityCandidateMailboxScope,
    PriorityCandidateScope,
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
    BootstrapState,
    GmailMessageMetadataProjection,
    MailboxProvider,
    MailboxReadAuthority,
    MailboxStateSnapshot,
    OutboxEvent,
    OutboxMailboxScope,
    OutboxMessageSnapshot,
)
from cuevion_mailbox.runtime import (
    ActiveWriteMailboxRepositories,
    build_active_write_mailbox_repositories,
    build_production_bootstrap_mailbox_repositories,
    production_bootstrap_authority_enabled,
)


OUTBOX_PRIORITY_MAX_BATCH = 20
OUTBOX_PRIORITY_LEASE_MILLIS = 60_000
OUTBOX_PRIORITY_RETRY_BASE_MILLIS = 5_000
OUTBOX_PRIORITY_RETRY_MAX_MILLIS = 300_000
OUTBOX_PRIORITY_PRUNE_MAX = 20

_SAFE_PROCESSING_ERROR = "priority_processing_failed"

logger = logging.getLogger(__name__)


class PriorityMailboxOutboxConsumerResult(str, Enum):
    UPSERTED = "upserted"
    REMOVED = "removed"
    SUPERSEDED = "superseded"
    STALE_GENERATION = "stale_generation"
    INELIGIBLE = "ineligible"
    OUTSIDE_WINDOW = "outside_window"
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
    def resolve_current_state(
        self,
        authority: MailboxReadAuthority,
    ) -> MailboxStateSnapshot | None:
        ...

    def list_gmail_message_metadata(
        self,
        scope,
        *,
        limit: int,
    ) -> Sequence[GmailMessageMetadataProjection]:
        ...

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


def _priority_mailbox_scope(
    authority: PriorityCandidatePopulationAuthority,
) -> PriorityCandidateMailboxScope:
    scope = PriorityCandidateMailboxScope(
        workspace_id=authority.workspace_id,
        user_id=authority.user_id,
        mailbox_id=authority.mailbox_id,
        mailbox_account_identity=authority.mailbox_account_identity,
        provider="google",
    )
    scope.canonical_bytes()
    return scope


def _read_current_gmail_window(
    authority: PriorityCandidatePopulationAuthority,
    reader: _OutboxReader,
) -> tuple[GmailMessageMetadataProjection, ...]:
    read_authority = MailboxReadAuthority(
        workspace_id=authority.workspace_id,
        owner_user_id=authority.user_id,
        mailbox_id=authority.mailbox_id,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity=authority.mailbox_account_identity,
    )
    state = reader.resolve_current_state(read_authority)
    if (
        type(state) is not MailboxStateSnapshot
        or state.bootstrap_state is not BootstrapState.READY
        or state.scope.provider is not MailboxProvider.GOOGLE
        or state.scope.provider_account_identity
        != authority.mailbox_account_identity
    ):
        raise RuntimeError("Priority Gmail current window is unavailable")

    rows = tuple(
        reader.list_gmail_message_metadata(
            state.scope,
            limit=MAX_CURRENT_WINDOW_CANDIDATES,
        )
    )
    if (
        len(rows) > MAX_CURRENT_WINDOW_CANDIDATES
        or any(type(row) is not GmailMessageMetadataProjection for row in rows)
    ):
        raise RuntimeError("invalid Priority Gmail current window")
    provider_ids: set[str] = set()
    for row in rows:
        provider_message_id = row.provider_message_id
        if (
            provider_message_id in provider_ids
            or row.provider_folder.casefold() != "inbox"
        ):
            raise RuntimeError("invalid Priority Gmail current window")
        provider_ids.add(provider_message_id)
    return rows


def _current_candidate_scope_keys(
    authority: PriorityCandidatePopulationAuthority,
    provider_message_ids: frozenset[str],
) -> frozenset[bytes]:
    keys: set[bytes] = set()
    for provider_message_id in provider_message_ids:
        scope = PriorityCandidateScope(
            workspace_id=authority.workspace_id,
            user_id=authority.user_id,
            mailbox_id=authority.mailbox_id,
            mailbox_account_identity=authority.mailbox_account_identity,
            provider="google",
            identity=PriorityMessageIdentity(
                provider="google",
                provider_message_id=provider_message_id,
            ),
        )
        keys.add(scope.canonical_bytes())
    return frozenset(keys)


def _prune_historical_candidates(
    authority: PriorityCandidatePopulationAuthority,
    *,
    current_scope_keys: frozenset[bytes],
    candidate_store: PriorityCandidateStore,
    now_millis: int,
) -> tuple[int, int]:
    mailbox_scope = _priority_mailbox_scope(authority)
    records = []
    offset = 0
    invalid_count = 0
    mailbox_incomplete = False
    total = 0

    while True:
        page = candidate_store.read_mailbox_prune_page(
            mailbox_scope,
            offset=offset,
            limit=CANDIDATE_MAX_PAGE_RECORDS,
        )
        total = page.total
        invalid_count += page.invalid_count
        mailbox_incomplete = mailbox_incomplete or page.mailbox_incomplete
        records.extend(page.records)
        if page.next_offset is None:
            break
        if page.next_offset <= offset:
            raise RuntimeError("invalid Priority candidate prune pagination")
        offset = page.next_offset

    if len(records) + invalid_count > total:
        raise RuntimeError("invalid Priority candidate prune inventory")

    stale = [
        record
        for record in records
        if record.state == "provider_confirmed"
        and record.scope.canonical_bytes() not in current_scope_keys
        and all(
            reference.expires_at <= now_millis
            for reference in record.positive_references
        )
    ]
    stale.sort(
        key=lambda record: (
            record.snapshot.render.created_at,
            record.provider_observed_at,
            record.scope.canonical_bytes(),
        )
    )

    removed = 0
    for record in stale[:OUTBOX_PRIORITY_PRUNE_MAX]:
        if candidate_store.remove_candidate(record.scope):
            removed += 1

    if mailbox_incomplete and total - removed < CANDIDATE_MAX_MAILBOX_RECORDS:
        candidate_store.clear_mailbox_incomplete(mailbox_scope)

    return removed, invalid_count


def _prepare_current_gmail_window(
    authority: PriorityCandidatePopulationAuthority,
    *,
    reader: _OutboxReader,
    candidate_store: PriorityCandidateStore,
    now_millis: int,
    reconcile: bool,
) -> frozenset[str]:
    try:
        rows = _read_current_gmail_window(authority, reader)
    except Exception:
        logger.warning(
            "Priority Gmail current window unavailable stage=durable_metadata"
        )
        raise

    current_ids = frozenset(row.provider_message_id for row in rows)
    if not reconcile:
        return current_ids

    try:
        removed, invalid_count = _prune_historical_candidates(
            authority,
            current_scope_keys=_current_candidate_scope_keys(
                authority,
                current_ids,
            ),
            candidate_store=candidate_store,
            now_millis=now_millis,
        )
    except Exception:
        logger.warning("Priority Gmail current window unavailable stage=prune")
        raise

    logger.info(
        "Priority Gmail current window records=%s pruned=%s malformed_preserved=%s",
        len(rows),
        removed,
        invalid_count,
    )
    return current_ids


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
    current_provider_message_ids: frozenset[str] | None = None,
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
        or (
            current_provider_message_ids is not None
            and (
                type(current_provider_message_ids) is not frozenset
                or any(
                    type(provider_message_id) is not str
                    or not provider_message_id
                    for provider_message_id in current_provider_message_ids
                )
            )
        )
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
            outside_window = bool(
                action.kind is PriorityMailboxOutboxActionKind.UPSERT
                and current_provider_message_ids is not None
                and action.source is not None
                and action.source.get("providerMessageId")
                not in current_provider_message_ids
            )
            applied = True if outside_window else apply_action(action)
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
        if outside_window:
            result = PriorityMailboxOutboxConsumerResult.OUTSIDE_WINDOW
        else:
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
    reconcile_current_window: bool = True,
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

    current_provider_message_ids = _prepare_current_gmail_window(
        authority,
        reader=runtime_repositories.reader,
        candidate_store=runtime_candidate_store,
        now_millis=now_millis,
        reconcile=reconcile_current_window,
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
        current_provider_message_ids=current_provider_message_ids,
    )
    logger.info(
        "Priority mailbox outbox consumer claimed=%s processed=%s retried=%s outcomes=%s",
        report.claimed,
        report.processed,
        report.retried,
        ",".join(f"{code}:{count}" for code, count in report.result_counts),
    )
    return report


def run_production_priority_mailbox_outbox_consumer(
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
    reconcile_current_window: bool = True,
) -> PriorityMailboxOutboxConsumerReport:
    """Production runtime boundary behind the existing exact bootstrap authority."""

    if not production_bootstrap_authority_enabled(environment):
        raise RuntimeError("production Priority mailbox outbox is disabled")

    authority = PriorityCandidatePopulationAuthority(
        workspace_id=workspace_id,
        user_id=owner_user_id,
        mailbox_id=mailbox_id,
        mailbox_account_identity=mailbox_account_identity.casefold(),
        provider="google",
    )
    runtime_repositories = (
        build_production_bootstrap_mailbox_repositories(environment)
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

    current_provider_message_ids = _prepare_current_gmail_window(
        authority,
        reader=runtime_repositories.reader,
        candidate_store=runtime_candidate_store,
        now_millis=now_millis,
        reconcile=reconcile_current_window,
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
        current_provider_message_ids=current_provider_message_ids,
    )
    logger.info(
        "Priority production mailbox outbox consumer claimed=%s processed=%s retried=%s outcomes=%s",
        report.claimed,
        report.processed,
        report.retried,
        ",".join(f"{code}:{count}" for code, count in report.result_counts),
    )
    return report


__all__ = (
    "OUTBOX_PRIORITY_LEASE_MILLIS",
    "OUTBOX_PRIORITY_MAX_BATCH",
    "OUTBOX_PRIORITY_PRUNE_MAX",
    "PriorityMailboxOutboxConsumerReport",
    "PriorityMailboxOutboxConsumerResult",
    "apply_priority_mailbox_outbox_action",
    "consume_priority_mailbox_outbox",
    "run_preview_priority_mailbox_outbox_consumer",
    "run_production_priority_mailbox_outbox_consumer",
)
