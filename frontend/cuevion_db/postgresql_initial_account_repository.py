"""Inactive Psycopg 3 adapter for schema-one initial-account creation.

The module contains fixed SQL and a caller-injected synchronous connection
boundary.  It owns no DSN, pool, environment lookup, clock, route, or policy.
"""

import sys as _sys


if (
    __name__ != "cuevion_db.postgresql_initial_account_repository"
    or __package__ != "cuevion_db"
):
    raise ImportError(
        "PostgreSQL initial-account repository requires its canonical import identity"
    )
if (
    getattr(
        _sys.modules.get(
            "cuevion_db.postgresql_initial_account_repository"
        ),
        "__dict__",
        None,
    )
    is not globals()
):
    raise ImportError(
        "PostgreSQL initial-account repository requires its canonical module object"
    )
if "_POSTGRESQL_INITIAL_ACCOUNT_REPOSITORY_INITIALIZED" in globals():
    raise ImportError(
        "PostgreSQL initial-account repository cannot initialize twice"
    )
_POSTGRESQL_INITIAL_ACCOUNT_REPOSITORY_INITIALIZED = True

import base64 as _base64
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
from datetime import timezone as _timezone
from typing import Protocol as _Protocol

import psycopg as _psycopg

from api.auth import models as _models
from cuevion_auth import account_repository_contract as _contract


if _models is not _sys.modules.get("api.auth.models"):
    raise ImportError("account models require their canonical import identity")
if _contract is not _sys.modules.get(
    "cuevion_auth.account_repository_contract"
):
    raise ImportError(
        "initial-account contract requires its canonical import identity"
    )


__all__ = (
    "PostgreSQLConnectionFactory",
    "InitialAccountNewOperationAuthorizer",
    "InitialAccountWriteContext",
    "PostgreSQLInitialAccountRepository",
)


class PostgreSQLConnectionFactory(_Protocol):
    """Return one fresh synchronous Psycopg-compatible connection per call."""

    def __call__(self) -> object:
        ...


class InitialAccountNewOperationAuthorizer(_Protocol):
    """Pure caller-owned authorization boundary for a genuinely new operation."""

    def authorize_new_operation(
        self,
        request: _contract.InitialAccountCreationRequest,
    ) -> "InitialAccountWriteContext | None":
        ...


class InitialAccountWriteContext:
    """Immutable trusted-now and provenance binding for one new write."""

    __slots__ = (
        "context_version",
        "trusted_now",
        "operation_reference",
        "evidence_assertion_id",
        "trust_domain",
        "verification_coordinator_id",
    )

    context_version: int
    trusted_now: int
    operation_reference: _contract.InitialAccountOperationReference
    evidence_assertion_id: str
    trust_domain: str
    verification_coordinator_id: str

    def __init__(
        self,
        context_version: int,
        trusted_now: int,
        operation_reference: _contract.InitialAccountOperationReference,
        evidence_assertion_id: str,
        trust_domain: str,
        verification_coordinator_id: str,
    ) -> None:
        if (
            type(context_version) is not int
            or context_version != 1
            or not _is_timestamp_int(trusted_now)
            or type(operation_reference)
            is not _contract.InitialAccountOperationReference
            or type(evidence_assertion_id) is not str
            or type(trust_domain) is not str
            or type(verification_coordinator_id) is not str
        ):
            raise TypeError("invalid initial-account write context")
        object.__setattr__(self, "context_version", context_version)
        object.__setattr__(self, "trusted_now", trusted_now)
        object.__setattr__(self, "operation_reference", operation_reference)
        object.__setattr__(
            self, "evidence_assertion_id", evidence_assertion_id
        )
        object.__setattr__(self, "trust_domain", trust_domain)
        object.__setattr__(
            self,
            "verification_coordinator_id",
            verification_coordinator_id,
        )

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("initial-account write context is immutable")

    def __delattr__(self, _name: str) -> None:
        raise TypeError("initial-account write context is immutable")

    def __init_subclass__(cls, **_keywords: object) -> None:
        raise TypeError("initial-account write context cannot be subclassed")

    def __reduce__(self) -> object:
        raise TypeError("initial-account write context cannot be serialized")

    def __reduce_ex__(self, _protocol: object) -> object:
        raise TypeError("initial-account write context cannot be serialized")

    def __getstate__(self) -> object:
        raise TypeError("initial-account write context cannot be serialized")

    def __repr__(self) -> str:
        return "InitialAccountWriteContext(...)"

    __str__ = __repr__


_SCHEMA = "cuevion_account"
_SECURITY_EVENT_STREAM_NAME = "cuevion.account.security"
_EPOCH = _datetime(1970, 1, 1, tzinfo=_timezone.utc)
_MAX_TIMESTAMP = 253_402_300_799
_BASE64URL_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789-_"
)

_USER_COLUMNS = (
    "schema_version",
    "user_id",
    "status",
    "primary_verified_email_id",
    "display_name",
    "security_epoch",
    "created_at",
    "updated_at",
    "row_version",
)
_VERIFIED_EMAIL_COLUMNS = (
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
)
_AUTHENTICATION_IDENTITY_COLUMNS = (
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
)
_WORKSPACE_COLUMNS = (
    "schema_version",
    "workspace_id",
    "status",
    "created_by_user_id",
    "created_at",
    "updated_at",
    "row_version",
)
_WORKSPACE_MEMBERSHIP_COLUMNS = (
    "schema_version",
    "workspace_id",
    "user_id",
    "role",
    "status",
    "created_at",
    "updated_at",
    "row_version",
)
_OPERATION_COLUMNS = (
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
)
_SECURITY_EVENT_COLUMNS = (
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
)

_SET_TRANSACTION_SQL = (
    "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ WRITE"
)
_SET_CONSTRAINTS_DEFERRED_SQL = "SET CONSTRAINTS ALL DEFERRED"
_SET_CONSTRAINTS_IMMEDIATE_SQL = "SET CONSTRAINTS ALL IMMEDIATE"
_ADVISORY_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
)
_NEXT_EVENT_POSITION_SQL = (
    "SELECT nextval('cuevion_account.security_event_stream_position_seq')"
)

_SELECT_OPERATION_SQL = """
SELECT
    operation_record_version,
    reference_schema_version,
    derivation_key_epoch,
    operation_digest,
    request_snapshot_version,
    request_version,
    snapshot_user_schema_version,
    snapshot_user_user_id,
    snapshot_user_status,
    snapshot_user_primary_verified_email_id,
    snapshot_user_display_name,
    snapshot_user_security_epoch,
    snapshot_user_created_at,
    snapshot_user_updated_at,
    snapshot_user_row_version,
    snapshot_verified_email_schema_version,
    snapshot_verified_email_email_id,
    snapshot_verified_email_user_id,
    snapshot_verified_email_canonical_email,
    snapshot_verified_email_status,
    snapshot_verified_email_verification_source,
    snapshot_verified_email_created_at,
    snapshot_verified_email_verified_at,
    snapshot_verified_email_retired_at,
    snapshot_verified_email_row_version,
    snapshot_authentication_identity_schema_version,
    snapshot_authentication_identity_identity_id,
    snapshot_authentication_identity_user_id,
    snapshot_authentication_identity_issuer,
    snapshot_authentication_identity_subject,
    snapshot_authentication_identity_authentication_method,
    snapshot_authentication_identity_status,
    snapshot_authentication_identity_verified_email_id,
    snapshot_authentication_identity_created_at,
    snapshot_authentication_identity_last_used_at,
    snapshot_authentication_identity_row_version,
    snapshot_workspace_schema_version,
    snapshot_workspace_workspace_id,
    snapshot_workspace_status,
    snapshot_workspace_created_by_user_id,
    snapshot_workspace_created_at,
    snapshot_workspace_updated_at,
    snapshot_workspace_row_version,
    snapshot_workspace_membership_schema_version,
    snapshot_workspace_membership_workspace_id,
    snapshot_workspace_membership_user_id,
    snapshot_workspace_membership_role,
    snapshot_workspace_membership_status,
    snapshot_workspace_membership_created_at,
    snapshot_workspace_membership_updated_at,
    snapshot_workspace_membership_row_version,
    snapshot_authentication_evidence_schema_version,
    snapshot_authentication_evidence_trust_domain,
    snapshot_authentication_evidence_verification_coordinator_id,
    snapshot_authentication_evidence_assertion_id,
    snapshot_authentication_evidence_issuer,
    snapshot_authentication_evidence_subject,
    snapshot_authentication_evidence_authentication_method,
    snapshot_authentication_evidence_canonical_verified_email,
    snapshot_authentication_evidence_verified_at,
    snapshot_authentication_evidence_issued_at,
    snapshot_authentication_evidence_expires_at,
    snapshot_security_event_schema_version,
    snapshot_security_event_event_id,
    snapshot_security_event_event_type,
    receipt_version,
    receipt_user_id,
    receipt_verified_email_id,
    receipt_authentication_identity_id,
    receipt_workspace_id,
    receipt_security_event_id,
    committed_at,
    row_version
FROM cuevion_account.initial_account_operations
WHERE reference_schema_version = %s
  AND derivation_key_epoch = %s
  AND operation_digest = %s
"""

_SELECT_SECURITY_EVENT_SQL = """
SELECT
    event_record_version, event_payload_version, event_id, event_type,
    reference_schema_version, derivation_key_epoch, operation_digest,
    actor_trust_domain, actor_verification_coordinator_id, user_id,
    verified_email_id, authentication_identity_id, workspace_id,
    membership_workspace_id, membership_user_id, security_epoch,
    event_at, recorded_at, event_stream_name, event_stream_position,
    row_version
FROM cuevion_account.security_events
WHERE reference_schema_version = %s
  AND derivation_key_epoch = %s
  AND operation_digest = %s
"""

_SELECT_EVIDENCE_CLAIM_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM cuevion_account.initial_account_operations
    WHERE snapshot_authentication_evidence_trust_domain = %s
      AND snapshot_authentication_evidence_verification_coordinator_id = %s
      AND snapshot_authentication_evidence_assertion_id = %s
)
"""
_SELECT_EMAIL_AUTHORITY_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM cuevion_account.verified_emails
    WHERE canonical_email = %s
      AND status = 'verified'
      AND retired_at IS NULL
)
"""
_SELECT_IDENTITY_AUTHORITY_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM cuevion_account.authentication_identities
    WHERE issuer = %s AND subject = %s
)
"""
_SELECT_USER_COLLISION_SQL = """
SELECT EXISTS (
    SELECT 1 FROM cuevion_account.users WHERE user_id = %s
)
"""
_SELECT_EMAIL_COLLISION_SQL = """
SELECT EXISTS (
    SELECT 1 FROM cuevion_account.verified_emails WHERE email_id = %s
)
"""
_SELECT_IDENTITY_COLLISION_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM cuevion_account.authentication_identities
    WHERE identity_id = %s
)
"""
_SELECT_WORKSPACE_COLLISION_SQL = """
SELECT EXISTS (
    SELECT 1 FROM cuevion_account.workspaces WHERE workspace_id = %s
)
"""
_SELECT_MEMBERSHIP_COLLISION_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM cuevion_account.workspace_memberships
    WHERE workspace_id = %s AND user_id = %s
)
"""
_SELECT_EVENT_COLLISION_SQL = """
SELECT EXISTS (
    SELECT 1 FROM cuevion_account.security_events WHERE event_id = %s
)
"""

_INSERT_USER_SQL = """
INSERT INTO cuevion_account.users (
    schema_version, user_id, status, primary_verified_email_id,
    display_name, security_epoch, created_at, updated_at, row_version
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
_INSERT_VERIFIED_EMAIL_SQL = """
INSERT INTO cuevion_account.verified_emails (
    schema_version, email_id, user_id, canonical_email, status,
    verification_source, created_at, verified_at, retired_at, row_version
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
_INSERT_AUTHENTICATION_IDENTITY_SQL = """
INSERT INTO cuevion_account.authentication_identities (
    schema_version, identity_id, user_id, issuer, subject,
    authentication_method, status, verified_email_id, created_at,
    last_used_at, row_version
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
_INSERT_WORKSPACE_SQL = """
INSERT INTO cuevion_account.workspaces (
    schema_version, workspace_id, status, created_by_user_id,
    created_at, updated_at, row_version
) VALUES (%s, %s, %s, %s, %s, %s, %s)
"""
_INSERT_WORKSPACE_MEMBERSHIP_SQL = """
INSERT INTO cuevion_account.workspace_memberships (
    schema_version, workspace_id, user_id, role, status,
    created_at, updated_at, row_version
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""
_INSERT_OPERATION_SQL = """
INSERT INTO cuevion_account.initial_account_operations (
    operation_record_version, reference_schema_version,
    derivation_key_epoch, operation_digest, request_snapshot_version,
    request_version, snapshot_user_schema_version, snapshot_user_user_id,
    snapshot_user_status, snapshot_user_primary_verified_email_id,
    snapshot_user_display_name, snapshot_user_security_epoch,
    snapshot_user_created_at, snapshot_user_updated_at,
    snapshot_user_row_version, snapshot_verified_email_schema_version,
    snapshot_verified_email_email_id, snapshot_verified_email_user_id,
    snapshot_verified_email_canonical_email,
    snapshot_verified_email_status,
    snapshot_verified_email_verification_source,
    snapshot_verified_email_created_at,
    snapshot_verified_email_verified_at,
    snapshot_verified_email_retired_at,
    snapshot_verified_email_row_version,
    snapshot_authentication_identity_schema_version,
    snapshot_authentication_identity_identity_id,
    snapshot_authentication_identity_user_id,
    snapshot_authentication_identity_issuer,
    snapshot_authentication_identity_subject,
    snapshot_authentication_identity_authentication_method,
    snapshot_authentication_identity_status,
    snapshot_authentication_identity_verified_email_id,
    snapshot_authentication_identity_created_at,
    snapshot_authentication_identity_last_used_at,
    snapshot_authentication_identity_row_version,
    snapshot_workspace_schema_version, snapshot_workspace_workspace_id,
    snapshot_workspace_status, snapshot_workspace_created_by_user_id,
    snapshot_workspace_created_at, snapshot_workspace_updated_at,
    snapshot_workspace_row_version,
    snapshot_workspace_membership_schema_version,
    snapshot_workspace_membership_workspace_id,
    snapshot_workspace_membership_user_id,
    snapshot_workspace_membership_role,
    snapshot_workspace_membership_status,
    snapshot_workspace_membership_created_at,
    snapshot_workspace_membership_updated_at,
    snapshot_workspace_membership_row_version,
    snapshot_authentication_evidence_schema_version,
    snapshot_authentication_evidence_trust_domain,
    snapshot_authentication_evidence_verification_coordinator_id,
    snapshot_authentication_evidence_assertion_id,
    snapshot_authentication_evidence_issuer,
    snapshot_authentication_evidence_subject,
    snapshot_authentication_evidence_authentication_method,
    snapshot_authentication_evidence_canonical_verified_email,
    snapshot_authentication_evidence_verified_at,
    snapshot_authentication_evidence_issued_at,
    snapshot_authentication_evidence_expires_at,
    snapshot_security_event_schema_version,
    snapshot_security_event_event_id, snapshot_security_event_event_type,
    receipt_version, receipt_user_id, receipt_verified_email_id,
    receipt_authentication_identity_id, receipt_workspace_id,
    receipt_security_event_id, committed_at, row_version
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s
)
"""
_INSERT_SECURITY_EVENT_SQL = """
INSERT INTO cuevion_account.security_events (
    event_record_version, event_payload_version, event_id, event_type,
    reference_schema_version, derivation_key_epoch, operation_digest,
    actor_trust_domain, actor_verification_coordinator_id, user_id,
    verified_email_id, authentication_identity_id, workspace_id,
    membership_workspace_id, membership_user_id, security_epoch,
    event_at, recorded_at, event_stream_name, event_stream_position,
    row_version
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
"""


class _StorageCorruption(Exception):
    __slots__ = ()

    def __init__(self) -> None:
        Exception.__init__(self)


class _AuthorizerFailure(Exception):
    __slots__ = ()

    def __init__(self) -> None:
        Exception.__init__(self)


class _ReconciliationUnavailable(Exception):
    __slots__ = ()

    def __init__(self) -> None:
        Exception.__init__(self)


def _is_timestamp_int(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_TIMESTAMP


def _timestamp_to_database(value: object) -> _datetime:
    if not _is_timestamp_int(value):
        raise _StorageCorruption()
    try:
        result = _EPOCH + _timedelta(seconds=value)
    except Exception:
        raise _StorageCorruption() from None
    if type(result) is not _datetime or result.tzinfo is not _timezone.utc:
        raise _StorageCorruption()
    return result


def _timestamp_from_database(value: object) -> int:
    if type(value) is not _datetime or value.tzinfo is None:
        raise _StorageCorruption()
    try:
        offset = value.utcoffset()
    except Exception:
        raise _StorageCorruption() from None
    if offset != _timedelta(0) or value.microsecond != 0:
        raise _StorageCorruption()
    try:
        utc_value = value.astimezone(_timezone.utc)
        difference = utc_value - _EPOCH
    except Exception:
        raise _StorageCorruption() from None
    result = difference.days * 86_400 + difference.seconds
    if difference.microseconds != 0 or not _is_timestamp_int(result):
        raise _StorageCorruption()
    if _timestamp_to_database(result) != utc_value:
        raise _StorageCorruption()
    return result


def _text_from_database(value: object) -> str:
    if type(value) is not str:
        raise _StorageCorruption()
    return value


def _optional_text_from_database(value: object) -> str | None:
    if value is None:
        return None
    return _text_from_database(value)


def _int_from_database(value: object) -> int:
    if type(value) is not int:
        raise _StorageCorruption()
    return value


def _decode_base64url(value: object) -> bytes:
    if (
        type(value) is not str
        or not value
        or not value.isascii()
        or any(character not in _BASE64URL_CHARACTERS for character in value)
        or len(value) % 4 == 1
    ):
        raise _StorageCorruption()
    try:
        decoded = _base64.b64decode(
            value.encode("ascii") + b"=" * ((-len(value)) % 4),
            altchars=b"-_",
            validate=True,
        )
    except Exception:
        raise _StorageCorruption() from None
    canonical = _base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value:
        raise _StorageCorruption()
    return decoded


def _digest_to_database(value: object) -> bytes:
    decoded = _decode_base64url(value)
    if len(decoded) != 32:
        raise _StorageCorruption()
    return decoded


def _digest_from_database(value: object) -> str:
    if type(value) is not bytes or len(value) != 32:
        raise _StorageCorruption()
    encoded = _base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
    if _digest_to_database(encoded) != value:
        raise _StorageCorruption()
    return encoded


def _exact_row(row: object, columns: tuple[str, ...]) -> dict[str, object]:
    if type(row) is not tuple or len(row) != len(columns):
        raise _StorageCorruption()
    return dict(zip(columns, row))


def _execute(cursor: object, sql: str, parameters: tuple[object, ...] | None = None) -> None:
    execute = getattr(cursor, "execute")
    if parameters is None:
        execute(sql)
    else:
        execute(sql, parameters)


def _fetch_all(
    cursor: object,
    sql: str,
    parameters: tuple[object, ...],
) -> list[tuple[object, ...]]:
    _execute(cursor, sql, parameters)
    rows = getattr(cursor, "fetchall")()
    if type(rows) is not list or any(type(row) is not tuple for row in rows):
        raise _StorageCorruption()
    return rows


def _exists(
    cursor: object,
    sql: str,
    parameters: tuple[object, ...],
) -> bool:
    rows = _fetch_all(cursor, sql, parameters)
    if len(rows) != 1 or len(rows[0]) != 1 or type(rows[0][0]) is not bool:
        raise _StorageCorruption()
    return rows[0][0]


def _operation_parameters(
    request: _contract.InitialAccountCreationRequest,
) -> tuple[object, ...]:
    reference = request.operation_reference
    return (
        reference.schema_version,
        reference.derivation_key_epoch,
        _digest_to_database(reference.operation_digest),
    )


def _lock_material(domain: str, *values: object) -> str:
    if type(domain) is not str or any(type(value) is not str for value in values):
        raise _StorageCorruption()
    pieces = ["cuevion-initial-account-lock-v1", domain]
    for value in values:
        encoded = value.encode("utf-8", errors="strict")
        pieces.append(f"{len(encoded)}:{value}")
    return "\x1f".join(pieces)


def _operation_lock_material(
    request: _contract.InitialAccountCreationRequest,
) -> str:
    reference = request.operation_reference
    return _lock_material(
        "operation-reference",
        str(reference.schema_version),
        str(reference.derivation_key_epoch),
        reference.operation_digest,
    )


def _remaining_lock_materials(
    request: _contract.InitialAccountCreationRequest,
) -> tuple[str, ...]:
    evidence = request.authentication_evidence
    identity = request.authentication_identity
    return (
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
        _lock_material(
            "candidate-workspace-id", request.workspace.workspace_id
        ),
        _lock_material(
            "candidate-membership-pair",
            request.workspace_membership.workspace_id,
            request.workspace_membership.user_id,
        ),
        _lock_material(
            "candidate-security-event-id", request.security_event.event_id
        ),
    )


def _acquire_lock(cursor: object, material: str) -> None:
    rows = _fetch_all(cursor, _ADVISORY_LOCK_SQL, (material,))
    if (
        len(rows) != 1
        or len(rows[0]) != 1
        or type(rows[0][0]) is not str
        or rows[0][0] != ""
    ):
        raise _StorageCorruption()


def _decode_user(values: dict[str, object], prefix: str = "") -> _models.CuevionUser:
    try:
        return _models.CuevionUser(
            schema_version=_int_from_database(values[prefix + "schema_version"]),
            user_id=_text_from_database(values[prefix + "user_id"]),
            status=_models.UserStatus(
                _text_from_database(values[prefix + "status"])
            ),
            primary_verified_email_id=_optional_text_from_database(
                values[prefix + "primary_verified_email_id"]
            ),
            display_name=_text_from_database(values[prefix + "display_name"]),
            security_epoch=_int_from_database(
                values[prefix + "security_epoch"]
            ),
            created_at=_timestamp_from_database(values[prefix + "created_at"]),
            updated_at=_timestamp_from_database(values[prefix + "updated_at"]),
            row_version=_int_from_database(values[prefix + "row_version"]),
        )
    except _StorageCorruption:
        raise
    except Exception:
        raise _StorageCorruption() from None


def _decode_verified_email(
    values: dict[str, object], prefix: str = ""
) -> _models.VerifiedEmail:
    retired = values[prefix + "retired_at"]
    try:
        return _models.VerifiedEmail(
            schema_version=_int_from_database(values[prefix + "schema_version"]),
            email_id=_text_from_database(values[prefix + "email_id"]),
            user_id=_text_from_database(values[prefix + "user_id"]),
            canonical_email=_text_from_database(
                values[prefix + "canonical_email"]
            ),
            status=_models.VerifiedEmailStatus(
                _text_from_database(values[prefix + "status"])
            ),
            verification_source=_text_from_database(
                values[prefix + "verification_source"]
            ),
            created_at=_timestamp_from_database(values[prefix + "created_at"]),
            verified_at=_timestamp_from_database(values[prefix + "verified_at"]),
            retired_at=(
                None if retired is None else _timestamp_from_database(retired)
            ),
            row_version=_int_from_database(values[prefix + "row_version"]),
        )
    except _StorageCorruption:
        raise
    except Exception:
        raise _StorageCorruption() from None


def _decode_authentication_identity(
    values: dict[str, object], prefix: str = ""
) -> _models.AuthenticationIdentity:
    last_used = values[prefix + "last_used_at"]
    try:
        return _models.AuthenticationIdentity(
            schema_version=_int_from_database(values[prefix + "schema_version"]),
            identity_id=_text_from_database(values[prefix + "identity_id"]),
            user_id=_text_from_database(values[prefix + "user_id"]),
            issuer=_text_from_database(values[prefix + "issuer"]),
            subject=_text_from_database(values[prefix + "subject"]),
            method=_models.AuthenticationMethod(
                _text_from_database(values[prefix + "authentication_method"])
            ),
            status=_models.AuthenticationIdentityStatus(
                _text_from_database(values[prefix + "status"])
            ),
            verified_email_id=_optional_text_from_database(
                values[prefix + "verified_email_id"]
            ),
            created_at=_timestamp_from_database(values[prefix + "created_at"]),
            last_used_at=(
                None if last_used is None else _timestamp_from_database(last_used)
            ),
            row_version=_int_from_database(values[prefix + "row_version"]),
        )
    except _StorageCorruption:
        raise
    except Exception:
        raise _StorageCorruption() from None


def _decode_workspace(
    values: dict[str, object], prefix: str = ""
) -> _models.Workspace:
    try:
        return _models.Workspace(
            schema_version=_int_from_database(values[prefix + "schema_version"]),
            workspace_id=_text_from_database(values[prefix + "workspace_id"]),
            status=_models.WorkspaceStatus(
                _text_from_database(values[prefix + "status"])
            ),
            created_by_user_id=_text_from_database(
                values[prefix + "created_by_user_id"]
            ),
            created_at=_timestamp_from_database(values[prefix + "created_at"]),
            updated_at=_timestamp_from_database(values[prefix + "updated_at"]),
            row_version=_int_from_database(values[prefix + "row_version"]),
        )
    except _StorageCorruption:
        raise
    except Exception:
        raise _StorageCorruption() from None


def _decode_workspace_membership(
    values: dict[str, object], prefix: str = ""
) -> _models.WorkspaceMembership:
    try:
        return _models.WorkspaceMembership(
            schema_version=_int_from_database(values[prefix + "schema_version"]),
            workspace_id=_text_from_database(values[prefix + "workspace_id"]),
            user_id=_text_from_database(values[prefix + "user_id"]),
            role=_models.WorkspaceRole(
                _text_from_database(values[prefix + "role"])
            ),
            status=_models.WorkspaceMembershipStatus(
                _text_from_database(values[prefix + "status"])
            ),
            created_at=_timestamp_from_database(values[prefix + "created_at"]),
            updated_at=_timestamp_from_database(values[prefix + "updated_at"]),
            row_version=_int_from_database(values[prefix + "row_version"]),
        )
    except _StorageCorruption:
        raise
    except Exception:
        raise _StorageCorruption() from None


def _same_record(first: object, second: object, fields: tuple[str, ...]) -> bool:
    if type(first) is not type(second):
        return False
    for field in fields:
        first_value = object.__getattribute__(first, field)
        second_value = object.__getattribute__(second, field)
        if type(first_value) is not type(second_value) or first_value != second_value:
            return False
    return True


def _receipt_for_request(
    request: _contract.InitialAccountCreationRequest,
) -> _contract.InitialAccountCreationReceipt:
    return _contract.InitialAccountCreationReceipt(
        schema_version=1,
        user_id=request.user.user_id,
        verified_email_id=request.verified_email.email_id,
        authentication_identity_id=request.authentication_identity.identity_id,
        workspace_id=request.workspace.workspace_id,
        security_event_id=request.security_event.event_id,
    )


def _decode_operation_row_worker(
    row: tuple[object, ...],
) -> tuple[
    _contract.InitialAccountCreationRequest,
    _contract.InitialAccountCreationReceipt,
    int,
]:
    values = _exact_row(row, _OPERATION_COLUMNS)
    exact_one = (
        "operation_record_version",
        "request_snapshot_version",
        "receipt_version",
        "row_version",
    )
    if any(
        type(values[field]) is not int or values[field] != 1
        for field in exact_one
    ):
        raise _StorageCorruption()
    reference = _contract.InitialAccountOperationReference(
        schema_version=_int_from_database(values["reference_schema_version"]),
        derivation_key_epoch=_int_from_database(values["derivation_key_epoch"]),
        operation_digest=_digest_from_database(values["operation_digest"]),
    )
    mapped = {
        key.removeprefix("snapshot_user_"): value
        for key, value in values.items()
        if key.startswith("snapshot_user_")
    }
    user = _decode_user(mapped)
    mapped = {
        key.removeprefix("snapshot_verified_email_"): value
        for key, value in values.items()
        if key.startswith("snapshot_verified_email_")
    }
    email = _decode_verified_email(mapped)
    mapped = {
        key.removeprefix("snapshot_authentication_identity_"): value
        for key, value in values.items()
        if key.startswith("snapshot_authentication_identity_")
    }
    identity = _decode_authentication_identity(mapped)
    mapped = {
        key.removeprefix("snapshot_workspace_"): value
        for key, value in values.items()
        if key.startswith("snapshot_workspace_")
        and not key.startswith("snapshot_workspace_membership_")
    }
    workspace = _decode_workspace(mapped)
    mapped = {
        key.removeprefix("snapshot_workspace_membership_"): value
        for key, value in values.items()
        if key.startswith("snapshot_workspace_membership_")
    }
    membership = _decode_workspace_membership(mapped)
    evidence = _contract.VerifiedAuthenticationEvidence(
        schema_version=_int_from_database(
            values["snapshot_authentication_evidence_schema_version"]
        ),
        trust_domain=_text_from_database(
            values["snapshot_authentication_evidence_trust_domain"]
        ),
        verification_coordinator_id=_text_from_database(
            values[
                "snapshot_authentication_evidence_verification_coordinator_id"
            ]
        ),
        assertion_id=_digest_from_database(
            values["snapshot_authentication_evidence_assertion_id"]
        ),
        issuer=_text_from_database(
            values["snapshot_authentication_evidence_issuer"]
        ),
        subject=_text_from_database(
            values["snapshot_authentication_evidence_subject"]
        ),
        authentication_method=_models.AuthenticationMethod(
            _text_from_database(
                values[
                    "snapshot_authentication_evidence_authentication_method"
                ]
            )
        ),
        canonical_verified_email=_text_from_database(
            values[
                "snapshot_authentication_evidence_canonical_verified_email"
            ]
        ),
        verified_at=_timestamp_from_database(
            values["snapshot_authentication_evidence_verified_at"]
        ),
        issued_at=_timestamp_from_database(
            values["snapshot_authentication_evidence_issued_at"]
        ),
        expires_at=_timestamp_from_database(
            values["snapshot_authentication_evidence_expires_at"]
        ),
    )
    event = _contract.InitialSecurityEventRequest(
        schema_version=_int_from_database(
            values["snapshot_security_event_schema_version"]
        ),
        event_id=_text_from_database(values["snapshot_security_event_event_id"]),
        event_type=_contract.InitialSecurityEventType(
            _text_from_database(values["snapshot_security_event_event_type"])
        ),
    )
    request = _contract.InitialAccountCreationRequest(
        request_version=_int_from_database(values["request_version"]),
        operation_reference=reference,
        user=user,
        verified_email=email,
        authentication_identity=identity,
        workspace=workspace,
        workspace_membership=membership,
        authentication_evidence=evidence,
        security_event=event,
    )
    receipt = _contract.InitialAccountCreationReceipt(
        schema_version=_int_from_database(values["receipt_version"]),
        user_id=_text_from_database(values["receipt_user_id"]),
        verified_email_id=_text_from_database(values["receipt_verified_email_id"]),
        authentication_identity_id=_text_from_database(
            values["receipt_authentication_identity_id"]
        ),
        workspace_id=_text_from_database(values["receipt_workspace_id"]),
        security_event_id=_text_from_database(values["receipt_security_event_id"]),
    )
    expected_receipt = _receipt_for_request(request)
    if not _same_record(
        receipt,
        expected_receipt,
        (
            "schema_version",
            "user_id",
            "verified_email_id",
            "authentication_identity_id",
            "workspace_id",
            "security_event_id",
        ),
    ):
        raise _StorageCorruption()
    committed_at = _timestamp_from_database(values["committed_at"])
    return request, receipt, committed_at


def _decode_operation_row(
    row: tuple[object, ...],
) -> tuple[
    _contract.InitialAccountCreationRequest,
    _contract.InitialAccountCreationReceipt,
    int,
]:
    try:
        return _decode_operation_row_worker(row)
    except _StorageCorruption:
        raise
    except Exception:
        raise _StorageCorruption() from None


def _verify_security_event(
    row: tuple[object, ...],
    request: _contract.InitialAccountCreationRequest,
    committed_at: int,
) -> None:
    values = _exact_row(row, _SECURITY_EVENT_COLUMNS)
    reference = request.operation_reference
    evidence = request.authentication_evidence
    expected = (
        1,
        request.security_event.schema_version,
        request.security_event.event_id,
        request.security_event.event_type.value,
        reference.schema_version,
        reference.derivation_key_epoch,
        _digest_to_database(reference.operation_digest),
        evidence.trust_domain,
        evidence.verification_coordinator_id,
        request.user.user_id,
        request.verified_email.email_id,
        request.authentication_identity.identity_id,
        request.workspace.workspace_id,
        request.workspace_membership.workspace_id,
        request.workspace_membership.user_id,
        request.user.security_epoch,
    )
    actual = tuple(values[column] for column in _SECURITY_EVENT_COLUMNS[:16])
    if any(
        type(actual[index]) is not type(expected[index])
        or actual[index] != expected[index]
        for index in range(len(expected))
    ):
        raise _StorageCorruption()
    event_at = _timestamp_from_database(values["event_at"])
    recorded_at = _timestamp_from_database(values["recorded_at"])
    if event_at != committed_at or recorded_at != committed_at:
        raise _StorageCorruption()
    if (
        type(values["event_stream_name"]) is not str
        or values["event_stream_name"] != _SECURITY_EVENT_STREAM_NAME
    ):
        raise _StorageCorruption()
    position = values["event_stream_position"]
    if type(position) is not int or position <= 0:
        raise _StorageCorruption()
    if type(values["row_version"]) is not int or values["row_version"] != 1:
        raise _StorageCorruption()


def _verify_stored_security_event(
    cursor: object,
    request: _contract.InitialAccountCreationRequest,
    committed_at: int,
) -> None:
    event_rows = _fetch_all(
        cursor,
        _SELECT_SECURITY_EVENT_SQL,
        _operation_parameters(request),
    )
    if len(event_rows) != 1:
        raise _StorageCorruption()
    _verify_security_event(event_rows[0], request, committed_at)


def _lookup_operation(
    cursor: object,
    request: _contract.InitialAccountCreationRequest,
) -> tuple[
    _contract.InitialAccountCreationRequest,
    _contract.InitialAccountCreationReceipt,
] | None:
    rows = _fetch_all(cursor, _SELECT_OPERATION_SQL, _operation_parameters(request))
    if not rows:
        return None
    if len(rows) != 1:
        raise _StorageCorruption()
    stored_request, receipt, committed_at = _decode_operation_row(rows[0])
    _verify_stored_security_event(cursor, stored_request, committed_at)
    return stored_request, receipt


def _classify_existing(
    stored: tuple[
        _contract.InitialAccountCreationRequest,
        _contract.InitialAccountCreationReceipt,
    ],
    request: _contract.InitialAccountCreationRequest,
    exact_outcome: _contract.InitialAccountCreationOutcome,
) -> _contract.InitialAccountCreationResult:
    stored_request, receipt = stored
    if _contract.initial_account_creation_requests_are_replay_equivalent(
        stored_request, request
    ):
        return _contract.InitialAccountCreationResult(
            outcome=exact_outcome,
            conflict_reason=None,
            receipt=receipt,
        )
    return _contract.InitialAccountCreationResult(
        outcome=_contract.InitialAccountCreationOutcome.CONFLICT,
        conflict_reason=(
            _contract.InitialAccountConflictReason.OPERATION_REFERENCE_MISMATCH
        ),
        receipt=None,
    )


def _fixed_result(
    outcome: _contract.InitialAccountCreationOutcome,
) -> _contract.InitialAccountCreationResult:
    return _contract.InitialAccountCreationResult(
        outcome=outcome,
        conflict_reason=None,
        receipt=None,
    )


def _conflict_result(
    reason: _contract.InitialAccountConflictReason,
) -> _contract.InitialAccountCreationResult:
    return _contract.InitialAccountCreationResult(
        outcome=_contract.InitialAccountCreationOutcome.CONFLICT,
        conflict_reason=reason,
        receipt=None,
    )


def _validate_context(
    context: object,
    request: _contract.InitialAccountCreationRequest,
) -> int:
    if type(context) is not InitialAccountWriteContext:
        raise _AuthorizerFailure()
    reference = context.operation_reference
    request_reference = request.operation_reference
    if (
        context.context_version != 1
        or not _is_timestamp_int(context.trusted_now)
        or type(reference) is not _contract.InitialAccountOperationReference
        or reference.schema_version != request_reference.schema_version
        or reference.derivation_key_epoch
        != request_reference.derivation_key_epoch
        or reference.operation_digest != request_reference.operation_digest
        or context.evidence_assertion_id
        != request.authentication_evidence.assertion_id
        or context.trust_domain != request.authentication_evidence.trust_domain
        or context.verification_coordinator_id
        != request.authentication_evidence.verification_coordinator_id
    ):
        raise _AuthorizerFailure()
    return context.trusted_now


def _new_operation_conflict(
    cursor: object,
    request: _contract.InitialAccountCreationRequest,
) -> _contract.InitialAccountConflictReason | None:
    evidence = request.authentication_evidence
    if _exists(
        cursor,
        _SELECT_EVIDENCE_CLAIM_SQL,
        (
            evidence.trust_domain,
            evidence.verification_coordinator_id,
            _digest_to_database(evidence.assertion_id),
        ),
    ):
        return _contract.InitialAccountConflictReason.EVIDENCE_ALREADY_CONSUMED
    if _exists(
        cursor,
        _SELECT_EMAIL_AUTHORITY_SQL,
        (request.verified_email.canonical_email,),
    ) or _exists(
        cursor,
        _SELECT_IDENTITY_AUTHORITY_SQL,
        (
            request.authentication_identity.issuer,
            request.authentication_identity.subject,
        ),
    ):
        return _contract.InitialAccountConflictReason.AUTHORITY_ALREADY_CLAIMED
    collision_checks = (
        (_SELECT_USER_COLLISION_SQL, (request.user.user_id,)),
        (
            _SELECT_EMAIL_COLLISION_SQL,
            (request.verified_email.email_id,),
        ),
        (
            _SELECT_IDENTITY_COLLISION_SQL,
            (request.authentication_identity.identity_id,),
        ),
        (
            _SELECT_WORKSPACE_COLLISION_SQL,
            (request.workspace.workspace_id,),
        ),
        (
            _SELECT_MEMBERSHIP_COLLISION_SQL,
            (
                request.workspace_membership.workspace_id,
                request.workspace_membership.user_id,
            ),
        ),
        (
            _SELECT_EVENT_COLLISION_SQL,
            (request.security_event.event_id,),
        ),
    )
    for sql, parameters in collision_checks:
        if _exists(cursor, sql, parameters):
            return _contract.InitialAccountConflictReason.RECORD_ID_COLLISION
    return None


def _user_insert_parameters(user: _models.CuevionUser) -> tuple[object, ...]:
    return (
        user.schema_version,
        user.user_id,
        user.status.value,
        user.primary_verified_email_id,
        user.display_name,
        user.security_epoch,
        _timestamp_to_database(user.created_at),
        _timestamp_to_database(user.updated_at),
        user.row_version,
    )


def _email_insert_parameters(
    email: _models.VerifiedEmail,
) -> tuple[object, ...]:
    return (
        email.schema_version,
        email.email_id,
        email.user_id,
        email.canonical_email,
        email.status.value,
        email.verification_source,
        _timestamp_to_database(email.created_at),
        _timestamp_to_database(email.verified_at),
        None
        if email.retired_at is None
        else _timestamp_to_database(email.retired_at),
        email.row_version,
    )


def _identity_insert_parameters(
    identity: _models.AuthenticationIdentity,
) -> tuple[object, ...]:
    return (
        identity.schema_version,
        identity.identity_id,
        identity.user_id,
        identity.issuer,
        identity.subject,
        identity.method.value,
        identity.status.value,
        identity.verified_email_id,
        _timestamp_to_database(identity.created_at),
        None
        if identity.last_used_at is None
        else _timestamp_to_database(identity.last_used_at),
        identity.row_version,
    )


def _workspace_insert_parameters(
    workspace: _models.Workspace,
) -> tuple[object, ...]:
    return (
        workspace.schema_version,
        workspace.workspace_id,
        workspace.status.value,
        workspace.created_by_user_id,
        _timestamp_to_database(workspace.created_at),
        _timestamp_to_database(workspace.updated_at),
        workspace.row_version,
    )


def _membership_insert_parameters(
    membership: _models.WorkspaceMembership,
) -> tuple[object, ...]:
    return (
        membership.schema_version,
        membership.workspace_id,
        membership.user_id,
        membership.role.value,
        membership.status.value,
        _timestamp_to_database(membership.created_at),
        _timestamp_to_database(membership.updated_at),
        membership.row_version,
    )


def _operation_insert_parameters(
    request: _contract.InitialAccountCreationRequest,
    receipt: _contract.InitialAccountCreationReceipt,
    trusted_now: int,
) -> tuple[object, ...]:
    reference = request.operation_reference
    user = request.user
    email = request.verified_email
    identity = request.authentication_identity
    workspace = request.workspace
    membership = request.workspace_membership
    evidence = request.authentication_evidence
    event = request.security_event
    return (
        1,
        reference.schema_version,
        reference.derivation_key_epoch,
        _digest_to_database(reference.operation_digest),
        1,
        request.request_version,
        *_user_insert_parameters(user),
        *_email_insert_parameters(email),
        *_identity_insert_parameters(identity),
        *_workspace_insert_parameters(workspace),
        *_membership_insert_parameters(membership),
        evidence.schema_version,
        evidence.trust_domain,
        evidence.verification_coordinator_id,
        _digest_to_database(evidence.assertion_id),
        evidence.issuer,
        evidence.subject,
        evidence.authentication_method.value,
        evidence.canonical_verified_email,
        _timestamp_to_database(evidence.verified_at),
        _timestamp_to_database(evidence.issued_at),
        _timestamp_to_database(evidence.expires_at),
        event.schema_version,
        event.event_id,
        event.event_type.value,
        receipt.schema_version,
        receipt.user_id,
        receipt.verified_email_id,
        receipt.authentication_identity_id,
        receipt.workspace_id,
        receipt.security_event_id,
        _timestamp_to_database(trusted_now),
        1,
    )


def _event_insert_parameters(
    request: _contract.InitialAccountCreationRequest,
    trusted_now: int,
    position: int,
) -> tuple[object, ...]:
    reference = request.operation_reference
    evidence = request.authentication_evidence
    trusted_timestamp = _timestamp_to_database(trusted_now)
    return (
        1,
        request.security_event.schema_version,
        request.security_event.event_id,
        request.security_event.event_type.value,
        reference.schema_version,
        reference.derivation_key_epoch,
        _digest_to_database(reference.operation_digest),
        evidence.trust_domain,
        evidence.verification_coordinator_id,
        request.user.user_id,
        request.verified_email.email_id,
        request.authentication_identity.identity_id,
        request.workspace.workspace_id,
        request.workspace_membership.workspace_id,
        request.workspace_membership.user_id,
        request.user.security_epoch,
        trusted_timestamp,
        trusted_timestamp,
        _SECURITY_EVENT_STREAM_NAME,
        position,
        1,
    )


def _allocate_event_position(cursor: object) -> int:
    rows = _fetch_all(cursor, _NEXT_EVENT_POSITION_SQL, ())
    if (
        len(rows) != 1
        or len(rows[0]) != 1
        or type(rows[0][0]) is not int
        or rows[0][0] <= 0
    ):
        raise _StorageCorruption()
    return rows[0][0]


def _insert_aggregate(
    cursor: object,
    request: _contract.InitialAccountCreationRequest,
    receipt: _contract.InitialAccountCreationReceipt,
    trusted_now: int,
) -> None:
    _execute(cursor, _SET_CONSTRAINTS_DEFERRED_SQL)
    position = _allocate_event_position(cursor)
    statements = (
        (_INSERT_USER_SQL, _user_insert_parameters(request.user)),
        (
            _INSERT_VERIFIED_EMAIL_SQL,
            _email_insert_parameters(request.verified_email),
        ),
        (
            _INSERT_AUTHENTICATION_IDENTITY_SQL,
            _identity_insert_parameters(request.authentication_identity),
        ),
        (_INSERT_WORKSPACE_SQL, _workspace_insert_parameters(request.workspace)),
        (
            _INSERT_WORKSPACE_MEMBERSHIP_SQL,
            _membership_insert_parameters(request.workspace_membership),
        ),
        (
            _INSERT_OPERATION_SQL,
            _operation_insert_parameters(request, receipt, trusted_now),
        ),
        (
            _INSERT_SECURITY_EVENT_SQL,
            _event_insert_parameters(request, trusted_now, position),
        ),
    )
    for sql, parameters in statements:
        if sql.count("%s") != len(parameters):
            raise _StorageCorruption()
        _execute(cursor, sql, parameters)
    _execute(cursor, _SET_CONSTRAINTS_IMMEDIATE_SQL)


def _is_availability_failure(error: BaseException) -> bool:
    return isinstance(
        error,
        (
            _psycopg.OperationalError,
            _psycopg.errors.SerializationFailure,
            _psycopg.errors.DeadlockDetected,
        ),
    )


def _attempt_rollback(connection: object) -> bool:
    try:
        getattr(connection, "rollback")()
    except Exception:
        return False
    return True


def _attempt_cursor_close(cursor: object | None) -> bool:
    if cursor is None:
        return True
    try:
        getattr(cursor, "close")()
    except Exception:
        return False
    return True


def _attempt_connection_close(connection: object) -> bool:
    try:
        getattr(connection, "close")()
    except Exception:
        return False
    return True


class PostgreSQLInitialAccountRepository:
    """Concrete but inert-until-called Psycopg transaction adapter."""

    __slots__ = ("_connection_factory", "_authorizer")

    def __init__(
        self,
        connection_factory: PostgreSQLConnectionFactory,
        authorizer: InitialAccountNewOperationAuthorizer,
    ) -> None:
        object.__setattr__(self, "_connection_factory", connection_factory)
        object.__setattr__(self, "_authorizer", authorizer)

    def create_initial_account(
        self,
        request: _contract.InitialAccountCreationRequest,
    ) -> _contract.InitialAccountCreationResult:
        _contract.validate_initial_account_creation_request(request)
        return self._create_validated(request)

    def _new_connection(self) -> object:
        factory = object.__getattribute__(self, "_connection_factory")
        return factory()

    def _create_validated(
        self,
        request: _contract.InitialAccountCreationRequest,
    ) -> _contract.InitialAccountCreationResult:
        try:
            connection = self._new_connection()
        except Exception as error:
            outcome = (
                _contract.InitialAccountCreationOutcome.UNAVAILABLE
                if _is_availability_failure(error)
                else _contract.InitialAccountCreationOutcome.INTERNAL_ERROR
            )
            return _fixed_result(outcome)

        cursor: object | None = None
        result: _contract.InitialAccountCreationResult | None = None
        reconciliation: str | None = None
        committed = False
        rollback_confirmed = False
        cleanup_succeeded = True
        try:
            if getattr(connection, "autocommit") is not False:
                raise _StorageCorruption()
            cursor = getattr(connection, "cursor")()
            _execute(cursor, _SET_TRANSACTION_SQL)
            _acquire_lock(cursor, _operation_lock_material(request))
            stored = _lookup_operation(cursor, request)
            if stored is not None:
                result = _classify_existing(
                    stored,
                    request,
                    _contract.InitialAccountCreationOutcome.EXACT_REPLAY,
                )
            else:
                authorizer = object.__getattribute__(self, "_authorizer")
                try:
                    context = authorizer.authorize_new_operation(request)
                except Exception:
                    raise _AuthorizerFailure() from None
                if context is None:
                    result = _fixed_result(
                        _contract.InitialAccountCreationOutcome.UNAVAILABLE
                    )
                else:
                    trusted_now = _validate_context(context, request)
                    for material in _remaining_lock_materials(request):
                        _acquire_lock(cursor, material)
                    stored = _lookup_operation(cursor, request)
                    if stored is not None:
                        result = _classify_existing(
                            stored,
                            request,
                            _contract.InitialAccountCreationOutcome.EXACT_REPLAY,
                        )
                    else:
                        conflict = _new_operation_conflict(cursor, request)
                        if conflict is not None:
                            result = _conflict_result(conflict)
                        else:
                            receipt = _receipt_for_request(request)
                            _insert_aggregate(
                                cursor, request, receipt, trusted_now
                            )
                            if not _attempt_cursor_close(cursor):
                                cursor = None
                                raise _StorageCorruption()
                            cursor = None
                            try:
                                getattr(connection, "commit")()
                            except Exception:
                                reconciliation = "commit"
                            else:
                                committed = True
                                result = _contract.InitialAccountCreationResult(
                                    outcome=(
                                        _contract.InitialAccountCreationOutcome.CREATED
                                    ),
                                    conflict_reason=None,
                                    receipt=receipt,
                                )
        except _psycopg.IntegrityError:
            reconciliation = "integrity"
        except _AuthorizerFailure:
            result = _fixed_result(
                _contract.InitialAccountCreationOutcome.INTERNAL_ERROR
            )
        except Exception as error:
            outcome = (
                _contract.InitialAccountCreationOutcome.UNAVAILABLE
                if _is_availability_failure(error)
                else _contract.InitialAccountCreationOutcome.INTERNAL_ERROR
            )
            result = _fixed_result(outcome)
        finally:
            cleanup_succeeded = _attempt_cursor_close(cursor)
            if not committed:
                rollback_confirmed = _attempt_rollback(connection)
                cleanup_succeeded = rollback_confirmed and cleanup_succeeded
            cleanup_succeeded = (
                _attempt_connection_close(connection) and cleanup_succeeded
            )

        if reconciliation == "commit":
            return self._reconcile_commit(request)
        if reconciliation == "integrity":
            if not rollback_confirmed:
                return _fixed_result(
                    _contract.InitialAccountCreationOutcome.UNAVAILABLE
                )
            return self._reconcile_integrity(request)
        if not committed and not cleanup_succeeded:
            return _fixed_result(
                _contract.InitialAccountCreationOutcome.INTERNAL_ERROR
            )
        if result is None:
            return _fixed_result(
                _contract.InitialAccountCreationOutcome.INTERNAL_ERROR
            )
        return result

    def _reconcile_commit(
        self,
        request: _contract.InitialAccountCreationRequest,
    ) -> _contract.InitialAccountCreationResult:
        try:
            connection = self._new_connection()
        except Exception:
            return _fixed_result(
                _contract.InitialAccountCreationOutcome.AMBIGUOUS
            )
        cursor: object | None = None
        result = _fixed_result(
            _contract.InitialAccountCreationOutcome.AMBIGUOUS
        )
        try:
            if getattr(connection, "autocommit") is not False:
                raise _ReconciliationUnavailable()
            cursor = getattr(connection, "cursor")()
            _execute(cursor, _SET_TRANSACTION_SQL)
            _acquire_lock(cursor, _operation_lock_material(request))
            stored = _lookup_operation(cursor, request)
            if stored is None:
                result = _fixed_result(
                    _contract.InitialAccountCreationOutcome.UNAVAILABLE
                )
            else:
                result = _classify_existing(
                    stored,
                    request,
                    _contract.InitialAccountCreationOutcome.CREATED,
                )
        except _StorageCorruption:
            result = _fixed_result(
                _contract.InitialAccountCreationOutcome.INTERNAL_ERROR
            )
        except Exception:
            result = _fixed_result(
                _contract.InitialAccountCreationOutcome.AMBIGUOUS
            )
        finally:
            _attempt_cursor_close(cursor)
            _attempt_rollback(connection)
            _attempt_connection_close(connection)
        return result

    def _reconcile_integrity(
        self,
        request: _contract.InitialAccountCreationRequest,
    ) -> _contract.InitialAccountCreationResult:
        try:
            connection = self._new_connection()
        except Exception as error:
            outcome = (
                _contract.InitialAccountCreationOutcome.UNAVAILABLE
                if _is_availability_failure(error)
                else _contract.InitialAccountCreationOutcome.INTERNAL_ERROR
            )
            return _fixed_result(outcome)
        cursor: object | None = None
        result: _contract.InitialAccountCreationResult | None = None
        try:
            if getattr(connection, "autocommit") is not False:
                raise _StorageCorruption()
            cursor = getattr(connection, "cursor")()
            _execute(cursor, _SET_TRANSACTION_SQL)
            _acquire_lock(cursor, _operation_lock_material(request))
            stored = _lookup_operation(cursor, request)
            if stored is not None:
                result = _classify_existing(
                    stored,
                    request,
                    _contract.InitialAccountCreationOutcome.EXACT_REPLAY,
                )
            else:
                for material in _remaining_lock_materials(request):
                    _acquire_lock(cursor, material)
                stored = _lookup_operation(cursor, request)
                if stored is not None:
                    result = _classify_existing(
                        stored,
                        request,
                        _contract.InitialAccountCreationOutcome.EXACT_REPLAY,
                    )
                else:
                    conflict = _new_operation_conflict(cursor, request)
                    result = (
                        _conflict_result(conflict)
                        if conflict is not None
                        else _fixed_result(
                            _contract.InitialAccountCreationOutcome.INTERNAL_ERROR
                        )
                    )
        except Exception as error:
            outcome = (
                _contract.InitialAccountCreationOutcome.UNAVAILABLE
                if _is_availability_failure(error)
                else _contract.InitialAccountCreationOutcome.INTERNAL_ERROR
            )
            result = _fixed_result(outcome)
        finally:
            _attempt_cursor_close(cursor)
            _attempt_rollback(connection)
            _attempt_connection_close(connection)
        if result is None:
            return _fixed_result(
                _contract.InitialAccountCreationOutcome.INTERNAL_ERROR
            )
        return result
