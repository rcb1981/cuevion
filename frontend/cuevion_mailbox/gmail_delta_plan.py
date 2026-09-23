"""Pure Gmail durable delta planning.

The planner turns already-projected Gmail MessageRecord values plus explicitly
supplied current durable projections/cursor state into a validated
ProviderDeltaCommit. It performs no I/O, reads no environment, generates no
random values, and never infers deletion from a bounded Gmail snapshot.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import replace

from cuevion_mailbox.gmail_projection import derive_gmail_message_id
from cuevion_mailbox.repository_contract import (
    BodyState,
    BootstrapState,
    MailboxProvider,
    MailboxScope,
    MessageMutation,
    MessageMutationKind,
    MessageProjection,
    MessageRecord,
    OutboxEventType,
    ProviderDeltaCommit,
    SyncCursor,
)


_MAX_GMAIL_DELTA_MESSAGES = 100
_MAX_GMAIL_RECOVERY_PROVIDER_MESSAGES = 100
_MAX_GMAIL_RECOVERY_DURABLE_MESSAGES = 100
_MAX_GMAIL_RECOVERY_COMBINED_MESSAGES = 200
_GMAIL_SCOPE_KEY = "gmail-account"


def _opaque_token(prefix: str, material: bytes) -> str:
    token = base64.urlsafe_b64encode(
        hashlib.sha256(material).digest()[:16]
    ).decode("ascii").rstrip("=")
    if len(token) != 22:
        raise RuntimeError("invalid Gmail durable commit plan")
    return prefix + token


def derive_gmail_event_id(
    scope: MailboxScope,
    message_id: str,
    resulting_row_version: int,
    event_type: OutboxEventType,
) -> str:
    """Derive an idempotent outbox id for one resulting durable row version."""

    if (
        type(scope) is not MailboxScope
        or scope.provider is not MailboxProvider.GOOGLE
        or type(message_id) is not str
        or len(message_id) != 26
        or not message_id.startswith("mbm_")
        or type(resulting_row_version) is not int
        or resulting_row_version < 1
        or type(event_type) is not OutboxEventType
    ):
        raise ValueError("invalid Gmail durable commit plan")
    material = "\x1f".join(
        (
            scope.workspace_id,
            scope.owner_user_id,
            scope.mailbox_id,
            str(scope.source_generation),
            scope.provider.value,
            scope.provider_account_identity,
            message_id,
            str(resulting_row_version),
            event_type.value,
        )
    ).encode("utf-8", errors="strict")
    return _opaque_token("mbe_", material)


def _provider_message_id_from_record(
    scope: MailboxScope,
    record: MessageRecord,
) -> str:
    if type(record) is not MessageRecord:
        raise ValueError("invalid Gmail durable commit plan")
    record.validate_for(MailboxProvider.GOOGLE)
    if record.body_state is not BodyState.NOT_CACHED:
        raise ValueError("invalid Gmail durable commit plan")
    provider_message_id = record.identity.provider_message_id
    if type(provider_message_id) is not str or not provider_message_id:
        raise ValueError("invalid Gmail durable commit plan")
    if (
        record.identity.message_id
        != derive_gmail_message_id(scope, provider_message_id)
    ):
        raise ValueError("invalid Gmail durable commit plan")
    return provider_message_id


def _provider_message_id_from_projection(
    scope: MailboxScope,
    projection: MessageProjection,
) -> str:
    if type(projection) is not MessageProjection:
        raise ValueError("invalid Gmail durable commit plan")
    projection.validate_for(MailboxProvider.GOOGLE)
    provider_message_id = projection.identity.provider_message_id
    if type(provider_message_id) is not str or not provider_message_id:
        raise ValueError("invalid Gmail durable commit plan")
    if (
        projection.identity.message_id
        != derive_gmail_message_id(scope, provider_message_id)
    ):
        raise ValueError("invalid Gmail durable commit plan")
    return provider_message_id


def _current_projection_index(
    scope: MailboxScope,
    current_projections: list[MessageProjection] | tuple[MessageProjection, ...],
    *,
    maximum: int = _MAX_GMAIL_DELTA_MESSAGES,
) -> dict[str, MessageProjection]:
    if (
        type(maximum) is not int
        or maximum < 0
        or type(current_projections) not in (list, tuple)
        or len(current_projections) > maximum
    ):
        raise ValueError("invalid Gmail durable commit plan")
    result: dict[str, MessageProjection] = {}
    for projection in current_projections:
        provider_message_id = _provider_message_id_from_projection(
            scope,
            projection,
        )
        if provider_message_id in result:
            raise ValueError("invalid Gmail durable commit plan")
        result[provider_message_id] = projection
    return result


def _preserve_existing_body_state(
    record: MessageRecord,
    current: MessageProjection,
) -> MessageRecord:
    if current.provider_deleted:
        return record
    if record.body_state is not current.body_state:
        return replace(record, body_state=current.body_state)
    return record


def _projection_matches_record(
    projection: MessageProjection,
    record: MessageRecord,
) -> bool:
    return (
        projection.identity == record.identity
        and projection.provider_thread_id == record.provider_thread_id
        and projection.metadata_hash == record.metadata_hash
        and projection.body_state is record.body_state
        and projection.unread is record.unread
        and projection.starred is record.starred
        and projection.provider_deleted is False
    )


def plan_gmail_message_mutations(
    scope: MailboxScope,
    records: list[MessageRecord] | tuple[MessageRecord, ...],
    current_projections: list[MessageProjection] | tuple[MessageProjection, ...],
) -> tuple[MessageMutation, ...]:
    """Plan bounded Gmail UPSERT mutations without inferring deletions."""

    if (
        type(scope) is not MailboxScope
        or scope.provider is not MailboxProvider.GOOGLE
        or type(records) not in (list, tuple)
        or len(records) > _MAX_GMAIL_DELTA_MESSAGES
    ):
        raise ValueError("invalid Gmail durable commit plan")

    current_by_provider_id = _current_projection_index(
        scope,
        current_projections,
    )
    seen_records: set[str] = set()
    mutations: list[MessageMutation] = []

    for record in records:
        provider_message_id = _provider_message_id_from_record(scope, record)
        if provider_message_id in seen_records:
            raise ValueError("invalid Gmail durable commit plan")
        seen_records.add(provider_message_id)

        current = current_by_provider_id.get(provider_message_id)
        if current is None:
            resulting_row_version = 1
            event_type = OutboxEventType.MESSAGE_ADDED
            mutation = MessageMutation(
                kind=MessageMutationKind.UPSERT,
                identity=record.identity,
                record=record,
                expected_row_version=None,
                event_id=derive_gmail_event_id(
                    scope,
                    record.identity.message_id,
                    resulting_row_version,
                    event_type,
                ),
                outbox_event_type=event_type,
            )
        else:
            planned_record = _preserve_existing_body_state(record, current)
            if _projection_matches_record(current, planned_record):
                continue
            resulting_row_version = current.row_version + 1
            event_type = OutboxEventType.MESSAGE_CHANGED
            mutation = MessageMutation(
                kind=MessageMutationKind.UPSERT,
                identity=planned_record.identity,
                record=planned_record,
                expected_row_version=current.row_version,
                event_id=derive_gmail_event_id(
                    scope,
                    planned_record.identity.message_id,
                    resulting_row_version,
                    event_type,
                ),
                outbox_event_type=event_type,
            )

        mutation.validate_for(MailboxProvider.GOOGLE)
        mutations.append(mutation)

    return tuple(mutations)


def _validated_absent_provider_message_ids(
    values: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    if type(values) not in (list, tuple) or len(values) > _MAX_GMAIL_DELTA_MESSAGES:
        raise ValueError("invalid Gmail durable history plan")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if type(value) is not str or not value:
            raise ValueError("invalid Gmail durable history plan")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeError:
            raise ValueError("invalid Gmail durable history plan") from None
        if not 1 <= len(encoded) <= 1_024 or value in seen:
            raise ValueError("invalid Gmail durable history plan")
        seen.add(value)
        result.append(value)
    return tuple(result)


def plan_gmail_history_message_mutations(
    scope: MailboxScope,
    records: list[MessageRecord] | tuple[MessageRecord, ...],
    verified_absent_provider_message_ids: list[str] | tuple[str, ...],
    current_projections: list[MessageProjection] | tuple[MessageProjection, ...],
) -> tuple[MessageMutation, ...]:
    """Plan exact History upserts and provider-verified Inbox tombstones.

    Absence is actionable only when the caller explicitly supplies a provider
    message id after an exact provider read established that the message is no
    longer in the tracked Inbox. Missing rows that were never durable are a
    no-op. Already-tombstoned rows are also a no-op.
    """

    if (
        type(scope) is not MailboxScope
        or scope.provider is not MailboxProvider.GOOGLE
        or type(records) not in (list, tuple)
        or len(records) > _MAX_GMAIL_DELTA_MESSAGES
    ):
        raise ValueError("invalid Gmail durable history plan")

    absent = _validated_absent_provider_message_ids(
        verified_absent_provider_message_ids
    )
    record_ids: list[str] = []
    seen_record_ids: set[str] = set()
    for record in records:
        provider_message_id = _provider_message_id_from_record(scope, record)
        if provider_message_id in seen_record_ids:
            raise ValueError("invalid Gmail durable history plan")
        seen_record_ids.add(provider_message_id)
        record_ids.append(provider_message_id)

    absent_set = set(absent)
    affected_ids = seen_record_ids | absent_set
    if (
        seen_record_ids.intersection(absent_set)
        or len(affected_ids) > _MAX_GMAIL_DELTA_MESSAGES
    ):
        raise ValueError("invalid Gmail durable history plan")

    current_by_provider_id = _current_projection_index(
        scope,
        current_projections,
    )
    if not set(current_by_provider_id).issubset(affected_ids):
        raise ValueError("invalid Gmail durable history plan")

    mutations = list(
        plan_gmail_message_mutations(
            scope,
            records,
            current_projections,
        )
    )
    for provider_message_id in absent:
        current = current_by_provider_id.get(provider_message_id)
        if current is None or current.provider_deleted:
            continue
        resulting_row_version = current.row_version + 1
        event_type = OutboxEventType.MESSAGE_DELETED
        mutation = MessageMutation(
            kind=MessageMutationKind.TOMBSTONE,
            identity=current.identity,
            record=None,
            expected_row_version=current.row_version,
            event_id=derive_gmail_event_id(
                scope,
                current.identity.message_id,
                resulting_row_version,
                event_type,
            ),
            outbox_event_type=event_type,
        )
        mutation.validate_for(MailboxProvider.GOOGLE)
        mutations.append(mutation)

    return tuple(mutations)


def _validated_recovery_provider_message_ids(
    values: list[str] | tuple[str, ...],
    *,
    maximum: int,
) -> tuple[str, ...]:
    if (
        type(maximum) is not int
        or maximum < 0
        or type(values) not in (list, tuple)
        or len(values) > maximum
    ):
        raise ValueError("invalid Gmail stale recovery plan")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if type(value) is not str or not value:
            raise ValueError("invalid Gmail stale recovery plan")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeError:
            raise ValueError("invalid Gmail stale recovery plan") from None
        if not 1 <= len(encoded) <= 1_024 or value in seen:
            raise ValueError("invalid Gmail stale recovery plan")
        seen.add(value)
        result.append(value)
    return tuple(result)


def plan_gmail_stale_recovery_message_mutations(
    scope: MailboxScope,
    provider_inventory_message_ids: list[str] | tuple[str, ...],
    records: list[MessageRecord] | tuple[MessageRecord, ...],
    verified_terminal_absent_provider_message_ids: list[str] | tuple[str, ...],
    active_current_projections: list[MessageProjection] | tuple[MessageProjection, ...],
    current_projections_for_records: list[MessageProjection] | tuple[MessageProjection, ...],
) -> tuple[MessageMutation, ...]:
    """Reconcile one proven-complete bounded Inbox after stale Gmail History.

    The caller must enumerate the complete provider Inbox within the recovery
    bound and exactly recover every listed provider message. The record IDs plus
    terminal-absent IDs must therefore account for the provider inventory
    exactly. Every active durable row omitted from the recovered record set may
    then be tombstoned without inferring deletion from a partial snapshot.
    """

    if (
        type(scope) is not MailboxScope
        or scope.provider is not MailboxProvider.GOOGLE
        or type(records) not in (list, tuple)
        or len(records) > _MAX_GMAIL_RECOVERY_PROVIDER_MESSAGES
        or type(active_current_projections) not in (list, tuple)
        or len(active_current_projections) > _MAX_GMAIL_RECOVERY_DURABLE_MESSAGES
        or type(current_projections_for_records) not in (list, tuple)
        or len(current_projections_for_records) > _MAX_GMAIL_RECOVERY_PROVIDER_MESSAGES
    ):
        raise ValueError("invalid Gmail stale recovery plan")

    provider_inventory = _validated_recovery_provider_message_ids(
        provider_inventory_message_ids,
        maximum=_MAX_GMAIL_RECOVERY_PROVIDER_MESSAGES,
    )
    terminal_absent = _validated_recovery_provider_message_ids(
        verified_terminal_absent_provider_message_ids,
        maximum=_MAX_GMAIL_RECOVERY_PROVIDER_MESSAGES,
    )

    record_by_provider_id: dict[str, MessageRecord] = {}
    for record in records:
        provider_message_id = _provider_message_id_from_record(scope, record)
        if provider_message_id in record_by_provider_id:
            raise ValueError("invalid Gmail stale recovery plan")
        record_by_provider_id[provider_message_id] = record

    recovered_ids = set(record_by_provider_id)
    terminal_absent_ids = set(terminal_absent)
    provider_inventory_ids = set(provider_inventory)
    if (
        recovered_ids.intersection(terminal_absent_ids)
        or recovered_ids.union(terminal_absent_ids) != provider_inventory_ids
    ):
        raise ValueError("invalid Gmail stale recovery plan")

    active_by_provider_id = _current_projection_index(
        scope,
        active_current_projections,
        maximum=_MAX_GMAIL_RECOVERY_DURABLE_MESSAGES,
    )
    if any(
        projection.provider_deleted
        for projection in active_by_provider_id.values()
    ):
        raise ValueError("invalid Gmail stale recovery plan")

    exact_by_provider_id = _current_projection_index(
        scope,
        current_projections_for_records,
        maximum=_MAX_GMAIL_RECOVERY_PROVIDER_MESSAGES,
    )
    if not set(exact_by_provider_id).issubset(recovered_ids):
        raise ValueError("invalid Gmail stale recovery plan")

    combined_current = dict(active_by_provider_id)
    for provider_message_id, projection in exact_by_provider_id.items():
        existing = combined_current.get(provider_message_id)
        if existing is not None and existing != projection:
            raise ValueError("invalid Gmail stale recovery plan")
        combined_current[provider_message_id] = projection
    if len(combined_current) > _MAX_GMAIL_RECOVERY_COMBINED_MESSAGES:
        raise ValueError("invalid Gmail stale recovery plan")

    recovered_current = [
        combined_current[provider_message_id]
        for provider_message_id in record_by_provider_id
        if provider_message_id in combined_current
    ]
    mutations = list(
        plan_gmail_message_mutations(
            scope,
            list(records),
            recovered_current,
        )
    )

    for provider_message_id, current in active_by_provider_id.items():
        if provider_message_id in recovered_ids:
            continue
        resulting_row_version = current.row_version + 1
        event_type = OutboxEventType.MESSAGE_DELETED
        mutation = MessageMutation(
            kind=MessageMutationKind.TOMBSTONE,
            identity=current.identity,
            record=None,
            expected_row_version=current.row_version,
            event_id=derive_gmail_event_id(
                scope,
                current.identity.message_id,
                resulting_row_version,
                event_type,
            ),
            outbox_event_type=event_type,
        )
        mutation.validate_for(MailboxProvider.GOOGLE)
        mutations.append(mutation)

    if len(mutations) > _MAX_GMAIL_RECOVERY_COMBINED_MESSAGES:
        raise ValueError("invalid Gmail stale recovery plan")
    return tuple(mutations)


def _validate_cursor_transition(
    current_cursor: SyncCursor | None,
    next_cursor: SyncCursor,
) -> int | None:
    if (
        type(next_cursor) is not SyncCursor
        or next_cursor.provider is not MailboxProvider.GOOGLE
        or next_cursor.scope_key != _GMAIL_SCOPE_KEY
    ):
        raise ValueError("invalid Gmail durable commit plan")

    if current_cursor is None:
        if next_cursor.row_version != 1:
            raise ValueError("invalid Gmail durable commit plan")
        return None

    if (
        type(current_cursor) is not SyncCursor
        or current_cursor.provider is not MailboxProvider.GOOGLE
        or current_cursor.scope_key != _GMAIL_SCOPE_KEY
        or current_cursor.cursor_generation != next_cursor.cursor_generation
        or next_cursor.row_version != current_cursor.row_version + 1
        or int(next_cursor.gmail_history_id) < int(current_cursor.gmail_history_id)
    ):
        raise ValueError("invalid Gmail durable commit plan")
    return current_cursor.row_version


def build_gmail_delta_commit(
    scope: MailboxScope,
    records: list[MessageRecord] | tuple[MessageRecord, ...],
    current_projections: list[MessageProjection] | tuple[MessageProjection, ...],
    *,
    expected_state_row_version: int,
    current_cursor: SyncCursor | None,
    next_cursor: SyncCursor,
    committed_at_millis: int,
    next_bootstrap_state: BootstrapState,
) -> ProviderDeltaCommit:
    """Build a validated Gmail ProviderDeltaCommit from explicit durable state."""

    if (
        type(scope) is not MailboxScope
        or scope.provider is not MailboxProvider.GOOGLE
        or type(expected_state_row_version) is not int
        or expected_state_row_version < 1
        or type(committed_at_millis) is not int
        or committed_at_millis < 0
        or type(next_bootstrap_state) is not BootstrapState
    ):
        raise ValueError("invalid Gmail durable commit plan")

    expected_cursor_row_version = _validate_cursor_transition(
        current_cursor,
        next_cursor,
    )
    mutations = plan_gmail_message_mutations(
        scope,
        records,
        current_projections,
    )
    return ProviderDeltaCommit(
        scope=scope,
        scope_key=_GMAIL_SCOPE_KEY,
        expected_state_row_version=expected_state_row_version,
        expected_cursor_row_version=expected_cursor_row_version,
        expected_cursor_generation=next_cursor.cursor_generation,
        committed_at_millis=committed_at_millis,
        mutations=mutations,
        next_cursor=next_cursor,
        next_bootstrap_state=next_bootstrap_state,
    )




def build_gmail_history_delta_commit(
    scope: MailboxScope,
    records: list[MessageRecord] | tuple[MessageRecord, ...],
    verified_absent_provider_message_ids: list[str] | tuple[str, ...],
    current_projections: list[MessageProjection] | tuple[MessageProjection, ...],
    *,
    expected_state_row_version: int,
    current_cursor: SyncCursor,
    next_cursor: SyncCursor,
    committed_at_millis: int,
    next_bootstrap_state: BootstrapState,
) -> ProviderDeltaCommit:
    """Build one CAS-protected commit for a complete exact Gmail History window."""

    if type(current_cursor) is not SyncCursor:
        raise ValueError("invalid Gmail durable history plan")

    base = build_gmail_delta_commit(
        scope,
        records,
        current_projections,
        expected_state_row_version=expected_state_row_version,
        current_cursor=current_cursor,
        next_cursor=next_cursor,
        committed_at_millis=committed_at_millis,
        next_bootstrap_state=next_bootstrap_state,
    )
    mutations = plan_gmail_history_message_mutations(
        scope,
        records,
        verified_absent_provider_message_ids,
        current_projections,
    )
    return ProviderDeltaCommit(
        scope=base.scope,
        scope_key=base.scope_key,
        expected_state_row_version=base.expected_state_row_version,
        expected_cursor_row_version=base.expected_cursor_row_version,
        expected_cursor_generation=base.expected_cursor_generation,
        committed_at_millis=base.committed_at_millis,
        mutations=mutations,
        next_cursor=base.next_cursor,
        next_bootstrap_state=base.next_bootstrap_state,
    )


def build_gmail_stale_recovery_commit(
    scope: MailboxScope,
    provider_inventory_message_ids: list[str] | tuple[str, ...],
    records: list[MessageRecord] | tuple[MessageRecord, ...],
    verified_terminal_absent_provider_message_ids: list[str] | tuple[str, ...],
    active_current_projections: list[MessageProjection] | tuple[MessageProjection, ...],
    current_projections_for_records: list[MessageProjection] | tuple[MessageProjection, ...],
    *,
    expected_state_row_version: int,
    current_cursor: SyncCursor,
    next_cursor: SyncCursor,
    committed_at_millis: int,
    next_bootstrap_state: BootstrapState,
) -> ProviderDeltaCommit:
    """Build one atomic reconciliation + fresh cursor commit after stale History."""

    if (
        type(current_cursor) is not SyncCursor
        or type(expected_state_row_version) is not int
        or expected_state_row_version < 1
        or type(committed_at_millis) is not int
        or committed_at_millis < 0
        or type(next_bootstrap_state) is not BootstrapState
    ):
        raise ValueError("invalid Gmail stale recovery plan")

    expected_cursor_row_version = _validate_cursor_transition(
        current_cursor,
        next_cursor,
    )
    mutations = plan_gmail_stale_recovery_message_mutations(
        scope,
        provider_inventory_message_ids,
        records,
        verified_terminal_absent_provider_message_ids,
        active_current_projections,
        current_projections_for_records,
    )
    return ProviderDeltaCommit(
        scope=scope,
        scope_key=_GMAIL_SCOPE_KEY,
        expected_state_row_version=expected_state_row_version,
        expected_cursor_row_version=expected_cursor_row_version,
        expected_cursor_generation=next_cursor.cursor_generation,
        committed_at_millis=committed_at_millis,
        mutations=mutations,
        next_cursor=next_cursor,
        next_bootstrap_state=next_bootstrap_state,
    )


__all__ = (
    "build_gmail_delta_commit",
    "build_gmail_history_delta_commit",
    "build_gmail_stale_recovery_commit",
    "derive_gmail_event_id",
    "plan_gmail_history_message_mutations",
    "plan_gmail_message_mutations",
    "plan_gmail_stale_recovery_message_mutations",
)
