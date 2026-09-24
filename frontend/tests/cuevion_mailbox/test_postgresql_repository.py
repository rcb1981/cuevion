"""Security and structure tests for the inert PostgreSQL mailbox adapter."""

import ast
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from cuevion_mailbox import postgresql_repository as repository
from cuevion_mailbox.repository_contract import (
    BootstrapState,
    CurrentStateInitializationOutcome,
    MailboxProvider,
    MailboxReadAuthority,
    MailboxScope,
    MailboxStateSnapshot,
    OutboxEvent,
    OutboxEventType,
    OutboxMailboxScope,
    OutboxStorageScope,
)
from cuevion_mailbox.role_policy import build_mailbox_role_plan


_FRONTEND = Path(__file__).resolve().parents[2]
_ADAPTER = _FRONTEND / "cuevion_mailbox" / "postgresql_repository.py"
_ROLE_POLICY = _FRONTEND / "cuevion_mailbox" / "role_policy.py"


class MailboxRolePolicyTests(unittest.TestCase):
    def test_creation_is_restricted_and_password_free(self):
        plan = build_mailbox_role_plan(
            "cuevion_test_mailbox_reader_v1",
            "cuevion_test_mailbox_writer_v1",
        )
        self.assertEqual(len(plan.creation_statements), 2)
        for statement in plan.creation_statements:
            normalized = statement.casefold()
            self.assertIn(" login ", normalized)
            self.assertIn(" nosuperuser ", normalized)
            self.assertIn(" nocreatedb ", normalized)
            self.assertIn(" nocreaterole ", normalized)
            self.assertIn(" noinherit ", normalized)
            self.assertIn(" nobypassrls", normalized)
            self.assertNotIn("password", normalized)
            self.assertNotIn("neon_superuser", normalized)

    def test_reader_and_writer_grants_are_exactly_bounded(self):
        plan = build_mailbox_role_plan(
            "cuevion_test_mailbox_reader_v1",
            "cuevion_test_mailbox_writer_v1",
        )
        sql = "\n".join(plan.grant_statements).casefold()
        self.assertIn("grant usage on schema cuevion_mailbox", sql)
        self.assertIn("grant select on cuevion_mailbox.mailbox_sync_state", sql)
        self.assertIn("grant select, insert, update on cuevion_mailbox.mailbox_sync_state", sql)
        reader_select = next(
            statement.casefold()
            for statement in plan.grant_statements
            if statement.casefold().startswith("grant select on ")
        )
        self.assertNotIn("mailbox_change_outbox", reader_select)
        writer_grant = next(
            statement.casefold()
            for statement in plan.grant_statements
            if statement.casefold().startswith("grant select, insert, update on ")
        )
        self.assertIn("mailbox_change_outbox", writer_grant)
        for forbidden in (
            " delete ",
            " truncate ",
            " references ",
            " trigger ",
            "cuevion_account.",
            "grant create",
        ):
            self.assertNotIn(forbidden, sql)

    def test_invalid_or_equal_roles_fail_closed(self):
        with self.assertRaises(ValueError):
            build_mailbox_role_plan("reader", "reader")
        with self.assertRaises(ValueError):
            build_mailbox_role_plan("Reader-Unsafe", "writer_safe")


class MailboxReadAuthorityTests(unittest.TestCase):
    def test_authority_rejects_non_enum_provider_and_out_of_bounds_identity(self):
        common = {
            "workspace_id": "wsp_" + ("a" * 22),
            "owner_user_id": "usr_" + ("b" * 22),
            "mailbox_id": "mailbox-1",
        }
        with self.assertRaises(ValueError):
            MailboxReadAuthority(
                **common,
                provider="google",  # type: ignore[arg-type]
                provider_account_identity="user@example.com",
            )
        with self.assertRaises(ValueError):
            MailboxReadAuthority(
                **common,
                provider=MailboxProvider.GOOGLE,
                provider_account_identity="x",
            )


class PostgreSQLMailboxReaderDelegationTests(unittest.TestCase):
    def test_current_state_lookup_delegates_and_scope_is_derived_from_state(self):
        authority = MailboxReadAuthority(
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            provider=MailboxProvider.GOOGLE,
            provider_account_identity="verified@gmail.com",
        )
        state = MailboxStateSnapshot(
            scope=MailboxScope(
                workspace_id=authority.workspace_id,
                owner_user_id=authority.owner_user_id,
                mailbox_id=authority.mailbox_id,
                source_generation=4,
                provider=authority.provider,
                provider_account_identity=authority.provider_account_identity,
            ),
            bootstrap_state=BootstrapState.RECENT_READY,
            row_version=8,
        )
        reader = repository.PostgreSQLMailboxReaderRepository(lambda: None)
        with patch.object(
            repository.PostgreSQLMailboxRepository,
            "resolve_current_state",
            return_value=state,
        ) as resolve:
            self.assertIs(reader.resolve_current_state(authority), state)
            self.assertIs(reader.resolve_current_scope(authority), state.scope)
        self.assertEqual(resolve.call_count, 2)
        resolve.assert_called_with(authority)


    def test_active_message_inventory_delegates(self):
        scope = MailboxScope(
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            source_generation=1,
            provider=MailboxProvider.GOOGLE,
            provider_account_identity="verified@gmail.com",
        )
        reader = repository.PostgreSQLMailboxReaderRepository(lambda: None)
        sentinel = object()
        with patch.object(
            repository.PostgreSQLMailboxRepository,
            "read_active_message_inventory",
            return_value=sentinel,
        ) as inventory:
            self.assertIs(
                reader.read_active_message_inventory(scope, limit=100),
                sentinel,
            )
        inventory.assert_called_once_with(scope, limit=100)


    def test_exact_provider_message_lookup_delegates(self):
        scope = MailboxScope(
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            source_generation=1,
            provider=MailboxProvider.GOOGLE,
            provider_account_identity="verified@gmail.com",
        )
        reader = repository.PostgreSQLMailboxReaderRepository(lambda: None)
        with patch.object(
            repository.PostgreSQLMailboxRepository,
            "read_messages_by_provider_message_ids",
            return_value=(),
        ) as exact_read:
            self.assertEqual(
                reader.read_messages_by_provider_message_ids(
                    scope,
                    ["gmail-message-1"],
                ),
                (),
            )
        exact_read.assert_called_once_with(scope, ["gmail-message-1"])


class _BootstrapCursor:
    def __init__(self, insert_rowcount: int, state_rows: list[tuple[object, ...]]) -> None:
        self.insert_rowcount = insert_rowcount
        self.state_rows = state_rows
        self.rowcount = -1
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        self.executions.append((sql, parameters))
        self.rowcount = (
            self.insert_rowcount
            if sql == repository._INSERT_INITIAL_STATE_SQL
            else len(self.state_rows)
        )

    def fetchall(self) -> list[tuple[object, ...]]:
        if not self.executions or self.executions[-1][0] != repository._SELECT_CURRENT_STATE_SQL:
            raise AssertionError("unexpected fetch")
        return list(self.state_rows)

    def close(self) -> None:
        self.closed = True


class _BootstrapConnection:
    autocommit = False

    def __init__(self, cursor: _BootstrapCursor) -> None:
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> _BootstrapCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class PostgreSQLMailboxCurrentStateBootstrapTests(unittest.TestCase):
    def _authority(self) -> MailboxReadAuthority:
        return MailboxReadAuthority(
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            provider=MailboxProvider.GOOGLE,
            provider_account_identity="verified@gmail.com",
        )

    def _repository(
        self,
        *,
        insert_rowcount: int,
        state_rows: list[tuple[object, ...]],
    ):
        cursor = _BootstrapCursor(insert_rowcount, state_rows)
        connection = _BootstrapConnection(cursor)
        adapter = repository.PostgreSQLMailboxRepository(lambda: connection)
        return adapter, connection, cursor

    def test_resolve_current_state_missing_returns_none_without_outbox_scope_logic(self):
        adapter, connection, cursor = self._repository(
            insert_rowcount=0,
            state_rows=[],
        )

        self.assertIsNone(adapter.resolve_current_state(self._authority()))

        self.assertEqual(
            [sql for sql, _parameters in cursor.executions],
            [repository._SELECT_CURRENT_STATE_SQL],
        )
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)
        self.assertTrue(cursor.closed)

    def test_initial_state_create_is_generation_one_and_commits_once(self):
        adapter, connection, cursor = self._repository(
            insert_rowcount=1,
            state_rows=[(1, "not_started", 1)],
        )
        result = adapter.initialize_current_state(
            self._authority(),
            initialized_at_millis=1_790_110_000_000,
        )
        self.assertIs(result.outcome, CurrentStateInitializationOutcome.CREATED)
        self.assertEqual(result.state.scope.source_generation, 1)
        self.assertEqual(result.state.row_version, 1)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(connection.closed)
        self.assertTrue(cursor.closed)
        self.assertEqual(
            [sql for sql, _ in cursor.executions],
            [
                repository._INSERT_INITIAL_STATE_SQL,
                repository._SELECT_CURRENT_STATE_SQL,
            ],
        )

    def test_exact_existing_state_is_idempotent_and_rolls_back_read_transaction(self):
        adapter, connection, _cursor = self._repository(
            insert_rowcount=0,
            state_rows=[(3, "recent_ready", 7)],
        )
        result = adapter.initialize_current_state(
            self._authority(),
            initialized_at_millis=1_790_110_000_000,
        )
        self.assertIs(result.outcome, CurrentStateInitializationOutcome.EXISTING)
        self.assertEqual(result.state.scope.source_generation, 3)
        self.assertEqual(result.state.row_version, 7)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_conflicting_or_unsafe_existing_state_fails_closed(self):
        adapter, connection, _cursor = self._repository(
            insert_rowcount=0,
            state_rows=[],
        )
        result = adapter.initialize_current_state(
            self._authority(),
            initialized_at_millis=1_790_110_000_000,
        )
        self.assertIs(result.outcome, CurrentStateInitializationOutcome.CONFLICT)
        self.assertIsNone(result.state)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_initial_state_sql_is_generation_one_current_and_idempotent(self):
        normalized = " ".join(repository._INSERT_INITIAL_STATE_SQL.casefold().split())
        self.assertIn("source_generation", normalized)
        self.assertIn("1, true, 'not_started'", normalized)
        self.assertIn("on conflict do nothing", normalized)


class _ExactLookupCursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        self.executions.append((sql, parameters))

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self.rows)

    def close(self) -> None:
        self.closed = True


class _ExactLookupConnection:
    autocommit = False

    def __init__(self, cursor: _ExactLookupCursor) -> None:
        self._cursor = cursor
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> _ExactLookupCursor:
        return self._cursor

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class PostgreSQLMailboxExactProviderLookupTests(unittest.TestCase):
    def _scope(self, provider=MailboxProvider.GOOGLE):
        return MailboxScope(
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            source_generation=1,
            provider=provider,
            provider_account_identity="verified@gmail.com",
        )

    def test_exact_lookup_returns_active_and_tombstoned_rows(self):
        rows = [
            (
                "mbm_" + ("a" * 22),
                "gmail-message-1",
                "Inbox",
                None,
                None,
                "thread-1",
                "1" * 64,
                "cached",
                True,
                False,
                False,
                4,
            ),
            (
                "mbm_" + ("b" * 22),
                "gmail-message-2",
                "Inbox",
                None,
                None,
                "thread-2",
                "2" * 64,
                "stale",
                False,
                False,
                True,
                7,
            ),
        ]
        cursor = _ExactLookupCursor(rows)
        connection = _ExactLookupConnection(cursor)
        adapter = repository.PostgreSQLMailboxRepository(lambda: connection)

        projections = adapter.read_messages_by_provider_message_ids(
            self._scope(),
            ["gmail-message-1", "gmail-message-2"],
        )

        self.assertEqual(len(projections), 2)
        self.assertFalse(projections[0].provider_deleted)
        self.assertTrue(projections[1].provider_deleted)
        self.assertEqual(projections[1].row_version, 7)
        self.assertEqual(len(cursor.executions), 1)
        sql, parameters = cursor.executions[0]
        self.assertEqual(sql, repository._SELECT_MESSAGES_BY_PROVIDER_IDS_SQL)
        self.assertEqual(
            parameters[-1],
            ["gmail-message-1", "gmail-message-2"],
        )
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)
        self.assertTrue(cursor.closed)

    def test_exact_lookup_empty_is_connection_free_and_invalid_shapes_fail(self):
        adapter = repository.PostgreSQLMailboxRepository(
            lambda: (_ for _ in ()).throw(AssertionError("connection opened"))
        )
        self.assertEqual(
            adapter.read_messages_by_provider_message_ids(self._scope(), []),
            (),
        )
        with self.assertRaises(ValueError):
            adapter.read_messages_by_provider_message_ids(
                self._scope(),
                ["gmail-message-1", "gmail-message-1"],
            )
        with self.assertRaises(ValueError):
            adapter.read_messages_by_provider_message_ids(
                self._scope(MailboxProvider.CUSTOM_IMAP),
                ["gmail-message-1"],
            )


class _OutboxResolutionCursor:
    def __init__(self, *, message_rows, state_rows=()) -> None:
        self.message_rows = list(message_rows)
        self.state_rows = list(state_rows)
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        self.executions.append((sql, parameters))

    def fetchall(self) -> list[tuple[object, ...]]:
        if not self.executions:
            raise AssertionError("fetch without execute")
        sql = self.executions[-1][0]
        if sql == repository._SELECT_OUTBOX_MESSAGE_SQL:
            return list(self.message_rows)
        if sql == repository._SELECT_OUTBOX_SCOPE_CURRENT_SQL:
            return list(self.state_rows)
        raise AssertionError("unexpected SQL")

    def close(self) -> None:
        self.closed = True


class PostgreSQLMailboxOutboxResolutionTests(unittest.TestCase):
    def _event(self, *, row_version=4):
        return OutboxEvent(
            event_id="mbe_" + ("a" * 22),
            scope=OutboxStorageScope(
                workspace_id="wsp_" + ("a" * 22),
                owner_user_id="usr_" + ("b" * 22),
                mailbox_id="gmail-1",
                source_generation=3,
            ),
            message_id="mbm_" + ("c" * 22),
            message_row_version=row_version,
            event_type=OutboxEventType.MESSAGE_CHANGED,
            attempt_count=1,
            claim_token="claim-token",
        )

    def _message_row(self, *, deleted=False, row_version=4):
        return (
            "google",
            "verified@gmail.com",
            "mbm_" + ("c" * 22),
            "gmail-message-1",
            "Inbox",
            None,
            None,
            "thread-1",
            ["INBOX", "UNREAD"],
            "rfc-1@example.test",
            None,
            ["root@example.test"],
            "sender@example.test",
            "Sender",
            ["owner@example.test"],
            [],
            "Subject",
            "Snippet",
            datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc),
            True,
            False,
            "stale" if deleted else "cached",
            "1" * 64,
            deleted,
            row_version,
        )

    def _repository(self, *, message_rows, state_rows=()):
        cursor = _OutboxResolutionCursor(
            message_rows=message_rows,
            state_rows=state_rows,
        )
        connection = _ExactLookupConnection(cursor)
        return (
            repository.PostgreSQLMailboxRepository(lambda: connection),
            connection,
            cursor,
        )

    def test_current_event_resolves_full_exact_message_snapshot(self):
        adapter, connection, cursor = self._repository(
            message_rows=[self._message_row()],
        )

        snapshot = adapter.resolve_outbox_message(self._event())

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.scope.source_generation, 3)
        self.assertEqual(snapshot.scope.provider_account_identity, "verified@gmail.com")
        self.assertEqual(
            snapshot.record.identity.provider_message_id,
            "gmail-message-1",
        )
        self.assertEqual(snapshot.record.provider_thread_id, "thread-1")
        self.assertEqual(snapshot.record.provider_labels, ("INBOX", "UNREAD"))
        self.assertEqual(snapshot.record.sender_address, "sender@example.test")
        self.assertEqual(snapshot.record.subject, "Subject")
        self.assertEqual(snapshot.record.provider_timestamp_millis, 1790236800000)
        self.assertIs(snapshot.record.body_state, repository.BodyState.CACHED)
        self.assertFalse(snapshot.provider_deleted)
        self.assertEqual(snapshot.row_version, 4)
        self.assertEqual(
            [sql for sql, _params in cursor.executions],
            [repository._SELECT_OUTBOX_MESSAGE_SQL],
        )
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)
        self.assertTrue(cursor.closed)

    def test_stale_generation_returns_none_only_after_current_scope_check(self):
        adapter, _connection, cursor = self._repository(
            message_rows=[],
            state_rows=[],
        )

        self.assertIsNone(adapter.resolve_outbox_message(self._event()))

        self.assertEqual(
            [sql for sql, _params in cursor.executions],
            [
                repository._SELECT_OUTBOX_MESSAGE_SQL,
                repository._SELECT_OUTBOX_SCOPE_CURRENT_SQL,
            ],
        )

    def test_missing_message_in_current_generation_is_storage_corruption(self):
        adapter, _connection, _cursor = self._repository(
            message_rows=[],
            state_rows=[("google", "verified@gmail.com")],
        )

        with self.assertRaises(RuntimeError):
            adapter.resolve_outbox_message(self._event())

    def test_tombstoned_current_row_is_returned_not_hidden(self):
        adapter, _connection, _cursor = self._repository(
            message_rows=[
                self._message_row(deleted=True, row_version=5),
            ],
        )

        snapshot = adapter.resolve_outbox_message(
            self._event(row_version=5)
        )

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertTrue(snapshot.provider_deleted)
        self.assertEqual(snapshot.row_version, 5)
        self.assertIs(snapshot.record.body_state, repository.BodyState.STALE)


class PostgreSQLMailboxActiveInventoryTests(unittest.TestCase):
    def _scope(self):
        return MailboxScope(
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            source_generation=1,
            provider=MailboxProvider.GOOGLE,
            provider_account_identity="verified@gmail.com",
        )

    def _row(self, suffix: str, *, deleted: bool = False, row_version: int = 1):
        return (
            "mbm_" + (suffix * 22),
            "gmail-message-" + suffix,
            "Inbox",
            None,
            None,
            "thread-" + suffix,
            suffix * 64,
            "cached",
            True,
            False,
            deleted,
            row_version,
        )

    def test_complete_active_inventory_returns_projections(self):
        cursor = _ExactLookupCursor(
            [
                self._row("a", row_version=3),
                self._row("b", row_version=5),
            ]
        )
        connection = _ExactLookupConnection(cursor)
        adapter = repository.PostgreSQLMailboxRepository(lambda: connection)

        inventory = adapter.read_active_message_inventory(
            self._scope(),
            limit=100,
        )

        self.assertFalse(inventory.overflow)
        self.assertEqual(len(inventory.projections), 2)
        self.assertEqual(
            [p.identity.provider_message_id for p in inventory.projections],
            ["gmail-message-a", "gmail-message-b"],
        )
        self.assertTrue(all(not p.provider_deleted for p in inventory.projections))
        sql, parameters = cursor.executions[0]
        self.assertEqual(sql, repository._SELECT_ACTIVE_MESSAGE_INVENTORY_SQL)
        self.assertEqual(parameters[-1], 101)
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)
        self.assertTrue(cursor.closed)

    def test_limit_plus_one_row_reports_overflow_without_partial_projection(self):
        cursor = _ExactLookupCursor(
            [
                self._row("a"),
                self._row("b"),
            ]
        )
        connection = _ExactLookupConnection(cursor)
        adapter = repository.PostgreSQLMailboxRepository(lambda: connection)

        inventory = adapter.read_active_message_inventory(
            self._scope(),
            limit=1,
        )

        self.assertTrue(inventory.overflow)
        self.assertEqual(inventory.projections, ())

    def test_deleted_storage_row_or_invalid_limit_fails_closed(self):
        cursor = _ExactLookupCursor([self._row("a", deleted=True)])
        connection = _ExactLookupConnection(cursor)
        adapter = repository.PostgreSQLMailboxRepository(lambda: connection)
        with self.assertRaises(RuntimeError):
            adapter.read_active_message_inventory(self._scope(), limit=100)

        for invalid in (0, 101, True):
            with self.subTest(limit=invalid):
                adapter = repository.PostgreSQLMailboxRepository(
                    lambda: (_ for _ in ()).throw(
                        AssertionError("connection opened")
                    )
                )
                with self.assertRaises(ValueError):
                    adapter.read_active_message_inventory(
                        self._scope(),
                        limit=invalid,
                    )


class _ScopedClaimCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executions = []
        self.closed = False

    def execute(self, sql, parameters):
        self.executions.append((sql, parameters))

    def fetchall(self):
        return list(self.rows)

    def close(self):
        self.closed = True


class _ScopedClaimConnection:
    autocommit = False

    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class PostgreSQLMailboxScopedOutboxClaimTests(unittest.TestCase):
    def _scope(self):
        return OutboxMailboxScope(
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
        )

    def test_scoped_claim_returns_only_exact_mailbox_rows(self):
        rows = [
            (
                "mbe_" + ("a" * 22),
                self._scope().workspace_id,
                self._scope().owner_user_id,
                self._scope().mailbox_id,
                2,
                "mbm_" + ("c" * 22),
                4,
                "message_changed",
                1,
                "claim-token",
            )
        ]
        cursor = _ScopedClaimCursor(rows)
        connection = _ScopedClaimConnection(cursor)
        adapter = repository.PostgreSQLMailboxRepository(lambda: connection)

        claimed = adapter.claim_outbox_batch_for_mailbox(
            self._scope(),
            limit=20,
            now_millis=1_790_250_000_000,
            lease_millis=60_000,
        )

        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].scope.source_generation, 2)
        self.assertEqual(claimed[0].attempt_count, 1)
        sql, parameters = cursor.executions[0]
        self.assertEqual(sql, repository._CLAIM_OUTBOX_FOR_MAILBOX_SQL)
        self.assertEqual(parameters[2:5], (
            self._scope().workspace_id,
            self._scope().owner_user_id,
            self._scope().mailbox_id,
        ))
        self.assertEqual(parameters[5], 20)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(connection.closed)
        self.assertTrue(cursor.closed)

    def test_scoped_claim_rejects_cross_tenant_returned_row(self):
        rows = [
            (
                "mbe_" + ("a" * 22),
                "wsp_" + ("z" * 22),
                self._scope().owner_user_id,
                self._scope().mailbox_id,
                2,
                "mbm_" + ("c" * 22),
                4,
                "message_changed",
                1,
                "claim-token",
            )
        ]
        cursor = _ScopedClaimCursor(rows)
        connection = _ScopedClaimConnection(cursor)
        adapter = repository.PostgreSQLMailboxRepository(lambda: connection)

        with self.assertRaises(RuntimeError):
            adapter.claim_outbox_batch_for_mailbox(
                self._scope(),
                limit=20,
                now_millis=1_790_250_000_000,
                lease_millis=60_000,
            )
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)


class PostgreSQLMailboxAdapterTests(unittest.TestCase):
    def test_module_has_no_runtime_configuration_or_network_boundary(self):
        source = _ADAPTER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertNotIn("os", imported)
        self.assertNotIn("socket", imported)
        self.assertNotIn("urllib", imported)
        for forbidden in (
            "DATABASE_URL",
            "os.environ",
            "psycopg.connect(",
            "requests.",
            "httpx.",
        ):
            self.assertNotIn(forbidden, source)

    def test_reader_public_surface_exposes_no_write_methods(self):
        reader = repository.PostgreSQLMailboxReaderRepository
        for forbidden in (
            "initialize_current_state",
            "commit_provider_delta",
            "claim_outbox_batch",
            "mark_outbox_processed",
            "mark_outbox_retry",
        ):
            self.assertFalse(hasattr(reader, forbidden))

    def test_read_sql_reproves_current_mailbox_authority(self):
        for sql in (
            repository._SELECT_CURRENT_STATE_SQL,
            repository._SELECT_CURSOR_SQL,
            repository._LIST_MESSAGES_SQL,
            repository._SELECT_ACTIVE_MESSAGE_INVENTORY_SQL,
            repository._SELECT_MESSAGES_BY_PROVIDER_IDS_SQL,
            repository._SELECT_BODY_SQL,
        ):
            normalized = " ".join(sql.casefold().split())
            self.assertIn("provider_account_identity = %s", normalized)
            self.assertIn("is_current = true", normalized)

    def test_outbox_resolution_sql_is_generation_exact_and_current_only(self):
        message_sql = " ".join(
            repository._SELECT_OUTBOX_MESSAGE_SQL.casefold().split()
        )
        scope_sql = " ".join(
            repository._SELECT_OUTBOX_SCOPE_CURRENT_SQL.casefold().split()
        )
        for required in (
            "workspace_id = %s",
            "owner_user_id = %s",
            "mailbox_id = %s",
            "source_generation = %s",
            "is_current = true",
        ):
            self.assertIn(required, message_sql)
            self.assertIn(required, scope_sql)
        self.assertIn("message_id = %s", message_sql)
        self.assertIn("provider_account_identity", message_sql)

    def test_current_scope_lookup_is_exact_and_current_only(self):
        normalized = " ".join(
            repository._SELECT_CURRENT_STATE_SQL.casefold().split()
        )
        for required in (
            "workspace_id = %s",
            "owner_user_id = %s",
            "mailbox_id = %s",
            "provider = %s",
            "provider_account_identity = %s",
            "is_current = true",
        ):
            self.assertIn(required, normalized)

    def test_mutation_sql_uses_cas_and_exact_provider_identity(self):
        for sql in (
            repository._UPDATE_MESSAGE_SQL,
            repository._TOMBSTONE_MESSAGE_SQL,
        ):
            normalized = " ".join(sql.casefold().split())
            self.assertIn("row_version = %s", normalized)
            self.assertIn("provider_message_id = %s", normalized)
            self.assertIn("provider_folder_digest = %s", normalized)
            self.assertIn("imap_uid_validity = %s", normalized)
            self.assertIn("imap_uid = %s", normalized)

    def test_outbox_claims_are_skip_locked_unique_and_expiring(self):
        for raw in (
            repository._CLAIM_OUTBOX_SQL,
            repository._CLAIM_OUTBOX_FOR_MAILBOX_SQL,
        ):
            claim = " ".join(raw.casefold().split())
            self.assertIn("for update skip locked", claim)
            self.assertIn("gen_random_uuid()", claim)
            self.assertIn("claim_expires_at", claim)
        scoped = " ".join(
            repository._CLAIM_OUTBOX_FOR_MAILBOX_SQL.casefold().split()
        )
        for required in (
            "workspace_id = %s",
            "owner_user_id = %s",
            "mailbox_id = %s",
        ):
            self.assertIn(required, scoped)
        for sql in (
            repository._MARK_OUTBOX_PROCESSED_SQL,
            repository._MARK_OUTBOX_RETRY_SQL,
        ):
            self.assertIn(
                "claim_expires_at > %s",
                " ".join(sql.casefold().split()),
            )

    def test_fixed_sql_placeholder_inventory(self):
        expected = {
            "_SELECT_CURRENT_STATE_SQL": 5,
            "_INSERT_INITIAL_STATE_SQL": 7,
            "_SELECT_CURSOR_SQL": 7,
            "_LIST_MESSAGES_SQL": 9,
            "_SELECT_ACTIVE_MESSAGE_INVENTORY_SQL": 7,
            "_SELECT_MESSAGES_BY_PROVIDER_IDS_SQL": 7,
            "_SELECT_OUTBOX_SCOPE_CURRENT_SQL": 4,
            "_SELECT_OUTBOX_MESSAGE_SQL": 5,
            "_SELECT_BODY_SQL": 7,
            "_LOCK_STATE_SQL": 4,
            "_LOCK_CURSOR_SQL": 6,
            "_INSERT_CURSOR_SQL": 17,
            "_UPDATE_CURSOR_SQL": 17,
            "_UPDATE_STATE_SQL": 10,
            "_INSERT_MESSAGE_SQL": 29,
            "_UPDATE_MESSAGE_SQL": 31,
            "_TOMBSTONE_MESSAGE_SQL": 13,
            "_INSERT_OUTBOX_SQL": 9,
            "_CLAIM_OUTBOX_SQL": 4,
            "_CLAIM_OUTBOX_FOR_MAILBOX_SQL": 7,
            "_MARK_OUTBOX_PROCESSED_SQL": 4,
            "_MARK_OUTBOX_RETRY_SQL": 5,
        }
        self.assertEqual(
            {
                name: getattr(repository, name).count("%s")
                for name in expected
            },
            expected,
        )

    def test_outbox_storage_scope_has_no_provider_authority(self):
        fields = set(OutboxStorageScope.__dataclass_fields__)
        self.assertEqual(
            fields,
            {"workspace_id", "owner_user_id", "mailbox_id", "source_generation"},
        )


if __name__ == "__main__":
    unittest.main()
