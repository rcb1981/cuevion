"""Fake-connection and security tests for the inactive PostgreSQL adapter."""

import ast
import base64
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
import inspect
import os
from pathlib import Path
import pickle
import re
import subprocess
import sys
import types
import typing
import unittest

import psycopg

from api.auth import models
from cuevion_auth import account_repository_contract as contract
from cuevion_db import postgresql_initial_account_repository as repository


_FRONTEND = Path(__file__).resolve().parents[2]
_REPOSITORY = _FRONTEND.parent
_SOURCE = (
    _FRONTEND / "cuevion_db" / "postgresql_initial_account_repository.py"
)
_DOCUMENTATION = (
    _FRONTEND
    / "cuevion_db"
    / "POSTGRESQL_INITIAL_ACCOUNT_REPOSITORY_ACTIVATION_REQUIREMENTS.md"
)


def _b64(octet: int, length: int) -> str:
    return base64.urlsafe_b64encode(bytes((octet,)) * length).rstrip(b"=").decode(
        "ascii"
    )


USER_ID = "usr_" + _b64(1, 16)
EMAIL_ID = "vem_" + _b64(2, 16)
IDENTITY_ID = "aid_" + _b64(3, 16)
WORKSPACE_ID = "wsp_" + _b64(4, 16)
EVENT_ID = "sev_" + _b64(5, 16)
OPERATION_DIGEST = _b64(6, 32)
ASSERTION_ID = _b64(7, 32)
TRUST_DOMAIN = "production.eu"
COORDINATOR = "initial-account-coordinator:v1"
EMAIL = "initial.owner@example.test"
ISSUER = "https://identity.example.test/tenant"
SUBJECT = "opaque-subject-A"
SENSITIVE_MARKERS = (
    USER_ID,
    EMAIL_ID,
    IDENTITY_ID,
    WORKSPACE_ID,
    EVENT_ID,
    OPERATION_DIGEST,
    ASSERTION_ID,
    TRUST_DOMAIN,
    COORDINATOR,
    EMAIL,
    ISSUER,
    SUBJECT,
)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


_INSERT_COLUMN_ORACLE = {
    "users": (
        "schema_version",
        "user_id",
        "status",
        "primary_verified_email_id",
        "display_name",
        "security_epoch",
        "created_at",
        "updated_at",
        "row_version",
    ),
    "verified_emails": (
        "schema_version",
        "email_id",
        "user_id",
        "canonical_email",
        "status",
        "verification_source",
        "created_at",
        "verified_at",
        "retired_at",
        "row_version",
    ),
    "authentication_identities": (
        "schema_version",
        "identity_id",
        "user_id",
        "issuer",
        "subject",
        "authentication_method",
        "status",
        "verified_email_id",
        "created_at",
        "last_used_at",
        "row_version",
    ),
    "workspaces": (
        "schema_version",
        "workspace_id",
        "status",
        "created_by_user_id",
        "created_at",
        "updated_at",
        "row_version",
    ),
    "workspace_memberships": (
        "schema_version",
        "workspace_id",
        "user_id",
        "role",
        "status",
        "created_at",
        "updated_at",
        "row_version",
    ),
    "initial_account_operations": (
        "operation_record_version",
        "reference_schema_version",
        "derivation_key_epoch",
        "operation_digest",
        "request_snapshot_version",
        "request_version",
        "snapshot_user_schema_version",
        "snapshot_user_user_id",
        "snapshot_user_status",
        "snapshot_user_primary_verified_email_id",
        "snapshot_user_display_name",
        "snapshot_user_security_epoch",
        "snapshot_user_created_at",
        "snapshot_user_updated_at",
        "snapshot_user_row_version",
        "snapshot_verified_email_schema_version",
        "snapshot_verified_email_email_id",
        "snapshot_verified_email_user_id",
        "snapshot_verified_email_canonical_email",
        "snapshot_verified_email_status",
        "snapshot_verified_email_verification_source",
        "snapshot_verified_email_created_at",
        "snapshot_verified_email_verified_at",
        "snapshot_verified_email_retired_at",
        "snapshot_verified_email_row_version",
        "snapshot_authentication_identity_schema_version",
        "snapshot_authentication_identity_identity_id",
        "snapshot_authentication_identity_user_id",
        "snapshot_authentication_identity_issuer",
        "snapshot_authentication_identity_subject",
        "snapshot_authentication_identity_authentication_method",
        "snapshot_authentication_identity_status",
        "snapshot_authentication_identity_verified_email_id",
        "snapshot_authentication_identity_created_at",
        "snapshot_authentication_identity_last_used_at",
        "snapshot_authentication_identity_row_version",
        "snapshot_workspace_schema_version",
        "snapshot_workspace_workspace_id",
        "snapshot_workspace_status",
        "snapshot_workspace_created_by_user_id",
        "snapshot_workspace_created_at",
        "snapshot_workspace_updated_at",
        "snapshot_workspace_row_version",
        "snapshot_workspace_membership_schema_version",
        "snapshot_workspace_membership_workspace_id",
        "snapshot_workspace_membership_user_id",
        "snapshot_workspace_membership_role",
        "snapshot_workspace_membership_status",
        "snapshot_workspace_membership_created_at",
        "snapshot_workspace_membership_updated_at",
        "snapshot_workspace_membership_row_version",
        "snapshot_authentication_evidence_schema_version",
        "snapshot_authentication_evidence_trust_domain",
        "snapshot_authentication_evidence_verification_coordinator_id",
        "snapshot_authentication_evidence_assertion_id",
        "snapshot_authentication_evidence_issuer",
        "snapshot_authentication_evidence_subject",
        "snapshot_authentication_evidence_authentication_method",
        "snapshot_authentication_evidence_canonical_verified_email",
        "snapshot_authentication_evidence_verified_at",
        "snapshot_authentication_evidence_issued_at",
        "snapshot_authentication_evidence_expires_at",
        "snapshot_security_event_schema_version",
        "snapshot_security_event_event_id",
        "snapshot_security_event_event_type",
        "receipt_version",
        "receipt_user_id",
        "receipt_verified_email_id",
        "receipt_authentication_identity_id",
        "receipt_workspace_id",
        "receipt_security_event_id",
        "committed_at",
        "row_version",
    ),
    "security_events": (
        "event_record_version",
        "event_payload_version",
        "event_id",
        "event_type",
        "reference_schema_version",
        "derivation_key_epoch",
        "operation_digest",
        "actor_trust_domain",
        "actor_verification_coordinator_id",
        "user_id",
        "verified_email_id",
        "authentication_identity_id",
        "workspace_id",
        "membership_workspace_id",
        "membership_user_id",
        "security_epoch",
        "event_at",
        "recorded_at",
        "event_stream_name",
        "event_stream_position",
        "row_version",
    ),
}
_APPROVED_RELATIONS = frozenset(
    "cuevion_account." + relation for relation in _INSERT_COLUMN_ORACLE
)
_APPROVED_SEQUENCE = "cuevion_account.security_event_stream_position_seq"


def _dt(value: int) -> datetime:
    return _EPOCH + timedelta(seconds=value)


def _request(*, display_name: str = "Initial Owner") -> contract.InitialAccountCreationRequest:
    operation = contract.InitialAccountOperationReference(
        schema_version=1,
        derivation_key_epoch=1,
        operation_digest=OPERATION_DIGEST,
    )
    user = models.CuevionUser(
        schema_version=1,
        user_id=USER_ID,
        status=models.UserStatus.ACTIVE,
        primary_verified_email_id=EMAIL_ID,
        display_name=display_name,
        security_epoch=1,
        created_at=0,
        updated_at=1,
        row_version=1,
    )
    email = models.VerifiedEmail(
        schema_version=1,
        email_id=EMAIL_ID,
        user_id=USER_ID,
        canonical_email=EMAIL,
        status=models.VerifiedEmailStatus.VERIFIED,
        verification_source="trusted_coordinator",
        created_at=0,
        verified_at=1,
        retired_at=None,
        row_version=1,
    )
    identity = models.AuthenticationIdentity(
        schema_version=1,
        identity_id=IDENTITY_ID,
        user_id=USER_ID,
        issuer=ISSUER,
        subject=SUBJECT,
        method=models.AuthenticationMethod.OIDC,
        status=models.AuthenticationIdentityStatus.ACTIVE,
        verified_email_id=EMAIL_ID,
        created_at=1,
        last_used_at=None,
        row_version=1,
    )
    workspace = models.Workspace(
        schema_version=1,
        workspace_id=WORKSPACE_ID,
        status=models.WorkspaceStatus.ACTIVE,
        created_by_user_id=USER_ID,
        created_at=1,
        updated_at=1,
        row_version=1,
    )
    membership = models.WorkspaceMembership(
        schema_version=1,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        role=models.WorkspaceRole.OWNER,
        status=models.WorkspaceMembershipStatus.ACTIVE,
        created_at=1,
        updated_at=1,
        row_version=1,
    )
    evidence = contract.VerifiedAuthenticationEvidence(
        schema_version=1,
        trust_domain=TRUST_DOMAIN,
        verification_coordinator_id=COORDINATOR,
        assertion_id=ASSERTION_ID,
        issuer=ISSUER,
        subject=SUBJECT,
        authentication_method=models.AuthenticationMethod.OIDC,
        canonical_verified_email=EMAIL,
        verified_at=1,
        issued_at=2,
        expires_at=3,
    )
    event = contract.InitialSecurityEventRequest(
        schema_version=1,
        event_id=EVENT_ID,
        event_type=contract.InitialSecurityEventType.INITIAL_ACCOUNT_CREATED,
    )
    return contract.InitialAccountCreationRequest(
        request_version=1,
        operation_reference=operation,
        user=user,
        verified_email=email,
        authentication_identity=identity,
        workspace=workspace,
        workspace_membership=membership,
        authentication_evidence=evidence,
        security_event=event,
    )


def _context(
    request: contract.InitialAccountCreationRequest,
    *,
    trusted_now: int = 10,
    trust_domain: str = TRUST_DOMAIN,
) -> repository.InitialAccountWriteContext:
    return repository.InitialAccountWriteContext(
        context_version=1,
        trusted_now=trusted_now,
        operation_reference=request.operation_reference,
        evidence_assertion_id=request.authentication_evidence.assertion_id,
        trust_domain=trust_domain,
        verification_coordinator_id=(
            request.authentication_evidence.verification_coordinator_id
        ),
    )


def _receipt(request: contract.InitialAccountCreationRequest) -> contract.InitialAccountCreationReceipt:
    return contract.InitialAccountCreationReceipt(
        schema_version=1,
        user_id=request.user.user_id,
        verified_email_id=request.verified_email.email_id,
        authentication_identity_id=request.authentication_identity.identity_id,
        workspace_id=request.workspace.workspace_id,
        security_event_id=request.security_event.event_id,
    )


def _receipt_values(
    receipt: contract.InitialAccountCreationReceipt,
) -> tuple[object, ...]:
    return tuple(
        object.__getattribute__(receipt, field)
        for field in (
            "schema_version",
            "user_id",
            "verified_email_id",
            "authentication_identity_id",
            "workspace_id",
            "security_event_id",
        )
    )


def _user_row(request: contract.InitialAccountCreationRequest) -> tuple[object, ...]:
    user = request.user
    return (
        user.schema_version,
        user.user_id,
        user.status.value,
        user.primary_verified_email_id,
        user.display_name,
        user.security_epoch,
        _dt(user.created_at),
        _dt(user.updated_at),
        user.row_version,
    )


def _email_row(request: contract.InitialAccountCreationRequest) -> tuple[object, ...]:
    email = request.verified_email
    return (
        email.schema_version,
        email.email_id,
        email.user_id,
        email.canonical_email,
        email.status.value,
        email.verification_source,
        _dt(email.created_at),
        _dt(typing.cast(int, email.verified_at)),
        None,
        email.row_version,
    )


def _identity_row(request: contract.InitialAccountCreationRequest) -> tuple[object, ...]:
    identity = request.authentication_identity
    return (
        identity.schema_version,
        identity.identity_id,
        identity.user_id,
        identity.issuer,
        identity.subject,
        identity.method.value,
        identity.status.value,
        identity.verified_email_id,
        _dt(identity.created_at),
        None,
        identity.row_version,
    )


def _workspace_row(request: contract.InitialAccountCreationRequest) -> tuple[object, ...]:
    workspace = request.workspace
    return (
        workspace.schema_version,
        workspace.workspace_id,
        workspace.status.value,
        workspace.created_by_user_id,
        _dt(workspace.created_at),
        _dt(workspace.updated_at),
        workspace.row_version,
    )


def _membership_row(request: contract.InitialAccountCreationRequest) -> tuple[object, ...]:
    membership = request.workspace_membership
    return (
        membership.schema_version,
        membership.workspace_id,
        membership.user_id,
        membership.role.value,
        membership.status.value,
        _dt(membership.created_at),
        _dt(membership.updated_at),
        membership.row_version,
    )


def _operation_row(
    request: contract.InitialAccountCreationRequest,
    *,
    committed_at: int = 10,
) -> tuple[object, ...]:
    reference = request.operation_reference
    evidence = request.authentication_evidence
    event = request.security_event
    receipt = _receipt(request)
    return (
        1,
        reference.schema_version,
        reference.derivation_key_epoch,
        base64.urlsafe_b64decode(reference.operation_digest + "="),
        1,
        request.request_version,
        *_user_row(request),
        *_email_row(request),
        *_identity_row(request),
        *_workspace_row(request),
        *_membership_row(request),
        evidence.schema_version,
        evidence.trust_domain,
        evidence.verification_coordinator_id,
        base64.urlsafe_b64decode(evidence.assertion_id + "="),
        evidence.issuer,
        evidence.subject,
        evidence.authentication_method.value,
        evidence.canonical_verified_email,
        _dt(evidence.verified_at),
        _dt(evidence.issued_at),
        _dt(evidence.expires_at),
        event.schema_version,
        event.event_id,
        event.event_type.value,
        receipt.schema_version,
        receipt.user_id,
        receipt.verified_email_id,
        receipt.authentication_identity_id,
        receipt.workspace_id,
        receipt.security_event_id,
        _dt(committed_at),
        1,
    )


def _event_row(
    request: contract.InitialAccountCreationRequest,
    *,
    committed_at: int = 10,
    position: int = 41,
) -> tuple[object, ...]:
    reference = request.operation_reference
    evidence = request.authentication_evidence
    return (
        1,
        request.security_event.schema_version,
        request.security_event.event_id,
        request.security_event.event_type.value,
        reference.schema_version,
        reference.derivation_key_epoch,
        base64.urlsafe_b64decode(reference.operation_digest + "="),
        evidence.trust_domain,
        evidence.verification_coordinator_id,
        request.user.user_id,
        request.verified_email.email_id,
        request.authentication_identity.identity_id,
        request.workspace.workspace_id,
        request.workspace_membership.workspace_id,
        request.workspace_membership.user_id,
        request.user.security_epoch,
        _dt(committed_at),
        _dt(committed_at),
        "cuevion.account.security",
        position,
        1,
    )


_SQL_KEYS = {
    repository._SET_TRANSACTION_SQL: "set_transaction",
    repository._SET_CONSTRAINTS_DEFERRED_SQL: "deferred",
    repository._SET_CONSTRAINTS_IMMEDIATE_SQL: "immediate",
    repository._ADVISORY_LOCK_SQL: "lock",
    repository._NEXT_EVENT_POSITION_SQL: "sequence",
    repository._SELECT_OPERATION_SQL: "operation",
    repository._SELECT_SECURITY_EVENT_SQL: "stored_event",
    repository._SELECT_EVIDENCE_CLAIM_SQL: "evidence",
    repository._SELECT_EMAIL_AUTHORITY_SQL: "email_authority",
    repository._SELECT_IDENTITY_AUTHORITY_SQL: "identity_authority",
    repository._SELECT_USER_COLLISION_SQL: "user_collision",
    repository._SELECT_EMAIL_COLLISION_SQL: "email_collision",
    repository._SELECT_IDENTITY_COLLISION_SQL: "identity_collision",
    repository._SELECT_WORKSPACE_COLLISION_SQL: "workspace_collision",
    repository._SELECT_MEMBERSHIP_COLLISION_SQL: "membership_collision",
    repository._SELECT_EVENT_COLLISION_SQL: "event_collision",
    repository._INSERT_USER_SQL: "insert_users",
    repository._INSERT_VERIFIED_EMAIL_SQL: "insert_verified_emails",
    repository._INSERT_AUTHENTICATION_IDENTITY_SQL: (
        "insert_authentication_identities"
    ),
    repository._INSERT_WORKSPACE_SQL: "insert_workspaces",
    repository._INSERT_WORKSPACE_MEMBERSHIP_SQL: "insert_workspace_memberships",
    repository._INSERT_OPERATION_SQL: "insert_initial_account_operations",
    repository._INSERT_SECURITY_EVENT_SQL: "insert_security_events",
}


def _statement_key(sql: str) -> str:
    if type(sql) is not str or sql not in _SQL_KEYS:
        raise AssertionError("unscripted SQL")
    return _SQL_KEYS[sql]


_NO_RESULT = object()


class ScriptedStep:
    __slots__ = ("key", "parameters", "rows", "failure")

    def __init__(
        self,
        key: str,
        parameters: tuple[object, ...] | None,
        rows: object = _NO_RESULT,
        failure: BaseException | None = None,
    ) -> None:
        self.key = key
        self.parameters = parameters
        self.rows = rows
        self.failure = failure


class ScriptedCursor:
    def __init__(self, connection: "ScriptedConnection") -> None:
        self.connection = connection
        self.closed = False
        self.pending: object = _NO_RESULT

    def execute(
        self, sql: str, parameters: tuple[object, ...] | None = None
    ) -> None:
        if self.closed:
            raise AssertionError("execute on closed cursor")
        if type(sql) is not str:
            raise AssertionError("SQL must be a fixed string")
        if parameters is not None and type(parameters) is not tuple:
            raise AssertionError("parameters must be an exact tuple")
        placeholder_count = sql.count("%s")
        if placeholder_count != (0 if parameters is None else len(parameters)):
            raise AssertionError("SQL parameter count mismatch")
        if self.pending is not _NO_RESULT:
            raise AssertionError("result was not fetched before next execute")
        key = _statement_key(sql)
        if not self.connection.script:
            raise AssertionError("unexpected scripted SQL call")
        step = self.connection.script.pop(0)
        if step.key != key or step.parameters != parameters:
            raise AssertionError(
                f"unexpected scripted SQL call: {key} {parameters!r}"
            )
        self.connection.calls.append((key, sql, parameters))
        if step.failure is not None:
            raise step.failure
        self.pending = step.rows

    def fetchall(self) -> list[tuple[object, ...]]:
        if self.closed:
            raise AssertionError("fetch on closed cursor")
        if self.pending is _NO_RESULT:
            raise AssertionError("fetchall without a result-producing statement")
        rows = self.pending
        self.pending = _NO_RESULT
        return typing.cast(list[tuple[object, ...]], rows)

    def close(self) -> None:
        if self.closed:
            raise AssertionError("cursor closed twice")
        self.connection.cursor_close_count += 1
        if self.connection.cursor_close_failure is not None:
            raise self.connection.cursor_close_failure
        self.closed = True


class ScriptedConnection:
    def __init__(
        self,
        script: list[ScriptedStep],
        *,
        commit_failure: BaseException | None = None,
        cursor_failure: BaseException | None = None,
        cursor_close_failure: BaseException | None = None,
        rollback_failure: BaseException | None = None,
        close_failure: BaseException | None = None,
        expected_commits: int = 0,
        expected_rollbacks: int = 1,
        autocommit: bool = False,
    ) -> None:
        self.autocommit = autocommit
        self.script = list(script)
        self.commit_failure = commit_failure
        self.cursor_failure = cursor_failure
        self.cursor_close_failure = cursor_close_failure
        self.rollback_failure = rollback_failure
        self.close_failure = close_failure
        self.expected_commits = expected_commits
        self.expected_rollbacks = expected_rollbacks
        self.calls: list[tuple[str, str, tuple[object, ...] | None]] = []
        self.cursors: list[ScriptedCursor] = []
        self.cursor_close_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def cursor(self) -> ScriptedCursor:
        if self.cursor_failure is not None:
            raise self.cursor_failure
        cursor = ScriptedCursor(self)
        self.cursors.append(cursor)
        return cursor

    def commit(self) -> None:
        self.commit_count += 1
        if self.commit_count > self.expected_commits:
            raise AssertionError("unexpected commit")
        if self.commit_failure is not None:
            raise self.commit_failure

    def rollback(self) -> None:
        self.rollback_count += 1
        if self.rollback_count > self.expected_rollbacks:
            raise AssertionError("unexpected rollback")
        if self.rollback_failure is not None:
            raise self.rollback_failure

    def close(self) -> None:
        self.close_count += 1
        if self.close_count > 1:
            raise AssertionError("connection closed twice")
        if self.close_failure is not None:
            raise self.close_failure

    def assert_exhausted(self) -> None:
        if self.script:
            raise AssertionError(
                "unconsumed scripted SQL: "
                + ", ".join(step.key for step in self.script)
            )
        if self.commit_count != self.expected_commits:
            raise AssertionError("expected commit count was not reached")
        if self.rollback_count != self.expected_rollbacks:
            raise AssertionError("expected rollback count was not reached")
        if self.close_count != 1:
            raise AssertionError("connection was not closed exactly once")


class ConnectionFactory:
    def __init__(self, *connections: object) -> None:
        self.connections = list(connections)
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        if not self.connections:
            raise AssertionError("unexpected connection request")
        connection = self.connections.pop(0)
        if isinstance(connection, BaseException):
            raise connection
        return connection


class Authorizer:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls = 0

    def authorize_new_operation(
        self, request: contract.InitialAccountCreationRequest
    ) -> object:
        self.calls += 1
        if isinstance(self.value, BaseException):
            raise self.value
        if callable(self.value):
            return self.value(request)
        return self.value


def _digest_bytes(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((-len(value)) % 4))


def _operation_parameters(
    request: contract.InitialAccountCreationRequest,
) -> tuple[object, ...]:
    reference = request.operation_reference
    return (
        reference.schema_version,
        reference.derivation_key_epoch,
        _digest_bytes(reference.operation_digest),
    )


def _lock_material(domain: str, *values: str) -> str:
    pieces = ["cuevion-initial-account-lock-v1", domain]
    for value in values:
        pieces.append(f"{len(value.encode('utf-8'))}:{value}")
    return "\x1f".join(pieces)


def _lock_materials(
    request: contract.InitialAccountCreationRequest,
) -> tuple[str, ...]:
    reference = request.operation_reference
    evidence = request.authentication_evidence
    identity = request.authentication_identity
    return (
        _lock_material(
            "operation-reference",
            str(reference.schema_version),
            str(reference.derivation_key_epoch),
            reference.operation_digest,
        ),
        _lock_material(
            "evidence-assertion-claim",
            evidence.trust_domain,
            evidence.verification_coordinator_id,
            evidence.assertion_id,
        ),
        _lock_material(
            "canonical-verified-email-authority-claim",
            request.verified_email.canonical_email,
        ),
        _lock_material(
            "issuer-subject-identity-authority-claim",
            identity.issuer,
            identity.subject,
        ),
        _lock_material("candidate-user-id", request.user.user_id),
        _lock_material(
            "candidate-verified-email-id", request.verified_email.email_id
        ),
        _lock_material(
            "candidate-authentication-identity-id", identity.identity_id
        ),
        _lock_material("candidate-workspace-id", request.workspace.workspace_id),
        _lock_material(
            "candidate-membership-pair",
            request.workspace_membership.workspace_id,
            request.workspace_membership.user_id,
        ),
        _lock_material(
            "candidate-security-event-id", request.security_event.event_id
        ),
    )


def _initial_steps(
    request: contract.InitialAccountCreationRequest,
    operation_rows: list[tuple[object, ...]],
) -> list[ScriptedStep]:
    return [
        ScriptedStep("set_transaction", None),
        ScriptedStep("lock", (_lock_materials(request)[0],), [(None,)]),
        ScriptedStep(
            "operation", _operation_parameters(request), operation_rows
        ),
    ]


def _stored_steps(
    request: contract.InitialAccountCreationRequest,
    *,
    operation_row: tuple[object, ...] | None = None,
    event_row: tuple[object, ...] | None = None,
) -> list[ScriptedStep]:
    return _initial_steps(request, [operation_row or _operation_row(request)]) + [
        ScriptedStep(
            "stored_event",
            _operation_parameters(request),
            [event_row or _event_row(request)],
        )
    ]


def _authorized_prefix_steps(
    request: contract.InitialAccountCreationRequest,
    second_operation_rows: list[tuple[object, ...]] | None = None,
) -> list[ScriptedStep]:
    steps = _initial_steps(request, [])
    steps.extend(
        ScriptedStep("lock", (material,), [(None,)])
        for material in _lock_materials(request)[1:]
    )
    steps.append(
        ScriptedStep(
            "operation",
            _operation_parameters(request),
            second_operation_rows or [],
        )
    )
    return steps


def _conflict_query_steps(
    request: contract.InitialAccountCreationRequest,
    *,
    evidence: object = False,
    email_authority: object = False,
    identity_authority: object = False,
    user_collision: object = False,
    email_collision: object = False,
    identity_collision: object = False,
    workspace_collision: object = False,
    membership_collision: object = False,
    event_collision: object = False,
) -> list[ScriptedStep]:
    evidence_parameters = (
        request.authentication_evidence.trust_domain,
        request.authentication_evidence.verification_coordinator_id,
        _digest_bytes(request.authentication_evidence.assertion_id),
    )
    specifications = (
        ("evidence", evidence_parameters, evidence),
        (
            "email_authority",
            (request.verified_email.canonical_email,),
            email_authority,
        ),
        (
            "identity_authority",
            (
                request.authentication_identity.issuer,
                request.authentication_identity.subject,
            ),
            identity_authority,
        ),
        ("user_collision", (request.user.user_id,), user_collision),
        (
            "email_collision",
            (request.verified_email.email_id,),
            email_collision,
        ),
        (
            "identity_collision",
            (request.authentication_identity.identity_id,),
            identity_collision,
        ),
        (
            "workspace_collision",
            (request.workspace.workspace_id,),
            workspace_collision,
        ),
        (
            "membership_collision",
            (
                request.workspace_membership.workspace_id,
                request.workspace_membership.user_id,
            ),
            membership_collision,
        ),
        (
            "event_collision",
            (request.security_event.event_id,),
            event_collision,
        ),
    )
    steps: list[ScriptedStep] = []
    for key, parameters, value in specifications:
        rows = value if type(value) is list else [(value,)]
        steps.append(ScriptedStep(key, parameters, rows))
        if value is True:
            break
    return steps


def _create_steps(
    request: contract.InitialAccountCreationRequest,
    *,
    trusted_now: int = 10,
    position: int = 41,
) -> list[ScriptedStep]:
    return [
        ScriptedStep("deferred", None),
        ScriptedStep("sequence", (), [(position,)]),
        ScriptedStep("insert_users", _user_row(request)),
        ScriptedStep("insert_verified_emails", _email_row(request)),
        ScriptedStep(
            "insert_authentication_identities", _identity_row(request)
        ),
        ScriptedStep("insert_workspaces", _workspace_row(request)),
        ScriptedStep("insert_workspace_memberships", _membership_row(request)),
        ScriptedStep(
            "insert_initial_account_operations",
            _operation_row(request, committed_at=trusted_now),
        ),
        ScriptedStep(
            "insert_security_events",
            _event_row(request, committed_at=trusted_now, position=position),
        ),
        ScriptedStep("immediate", None),
    ]


def _successful_create_steps(
    request: contract.InitialAccountCreationRequest,
) -> list[ScriptedStep]:
    return (
        _authorized_prefix_steps(request)
        + _conflict_query_steps(request)
        + _create_steps(request)
    )


def _steps_through_failure(
    steps: list[ScriptedStep], key: str, failure: BaseException
) -> list[ScriptedStep]:
    for index, step in enumerate(steps):
        if step.key == key:
            step.failure = failure
            return steps[: index + 1]
    raise AssertionError("failure target is absent from script")


def _sql_without_literals(sql: str) -> str:
    if "--" in sql or "/*" in sql or "*/" in sql or ";" in sql:
        raise AssertionError("comments and statement separators are forbidden")
    output: list[str] = []
    index = 0
    in_literal = False
    while index < len(sql):
        character = sql[index]
        if character == "'":
            if in_literal and index + 1 < len(sql) and sql[index + 1] == "'":
                output.extend((" ", " "))
                index += 2
                continue
            in_literal = not in_literal
            output.append(" ")
        else:
            output.append(" " if in_literal else character)
        index += 1
    if in_literal:
        raise AssertionError("unterminated SQL literal")
    return "".join(output)


def _audit_sql_surface(sql_values: tuple[str, ...]) -> set[str]:
    seen_relations: set[str] = set()
    seen_sequences: list[str] = []
    for sql in sql_values:
        if type(sql) is not str:
            raise AssertionError("SQL must be an exact built-in string")
        scrubbed = _sql_without_literals(sql)
        normalized = " ".join(scrubbed.split()).casefold()
        forbidden_patterns = (
            r"\bselect\s+\*",
            r"\bon\s+conflict\b",
            r"\breturning\b",
            r"\bcreate\b",
            r"\balter\b",
            r"\bdrop\b",
            r"\bgrant\b",
            r"\brevoke\b",
        )
        if any(re.search(pattern, normalized) for pattern in forbidden_patterns):
            raise AssertionError("forbidden SQL construct")
        targets = re.findall(
            r"\b(?:from|join|into|update|table)\s+"
            r"([a-z_][a-z0-9_]*(?:[.][a-z_][a-z0-9_]*)?)\b",
            normalized,
        )
        for target in targets:
            if "." not in target or target not in _APPROVED_RELATIONS:
                raise AssertionError("unapproved SQL relation")
            seen_relations.add(target)
        qualified_identifiers = set(
            re.findall(
                r"\b[a-z_][a-z0-9_]*[.][a-z_][a-z0-9_]*\b",
                normalized,
            )
        )
        if not qualified_identifiers.issubset(_APPROVED_RELATIONS):
            raise AssertionError("unapproved schema-qualified identifier")
        sequence_references = re.findall(
            r"\bnextval\s*\(\s*'([^']+)'\s*\)", sql, flags=re.IGNORECASE
        )
        if any(reference != _APPROVED_SEQUENCE for reference in sequence_references):
            raise AssertionError("unapproved sequence")
        seen_sequences.extend(sequence_references)
    if seen_relations != set(_APPROVED_RELATIONS):
        raise AssertionError("approved relation surface is incomplete")
    if seen_sequences != [_APPROVED_SEQUENCE]:
        raise AssertionError("sequence surface is not exact")
    return seen_relations


def _parse_insert(sql: str) -> tuple[str, tuple[str, ...], int]:
    match = re.fullmatch(
        r"\s*insert\s+into\s+cuevion_account[.]([a-z_][a-z0-9_]*)"
        r"\s*\(([^()]*)\)\s*values\s*\(([^()]*)\)\s*",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        raise AssertionError("INSERT does not match the fixed grammar")
    relation = match.group(1).casefold()
    columns = tuple(column.strip().casefold() for column in match.group(2).split(","))
    placeholders = tuple(value.strip() for value in match.group(3).split(","))
    if any(value != "%s" for value in placeholders):
        raise AssertionError("INSERT contains a non-parameter value")
    return relation, columns, len(placeholders)


def _repository(
    factory: ConnectionFactory, authorizer: Authorizer
) -> repository.PostgreSQLInitialAccountRepository:
    return repository.PostgreSQLInitialAccountRepository(factory, authorizer)


class PostgreSQLInitialAccountRepositoryTests(unittest.TestCase):
    def assert_result(
        self,
        result: contract.InitialAccountCreationResult,
        outcome: contract.InitialAccountCreationOutcome,
        reason: contract.InitialAccountConflictReason | None = None,
    ) -> None:
        self.assertIs(result.outcome, outcome)
        self.assertIs(result.conflict_reason, reason)
        if outcome not in {
            contract.InitialAccountCreationOutcome.CREATED,
            contract.InitialAccountCreationOutcome.EXACT_REPLAY,
        }:
            self.assertIsNone(result.receipt)

    def assert_cleaned(self, *connections: ScriptedConnection) -> None:
        for connection in connections:
            self.assertEqual(connection.close_count, 1)
            self.assertTrue(all(cursor.closed for cursor in connection.cursors))
            connection.assert_exhausted()

    def test_construction_performs_no_io_or_dependency_call(self):
        factory = ConnectionFactory()
        authorizer = Authorizer(None)
        instance = _repository(factory, authorizer)
        self.assertIs(type(instance), repository.PostgreSQLInitialAccountRepository)
        self.assertEqual(factory.calls, 0)
        self.assertEqual(authorizer.calls, 0)

    def test_invalid_request_escapes_fixed_contract_error_before_io(self):
        factory = ConnectionFactory()
        authorizer = Authorizer(None)
        with self.assertRaises(contract.AccountRepositoryContractValidationError) as caught:
            _repository(factory, authorizer).create_initial_account(object())  # type: ignore[arg-type]
        self.assertEqual(caught.exception.args, ())
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(factory.calls, 0)
        self.assertEqual(authorizer.calls, 0)

    def test_exact_replay_uses_snapshot_and_historical_receipt_without_authorizer(self):
        request = _request()
        connection = ScriptedConnection(_stored_steps(request))
        factory = ConnectionFactory(connection)
        authorizer = Authorizer(AssertionError("must not authorize replay"))
        result = _repository(factory, authorizer).create_initial_account(request)
        self.assert_result(result, contract.InitialAccountCreationOutcome.EXACT_REPLAY)
        self.assertEqual(authorizer.calls, 0)
        self.assertEqual(result.receipt.security_event_id, EVENT_ID)
        self.assertFalse(any(key.startswith("insert_") for key, *_ in connection.calls))
        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assert_cleaned(connection)

    def test_exact_replay_ignores_valid_current_user_display_name_mutation(self):
        request = _request()
        current_user = models.CuevionUser(
            schema_version=1,
            user_id=USER_ID,
            status=models.UserStatus.ACTIVE,
            primary_verified_email_id=EMAIL_ID,
            display_name="Current Display Name",
            security_epoch=1,
            created_at=0,
            updated_at=2,
            row_version=2,
        )
        connection = ScriptedConnection(_stored_steps(request))
        connection.current_rows = {"users": current_user}
        authorizer = Authorizer(AssertionError("must not authorize replay"))
        result = _repository(
            ConnectionFactory(connection), authorizer
        ).create_initial_account(request)
        self.assert_result(
            result, contract.InitialAccountCreationOutcome.EXACT_REPLAY
        )
        self.assertEqual(
            _receipt_values(typing.cast(contract.InitialAccountCreationReceipt, result.receipt)),
            _receipt_values(_receipt(request)),
        )
        self.assertEqual(authorizer.calls, 0)
        self.assertNotIn("sequence", [key for key, *_ in connection.calls])
        self.assertFalse(
            any(key.startswith("insert_") for key, *_ in connection.calls)
        )
        self.assert_cleaned(connection)

    def test_exact_replay_ignores_valid_mutations_on_all_current_relations(self):
        request = _request()
        current_rows = {
            "users": models.CuevionUser(
                1,
                USER_ID,
                models.UserStatus.SUSPENDED,
                EMAIL_ID,
                "Current Owner",
                2,
                0,
                2,
                2,
            ),
            "verified_emails": models.VerifiedEmail(
                1,
                EMAIL_ID,
                USER_ID,
                EMAIL,
                models.VerifiedEmailStatus.RETIRED,
                "trusted_coordinator",
                0,
                1,
                2,
                2,
            ),
            "authentication_identities": models.AuthenticationIdentity(
                1,
                IDENTITY_ID,
                USER_ID,
                ISSUER,
                SUBJECT,
                models.AuthenticationMethod.OIDC,
                models.AuthenticationIdentityStatus.REVOKED,
                EMAIL_ID,
                1,
                2,
                2,
            ),
            "workspaces": models.Workspace(
                1,
                WORKSPACE_ID,
                models.WorkspaceStatus.ARCHIVED,
                USER_ID,
                1,
                2,
                2,
            ),
            "workspace_memberships": models.WorkspaceMembership(
                1,
                WORKSPACE_ID,
                USER_ID,
                models.WorkspaceRole.MEMBER,
                models.WorkspaceMembershipStatus.REMOVED,
                1,
                2,
                2,
            ),
        }
        for relation, current_row in current_rows.items():
            with self.subTest(relation=relation):
                connection = ScriptedConnection(_stored_steps(request))
                connection.current_rows = {relation: current_row}
                authorizer = Authorizer(
                    AssertionError("must not authorize historical replay")
                )
                result = _repository(
                    ConnectionFactory(connection), authorizer
                ).create_initial_account(request)
                self.assert_result(
                    result, contract.InitialAccountCreationOutcome.EXACT_REPLAY
                )
                self.assertEqual(
                    _receipt_values(
                        typing.cast(
                            contract.InitialAccountCreationReceipt,
                            result.receipt,
                        )
                    ),
                    _receipt_values(_receipt(request)),
                )
                self.assertEqual(authorizer.calls, 0)
                self.assertEqual(
                    [key for key, *_ in connection.calls],
                    ["set_transaction", "lock", "operation", "stored_event"],
                )
                self.assert_cleaned(connection)

    def test_operation_mismatch_remains_conflict_after_current_mutation(self):
        incoming = _request()
        stored = _request(display_name="Frozen Historical Owner")
        connection = ScriptedConnection(_stored_steps(stored))
        connection.current_rows = {
            "users": models.CuevionUser(
                1,
                USER_ID,
                models.UserStatus.ACTIVE,
                EMAIL_ID,
                incoming.user.display_name,
                1,
                0,
                2,
                2,
            )
        }
        authorizer = Authorizer(AssertionError("must not authorize mismatch"))
        result = _repository(
            ConnectionFactory(connection), authorizer
        ).create_initial_account(incoming)
        self.assert_result(
            result,
            contract.InitialAccountCreationOutcome.CONFLICT,
            contract.InitialAccountConflictReason.OPERATION_REFERENCE_MISMATCH,
        )
        self.assertEqual(authorizer.calls, 0)
        self.assert_cleaned(connection)

    def test_operation_mismatch_precedes_authorizer_and_performs_no_write(self):
        incoming = _request()
        stored = _request(display_name="Different Initial Owner")
        connection = ScriptedConnection(_stored_steps(stored))
        authorizer = Authorizer(AssertionError("must not authorize mismatch"))
        result = _repository(ConnectionFactory(connection), authorizer).create_initial_account(incoming)
        self.assert_result(
            result,
            contract.InitialAccountCreationOutcome.CONFLICT,
            contract.InitialAccountConflictReason.OPERATION_REFERENCE_MISMATCH,
        )
        self.assertEqual(authorizer.calls, 0)
        self.assertFalse(any(key.startswith("insert_") for key, *_ in connection.calls))
        self.assert_cleaned(connection)

    def test_corrupt_stored_snapshot_is_internal_error(self):
        request = _request()
        row = list(_operation_row(request))
        row[0] = 2
        connection = ScriptedConnection(_initial_steps(request, [tuple(row)]))
        authorizer = Authorizer(AssertionError("must not authorize corruption"))
        result = _repository(ConnectionFactory(connection), authorizer).create_initial_account(request)
        self.assert_result(result, contract.InitialAccountCreationOutcome.INTERNAL_ERROR)
        self.assertEqual(authorizer.calls, 0)
        self.assert_cleaned(connection)

    def test_non_utc_or_fractional_stored_timestamp_is_corruption(self):
        request = _request()
        for timestamp in (
            datetime(1970, 1, 1),
            datetime(1970, 1, 1, microsecond=1, tzinfo=timezone.utc),
            datetime(1970, 1, 1, tzinfo=timezone(timedelta(hours=1))),
        ):
            row = list(_operation_row(request))
            row[12] = timestamp
            connection = ScriptedConnection(
                _initial_steps(request, [tuple(row)])
            )
            result = _repository(
                ConnectionFactory(connection), Authorizer(None)
            ).create_initial_account(request)
            self.assert_result(result, contract.InitialAccountCreationOutcome.INTERNAL_ERROR)
            self.assert_cleaned(connection)

    def test_stored_operation_rejects_scalar_subclasses_and_malformed_rows(self):
        request = _request()

        class IntSubclass(int):
            pass

        class BytesSubclass(bytes):
            pass

        class DatetimeSubclass(datetime):
            pass

        mutations: tuple[object, ...] = (
            (2, IntSubclass(1)),
            (3, BytesSubclass(_digest_bytes(OPERATION_DIGEST))),
            (
                12,
                DatetimeSubclass(1970, 1, 1, tzinfo=timezone.utc),
            ),
            "wrong_width",
            "list_row",
        )
        for mutation in mutations:
            with self.subTest(mutation=repr(mutation)):
                row = list(_operation_row(request))
                if mutation == "wrong_width":
                    rows: object = [tuple(row[:-1])]
                elif mutation == "list_row":
                    rows = [row]
                else:
                    index, value = typing.cast(tuple[int, object], mutation)
                    row[index] = value
                    rows = [tuple(row)]
                steps = _initial_steps(request, [])
                steps[-1].rows = rows
                connection = ScriptedConnection(steps)
                result = _repository(
                    ConnectionFactory(connection), Authorizer(None)
                ).create_initial_account(request)
                self.assert_result(
                    result, contract.InitialAccountCreationOutcome.INTERNAL_ERROR
                )
                self.assert_cleaned(connection)

    def test_corrupt_stored_event_and_stream_subclass_are_internal_error(self):
        request = _request()

        class TextSubclass(str):
            pass

        events = []
        corrupt_binding = list(_event_row(request))
        corrupt_binding[9] = "usr_" + _b64(9, 16)
        events.append(tuple(corrupt_binding))
        subclass_stream = list(_event_row(request))
        subclass_stream[18] = TextSubclass("cuevion.account.security")
        events.append(tuple(subclass_stream))
        for event_row in events:
            with self.subTest(stream_type=type(event_row[18]).__name__):
                connection = ScriptedConnection(
                    _stored_steps(request, event_row=event_row)
                )
                result = _repository(
                    ConnectionFactory(connection), Authorizer(None)
                ).create_initial_account(request)
                self.assert_result(
                    result, contract.InitialAccountCreationOutcome.INTERNAL_ERROR
                )
                self.assert_cleaned(connection)

    def test_malformed_exists_rows_never_become_business_conflicts(self):
        request = _request()
        for malformed in ([(object(),)], [(1,)], [], [(False,), (False,)]):
            with self.subTest(shape=repr(malformed)):
                connection = ScriptedConnection(
                    _authorized_prefix_steps(request)
                    + _conflict_query_steps(request, evidence=malformed)[:1]
                )
                result = _repository(
                    ConnectionFactory(connection), Authorizer(_context(request))
                ).create_initial_account(request)
                self.assert_result(
                    result, contract.InitialAccountCreationOutcome.INTERNAL_ERROR
                )
                self.assert_cleaned(connection)

    def test_new_operation_without_authorization_is_unavailable_and_write_free(self):
        request = _request()
        connection = ScriptedConnection(_initial_steps(request, []))
        authorizer = Authorizer(None)
        result = _repository(ConnectionFactory(connection), authorizer).create_initial_account(request)
        self.assert_result(result, contract.InitialAccountCreationOutcome.UNAVAILABLE)
        self.assertEqual(authorizer.calls, 1)
        self.assertFalse(any(key.startswith("insert_") for key, *_ in connection.calls))
        self.assertEqual(connection.rollback_count, 1)
        self.assert_cleaned(connection)

    def test_mismatching_or_wrong_type_write_context_is_internal_error(self):
        request = _request()
        contexts = (_context(request, trust_domain="preview.eu"), object())
        for context in contexts:
            connection = ScriptedConnection(_initial_steps(request, []))
            result = _repository(
                ConnectionFactory(connection), Authorizer(context)
            ).create_initial_account(request)
            self.assert_result(result, contract.InitialAccountCreationOutcome.INTERNAL_ERROR)
            self.assertFalse(any(key.startswith("insert_") for key, *_ in connection.calls))
            self.assert_cleaned(connection)

    def test_context_is_exact_immutable_and_redacted(self):
        context = _context(_request())
        self.assertEqual(repr(context), "InitialAccountWriteContext(...)")
        for marker in SENSITIVE_MARKERS:
            self.assertNotIn(marker, repr(context))
        with self.assertRaises(TypeError):
            context.trusted_now = 11  # type: ignore[misc]
        with self.assertRaises(TypeError):
            del context.trusted_now
        with self.assertRaises(TypeError):
            repository.InitialAccountWriteContext(  # type: ignore[arg-type]
                1, True, _request().operation_reference, ASSERTION_ID, TRUST_DOMAIN, COORDINATOR
            )
        with self.assertRaises(TypeError):
            pickle.dumps(context)
        with self.assertRaises(TypeError):
            class DerivedContext(repository.InitialAccountWriteContext):
                pass

    def test_evidence_conflict_wins_and_stops_later_checks(self):
        request = _request()
        connection = ScriptedConnection(
            _authorized_prefix_steps(request)
            + _conflict_query_steps(request, evidence=True)
        )
        result = _repository(
            ConnectionFactory(connection), Authorizer(_context(request))
        ).create_initial_account(request)
        self.assert_result(
            result,
            contract.InitialAccountCreationOutcome.CONFLICT,
            contract.InitialAccountConflictReason.EVIDENCE_ALREADY_CONSUMED,
        )
        keys = [key for key, *_ in connection.calls]
        self.assertIn("evidence", keys)
        self.assertNotIn("email_authority", keys)
        self.assertNotIn("user_collision", keys)
        self.assert_cleaned(connection)

    def test_authority_conflict_wins_over_record_id_collision(self):
        request = _request()
        connection = ScriptedConnection(
            _authorized_prefix_steps(request)
            + _conflict_query_steps(request, email_authority=True)
        )
        result = _repository(
            ConnectionFactory(connection), Authorizer(_context(request))
        ).create_initial_account(request)
        self.assert_result(
            result,
            contract.InitialAccountCreationOutcome.CONFLICT,
            contract.InitialAccountConflictReason.AUTHORITY_ALREADY_CLAIMED,
        )
        self.assertNotIn("user_collision", [key for key, *_ in connection.calls])
        self.assert_cleaned(connection)

    def test_identity_authority_conflict_is_case_sensitive_and_precedes_ids(self):
        request = _request()
        connection = ScriptedConnection(
            _authorized_prefix_steps(request)
            + _conflict_query_steps(request, identity_authority=True)
        )
        result = _repository(
            ConnectionFactory(connection), Authorizer(_context(request))
        ).create_initial_account(request)
        self.assert_result(
            result,
            contract.InitialAccountCreationOutcome.CONFLICT,
            contract.InitialAccountConflictReason.AUTHORITY_ALREADY_CLAIMED,
        )
        identity_call = next(call for call in connection.calls if call[0] == "identity_authority")
        self.assertEqual(identity_call[2], (ISSUER, SUBJECT))
        self.assert_cleaned(connection)

    def test_only_id_collision_is_record_id_collision(self):
        request = _request()
        connection = ScriptedConnection(
            _authorized_prefix_steps(request)
            + _conflict_query_steps(request, user_collision=True)
        )
        result = _repository(
            ConnectionFactory(connection), Authorizer(_context(request))
        ).create_initial_account(request)
        self.assert_result(
            result,
            contract.InitialAccountCreationOutcome.CONFLICT,
            contract.InitialAccountConflictReason.RECORD_ID_COLLISION,
        )
        self.assert_cleaned(connection)

    def test_successful_create_has_exact_sql_order_values_and_one_commit(self):
        request = _request()
        connection = ScriptedConnection(
            _successful_create_steps(request),
            expected_commits=1,
            expected_rollbacks=0,
        )
        authorizer = Authorizer(_context(request, trusted_now=10))
        result = _repository(ConnectionFactory(connection), authorizer).create_initial_account(request)
        self.assert_result(result, contract.InitialAccountCreationOutcome.CREATED)
        self.assertEqual(authorizer.calls, 1)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertEqual(result.receipt.user_id, USER_ID)
        keys = [key for key, *_ in connection.calls]
        self.assertEqual(keys[0:3], ["set_transaction", "lock", "operation"])
        self.assertEqual(keys.count("lock"), 10)
        self.assertEqual(keys.count("operation"), 2)
        insert_keys = [key for key in keys if key.startswith("insert_")]
        self.assertEqual(
            insert_keys,
            [
                "insert_users",
                "insert_verified_emails",
                "insert_authentication_identities",
                "insert_workspaces",
                "insert_workspace_memberships",
                "insert_initial_account_operations",
                "insert_security_events",
            ],
        )
        self.assertLess(keys.index("deferred"), keys.index("sequence"))
        self.assertLess(keys.index("sequence"), keys.index("insert_users"))
        self.assertGreater(keys.index("immediate"), keys.index("insert_security_events"))
        operation_call = next(
            call for call in connection.calls if call[0] == "insert_initial_account_operations"
        )
        event_call = next(call for call in connection.calls if call[0] == "insert_security_events")
        self.assertEqual(operation_call[2], _operation_row(request))
        self.assertEqual(event_call[2], _event_row(request))
        self.assertEqual(operation_call[1].count("%s"), 73)
        self.assertEqual(event_call[1].count("%s"), 21)
        expected_insert_parameters = {
            "insert_users": _user_row(request),
            "insert_verified_emails": _email_row(request),
            "insert_authentication_identities": _identity_row(request),
            "insert_workspaces": _workspace_row(request),
            "insert_workspace_memberships": _membership_row(request),
            "insert_initial_account_operations": _operation_row(request),
            "insert_security_events": _event_row(request),
        }
        for key, _sql, parameters in connection.calls:
            if key not in expected_insert_parameters:
                continue
            expected = expected_insert_parameters[key]
            self.assertEqual(len(typing.cast(tuple[object, ...], parameters)), len(expected))
            for actual_value, expected_value in zip(
                typing.cast(tuple[object, ...], parameters), expected
            ):
                self.assertIs(type(actual_value), type(expected_value))
                self.assertEqual(actual_value, expected_value)
        self.assert_cleaned(connection)

    def test_lock_order_is_fixed_domain_separated_and_second_lookup_follows_locks(self):
        request = _request()
        connection = ScriptedConnection(
            _successful_create_steps(request),
            expected_commits=1,
            expected_rollbacks=0,
        )
        _repository(
            ConnectionFactory(connection), Authorizer(_context(request))
        ).create_initial_account(request)
        lock_materials = [
            typing.cast(tuple[object, ...], parameters)[0]
            for key, _sql, parameters in connection.calls
            if key == "lock"
        ]
        expected_domains = (
            "operation-reference",
            "evidence-assertion-claim",
            "canonical-verified-email-authority-claim",
            "issuer-subject-identity-authority-claim",
            "candidate-user-id",
            "candidate-verified-email-id",
            "candidate-authentication-identity-id",
            "candidate-workspace-id",
            "candidate-membership-pair",
            "candidate-security-event-id",
        )
        self.assertEqual(len(lock_materials), len(set(lock_materials)))
        for material, domain in zip(lock_materials, expected_domains):
            self.assertIn("\x1f" + domain, material)
        operation_indexes = [
            index for index, call in enumerate(connection.calls) if call[0] == "operation"
        ]
        last_lock_index = max(
            index for index, call in enumerate(connection.calls) if call[0] == "lock"
        )
        self.assertLess(operation_indexes[0], last_lock_index)
        self.assertGreater(operation_indexes[1], last_lock_index)
        self.assert_cleaned(connection)

    def test_operation_committed_while_remaining_locks_are_acquired_becomes_replay(self):
        request = _request()
        steps = _authorized_prefix_steps(request, [_operation_row(request)])
        steps.append(
            ScriptedStep(
                "stored_event",
                _operation_parameters(request),
                [_event_row(request)],
            )
        )
        connection = ScriptedConnection(steps)
        authorizer = Authorizer(_context(request))
        result = _repository(
            ConnectionFactory(connection), authorizer
        ).create_initial_account(request)
        self.assert_result(
            result, contract.InitialAccountCreationOutcome.EXACT_REPLAY
        )
        self.assertEqual(authorizer.calls, 1)
        self.assertFalse(
            any(key.startswith("insert_") for key, *_ in connection.calls)
        )
        self.assertEqual(connection.rollback_count, 1)
        self.assert_cleaned(connection)

    def test_serialization_and_deadlock_after_confirmed_rollback_are_unavailable(self):
        request = _request()
        failures = (
            psycopg.errors.SerializationFailure("private serialization detail"),
            psycopg.errors.DeadlockDetected("private deadlock detail"),
        )
        for failure in failures:
            steps = _authorized_prefix_steps(request)
            steps[-1].failure = failure
            connection = ScriptedConnection(steps)
            result = _repository(
                ConnectionFactory(connection), Authorizer(_context(request))
            ).create_initial_account(request)
            self.assert_result(result, contract.InitialAccountCreationOutcome.UNAVAILABLE)
            self.assertEqual(connection.rollback_count, 1)
            self.assert_cleaned(connection)

    def test_interface_errors_are_internal_but_operational_error_is_unavailable(self):
        request = _request()
        cases = (
            (
                "set_transaction",
                psycopg.InterfaceError("private interface detail " + EMAIL),
                contract.InitialAccountCreationOutcome.INTERNAL_ERROR,
            ),
            (
                "operation",
                psycopg.InterfaceError("closed cursor " + EMAIL),
                contract.InitialAccountCreationOutcome.INTERNAL_ERROR,
            ),
            (
                "set_transaction",
                psycopg.OperationalError("transport unavailable " + EMAIL),
                contract.InitialAccountCreationOutcome.UNAVAILABLE,
            ),
        )
        for key, failure, expected in cases:
            with self.subTest(key=key, failure=type(failure).__name__):
                steps = _initial_steps(request, [])
                target = next(step for step in steps if step.key == key)
                target.failure = failure
                target_index = steps.index(target)
                connection = ScriptedConnection(steps[: target_index + 1])
                result = _repository(
                    ConnectionFactory(connection), Authorizer(None)
                ).create_initial_account(request)
                self.assert_result(result, expected)
                for marker in SENSITIVE_MARKERS:
                    self.assertNotIn(marker, repr(result))
                    self.assertNotIn(marker, str(result))
                self.assert_cleaned(connection)

    def test_cleanup_exception_fails_closed_without_sensitive_output(self):
        request = _request()
        connection = ScriptedConnection(
            _stored_steps(request),
            close_failure=RuntimeError("private close failure " + EMAIL),
        )
        result = _repository(
            ConnectionFactory(connection), Authorizer(None)
        ).create_initial_account(request)
        self.assert_result(
            result, contract.InitialAccountCreationOutcome.INTERNAL_ERROR
        )
        for marker in SENSITIVE_MARKERS:
            self.assertNotIn(marker, repr(result))
            self.assertNotIn(marker, str(result))
        self.assert_cleaned(connection)

        cursor_failure = ScriptedConnection(
            _stored_steps(request),
            cursor_close_failure=RuntimeError("private cursor close " + EMAIL),
        )
        cursor_result = _repository(
            ConnectionFactory(cursor_failure), Authorizer(None)
        ).create_initial_account(request)
        self.assert_result(
            cursor_result, contract.InitialAccountCreationOutcome.INTERNAL_ERROR
        )
        self.assertEqual(cursor_failure.cursor_close_count, 1)
        self.assertEqual(cursor_failure.rollback_count, 1)
        self.assertEqual(cursor_failure.close_count, 1)
        cursor_failure.assert_exhausted()

    def test_baseexceptions_from_every_dependency_phase_propagate(self):
        request = _request()

        for fatal in (KeyboardInterrupt(), SystemExit(), GeneratorExit()):
            with self.subTest(factory=type(fatal).__name__):
                with self.assertRaises(type(fatal)) as caught:
                    _repository(
                        ConnectionFactory(fatal), Authorizer(None)
                    ).create_initial_account(request)
                self.assertIs(caught.exception, fatal)

        class Fatal(BaseException):
            pass

        cursor_fatal = Fatal()
        cursor_connection = ScriptedConnection(
            [], cursor_failure=cursor_fatal
        )
        with self.assertRaises(Fatal) as caught:
            _repository(
                ConnectionFactory(cursor_connection), Authorizer(None)
            ).create_initial_account(request)
        self.assertIs(caught.exception, cursor_fatal)
        self.assert_cleaned(cursor_connection)

        execute_fatal = Fatal()
        execute_steps = _initial_steps(request, [])
        execute_steps[0].failure = execute_fatal
        execute_connection = ScriptedConnection(execute_steps[:1])
        with self.assertRaises(Fatal) as caught:
            _repository(
                ConnectionFactory(execute_connection), Authorizer(None)
            ).create_initial_account(request)
        self.assertIs(caught.exception, execute_fatal)
        self.assert_cleaned(execute_connection)

        authorizer_fatal = Fatal()
        authorizer_connection = ScriptedConnection(_initial_steps(request, []))
        with self.assertRaises(Fatal) as caught:
            _repository(
                ConnectionFactory(authorizer_connection),
                Authorizer(authorizer_fatal),
            ).create_initial_account(request)
        self.assertIs(caught.exception, authorizer_fatal)
        self.assert_cleaned(authorizer_connection)

        commit_fatal = Fatal()
        commit_connection = ScriptedConnection(
            _successful_create_steps(request),
            commit_failure=commit_fatal,
            expected_commits=1,
        )
        with self.assertRaises(Fatal) as caught:
            _repository(
                ConnectionFactory(commit_connection),
                Authorizer(_context(request)),
            ).create_initial_account(request)
        self.assertIs(caught.exception, commit_fatal)
        self.assert_cleaned(commit_connection)

        close_fatal = Fatal()
        close_connection = ScriptedConnection(
            _stored_steps(request), close_failure=close_fatal
        )
        with self.assertRaises(Fatal) as caught:
            _repository(
                ConnectionFactory(close_connection), Authorizer(None)
            ).create_initial_account(request)
        self.assertIs(caught.exception, close_fatal)
        self.assert_cleaned(close_connection)

    def test_integrity_failure_reconciles_by_frozen_checks_not_constraint_name(self):
        request = _request()
        primary = ScriptedConnection(
            _steps_through_failure(
                _successful_create_steps(request),
                "insert_users",
                psycopg.IntegrityError("private_constraint_and_value"),
            )
        )
        reconciliation = ScriptedConnection(
            _authorized_prefix_steps(request)
            + _conflict_query_steps(request, evidence=True)
        )
        result = _repository(
            ConnectionFactory(primary, reconciliation),
            Authorizer(_context(request)),
        ).create_initial_account(request)
        self.assert_result(
            result,
            contract.InitialAccountCreationOutcome.CONFLICT,
            contract.InitialAccountConflictReason.EVIDENCE_ALREADY_CONSUMED,
        )
        self.assertEqual(primary.rollback_count, 1)
        self.assertEqual(reconciliation.rollback_count, 1)
        self.assert_cleaned(primary, reconciliation)

    def test_integrity_reconciliation_exact_durable_operation_is_exact_replay(self):
        request = _request()
        primary = ScriptedConnection(
            _steps_through_failure(
                _successful_create_steps(request),
                "insert_users",
                psycopg.IntegrityError("private"),
            )
        )
        reconciliation = ScriptedConnection(_stored_steps(request))
        result = _repository(
            ConnectionFactory(primary, reconciliation), Authorizer(_context(request))
        ).create_initial_account(request)
        self.assert_result(result, contract.InitialAccountCreationOutcome.EXACT_REPLAY)
        self.assert_cleaned(primary, reconciliation)

    def test_commit_exception_exact_durable_row_returns_created(self):
        request = _request()
        primary = ScriptedConnection(
            _successful_create_steps(request),
            commit_failure=psycopg.OperationalError("disconnect at commit"),
            expected_commits=1,
        )
        reconciliation = ScriptedConnection(_stored_steps(request))
        result = _repository(
            ConnectionFactory(primary, reconciliation), Authorizer(_context(request))
        ).create_initial_account(request)
        self.assert_result(result, contract.InitialAccountCreationOutcome.CREATED)
        self.assertEqual(primary.commit_count, 1)
        self.assertEqual(primary.rollback_count, 1)
        self.assertEqual(reconciliation.rollback_count, 1)
        self.assert_cleaned(primary, reconciliation)

    def test_commit_exception_authoritative_absence_returns_unavailable(self):
        request = _request()
        primary = ScriptedConnection(
            _successful_create_steps(request),
            commit_failure=psycopg.OperationalError("disconnect at commit"),
            expected_commits=1,
        )
        reconciliation = ScriptedConnection(_initial_steps(request, []))
        result = _repository(
            ConnectionFactory(primary, reconciliation), Authorizer(_context(request))
        ).create_initial_account(request)
        self.assert_result(result, contract.InitialAccountCreationOutcome.UNAVAILABLE)
        self.assert_cleaned(primary, reconciliation)

    def test_commit_exception_mismatch_and_corruption_are_classified(self):
        incoming = _request()
        stored = _request(display_name="Different Initial Owner")
        primary = ScriptedConnection(
            _successful_create_steps(incoming),
            commit_failure=psycopg.OperationalError("disconnect at commit"),
            expected_commits=1,
        )
        mismatch_read = ScriptedConnection(_stored_steps(stored))
        mismatch = _repository(
            ConnectionFactory(primary, mismatch_read),
            Authorizer(_context(incoming)),
        ).create_initial_account(incoming)
        self.assert_result(
            mismatch,
            contract.InitialAccountCreationOutcome.CONFLICT,
            contract.InitialAccountConflictReason.OPERATION_REFERENCE_MISMATCH,
        )
        self.assert_cleaned(primary, mismatch_read)

        corrupt_row = list(_operation_row(incoming))
        corrupt_row[8] = "unsupported_user_status"
        corrupt_primary = ScriptedConnection(
            _successful_create_steps(incoming),
            commit_failure=psycopg.OperationalError("disconnect at commit"),
            expected_commits=1,
        )
        corrupt_read = ScriptedConnection(
            _initial_steps(incoming, [tuple(corrupt_row)])
        )
        corrupt = _repository(
            ConnectionFactory(corrupt_primary, corrupt_read),
            Authorizer(_context(incoming)),
        ).create_initial_account(incoming)
        self.assert_result(
            corrupt, contract.InitialAccountCreationOutcome.INTERNAL_ERROR
        )
        self.assert_cleaned(corrupt_primary, corrupt_read)

    def test_commit_exception_reconciliation_failure_is_ambiguous(self):
        request = _request()
        primary = ScriptedConnection(
            _successful_create_steps(request),
            commit_failure=psycopg.OperationalError("disconnect at commit"),
            expected_commits=1,
        )
        reconciliation_steps = _initial_steps(request, [])
        reconciliation_steps[-1].failure = psycopg.OperationalError(
            "reconciliation unavailable"
        )
        reconciliation = ScriptedConnection(reconciliation_steps)
        result = _repository(
            ConnectionFactory(primary, reconciliation), Authorizer(_context(request))
        ).create_initial_account(request)
        self.assert_result(result, contract.InitialAccountCreationOutcome.AMBIGUOUS)
        self.assert_cleaned(primary, reconciliation)

    def test_later_call_after_ambiguous_commit_is_exact_replay(self):
        request = _request()
        primary = ScriptedConnection(
            _successful_create_steps(request),
            commit_failure=psycopg.OperationalError("disconnect at commit"),
            expected_commits=1,
        )
        reconciliation_steps = _initial_steps(request, [])
        reconciliation_steps[-1].failure = psycopg.OperationalError(
            "first reconciliation unavailable"
        )
        reconciliation = ScriptedConnection(reconciliation_steps)
        later_read = ScriptedConnection(_stored_steps(request))
        factory = ConnectionFactory(primary, reconciliation, later_read)
        authorizer = Authorizer(_context(request))
        instance = _repository(factory, authorizer)

        first = instance.create_initial_account(request)
        second = instance.create_initial_account(request)

        self.assert_result(first, contract.InitialAccountCreationOutcome.AMBIGUOUS)
        self.assert_result(
            second, contract.InitialAccountCreationOutcome.EXACT_REPLAY
        )
        self.assertEqual(
            _receipt_values(
                typing.cast(
                    contract.InitialAccountCreationReceipt, second.receipt
                )
            ),
            _receipt_values(_receipt(request)),
        )
        self.assertEqual(authorizer.calls, 1)
        self.assertFalse(
            any(key.startswith("insert_") for key, *_ in later_read.calls)
        )
        self.assertNotIn("sequence", [key for key, *_ in later_read.calls])
        self.assert_cleaned(primary, reconciliation, later_read)

    def test_unexpected_authorizer_exception_is_fixed_internal_and_baseexception_propagates(self):
        request = _request()
        connection = ScriptedConnection(_initial_steps(request, []))
        result = _repository(
            ConnectionFactory(connection),
            Authorizer(RuntimeError("private authorizer value " + EMAIL)),
        ).create_initial_account(request)
        self.assert_result(result, contract.InitialAccountCreationOutcome.INTERNAL_ERROR)
        self.assert_cleaned(connection)
        for marker in SENSITIVE_MARKERS:
            self.assertNotIn(marker, repr(result))

        class StopNow(BaseException):
            pass

        fatal_connection = ScriptedConnection(_initial_steps(request, []))
        with self.assertRaises(StopNow):
            _repository(
                ConnectionFactory(fatal_connection), Authorizer(StopNow())
            ).create_initial_account(request)
        self.assertEqual(fatal_connection.rollback_count, 1)
        self.assert_cleaned(fatal_connection)

    def test_factory_failures_have_closed_value_free_mapping(self):
        request = _request()
        unavailable = _repository(
            ConnectionFactory(psycopg.OperationalError("private DSN")),
            Authorizer(None),
        ).create_initial_account(request)
        internal = _repository(
            ConnectionFactory(RuntimeError("private programming failure")),
            Authorizer(None),
        ).create_initial_account(request)
        self.assert_result(unavailable, contract.InitialAccountCreationOutcome.UNAVAILABLE)
        self.assert_result(internal, contract.InitialAccountCreationOutcome.INTERNAL_ERROR)

    def test_autocommit_true_fails_closed_and_closes_connection(self):
        request = _request()
        connection = ScriptedConnection([], autocommit=True)
        result = _repository(
            ConnectionFactory(connection), Authorizer(_context(request))
        ).create_initial_account(request)
        self.assert_result(result, contract.InitialAccountCreationOutcome.INTERNAL_ERROR)
        self.assertEqual(connection.rollback_count, 1)
        self.assert_cleaned(connection)

    def test_protocol_method_surface_is_exact(self):
        signature = inspect.signature(
            repository.PostgreSQLInitialAccountRepository.create_initial_account
        )
        self.assertEqual(tuple(signature.parameters), ("self", "request"))
        hints = typing.get_type_hints(
            repository.PostgreSQLInitialAccountRepository.create_initial_account
        )
        self.assertIs(hints["request"], contract.InitialAccountCreationRequest)
        self.assertIs(hints["return"], contract.InitialAccountCreationResult)


class PostgreSQLInitialAccountRepositorySecurityTests(unittest.TestCase):
    def test_all_sql_is_fixed_parameterized_and_has_only_approved_surface(self):
        sql_items = tuple(
            (name, value)
            for name, value in vars(repository).items()
            if name.endswith("_SQL") and type(value) is str
        )
        sql_values = tuple(value for _name, value in sql_items)
        self.assertGreater(len(sql_values), 20)
        self.assertTrue(all(name.startswith("_") for name, _value in sql_items))
        combined = "\n".join(sql_values)
        for marker in SENSITIVE_MARKERS:
            self.assertNotIn(marker, combined)
        self.assertEqual(_audit_sql_surface(sql_values), set(_APPROVED_RELATIONS))
        for sql in sql_values:
            self.assertNotIn("session", _sql_without_literals(sql).casefold())
            self.assertNotIn("entitlement", _sql_without_literals(sql).casefold())
            self.assertNotIn("mailbox", _sql_without_literals(sql).casefold())
            self.assertNotIn("provider", _sql_without_literals(sql).casefold())

    def test_insert_column_order_matches_independent_migration_oracle(self):
        insert_sql = (
            repository._INSERT_USER_SQL,
            repository._INSERT_VERIFIED_EMAIL_SQL,
            repository._INSERT_AUTHENTICATION_IDENTITY_SQL,
            repository._INSERT_WORKSPACE_SQL,
            repository._INSERT_WORKSPACE_MEMBERSHIP_SQL,
            repository._INSERT_OPERATION_SQL,
            repository._INSERT_SECURITY_EVENT_SQL,
        )
        parsed: dict[str, tuple[str, ...]] = {}
        for sql in insert_sql:
            relation, columns, placeholder_count = _parse_insert(sql)
            self.assertNotIn(relation, parsed)
            parsed[relation] = columns
            self.assertEqual(placeholder_count, len(columns))
        self.assertEqual(parsed, _INSERT_COLUMN_ORACLE)

    def test_sql_oracle_and_fake_reject_extra_relation_or_unscripted_sql(self):
        sql_values = tuple(
            value
            for name, value in vars(repository).items()
            if name.endswith("_SQL") and type(value) is str
        )
        with self.assertRaises(AssertionError):
            _audit_sql_surface(
                sql_values + ("SELECT 1 FROM private.secret_authority",)
            )

        connection = ScriptedConnection([], expected_rollbacks=0)
        cursor = connection.cursor()
        with self.assertRaises(AssertionError):
            cursor.execute("SELECT 1 FROM private.secret_authority")
        cursor.close()
        connection.close()
        connection.assert_exhausted()

    def test_source_has_no_runtime_or_policy_capabilities(self):
        source = _SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module.split(".")[0])
        self.assertTrue(
            imports.isdisjoint(
                {
                    "os",
                    "logging",
                    "socket",
                    "pathlib",
                    "subprocess",
                    "requests",
                    "urllib",
                    "random",
                    "secrets",
                    "time",
                    "sqlalchemy",
                }
            )
        )
        normalized = source.casefold()
        for forbidden in (
            "os.environ",
            "create_engine",
            "sqlalchemy.orm",
            "create_all",
            "handler",
            "router",
            "fastapi",
            "connection pool",
        ):
            self.assertNotIn(forbidden, normalized)

    def test_active_tracked_api_never_imports_adapter(self):
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--", "frontend/api/**/*.py"],
            cwd=_REPOSITORY,
            check=True,
            capture_output=True,
        )
        paths = tuple(
            Path(item.decode())
            for item in completed.stdout.split(b"\0")
            if item
        )
        protected = Path("frontend/api/inboxes/oauth_google.py")
        self.assertNotIn(protected, paths)
        for relative in paths:
            if relative.name.startswith("test_"):
                continue
            source = (_REPOSITORY / relative).read_text(encoding="utf-8")
            self.assertNotIn(
                "postgresql_initial_account_repository", source.casefold()
            )

    def test_canonical_reload_and_alternate_module_guards_fail_closed(self):
        with self.assertRaises(ImportError):
            importlib.reload(repository)
        spec = importlib.util.spec_from_file_location("alternate_adapter", _SOURCE)
        self.assertIsNotNone(spec)
        alternate = importlib.util.module_from_spec(typing.cast(object, spec))
        with self.assertRaises(ImportError):
            typing.cast(object, spec).loader.exec_module(alternate)

    def test_isolated_import_attempts_no_environment_network_or_application_io(self):
        program = r'''
import builtins
import os
import random
import secrets
import socket
import sys
import time
from unittest import mock

import psycopg
from api.auth import models
from cuevion_auth import account_repository_contract

def forbidden(*args, **kwargs):
    raise AssertionError("I/O attempted")

class ForbiddenEnvironment(dict):
    def __getitem__(self, key):
        raise AssertionError("environment read")
    def get(self, key, default=None):
        raise AssertionError("environment read")

with mock.patch.object(builtins, "open", forbidden), \
     mock.patch.object(socket, "socket", forbidden), \
     mock.patch.object(os, "getenv", forbidden), \
     mock.patch.object(os, "environ", ForbiddenEnvironment()), \
     mock.patch.object(psycopg, "connect", forbidden), \
     mock.patch.object(time, "time", forbidden), \
     mock.patch.object(time, "time_ns", forbidden), \
     mock.patch.object(time, "monotonic", forbidden), \
     mock.patch.object(time, "monotonic_ns", forbidden), \
     mock.patch.object(random, "random", forbidden), \
     mock.patch.object(secrets, "token_bytes", forbidden), \
     mock.patch.object(secrets, "token_urlsafe", forbidden):
    import cuevion_db.postgresql_initial_account_repository as module
    factory = lambda: forbidden()
    class Authorizer:
        def authorize_new_operation(self, request):
            return None
    module.PostgreSQLInitialAccountRepository(factory, Authorizer())
print("safe")
'''
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PSYCOPG_IMPL"] = "binary"
        completed = subprocess.run(
            [sys.executable, "-c", program],
            cwd=_FRONTEND,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertEqual(completed.stdout, "safe\n")

    def test_activation_document_is_explicitly_inactive(self):
        documentation = _DOCUMENTATION.read_text(encoding="utf-8").casefold()
        for required in (
            "completely inactive",
            "no bootstrap",
            "serializable",
            "advisory",
            "exact replay",
            "evidence_already_consumed",
            "authority_already_claimed",
            "record_id_collision",
            "commit ambiguity",
            "sequence gaps",
            "preview",
            "production",
            "explicit activation decision",
        ):
            self.assertIn(required, documentation)


if __name__ == "__main__":
    unittest.main()
