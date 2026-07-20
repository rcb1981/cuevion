"""Offline tests for the Auth0 current-account authority composition."""

from __future__ import annotations

import ast
import base64
from pathlib import Path
import pickle
import unittest

from api.auth import account_authority
from api.auth import models
from cuevion_auth import current_account_repository_contract as contract


def _record_id(prefix: str, octet: int) -> str:
    encoded = base64.urlsafe_b64encode(bytes((octet,)) * 16)
    return prefix + encoded.rstrip(b"=").decode("ascii")


USER_ID = _record_id("usr_", 1)
OTHER_USER_ID = _record_id("usr_", 2)
EMAIL_ID = _record_id("vem_", 3)
OTHER_EMAIL_ID = _record_id("vem_", 7)
IDENTITY_ID = _record_id("aid_", 4)
WORKSPACE_ID = _record_id("wsp_", 5)
OTHER_WORKSPACE_ID = _record_id("wsp_", 6)
ISSUER = "https://cuevion-dev.eu.auth0.com/"
SUBJECT = "email|opaque-subject"
EMAIL = "rutger@hysteriarecs.com"
DATABASE_URL = (
    "postgresql://reader:reader-secret@reader.example.test:5432/cuevion"
    "?sslmode=require&channel_binding=require"
)
SENSITIVE_DATABASE_PARTS = (
    "reader-secret",
    "reader.example.test",
    "cuevion",
)

IDLE = object()
UNKNOWN = object()
IN_TRANSACTION = object()
DRIVER_CONTRACT = (IDLE, UNKNOWN, ())


def _candidate_row(
    *,
    user_id: object = USER_ID,
    user_row_version: object = 4,
    security_epoch: object = 3,
    email_id: object = EMAIL_ID,
    email_row_version: object = 2,
    identity_id: object = IDENTITY_ID,
    identity_row_version: object = 3,
    workspace_id: object = WORKSPACE_ID,
    workspace_row_version: object = 4,
    membership_row_version: object = 5,
) -> tuple[object, ...]:
    return (
        user_id,
        user_row_version,
        security_epoch,
        email_id,
        email_row_version,
        identity_id,
        identity_row_version,
        workspace_id,
        workspace_row_version,
        membership_row_version,
    )


def _identity_key(
    issuer: str = ISSUER,
    subject: str = SUBJECT,
) -> contract.AuthenticationIdentityLookupKey:
    return contract.AuthenticationIdentityLookupKey(issuer, subject)


def _authority_result(
    *,
    user_id: str = USER_ID,
    workspace_id: str = WORKSPACE_ID,
    issuer: str = ISSUER,
    subject: str = SUBJECT,
    email: str = EMAIL,
    method: models.AuthenticationMethod = models.AuthenticationMethod.EMAIL_OTP,
    identity_verified_email_id: str | None = EMAIL_ID,
    identity_status: models.AuthenticationIdentityStatus = (
        models.AuthenticationIdentityStatus.ACTIVE
    ),
) -> contract.CurrentAccountAuthorityResult:
    user = models.CuevionUser(
        schema_version=1,
        user_id=user_id,
        status=models.UserStatus.ACTIVE,
        primary_verified_email_id=EMAIL_ID,
        display_name="Cuevion Member",
        security_epoch=3,
        created_at=1,
        updated_at=2,
        row_version=4,
    )
    verified_email = models.VerifiedEmail(
        schema_version=1,
        email_id=EMAIL_ID,
        user_id=user_id,
        canonical_email=email,
        status=models.VerifiedEmailStatus.VERIFIED,
        verification_source="auth0_email_otp",
        created_at=1,
        verified_at=1,
        retired_at=None,
        row_version=2,
    )
    identity = models.AuthenticationIdentity(
        schema_version=1,
        identity_id=IDENTITY_ID,
        user_id=user_id,
        issuer=issuer,
        subject=subject,
        method=method,
        status=identity_status,
        verified_email_id=identity_verified_email_id,
        created_at=1,
        last_used_at=2,
        row_version=3,
    )
    workspace = models.Workspace(
        schema_version=1,
        workspace_id=workspace_id,
        status=models.WorkspaceStatus.ACTIVE,
        created_by_user_id=OTHER_USER_ID,
        created_at=1,
        updated_at=2,
        row_version=4,
    )
    membership = models.WorkspaceMembership(
        schema_version=1,
        workspace_id=workspace_id,
        user_id=user_id,
        role=models.WorkspaceRole.MEMBER,
        status=models.WorkspaceMembershipStatus.ACTIVE,
        created_at=1,
        updated_at=2,
        row_version=5,
    )
    authority = contract.CurrentAccountAuthority(
        user=user,
        primary_verified_email=verified_email,
        authentication_identity=identity,
        workspace=workspace,
        workspace_membership=membership,
    )
    return contract.CurrentAccountAuthorityResult(
        contract.CurrentAccountReadOutcome.FOUND,
        authority,
    )


def _failure_result(
    outcome: contract.CurrentAccountReadOutcome,
) -> contract.CurrentAccountAuthorityResult:
    return contract.CurrentAccountAuthorityResult(outcome, None)


class FakePgconn:
    def __init__(self, ssl_in_use: object = True) -> None:
        self.ssl_in_use = ssl_in_use


class FakeInfo:
    def __init__(self, transaction_status: object = IDLE) -> None:
        self.transaction_status = transaction_status


class FakeCursor:
    def __init__(
        self,
        rows: object,
        *,
        execute_failure: BaseException | None = None,
        fetch_failure: BaseException | None = None,
        close_failure: BaseException | None = None,
    ) -> None:
        self.rows = rows
        self.execute_failure = execute_failure
        self.fetch_failure = fetch_failure
        self.close_failure = close_failure
        self.calls: list[tuple[str, object]] = []
        self.closed = 0

    def execute(self, sql: str, parameters: object = None) -> None:
        self.calls.append((sql, parameters))
        if self.execute_failure is not None:
            raise self.execute_failure

    def fetchall(self) -> object:
        if self.fetch_failure is not None:
            raise self.fetch_failure
        return self.rows

    def close(self) -> None:
        self.closed += 1
        if self.close_failure is not None:
            raise self.close_failure


class FakeConnection:
    def __init__(
        self,
        rows: object,
        *,
        autocommit: object = False,
        ssl_in_use: object = True,
        transaction_status: object = IDLE,
        cursor_failure: BaseException | None = None,
        execute_failure: BaseException | None = None,
        fetch_failure: BaseException | None = None,
        cursor_close_failure: BaseException | None = None,
        rollback_failure: BaseException | None = None,
        close_failure: BaseException | None = None,
    ) -> None:
        self.autocommit = autocommit
        self.pgconn = FakePgconn(ssl_in_use)
        self.info = FakeInfo(transaction_status)
        self.cursor_failure = cursor_failure
        self.cursor_instance = FakeCursor(
            rows,
            execute_failure=execute_failure,
            fetch_failure=fetch_failure,
            close_failure=cursor_close_failure,
        )
        self.rollback_failure = rollback_failure
        self.close_failure = close_failure
        self.cursor_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def cursor(self) -> FakeCursor:
        self.cursor_count += 1
        if self.cursor_failure is not None:
            raise self.cursor_failure
        return self.cursor_instance

    def rollback(self) -> None:
        self.rollback_count += 1
        if self.rollback_failure is not None:
            raise self.rollback_failure

    def close(self) -> None:
        self.close_count += 1
        if self.close_failure is not None:
            raise self.close_failure


class Connector:
    def __init__(self, *connections: object) -> None:
        self.connections = list(connections)
        self.calls: list[tuple[str, bool, int]] = []

    def __call__(
        self,
        conninfo: str,
        *,
        autocommit: bool,
        connect_timeout: int,
    ) -> object:
        self.calls.append((conninfo, autocommit, connect_timeout))
        if not self.connections:
            raise AssertionError("unexpected connection")
        value = self.connections.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class FakeRepository:
    def __init__(
        self,
        identity_result: object,
        user_result: object | None = None,
    ) -> None:
        self.identity_result = identity_result
        self.user_result = user_result
        self.identity_calls: list[tuple[object, str]] = []
        self.user_calls: list[tuple[str, str]] = []

    def resolve_current_account_by_identity(
        self,
        identity_key: contract.AuthenticationIdentityLookupKey,
        workspace_id: str,
    ) -> object:
        self.identity_calls.append((identity_key, workspace_id))
        if isinstance(self.identity_result, BaseException):
            raise self.identity_result
        return self.identity_result

    def read_current_account_by_user(
        self,
        user_id: str,
        workspace_id: str,
    ) -> object:
        self.user_calls.append((user_id, workspace_id))
        return self.user_result


def _database_url() -> account_authority.AccountReaderDatabaseUrl:
    return account_authority.parse_account_reader_database_url(
        {
            "CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": DATABASE_URL,
        }
    )


def _runtime(
    repository: FakeRepository,
    *connections: object,
    driver_contract: object = DRIVER_CONTRACT,
) -> tuple[
    account_authority.RuntimeAccountAuthority,
    Connector,
]:
    connector = Connector(*connections)
    connection_factory = account_authority.AccountReaderConnectionFactory(
        _database_url(),
        connector,
    )
    runtime = account_authority.RuntimeAccountAuthority(
        connection_factory,
        repository,  # type: ignore[arg-type]
        driver_contract=driver_contract,
    )
    return runtime, connector


class ConfigurationTests(unittest.TestCase):
    def test_public_surface_is_narrow(self) -> None:
        self.assertEqual(
            account_authority.__all__,
            (
                "AccountAuthorityConfigurationError",
                "AccountAuthorityUnavailableError",
                "AccountReaderDatabaseUrl",
                "parse_account_reader_database_url",
                "AccountReaderConnectionFactory",
                "RuntimeAccountAuthority",
                "build_runtime_account_authority",
                "auth0_authority_matches",
            ),
        )

    def test_exact_dedicated_reader_url_is_accepted_and_redacted(self) -> None:
        value = _database_url()
        self.assertEqual(value.value, DATABASE_URL)
        rendered = repr(value) + str(value)
        for marker in SENSITIVE_DATABASE_PARTS:
            self.assertNotIn(marker, rendered)
        with self.assertRaises(account_authority.AccountAuthorityConfigurationError):
            value.value = "replacement"  # type: ignore[misc]
        with self.assertRaises(account_authority.AccountAuthorityConfigurationError):
            pickle.dumps(value)

        reversed_query = DATABASE_URL.replace(
            "sslmode=require&channel_binding=require",
            "channel_binding=require&sslmode=require",
        )
        parsed = account_authority.parse_account_reader_database_url(
            {"CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": reversed_query}
        )
        self.assertEqual(parsed.value, reversed_query)

    def test_missing_or_generic_database_configuration_is_rejected(self) -> None:
        cases: tuple[object, ...] = (
            {},
            {"CUEVION_DATABASE_URL": DATABASE_URL},
            {"CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": ""},
            {"CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": " " + DATABASE_URL},
            None,
        )
        for environment in cases:
            with self.subTest(environment_type=type(environment).__name__):
                with self.assertRaises(
                    account_authority.AccountAuthorityConfigurationError
                ) as caught:
                    account_authority.parse_account_reader_database_url(
                        environment  # type: ignore[arg-type]
                    )
                self.assertEqual(caught.exception.args, ())
                self.assertEqual(
                    str(caught.exception),
                    "invalid account authority configuration",
                )
                for marker in SENSITIVE_DATABASE_PARTS:
                    self.assertNotIn(marker, repr(caught.exception))

    def test_tls_query_and_psycopg_url_contract_is_exact(self) -> None:
        mutations = (
            ("postgresql://", "postgres://"),
            ("postgresql://", "postgresql+psycopg://"),
            ("sslmode=require", "sslmode=prefer"),
            ("channel_binding=require", "channel_binding=prefer"),
            ("&channel_binding=require", ""),
            ("sslmode=require&", ""),
            (
                "channel_binding=require",
                "channel_binding=require&application_name=cuevion",
            ),
            ("?sslmode", "#fragment?sslmode"),
        )
        for old, new in mutations:
            with self.subTest(old=old, new=new):
                value = DATABASE_URL.replace(old, new)
                with self.assertRaises(
                    account_authority.AccountAuthorityConfigurationError
                ):
                    account_authority.parse_account_reader_database_url(
                        {"CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": value}
                    )

    def test_malformed_authority_urls_are_rejected(self) -> None:
        invalid = (
            "postgresql://reader@reader.example.test/cuevion"
            "?sslmode=require&channel_binding=require",
            "postgresql://:secret@reader.example.test/cuevion"
            "?sslmode=require&channel_binding=require",
            "postgresql://reader:secret@/cuevion"
            "?sslmode=require&channel_binding=require",
            "postgresql://reader:secret@reader.example.test/"
            "?sslmode=require&channel_binding=require",
            "postgresql://reader:secret@reader.example.test/a/b"
            "?sslmode=require&channel_binding=require",
            "postgresql://reader:secret@reader.example.test:99999/cuevion"
            "?sslmode=require&channel_binding=require",
            "postgresql://reader:bad%ZZ@reader.example.test/cuevion"
            "?sslmode=require&channel_binding=require",
            "postgresql://reader:secret@reader.example.test/bad%ZZ"
            "?sslmode=require&channel_binding=require",
        )
        for value in invalid:
            with self.subTest(value=value.split("@")[-1]):
                with self.assertRaises(
                    account_authority.AccountAuthorityConfigurationError
                ):
                    account_authority.parse_account_reader_database_url(
                        {"CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": value}
                    )

    def test_rejected_url_is_absent_after_secret_parsing_frames_unwind(self) -> None:
        rejected = DATABASE_URL.replace("sslmode=require", "sslmode=disable")
        try:
            account_authority.parse_account_reader_database_url(
                {"CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": rejected}
            )
        except account_authority.AccountAuthorityConfigurationError as error:
            traceback = error.__traceback__
            while traceback is not None:
                if (
                    traceback.tb_frame.f_code.co_filename
                    == account_authority.__file__
                ):
                    rendered = repr(traceback.tb_frame.f_locals)
                    for marker in SENSITIVE_DATABASE_PARTS:
                        self.assertNotIn(marker, rendered)
                traceback = traceback.tb_next
        else:
            self.fail("invalid reader URL was accepted")


class ConnectionFactoryTests(unittest.TestCase):
    def test_factory_requests_fresh_non_autocommit_connections_and_exact_tls(
        self,
    ) -> None:
        first = FakeConnection([])
        second = FakeConnection([])
        connector = Connector(first, second)
        factory = account_authority.AccountReaderConnectionFactory(
            _database_url(),
            connector,
        )
        self.assertIs(factory(), first)
        self.assertIs(factory(), second)
        self.assertEqual(
            connector.calls,
            [
                (DATABASE_URL, False, 5),
                (DATABASE_URL, False, 5),
            ],
        )
        rendered = repr(factory)
        for marker in SENSITIVE_DATABASE_PARTS:
            self.assertNotIn(marker, rendered)

    def test_factory_rejects_nonexact_tls_evidence_and_closes_connection(self) -> None:
        for evidence in (False, None, 1, "true"):
            with self.subTest(evidence=evidence):
                connection = FakeConnection([], ssl_in_use=evidence)
                factory = account_authority.AccountReaderConnectionFactory(
                    _database_url(),
                    Connector(connection),
                )
                with self.assertRaises(
                    account_authority.AccountAuthorityUnavailableError
                ) as caught:
                    factory()
                self.assertEqual(caught.exception.args, ())
                self.assertEqual(connection.close_count, 1)

    def test_factory_rejects_autocommit_connection_and_closes_it(self) -> None:
        connection = FakeConnection([], autocommit=True)
        factory = account_authority.AccountReaderConnectionFactory(
            _database_url(),
            Connector(connection),
        )
        with self.assertRaises(account_authority.AccountAuthorityUnavailableError):
            factory()
        self.assertEqual(connection.close_count, 1)


class ResolverTests(unittest.TestCase):
    def assert_cleaned(self, connection: FakeConnection) -> None:
        self.assertEqual(connection.cursor_instance.closed, 1)
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(connection.close_count, 1)

    def test_success_uses_unique_pre_and_post_reads_around_existing_repository(
        self,
    ) -> None:
        before = FakeConnection([_candidate_row()])
        after = FakeConnection([_candidate_row()])
        result = _authority_result()
        repository = FakeRepository(result)
        runtime, connector = _runtime(repository, before, after)
        identity_key = _identity_key()

        actual = runtime.resolve_current_account_by_identity(identity_key)

        self.assertIs(actual, result)
        self.assertEqual(
            repository.identity_calls,
            [(identity_key, WORKSPACE_ID)],
        )
        self.assertEqual(len(connector.calls), 2)
        for connection in (before, after):
            self.assertEqual(
                connection.cursor_instance.calls,
                [
                    (account_authority._SET_TRANSACTION_SQL, None),
                    (
                        account_authority._SELECT_UNIQUE_ACTIVE_WORKSPACE_SQL,
                        (ISSUER, SUBJECT),
                    ),
                ],
            )
            self.assert_cleaned(connection)

    def test_zero_or_multiple_active_workspaces_are_not_authorized(self) -> None:
        cases = (
            [],
            [
                _candidate_row(),
                _candidate_row(workspace_id=OTHER_WORKSPACE_ID),
            ],
            [_candidate_row(), _candidate_row()],
        )
        for rows in cases:
            with self.subTest(row_count=len(rows)):
                repository = FakeRepository(_authority_result())
                connection = FakeConnection(rows)
                runtime, _connector = _runtime(repository, connection)
                result = runtime.resolve_current_account_by_identity(
                    _identity_key()
                )
                self.assertIs(
                    result.outcome,
                    contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
                )
                self.assertIsNone(result.authority)
                self.assertEqual(repository.identity_calls, [])
                self.assert_cleaned(connection)

    def test_malformed_candidate_storage_is_internal_error(self) -> None:
        malformed_rows = (
            [(USER_ID,)],
            [[USER_ID, WORKSPACE_ID]],
            [_candidate_row(user_id="not-a-user-id")],
            [_candidate_row(workspace_id="not-a-workspace-id")],
            (_candidate_row(),),
        )
        for rows in malformed_rows:
            with self.subTest(rows_type=type(rows).__name__):
                connection = FakeConnection(rows)
                repository = FakeRepository(_authority_result())
                runtime, _connector = _runtime(repository, connection)
                result = runtime.resolve_current_account_by_identity(
                    _identity_key()
                )
                self.assertIs(
                    result.outcome,
                    contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
                )
                self.assertEqual(repository.identity_calls, [])
                self.assert_cleaned(connection)

    def test_post_read_workspace_change_or_new_membership_denies(self) -> None:
        post_cases = (
            [_candidate_row(workspace_id=OTHER_WORKSPACE_ID)],
            [_candidate_row(user_row_version=5)],
            [_candidate_row(security_epoch=4)],
            [_candidate_row(email_id=OTHER_EMAIL_ID)],
            [
                _candidate_row(),
                _candidate_row(workspace_id=OTHER_WORKSPACE_ID),
            ],
            [],
        )
        for post_rows in post_cases:
            with self.subTest(post_rows=post_rows):
                before = FakeConnection([_candidate_row()])
                after = FakeConnection(post_rows)
                repository = FakeRepository(_authority_result())
                runtime, _connector = _runtime(repository, before, after)
                result = runtime.resolve_current_account_by_identity(
                    _identity_key()
                )
                self.assertIs(
                    result.outcome,
                    contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
                )
                self.assert_cleaned(before)
                self.assert_cleaned(after)

    def test_repository_denial_or_failure_is_preserved_without_post_read(self) -> None:
        for outcome in (
            contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
            contract.CurrentAccountReadOutcome.UNAVAILABLE,
            contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
        ):
            with self.subTest(outcome=outcome):
                before = FakeConnection([_candidate_row()])
                repository_result = _failure_result(outcome)
                repository = FakeRepository(repository_result)
                runtime, connector = _runtime(repository, before)
                result = runtime.resolve_current_account_by_identity(
                    _identity_key()
                )
                self.assertIs(result, repository_result)
                self.assertEqual(len(connector.calls), 1)
                self.assert_cleaned(before)

    def test_stored_identity_method_and_candidate_mismatches_deny(self) -> None:
        cases = (
            _authority_result(method=models.AuthenticationMethod.OIDC),
            _authority_result(identity_verified_email_id=None),
            _authority_result(issuer="https://other.example.test/"),
            _authority_result(subject="email|other"),
            _authority_result(user_id=OTHER_USER_ID),
            _authority_result(workspace_id=OTHER_WORKSPACE_ID),
        )
        for repository_result in cases:
            with self.subTest(
                method=repository_result.authority.authentication_identity.method
            ):
                before = FakeConnection([_candidate_row()])
                repository = FakeRepository(repository_result)
                runtime, connector = _runtime(repository, before)
                result = runtime.resolve_current_account_by_identity(
                    _identity_key()
                )
                self.assertIs(
                    result.outcome,
                    contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
                )
                self.assertEqual(len(connector.calls), 1)

    def test_operational_and_transaction_status_failures_are_classified(self) -> None:
        class OperationalFailure(Exception):
            pass

        driver = (IDLE, UNKNOWN, (OperationalFailure,))
        cases = (
            (
                Connector(OperationalFailure("private endpoint")),
                contract.CurrentAccountReadOutcome.UNAVAILABLE,
            ),
            (
                Connector(FakeConnection([], transaction_status=UNKNOWN)),
                contract.CurrentAccountReadOutcome.UNAVAILABLE,
            ),
            (
                Connector(FakeConnection([], transaction_status=IN_TRANSACTION)),
                contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            ),
        )
        for connector, expected in cases:
            with self.subTest(expected=expected):
                connection_factory = (
                    account_authority.AccountReaderConnectionFactory(
                        _database_url(),
                        connector,
                    )
                )
                repository = FakeRepository(_authority_result())
                runtime = account_authority.RuntimeAccountAuthority(
                    connection_factory,
                    repository,  # type: ignore[arg-type]
                    driver_contract=driver,
                )
                result = runtime.resolve_current_account_by_identity(
                    _identity_key()
                )
                self.assertIs(result.outcome, expected)
                self.assertIsNone(result.authority)

    def test_cleanup_failure_overrides_success_and_cleanup_continues(self) -> None:
        class OperationalFailure(Exception):
            pass

        driver = (IDLE, UNKNOWN, (OperationalFailure,))
        connection = FakeConnection(
            [_candidate_row()],
            rollback_failure=OperationalFailure("private rollback"),
        )
        repository = FakeRepository(_authority_result())
        runtime, _connector = _runtime(
            repository,
            connection,
            driver_contract=driver,
        )
        result = runtime.resolve_current_account_by_identity(_identity_key())
        self.assertIs(
            result.outcome,
            contract.CurrentAccountReadOutcome.UNAVAILABLE,
        )
        self.assertEqual(connection.close_count, 1)
        self.assertEqual(repository.identity_calls, [])

    def test_invalid_identity_fails_before_connection_or_repository(self) -> None:
        repository = FakeRepository(_authority_result())
        runtime, connector = _runtime(repository)
        with self.assertRaises(
            contract.CurrentAccountRepositoryContractValidationError
        ):
            runtime.resolve_current_account_by_identity(
                object()  # type: ignore[arg-type]
            )
        self.assertEqual(connector.calls, [])
        self.assertEqual(repository.identity_calls, [])

    def test_by_user_read_delegates_to_existing_repository(self) -> None:
        sentinel = object()
        repository = FakeRepository(_authority_result(), sentinel)
        runtime, connector = _runtime(repository)
        result = runtime.read_current_account_by_user(USER_ID, WORKSPACE_ID)
        self.assertIs(result, sentinel)
        self.assertEqual(repository.user_calls, [(USER_ID, WORKSPACE_ID)])
        self.assertEqual(connector.calls, [])


class ConsistencyAndCompositionTests(unittest.TestCase):
    def test_auth0_authority_match_requires_exact_claims_email_and_method(self) -> None:
        result = _authority_result()
        key = _identity_key()
        self.assertTrue(
            account_authority.auth0_authority_matches(result, key, EMAIL)
        )
        mismatches = (
            (result, _identity_key(subject="email|other"), EMAIL),
            (result, key, "other@example.test"),
            (result, key, EMAIL.upper()),
            (_authority_result(method=models.AuthenticationMethod.OIDC), key, EMAIL),
            (_authority_result(identity_verified_email_id=None), key, EMAIL),
            (
                _failure_result(
                    contract.CurrentAccountReadOutcome.NOT_AUTHORIZED
                ),
                key,
                EMAIL,
            ),
            (object(), key, EMAIL),
            (result, object(), EMAIL),
            (result, key, object()),
        )
        for candidate_result, candidate_key, email in mismatches:
            with self.subTest(email_type=type(email).__name__):
                self.assertFalse(
                    account_authority.auth0_authority_matches(
                        candidate_result,
                        candidate_key,
                        email,
                    )
                )

    def test_builder_uses_only_caller_mapping_and_injected_dependencies(self) -> None:
        before = FakeConnection([_candidate_row()])
        after = FakeConnection([_candidate_row()])
        connector = Connector(before, after)
        repository = FakeRepository(_authority_result())
        received_factories: list[object] = []

        def repository_factory(connection_factory: object) -> FakeRepository:
            received_factories.append(connection_factory)
            return repository

        runtime = account_authority.build_runtime_account_authority(
            {"CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": DATABASE_URL},
            connect=connector,
            repository_factory=repository_factory,  # type: ignore[arg-type]
            driver_contract=DRIVER_CONTRACT,
        )
        result = runtime.resolve_current_account_by_identity(_identity_key())
        self.assertIs(result.outcome, contract.CurrentAccountReadOutcome.FOUND)
        self.assertEqual(len(received_factories), 1)
        self.assertIsInstance(
            received_factories[0],
            account_authority.AccountReaderConnectionFactory,
        )

    def test_fixed_sql_is_read_only_and_uses_only_current_authority_tables(
        self,
    ) -> None:
        normalized = " ".join(
            account_authority._SELECT_UNIQUE_ACTIVE_WORKSPACE_SQL.split()
        ).casefold()
        expected_tables = {
            "cuevion_account.authentication_identities",
            "cuevion_account.users",
            "cuevion_account.verified_emails",
            "cuevion_account.workspace_memberships",
            "cuevion_account.workspaces",
        }
        import re

        seen = set(
            re.findall(
                r"\b(?:from|join)\s+(cuevion_account[.][a-z_]+)\b",
                normalized,
            )
        )
        self.assertEqual(seen, expected_tables)
        self.assertEqual(normalized.count("%s"), 2)
        self.assertTrue(normalized.endswith("limit 2"))
        for forbidden in (
            "initial_account_operations",
            "security_events",
            " insert ",
            " update ",
            " delete ",
            " merge ",
            " commit ",
            " for update ",
        ):
            self.assertNotIn(forbidden, " " + normalized + " ")
        self.assertEqual(
            " ".join(account_authority._SET_TRANSACTION_SQL.split()).upper(),
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        )

    def test_psycopg_and_repository_imports_are_deferred(self) -> None:
        source_path = Path(account_authority.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        top_level_imports: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level_imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                top_level_imports.add(node.module)
        self.assertNotIn("psycopg", top_level_imports)
        self.assertNotIn(
            "cuevion_db.postgresql_current_account_repository",
            top_level_imports,
        )


if __name__ == "__main__":
    unittest.main()
