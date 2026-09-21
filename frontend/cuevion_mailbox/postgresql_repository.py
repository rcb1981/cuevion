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
    CachedBody,
    DeltaCommitOutcome,
    MailboxProvider,
    MailboxRepository,
    MailboxScope,
    MessageIdentity,
    MessageMutationKind,
    MessageProjection,
    OutboxEvent,
    OutboxEventType,
    OutboxStorageScope,
    ProviderDeltaCommit,
    SyncCursor,
    derive_locator_digest,
)


class PostgreSQLConnectionFactory(Protocol):
    def __call__(self) -> object:
        ...


_SELECT_CURSOR_SQL = """
SELECT
    scope_key,
    cursor_generation,
    provider,
    gmail_history_id,
    imap_uid_validity,
    imap_highest_uid,
    imap_uidnext_observed,
    backfill_state,
    backfill_cursor,
    row_version
FROM cuevion_mailbox.mailbox_sync_cursor
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND provider = %s
  AND scope_key_digest = %s
""".strip()

_LIST_MESSAGES_SQL = """
SELECT
    message_id,
    provider_message_id,
    provider_folder,
    imap_uid_validity,
    imap_uid,
    provider_thread_id,
    metadata_hash,
    body_state,
    unread,
    starred,
    provider_deleted,
    row_version
FROM cuevion_mailbox.mailbox_messages
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND provider = %s
  AND provider_deleted = false
  AND (%s IS NULL OR provider_timestamp < %s)
ORDER BY provider_timestamp DESC, message_id DESC
LIMIT %s
""".strip()

_SELECT_BODY_SQL = """
SELECT message_id, body_text, body_html, content_hash, body_version, row_version
FROM cuevion_mailbox.mailbox_message_bodies
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND message_id = %s
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
SET claim_token = %s || o.event_id,
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


class PostgreSQLMailboxRepository(MailboxRepository):
    __slots__ = ("_connection_factory",)

    def __init__(self, connection_factory: PostgreSQLConnectionFactory) -> None:
        object.__setattr__(self, "_connection_factory", connection_factory)

    def _connection(self) -> object:
        connection = self._connection_factory()
        if getattr(connection, "autocommit", None) is not False:
            raise RuntimeError("mailbox repository requires transactional connection")
        return connection

    def read_cursor(self, scope: MailboxScope, scope_key: str) -> SyncCursor | None:
        digest = derive_locator_digest(scope_key)
        connection = self._connection()
        cursor = None
        try:
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(
                _SELECT_CURSOR_SQL,
                _scope_params(scope) + (digest,),
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
                _scope_params(scope) + (before, before, limit),
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
                            + (identity.message_id, mutation.expected_row_version),
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
                        + (mutation.identity.message_id, mutation.expected_row_version),
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
        prefix = f"{now_millis:x}-"
        connection = self._connection()
        cursor = None
        try:
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(
                _CLAIM_OUTBOX_SQL,
                (now, now, limit, prefix, expires),
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
        return self._mark_outbox(
            _MARK_OUTBOX_PROCESSED_SQL,
            (_dt(processed_at_millis), event_id, claim_token),
        )

    def mark_outbox_retry(
        self,
        event_id: str,
        *,
        claim_token: str,
        next_attempt_at_millis: int,
        safe_error_code: str,
    ) -> bool:
        if (
            type(safe_error_code) is not str
            or not 1 <= len(safe_error_code.encode("utf-8")) <= 128
        ):
            raise ValueError("invalid safe error code")
        return self._mark_outbox(
            _MARK_OUTBOX_RETRY_SQL,
            (_dt(next_attempt_at_millis), safe_error_code, event_id, claim_token),
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
        finally:
            if cursor is not None:
                getattr(cursor, "close")()
            getattr(connection, "close")()


__all__ = (
    "PostgreSQLConnectionFactory",
    "PostgreSQLMailboxRepository",
)
