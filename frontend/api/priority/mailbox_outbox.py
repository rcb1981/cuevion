"""Pure durable-mailbox outbox planning for Priority candidate transport.

This module performs no PostgreSQL, Redis, provider, or route I/O. It turns one
claimed mailbox outbox event plus the exact latest durable row for that event's
current generation into a deterministic downstream action. Older outbox events
are explicitly superseded so delayed delivery can never roll Priority state
back after a newer mailbox row version.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from api.priority.authority import PriorityMessageIdentity
from api.priority.candidate_projection import (
    CandidateProjectionInvalid,
    PriorityCandidatePopulationAuthority,
    project_priority_candidate,
)
from api.priority.candidate_store import PriorityCandidateScope
from cuevion_mailbox.repository_contract import (
    MailboxProvider,
    OutboxEvent,
    OutboxEventType,
    OutboxMessageSnapshot,
    OutboxStorageScope,
)


class PriorityMailboxOutboxActionKind(str, Enum):
    UPSERT = "upsert"
    REMOVE = "remove"
    SUPERSEDED = "superseded"
    STALE_GENERATION = "stale_generation"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True, slots=True)
class PriorityMailboxOutboxAction:
    kind: PriorityMailboxOutboxActionKind
    event_id: str
    candidate_scope: PriorityCandidateScope | None
    source: dict | None

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not PriorityMailboxOutboxActionKind
            or type(self.event_id) is not str
            or not self.event_id
        ):
            raise ValueError("invalid Priority mailbox outbox action")
        if self.kind is PriorityMailboxOutboxActionKind.UPSERT:
            if (
                not isinstance(self.candidate_scope, PriorityCandidateScope)
                or type(self.source) is not dict
            ):
                raise ValueError("invalid Priority mailbox outbox action")
            return
        if self.kind is PriorityMailboxOutboxActionKind.REMOVE:
            if (
                not isinstance(self.candidate_scope, PriorityCandidateScope)
                or self.source is not None
            ):
                raise ValueError("invalid Priority mailbox outbox action")
            return
        if self.candidate_scope is not None or self.source is not None:
            raise ValueError("invalid Priority mailbox outbox action")


def _scope_for_google_message(
    authority: PriorityCandidatePopulationAuthority,
    provider_message_id: str,
) -> PriorityCandidateScope:
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
    scope.canonical_bytes()
    return scope


def _candidate_source(snapshot: OutboxMessageSnapshot) -> dict:
    record = snapshot.record
    identity = record.identity
    return {
        "provider": "google",
        "providerMessageId": identity.provider_message_id,
        "providerThreadId": record.provider_thread_id,
        "providerFolder": identity.provider_folder.upper(),
        "labels": list(record.provider_labels),
        "senderDisplay": record.sender_display or "",
        "senderAddress": record.sender_address or "",
        "subject": record.subject,
        "snippet": record.snippet,
        "unread": record.unread,
        "flagged": record.starred,
        "providerTimestampMillis": str(record.provider_timestamp_millis),
        "rfcDate": None,
    }


def _validate_authority(
    authority: PriorityCandidatePopulationAuthority,
    event: OutboxEvent,
    snapshot: OutboxMessageSnapshot | None,
) -> None:
    if (
        not isinstance(authority, PriorityCandidatePopulationAuthority)
        or type(event) is not OutboxEvent
        or type(event.scope) is not OutboxStorageScope
        or authority.provider != "google"
        or event.scope.workspace_id != authority.workspace_id
        or event.scope.owner_user_id != authority.user_id
        or event.scope.mailbox_id != authority.mailbox_id
    ):
        raise ValueError("invalid Priority mailbox outbox authority")

    if snapshot is None:
        return
    scope = snapshot.scope
    if (
        scope.workspace_id != event.scope.workspace_id
        or scope.owner_user_id != event.scope.owner_user_id
        or scope.mailbox_id != event.scope.mailbox_id
        or scope.source_generation != event.scope.source_generation
        or scope.provider is not MailboxProvider.GOOGLE
        or scope.provider_account_identity != authority.mailbox_account_identity
        or snapshot.record.identity.message_id != event.message_id
    ):
        raise ValueError("invalid Priority mailbox outbox authority")


def plan_priority_mailbox_outbox_event(
    authority: PriorityCandidatePopulationAuthority,
    event: OutboxEvent,
    snapshot: OutboxMessageSnapshot | None,
) -> PriorityMailboxOutboxAction:
    """Plan one idempotent Priority transport action from exact durable state."""

    _validate_authority(authority, event, snapshot)
    if snapshot is None:
        return PriorityMailboxOutboxAction(
            PriorityMailboxOutboxActionKind.STALE_GENERATION,
            event.event_id,
            None,
            None,
        )

    if snapshot.row_version < event.message_row_version:
        raise ValueError("invalid Priority mailbox outbox row version")
    if snapshot.row_version > event.message_row_version:
        return PriorityMailboxOutboxAction(
            PriorityMailboxOutboxActionKind.SUPERSEDED,
            event.event_id,
            None,
            None,
        )

    provider_message_id = snapshot.record.identity.provider_message_id
    if type(provider_message_id) is not str or not provider_message_id:
        raise ValueError("invalid Priority mailbox outbox message identity")

    if event.event_type is OutboxEventType.MESSAGE_DELETED:
        if snapshot.provider_deleted is not True:
            raise ValueError("invalid Priority mailbox outbox deletion")
        return PriorityMailboxOutboxAction(
            PriorityMailboxOutboxActionKind.REMOVE,
            event.event_id,
            _scope_for_google_message(authority, provider_message_id),
            None,
        )

    if event.event_type not in {
        OutboxEventType.MESSAGE_ADDED,
        OutboxEventType.MESSAGE_CHANGED,
    } or snapshot.provider_deleted:
        raise ValueError("invalid Priority mailbox outbox mutation")

    source = _candidate_source(snapshot)
    try:
        candidate_scope, _candidate_snapshot = project_priority_candidate(
            authority,
            source,
        )
    except CandidateProjectionInvalid:
        return PriorityMailboxOutboxAction(
            PriorityMailboxOutboxActionKind.INELIGIBLE,
            event.event_id,
            None,
            None,
        )

    return PriorityMailboxOutboxAction(
        PriorityMailboxOutboxActionKind.UPSERT,
        event.event_id,
        candidate_scope,
        source,
    )


__all__ = (
    "PriorityMailboxOutboxAction",
    "PriorityMailboxOutboxActionKind",
    "plan_priority_mailbox_outbox_event",
)
