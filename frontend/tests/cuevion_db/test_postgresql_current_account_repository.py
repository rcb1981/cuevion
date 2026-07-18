"""Scripted-fake tests for the inactive current-account PostgreSQL reader."""

import ast
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import uuid
import unittest

import psycopg

from api.auth import models
from cuevion_auth import current_account_repository_contract as contract
from cuevion_db import postgresql_current_account_repository as repository


_FRONTEND = Path(__file__).resolve().parents[2]
_SOURCE = _FRONTEND / "cuevion_db" / "postgresql_current_account_repository.py"


def _b64(octet: int) -> str:
    return base64.urlsafe_b64encode(bytes((octet,)) * 16).rstrip(b"=").decode(
        "ascii"
    )


USER_ID = "usr_" + _b64(1)
OTHER_USER_ID = "usr_" + _b64(2)
EMAIL_ID = "vem_" + _b64(3)
OTHER_EMAIL_ID = "vem_" + _b64(4)
IDENTITY_ID = "aid_" + _b64(5)
WORKSPACE_ID = "wsp_" + _b64(6)
OTHER_WORKSPACE_ID = "wsp_" + _b64(7)
EMAIL = "reader@example.test"
ISSUER = "https://identity.example.test/tenant"
SUBJECT = "Opaque-Subject-A"
SENSITIVE_MARKERS = (
    USER_ID,
    OTHER_USER_ID,
    EMAIL_ID,
    OTHER_EMAIL_ID,
    IDENTITY_ID,
    WORKSPACE_ID,
    OTHER_WORKSPACE_ID,
    EMAIL,
    ISSUER,
    SUBJECT,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_NO_RESULT = object()
_USER_WIDTH = 9
_EMAIL_WIDTH = 10
_IDENTITY_WIDTH = 11
_WORKSPACE_WIDTH = 7
_MEMBERSHIP_WIDTH = 8
_IDENTITY_ROW_WIDTH = 45
_USER_ROW_WIDTH = 34
_APPROVED_RELATIONS = frozenset(
    {
        "cuevion_account.users",
        "cuevion_account.verified_emails",
        "cuevion_account.authentication_identities",
        "cuevion_account.workspaces",
        "cuevion_account.workspace_memberships",
    }
)


def _dt(seconds: int) -> datetime:
    return _EPOCH + timedelta(seconds=seconds)


def _replace(row: tuple[object, ...], index: int, value: object) -> tuple[object, ...]:
    values = list(row)
    values[index] = value
    return tuple(values)


def _absent(width: int) -> tuple[None, ...]:
    return (None,) * width


def _user_segment(
    *,
    user_id: object = USER_ID,
    status: object = "active",
    primary_email_id: object = EMAIL_ID,
    display_name: object = "Current Reader",
    security_epoch: object = 3,
    schema_version: object = 1,
    created_at: object = _dt(1),
    updated_at: object = _dt(8),
    row_version: object = 4,
) -> tuple[object, ...]:
    return (
        schema_version,
        user_id,
        status,
        primary_email_id,
        display_name,
        security_epoch,
        created_at,
        updated_at,
        row_version,
    )


def _email_segment(
    *,
    email_id: object = EMAIL_ID,
    user_id: object = USER_ID,
    canonical_email: object = EMAIL,
    status: object = "verified",
    verification_source: object = "oidc",
    schema_version: object = 1,
    created_at: object = _dt(1),
    verified_at: object = _dt(2),
    retired_at: object = None,
    row_version: object = 2,
) -> tuple[object, ...]:
    return (
        schema_version,
        email_id,
        user_id,
        canonical_email,
        status,
        verification_source,
        created_at,
        verified_at,
        retired_at,
        row_version,
    )


def _identity_segment(
    *,
    identity_id: object = IDENTITY_ID,
    user_id: object = USER_ID,
    issuer: object = ISSUER,
    subject: object = SUBJECT,
    method: object = "oidc",
    status: object = "active",
    verified_email_id: object = EMAIL_ID,
    schema_version: object = 1,
    created_at: object = _dt(2),
    last_used_at: object = _dt(7),
    row_version: object = 5,
) -> tuple[object, ...]:
    return (
        schema_version,
        identity_id,
        user_id,
        issuer,
        subject,
        method,
        status,
        verified_email_id,
        created_at,
        last_used_at,
        row_version,
    )


def _workspace_segment(
    *,
    workspace_id: object = WORKSPACE_ID,
    status: object = "active",
    created_by_user_id: object = USER_ID,
    schema_version: object = 1,
    created_at: object = _dt(1),
    updated_at: object = _dt(8),
    row_version: object = 6,
) -> tuple[object, ...]:
    return (
        schema_version,
        workspace_id,
        status,
        created_by_user_id,
        created_at,
        updated_at,
        row_version,
    )


def _membership_segment(
    *,
    workspace_id: object = WORKSPACE_ID,
    user_id: object = USER_ID,
    role: object = "member",
    status: object = "active",
    schema_version: object = 1,
    created_at: object = _dt(2),
    updated_at: object = _dt(8),
    row_version: object = 7,
) -> tuple[object, ...]:
    return (
        schema_version,
        workspace_id,
        user_id,
        role,
        status,
        created_at,
        updated_at,
        row_version,
    )


def _identity_row(
    *,
    user: tuple[object, ...] | None = None,
    email: tuple[object, ...] | None = None,
    identity: tuple[object, ...] | None = None,
    workspace: tuple[object, ...] | None = None,
    membership: tuple[object, ...] | None = None,
) -> tuple[object, ...]:
    row = (
        (user if user is not None else _user_segment())
        + (email if email is not None else _email_segment())
        + (identity if identity is not None else _identity_segment())
        + (workspace if workspace is not None else _workspace_segment())
        + (membership if membership is not None else _membership_segment())
    )
    if len(row) != _IDENTITY_ROW_WIDTH:
        raise AssertionError("identity fixture width changed")
    return row


def _user_row(
    *,
    user: tuple[object, ...] | None = None,
    email: tuple[object, ...] | None = None,
    workspace: tuple[object, ...] | None = None,
    membership: tuple[object, ...] | None = None,
) -> tuple[object, ...]:
    row = (
        (user if user is not None else _user_segment())
        + (email if email is not None else _email_segment())
        + (workspace if workspace is not None else _workspace_segment())
        + (membership if membership is not None else _membership_segment())
    )
    if len(row) != _USER_ROW_WIDTH:
        raise AssertionError("user fixture width changed")
    return row


def _identity_absent_row() -> tuple[None, ...]:
    return _absent(_IDENTITY_ROW_WIDTH)


def _user_absent_row() -> tuple[None, ...]:
    return _absent(_USER_ROW_WIDTH)


def _workspace_absent_identity_row() -> tuple[object, ...]:
    return (
        _user_segment()
        + _email_segment()
        + _identity_segment()
        + _absent(_WORKSPACE_WIDTH)
        + _absent(_MEMBERSHIP_WIDTH)
    )


def _workspace_absent_user_row() -> tuple[object, ...]:
    return (
        _user_segment()
        + _email_segment()
        + _absent(_WORKSPACE_WIDTH)
        + _absent(_MEMBERSHIP_WIDTH)
    )


class ScriptedStep:
    __slots__ = (
        "key",
        "parameters",
        "rows",
        "execute_failure",
        "fetch_failure",
    )

    def __init__(
        self,
        key: str,
        parameters: tuple[object, ...] | None,
        rows: object = _NO_RESULT,
        *,
        execute_failure: BaseException | None = None,
        fetch_failure: BaseException | None = None,
    ) -> None:
        self.key = key
        self.parameters = parameters
        self.rows = rows
        self.execute_failure = execute_failure
        self.fetch_failure = fetch_failure


def _statement_key(sql: str) -> str:
    if type(sql) is not str:
        raise AssertionError("SQL must be an exact built-in string")
    mapping = {
        repository._SET_TRANSACTION_SQL: "set_transaction",
        repository._SELECT_CURRENT_ACCOUNT_BY_IDENTITY_SQL: "identity",
        repository._SELECT_CURRENT_ACCOUNT_BY_USER_SQL: "user",
    }
    try:
        return mapping[sql]
    except KeyError:
        raise AssertionError("unscripted SQL") from None


class ScriptedCursor:
    def __init__(self, connection: "ScriptedConnection") -> None:
        self.connection = connection
        self.closed = False
        self.pending: ScriptedStep | None = None

    def execute(
        self, sql: str, parameters: tuple[object, ...] | None = None
    ) -> None:
        if self.closed:
            raise AssertionError("execute on closed cursor")
        if type(sql) is not str:
            raise AssertionError("SQL must be an exact built-in string")
        if parameters is not None and type(parameters) is not tuple:
            raise AssertionError("parameters must be an exact tuple")
        expected_parameter_count = 0 if parameters is None else len(parameters)
        if sql.count("%s") != expected_parameter_count:
            raise AssertionError("SQL parameter count mismatch")
        if self.pending is not None:
            raise AssertionError("result was not fetched before next execute")
        key = _statement_key(sql)
        if not self.connection.script:
            raise AssertionError("unexpected SQL call")
        step = self.connection.script.pop(0)
        if step.key != key or step.parameters != parameters:
            raise AssertionError("unexpected SQL or parameters")
        self.connection.calls.append((key, sql, parameters))
        if step.execute_failure is not None:
            raise step.execute_failure
        if step.rows is not _NO_RESULT:
            self.pending = step

    def fetchall(self) -> list[tuple[object, ...]]:
        if self.closed:
            raise AssertionError("fetch on closed cursor")
        if self.pending is None:
            raise AssertionError("fetchall without a result")
        step = self.pending
        self.pending = None
        self.connection.fetchall_count += 1
        if step.fetch_failure is not None:
            raise step.fetch_failure
        return step.rows  # type: ignore[return-value]

    def close(self) -> None:
        if self.closed:
            raise AssertionError("cursor closed twice")
        self.closed = True
        self.connection.cursor_close_count += 1
        self.connection.cleanup_order.append("cursor.close")
        if self.connection.cursor_close_failure is not None:
            raise self.connection.cursor_close_failure


class ScriptedConnectionInfo:
    __slots__ = ("transaction_status",)

    def __init__(self, transaction_status: object) -> None:
        self.transaction_status = transaction_status


class ScriptedConnection:
    def __init__(
        self,
        script: list[ScriptedStep],
        *,
        autocommit: object = False,
        transaction_status: object = psycopg.pq.TransactionStatus.IDLE,
        cursor_failure: BaseException | None = None,
        cursor_close_failure: BaseException | None = None,
        rollback_failure: BaseException | None = None,
        close_failure: BaseException | None = None,
    ) -> None:
        self.autocommit = autocommit
        self.info = ScriptedConnectionInfo(transaction_status)
        self.script = list(script)
        self.cursor_failure = cursor_failure
        self.cursor_close_failure = cursor_close_failure
        self.rollback_failure = rollback_failure
        self.close_failure = close_failure
        self.calls: list[tuple[str, str, tuple[object, ...] | None]] = []
        self.cursors: list[ScriptedCursor] = []
        self.fetchall_count = 0
        self.cursor_close_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.commit_count = 0
        self.cleanup_order: list[str] = []

    def cursor(self) -> ScriptedCursor:
        if self.cursor_failure is not None:
            raise self.cursor_failure
        cursor = ScriptedCursor(self)
        self.cursors.append(cursor)
        return cursor

    def commit(self) -> None:
        self.commit_count += 1
        raise AssertionError("COMMIT is forbidden")

    def rollback(self) -> None:
        self.rollback_count += 1
        self.cleanup_order.append("rollback")
        if self.rollback_count > 1:
            raise AssertionError("rollback attempted more than once")
        if self.rollback_failure is not None:
            raise self.rollback_failure

    def close(self) -> None:
        self.close_count += 1
        self.cleanup_order.append("connection.close")
        if self.close_count > 1:
            raise AssertionError("connection closed more than once")
        if self.close_failure is not None:
            raise self.close_failure

    def assert_cleaned(self, *, cursor_expected: bool = True) -> None:
        if self.script:
            raise AssertionError("script was not exhausted")
        if cursor_expected:
            if len(self.cursors) != 1 or self.cursor_close_count != 1:
                raise AssertionError("cursor was not closed exactly once")
        elif self.cursors or self.cursor_close_count:
            raise AssertionError("unexpected cursor")
        if self.rollback_count != 1:
            raise AssertionError("rollback was not attempted exactly once")
        if self.close_count != 1:
            raise AssertionError("connection was not closed exactly once")
        if self.commit_count != 0:
            raise AssertionError("COMMIT was attempted")


class ConnectionFactory:
    def __init__(self, *values: object) -> None:
        self.values = list(values)
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        if not self.values:
            raise AssertionError("unexpected connection request")
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def _identity_steps(
    rows: object,
    *,
    set_failure: BaseException | None = None,
    query_failure: BaseException | None = None,
    fetch_failure: BaseException | None = None,
    issuer: str = ISSUER,
    subject: str = SUBJECT,
    workspace_id: str = WORKSPACE_ID,
) -> list[ScriptedStep]:
    steps = [
        ScriptedStep(
            "set_transaction", None, execute_failure=set_failure
        )
    ]
    if set_failure is None:
        steps.append(
            ScriptedStep(
                "identity",
                (issuer, subject, workspace_id),
                rows,
                execute_failure=query_failure,
                fetch_failure=fetch_failure,
            )
        )
    return steps


def _user_steps(
    rows: object,
    *,
    set_failure: BaseException | None = None,
    query_failure: BaseException | None = None,
    fetch_failure: BaseException | None = None,
    user_id: str = USER_ID,
    workspace_id: str = WORKSPACE_ID,
) -> list[ScriptedStep]:
    steps = [
        ScriptedStep(
            "set_transaction", None, execute_failure=set_failure
        )
    ]
    if set_failure is None:
        steps.append(
            ScriptedStep(
                "user",
                (user_id, workspace_id),
                rows,
                execute_failure=query_failure,
                fetch_failure=fetch_failure,
            )
        )
    return steps


def _new_repository(factory: ConnectionFactory) -> repository.PostgreSQLCurrentAccountRepository:
    return repository.PostgreSQLCurrentAccountRepository(factory)


def _identity_key(
    *, issuer: str = ISSUER, subject: str = SUBJECT
) -> contract.AuthenticationIdentityLookupKey:
    return contract.AuthenticationIdentityLookupKey(issuer=issuer, subject=subject)


class PostgreSQLCurrentAccountRepositoryTests(unittest.TestCase):
    def assert_result(
        self,
        result: object,
        outcome: contract.CurrentAccountReadOutcome,
        *,
        identity_operation: bool,
    ) -> None:
        expected_type = (
            contract.CurrentAccountAuthorityResult
            if identity_operation
            else contract.CurrentAccountByUserAuthorityResult
        )
        self.assertIs(type(result), expected_type)
        self.assertIs(result.outcome, outcome)  # type: ignore[attr-defined]
        if outcome is contract.CurrentAccountReadOutcome.FOUND:
            expected_authority = (
                contract.CurrentAccountAuthority
                if identity_operation
                else contract.CurrentAccountByUserAuthority
            )
            self.assertIs(type(result.authority), expected_authority)  # type: ignore[attr-defined]
        else:
            self.assertIsNone(result.authority)  # type: ignore[attr-defined]

    def assert_value_free(self, value: object) -> None:
        rendered = (repr(value), str(value))
        for marker in SENSITIVE_MARKERS:
            for surface in rendered:
                self.assertNotIn(marker, surface)

    def assert_connection_cleaned(
        self, connection: ScriptedConnection, *, cursor_expected: bool = True
    ) -> None:
        connection.assert_cleaned(cursor_expected=cursor_expected)
        expected_order = (
            ["cursor.close", "rollback", "connection.close"]
            if cursor_expected
            else ["rollback", "connection.close"]
        )
        self.assertEqual(connection.cleanup_order, expected_order)

    def call_identity(
        self,
        rows: object,
        **connection_keywords: object,
    ) -> tuple[object, ScriptedConnection, ConnectionFactory]:
        connection = ScriptedConnection(
            _identity_steps(rows), **connection_keywords
        )
        factory = ConnectionFactory(connection)
        result = _new_repository(factory).resolve_current_account_by_identity(
            _identity_key(), WORKSPACE_ID
        )
        return result, connection, factory

    def call_user(
        self,
        rows: object,
        **connection_keywords: object,
    ) -> tuple[object, ScriptedConnection, ConnectionFactory]:
        connection = ScriptedConnection(_user_steps(rows), **connection_keywords)
        factory = ConnectionFactory(connection)
        result = _new_repository(factory).read_current_account_by_user(
            USER_ID, WORKSPACE_ID
        )
        return result, connection, factory

    def test_construction_is_inactive_and_invalid_inputs_fail_before_io(self):
        factory = ConnectionFactory()
        instance = _new_repository(factory)
        self.assertIs(type(instance), repository.PostgreSQLCurrentAccountRepository)
        self.assertEqual(factory.calls, 0)

        invalid_calls = (
            lambda: instance.resolve_current_account_by_identity(  # type: ignore[arg-type]
                object(), WORKSPACE_ID
            ),
            lambda: instance.resolve_current_account_by_identity(
                _identity_key(), "private-workspace-marker"
            ),
            lambda: instance.read_current_account_by_user(
                "private-user-marker", WORKSPACE_ID
            ),
            lambda: instance.read_current_account_by_user(
                USER_ID, "private-workspace-marker"
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(
                    contract.CurrentAccountRepositoryContractValidationError
                ) as caught:
                    call()
                self.assertEqual(caught.exception.args, ())
                self.assertIsNone(caught.exception.__cause__)
                self.assert_value_free(caught.exception)
                self.assertNotIn("private-user-marker", repr(caught.exception))
                self.assertNotIn("private-user-marker", str(caught.exception))
                self.assertNotIn(
                    "private-workspace-marker", repr(caught.exception)
                )
                self.assertNotIn(
                    "private-workspace-marker", str(caught.exception)
                )
        self.assertEqual(factory.calls, 0)

    def test_successful_identity_read_decodes_complete_authority(self):
        result, connection, factory = self.call_identity([_identity_row()])
        self.assert_result(
            result,
            contract.CurrentAccountReadOutcome.FOUND,
            identity_operation=True,
        )
        authority = result.authority  # type: ignore[attr-defined]
        self.assertIs(type(authority.user), models.CuevionUser)
        self.assertIs(type(authority.primary_verified_email), models.VerifiedEmail)
        self.assertIs(
            type(authority.authentication_identity),
            models.AuthenticationIdentity,
        )
        self.assertIs(type(authority.workspace), models.Workspace)
        self.assertIs(
            type(authority.workspace_membership), models.WorkspaceMembership
        )
        self.assertEqual(authority.user.user_id, USER_ID)
        self.assertEqual(authority.user.security_epoch, 3)
        self.assertEqual(authority.user.row_version, 4)
        self.assertEqual(authority.authentication_identity.issuer, ISSUER)
        self.assertEqual(authority.authentication_identity.subject, SUBJECT)
        self.assertEqual(authority.workspace.workspace_id, WORKSPACE_ID)
        self.assertEqual(factory.calls, 1)
        self.assertEqual(connection.fetchall_count, 1)
        self.assertEqual(
            [key for key, _sql, _parameters in connection.calls],
            ["set_transaction", "identity"],
        )
        self.assertEqual(
            connection.calls[1][2], (ISSUER, SUBJECT, WORKSPACE_ID)
        )
        self.assert_connection_cleaned(connection)

    def test_identity_inputs_are_preserved_without_normalization(self):
        exact_issuer = "HTTPS://Identity.Example.test/Tenant-A"
        exact_subject = "Case-Sensitive-Opaque-Subject"
        row = _identity_row(
            identity=_identity_segment(
                issuer=exact_issuer,
                subject=exact_subject,
            )
        )
        connection = ScriptedConnection(
            _identity_steps(
                [row], issuer=exact_issuer, subject=exact_subject
            )
        )
        result = _new_repository(
            ConnectionFactory(connection)
        ).resolve_current_account_by_identity(
            _identity_key(issuer=exact_issuer, subject=exact_subject),
            WORKSPACE_ID,
        )
        self.assert_result(
            result,
            contract.CurrentAccountReadOutcome.FOUND,
            identity_operation=True,
        )
        self.assertEqual(
            connection.calls[1][2],
            (exact_issuer, exact_subject, WORKSPACE_ID),
        )
        self.assertEqual(
            result.authority.authentication_identity.issuer,  # type: ignore[attr-defined]
            exact_issuer,
        )
        self.assertEqual(
            result.authority.authentication_identity.subject,  # type: ignore[attr-defined]
            exact_subject,
        )
        self.assert_connection_cleaned(connection)

    def test_identity_with_null_email_link_is_authorized(self):
        row = _identity_row(
            identity=_identity_segment(verified_email_id=None)
        )
        result, connection, _factory = self.call_identity([row])
        self.assert_result(
            result,
            contract.CurrentAccountReadOutcome.FOUND,
            identity_operation=True,
        )
        self.assertIsNone(
            result.authority.authentication_identity.verified_email_id  # type: ignore[attr-defined]
        )
        self.assert_connection_cleaned(connection)

    def test_successful_user_read_decodes_complete_authority(self):
        result, connection, factory = self.call_user([_user_row()])
        self.assert_result(
            result,
            contract.CurrentAccountReadOutcome.FOUND,
            identity_operation=False,
        )
        authority = result.authority  # type: ignore[attr-defined]
        self.assertIs(type(authority.user), models.CuevionUser)
        self.assertIs(type(authority.primary_verified_email), models.VerifiedEmail)
        self.assertIs(type(authority.workspace), models.Workspace)
        self.assertIs(
            type(authority.workspace_membership), models.WorkspaceMembership
        )
        self.assertEqual(authority.user.security_epoch, 3)
        self.assertEqual(authority.workspace_membership.row_version, 7)
        self.assertEqual(factory.calls, 1)
        self.assertEqual(connection.calls[1][2], (USER_ID, WORKSPACE_ID))
        self.assert_connection_cleaned(connection)

    def test_all_active_roles_and_non_owner_creator_provenance_are_valid(self):
        for role in ("owner", "admin", "member"):
            for identity_operation in (True, False):
                with self.subTest(role=role, identity_operation=identity_operation):
                    workspace = _workspace_segment(
                        created_by_user_id=OTHER_USER_ID
                    )
                    membership = _membership_segment(role=role)
                    if identity_operation:
                        result, connection, _factory = self.call_identity(
                            [
                                _identity_row(
                                    workspace=workspace,
                                    membership=membership,
                                )
                            ]
                        )
                    else:
                        result, connection, _factory = self.call_user(
                            [
                                _user_row(
                                    workspace=workspace,
                                    membership=membership,
                                )
                            ]
                        )
                    self.assert_result(
                        result,
                        contract.CurrentAccountReadOutcome.FOUND,
                        identity_operation=identity_operation,
                    )
                    self.assertEqual(
                        result.authority.workspace.created_by_user_id,  # type: ignore[attr-defined]
                        OTHER_USER_ID,
                    )
                    self.assertEqual(
                        result.authority.workspace_membership.role.value,  # type: ignore[attr-defined]
                        role,
                    )
                    self.assert_connection_cleaned(connection)

    def test_identity_absence_and_inactive_identity_are_not_authorized(self):
        cases = (
            ("absent", _identity_absent_row()),
            (
                "disabled",
                _identity_row(identity=_identity_segment(status="disabled")),
            ),
            (
                "revoked",
                _identity_row(identity=_identity_segment(status="revoked")),
            ),
        )
        for name, row in cases:
            with self.subTest(name=name):
                result, connection, _factory = self.call_identity([row])
                self.assert_result(
                    result,
                    contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
                    identity_operation=True,
                )
                self.assert_value_free(result)
                self.assert_connection_cleaned(connection)

    def test_submitted_user_absence_is_not_authorized(self):
        result, connection, _factory = self.call_user([_user_absent_row()])
        self.assert_result(
            result,
            contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
            identity_operation=False,
        )
        self.assert_value_free(result)
        self.assert_connection_cleaned(connection)

    def test_inactive_user_and_primary_email_states_are_not_authorized(self):
        cases = (
            ("user-suspended", _user_segment(status="suspended"), _email_segment()),
            ("user-disabled", _user_segment(status="disabled"), _email_segment()),
            (
                "email-pending",
                _user_segment(),
                _email_segment(
                    status="pending", verified_at=None, retired_at=None
                ),
            ),
            (
                "email-retired",
                _user_segment(),
                _email_segment(
                    status="retired", verified_at=_dt(2), retired_at=_dt(7)
                ),
            ),
        )
        for name, user, email in cases:
            for identity_operation in (True, False):
                with self.subTest(name=name, identity_operation=identity_operation):
                    row = (
                        _identity_row(user=user, email=email)
                        if identity_operation
                        else _user_row(user=user, email=email)
                    )
                    if identity_operation:
                        result, connection, _factory = self.call_identity([row])
                    else:
                        result, connection, _factory = self.call_user([row])
                    self.assert_result(
                        result,
                        contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
                        identity_operation=identity_operation,
                    )
                    self.assert_value_free(result)
                    self.assert_connection_cleaned(connection)

    def test_identity_linked_to_non_primary_email_is_not_authorized(self):
        row = _identity_row(
            identity=_identity_segment(verified_email_id=OTHER_EMAIL_ID)
        )
        result, connection, _factory = self.call_identity([row])
        self.assert_result(
            result,
            contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
            identity_operation=True,
        )
        self.assert_value_free(result)
        self.assert_connection_cleaned(connection)

    def test_workspace_absence_and_inactive_workspace_are_not_authorized(self):
        for identity_operation in (True, False):
            cases = (
                (
                    "absent",
                    _workspace_absent_identity_row()
                    if identity_operation
                    else _workspace_absent_user_row(),
                ),
                (
                    "suspended",
                    _identity_row(workspace=_workspace_segment(status="suspended"))
                    if identity_operation
                    else _user_row(workspace=_workspace_segment(status="suspended")),
                ),
                (
                    "archived",
                    _identity_row(workspace=_workspace_segment(status="archived"))
                    if identity_operation
                    else _user_row(workspace=_workspace_segment(status="archived")),
                ),
            )
            for name, row in cases:
                with self.subTest(name=name, identity_operation=identity_operation):
                    if identity_operation:
                        result, connection, _factory = self.call_identity([row])
                    else:
                        result, connection, _factory = self.call_user([row])
                    self.assert_result(
                        result,
                        contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
                        identity_operation=identity_operation,
                    )
                    self.assert_value_free(result)
                    self.assert_connection_cleaned(connection)

    def test_membership_absence_and_inactive_membership_are_not_authorized(self):
        for identity_operation in (True, False):
            cases = (
                ("absent", _absent(_MEMBERSHIP_WIDTH)),
                ("suspended", _membership_segment(status="suspended")),
                ("removed", _membership_segment(status="removed")),
                (
                    "inactive-owner",
                    _membership_segment(role="owner", status="suspended"),
                ),
            )
            for name, membership in cases:
                with self.subTest(name=name, identity_operation=identity_operation):
                    row = (
                        _identity_row(membership=membership)
                        if identity_operation
                        else _user_row(membership=membership)
                    )
                    if identity_operation:
                        result, connection, _factory = self.call_identity([row])
                    else:
                        result, connection, _factory = self.call_user([row])
                    self.assert_result(
                        result,
                        contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
                        identity_operation=identity_operation,
                    )
                    self.assert_value_free(result)
                    self.assert_connection_cleaned(connection)

    def test_identity_present_with_missing_referenced_user_is_internal_error(self):
        row = (
            _absent(_USER_WIDTH)
            + _absent(_EMAIL_WIDTH)
            + _identity_segment()
            + _absent(_WORKSPACE_WIDTH)
            + _absent(_MEMBERSHIP_WIDTH)
        )
        result, connection, _factory = self.call_identity([row])
        self.assert_result(
            result,
            contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            identity_operation=True,
        )
        self.assert_value_free(result)
        self.assert_connection_cleaned(connection)

    def test_absent_identity_or_user_cannot_independently_return_workspace(self):
        identity_row = (
            _absent(_USER_WIDTH)
            + _absent(_EMAIL_WIDTH)
            + _absent(_IDENTITY_WIDTH)
            + _workspace_segment()
            + _absent(_MEMBERSHIP_WIDTH)
        )
        identity_result, identity_connection, _factory = self.call_identity(
            [identity_row]
        )
        self.assert_result(
            identity_result,
            contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            identity_operation=True,
        )
        self.assert_connection_cleaned(identity_connection)

        user_row = (
            _absent(_USER_WIDTH)
            + _absent(_EMAIL_WIDTH)
            + _workspace_segment()
            + _absent(_MEMBERSHIP_WIDTH)
        )
        user_result, user_connection, _factory = self.call_user([user_row])
        self.assert_result(
            user_result,
            contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            identity_operation=False,
        )
        self.assert_connection_cleaned(user_connection)

    def test_active_user_missing_primary_email_is_internal_error(self):
        rows = (
            _user_segment()
            + _absent(_EMAIL_WIDTH)
            + _identity_segment()
            + _workspace_segment()
            + _membership_segment(),
            _user_segment()
            + _absent(_EMAIL_WIDTH)
            + _workspace_segment()
            + _membership_segment(),
        )
        for identity_operation, row in zip((True, False), rows):
            with self.subTest(identity_operation=identity_operation):
                if identity_operation:
                    result, connection, _factory = self.call_identity([row])
                else:
                    result, connection, _factory = self.call_user([row])
                self.assert_result(
                    result,
                    contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
                    identity_operation=identity_operation,
                )
                self.assert_connection_cleaned(connection)

    def test_partial_segments_are_internal_error(self):
        partial_user = (1,) + _absent(_USER_WIDTH - 1)
        partial_email = (1,) + _absent(_EMAIL_WIDTH - 1)
        partial_identity = (1,) + _absent(_IDENTITY_WIDTH - 1)
        partial_workspace = (1,) + _absent(_WORKSPACE_WIDTH - 1)
        partial_membership = (1,) + _absent(_MEMBERSHIP_WIDTH - 1)
        cases = (
            (
                "user",
                partial_user
                + _absent(_EMAIL_WIDTH)
                + _identity_segment()
                + _absent(_WORKSPACE_WIDTH)
                + _absent(_MEMBERSHIP_WIDTH),
            ),
            (
                "email",
                _identity_row(email=partial_email),
            ),
            (
                "identity",
                _identity_row(identity=partial_identity),
            ),
            (
                "workspace",
                _identity_row(
                    workspace=partial_workspace,
                    membership=_absent(_MEMBERSHIP_WIDTH),
                ),
            ),
            (
                "membership",
                _identity_row(membership=partial_membership),
            ),
        )
        for name, row in cases:
            with self.subTest(name=name):
                result, connection, _factory = self.call_identity([row])
                self.assert_result(
                    result,
                    contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
                    identity_operation=True,
                )
                self.assert_connection_cleaned(connection)

    def test_result_container_and_row_protocol_are_strict(self):
        class TupleSubclass(tuple):
            pass

        malformed_results = (
            ("fetchall-tuple", (_identity_row(),)),
            ("zero-rows", []),
            ("multiple-rows", [_identity_row(), _identity_row()]),
            ("list-row", [list(_identity_row())]),
            ("mapping-row", [{"private": "row"}]),
            ("tuple-subclass", [TupleSubclass(_identity_row())]),
            ("short-row", [_identity_row()[:-1]]),
            ("long-row", [_identity_row() + (None,)]),
        )
        for name, rows in malformed_results:
            with self.subTest(name=name):
                result, connection, _factory = self.call_identity(rows)
                self.assert_result(
                    result,
                    contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
                    identity_operation=True,
                )
                self.assert_value_free(result)
                self.assert_connection_cleaned(connection)

        user_result, user_connection, _factory = self.call_user(
            [_user_row()[:-1]]
        )
        self.assert_result(
            user_result,
            contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            identity_operation=False,
        )
        self.assert_connection_cleaned(user_connection)

    def test_stored_scalar_types_are_exact(self):
        class TextSubclass(str):
            pass

        class IntSubclass(int):
            pass

        cases = (
            ("text-subclass", 22, TextSubclass(ISSUER)),
            ("int-subclass", 5, IntSubclass(3)),
            ("bool-as-int", 5, True),
            ("uuid-id", 1, uuid.UUID(int=0)),
        )
        for name, index, value in cases:
            with self.subTest(name=name):
                row = _replace(_identity_row(), index, value)
                result, connection, _factory = self.call_identity([row])
                self.assert_result(
                    result,
                    contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
                    identity_operation=True,
                )
                self.assert_connection_cleaned(connection)

    def test_database_datetimes_must_be_exact_whole_second_utc(self):
        class DatetimeSubclass(datetime):
            pass

        invalid_datetimes = (
            ("naive", datetime(1970, 1, 1)),
            (
                "non-utc",
                datetime(1970, 1, 1, tzinfo=timezone(timedelta(hours=1))),
            ),
            ("fractional", _dt(1).replace(microsecond=1)),
            ("before-epoch", datetime(1969, 12, 31, tzinfo=timezone.utc)),
            (
                "datetime-subclass",
                DatetimeSubclass(1970, 1, 1, tzinfo=timezone.utc),
            ),
        )
        for name, value in invalid_datetimes:
            with self.subTest(name=name):
                row = _replace(_identity_row(), 6, value)
                result, connection, _factory = self.call_identity([row])
                self.assert_result(
                    result,
                    contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
                    identity_operation=True,
                )
                self.assert_connection_cleaned(connection)

    def test_malformed_stored_values_are_internal_error(self):
        cases = (
            ("unexpected-null", 4, None),
            ("unknown-enum", 2, "future-user-state"),
            ("invalid-id", 1, "usr_not-canonical"),
            ("invalid-email", 12, "not-an-email"),
            ("schema-version", 0, 2),
            ("zero-row-version", 8, 0),
            ("negative-row-version", 8, -1),
            ("zero-security-epoch", 5, 0),
            ("negative-security-epoch", 5, -1),
        )
        for name, index, value in cases:
            with self.subTest(name=name):
                result, connection, _factory = self.call_identity(
                    [_replace(_identity_row(), index, value)]
                )
                self.assert_result(
                    result,
                    contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
                    identity_operation=True,
                )
                self.assert_connection_cleaned(connection)

        unknown_role = _identity_row(
            membership=_membership_segment(role="future-role")
        )
        result, connection, _factory = self.call_identity([unknown_role])
        self.assert_result(
            result,
            contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            identity_operation=True,
        )
        self.assert_connection_cleaned(connection)

    def test_invalid_lifecycle_ordering_is_internal_error(self):
        cases = (
            (
                "user-created-after-updated",
                _identity_row(
                    user=_user_segment(
                        created_at=_dt(9), updated_at=_dt(8)
                    )
                ),
            ),
            (
                "email-verified-before-created",
                _identity_row(
                    email=_email_segment(
                        created_at=_dt(2), verified_at=_dt(1)
                    )
                ),
            ),
            (
                "email-retired-before-verified",
                _identity_row(
                    email=_email_segment(
                        status="retired",
                        verified_at=_dt(2),
                        retired_at=_dt(1),
                    )
                ),
            ),
            (
                "identity-last-used-before-created",
                _identity_row(
                    identity=_identity_segment(
                        created_at=_dt(2), last_used_at=_dt(1)
                    )
                ),
            ),
            (
                "workspace-created-after-updated",
                _identity_row(
                    workspace=_workspace_segment(
                        created_at=_dt(9), updated_at=_dt(8)
                    )
                ),
            ),
            (
                "membership-created-after-updated",
                _identity_row(
                    membership=_membership_segment(
                        created_at=_dt(9), updated_at=_dt(8)
                    )
                ),
            ),
        )
        for name, row in cases:
            with self.subTest(name=name):
                result, connection, _factory = self.call_identity([row])
                self.assert_result(
                    result,
                    contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
                    identity_operation=True,
                )
                self.assert_connection_cleaned(connection)

    def test_relationship_mismatches_are_internal_error(self):
        cases = (
            (
                "user-primary-email",
                _identity_row(user=_user_segment(primary_email_id=OTHER_EMAIL_ID)),
            ),
            (
                "email-user",
                _identity_row(email=_email_segment(user_id=OTHER_USER_ID)),
            ),
            (
                "identity-user",
                _identity_row(identity=_identity_segment(user_id=OTHER_USER_ID)),
            ),
            (
                "workspace-id",
                _identity_row(
                    workspace=_workspace_segment(
                        workspace_id=OTHER_WORKSPACE_ID
                    )
                ),
            ),
            (
                "membership-user",
                _identity_row(
                    membership=_membership_segment(user_id=OTHER_USER_ID)
                ),
            ),
            (
                "membership-workspace",
                _identity_row(
                    membership=_membership_segment(
                        workspace_id=OTHER_WORKSPACE_ID
                    )
                ),
            ),
        )
        for name, row in cases:
            with self.subTest(name=name):
                result, connection, _factory = self.call_identity([row])
                self.assert_result(
                    result,
                    contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
                    identity_operation=True,
                )
                self.assert_connection_cleaned(connection)

    def test_structural_corruption_wins_over_inactive_denial(self):
        row = _identity_row(
            identity=_identity_segment(status="disabled"),
            workspace=_workspace_segment(workspace_id=OTHER_WORKSPACE_ID),
        )
        result, connection, _factory = self.call_identity([row])
        self.assert_result(
            result,
            contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            identity_operation=True,
        )
        self.assert_connection_cleaned(connection)

    def test_factory_and_cursor_acquisition_failures_are_classified(self):
        factory_cases = (
            (
                psycopg.OperationalError("private transport " + EMAIL),
                contract.CurrentAccountReadOutcome.UNAVAILABLE,
            ),
            (
                RuntimeError("private integration " + EMAIL),
                contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            ),
        )
        for failure, expected in factory_cases:
            with self.subTest(factory_failure=type(failure).__name__):
                factory = ConnectionFactory(failure)
                result = _new_repository(factory).read_current_account_by_user(
                    USER_ID, WORKSPACE_ID
                )
                self.assert_result(result, expected, identity_operation=False)
                self.assert_value_free(result)
                self.assertEqual(factory.calls, 1)

        cursor_cases = (
            (
                psycopg.OperationalError("private cursor transport"),
                contract.CurrentAccountReadOutcome.UNAVAILABLE,
            ),
            (
                RuntimeError("private cursor protocol"),
                contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            ),
        )
        for failure, expected in cursor_cases:
            with self.subTest(cursor_failure=type(failure).__name__):
                connection = ScriptedConnection([], cursor_failure=failure)
                result = _new_repository(
                    ConnectionFactory(connection)
                ).read_current_account_by_user(USER_ID, WORKSPACE_ID)
                self.assert_result(result, expected, identity_operation=False)
                self.assert_connection_cleaned(
                    connection, cursor_expected=False
                )

    def test_transaction_query_and_fetch_failures_are_classified(self):
        cases = (
            (
                "set-operational",
                {"set_failure": psycopg.OperationalError("private set transport")},
                contract.CurrentAccountReadOutcome.UNAVAILABLE,
            ),
            (
                "set-programming",
                {"set_failure": psycopg.ProgrammingError("private schema")},
                contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            ),
            (
                "query-operational",
                {"query_failure": psycopg.OperationalError("private query transport")},
                contract.CurrentAccountReadOutcome.UNAVAILABLE,
            ),
            (
                "query-programming",
                {"query_failure": psycopg.ProgrammingError("private permission")},
                contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            ),
            (
                "fetch-operational",
                {"fetch_failure": psycopg.OperationalError("private fetch transport")},
                contract.CurrentAccountReadOutcome.UNAVAILABLE,
            ),
            (
                "fetch-programming",
                {"fetch_failure": psycopg.ProgrammingError("private fetch protocol")},
                contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            ),
        )
        for name, failures, expected in cases:
            with self.subTest(name=name):
                steps = _user_steps([_user_row()], **failures)
                connection = ScriptedConnection(steps)
                result = _new_repository(
                    ConnectionFactory(connection)
                ).read_current_account_by_user(USER_ID, WORKSPACE_ID)
                self.assert_result(result, expected, identity_operation=False)
                self.assert_value_free(result)
                self.assert_connection_cleaned(connection)

    def test_serialization_failures_are_unavailable_without_retry(self):
        failures = (
            psycopg.errors.SerializationFailure("private serialization"),
            psycopg.errors.DeadlockDetected("private deadlock"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                connection = ScriptedConnection(
                    _identity_steps([_identity_row()], query_failure=failure)
                )
                factory = ConnectionFactory(connection)
                result = _new_repository(
                    factory
                ).resolve_current_account_by_identity(
                    _identity_key(), WORKSPACE_ID
                )
                self.assert_result(
                    result,
                    contract.CurrentAccountReadOutcome.UNAVAILABLE,
                    identity_operation=True,
                )
                self.assertEqual(factory.calls, 1)
                self.assert_connection_cleaned(connection)

    def test_cleanup_failures_override_success_and_cleanup_continues(self):
        cases = (
            (
                "rollback-operational",
                {
                    "rollback_failure": psycopg.OperationalError(
                        "private rollback transport"
                    )
                },
                contract.CurrentAccountReadOutcome.UNAVAILABLE,
            ),
            (
                "rollback-unexpected",
                {"rollback_failure": RuntimeError("private rollback protocol")},
                contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            ),
            (
                "cursor-close",
                {"cursor_close_failure": RuntimeError("private cursor close")},
                contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            ),
            (
                "connection-close",
                {"close_failure": RuntimeError("private connection close")},
                contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            ),
            (
                "connection-close-operational",
                {
                    "close_failure": psycopg.OperationalError(
                        "private connection close transport"
                    )
                },
                contract.CurrentAccountReadOutcome.UNAVAILABLE,
            ),
        )
        for name, keywords, expected in cases:
            with self.subTest(name=name):
                result, connection, _factory = self.call_user(
                    [_user_row()], **keywords
                )
                self.assert_result(result, expected, identity_operation=False)
                self.assert_value_free(result)
                self.assert_connection_cleaned(connection)

        connection = ScriptedConnection(
            _user_steps([_user_row()]),
            cursor_close_failure=RuntimeError("first cleanup failure"),
            rollback_failure=RuntimeError("second cleanup failure"),
            close_failure=RuntimeError("third cleanup failure"),
        )
        result = _new_repository(
            ConnectionFactory(connection)
        ).read_current_account_by_user(USER_ID, WORKSPACE_ID)
        self.assert_result(
            result,
            contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            identity_operation=False,
        )
        self.assert_connection_cleaned(connection)

    def test_body_internal_error_wins_over_operational_cleanup_failure(self):
        connection = ScriptedConnection(
            _user_steps(
                [_user_row()],
                query_failure=psycopg.ProgrammingError("private schema"),
            ),
            rollback_failure=psycopg.OperationalError(
                "private rollback transport"
            ),
        )
        result = _new_repository(
            ConnectionFactory(connection)
        ).read_current_account_by_user(USER_ID, WORKSPACE_ID)
        self.assert_result(
            result,
            contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            identity_operation=False,
        )
        self.assert_value_free(result)
        self.assert_connection_cleaned(connection)

    def test_unsafe_connection_state_fails_closed_without_authority_select(self):
        cases = (
            {"autocommit": True},
            {"transaction_status": psycopg.pq.TransactionStatus.INTRANS},
        )
        for keywords in cases:
            with self.subTest(keywords=keywords):
                connection = ScriptedConnection([], **keywords)
                result = _new_repository(
                    ConnectionFactory(connection)
                ).read_current_account_by_user(USER_ID, WORKSPACE_ID)
                self.assert_result(
                    result,
                    contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
                    identity_operation=False,
                )
                self.assertEqual(connection.calls, [])
                self.assert_connection_cleaned(
                    connection, cursor_expected=False
                )

    def test_baseexception_propagates_after_all_cleanup(self):
        class Fatal(BaseException):
            pass

        fatal = Fatal()
        connection = ScriptedConnection(
            _identity_steps([_identity_row()], query_failure=fatal)
        )
        with self.assertRaises(Fatal) as caught:
            _new_repository(
                ConnectionFactory(connection)
            ).resolve_current_account_by_identity(_identity_key(), WORKSPACE_ID)
        self.assertIs(caught.exception, fatal)
        self.assert_connection_cleaned(connection)

        factory_fatal = Fatal()
        with self.assertRaises(Fatal) as caught:
            _new_repository(
                ConnectionFactory(factory_fatal)
            ).read_current_account_by_user(USER_ID, WORKSPACE_ID)
        self.assertIs(caught.exception, factory_fatal)

        cleanup_fatal = Fatal()
        cleanup_connection = ScriptedConnection(
            _user_steps([_user_row()]),
            cursor_close_failure=cleanup_fatal,
        )
        with self.assertRaises(Fatal) as caught:
            _new_repository(
                ConnectionFactory(cleanup_connection)
            ).read_current_account_by_user(USER_ID, WORKSPACE_ID)
        self.assertIs(caught.exception, cleanup_fatal)
        self.assert_connection_cleaned(cleanup_connection)

        body_fatal = Fatal()
        later_cleanup_fatal = Fatal()
        precedence_connection = ScriptedConnection(
            _user_steps([_user_row()], query_failure=body_fatal),
            rollback_failure=later_cleanup_fatal,
        )
        with self.assertRaises(Fatal) as caught:
            _new_repository(
                ConnectionFactory(precedence_connection)
            ).read_current_account_by_user(USER_ID, WORKSPACE_ID)
        self.assertIs(caught.exception, body_fatal)
        self.assert_connection_cleaned(precedence_connection)

    def test_pre_and_post_mutation_snapshots_are_each_complete_single_reads(self):
        pre_connection = ScriptedConnection(_identity_steps([_identity_row()]))
        post_connection = ScriptedConnection(
            _identity_steps(
                [
                    _identity_row(
                        workspace=_workspace_segment(status="suspended")
                    )
                ]
            )
        )
        factory = ConnectionFactory(pre_connection, post_connection)
        instance = _new_repository(factory)

        before = instance.resolve_current_account_by_identity(
            _identity_key(), WORKSPACE_ID
        )
        after = instance.resolve_current_account_by_identity(
            _identity_key(), WORKSPACE_ID
        )

        self.assert_result(
            before,
            contract.CurrentAccountReadOutcome.FOUND,
            identity_operation=True,
        )
        self.assert_result(
            after,
            contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
            identity_operation=True,
        )
        self.assertEqual(factory.calls, 2)
        for connection in (pre_connection, post_connection):
            self.assertEqual(connection.fetchall_count, 1)
            self.assertEqual(
                [key for key, _sql, _parameters in connection.calls],
                ["set_transaction", "identity"],
            )
            self.assert_connection_cleaned(connection)


class PostgreSQLCurrentAccountRepositorySQLSecurityTests(unittest.TestCase):
    def test_sql_constants_are_exact_fixed_and_transaction_is_repeatable_read_only(self):
        sql_items = {
            name: value
            for name, value in vars(repository).items()
            if name.endswith("_SQL") and type(value) is str
        }
        self.assertEqual(
            set(sql_items),
            {
                "_SET_TRANSACTION_SQL",
                "_SELECT_CURRENT_ACCOUNT_BY_IDENTITY_SQL",
                "_SELECT_CURRENT_ACCOUNT_BY_USER_SQL",
            },
        )
        self.assertEqual(
            " ".join(repository._SET_TRANSACTION_SQL.split()).upper(),
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        )
        source_tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
        assignments = {
            target.id: node.value
            for node in ast.walk(source_tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id.endswith("_SQL")
        }
        self.assertEqual(set(assignments), set(sql_items))
        for value in assignments.values():
            is_literal = isinstance(value, ast.Constant) and isinstance(
                value.value, str
            )
            is_stripped_literal = (
                isinstance(value, ast.Call)
                and not value.args
                and not value.keywords
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "strip"
                and isinstance(value.func.value, ast.Constant)
                and isinstance(value.func.value.value, str)
            )
            self.assertTrue(is_literal or is_stripped_literal)

    def test_queries_have_one_values_anchor_one_select_and_only_current_tables(self):
        queries = (
            (
                repository._SELECT_CURRENT_ACCOUNT_BY_IDENTITY_SQL,
                3,
                _APPROVED_RELATIONS,
            ),
            (
                repository._SELECT_CURRENT_ACCOUNT_BY_USER_SQL,
                2,
                _APPROVED_RELATIONS
                - {"cuevion_account.authentication_identities"},
            ),
        )
        all_seen: set[str] = set()
        for sql, parameter_count, expected_relations in queries:
            with self.subTest(parameter_count=parameter_count):
                self.assertIs(type(sql), str)
                self.assertEqual(sql.count("%s"), parameter_count)
                self.assertNotIn(";", sql)
                self.assertNotIn("--", sql)
                self.assertNotIn("/*", sql)
                normalized = " ".join(sql.split()).casefold()
                self.assertEqual(len(re.findall(r"\bselect\b", normalized)), 1)
                self.assertEqual(len(re.findall(r"\bvalues\b", normalized)), 1)
                values_pattern = (
                    r"\bvalues\s*\(\s*%s"
                    + (r"\s*,\s*%s" * (parameter_count - 1))
                    + r"\s*\)"
                )
                self.assertRegex(normalized, values_pattern)
                self.assertRegex(normalized, r"^with\s+request\s*\(")
                self.assertIn(" from request ", normalized)
                function_like_tokens = set(
                    re.findall(
                        r"\b([a-z_][a-z0-9_]*)\s*\(", normalized
                    )
                )
                function_like_tokens.difference_update(
                    {"request", "values", "as"}
                )
                self.assertEqual(function_like_tokens, set())
                self.assertNotRegex(normalized, r"\bselect\s+\*")
                self.assertNotRegex(normalized, r"\border\s+by\b")
                self.assertNotRegex(normalized, r"\blimit\b")
                self.assertNotRegex(normalized, r"\bfor\s+(?:update|share)\b")
                select_clause = normalized.split("select", 1)[1].split(
                    "from request", 1
                )[0]
                self.assertNotIn("request.", select_clause)
                relations = set(
                    re.findall(
                        r"\b(?:from|join)\s+"
                        r"(cuevion_account[.][a-z_][a-z0-9_]*)\b",
                        normalized,
                    )
                )
                self.assertEqual(relations, set(expected_relations))
                all_seen.update(relations)
                self.assertEqual(
                    len(re.findall(r"\bleft\s+join\b", normalized)),
                    len(expected_relations),
                )
                forbidden = (
                    "initial_account_operations",
                    "security_events",
                    "nextval",
                    "setval",
                    "sequence",
                    "advisory",
                    "pg_advisory",
                    " insert ",
                    " update ",
                    " delete ",
                    " merge ",
                    " call ",
                    " create ",
                    " alter ",
                    " drop ",
                    " grant ",
                    " revoke ",
                    " truncate ",
                    " commit ",
                )
                padded = " " + normalized + " "
                for marker in forbidden:
                    self.assertNotIn(marker, padded)
        self.assertEqual(all_seen, set(_APPROVED_RELATIONS))

    def test_join_shape_uses_primary_email_and_causally_resolved_workspace(self):
        for sql in (
            repository._SELECT_CURRENT_ACCOUNT_BY_IDENTITY_SQL,
            repository._SELECT_CURRENT_ACCOUNT_BY_USER_SQL,
        ):
            normalized = " ".join(sql.split()).casefold()
            request_match = re.search(
                r"\bfrom\s+request\s+as\s+([a-z_][a-z0-9_]*)\b",
                normalized,
            )
            user_match = re.search(
                r"\bleft\s+join\s+cuevion_account[.]users\s+as\s+"
                r"([a-z_][a-z0-9_]*)\b",
                normalized,
            )
            workspace_match = re.search(
                r"\bleft\s+join\s+cuevion_account[.]workspaces\s+as\s+"
                r"([a-z_][a-z0-9_]*)\b",
                normalized,
            )
            self.assertIsNotNone(request_match)
            self.assertIsNotNone(user_match)
            self.assertIsNotNone(workspace_match)
            request_alias = request_match.group(1)  # type: ignore[union-attr]
            user_alias = user_match.group(1)  # type: ignore[union-attr]
            workspace_alias = workspace_match.group(1)  # type: ignore[union-attr]
            email_join = normalized.split(
                "left join cuevion_account.verified_emails", 1
            )[1].split("left join cuevion_account.workspaces", 1)[0]
            self.assertIn("primary_verified_email_id", email_join)
            self.assertGreaterEqual(email_join.count("user_id"), 2)
            workspace_join = normalized.split(
                "left join cuevion_account.workspaces", 1
            )[1].split(
                "left join cuevion_account.workspace_memberships", 1
            )[0]
            self.assertIn(f"{request_alias}.workspace_id", workspace_join)
            self.assertRegex(
                workspace_join,
                rf"\b{re.escape(user_alias)}[.]user_id\s+is\s+not\s+null\b",
            )
            membership_join = normalized.split(
                "left join cuevion_account.workspace_memberships", 1
            )[1]
            self.assertIn(f"{user_alias}.user_id", membership_join)
            self.assertIn(f"{workspace_alias}.workspace_id", membership_join)

    def test_runtime_calls_use_first_transaction_statement_and_one_aggregate_select(self):
        identity_connection = ScriptedConnection(
            _identity_steps([_identity_row()])
        )
        user_connection = ScriptedConnection(_user_steps([_user_row()]))
        instance = _new_repository(
            ConnectionFactory(identity_connection, user_connection)
        )
        identity_result = instance.resolve_current_account_by_identity(
            _identity_key(), WORKSPACE_ID
        )
        user_result = instance.read_current_account_by_user(
            USER_ID, WORKSPACE_ID
        )
        self.assertIs(
            identity_result.outcome, contract.CurrentAccountReadOutcome.FOUND
        )
        self.assertIs(user_result.outcome, contract.CurrentAccountReadOutcome.FOUND)
        self.assertEqual(
            identity_connection.calls,
            [
                ("set_transaction", repository._SET_TRANSACTION_SQL, None),
                (
                    "identity",
                    repository._SELECT_CURRENT_ACCOUNT_BY_IDENTITY_SQL,
                    (ISSUER, SUBJECT, WORKSPACE_ID),
                ),
            ],
        )
        self.assertEqual(
            user_connection.calls,
            [
                ("set_transaction", repository._SET_TRANSACTION_SQL, None),
                (
                    "user",
                    repository._SELECT_CURRENT_ACCOUNT_BY_USER_SQL,
                    (USER_ID, WORKSPACE_ID),
                ),
            ],
        )
        combined_sql = "\n".join(
            (
                repository._SET_TRANSACTION_SQL,
                repository._SELECT_CURRENT_ACCOUNT_BY_IDENTITY_SQL,
                repository._SELECT_CURRENT_ACCOUNT_BY_USER_SQL,
            )
        )
        for marker in SENSITIVE_MARKERS:
            self.assertNotIn(marker, combined_sql)
        identity_connection.assert_cleaned()
        user_connection.assert_cleaned()

    def test_source_has_no_activation_or_write_capabilities(self):
        source = _SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_roots.add(node.module.split(".")[0])
        self.assertFalse(
            imported_roots
            & {
                "os",
                "subprocess",
                "socket",
                "urllib",
                "requests",
                "httpx",
                "secrets",
            }
        )
        lowered = source.casefold()
        for marker in (
            "getenv(",
            "connect(",
            "connectionpool",
        ):
            self.assertNotIn(marker, lowered)


if __name__ == "__main__":
    unittest.main()
