"""Psycopg 3 durable mailbox repository.

This adapter is inert until called. It owns no DSN, environment lookup, pool,
route, provider I/O, logging, retry loop, or application activation. Reader and
writer connections are injected separately so database privileges can remain
least-privilege.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Callable, Protocol, Sequence

import psycopg
from psycopg.types.json import Jsonb

from cuevion_mailbox import repository_contract as contract


class MailboxRepositoryUnavailableError(RuntimeError):
    __slots__ = ()

    def __init__(self) -> None:
        RuntimeError.__init__(self)

    def __str__(self) -> str:
        return "mailbox repository unavailable"

    def __repr__(self) -> str:
        return "MailboxRepositoryUnavailableError()"


class MailboxRepositoryIntegrityError(RuntimeError):
    __slots__ = ()

    def __init__(self) -> None:
        RuntimeError.__init__(self)

    def __str__(self) -> str:
        return "mailbox repository integrity failure"

    def __repr__(self) -> str:
        return "MailboxRepositoryIntegrityError()"


class PostgreSQLConnectionFactory(Protocol):
    def __call__(self) -> object:
        ...


_SET_READER_TRANSACTION = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
)
_SET_WRITER_TRANSACTION = "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"

_SELECT_CURRENT_GENERATION = """
SELECT provider, provider_account_identity, source_generation, row_version
FROM cuevion_mailbox.mailbox_sync_state
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND is_current IS TRUE
FOR UPDATE
""".strip()

_SELECT_MAX_GENERATION = """
SELECT COALESCE(MAX(source_generation), 0)
FROM cuevion_mailbox.mailbox_sync_state
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
""".strip()

_DEACTIVATE_GENERATION = """
UPDATE cuevion_mailbox.mailbox_sync_state
SET is_current = FALSE,
    updated_at = %s,
    row_version = row_version + 1
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND is_current IS TRUE
  AND row_version = %s
""".strip()

_INSERT_GENERATION = """
INSERT INTO cuevion_mailbox.mailbox_sync_state (
    schema_version,
    workspace_id,
    owner_user_id,
    mailbox_id,
    provider,
    provider_account_identity,
    source_generation,
    is_current,
    bootstrap_state,
    backfill_cutoff_at,
    backfill_oldest_indexed_at,
    last_successful_sync_at,
    last_error_code,
    created_at,
    updated_at,
    row_version
)
VALUES (
    1, %s, %s, %s, %s, %s, %s, TRUE, %s,
    %s, NULL, NULL, NULL, %s, %s, 1
)
""".strip()

_SELECT_SCOPE_STATE_FOR_UPDATE = """
SELECT provider, provider_account_identity, is_current, row_version
FROM cuevion_mailbox.mailbox_sync_state
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
FOR UPDATE
""".strip()

_SELECT_CURSOR = """
SELECT
    cursor.scope_key,
    cursor.cursor_generation,
    cursor.provider,
    cursor.gmail_history_id,
    cursor.imap_uid_validity,
    cursor.imap_highest_uid,
    cursor.imap_uidnext_observed,
    cursor.backfill_state,
    cursor.backfill_cursor,
    cursor.row_version
FROM cuevion_mailbox.mailbox_sync_cursor AS cursor
JOIN cuevion_mailbox.mailbox_sync_state AS state
  ON state.workspace_id = cursor.workspace_id
 AND state.owner_user_id = cursor.owner_user_id
 AND state.mailbox_id = cursor.mailbox_id
 AND state.source_generation = cursor.source_generation
 AND state.provider = cursor.provider
WHERE cursor.workspace_id = %s
  AND cursor.owner_user_id = %s
  AND cursor.mailbox_id = %s
  AND cursor.source_generation = %s
  AND cursor.scope_key_digest = %s
  AND state.is_current IS TRUE
  AND state.provider = %s
  AND state.provider_account_identity = %s
""".strip()

_SELECT_CURSOR_FOR_UPDATE = """
SELECT
    scope_key,
    cursor_generation,
    provider,
    row_version
FROM cuevion_mailbox.mailbox_sync_cursor
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND scope_key_digest = %s
FOR UPDATE
""".strip()

_INSERT_CURSOR = """
INSERT INTO cuevion_mailbox.mailbox_sync_cursor (
    schema_version,
    workspace_id,
    owner_user_id,
    mailbox_id,
    source_generation,
    provider,
    scope_key,
    scope_key_digest,
    cursor_generation,
    gmail_history_id,
    imap_uid_validity,
    imap_highest_uid,
    imap_uidnext_observed,
    backfill_state,
    backfill_cursor,
    last_successful_sync_at,
    created_at,
    updated_at,
    row_version
)
VALUES (
    1, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, 1
)
""".strip()

_UPDATE_CURSOR = """
UPDATE cuevion_mailbox.mailbox_sync_cursor
SET scope_key = %s,
    cursor_generation = %s,
    gmail_history_id = %s,
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
  AND scope_key_digest = %s
  AND provider = %s
  AND row_version = %s
""".strip()

_UPDATE_SYNC_STATE_AFTER_DELTA = """
UPDATE cuevion_mailbox.mailbox_sync_state
SET bootstrap_state = %s,
    last_successful_sync_at = %s,
    last_error_code = NULL,
    updated_at = %s,
    row_version = row_version + 1
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND provider = %s
  AND provider_account_identity = %s
  AND is_current IS TRUE
  AND row_version = %s
""".strip()

_LIST_MESSAGES_BASE = """
SELECT
    message.message_id,
    message.provider_message_id,
    message.provider_folder,
    message.imap_uid_validity,
    message.imap_uid,
    message.provider_thread_id,
    message.metadata_hash,
    message.body_state,
    message.unread,
    message.starred,
    message.provider_deleted,
    message.row_version
FROM cuevion_mailbox.mailbox_messages AS message
JOIN cuevion_mailbox.mailbox_sync_state AS state
  ON state.workspace_id = message.workspace_id
 AND state.owner_user_id = message.owner_user_id
 AND state.mailbox_id = message.mailbox_id
 AND state.source_generation = message.source_generation
 AND state.provider = message.provider
WHERE message.workspace_id = %s
  AND message.owner_user_id = %s
  AND message.mailbox_id = %s
  AND message.source_generation = %s
  AND message.provider = %s
  AND message.provider_deleted IS FALSE
  AND state.is_current IS TRUE
  AND state.provider_account_identity = %s
""".strip()

_SELECT_BODY = """
SELECT
    body.message_id,
    body.body_text,
    body.body_html,
    body.content_hash,
    body.body_version,
    body.row_version
FROM cuevion_mailbox.mailbox_message_bodies AS body
JOIN cuevion_mailbox.mailbox_messages AS message
  ON message.workspace_id = body.workspace_id
 AND message.owner_user_id = body.owner_user_id
 AND message.mailbox_id = body.mailbox_id
 AND message.message_id = body.message_id
JOIN cuevion_mailbox.mailbox_sync_state AS state
  ON state.workspace_id = message.workspace_id
 AND state.owner_user_id = message.owner_user_id
 AND state.mailbox_id = message.mailbox_id
 AND state.source_generation = message.source_generation
 AND state.provider = message.provider
WHERE message.workspace_id = %s
  AND message.owner_user_id = %s
  AND message.mailbox_id = %s
  AND message.source_generation = %s
  AND message.message_id = %s
  AND message.provider = %s
  AND message.provider_deleted IS FALSE
  AND state.is_current IS TRUE
  AND state.provider_account_identity = %s
""".strip()

_SELECT_MESSAGE_FOR_BODY_UPDATE = """
SELECT row_version
FROM cuevion_mailbox.mailbox_messages
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND message_id = %s
  AND provider = %s
  AND provider_deleted IS FALSE
FOR UPDATE
""".strip()

_UPSERT_BODY = """
INSERT INTO cuevion_mailbox.mailbox_message_bodies (
    schema_version,
    workspace_id,
    owner_user_id,
    mailbox_id,
    message_id,
    body_text,
    body_html,
    content_hash,
    body_version,
    fetched_at,
    row_version
)
VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
ON CONFLICT (workspace_id, owner_user_id, mailbox_id, message_id)
DO UPDATE SET
    body_text = EXCLUDED.body_text,
    body_html = EXCLUDED.body_html,
    content_hash = EXCLUDED.content_hash,
    body_version = EXCLUDED.body_version,
    fetched_at = EXCLUDED.fetched_at,
    row_version = cuevion_mailbox.mailbox_message_bodies.row_version + 1
WHERE EXCLUDED.body_version >= cuevion_mailbox.mailbox_message_bodies.body_version
""".strip()

_MARK_MESSAGE_BODY_CACHED = """
UPDATE cuevion_mailbox.mailbox_messages
SET body_state = 'cached',
    updated_at = %s,
    row_version = row_version + 1
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND source_generation = %s
  AND message_id = %s
  AND provider = %s
  AND row_version = %s
  AND provider_deleted IS FALSE
""".strip()

_SELECT_MESSAGE_FOR_DELTA = """
SELECT
    provider,
    provider_message_id,
    provider_folder_digest,
    imap_uid_validity,
    imap_uid,
    row_version,
    body_state,
    provider_deleted
FROM cuevion_mailbox.mailbox_messages
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND message_id = %s
FOR UPDATE
""".strip()

_INSERT_MESSAGE = """
INSERT INTO cuevion_mailbox.mailbox_messages (
    schema_version,
    message_id,
    workspace_id,
    owner_user_id,
    mailbox_id,
    source_generation,
    provider,
    provider_message_id,
    provider_thread_id,
    provider_folder,
    provider_folder_digest,
    provider_labels,
    imap_uid_validity,
    imap_uid,
    rfc_message_id,
    in_reply_to,
    references_json,
    sender_address,
    sender_display,
    to_json,
    cc_json,
    subject,
    snippet,
    provider_timestamp,
    unread,
    starred,
    provider_deleted,
    body_state,
    metadata_hash,
    created_at,
    updated_at,
    row_version
)
VALUES (
    1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, FALSE, 'not_cached', %s, %s, %s, 1
)
""".strip()

_UPDATE_MESSAGE = """
UPDATE cuevion_mailbox.mailbox_messages
SET provider_thread_id = %s,
    provider_folder = %s,
    provider_folder_digest = %s,
    provider_labels = %s,
    rfc_message_id = %s,
    in_reply_to = %s,
    references_json = %s,
    sender_address = %s,
    sender_display = %s,
    to_json = %s,
    cc_json = %s,
    subject = %s,
    snippet = %s,
    provider_timestamp = %s,
    unread = %s,
    starred = %s,
    provider_deleted = FALSE,
    metadata_hash = %s,
    updated_at = %s,
    row_version = %s
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND message_id = %s
  AND source_generation = %s
  AND provider = %s
  AND row_version = %s
""".strip()

_TOMBSTONE_MESSAGE = """
UPDATE cuevion_mailbox.mailbox_messages
SET provider_deleted = TRUE,
    updated_at = %s,
    row_version = %s
WHERE workspace_id = %s
  AND owner_user_id = %s
  AND mailbox_id = %s
  AND message_id = %s
  AND source_generation = %s
  AND provider = %s
  AND row_version = %s
  AND provider_deleted IS FALSE
""".strip()

_INSERT_OUTBOX_EVENT = """
INSERT INTO cuevion_mailbox.mailbox_change_outbox (
    schema_version,
    event_id,
    workspace_id,
    owner_user_id,
    mailbox_id,
    source_generation,
    message_id,
    message_row_version,
    event_type,
    created_at,
    attempt_count,
    next_attempt_at,
    claim_token,
    claim_expires_at,
    processed_at,
    last_error_code
)
VALUES (
    1, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    0, NULL, NULL, NULL, NULL, NULL
)
""".strip()

_SELECT_DUE_OUTBOX = """
SELECT
    outbox.event_id,
    outbox.workspace_id,
    outbox.owner_user_id,
    outbox.mailbox_id,
    outbox.source_generation,
    state.provider,
    state.provider_account_identity,
    outbox.message_id,
    outbox.message_row_version,
    outbox.event_type,
    outbox.attempt_count
FROM cuevion_mailbox.mailbox_change_outbox AS outbox
JOIN cuevion_mailbox.mailbox_sync_state AS state
  ON state.workspace_id = outbox.workspace_id
 AND state.owner_user_id = outbox.owner_user_id
 AND state.mailbox_id = outbox.mailbox_id
 AND state.source_generation = outbox.source_generation
WHERE outbox.processed_at IS NULL
  AND (outbox.next_attempt_at IS NULL OR outbox.next_attempt_at <= %s)
  AND (outbox.claim_expires_at IS NULL OR outbox.claim_expires_at <= %s)
  AND state.is_current IS TRUE
ORDER BY outbox.created_at ASC, outbox.event_id ASC
FOR UPDATE OF outbox SKIP LOCKED
LIMIT %s
""".strip()

_CLAIM_OUTBOX_EVENT = """
UPDATE cuevion_mailbox.mailbox_change_outbox
SET claim_token = %s,
    claim_expires_at = %s,
    attempt_count = attempt_count + 1
WHERE event_id = %s
  AND processed_at IS NULL
  AND (claim_expires_at IS NULL OR claim_expires_at <= %s)
""".strip()

_MARK_OUTBOX_PROCESSED = """
UPDATE cuevion_mailbox.mailbox_change_outbox
SET processed_at = %s,
    claim_token = NULL,
    claim_expires_at = NULL,
    last_error_code = NULL
WHERE event_id = %s
  AND claim_token = %s
  AND processed_at IS NULL
  AND claim_expires_at >= %s
""".strip()

_MARK_OUTBOX_RETRY = """
UPDATE cuevion_mailbox.mailbox_change_outbox
SET next_attempt_at = %s,
    claim_token = NULL,
    claim_expires_at = NULL,
    last_error_code = %s
WHERE event_id = %s
  AND claim_token = %s
  AND processed_at IS NULL
""".strip()


class _Conflict(Exception):
    pass


class _StaleGeneration(Exception):
    pass


class _NotFound(Exception):
    pass


class _StorageCorruption(Exception):
    pass


def _dt(millis: int) -> datetime:
    if type(millis) is not int or millis < 0 or millis > 253_402_300_799_999:
        raise ValueError("invalid mailbox timestamp")
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def _rows(cursor: object) -> list[tuple[object, ...]]:
    result = cursor.fetchall()
    if type(result) is not list:
        result = list(result)
    return result


def _one_or_none(cursor: object) -> tuple[object, ...] | None:
    rows = _rows(cursor)
    if len(rows) > 1:
        raise _StorageCorruption()
    return None if not rows else tuple(rows[0])


def _party_json(parties: tuple[contract.MailboxParty, ...]) -> list[dict[str, str | None]]:
    return [
        {"address": item.address, "display_name": item.display_name}
        for item in parties
    ]


def _provider(value: object) -> contract.MailboxProvider:
    try:
        return contract.MailboxProvider(value)
    except Exception:
        raise _StorageCorruption() from None


def _backfill(value: object) -> contract.BackfillState:
    try:
        return contract.BackfillState(value)
    except Exception:
        raise _StorageCorruption() from None


def _body_state(value: object) -> contract.BodyState:
    try:
        return contract.BodyState(value)
    except Exception:
        raise _StorageCorruption() from None


def _event_type(value: object) -> contract.OutboxEventType:
    try:
        return contract.OutboxEventType(value)
    except Exception:
        raise _StorageCorruption() from None


def _safe_close(target: object | None, method: str) -> None:
    if target is None:
        return
    try:
        getattr(target, method)()
    except Exception:
        return


def _is_unavailable(error: BaseException) -> bool:
    return isinstance(error, psycopg.OperationalError)


def _is_concurrency(error: BaseException) -> bool:
    return isinstance(
        error,
        (
            psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected,
            psycopg.errors.UniqueViolation,
        ),
    )


def _raise_repository_failure(error: BaseException) -> None:
    if _is_unavailable(error):
        raise MailboxRepositoryUnavailableError() from None
    raise MailboxRepositoryIntegrityError() from None


class PostgreSQLMailboxRepository:
    __slots__ = ("_reader_factory", "_writer_factory")

    def __init__(
        self,
        reader_connection_factory: PostgreSQLConnectionFactory,
        writer_connection_factory: PostgreSQLConnectionFactory,
    ) -> None:
        if not callable(reader_connection_factory) or not callable(
            writer_connection_factory
        ):
            raise TypeError("mailbox repository requires connection factories")
        object.__setattr__(self, "_reader_factory", reader_connection_factory)
        object.__setattr__(self, "_writer_factory", writer_connection_factory)

    def _read(self, operation: Callable[[object], object]) -> object:
        connection = None
        cursor = None
        try:
            connection = self._reader_factory()
            cursor = connection.cursor()
            cursor.execute(_SET_READER_TRANSACTION)
            result = operation(cursor)
            connection.rollback()
            return result
        except BaseException as error:
            _safe_close(connection, "rollback")
            if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            if isinstance(error, _StorageCorruption):
                raise MailboxRepositoryIntegrityError() from None
            _raise_repository_failure(error)
        finally:
            _safe_close(cursor, "close")
            _safe_close(connection, "close")

    def _write(
        self,
        operation: Callable[[object], object],
        *,
        concurrency_result: object,
    ) -> object:
        connection = None
        cursor = None
        try:
            connection = self._writer_factory()
            cursor = connection.cursor()
            cursor.execute(_SET_WRITER_TRANSACTION)
            result = operation(cursor)
            connection.commit()
            return result
        except BaseException as error:
            _safe_close(connection, "rollback")
            if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            if isinstance(error, _Conflict) or _is_concurrency(error):
                return concurrency_result
            if isinstance(error, _StaleGeneration):
                raise
            if isinstance(error, _NotFound):
                raise
            if isinstance(error, _StorageCorruption):
                raise MailboxRepositoryIntegrityError() from None
            if isinstance(error, psycopg.IntegrityError):
                raise MailboxRepositoryIntegrityError() from None
            _raise_repository_failure(error)
        finally:
            _safe_close(cursor, "close")
            _safe_close(connection, "close")

    def activate_generation(
        self,
        binding: contract.MailboxBinding,
        *,
        bootstrap_state: contract.BootstrapState,
        activated_at_millis: int,
        backfill_cutoff_millis: int | None,
    ) -> contract.GenerationActivation:
        if type(binding) is not contract.MailboxBinding:
            raise ValueError("invalid mailbox binding")
        if type(bootstrap_state) is not contract.BootstrapState:
            raise ValueError("invalid bootstrap state")
        activated_at = _dt(activated_at_millis)
        cutoff = (
            None
            if backfill_cutoff_millis is None
            else _dt(backfill_cutoff_millis)
        )

        def operation(cursor: object) -> contract.GenerationActivation:
            key = (
                binding.workspace_id,
                binding.owner_user_id,
                binding.mailbox_id,
            )
            cursor.execute(_SELECT_CURRENT_GENERATION, key)
            current = _one_or_none(cursor)
            if current is not None:
                if len(current) != 4:
                    raise _StorageCorruption()
                provider = _provider(current[0])
                account_identity = current[1]
                generation = current[2]
                row_version = current[3]
                if (
                    type(account_identity) is not str
                    or type(generation) is not int
                    or generation < 1
                    or type(row_version) is not int
                    or row_version < 1
                ):
                    raise _StorageCorruption()
                if (
                    provider is binding.provider
                    and account_identity == binding.provider_account_identity
                ):
                    scope = contract.MailboxScope(
                        workspace_id=binding.workspace_id,
                        owner_user_id=binding.owner_user_id,
                        mailbox_id=binding.mailbox_id,
                        source_generation=generation,
                        provider=provider,
                        provider_account_identity=account_identity,
                    )
                    return contract.GenerationActivation(
                        outcome=contract.GenerationActivationOutcome.CURRENT,
                        scope=scope,
                        state_row_version=row_version,
                    )
            cursor.execute(_SELECT_MAX_GENERATION, key)
            maximum = _one_or_none(cursor)
            if maximum is None or len(maximum) != 1:
                raise _StorageCorruption()
            max_generation = maximum[0]
            if type(max_generation) is not int or max_generation < 0:
                raise _StorageCorruption()
            generation = max_generation + 1

            if current is not None:
                cursor.execute(
                    _DEACTIVATE_GENERATION,
                    (
                        activated_at,
                        binding.workspace_id,
                        binding.owner_user_id,
                        binding.mailbox_id,
                        current[2],
                        current[3],
                    ),
                )
                if cursor.rowcount != 1:
                    raise _Conflict()

            cursor.execute(
                _INSERT_GENERATION,
                (
                    binding.workspace_id,
                    binding.owner_user_id,
                    binding.mailbox_id,
                    binding.provider.value,
                    binding.provider_account_identity,
                    generation,
                    bootstrap_state.value,
                    cutoff,
                    activated_at,
                    activated_at,
                ),
            )
            if cursor.rowcount != 1:
                raise _Conflict()
            scope = contract.MailboxScope(
                workspace_id=binding.workspace_id,
                owner_user_id=binding.owner_user_id,
                mailbox_id=binding.mailbox_id,
                source_generation=generation,
                provider=binding.provider,
                provider_account_identity=binding.provider_account_identity,
            )
            return contract.GenerationActivation(
                outcome=contract.GenerationActivationOutcome.CREATED,
                scope=scope,
                state_row_version=1,
            )

        result = self._write(
            operation,
            concurrency_result=contract.GenerationActivation(
                outcome=contract.GenerationActivationOutcome.CONFLICT,
                scope=None,
                state_row_version=None,
            ),
        )
        if type(result) is not contract.GenerationActivation:
            raise MailboxRepositoryIntegrityError()
        return result

    def read_cursor(
        self,
        scope: contract.MailboxScope,
        scope_key: str,
    ) -> contract.SyncCursor | None:
        if type(scope) is not contract.MailboxScope:
            raise ValueError("invalid mailbox scope")
        digest = contract.derive_locator_digest(scope_key)

        def operation(cursor: object) -> contract.SyncCursor | None:
            cursor.execute(
                _SELECT_CURSOR,
                (
                    scope.workspace_id,
                    scope.owner_user_id,
                    scope.mailbox_id,
                    scope.source_generation,
                    digest,
                    scope.provider.value,
                    scope.provider_account_identity,
                ),
            )
            row = _one_or_none(cursor)
            if row is None:
                return None
            if len(row) != 10 or row[0] != scope_key:
                raise _StorageCorruption()
            return contract.SyncCursor(
                scope_key=row[0],
                cursor_generation=row[1],
                provider=_provider(row[2]),
                gmail_history_id=row[3],
                imap_uid_validity=row[4],
                imap_highest_uid=row[5],
                imap_uidnext_observed=row[6],
                backfill_state=_backfill(row[7]),
                backfill_cursor=row[8],
                row_version=row[9],
            )

        return self._read(operation)  # type: ignore[return-value]

    def list_messages(
        self,
        scope: contract.MailboxScope,
        *,
        limit: int,
        before_timestamp_millis: int | None = None,
    ) -> Sequence[contract.MessageProjection]:
        if type(scope) is not contract.MailboxScope:
            raise ValueError("invalid mailbox scope")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid message limit")
        before = (
            None
            if before_timestamp_millis is None
            else _dt(before_timestamp_millis)
        )

        def operation(cursor: object) -> Sequence[contract.MessageProjection]:
            sql = _LIST_MESSAGES_BASE
            params: list[object] = [
                scope.workspace_id,
                scope.owner_user_id,
                scope.mailbox_id,
                scope.source_generation,
                scope.provider.value,
                scope.provider_account_identity,
            ]
            if before is not None:
                sql += "\n  AND message.provider_timestamp < %s"
                params.append(before)
            sql += (
                "\nORDER BY message.provider_timestamp DESC, "
                "message.message_id DESC\nLIMIT %s"
            )
            params.append(limit)
            cursor.execute(sql, tuple(params))
            results = []
            for raw in _rows(cursor):
                row = tuple(raw)
                if len(row) != 12:
                    raise _StorageCorruption()
                identity = contract.MessageIdentity(
                    message_id=row[0],
                    provider_message_id=row[1],
                    provider_folder=row[2],
                    imap_uid_validity=row[3],
                    imap_uid=row[4],
                )
                projection = contract.MessageProjection(
                    identity=identity,
                    provider_thread_id=row[5],
                    metadata_hash=row[6],
                    body_state=_body_state(row[7]),
                    unread=row[8],
                    starred=row[9],
                    provider_deleted=row[10],
                    row_version=row[11],
                )
                projection.validate_for(scope.provider, scope)
                results.append(projection)
            return tuple(results)

        return self._read(operation)  # type: ignore[return-value]

    def read_cached_body(
        self,
        scope: contract.MailboxScope,
        message_id: str,
    ) -> contract.CachedBody | None:
        if type(scope) is not contract.MailboxScope:
            raise ValueError("invalid mailbox scope")
        if type(message_id) is not str:
            raise ValueError("invalid message id")

        def operation(cursor: object) -> contract.CachedBody | None:
            cursor.execute(
                _SELECT_BODY,
                (
                    scope.workspace_id,
                    scope.owner_user_id,
                    scope.mailbox_id,
                    scope.source_generation,
                    message_id,
                    scope.provider.value,
                    scope.provider_account_identity,
                ),
            )
            row = _one_or_none(cursor)
            if row is None:
                return None
            if len(row) != 6:
                raise _StorageCorruption()
            return contract.CachedBody(
                message_id=row[0],
                body_text=row[1],
                body_html=row[2],
                content_hash=row[3],
                body_version=row[4],
                row_version=row[5],
            )

        return self._read(operation)  # type: ignore[return-value]

    def cache_message_body(
        self,
        scope: contract.MailboxScope,
        message_id: str,
        *,
        expected_message_row_version: int,
        body: contract.BodyWrite,
    ) -> contract.BodyCacheOutcome:
        if (
            type(scope) is not contract.MailboxScope
            or type(message_id) is not str
            or type(expected_message_row_version) is not int
            or expected_message_row_version < 1
            or type(body) is not contract.BodyWrite
        ):
            raise ValueError("invalid body cache request")
        fetched_at = _dt(body.fetched_at_millis)

        def operation(cursor: object) -> contract.BodyCacheOutcome:
            self._lock_current_scope(cursor, scope)
            cursor.execute(
                _SELECT_MESSAGE_FOR_BODY_UPDATE,
                (
                    scope.workspace_id,
                    scope.owner_user_id,
                    scope.mailbox_id,
                    scope.source_generation,
                    message_id,
                    scope.provider.value,
                ),
            )
            row = _one_or_none(cursor)
            if row is None:
                raise _NotFound()
            if len(row) != 1 or type(row[0]) is not int:
                raise _StorageCorruption()
            if row[0] != expected_message_row_version:
                raise _Conflict()

            cursor.execute(
                _UPSERT_BODY,
                (
                    scope.workspace_id,
                    scope.owner_user_id,
                    scope.mailbox_id,
                    message_id,
                    body.body_text,
                    body.body_html,
                    body.content_hash,
                    body.body_version,
                    fetched_at,
                ),
            )
            if cursor.rowcount != 1:
                raise _Conflict()
            cursor.execute(
                _MARK_MESSAGE_BODY_CACHED,
                (
                    fetched_at,
                    scope.workspace_id,
                    scope.owner_user_id,
                    scope.mailbox_id,
                    scope.source_generation,
                    message_id,
                    scope.provider.value,
                    expected_message_row_version,
                ),
            )
            if cursor.rowcount != 1:
                raise _Conflict()
            return contract.BodyCacheOutcome.STORED

        try:
            result = self._write(
                operation,
                concurrency_result=contract.BodyCacheOutcome.CONFLICT,
            )
        except _StaleGeneration:
            return contract.BodyCacheOutcome.STALE_GENERATION
        except _NotFound:
            return contract.BodyCacheOutcome.NOT_FOUND
        if type(result) is not contract.BodyCacheOutcome:
            raise MailboxRepositoryIntegrityError()
        return result

    def _lock_current_scope(
        self,
        cursor: object,
        scope: contract.MailboxScope,
    ) -> int:
        cursor.execute(
            _SELECT_SCOPE_STATE_FOR_UPDATE,
            (
                scope.workspace_id,
                scope.owner_user_id,
                scope.mailbox_id,
                scope.source_generation,
            ),
        )
        row = _one_or_none(cursor)
        if row is None:
            raise _NotFound()
        if len(row) != 4:
            raise _StorageCorruption()
        provider = _provider(row[0])
        identity = row[1]
        current = row[2]
        row_version = row[3]
        if (
            type(identity) is not str
            or type(current) is not bool
            or type(row_version) is not int
            or row_version < 1
        ):
            raise _StorageCorruption()
        if (
            provider is not scope.provider
            or identity != scope.provider_account_identity
            or current is not True
        ):
            raise _StaleGeneration()
        return row_version

    def commit_provider_delta(
        self,
        commit: contract.ProviderDeltaCommit,
    ) -> contract.DeltaCommitOutcome:
        if type(commit) is not contract.ProviderDeltaCommit:
            raise ValueError("invalid provider delta commit")
        committed_at = _dt(commit.committed_at_millis)
        cursor_digest = contract.derive_locator_digest(commit.scope_key)

        def operation(cursor: object) -> contract.DeltaCommitOutcome:
            state_row_version = self._lock_current_scope(cursor, commit.scope)
            if state_row_version != commit.expected_state_row_version:
                raise _Conflict()

            cursor.execute(
                _SELECT_CURSOR_FOR_UPDATE,
                (
                    commit.scope.workspace_id,
                    commit.scope.owner_user_id,
                    commit.scope.mailbox_id,
                    commit.scope.source_generation,
                    cursor_digest,
                ),
            )
            stored_cursor = _one_or_none(cursor)
            if commit.expected_cursor_row_version is None:
                if stored_cursor is not None or commit.next_cursor.row_version != 1:
                    raise _Conflict()
            else:
                if stored_cursor is None or len(stored_cursor) != 4:
                    raise _Conflict()
                if (
                    stored_cursor[0] != commit.scope_key
                    or stored_cursor[1] != commit.expected_cursor_generation
                    or _provider(stored_cursor[2]) is not commit.scope.provider
                    or stored_cursor[3] != commit.expected_cursor_row_version
                    or commit.next_cursor.row_version
                    != commit.expected_cursor_row_version + 1
                ):
                    raise _Conflict()

            for mutation in commit.mutations:
                self._apply_mutation(
                    cursor,
                    commit.scope,
                    mutation,
                    committed_at,
                )

            if commit.expected_cursor_row_version is None:
                cursor.execute(
                    _INSERT_CURSOR,
                    self._cursor_insert_values(
                        commit.scope,
                        commit.next_cursor,
                        cursor_digest,
                        committed_at,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _Conflict()
            else:
                cursor.execute(
                    _UPDATE_CURSOR,
                    self._cursor_update_values(
                        commit.scope,
                        commit.next_cursor,
                        cursor_digest,
                        committed_at,
                        commit.expected_cursor_row_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _Conflict()

            cursor.execute(
                _UPDATE_SYNC_STATE_AFTER_DELTA,
                (
                    commit.next_bootstrap_state.value,
                    committed_at,
                    committed_at,
                    commit.scope.workspace_id,
                    commit.scope.owner_user_id,
                    commit.scope.mailbox_id,
                    commit.scope.source_generation,
                    commit.scope.provider.value,
                    commit.scope.provider_account_identity,
                    commit.expected_state_row_version,
                ),
            )
            if cursor.rowcount != 1:
                raise _Conflict()
            return contract.DeltaCommitOutcome.APPLIED

        try:
            result = self._write(
                operation,
                concurrency_result=contract.DeltaCommitOutcome.CONFLICT,
            )
        except _StaleGeneration:
            return contract.DeltaCommitOutcome.STALE_GENERATION
        except _NotFound:
            return contract.DeltaCommitOutcome.NOT_FOUND
        if type(result) is not contract.DeltaCommitOutcome:
            raise MailboxRepositoryIntegrityError()
        return result

    def _apply_mutation(
        self,
        cursor: object,
        scope: contract.MailboxScope,
        mutation: contract.MessageMutation,
        committed_at: datetime,
    ) -> None:
        mutation.validate_for(scope.provider, scope)
        projection = mutation.projection
        identity = projection.identity
        cursor.execute(
            _SELECT_MESSAGE_FOR_DELTA,
            (
                scope.workspace_id,
                scope.owner_user_id,
                scope.mailbox_id,
                identity.message_id,
            ),
        )
        existing = _one_or_none(cursor)

        if existing is None:
            if mutation.kind is contract.MessageMutationKind.TOMBSTONE:
                return
            if (
                projection.row_version != 1
                or mutation.outbox_event_type
                is not contract.OutboxEventType.MESSAGE_ADDED
                or projection.body_state is not contract.BodyState.NOT_CACHED
                or mutation.write is None
            ):
                raise _Conflict()
            cursor.execute(
                _INSERT_MESSAGE,
                self._message_insert_values(
                    scope, mutation.write, committed_at
                ),
            )
            if cursor.rowcount != 1:
                raise _Conflict()
            self._insert_outbox(
                cursor,
                scope,
                identity.message_id,
                1,
                mutation.outbox_event_type,
                committed_at,
            )
            return

        if len(existing) != 8:
            raise _StorageCorruption()
        (
            stored_provider,
            stored_provider_message_id,
            stored_folder_digest,
            stored_uid_validity,
            stored_uid,
            stored_row_version,
            stored_body_state,
            stored_deleted,
        ) = existing
        stored_body_state_value = _body_state(stored_body_state)
        if (
            _provider(stored_provider) is not scope.provider
            or type(stored_row_version) is not int
            or stored_row_version < 1
            or type(stored_deleted) is not bool
            or projection.body_state is not stored_body_state_value
        ):
            raise _StorageCorruption()
        if not self._provider_identity_matches(
            scope,
            identity,
            stored_provider_message_id,
            stored_folder_digest,
            stored_uid_validity,
            stored_uid,
        ):
            raise _StorageCorruption()
        if projection.row_version != stored_row_version + 1:
            raise _Conflict()

        if mutation.kind is contract.MessageMutationKind.TOMBSTONE:
            if mutation.outbox_event_type is not contract.OutboxEventType.MESSAGE_DELETED:
                raise _Conflict()
            if stored_deleted:
                raise _Conflict()
            cursor.execute(
                _TOMBSTONE_MESSAGE,
                (
                    committed_at,
                    projection.row_version,
                    scope.workspace_id,
                    scope.owner_user_id,
                    scope.mailbox_id,
                    identity.message_id,
                    scope.source_generation,
                    scope.provider.value,
                    stored_row_version,
                ),
            )
            if cursor.rowcount != 1:
                raise _Conflict()
        else:
            if (
                mutation.outbox_event_type
                is not contract.OutboxEventType.MESSAGE_CHANGED
                or mutation.write is None
            ):
                raise _Conflict()
            cursor.execute(
                _UPDATE_MESSAGE,
                self._message_update_values(
                    scope,
                    mutation.write,
                    committed_at,
                    stored_row_version,
                ),
            )
            if cursor.rowcount != 1:
                raise _Conflict()

        self._insert_outbox(
            cursor,
            scope,
            identity.message_id,
            projection.row_version,
            mutation.outbox_event_type,
            committed_at,
        )

    @staticmethod
    def _provider_identity_matches(
        scope: contract.MailboxScope,
        identity: contract.MessageIdentity,
        stored_provider_message_id: object,
        stored_folder_digest: object,
        stored_uid_validity: object,
        stored_uid: object,
    ) -> bool:
        if scope.provider is contract.MailboxProvider.GOOGLE:
            return (
                stored_provider_message_id == identity.provider_message_id
                and stored_uid_validity is None
                and stored_uid is None
            )
        return (
            stored_provider_message_id is None
            and stored_folder_digest
            == contract.derive_locator_digest(identity.provider_folder)
            and stored_uid_validity == identity.imap_uid_validity
            and stored_uid == identity.imap_uid
        )

    @staticmethod
    def _message_insert_values(
        scope: contract.MailboxScope,
        write: contract.MessageWrite,
        committed_at: datetime,
    ) -> tuple[object, ...]:
        p = write.projection
        i = p.identity
        return (
            i.message_id,
            scope.workspace_id,
            scope.owner_user_id,
            scope.mailbox_id,
            scope.source_generation,
            scope.provider.value,
            i.provider_message_id,
            p.provider_thread_id,
            i.provider_folder,
            contract.derive_locator_digest(i.provider_folder),
            Jsonb(list(write.provider_labels)),
            i.imap_uid_validity,
            i.imap_uid,
            write.rfc_message_id,
            write.in_reply_to,
            Jsonb(list(write.references)),
            None if write.sender is None else write.sender.address,
            None if write.sender is None else write.sender.display_name,
            Jsonb(_party_json(write.to)),
            Jsonb(_party_json(write.cc)),
            write.subject,
            write.snippet,
            _dt(write.provider_timestamp_millis),
            p.unread,
            p.starred,
            p.metadata_hash,
            committed_at,
            committed_at,
        )

    @staticmethod
    def _message_update_values(
        scope: contract.MailboxScope,
        write: contract.MessageWrite,
        committed_at: datetime,
        stored_row_version: int,
    ) -> tuple[object, ...]:
        p = write.projection
        i = p.identity
        return (
            p.provider_thread_id,
            i.provider_folder,
            contract.derive_locator_digest(i.provider_folder),
            Jsonb(list(write.provider_labels)),
            write.rfc_message_id,
            write.in_reply_to,
            Jsonb(list(write.references)),
            None if write.sender is None else write.sender.address,
            None if write.sender is None else write.sender.display_name,
            Jsonb(_party_json(write.to)),
            Jsonb(_party_json(write.cc)),
            write.subject,
            write.snippet,
            _dt(write.provider_timestamp_millis),
            p.unread,
            p.starred,
            p.metadata_hash,
            committed_at,
            p.row_version,
            scope.workspace_id,
            scope.owner_user_id,
            scope.mailbox_id,
            i.message_id,
            scope.source_generation,
            scope.provider.value,
            stored_row_version,
        )

    @staticmethod
    def _cursor_insert_values(
        scope: contract.MailboxScope,
        value: contract.SyncCursor,
        digest: str,
        committed_at: datetime,
    ) -> tuple[object, ...]:
        return (
            scope.workspace_id,
            scope.owner_user_id,
            scope.mailbox_id,
            scope.source_generation,
            scope.provider.value,
            value.scope_key,
            digest,
            value.cursor_generation,
            value.gmail_history_id,
            value.imap_uid_validity,
            value.imap_highest_uid,
            value.imap_uidnext_observed,
            value.backfill_state.value,
            value.backfill_cursor,
            committed_at,
            committed_at,
            committed_at,
        )

    @staticmethod
    def _cursor_update_values(
        scope: contract.MailboxScope,
        value: contract.SyncCursor,
        digest: str,
        committed_at: datetime,
        expected_row_version: int,
    ) -> tuple[object, ...]:
        return (
            value.scope_key,
            value.cursor_generation,
            value.gmail_history_id,
            value.imap_uid_validity,
            value.imap_highest_uid,
            value.imap_uidnext_observed,
            value.backfill_state.value,
            value.backfill_cursor,
            committed_at,
            committed_at,
            value.row_version,
            scope.workspace_id,
            scope.owner_user_id,
            scope.mailbox_id,
            scope.source_generation,
            digest,
            scope.provider.value,
            expected_row_version,
        )

    @staticmethod
    def _insert_outbox(
        cursor: object,
        scope: contract.MailboxScope,
        message_id: str,
        row_version: int,
        event_type: contract.OutboxEventType,
        committed_at: datetime,
    ) -> None:
        event_id = contract.derive_outbox_event_id(
            scope,
            message_id=message_id,
            message_row_version=row_version,
            event_type=event_type,
        )
        cursor.execute(
            _INSERT_OUTBOX_EVENT,
            (
                event_id,
                scope.workspace_id,
                scope.owner_user_id,
                scope.mailbox_id,
                scope.source_generation,
                message_id,
                row_version,
                event_type.value,
                committed_at,
            ),
        )
        if cursor.rowcount != 1:
            raise _Conflict()

    def claim_outbox_batch(
        self,
        *,
        limit: int,
        now_millis: int,
        lease_millis: int,
    ) -> Sequence[contract.OutboxEvent]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(lease_millis) is not int
            or not 1_000 <= lease_millis <= 3_600_000
        ):
            raise ValueError("invalid outbox claim request")
        now = _dt(now_millis)
        expires = _dt(now_millis + lease_millis)

        def operation(cursor: object) -> Sequence[contract.OutboxEvent]:
            cursor.execute(_SELECT_DUE_OUTBOX, (now, now, limit))
            rows = _rows(cursor)
            results = []
            for raw in rows:
                row = tuple(raw)
                if len(row) != 11:
                    raise _StorageCorruption()
                claim_token = secrets.token_hex(16)
                cursor.execute(
                    _CLAIM_OUTBOX_EVENT,
                    (claim_token, expires, row[0], now),
                )
                if cursor.rowcount != 1:
                    raise _Conflict()
                scope = contract.MailboxScope(
                    workspace_id=row[1],
                    owner_user_id=row[2],
                    mailbox_id=row[3],
                    source_generation=row[4],
                    provider=_provider(row[5]),
                    provider_account_identity=row[6],
                )
                results.append(
                    contract.OutboxEvent(
                        event_id=row[0],
                        scope=scope,
                        message_id=row[7],
                        message_row_version=row[8],
                        event_type=_event_type(row[9]),
                        attempt_count=row[10] + 1,
                        claim_token=claim_token,
                    )
                )
            return tuple(results)

        result = self._write(operation, concurrency_result=())
        if not isinstance(result, tuple):
            raise MailboxRepositoryIntegrityError()
        return result

    def mark_outbox_processed(
        self,
        event_id: str,
        *,
        claim_token: str,
        processed_at_millis: int,
    ) -> bool:
        if (
            type(event_id) is not str
            or type(claim_token) is not str
            or not 16 <= len(claim_token) <= 64
        ):
            raise ValueError("invalid outbox completion")
        processed_at = _dt(processed_at_millis)

        def operation(cursor: object) -> bool:
            cursor.execute(
                _MARK_OUTBOX_PROCESSED,
                (
                    processed_at,
                    event_id,
                    claim_token,
                    processed_at,
                ),
            )
            return cursor.rowcount == 1

        result = self._write(operation, concurrency_result=False)
        return bool(result)

    def mark_outbox_retry(
        self,
        event_id: str,
        *,
        claim_token: str,
        next_attempt_at_millis: int,
        safe_error_code: str,
    ) -> bool:
        if (
            type(event_id) is not str
            or type(claim_token) is not str
            or not 16 <= len(claim_token) <= 64
            or type(safe_error_code) is not str
            or not 1 <= len(safe_error_code.encode("utf-8")) <= 128
        ):
            raise ValueError("invalid outbox retry")
        next_attempt = _dt(next_attempt_at_millis)

        def operation(cursor: object) -> bool:
            cursor.execute(
                _MARK_OUTBOX_RETRY,
                (
                    next_attempt,
                    safe_error_code,
                    event_id,
                    claim_token,
                ),
            )
            return cursor.rowcount == 1

        result = self._write(operation, concurrency_result=False)
        return bool(result)


__all__ = (
    "MailboxRepositoryIntegrityError",
    "MailboxRepositoryUnavailableError",
    "PostgreSQLConnectionFactory",
    "PostgreSQLMailboxRepository",
)
