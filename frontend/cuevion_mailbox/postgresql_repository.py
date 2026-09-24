"""Inactive Psycopg 3 adapter for durable mailbox synchronization.

The adapter owns fixed SQL and transactional invariants only. It does not read
environment variables, create connections by DSN, call mail providers, activate
routes, or process Priority events.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Protocol, Sequence

import psycopg

from cuevion_mailbox.repository_contract import (
    BackfillState,
    BodyState,
    BootstrapState,
    BoundedMessageProjectionInventory,
    CachedBody,
    CurrentStateInitializationOutcome,
    CurrentStateInitializationResult,
    DeltaCommitOutcome,
    MailboxProvider,
    MailboxReadAuthority,
    MailboxReaderRepository,
    MailboxStateSnapshot,
    MailboxRepository,
    MailboxScope,
    MessageIdentity,
    MessageMutationKind,
    MessageProjection,
    MessageRecord,
    OutboxEvent,
    OutboxEventType,
    OutboxMessageSnapshot,
    OutboxStorageScope,
    ProviderDeltaCommit,
    SyncCursor,
    derive_locator_digest,
)


_SELECT_CURRENT_STATE_SQL = """
SELECT source_generation, bootstrap_state, row_version
FROM cuevion_mailbox.mailbox_sync_state
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND provider = %s
  AND provider_account_identity = %s
  AND is_current = true
""".strip()

_INSERT_INITIAL_STATE_SQL = """
INSERT INTO cuevion_mailbox.mailbox_sync_state (
    schema_version, workspace_id, owner_user_id, mailbox_id, provider,
    provider_account_identity, source_generation, is_current, bootstrap_state,
    backfill_cutoff_at, backfill_oldest_indexed_at, last_successful_sync_at,
    last_error_code, created_at, updated_at, row_version
) VALUES (
    1, %s, %s, %s, %s, %s, 1, true, 'not_started',
    NULL, NULL, NULL, NULL, %s, %s, 1
)
ON CONFLICT DO NOTHING
""".strip()


class PostgreSQLConnectionFactory(Protocol):
    def __call__(self) -> object:
        ...


_SELECT_CURSOR_SQL = """
SELECT
    c.scope_key,
    c.cursor_generation,
    c.provider,
    c.gmail_history_id,
    c.imap_uid_validity,
    c.imap_highest_uid,
    c.imap_uidnext_observed,
    c.backfill_state,
    c.backfill_cursor,
    c.row_version
FROM cuevion_mailbox.mailbox_sync_cursor AS c
JOIN cuevion_mailbox.mailbox_sync_state AS s
  ON s.workspace_id = c.workspace_id
 AND s.owner_user_id = c.owner_user_id
 AND s.mailbox_id = c.mailbox_id
 AND s.source_generation = c.source_generation
 AND s.provider = c.provider
WHERE c.workspace_id = %s
  AND c.owner_user_id = %s
  AND c.mailbox_id = %s
  AND c.source_generation = %s
  AND c.provider = %s
  AND c.scope_key_digest = %s
  AND s.provider_account_identity = %s
  AND s.is_current = true
""".strip()

_LIST_MESSAGES_SQL = """
SELECT
    m.message_id,
    m.provider_message_id,
    m.provider_folder,
    m.imap_uid_validity,
    m.imap_uid,
    m.provider_thread_id,
    m.metadata_hash,
    m.body_state,
    m.unread,
    m.starred,
    m.provider_deleted,
    m.row_version
FROM cuevion_mailbox.mailbox_messages AS m
JOIN cuevion_mailbox.mailbox_sync_state AS s
  ON s.workspace_id = m.workspace_id
 AND s.owner_user_id = m.owner_user_id
 AND s.mailbox_id = m.mailbox_id
 AND s.source_generation = m.source_generation
 AND s.provider = m.provider
WHERE m.workspace_id = %s
  AND m.owner_user_id = %s
  AND m.mailbox_id = %s
  AND m.source_generation = %s
  AND m.provider = %s
  AND s.provider_account_identity = %s
  AND s.is_current = true
  AND m.provider_deleted = false
  AND (%s::timestamptz IS NULL OR m.provider_timestamp < %s::timestamptz)
ORDER BY m.provider_timestamp DESC, m.message_id DESC
LIMIT %s
""".strip()

_SELECT_ACTIVE_MESSAGE_INVENTORY_SQL = """
SELECT
    m.message_id,
    m.provider_message_id,
    m.provider_folder,
    m.imap_uid_validity,
    m.imap_uid,
    m.provider_thread_id,
    m.metadata_hash,
    m.body_state,
    m.unread,
    m.starred,
    m.provider_deleted,
    m.row_version
FROM cuevion_mailbox.mailbox_messages AS m
JOIN cuevion_mailbox.mailbox_sync_state AS s
  ON s.workspace_id = m.workspace_id
 AND s.owner_user_id = m.owner_user_id
 AND s.mailbox_id = m.mailbox_id
 AND s.source_generation = m.source_generation
 AND s.provider = m.provider
WHERE m.workspace_id = %s
  AND m.owner_user_id = %s
  AND m.mailbox_id = %s
  AND m.source_generation = %s
  AND m.provider = %s
  AND s.provider_account_identity = %s
  AND s.is_current = true
  AND m.provider_deleted = false
ORDER BY m.message_id
LIMIT %s
""".strip()

_SELECT_MESSAGES_BY_PROVIDER_IDS_SQL = """
SELECT
    m.message_id,
    m.provider_message_id,
    m.provider_folder,
    m.imap_uid_validity,
    m.imap_uid,
    m.provider_thread_id,
    m.metadata_hash,
    m.body_state,
    m.unread,
    m.starred,
    m.provider_deleted,
    m.row_version
FROM cuevion_mailbox.mailbox_messages AS m
JOIN cuevion_mailbox.mailbox_sync_state AS s
  ON s.workspace_id = m.workspace_id
 AND s.owner_user_id = m.owner_user_id
 AND s.mailbox_id = m.mailbox_id
 AND s.source_generation = m.source_generation
 AND s.provider = m.provider
WHERE m.workspace_id = %s
  AND m.owner_user_id = %s
  AND m.mailbox_id = %s
  AND m.source_generation = %s
  AND m.provider = %s
  AND s.provider_account_identity = %s
  AND s.is_current = true
  AND m.provider_message_id = ANY(%s::text[])
ORDER BY m.provider_message_id
""".strip()

_SELECT_OUTBOX_SCOPE_CURRENT_SQL = """
SELECT provider, provider_account_identity
FROM cuevion_mailbox.mailbox_sync_state
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND is_current = true
""".strip()

_SELECT_OUTBOX_MESSAGE_SQL = """
SELECT
    s.provider,
    s.provider_account_identity,
    m.message_id,
    m.provider_message_id,
    m.provider_folder,
    m.imap_uid_validity,
    m.imap_uid,
    m.provider_thread_id,
    m.provider_labels,
    m.rfc_message_id,
    m.in_reply_to,
    m.references_json,
    m.sender_address,
    m.sender_display,
    m.to_json,
    m.cc_json,
    m.subject,
    m.snippet,
    m.provider_timestamp,
    m.unread,
    m.starred,
    m.body_state,
    m.metadata_hash,
    m.provider_deleted,
    m.row_version
FROM cuevion_mailbox.mailbox_messages AS m
JOIN cuevion_mailbox.mailbox_sync_state AS s
  ON s.workspace_id = m.workspace_id
 AND s.owner_user_id = m.owner_user_id
 AND s.mailbox_id = m.mailbox_id
 AND s.source_generation = m.source_generation
 AND s.provider = m.provider
WHERE m.workspace_id = %s
  AND m.owner_user_id = %s
  AND m.mailbox_id = %s
  AND m.source_generation = %s
  AND m.message_id = %s
  AND s.is_current = true
""".strip()

_SELECT_BODY_SQL = """
SELECT b.message_id, b.body_text, b.body_html, b.content_hash, b.body_version, b.row_version
FROM cuevion_mailbox.mailbox_message_bodies AS b
JOIN cuevion_mailbox.mailbox_messages AS m
  ON m.workspace_id = b.workspace_id
 AND m.owner_user_id = b.owner_user_id
 AND m.mailbox_id = b.mailbox_id
 AND m.message_id = b.message_id
JOIN cuevion_mailbox.mailbox_sync_state AS s
  ON s.workspace_id = m.workspace_id
 AND s.owner_user_id = m.owner_user_id
 AND s.mailbox_id = m.mailbox_id
 AND s.source_generation = m.source_generation
 AND s.provider = m.provider
WHERE b.workspace_id = %s
  AND b.owner_user_id = %s
  AND b.mailbox_id = %s
  AND b.message_id = %s
  AND m.source_generation = %s
  AND m.provider = %s
  AND m.provider_deleted = false
  AND s.provider_account_identity = %s
  AND s.is_current = true
""".strip()

_LOCK_STATE_SQL = """
SELECT provider, provider_account_identity, bootstrap_state, row_version, is_current
FROM cuevion_mailbox.mailbox_sync_state
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
FOR UPDATE
""".strip()

_LOCK_CURSOR_SQL = """
SELECT cursor_generation, row_version
FROM cuevion_mailbox.mailbox_sync_cursor
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND provider = %s
  AND scope_key_digest = %s
FOR UPDATE
""".strip()

_INSERT_CURSOR_SQL = """
INSERT INTO cuevion_mailbox.mailbox_sync_cursor (
    schema_version, workspace_id, owner_user_id, mailbox_id, source_generation,
    provider, scope_key, scope_key_digest, cursor_generation, gmail_history_id,
    imap_uid_validity, imap_highest_uid, imap_uidnext_observed, backfill_state,
    backfill_cursor, last_successful_sync_at, created_at, updated_at, row_version
) VALUES (
    1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1
)
""".strip()

_UPDATE_CURSOR_SQL = """
UPDATE cuevion_mailbox.mailbox_sync_cursor
SET gmail_history_id = %s,
    imap_uid_validity = %s,
    imap_highest_uid = %s,
    imap_uidnext_observed = %s,
    backfill_state = %s,
    backfill_cursor = %s,
    last_successful_sync_at = %s,
    updated_at = %s,
    row_version = %s
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND provider = %s
  AND scope_key_digest = %s
  AND cursor_generation = %s
  AND row_version = %s
""".strip()

_UPDATE_STATE_SQL = """
UPDATE cuevion_mailbox.mailbox_sync_state
SET bootstrap_state = %s,
    last_successful_sync_at = %s,
    last_error_code = NULL,
    updated_at = %s,
    row_version = %s
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND provider = %s
  AND row_version = %s
  AND is_current = true
""".strip()

_INSERT_MESSAGE_SQL = """
INSERT INTO cuevion_mailbox.mailbox_messages (
    schema_version, message_id, workspace_id, owner_user_id, mailbox_id,
    source_generation, provider, provider_message_id, provider_thread_id,
    provider_folder, provider_folder_digest, provider_labels, imap_uid_validity,
    imap_uid, rfc_message_id, in_reply_to, references_json, sender_address,
    sender_display, to_json, cc_json, subject, snippet, provider_timestamp,
    unread, starred, provider_deleted, body_state, metadata_hash, created_at,
    updated_at, row_version
) VALUES (
    1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s,
    %s::jsonb, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, false, %s,
    %s, %s, %s, 1
)
""".strip()

_UPDATE_MESSAGE_SQL = """
UPDATE cuevion_mailbox.mailbox_messages
SET provider_thread_id = %s,
    provider_folder = %s,
    provider_folder_digest = %s,
    provider_labels = %s::jsonb,
    rfc_message_id = %s,
    in_reply_to = %s,
    references_json = %s::jsonb,
    sender_address = %s,
    sender_display = %s,
    to_json = %s::jsonb,
    cc_json = %s::jsonb,
    subject = %s,
    snippet = %s,
    provider_timestamp = %s,
    unread = %s,
    starred = %s,
    provider_deleted = false,
    body_state = %s,
    metadata_hash = %s,
    updated_at = %s,
    row_version = %s
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND provider = %s
  AND message_id = %s
  AND row_version = %s
  AND (
        (provider = 'google' AND provider_message_id = %s)
        OR
        (provider = 'custom_imap'
         AND provider_folder_digest = %s
         AND imap_uid_validity = %s
         AND imap_uid = %s)
      )
""".strip()

_TOMBSTONE_MESSAGE_SQL = """
UPDATE cuevion_mailbox.mailbox_messages
SET provider_deleted = true,
    body_state = 'stale',
    updated_at = %s,
    row_version = %s
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND provider = %s
  AND message_id = %s
  AND row_version = %s
  AND provider_deleted = false
  AND (
        (provider = 'google' AND provider_message_id = %s)
        OR
        (provider = 'custom_imap'
         AND provider_folder_digest = %s
         AND imap_uid_validity = %s
         AND imap_uid = %s)
      )
""".strip()

_INSERT_OUTBOX_SQL = """
INSERT INTO cuevion_mailbox.mailbox_change_outbox (
    schema_version, event_id, workspace_id, owner_user_id, mailbox_id,
    source_generation, message_id, message_row_version, event_type, created_at,
    attempt_count
) VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
ON CONFLICT (
    workspace_id, owner_user_id, mailbox_id, source_generation, message_id,
    message_row_version, event_type
) DO NOTHING
""".strip()

_CLAIM_OUTBOX_SQL = """
WITH due AS (
    SELECT event_id
    FROM cuevion_mailbox.mailbox_change_outbox
    WHERE processed_at IS NULL
      AND (next_attempt_at IS NULL OR next_attempt_at <= %s)
      AND (claim_expires_at IS NULL OR claim_expires_at <= %s)
    ORDER BY created_at, event_id
    FOR UPDATE SKIP LOCKED
    LIMIT %s
)
UPDATE cuevion_mailbox.mailbox_change_outbox AS o
SET claim_token = replace(gen_random_uuid()::text, '-', ''),
    claim_expires_at = %s,
    attempt_count = o.attempt_count + 1
FROM due
WHERE o.event_id = due.event_id
RETURNING
    o.event_id, o.workspace_id, o.owner_user_id, o.mailbox_id,
    o.source_generation, o.message_id, o.message_row_version, o.event_type,
    o.attempt_count, o.claim_token
""".strip()

_MARK_OUTBOX_PROCESSED_SQL = """
UPDATE cuevion_mailbox.mailbox_change_outbox
SET processed_at = %s,
    claim_token = NULL,
    claim_expires_at = NULL,
    last_error_code = NULL
WHERE event_id = %s
  AND claim_token = %s
  AND processed_at IS NULL
  AND claim_expires_at > %s
""".strip()

_MARK_OUTBOX_RETRY_SQL = """
UPDATE cuevion_mailbox.mailbox_change_outbox
SET next_attempt_at = %s,
    claim_token = NULL,
    claim_expires_at = NULL,
    last_error_code = %s
WHERE event_id = %s
  AND claim_token = %s
  AND processed_at IS NULL
  AND claim_expires_at > %s
""".strip()


def _dt(millis: int) -> datetime:
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def _json(values: tuple[str, ...]) -> str:
    return json.dumps(values, separators=(",", ":"), ensure_ascii=False)


def _scope_params(scope: MailboxScope) -> tuple[object, ...]:
    return (
        scope.workspace_id,
        scope.owner_user_id,
        scope.mailbox_id,
        scope.source_generation,
        scope.provider.value,
    )


def _validate_provider_message_ids(
    scope: MailboxScope,
    provider_message_ids: Sequence[str],
) -> tuple[str, ...]:
    if (
        type(scope) is not MailboxScope
        or scope.provider is not MailboxProvider.GOOGLE
        or type(provider_message_ids) not in (list, tuple)
        or len(provider_message_ids) > 100
    ):
        raise ValueError("invalid Gmail provider message ids")

    result: list[str] = []
    seen: set[str] = set()
    for value in provider_message_ids:
        if type(value) is not str or not value:
            raise ValueError("invalid Gmail provider message ids")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeError:
            raise ValueError("invalid Gmail provider message ids") from None
        if not 1 <= len(encoded) <= 1_024 or value in seen:
            raise ValueError("invalid Gmail provider message ids")
        seen.add(value)
        result.append(value)
    return tuple(result)


def _fetchall(cursor: object) -> list[tuple[object, ...]]:
    rows = getattr(cursor, "fetchall")()
    if type(rows) is not list or any(type(row) is not tuple for row in rows):
        raise RuntimeError("mailbox repository storage corruption")
    return rows


def _rowcount(cursor: object) -> int:
    value = getattr(cursor, "rowcount")
    if type(value) is not int:
        raise RuntimeError("mailbox repository storage corruption")
    return value


class PostgreSQLMailboxReaderRepository(MailboxReaderRepository):
    """Read-only public surface for the least-privilege mailbox reader role."""

    __slots__ = ("_delegate",)

    def __init__(self, connection_factory: PostgreSQLConnectionFactory) -> None:
        object.__setattr__(
            self,
            "_delegate",
            PostgreSQLMailboxRepository(connection_factory),
        )

    def resolve_current_state(
        self,
        authority: MailboxReadAuthority,
    ) -> MailboxStateSnapshot | None:
        return self._delegate.resolve_current_state(authority)

    def resolve_current_scope(
        self,
        authority: MailboxReadAuthority,
    ) -> MailboxScope | None:
        state = self.resolve_current_state(authority)
        return None if state is None else state.scope

    def read_cursor(self, scope: MailboxScope, scope_key: str) -> SyncCursor | None:
        return self._delegate.read_cursor(scope, scope_key)

    def list_messages(
        self,
        scope: MailboxScope,
        *,
        limit: int,
        before_timestamp_millis: int | None = None,
    ) -> Sequence[MessageProjection]:
        return self._delegate.list_messages(
            scope,
            limit=limit,
            before_timestamp_millis=before_timestamp_millis,
        )

    def read_active_message_inventory(
        self,
        scope: MailboxScope,
        *,
        limit: int,
    ) -> BoundedMessageProjectionInventory:
        return self._delegate.read_active_message_inventory(
            scope,
            limit=limit,
        )

    def read_messages_by_provider_message_ids(
        self,
        scope: MailboxScope,
        provider_message_ids: Sequence[str],
    ) -> Sequence[MessageProjection]:
        return self._delegate.read_messages_by_provider_message_ids(
            scope,
            provider_message_ids,
        )

    def resolve_outbox_message(
        self,
        event: OutboxEvent,
    ) -> OutboxMessageSnapshot | None:
        return self._delegate.resolve_outbox_message(event)

    def read_cached_body(
        self,
        scope: MailboxScope,
        message_id: str,
    ) -> CachedBody | None:
        return self._delegate.read_cached_body(scope, message_id)


class PostgreSQLMailboxRepository(MailboxRepository):
    __slots__ = ("_connection_factory",)

    def __init__(self, connection_factory: PostgreSQLConnectionFactory) -> None:
        object.__setattr__(self, "_connection_factory", connection_factory)

    def _connection(self) -> object:
        connection = self._connection_factory()
        if getattr(connection, "autocommit", None) is not False:
            raise RuntimeError("mailbox repository requires transactional connection")
        return connection

    def resolve_current_state(
        self,
        authority: MailboxReadAuthority,
    ) -> MailboxStateSnapshot | None:
        if type(authority) is not MailboxReadAuthority:
            raise ValueError("invalid mailbox read authority")
        connection = self._connection()
        cursor = None
        try:
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(
                _SELECT_CURRENT_STATE_SQL,
                (
                    authority.workspace_id,
                    authority.owner_user_id,
                    authority.mailbox_id,
                    authority.provider.value,
                    authority.provider_account_identity,
                ),
            )
            rows = _fetchall(cursor)
            if len(rows) > 1:
                raise RuntimeError("mailbox repository storage corruption")
            if not rows:
                getattr(cursor, "execute")(
                    _SELECT_OUTBOX_SCOPE_CURRENT_SQL,
                    (
                        storage_scope.workspace_id,
                        storage_scope.owner_user_id,
                        storage_scope.mailbox_id,
                        storage_scope.source_generation,
                    ),
                )
                state_rows = _fetchall(cursor)
                if len(state_rows) > 1:
                    raise RuntimeError("mailbox repository storage corruption")
                if not state_rows:
                    return None
                raise RuntimeError("mailbox repository storage corruption")
            row = rows[0]
            if (
                len(row) != 3
                or type(row[0]) is not int
                or row[0] < 1
                or type(row[2]) is not int
                or row[2] < 1
            ):
                raise RuntimeError("mailbox repository storage corruption")
            scope = MailboxScope(
                workspace_id=authority.workspace_id,
                owner_user_id=authority.owner_user_id,
                mailbox_id=authority.mailbox_id,
                source_generation=row[0],
                provider=authority.provider,
                provider_account_identity=authority.provider_account_identity,
            )
            return MailboxStateSnapshot(
                scope=scope,
                bootstrap_state=BootstrapState(row[1]),
                row_version=row[2],
            )
        finally:
            if cursor is not None:
                getattr(cursor, "close")()
            getattr(connection, "rollback")()
            getattr(connection, "close")()

    def resolve_current_scope(
        self,
        authority: MailboxReadAuthority,
    ) -> MailboxScope | None:
        state = self.resolve_current_state(authority)
        return None if state is None else state.scope

    def initialize_current_state(
        self,
        authority: MailboxReadAuthority,
        *,
        initialized_at_millis: int,
    ) -> CurrentStateInitializationResult:
        if (
            type(authority) is not MailboxReadAuthority
            or type(initialized_at_millis) is not int
            or initialized_at_millis < 0
        ):
            raise ValueError("invalid mailbox state initialization")
        connection = self._connection()
        cursor = None
        try:
            now = _dt(initialized_at_millis)
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(
                _INSERT_INITIAL_STATE_SQL,
                (
                    authority.workspace_id,
                    authority.owner_user_id,
                    authority.mailbox_id,
                    authority.provider.value,
                    authority.provider_account_identity,
                    now,
                    now,
                ),
            )
            inserted = _rowcount(cursor)
            if inserted not in (0, 1):
                raise RuntimeError("mailbox repository storage corruption")

            getattr(cursor, "execute")(
                _SELECT_CURRENT_STATE_SQL,
                (
                    authority.workspace_id,
                    authority.owner_user_id,
                    authority.mailbox_id,
                    authority.provider.value,
                    authority.provider_account_identity,
                ),
            )
            rows = _fetchall(cursor)
            if len(rows) > 1:
                raise RuntimeError("mailbox repository storage corruption")
            if not rows:
                getattr(connection, "rollback")()
                return CurrentStateInitializationResult(
                    CurrentStateInitializationOutcome.CONFLICT,
                    None,
                )
            row = rows[0]
            if (
                len(row) != 3
                or type(row[0]) is not int
                or row[0] < 1
                or type(row[2]) is not int
                or row[2] < 1
            ):
                raise RuntimeError("mailbox repository storage corruption")
            state = MailboxStateSnapshot(
                scope=MailboxScope(
                    workspace_id=authority.workspace_id,
                    owner_user_id=authority.owner_user_id,
                    mailbox_id=authority.mailbox_id,
                    source_generation=row[0],
                    provider=authority.provider,
                    provider_account_identity=authority.provider_account_identity,
                ),
                bootstrap_state=BootstrapState(row[1]),
                row_version=row[2],
            )
            if inserted == 1:
                getattr(connection, "commit")()
                outcome = CurrentStateInitializationOutcome.CREATED
            else:
                getattr(connection, "rollback")()
                outcome = CurrentStateInitializationOutcome.EXISTING
            return CurrentStateInitializationResult(outcome, state)
        except Exception:
            getattr(connection, "rollback")()
            raise
        finally:
            if cursor is not None:
                getattr(cursor, "close")()
            getattr(connection, "close")()

    def read_cursor(self, scope: MailboxScope, scope_key: str) -> SyncCursor | None:
        digest = derive_locator_digest(scope_key)
        connection = self._connection()
        cursor = None
        try:
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(
                _SELECT_CURSOR_SQL,
                _scope_params(scope) + (digest, scope.provider_account_identity),
            )
            rows = _fetchall(cursor)
            if len(rows) > 1:
                raise RuntimeError("mailbox repository storage corruption")
            if not rows:
                return None
            row = rows[0]
            if len(row) != 10:
                raise RuntimeError("mailbox repository storage corruption")
            return SyncCursor(
                scope_key=row[0],
                cursor_generation=row[1],
                provider=MailboxProvider(row[2]),
                gmail_history_id=row[3],
                imap_uid_validity=row[4],
                imap_highest_uid=row[5],
                imap_uidnext_observed=row[6],
                backfill_state=BackfillState(row[7]),
                backfill_cursor=row[8],
                row_version=row[9],
            )
        finally:
            if cursor is not None:
                getattr(cursor, "close")()
            getattr(connection, "rollback")()
            getattr(connection, "close")()

    def list_messages(
        self,
        scope: MailboxScope,
        *,
        limit: int,
        before_timestamp_millis: int | None = None,
    ) -> Sequence[MessageProjection]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid mailbox list limit")
        before = None if before_timestamp_millis is None else _dt(before_timestamp_millis)
        connection = self._connection()
        cursor = None
        try:
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(
                _LIST_MESSAGES_SQL,
                _scope_params(scope)
                + (scope.provider_account_identity, before, before, limit),
            )
            rows = _fetchall(cursor)
            result = []
            for row in rows:
                if len(row) != 12:
                    raise RuntimeError("mailbox repository storage corruption")
                identity = MessageIdentity(
                    message_id=row[0],
                    provider_message_id=row[1],
                    provider_folder=row[2],
                    imap_uid_validity=row[3],
                    imap_uid=row[4],
                )
                projection = MessageProjection(
                    identity=identity,
                    provider_thread_id=row[5],
                    metadata_hash=row[6],
                    body_state=BodyState(row[7]),
                    unread=row[8],
                    starred=row[9],
                    provider_deleted=row[10],
                    row_version=row[11],
                )
                projection.validate_for(scope.provider)
                result.append(projection)
            return tuple(result)
        finally:
            if cursor is not None:
                getattr(cursor, "close")()
            getattr(connection, "rollback")()
            getattr(connection, "close")()

    def read_active_message_inventory(
        self,
        scope: MailboxScope,
        *,
        limit: int,
    ) -> BoundedMessageProjectionInventory:
        if (
            type(scope) is not MailboxScope
            or type(limit) is not int
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
        ):
            raise ValueError("invalid active message inventory request")

        connection = self._connection()
        cursor = None
        try:
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(
                _SELECT_ACTIVE_MESSAGE_INVENTORY_SQL,
                _scope_params(scope)
                + (scope.provider_account_identity, limit + 1),
            )
            rows = _fetchall(cursor)
            if len(rows) > limit:
                return BoundedMessageProjectionInventory((), True)

            projections: list[MessageProjection] = []
            for row in rows:
                if len(row) != 12 or row[10] is not False:
                    raise RuntimeError("mailbox repository storage corruption")
                identity = MessageIdentity(
                    message_id=row[0],
                    provider_message_id=row[1],
                    provider_folder=row[2],
                    imap_uid_validity=row[3],
                    imap_uid=row[4],
                )
                projection = MessageProjection(
                    identity=identity,
                    provider_thread_id=row[5],
                    metadata_hash=row[6],
                    body_state=BodyState(row[7]),
                    unread=row[8],
                    starred=row[9],
                    provider_deleted=row[10],
                    row_version=row[11],
                )
                projection.validate_for(scope.provider)
                projections.append(projection)
            return BoundedMessageProjectionInventory(
                tuple(projections),
                False,
            )
        finally:
            if cursor is not None:
                getattr(cursor, "close")()
            getattr(connection, "rollback")()
            getattr(connection, "close")()

    def read_messages_by_provider_message_ids(
        self,
        scope: MailboxScope,
        provider_message_ids: Sequence[str],
    ) -> Sequence[MessageProjection]:
        requested = _validate_provider_message_ids(scope, provider_message_ids)
        if not requested:
            return ()

        connection = self._connection()
        cursor = None
        try:
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(
                _SELECT_MESSAGES_BY_PROVIDER_IDS_SQL,
                _scope_params(scope)
                + (scope.provider_account_identity, list(requested)),
            )
            rows = _fetchall(cursor)
            if len(rows) > len(requested):
                raise RuntimeError("mailbox repository storage corruption")

            requested_set = set(requested)
            seen: set[str] = set()
            result: list[MessageProjection] = []
            for row in rows:
                if (
                    len(row) != 12
                    or type(row[1]) is not str
                    or row[1] not in requested_set
                    or row[1] in seen
                ):
                    raise RuntimeError("mailbox repository storage corruption")
                seen.add(row[1])
                identity = MessageIdentity(
                    message_id=row[0],
                    provider_message_id=row[1],
                    provider_folder=row[2],
                    imap_uid_validity=row[3],
                    imap_uid=row[4],
                )
                projection = MessageProjection(
                    identity=identity,
                    provider_thread_id=row[5],
                    metadata_hash=row[6],
                    body_state=BodyState(row[7]),
                    unread=row[8],
                    starred=row[9],
                    provider_deleted=row[10],
                    row_version=row[11],
                )
                projection.validate_for(scope.provider)
                result.append(projection)
            return tuple(result)
        finally:
            if cursor is not None:
                getattr(cursor, "close")()
            getattr(connection, "rollback")()
            getattr(connection, "close")()

    def resolve_outbox_message(
        self,
        event: OutboxEvent,
    ) -> OutboxMessageSnapshot | None:
        if (
            type(event) is not OutboxEvent
            or type(event.scope) is not OutboxStorageScope
            or type(event.event_id) is not str
            or not event.event_id
            or type(event.message_id) is not str
            or not event.message_id
            or type(event.message_row_version) is not int
            or event.message_row_version < 1
            or type(event.event_type) is not OutboxEventType
            or type(event.attempt_count) is not int
            or event.attempt_count < 1
            or type(event.claim_token) is not str
            or not event.claim_token
        ):
            raise ValueError("invalid outbox message resolution")

        storage_scope = event.scope
        connection = self._connection()
        cursor = None
        try:
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(
                _SELECT_OUTBOX_MESSAGE_SQL,
                (
                    storage_scope.workspace_id,
                    storage_scope.owner_user_id,
                    storage_scope.mailbox_id,
                    storage_scope.source_generation,
                    event.message_id,
                ),
            )
            rows = _fetchall(cursor)
            if len(rows) > 1:
                raise RuntimeError("mailbox repository storage corruption")
            if not rows:
                return None
            row = rows[0]
            if len(row) != 25:
                raise RuntimeError("mailbox repository storage corruption")

            provider = MailboxProvider(row[0])
            account_identity = row[1]
            provider_timestamp = row[18]
            if (
                type(account_identity) is not str
                or not account_identity
                or not isinstance(provider_timestamp, datetime)
                or provider_timestamp.tzinfo is None
            ):
                raise RuntimeError("mailbox repository storage corruption")

            identity = MessageIdentity(
                message_id=row[2],
                provider_message_id=row[3],
                provider_folder=row[4],
                imap_uid_validity=row[5],
                imap_uid=row[6],
            )
            record = MessageRecord(
                identity=identity,
                provider_thread_id=row[7],
                provider_labels=tuple(row[8]),
                rfc_message_id=row[9],
                in_reply_to=row[10],
                references=tuple(row[11]),
                sender_address=row[12],
                sender_display=row[13],
                to_recipients=tuple(row[14]),
                cc_recipients=tuple(row[15]),
                subject=row[16],
                snippet=row[17],
                provider_timestamp_millis=int(
                    provider_timestamp.timestamp() * 1_000
                ),
                unread=row[19],
                starred=row[20],
                body_state=BodyState(row[21]),
                metadata_hash=row[22],
            )
            scope = MailboxScope(
                workspace_id=storage_scope.workspace_id,
                owner_user_id=storage_scope.owner_user_id,
                mailbox_id=storage_scope.mailbox_id,
                source_generation=storage_scope.source_generation,
                provider=provider,
                provider_account_identity=account_identity,
            )
            snapshot = OutboxMessageSnapshot(
                scope=scope,
                record=record,
                provider_deleted=row[23],
                row_version=row[24],
            )
            if snapshot.record.identity.message_id != event.message_id:
                raise RuntimeError("mailbox repository storage corruption")
            return snapshot
        finally:
            if cursor is not None:
                getattr(cursor, "close")()
            getattr(connection, "rollback")()
            getattr(connection, "close")()

    def read_cached_body(
        self,
        scope: MailboxScope,
        message_id: str,
    ) -> CachedBody | None:
        connection = self._connection()
        cursor = None
        try:
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(
                _SELECT_BODY_SQL,
                (
                    scope.workspace_id,
                    scope.owner_user_id,
                    scope.mailbox_id,
                    message_id,
                    scope.source_generation,
                    scope.provider.value,
                    scope.provider_account_identity,
                ),
            )
            rows = _fetchall(cursor)
            if len(rows) > 1:
                raise RuntimeError("mailbox repository storage corruption")
            if not rows:
                return None
            row = rows[0]
            if len(row) != 6 or row[0] != message_id:
                raise RuntimeError("mailbox repository storage corruption")
            return CachedBody(*row)
        finally:
            if cursor is not None:
                getattr(cursor, "close")()
            getattr(connection, "rollback")()
            getattr(connection, "close")()

    def commit_provider_delta(
        self,
        commit: ProviderDeltaCommit,
    ) -> DeltaCommitOutcome:
        scope = commit.scope
        connection = self._connection()
        cursor = None
        try:
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(
                _LOCK_STATE_SQL,
                (
                    scope.workspace_id,
                    scope.owner_user_id,
                    scope.mailbox_id,
                    scope.source_generation,
                ),
            )
            rows = _fetchall(cursor)
            if not rows:
                getattr(connection, "rollback")()
                return DeltaCommitOutcome.NOT_FOUND
            if len(rows) != 1 or len(rows[0]) != 5:
                raise RuntimeError("mailbox repository storage corruption")
            provider, account_identity, _bootstrap, state_version, is_current = rows[0]
            if (
                provider != scope.provider.value
                or account_identity != scope.provider_account_identity
                or is_current is not True
            ):
                getattr(connection, "rollback")()
                return DeltaCommitOutcome.STALE_GENERATION
            if state_version != commit.expected_state_row_version:
                getattr(connection, "rollback")()
                return DeltaCommitOutcome.CONFLICT

            digest = derive_locator_digest(commit.scope_key)
            getattr(cursor, "execute")(
                _LOCK_CURSOR_SQL,
                _scope_params(scope) + (digest,),
            )
            cursor_rows = _fetchall(cursor)
            if commit.expected_cursor_row_version is None:
                if cursor_rows:
                    getattr(connection, "rollback")()
                    return DeltaCommitOutcome.CONFLICT
            else:
                if (
                    len(cursor_rows) != 1
                    or len(cursor_rows[0]) != 2
                    or cursor_rows[0][0] != commit.expected_cursor_generation
                    or cursor_rows[0][1] != commit.expected_cursor_row_version
                ):
                    getattr(connection, "rollback")()
                    return DeltaCommitOutcome.CONFLICT

            now = _dt(commit.committed_at_millis)
            for mutation in commit.mutations:
                if mutation.kind is MessageMutationKind.UPSERT:
                    record = mutation.record
                    if record is None:
                        raise RuntimeError("invalid validated mailbox mutation")
                    identity = mutation.identity
                    folder_digest = derive_locator_digest(identity.provider_folder)
                    if mutation.expected_row_version is None:
                        getattr(cursor, "execute")(
                            _INSERT_MESSAGE_SQL,
                            (
                                identity.message_id,
                                scope.workspace_id,
                                scope.owner_user_id,
                                scope.mailbox_id,
                                scope.source_generation,
                                scope.provider.value,
                                identity.provider_message_id,
                                record.provider_thread_id,
                                identity.provider_folder,
                                folder_digest,
                                _json(record.provider_labels),
                                identity.imap_uid_validity,
                                identity.imap_uid,
                                record.rfc_message_id,
                                record.in_reply_to,
                                _json(record.references),
                                record.sender_address,
                                record.sender_display,
                                _json(record.to_recipients),
                                _json(record.cc_recipients),
                                record.subject,
                                record.snippet,
                                _dt(record.provider_timestamp_millis),
                                record.unread,
                                record.starred,
                                record.body_state.value,
                                record.metadata_hash,
                                now,
                                now,
                            ),
                        )
                        new_row_version = 1
                    else:
                        new_row_version = mutation.expected_row_version + 1
                        getattr(cursor, "execute")(
                            _UPDATE_MESSAGE_SQL,
                            (
                                record.provider_thread_id,
                                identity.provider_folder,
                                folder_digest,
                                _json(record.provider_labels),
                                record.rfc_message_id,
                                record.in_reply_to,
                                _json(record.references),
                                record.sender_address,
                                record.sender_display,
                                _json(record.to_recipients),
                                _json(record.cc_recipients),
                                record.subject,
                                record.snippet,
                                _dt(record.provider_timestamp_millis),
                                record.unread,
                                record.starred,
                                record.body_state.value,
                                record.metadata_hash,
                                now,
                                new_row_version,
                            )
                            + _scope_params(scope)
                            + (
                                identity.message_id,
                                mutation.expected_row_version,
                                identity.provider_message_id,
                                folder_digest,
                                identity.imap_uid_validity,
                                identity.imap_uid,
                            ),
                        )
                        if _rowcount(cursor) != 1:
                            getattr(connection, "rollback")()
                            return DeltaCommitOutcome.CONFLICT
                else:
                    new_row_version = mutation.expected_row_version + 1  # type: ignore[operator]
                    getattr(cursor, "execute")(
                        _TOMBSTONE_MESSAGE_SQL,
                        (
                            now,
                            new_row_version,
                        )
                        + _scope_params(scope)
                        + (
                            mutation.identity.message_id,
                            mutation.expected_row_version,
                            mutation.identity.provider_message_id,
                            derive_locator_digest(mutation.identity.provider_folder),
                            mutation.identity.imap_uid_validity,
                            mutation.identity.imap_uid,
                        ),
                    )
                    if _rowcount(cursor) != 1:
                        getattr(connection, "rollback")()
                        return DeltaCommitOutcome.CONFLICT

                getattr(cursor, "execute")(
                    _INSERT_OUTBOX_SQL,
                    (
                        mutation.event_id,
                        scope.workspace_id,
                        scope.owner_user_id,
                        scope.mailbox_id,
                        scope.source_generation,
                        mutation.identity.message_id,
                        new_row_version,
                        mutation.outbox_event_type.value,
                        now,
                    ),
                )

            next_cursor = commit.next_cursor
            if commit.expected_cursor_row_version is None:
                getattr(cursor, "execute")(
                    _INSERT_CURSOR_SQL,
                    (
                        scope.workspace_id,
                        scope.owner_user_id,
                        scope.mailbox_id,
                        scope.source_generation,
                        scope.provider.value,
                        next_cursor.scope_key,
                        digest,
                        next_cursor.cursor_generation,
                        next_cursor.gmail_history_id,
                        next_cursor.imap_uid_validity,
                        next_cursor.imap_highest_uid,
                        next_cursor.imap_uidnext_observed,
                        next_cursor.backfill_state.value,
                        next_cursor.backfill_cursor,
                        now,
                        now,
                        now,
                    ),
                )
            else:
                getattr(cursor, "execute")(
                    _UPDATE_CURSOR_SQL,
                    (
                        next_cursor.gmail_history_id,
                        next_cursor.imap_uid_validity,
                        next_cursor.imap_highest_uid,
                        next_cursor.imap_uidnext_observed,
                        next_cursor.backfill_state.value,
                        next_cursor.backfill_cursor,
                        now,
                        now,
                        next_cursor.row_version,
                        scope.workspace_id,
                        scope.owner_user_id,
                        scope.mailbox_id,
                        scope.source_generation,
                        scope.provider.value,
                        digest,
                        commit.expected_cursor_generation,
                        commit.expected_cursor_row_version,
                    ),
                )
                if _rowcount(cursor) != 1:
                    getattr(connection, "rollback")()
                    return DeltaCommitOutcome.CONFLICT

            getattr(cursor, "execute")(
                _UPDATE_STATE_SQL,
                (
                    commit.next_bootstrap_state.value,
                    now,
                    now,
                    commit.expected_state_row_version + 1,
                    scope.workspace_id,
                    scope.owner_user_id,
                    scope.mailbox_id,
                    scope.source_generation,
                    scope.provider.value,
                    commit.expected_state_row_version,
                ),
            )
            if _rowcount(cursor) != 1:
                getattr(connection, "rollback")()
                return DeltaCommitOutcome.CONFLICT

            getattr(connection, "commit")()
            return DeltaCommitOutcome.APPLIED
        except (psycopg.OperationalError, psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected):
            getattr(connection, "rollback")()
            raise
        except Exception:
            getattr(connection, "rollback")()
            raise
        finally:
            if cursor is not None:
                getattr(cursor, "close")()
            getattr(connection, "close")()

    def claim_outbox_batch(
        self,
        *,
        limit: int,
        now_millis: int,
        lease_millis: int,
    ) -> Sequence[OutboxEvent]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(now_millis) is not int
            or now_millis < 0
            or type(lease_millis) is not int
            or not 1_000 <= lease_millis <= 300_000
        ):
            raise ValueError("invalid outbox claim request")
        now = _dt(now_millis)
        expires = _dt(now_millis + lease_millis)
        connection = self._connection()
        cursor = None
        try:
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(
                _CLAIM_OUTBOX_SQL,
                (now, now, limit, expires),
            )
            rows = _fetchall(cursor)
            events = []
            for row in rows:
                if len(row) != 10:
                    raise RuntimeError("mailbox repository storage corruption")
                events.append(
                    OutboxEvent(
                        event_id=row[0],
                        scope=OutboxStorageScope(
                            workspace_id=row[1],
                            owner_user_id=row[2],
                            mailbox_id=row[3],
                            source_generation=row[4],
                        ),
                        message_id=row[5],
                        message_row_version=row[6],
                        event_type=OutboxEventType(row[7]),
                        attempt_count=row[8],
                        claim_token=row[9],
                    )
                )
            getattr(connection, "commit")()
            return tuple(events)
        except Exception:
            getattr(connection, "rollback")()
            raise
        finally:
            if cursor is not None:
                getattr(cursor, "close")()
            getattr(connection, "close")()

    def mark_outbox_processed(
        self,
        event_id: str,
        *,
        claim_token: str,
        processed_at_millis: int,
    ) -> bool:
        processed_at = _dt(processed_at_millis)
        return self._mark_outbox(
            _MARK_OUTBOX_PROCESSED_SQL,
            (processed_at, event_id, claim_token, processed_at),
        )

    def mark_outbox_retry(
        self,
        event_id: str,
        *,
        claim_token: str,
        now_millis: int,
        next_attempt_at_millis: int,
        safe_error_code: str,
    ) -> bool:
        if (
            type(safe_error_code) is not str
            or not 1 <= len(safe_error_code.encode("utf-8")) <= 128
        ):
            raise ValueError("invalid safe error code")
        if type(now_millis) is not int or now_millis < 0:
            raise ValueError("invalid outbox retry time")
        return self._mark_outbox(
            _MARK_OUTBOX_RETRY_SQL,
            (
                _dt(next_attempt_at_millis),
                safe_error_code,
                event_id,
                claim_token,
                _dt(now_millis),
            ),
        )

    def _mark_outbox(self, sql: str, parameters: tuple[object, ...]) -> bool:
        connection = self._connection()
        cursor = None
        try:
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(sql, parameters)
            changed = _rowcount(cursor) == 1
            getattr(connection, "commit")()
            return changed
        except Exception:
            getattr(connection, "rollback")()
            raise
        finally:
            if cursor is not None:
                getattr(cursor, "close")()
            getattr(connection, "close")()


__all__ = (
    "PostgreSQLConnectionFactory",
    "PostgreSQLMailboxReaderRepository",
    "PostgreSQLMailboxRepository",
)
