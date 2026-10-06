"""Custom IMAP sync application service using the shared mailbox transaction.

The complete selected-folder UID inventory reconstructs bootstrap recovery even
when a failed transaction retained no evidence. Recent rows can be cached ahead
of progress, but only a handled inventory prefix advances the durable cursor.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json

from cuevion_mailbox.imap_projection import (
    derive_imap_event_id, derive_imap_message_id, project_imap_message,
    validate_imap_locator,
)
from cuevion_mailbox.repository_contract import (
    BackfillState, BootstrapState, MailboxScope, MailboxStateSnapshot,
    MailboxProvider, MailboxReadAuthority, MessageMutation, MessageMutationKind,
    OutboxEventType, ProviderDeltaCommit, SyncCursor,
)


@dataclass(frozen=True, slots=True)
class ImapDurableSyncResult:
    status: str
    mutation_count: int
    source_generation: int | None


def prepare_imap_state(repositories, authority):
    state = repositories.reader.resolve_current_state(authority)
    if state is not None:
        return state, False
    # Initialization joins the message transaction. A failed first attempt
    # leaves no successful-looking state or cursor behind.
    return MailboxStateSnapshot(
        MailboxScope(authority.workspace_id, authority.owner_user_id, authority.mailbox_id,
                     1, authority.provider, authority.provider_account_identity),
        BootstrapState.NOT_STARTED, 1,
    ), True


def sync_imap_snapshot(
    *, repositories, authority: MailboxReadAuthority, folder: str,
    uid_validity: str, uid_set: list[str], uidnext_observed: int | None,
    messages: list, committed_at_millis: int, recover_uid=None,
    prepared_state=None, initialize_state=False, budget=None, recovery_limit=100,
    mutation_limit=200,
) -> ImapDurableSyncResult:
    if type(authority) is not MailboxReadAuthority or authority.provider is not MailboxProvider.CUSTOM_IMAP:
        raise ValueError("invalid IMAP sync authority")
    if type(recovery_limit) is not int or not 1 <= recovery_limit <= 100:
        raise ValueError("invalid IMAP recovery bound")
    if type(mutation_limit) is not int or not recovery_limit <= mutation_limit <= 200:
        raise ValueError("invalid IMAP mutation bound")
    def checkpoint():
        if budget is not None:
            budget.check()
    checkpoint()
    validate_imap_locator(folder, uid_validity, 1)
    if (
        type(uid_set) is not list or len(uid_set) > 100_000
        or type(messages) is not list or len(messages) > 100
        or type(committed_at_millis) is not int or committed_at_millis < 0
    ):
        raise ValueError("invalid IMAP sync snapshot")
    uids = []
    for value in uid_set:
        if (type(value) is not str or not value.isascii() or not value.isdigit()
            or value.startswith("0") or len(value) > 10):
            raise ValueError("invalid IMAP UID inventory")
        uid = int(value)
        validate_imap_locator(folder, uid_validity, uid)
        uids.append(uid)
    if uids != sorted(set(uids)):
        raise ValueError("invalid IMAP UID inventory")
    if uidnext_observed is not None and (
        type(uidnext_observed) is not int
        or not 1 <= uidnext_observed <= 4_294_967_296
        or (uids and uidnext_observed <= uids[-1])
    ):
        raise ValueError("invalid IMAP UIDNEXT")

    if prepared_state is None:
        state, initialize_state = prepare_imap_state(repositories, authority)
    else:
        state = prepared_state
    scope = state.scope
    if (scope.workspace_id, scope.owner_user_id, scope.mailbox_id, scope.provider,
        scope.provider_account_identity) != (
        authority.workspace_id, authority.owner_user_id, authority.mailbox_id,
        authority.provider, authority.provider_account_identity,
    ):
        raise ValueError("invalid prepared IMAP state")
    current_cursor = repositories.reader.read_cursor(scope, folder)
    checkpoint()
    previous_generation = None
    if current_cursor is not None and current_cursor.imap_uid_validity != uid_validity:
        previous_generation = scope.source_generation
        scope = replace(scope, source_generation=scope.source_generation + 1)
        current_cursor = None
    records = {}
    bodies = {}

    def add(item):
        checkpoint()
        record, body = project_imap_message(scope, folder=folder, uid_validity=uid_validity, item=item)
        checkpoint()
        uid = record.identity.imap_uid
        if uid not in uid_inventory:
            raise ValueError("IMAP message outside confirmed inventory")
        if uid in records and (records[uid] != record or bodies[uid] != body):
            raise ValueError("conflicting duplicate IMAP copy")
        records[uid], bodies[uid] = record, body

    uid_inventory = set(uids)
    for item in messages:
        add(item)
    # A bootstrap must cover exactly the existing recent fetch window (duplicate
    # identical input is harmless). Partial/warning snapshots cannot initialize
    # a successful cursor. The provider adapter also checks the requested limit.
    if current_cursor is None and (
        (uids and not records)
        or (records and set(records) != set(uids[-len(records):]))
    ):
        raise ValueError("incomplete IMAP recent snapshot")
    # No durable anchor means no earlier UID can safely be declared handled.
    # Reconstruct from real SEARCH ALL evidence, never numeric UID ranges.
    highest = 0 if current_cursor is None else current_cursor.imap_highest_uid
    pending = [uid for uid in uids if uid > highest]
    for uid in pending[:recovery_limit]:
        checkpoint()
        if uid not in records:
            if recover_uid is None:
                break
            item = recover_uid(str(uid))
            if item is None or item[2] != str(uid):
                return ImapDurableSyncResult("provider_retry", 0, scope.source_generation)
            add(item)
        highest = uid
    backlog = any(uid > highest for uid in pending)
    current = () if previous_generation is not None else repositories.reader.read_imap_projections(
        scope, folder=folder, uid_validity=uid_validity, uids=list(records),
    )
    absent = () if previous_generation is not None else repositories.reader.read_imap_projections(
        scope, folder=folder, uid_validity=uid_validity, uids=uids,
        absent_from_uid_set=True, limit=min(100, mutation_limit),
    )
    checkpoint()
    current_by_uid = {row.identity.imap_uid: row for row in current}
    for uid, row in current_by_uid.items():
        if uid not in records or row.identity != records[uid].identity:
            raise ValueError("invalid IMAP durable projection")
    mutations = []
    cached_bodies = []
    deferred = False
    # Prioritize every UID covered by this planned cursor advance. Additional
    # recent metadata can wait, but a required provider copy can never wait
    # behind a committed high-water mark.
    handled = {uid for uid in pending if uid <= highest}
    for uid in sorted(records, key=lambda uid: (uid not in handled, uid if uid in handled else -uid)):
        record = records[uid]
        projection = current_by_uid.get(uid)
        if projection is not None and (
            projection.identity == record.identity and projection.metadata_hash == record.metadata_hash
            and projection.body_state == record.body_state
            and projection.unread == record.unread and projection.starred == record.starred
            and not projection.provider_deleted
        ):
            continue
        if len(mutations) >= mutation_limit:
            deferred = True
            continue
        version = None if projection is None else projection.row_version
        event_type = OutboxEventType.MESSAGE_ADDED if version is None else OutboxEventType.MESSAGE_CHANGED
        mutations.append(MessageMutation(
            MessageMutationKind.UPSERT, record.identity, record, version,
            derive_imap_event_id(scope, record.identity.message_id, (version or 0) + 1, event_type),
            event_type,
        ))
        cached_bodies.append(bodies[uid])
    for projection in absent:
        if len(mutations) >= mutation_limit:
            deferred = True
            break
        identity = projection.identity
        if (
            identity.provider_folder != folder or identity.imap_uid_validity != uid_validity
            or identity.imap_uid in uid_inventory or projection.provider_deleted
            or identity.message_id != derive_imap_message_id(scope, folder, uid_validity, identity.imap_uid)
        ):
            raise ValueError("invalid IMAP reconciliation projection")
        mutations.append(MessageMutation(
            MessageMutationKind.TOMBSTONE, identity, None, projection.row_version,
            derive_imap_event_id(scope, identity.message_id, projection.row_version + 1,
                                 OutboxEventType.MESSAGE_DELETED),
            OutboxEventType.MESSAGE_DELETED,
        ))
    next_cursor = SyncCursor(
        scope_key=folder, cursor_generation=1 if current_cursor is None else current_cursor.cursor_generation,
        provider=MailboxProvider.CUSTOM_IMAP, gmail_history_id=None,
        imap_uid_validity=uid_validity, imap_highest_uid=highest,
        imap_uidnext_observed=uidnext_observed if uidnext_observed is not None else (
            None if current_cursor is None else current_cursor.imap_uidnext_observed
        ),
        backfill_state=BackfillState.RECOVERING if backlog else BackfillState.COMPLETE,
        backfill_cursor=json.dumps({"imap_after_uid": highest}, separators=(",", ":")) if backlog else None,
        row_version=1 if current_cursor is None else current_cursor.row_version + 1,
    )
    commit = ProviderDeltaCommit(
        scope=scope, scope_key=folder, expected_state_row_version=state.row_version,
        expected_cursor_row_version=None if current_cursor is None else current_cursor.row_version,
        expected_cursor_generation=next_cursor.cursor_generation,
        committed_at_millis=committed_at_millis, mutations=tuple(mutations),
        next_cursor=next_cursor,
        next_bootstrap_state=BootstrapState.RECOVERING if (
            backlog or deferred or len(absent) == min(100, mutation_limit)
        ) else BootstrapState.RECENT_READY,
        previous_source_generation=previous_generation, cached_bodies=tuple(cached_bodies),
        initialize_imap_state=initialize_state,
    )
    checkpoint()
    outcome = repositories.writer.commit_provider_delta(commit)
    status = "state_conflict" if initialize_state and outcome.value == "stale_generation" else outcome.value
    return ImapDurableSyncResult(status, len(mutations), scope.source_generation)
