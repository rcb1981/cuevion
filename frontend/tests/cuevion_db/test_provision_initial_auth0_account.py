"""Focused offline tests for the inactive first-Auth0-account operator."""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path
import unittest
from unittest import mock

from api.auth import models
from cuevion_auth import account_repository_contract as account_contract
from cuevion_auth import current_account_repository_contract as current_contract
from cuevion_db import postgresql_initial_account_repository as initial_repository
from cuevion_db import provision_initial_auth0_account as operator


_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "cuevion_db"
    / "provision_initial_auth0_account.py"
)


def _identifier(prefix: str, octet: int) -> str:
    suffix = base64.urlsafe_b64encode(bytes([octet]) * 16).rstrip(b"=").decode()
    return prefix + suffix


USER_ID = _identifier("usr_", 1)
EMAIL_ID = _identifier("vem_", 2)
IDENTITY_ID = _identifier("aid_", 3)
WORKSPACE_ID = _identifier("wsp_", 4)


def _authority_result(
    *,
    email: str = "rutger@hysteriarecs.com",
    display_name: str = "Rutger Bäumer",
    method: models.AuthenticationMethod = models.AuthenticationMethod.EMAIL_OTP,
    role: models.WorkspaceRole = models.WorkspaceRole.OWNER,
    workspace_creator: str = USER_ID,
    security_epoch: int = 1,
    issuer: str = "https://cuevion-dev.eu.auth0.com/",
    subject: str = "email|6a5e8971963ce518400f660b",
    row_version: int = 1,
) -> current_contract.CurrentAccountAuthorityResult:
    user = models.CuevionUser(
        schema_version=1,
        user_id=USER_ID,
        status=models.UserStatus.ACTIVE,
        primary_verified_email_id=EMAIL_ID,
        display_name=display_name,
        security_epoch=security_epoch,
        created_at=100,
        updated_at=100,
        row_version=row_version,
    )
    verified_email = models.VerifiedEmail(
        schema_version=1,
        email_id=EMAIL_ID,
        user_id=USER_ID,
        canonical_email=email,
        status=models.VerifiedEmailStatus.VERIFIED,
        verification_source="reviewed-test-evidence",
        created_at=100,
        verified_at=100,
        retired_at=None,
        row_version=row_version,
    )
    identity = models.AuthenticationIdentity(
        schema_version=1,
        identity_id=IDENTITY_ID,
        user_id=USER_ID,
        issuer=issuer,
        subject=subject,
        method=method,
        status=models.AuthenticationIdentityStatus.ACTIVE,
        verified_email_id=EMAIL_ID,
        created_at=100,
        last_used_at=None,
        row_version=row_version,
    )
    workspace = models.Workspace(
        schema_version=1,
        workspace_id=WORKSPACE_ID,
        status=models.WorkspaceStatus.ACTIVE,
        created_by_user_id=workspace_creator,
        created_at=100,
        updated_at=100,
        row_version=row_version,
    )
    membership = models.WorkspaceMembership(
        schema_version=1,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        role=role,
        status=models.WorkspaceMembershipStatus.ACTIVE,
        created_at=100,
        updated_at=100,
        row_version=row_version,
    )
    authority = current_contract.CurrentAccountAuthority(
        user=user,
        primary_verified_email=verified_email,
        authentication_identity=identity,
        workspace=workspace,
        workspace_membership=membership,
    )
    return current_contract.CurrentAccountAuthorityResult(
        outcome=current_contract.CurrentAccountReadOutcome.FOUND,
        authority=authority,
    )


def _not_authorized_result() -> current_contract.CurrentAccountAuthorityResult:
    return current_contract.CurrentAccountAuthorityResult(
        outcome=current_contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
        authority=None,
    )


class FakeInventory:
    def __init__(self, *values: object) -> None:
        self.values = [self._snapshot(value) for value in values]
        self.calls = 0

    @staticmethod
    def _snapshot(value: object):
        if value is None:
            return None
        if (
            type(value) is tuple
            and len(value) == 2
            and type(value[0]) is tuple
            and type(value[1]) is str
        ):
            return value
        return (value, "stable-fingerprint")

    def read_snapshot(self):
        self.calls += 1
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


class FakeAuthority:
    def __init__(self, result: current_contract.CurrentAccountAuthorityResult) -> None:
        self.result = result
        self.calls: list[current_contract.AuthenticationIdentityLookupKey] = []

    def resolve_current_account_by_identity(
        self, identity_key: current_contract.AuthenticationIdentityLookupKey
    ) -> current_contract.CurrentAccountAuthorityResult:
        self.calls.append(identity_key)
        return self.result


class SequenceInspector:
    def __init__(self, *states: operator.GraphState) -> None:
        self.states = list(states)
        self.calls = 0

    def inspect(self) -> operator.GraphState:
        self.calls += 1
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]


def _receipt(
    request: account_contract.InitialAccountCreationRequest,
) -> account_contract.InitialAccountCreationReceipt:
    return account_contract.InitialAccountCreationReceipt(
        schema_version=1,
        user_id=request.user.user_id,
        verified_email_id=request.verified_email.email_id,
        authentication_identity_id=request.authentication_identity.identity_id,
        workspace_id=request.workspace.workspace_id,
        security_event_id=request.security_event.event_id,
    )


class RecordingRepository:
    def __init__(
        self,
        outcome: account_contract.InitialAccountCreationOutcome,
        on_create=None,
    ) -> None:
        self.outcome = outcome
        self.on_create = on_create
        self.calls: list[account_contract.InitialAccountCreationRequest] = []

    def create_initial_account(
        self, request: account_contract.InitialAccountCreationRequest
    ) -> account_contract.InitialAccountCreationResult:
        self.calls.append(request)
        if self.on_create is not None:
            self.on_create(request)
        if self.outcome in (
            account_contract.InitialAccountCreationOutcome.CREATED,
            account_contract.InitialAccountCreationOutcome.EXACT_REPLAY,
        ):
            return account_contract.InitialAccountCreationResult(
                outcome=self.outcome,
                conflict_reason=None,
                receipt=_receipt(request),
            )
        if self.outcome is account_contract.InitialAccountCreationOutcome.CONFLICT:
            return account_contract.InitialAccountCreationResult(
                outcome=self.outcome,
                conflict_reason=(
                    account_contract.InitialAccountConflictReason.AUTHORITY_ALREADY_CLAIMED
                ),
                receipt=None,
            )
        return account_contract.InitialAccountCreationResult(
            outcome=self.outcome,
            conflict_reason=None,
            receipt=None,
        )


class FakeCursor:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.statements: list[str] = []
        self.closed = 0

    def execute(self, sql: str, _parameters=None) -> None:
        self.statements.append(sql)

    def fetchall(self):
        return self.rows

    def close(self) -> None:
        self.closed += 1


class FakeConnection:
    def __init__(self, rows) -> None:
        self.autocommit = False
        self.cursor_value = FakeCursor(rows)
        self.rollbacks = 0
        self.closes = 0
        self.commits = 0

    def cursor(self) -> FakeCursor:
        return self.cursor_value

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1

    def commit(self) -> None:
        self.commits += 1


class AggregateCursor:
    def __init__(self, connection, fail_on_table: str | None = None) -> None:
        self.connection = connection
        self.fail_on_table = fail_on_table
        self.rows = []
        self.closed = 0
        self.statements: list[str] = []

    def execute(self, sql: str, _parameters=None) -> None:
        normalized = sql.strip()
        self.statements.append(normalized)
        if normalized.startswith("SET "):
            self.rows = []
            return
        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            self.rows = [("",)]
            return
        if normalized.startswith("SELECT nextval"):
            self.rows = [(1,)]
            return
        if normalized.startswith("SELECT EXISTS"):
            self.rows = [(False,)]
            return
        if (
            normalized.startswith("SELECT")
            and "FROM cuevion_account.initial_account_operations" in normalized
        ):
            self.rows = []
            return
        prefix = "INSERT INTO cuevion_account."
        if normalized.startswith(prefix):
            table = normalized[len(prefix) :].split()[0]
            if table == self.fail_on_table:
                raise RuntimeError("fixed mid-aggregate failure")
            self.connection.staged.append(table)
            self.rows = []
            return
        raise AssertionError("unexpected repository statement")

    def fetchall(self):
        return self.rows

    def close(self) -> None:
        self.closed += 1


class AggregateConnection:
    def __init__(self, fail_on_table: str | None = None) -> None:
        self.autocommit = False
        self.staged: list[str] = []
        self.committed: list[str] = []
        self.cursor_value = AggregateCursor(self, fail_on_table)
        self.cursor_calls = 0
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self) -> AggregateCursor:
        self.cursor_calls += 1
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1
        self.committed.extend(self.staged)
        self.staged.clear()

    def rollback(self) -> None:
        self.rollbacks += 1
        self.staged.clear()

    def close(self) -> None:
        self.closes += 1


class AggregateConnectionFactory:
    def __init__(self, connection: AggregateConnection) -> None:
        self.connection = connection
        self.calls = 0

    def __call__(self) -> AggregateConnection:
        self.calls += 1
        return self.connection


class CheckModeTests(unittest.TestCase):
    def _operator(self, inspector, repository_factory) -> operator.FirstAuth0AccountOperator:
        return operator.FirstAuth0AccountOperator(
            inspector,
            repository_factory,
            lambda: 100,
        )

    def test_empty_authority_check_is_read_only_and_ready(self) -> None:
        connection = FakeConnection([(0, 0, 0, 0, 0, "")])
        reader = operator._InventoryReader(lambda: connection)

        self.assertEqual(
            reader.read_snapshot(),
            ((0, 0, 0, 0, 0), ""),
        )
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.closes, 1)
        self.assertEqual(connection.cursor_value.closed, 1)
        combined = "\n".join(connection.cursor_value.statements).upper()
        self.assertIn("READ ONLY", combined)
        self.assertIn("SELECT", combined)
        for forbidden in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            self.assertNotIn(forbidden, combined)

    def test_default_and_explicit_check_never_construct_repository(self) -> None:
        for arguments in ([], ["check"]):
            with self.subTest(arguments=arguments):
                writes = []

                def forbidden_repository_factory(*values):
                    writes.append(values)
                    raise AssertionError("check must not construct writer")

                instance = self._operator(
                    SequenceInspector(operator.GraphState.EMPTY),
                    forbidden_repository_factory,
                )
                stdout = io.StringIO()
                stderr = io.StringIO()
                exit_code = operator._run(
                    arguments,
                    lambda: instance,
                    stdout,
                    stderr,
                )

                self.assertEqual(exit_code, 0)
                self.assertEqual(stdout.getvalue(), "CHECK PASSED\nREADY TO PROVISION\n")
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(writes, [])

    def test_only_exact_apply_confirmation_can_reach_operator_factory(self) -> None:
        invalid = (
            ["apply"],
            ["--apply"],
            ["apply", "yes"],
            ["check", "apply"],
            ["apply", operator._APPLY_CONFIRMATION, "extra"],
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                calls = []
                stderr = io.StringIO()
                exit_code = operator._run(
                    list(arguments),
                    lambda: calls.append(True),
                    io.StringIO(),
                    stderr,
                )
                self.assertEqual(exit_code, 2)
                self.assertEqual(calls, [])
                self.assertEqual(
                    stderr.getvalue(), "CONFLICT — NO CHANGES MADE\n"
                )


class ApplyModeTests(unittest.TestCase):
    def test_success_dispatches_one_complete_aggregate_repository_call(self) -> None:
        inspector = SequenceInspector(
            operator.GraphState.EMPTY,
            operator.GraphState.EXACT,
        )
        repository = RecordingRepository(
            account_contract.InitialAccountCreationOutcome.CREATED
        )
        factory_calls = []

        def repository_factory(request, trusted_now):
            factory_calls.append((request, trusted_now))
            return repository

        instance = operator.FirstAuth0AccountOperator(
            inspector,
            repository_factory,
            lambda: 100,
        )
        status = instance.apply()

        self.assertIs(status, operator.OperatorStatus.PROVISIONING_PASSED)
        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(len(repository.calls), 1)
        request = repository.calls[0]
        account_contract.validate_initial_account_creation_request(request)
        self.assertEqual(request.user.display_name, "Rutger Bäumer")
        self.assertEqual(request.user.security_epoch, 1)
        self.assertEqual(
            request.verified_email.canonical_email, "rutger@hysteriarecs.com"
        )
        self.assertEqual(
            request.user.primary_verified_email_id,
            request.verified_email.email_id,
        )
        self.assertEqual(
            request.authentication_identity.issuer,
            "https://cuevion-dev.eu.auth0.com/",
        )
        self.assertEqual(
            request.authentication_identity.subject,
            "email|6a5e8971963ce518400f660b",
        )
        self.assertIs(
            request.authentication_identity.method,
            models.AuthenticationMethod.EMAIL_OTP,
        )
        self.assertEqual(
            request.workspace.created_by_user_id,
            request.user.user_id,
        )
        self.assertEqual(
            request.workspace_membership.workspace_id,
            request.workspace.workspace_id,
        )
        self.assertIs(request.workspace_membership.role, models.WorkspaceRole.OWNER)
        self.assertNotIn("INSERT INTO", _SOURCE.read_text(encoding="utf-8").upper())

    def test_exact_second_execution_is_idempotent_no_op(self) -> None:
        state = {"value": operator.GraphState.EMPTY}
        repository_calls = []

        class StatefulInspector:
            def inspect(self):
                return state["value"]

        def repository_factory(request, trusted_now):
            repository_calls.append((request, trusted_now))

            def provision(_request):
                state["value"] = operator.GraphState.EXACT

            return RecordingRepository(
                account_contract.InitialAccountCreationOutcome.CREATED,
                provision,
            )

        instance = operator.FirstAuth0AccountOperator(
            StatefulInspector(), repository_factory, lambda: 100
        )

        self.assertIs(
            instance.apply(), operator.OperatorStatus.PROVISIONING_PASSED
        )
        self.assertIs(
            instance.apply(), operator.OperatorStatus.ALREADY_PROVISIONED
        )
        self.assertEqual(len(repository_calls), 1)

    def test_repository_failure_with_empty_post_state_reports_full_rollback(self) -> None:
        inspector = SequenceInspector(
            operator.GraphState.EMPTY,
            operator.GraphState.EMPTY,
        )
        repository = RecordingRepository(
            account_contract.InitialAccountCreationOutcome.INTERNAL_ERROR
        )
        instance = operator.FirstAuth0AccountOperator(
            inspector,
            lambda _request, _trusted_now: repository,
            lambda: 100,
        )

        self.assertIs(instance.apply(), operator.OperatorStatus.ROLLED_BACK)
        self.assertEqual(len(repository.calls), 1)

    def test_repository_conflict_with_empty_post_state_remains_conflict(self) -> None:
        inspector = SequenceInspector(
            operator.GraphState.EMPTY,
            operator.GraphState.EMPTY,
        )
        repository = RecordingRepository(
            account_contract.InitialAccountCreationOutcome.CONFLICT
        )
        instance = operator.FirstAuth0AccountOperator(
            inspector,
            lambda _request, _trusted_now: repository,
            lambda: 100,
        )

        self.assertIs(instance.apply(), operator.OperatorStatus.CONFLICT)
        self.assertEqual(len(repository.calls), 1)

    def test_concurrent_exact_graph_after_repository_conflict_is_already_provisioned(self) -> None:
        inspector = SequenceInspector(
            operator.GraphState.EMPTY,
            operator.GraphState.EXACT,
        )
        repository = RecordingRepository(
            account_contract.InitialAccountCreationOutcome.CONFLICT
        )
        instance = operator.FirstAuth0AccountOperator(
            inspector,
            lambda _request, _trusted_now: repository,
            lambda: 100,
        )

        self.assertIs(
            instance.apply(), operator.OperatorStatus.ALREADY_PROVISIONED
        )
        self.assertEqual(len(repository.calls), 1)

    def test_post_write_conflict_is_unknown_instead_of_no_changes(self) -> None:
        inspector = SequenceInspector(
            operator.GraphState.EMPTY,
            operator.GraphState.CONFLICT,
        )
        repository = RecordingRepository(
            account_contract.InitialAccountCreationOutcome.CREATED
        )
        instance = operator.FirstAuth0AccountOperator(
            inspector,
            lambda _request, _trusted_now: repository,
            lambda: 100,
        )

        self.assertIs(instance.apply(), operator.OperatorStatus.UNKNOWN)
        self.assertEqual(len(repository.calls), 1)

    def test_apply_output_requires_exact_confirmation_and_is_value_free(self) -> None:
        inspector = SequenceInspector(
            operator.GraphState.EMPTY,
            operator.GraphState.EXACT,
        )
        repository = RecordingRepository(
            account_contract.InitialAccountCreationOutcome.CREATED
        )
        instance = operator.FirstAuth0AccountOperator(
            inspector,
            lambda _request, _trusted_now: repository,
            lambda: 100,
        )
        stdout = io.StringIO()
        exit_code = operator._run(
            ["apply", operator._APPLY_CONFIRMATION],
            lambda: instance,
            stdout,
            io.StringIO(),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), "PROVISIONING PASSED\n")
        self.assertEqual(len(repository.calls), 1)


class RepositoryTransactionTests(unittest.TestCase):
    def _repository(self, connection: AggregateConnection, request):
        factory = AggregateConnectionFactory(connection)
        authorizer = operator._BoundNewOperationAuthorizer(request, 100)
        repository = initial_repository.PostgreSQLInitialAccountRepository(
            factory,
            authorizer,
        )
        return repository, factory

    def test_existing_repository_commits_all_five_current_records_once(self) -> None:
        request = operator._build_initial_request(100)
        connection = AggregateConnection()
        repository, factory = self._repository(connection, request)

        result = repository.create_initial_account(request)

        self.assertIs(
            result.outcome,
            account_contract.InitialAccountCreationOutcome.CREATED,
        )
        self.assertEqual(factory.calls, 1)
        self.assertEqual(connection.cursor_calls, 1)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(
            connection.committed,
            [
                "users",
                "verified_emails",
                "authentication_identities",
                "workspaces",
                "workspace_memberships",
                "initial_account_operations",
                "security_events",
            ],
        )
        self.assertEqual(connection.staged, [])
        self.assertEqual(connection.cursor_value.closed, 1)
        self.assertEqual(connection.closes, 1)

    def test_mid_record_failure_rolls_back_every_staged_record(self) -> None:
        request = operator._build_initial_request(100)
        connection = AggregateConnection(
            fail_on_table="authentication_identities"
        )
        repository, factory = self._repository(connection, request)

        result = repository.create_initial_account(request)

        self.assertIs(
            result.outcome,
            account_contract.InitialAccountCreationOutcome.INTERNAL_ERROR,
        )
        self.assertEqual(factory.calls, 1)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.committed, [])
        self.assertEqual(connection.staged, [])
        self.assertEqual(connection.cursor_value.closed, 1)
        self.assertEqual(connection.closes, 1)


class ConflictClassificationTests(unittest.TestCase):
    def _inspect(self, counts, result) -> operator.GraphState:
        inventory = FakeInventory(counts, counts)
        authority = FakeAuthority(result)
        return operator.CurrentGraphInspector(inventory, authority).inspect()

    def test_exact_existing_graph_is_already_provisioned(self) -> None:
        inspector = operator.CurrentGraphInspector(
            FakeInventory((1, 1, 1, 1, 1), (1, 1, 1, 1, 1)),
            FakeAuthority(_authority_result()),
        )
        instance = operator.FirstAuth0AccountOperator(
            inspector,
            lambda *_values: self.fail("already provisioned must not write"),
            lambda: 100,
        )

        self.assertIs(instance.check(), operator.OperatorStatus.ALREADY_PROVISIONED)
        self.assertIs(instance.apply(), operator.OperatorStatus.ALREADY_PROVISIONED)

    def test_partial_graph_is_rejected_before_authority_resolution(self) -> None:
        authority = FakeAuthority(_authority_result())
        inspector = operator.CurrentGraphInspector(
            FakeInventory((1, 1, 0, 0, 0)), authority
        )

        self.assertIs(inspector.inspect(), operator.GraphState.CONFLICT)
        self.assertEqual(authority.calls, [])

    def test_unrelated_or_second_global_graph_is_never_ready_or_exact(self) -> None:
        unrelated = operator.CurrentGraphInspector(
            FakeInventory((1, 1, 1, 1, 1), (1, 1, 1, 1, 1)),
            FakeAuthority(_not_authorized_result()),
        )
        second = operator.CurrentGraphInspector(
            FakeInventory((2, 2, 2, 2, 2)),
            FakeAuthority(_authority_result()),
        )

        self.assertIs(unrelated.inspect(), operator.GraphState.CONFLICT)
        self.assertIs(second.inspect(), operator.GraphState.CONFLICT)

    def test_email_issuer_and_subject_conflicts_are_rejected(self) -> None:
        conflicts = {
            "email": _authority_result(email="other@example.com"),
            "issuer": _authority_result(issuer="https://other.example.com/"),
            "subject": _authority_result(subject="email|other-subject"),
            "unresolved-claim": _not_authorized_result(),
        }
        for conflict_name, result in conflicts.items():
            with self.subTest(conflict=conflict_name):
                self.assertIs(
                    self._inspect((1, 1, 1, 1, 1), result),
                    operator.GraphState.CONFLICT,
                )

    def test_same_cardinality_graph_replacement_is_rejected(self) -> None:
        counts = (1, 1, 1, 1, 1)
        inspector = operator.CurrentGraphInspector(
            FakeInventory(
                (counts, "before-fingerprint"),
                (counts, "after-fingerprint"),
            ),
            FakeAuthority(_authority_result()),
        )

        self.assertIs(inspector.inspect(), operator.GraphState.CONFLICT)

    def test_second_active_workspace_is_rejected(self) -> None:
        self.assertIs(
            self._inspect((1, 1, 1, 2, 2), _authority_result()),
            operator.GraphState.CONFLICT,
        )

    def test_multiple_active_memberships_are_rejected(self) -> None:
        self.assertIs(
            self._inspect((1, 1, 1, 1, 2), _authority_result()),
            operator.GraphState.CONFLICT,
        )

    def test_wrong_authentication_method_is_rejected(self) -> None:
        self.assertIs(
            self._inspect(
                (1, 1, 1, 1, 1),
                _authority_result(method=models.AuthenticationMethod.OIDC),
            ),
            operator.GraphState.CONFLICT,
        )

    def test_conflicting_display_owner_and_creator_fail_closed(self) -> None:
        conflicts = (
            _authority_result(display_name="Other"),
            _authority_result(role=models.WorkspaceRole.ADMIN),
            _authority_result(workspace_creator=_identifier("usr_", 9)),
        )
        for result in conflicts:
            with self.subTest(result=result):
                self.assertIs(
                    self._inspect((1, 1, 1, 1, 1), result),
                    operator.GraphState.CONFLICT,
                )

    def test_later_valid_security_and_row_versions_remain_provisioned(self) -> None:
        self.assertIs(
            self._inspect(
                (1, 1, 1, 1, 1),
                _authority_result(security_epoch=2, row_version=4),
            ),
            operator.GraphState.EXACT,
        )


class SecretSafetyTests(unittest.TestCase):
    def test_database_url_and_exception_values_never_reach_output(self) -> None:
        sensitive = (
            "postgresql://cuevion_preview_migrator:private-password@"
            "ep-private.neon.tech/neondb?sslmode=require&channel_binding=require"
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        def failing_factory():
            raise RuntimeError(sensitive)

        exit_code = operator._run(["check"], failing_factory, stdout, stderr)
        combined = stdout.getvalue() + stderr.getvalue()

        self.assertEqual(exit_code, 1)
        self.assertEqual(combined, "CONFLICT — NO CHANGES MADE\n")
        for fragment in (
            "postgresql://",
            "private-password",
            "ep-private",
            "neondb",
            "cuevion_preview_migrator",
        ):
            self.assertNotIn(fragment, combined)

    def test_apply_exception_is_unknown_and_never_claims_no_changes(self) -> None:
        sensitive = "postgresql://writer:private-password@private-host/neondb"

        class ExplodingOperator:
            def apply(self):
                raise RuntimeError(sensitive)

        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = operator._run(
            ["apply", operator._APPLY_CONFIRMATION],
            ExplodingOperator,
            stdout,
            stderr,
        )
        combined = stdout.getvalue() + stderr.getvalue()

        self.assertEqual(exit_code, 1)
        self.assertEqual(combined, "STATE UNKNOWN — DO NOT RETRY\n")
        self.assertNotIn("private-password", combined)
        self.assertNotIn("private-host", combined)

    def test_rejected_writer_connection_is_closed_when_metadata_raises(self) -> None:
        host = "ep-unit-test.neon.tech"
        expected_hash = hashlib.sha256(host.encode("ascii")).hexdigest()
        value = (
            "postgresql://cuevion_preview_migrator:private-password@"
            f"{host}/neondb?sslmode=require&channel_binding=require"
        )

        class Candidate:
            autocommit = False

            def __init__(self) -> None:
                self.closes = 0

            @property
            def info(self):
                raise RuntimeError("private-password")

            def close(self) -> None:
                self.closes += 1

        candidate = Candidate()
        with mock.patch.object(
            operator, "_EXPECTED_PREVIEW_HOST_SHA256", expected_hash
        ):
            parsed = operator._parse_writer_database_url(value)
            factory = operator.WriterConnectionFactory(
                parsed,
                lambda *_args, **_kwargs: candidate,
            )
            with self.assertRaises(operator.OperatorFailure):
                factory()

        self.assertEqual(candidate.closes, 1)

    def test_writer_url_and_factory_representations_are_redacted(self) -> None:
        host = "ep-unit-test.neon.tech"
        expected_hash = hashlib.sha256(host.encode("ascii")).hexdigest()
        value = (
            "postgresql://cuevion_preview_migrator:private-password@"
            f"{host}/neondb?sslmode=require&channel_binding=require"
        )
        with mock.patch.object(
            operator, "_EXPECTED_PREVIEW_HOST_SHA256", expected_hash
        ):
            parsed = operator._parse_writer_database_url(value)
            factory = operator.WriterConnectionFactory(
                parsed,
                lambda *_args, **_kwargs: None,
            )

        self.assertEqual(str(parsed), "WriterDatabaseUrl(<redacted>)")
        self.assertEqual(repr(parsed), "WriterDatabaseUrl(<redacted>)")
        self.assertEqual(repr(factory), "WriterConnectionFactory(<redacted>)")
        for rendered in (str(parsed), repr(parsed), repr(factory)):
            self.assertNotIn("private-password", rendered)
            self.assertNotIn(host, rendered)

    def test_permanent_reader_url_is_never_accepted_as_writer(self) -> None:
        host = "ep-unit-test.neon.tech"
        expected_hash = hashlib.sha256(host.encode("ascii")).hexdigest()
        value = (
            "postgresql://cuevion_preview_current_account_reader_v1:password@"
            f"{host}/neondb?sslmode=require&channel_binding=require"
        )
        with mock.patch.object(
            operator, "_EXPECTED_PREVIEW_HOST_SHA256", expected_hash
        ):
            with self.assertRaises(operator.OperatorFailure):
                operator._parse_writer_database_url(value)


class InactivityTests(unittest.TestCase):
    def test_module_has_no_manual_insert_or_import_time_activation(self) -> None:
        source = _SOURCE.read_text(encoding="utf-8")
        normalized = source.casefold()
        self.assertNotIn("insert into", normalized)
        self.assertNotIn("os.environ", normalized)
        self.assertNotIn("getenv(", normalized)
        self.assertIn('if __name__ == "__main__":', source)
        self.assertIn(
            "postgresql_initial_account_repository.PostgreSQLInitialAccountRepository",
            source,
        )
        sql = operator._SELECT_CURRENT_AUTHORITY_SNAPSHOT_SQL
        for table in (
            "users",
            "verified_emails",
            "authentication_identities",
            "workspaces",
            "workspace_memberships",
        ):
            self.assertIn(f"cuevion_account.{table}", sql)
        self.assertNotIn("%s", sql)
        self.assertNotIn(" WHERE ", " ".join(sql.split()).upper())
        self.assertNotIn("initial_account_operations", sql)
        self.assertNotIn("security_events", sql)


if __name__ == "__main__":
    unittest.main()
