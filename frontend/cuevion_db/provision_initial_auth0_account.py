"""Inactive one-time operator for the first Cuevion Auth0 account.

Importing this module performs no filesystem, environment, network, database,
clock, or random operation.  The command-line entry point defaults to a
read-only check.  A write is reachable only through the exact apply command,
and the existing PostgreSQL initial-account repository remains the sole write
boundary.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from enum import Enum
import hashlib
import os
from pathlib import Path
import secrets
import stat
import sys
import time
from typing import Protocol
from urllib.parse import parse_qsl, unquote, urlsplit

from api.auth import account_authority
from api.auth import models
from cuevion_auth import account_record_ids
from cuevion_auth import account_repository_contract as account_contract
from cuevion_auth import current_account_repository_contract as current_contract
from cuevion_db import postgresql_current_account_repository
from cuevion_db import postgresql_initial_account_repository


__all__ = ("main",)


_CANONICAL_EMAIL = "rutger@hysteriarecs.com"
_DISPLAY_NAME = "Rutger Bäumer"
_AUTH_ISSUER = "https://cuevion-dev.eu.auth0.com/"
_AUTH_SUBJECT = "email|6a5e8971963ce518400f660b"
_INITIAL_SECURITY_EPOCH = 1
_SCHEMA_VERSION = 1
_INITIAL_ROW_VERSION = 1
_EVIDENCE_LIFETIME_SECONDS = 300
_VERIFICATION_SOURCE = "cuevion_first_account_operator_v1"
_TRUST_DOMAIN = "cuevion.preview.first-account.v1"
_VERIFICATION_COORDINATOR = "local-operator-reviewed-auth0-binding-v1"

_WRITER_DATABASE_URL_PATH = Path(
    "/Users/rutger/.local/share/cuevion-db-tools/secrets/"
    "cuevion-preview-auth-reader-migrator-v1.url"
)
_MAX_DATABASE_URL_BYTES = 8_192
_EXPECTED_WRITER_ROLE = "cuevion_preview_migrator"
_FORBIDDEN_READER_ROLE = "cuevion_preview_current_account_reader_v1"
_EXPECTED_DATABASE = "neondb"
_EXPECTED_PREVIEW_HOST_SHA256 = (
    "659cd2b1b4d492715829a21d693ed5a66ff9c7a982a368492f498509b1884b33"
)
_CONNECT_TIMEOUT_SECONDS = 5
_APPLICATION_NAME = "cuevion-first-auth0-account-v1"
_APPLY_CONFIRMATION = "APPLY_CUEVION_FIRST_AUTH0_ACCOUNT_V1"

_SET_READ_ONLY_TRANSACTION_SQL = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
)
_SELECT_CURRENT_AUTHORITY_SNAPSHOT_SQL = """
WITH authority_user_records AS (
    SELECT user_record.* FROM cuevion_account.users AS user_record
), authority_email_records AS (
    SELECT email_record.*
    FROM cuevion_account.verified_emails AS email_record
), authority_identity_records AS (
    SELECT identity_record.*
    FROM cuevion_account.authentication_identities AS identity_record
), authority_workspace_records AS (
    SELECT workspace_record.*
    FROM cuevion_account.workspaces AS workspace_record
), authority_membership_records AS (
    SELECT membership_record.*
    FROM cuevion_account.workspace_memberships AS membership_record
), inventory AS (
    SELECT
        (SELECT COUNT(*) FROM authority_user_records) AS user_count,
        (SELECT COUNT(*) FROM authority_email_records) AS email_count,
        (SELECT COUNT(*) FROM authority_identity_records) AS identity_count,
        (SELECT COUNT(*) FROM authority_workspace_records) AS workspace_count,
        (SELECT COUNT(*) FROM authority_membership_records) AS membership_count
)
SELECT
    inventory.user_count,
    inventory.email_count,
    inventory.identity_count,
    inventory.workspace_count,
    inventory.membership_count,
    CASE WHEN inventory.user_count = 1
           AND inventory.email_count = 1
           AND inventory.identity_count = 1
           AND inventory.workspace_count = 1
           AND inventory.membership_count = 1
         THEN jsonb_build_object(
             'user', (
                 SELECT to_jsonb(record_row)
                 FROM authority_user_records AS record_row
             ),
             'email', (
                 SELECT to_jsonb(record_row)
                 FROM authority_email_records AS record_row
             ),
             'identity', (
                 SELECT to_jsonb(record_row)
                 FROM authority_identity_records AS record_row
             ),
             'workspace', (
                 SELECT to_jsonb(record_row)
                 FROM authority_workspace_records AS record_row
             ),
             'membership', (
                 SELECT to_jsonb(record_row)
                 FROM authority_membership_records AS record_row
             )
         )::text
         ELSE ''
    END
FROM inventory
""".strip()
_EMPTY_COUNTS = (0, 0, 0, 0, 0)
_EXACT_COUNTS = (1, 1, 1, 1, 1)
_MAX_INVENTORY_FINGERPRINT_CHARACTERS = 32_768
_LOAD_FAILED = object()


class OperatorFailure(RuntimeError):
    """A fixed, value-free operator failure."""

    __slots__ = ()

    def __new__(cls, *_arguments: object, **_keywords: object) -> "OperatorFailure":
        return RuntimeError.__new__(cls)

    def __init__(self, *_arguments: object, **_keywords: object) -> None:
        RuntimeError.__init__(self)

    @property
    def args(self) -> tuple[object, ...]:
        return ()

    @args.setter
    def args(self, _value: object) -> None:
        return None

    def __str__(self) -> str:
        return "first-account operator failed"

    def __repr__(self) -> str:
        return "OperatorFailure()"


def _raise_operator_failure() -> None:
    error = OperatorFailure()
    try:
        raise error
    finally:
        object.__setattr__(error, "__context__", None)
        object.__setattr__(error, "__cause__", None)


class _WriterDatabaseUrl:
    __slots__ = ("_value", "_hostname")

    def __init__(self, value: str, hostname: str) -> None:
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_hostname", hostname)

    @property
    def value(self) -> str:
        return object.__getattribute__(self, "_value")

    @property
    def hostname(self) -> str:
        return object.__getattribute__(self, "_hostname")

    def __setattr__(self, _name: str, _value: object) -> None:
        _raise_operator_failure()

    def __delattr__(self, _name: str) -> None:
        _raise_operator_failure()

    def __repr__(self) -> str:
        return "WriterDatabaseUrl(<redacted>)"

    __str__ = __repr__

    def __reduce__(self) -> object:
        _raise_operator_failure()

    def __reduce_ex__(self, _protocol: object) -> object:
        _raise_operator_failure()


def _normalized_direct_preview_host(value: object) -> str | None:
    if type(value) is not str:
        return None
    hostname = value.casefold()
    labels = hostname.split(".")
    if (
        value != hostname
        or len(labels) < 3
        or labels[-2:] != ["neon", "tech"]
        or labels[0].endswith("-pooler")
        or "-pooler" in labels[0]
        or hashlib.sha256(hostname.encode("ascii", errors="ignore")).hexdigest()
        != _EXPECTED_PREVIEW_HOST_SHA256
    ):
        return None
    return hostname


def _parse_writer_database_url_worker(value: object) -> object:
    try:
        if (
            type(value) is not str
            or not value
            or len(value.encode("utf-8", errors="strict")) > _MAX_DATABASE_URL_BYTES
            or value != value.strip()
            or "\n" in value
            or "\r" in value
        ):
            return _LOAD_FAILED
        parsed = urlsplit(value)
        query = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
        )
        query_values = dict(query)
        username = unquote(parsed.username or "", encoding="utf-8", errors="strict")
        password = unquote(parsed.password or "", encoding="utf-8", errors="strict")
        database = unquote(parsed.path[1:], encoding="utf-8", errors="strict")
        hostname = _normalized_direct_preview_host(parsed.hostname)
        effective_port = 5_432 if parsed.port is None else parsed.port
        if (
            parsed.scheme != "postgresql"
            or not parsed.netloc
            or parsed.fragment
            or parsed.netloc.count("@") != 1
            or parsed.netloc.partition("@")[0].count(":") != 1
            or parsed.username is None
            or parsed.password is None
            or not password
            or username != _EXPECTED_WRITER_ROLE
            or username == _FORBIDDEN_READER_ROLE
            or hostname is None
            or parsed.path.count("/") != 1
            or database != _EXPECTED_DATABASE
            or effective_port != 5_432
            or len(query) != 2
            or query_values
            != {"sslmode": "require", "channel_binding": "require"}
        ):
            return _LOAD_FAILED
        return _WriterDatabaseUrl(value, hostname)
    except Exception:
        return _LOAD_FAILED


def _parse_writer_database_url(value: object) -> _WriterDatabaseUrl:
    result = _parse_writer_database_url_worker(value)
    if type(result) is _WriterDatabaseUrl:
        return result
    del value, result
    _raise_operator_failure()


def _load_writer_database_url() -> _WriterDatabaseUrl:
    descriptor: int | None = None
    result: object = _LOAD_FAILED
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        if no_follow == 0:
            return _raise_operator_failure()
        descriptor = os.open(_WRITER_DATABASE_URL_PATH, flags | no_follow)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_DATABASE_URL_BYTES + 1
        ):
            return _raise_operator_failure()
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            payload = stream.read(_MAX_DATABASE_URL_BYTES + 2)
        if len(payload) > _MAX_DATABASE_URL_BYTES + 1:
            return _raise_operator_failure()
        raw = payload.decode("utf-8", errors="strict")
        value = raw[:-1] if raw.endswith("\n") else raw
        if not value or value != value.strip() or "\n" in value or "\r" in value:
            return _raise_operator_failure()
        result = _parse_writer_database_url_worker(value)
    except Exception:
        result = _LOAD_FAILED
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except Exception:
                result = _LOAD_FAILED
    if type(result) is _WriterDatabaseUrl:
        return result
    del result
    _raise_operator_failure()


class _ConnectCallable(Protocol):
    def __call__(
        self,
        conninfo: str,
        *,
        autocommit: bool,
        connect_timeout: int,
        channel_binding: str,
        application_name: str,
    ) -> object:
        ...


def _default_connect(
    conninfo: str,
    *,
    autocommit: bool,
    connect_timeout: int,
    channel_binding: str,
    application_name: str,
) -> object:
    import psycopg

    return psycopg.connect(
        conninfo,
        autocommit=autocommit,
        connect_timeout=connect_timeout,
        channel_binding=channel_binding,
        application_name=application_name,
    )


class WriterConnectionFactory:
    """Fresh direct Preview writer connections with redacted representation."""

    __slots__ = ("_database_url", "_connect")

    def __init__(
        self,
        database_url: _WriterDatabaseUrl,
        connect: _ConnectCallable | None = None,
    ) -> None:
        if type(database_url) is not _WriterDatabaseUrl or (
            connect is not None and not callable(connect)
        ):
            _raise_operator_failure()
        object.__setattr__(self, "_database_url", database_url)
        object.__setattr__(self, "_connect", connect)

    def __repr__(self) -> str:
        return "WriterConnectionFactory(<redacted>)"

    def __call__(self) -> object:
        database_url = object.__getattribute__(self, "_database_url")
        injected = object.__getattribute__(self, "_connect")
        connector = _default_connect if injected is None else injected
        candidate: object | None = None
        accepted = False
        try:
            candidate = connector(
                database_url.value,
                autocommit=False,
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                channel_binding="require",
                application_name=_APPLICATION_NAME,
            )
            info = getattr(candidate, "info")
            pgconn = getattr(candidate, "pgconn")
            effective_host = _normalized_direct_preview_host(
                getattr(info, "host")
            )
            if (
                getattr(candidate, "autocommit") is not False
                or getattr(info, "user") != _EXPECTED_WRITER_ROLE
                or getattr(info, "user") == _FORBIDDEN_READER_ROLE
                or getattr(info, "dbname") != _EXPECTED_DATABASE
                or getattr(info, "port") != 5_432
                or effective_host != database_url.hostname
                or getattr(pgconn, "ssl_in_use") is not True
            ):
                raise OperatorFailure()
            accepted = True
        except Exception:
            accepted = False
        finally:
            if candidate is not None and not accepted:
                try:
                    getattr(candidate, "close")()
                except BaseException:
                    pass
        if accepted and candidate is not None:
            return candidate
        _raise_operator_failure()


class _InventoryReader:
    __slots__ = ("_connection_factory",)

    def __init__(self, connection_factory: Callable[[], object]) -> None:
        object.__setattr__(self, "_connection_factory", connection_factory)

    def read_snapshot(
        self,
    ) -> tuple[tuple[int, int, int, int, int], str] | None:
        connection: object | None = None
        cursor: object | None = None
        snapshot: tuple[tuple[int, int, int, int, int], str] | None = None
        cleanup_ok = True
        try:
            connection = object.__getattribute__(self, "_connection_factory")()
            if getattr(connection, "autocommit") is not False:
                raise OperatorFailure()
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(_SET_READ_ONLY_TRANSACTION_SQL)
            getattr(cursor, "execute")(_SELECT_CURRENT_AUTHORITY_SNAPSHOT_SQL)
            rows = getattr(cursor, "fetchall")()
            if (
                type(rows) is not list
                or len(rows) != 1
                or type(rows[0]) is not tuple
                or len(rows[0]) != 6
                or any(type(value) is not int or value < 0 for value in rows[0][:5])
                or type(rows[0][5]) is not str
                or len(rows[0][5]) > _MAX_INVENTORY_FINGERPRINT_CHARACTERS
            ):
                raise OperatorFailure()
            counts = rows[0][:5]
            fingerprint = rows[0][5]
            if counts == _EXACT_COUNTS and not fingerprint:
                raise OperatorFailure()
            snapshot = (counts, fingerprint)
        except Exception:
            snapshot = None
        finally:
            for target, method_name in (
                (cursor, "close"),
                (connection, "rollback"),
                (connection, "close"),
            ):
                if target is None:
                    continue
                try:
                    getattr(target, method_name)()
                except Exception:
                    cleanup_ok = False
        return snapshot if cleanup_ok else None


class GraphState(Enum):
    EMPTY = "empty"
    EXACT = "exact"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"


class _InventoryProtocol(Protocol):
    def read_snapshot(
        self,
    ) -> tuple[tuple[int, int, int, int, int], str] | None:
        ...


class _AuthorityProtocol(Protocol):
    def resolve_current_account_by_identity(
        self, identity_key: current_contract.AuthenticationIdentityLookupKey
    ) -> current_contract.CurrentAccountAuthorityResult:
        ...


def _expected_identity_key() -> current_contract.AuthenticationIdentityLookupKey:
    return current_contract.AuthenticationIdentityLookupKey(
        issuer=_AUTH_ISSUER,
        subject=_AUTH_SUBJECT,
    )


def _matches_exact_current_graph(
    result: object,
    identity_key: current_contract.AuthenticationIdentityLookupKey,
) -> bool:
    try:
        if not account_authority.auth0_authority_matches(
            result, identity_key, _CANONICAL_EMAIL
        ):
            return False
        if type(result) is not current_contract.CurrentAccountAuthorityResult:
            return False
        authority = result.authority
        if type(authority) is not current_contract.CurrentAccountAuthority:
            return False
        user = authority.user
        email = authority.primary_verified_email
        identity = authority.authentication_identity
        workspace = authority.workspace
        membership = authority.workspace_membership
        models.validate_user_primary_email(user, email)
        models.validate_identity_for_user(identity, user, email)
        models.validate_membership_for_user(membership, workspace, user)
        return (
            user.display_name == _DISPLAY_NAME
            and workspace.created_by_user_id == user.user_id
            and membership.role is models.WorkspaceRole.OWNER
            and all(
                record.schema_version == _SCHEMA_VERSION
                for record in (user, email, identity, workspace, membership)
            )
        )
    except Exception:
        return False


class CurrentGraphInspector:
    """Fail-closed classifier for the five current-authority tables only."""

    __slots__ = ("_inventory", "_authority")

    def __init__(
        self,
        inventory: _InventoryProtocol,
        authority: _AuthorityProtocol,
    ) -> None:
        object.__setattr__(self, "_inventory", inventory)
        object.__setattr__(self, "_authority", authority)

    def inspect(self) -> GraphState:
        try:
            before = object.__getattribute__(self, "_inventory").read_snapshot()
            if before is None:
                return GraphState.UNAVAILABLE
            before_counts, _before_fingerprint = before
            if before_counts == _EMPTY_COUNTS:
                return GraphState.EMPTY
            if before_counts != _EXACT_COUNTS:
                return GraphState.CONFLICT
            identity_key = _expected_identity_key()
            result = object.__getattribute__(
                self, "_authority"
            ).resolve_current_account_by_identity(identity_key)
            if result.outcome in (
                current_contract.CurrentAccountReadOutcome.UNAVAILABLE,
                current_contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            ):
                return GraphState.UNAVAILABLE
            if not _matches_exact_current_graph(result, identity_key):
                return GraphState.CONFLICT
            after = object.__getattribute__(self, "_inventory").read_snapshot()
            if after is None:
                return GraphState.UNAVAILABLE
            return GraphState.EXACT if after == before else GraphState.CONFLICT
        except Exception:
            return GraphState.UNAVAILABLE


def _base64url_entropy(byte_length: int) -> str:
    try:
        entropy = secrets.token_bytes(byte_length)
        encoded = base64.urlsafe_b64encode(entropy).rstrip(b"=").decode("ascii")
    except Exception:
        _raise_operator_failure()
    if (
        type(entropy) is not bytes
        or len(entropy) != byte_length
        or type(encoded) is not str
    ):
        _raise_operator_failure()
    return encoded


def _build_initial_request(
    trusted_now: int,
) -> account_contract.InitialAccountCreationRequest:
    if (
        type(trusted_now) is not int
        or trusted_now < 0
        or trusted_now
        > models.MAX_UNIX_UTC_SECONDS - _EVIDENCE_LIFETIME_SECONDS
    ):
        _raise_operator_failure()
    user_id = account_record_ids.generate_user_id_candidate()
    email_id = account_record_ids.generate_verified_email_id_candidate()
    identity_id = (
        account_record_ids.generate_authentication_identity_id_candidate()
    )
    workspace_id = account_record_ids.generate_workspace_id_candidate()
    operation = account_contract.InitialAccountOperationReference(
        schema_version=_SCHEMA_VERSION,
        derivation_key_epoch=1,
        operation_digest=_base64url_entropy(32),
    )
    user = models.CuevionUser(
        schema_version=_SCHEMA_VERSION,
        user_id=user_id,
        status=models.UserStatus.ACTIVE,
        primary_verified_email_id=email_id,
        display_name=_DISPLAY_NAME,
        security_epoch=_INITIAL_SECURITY_EPOCH,
        created_at=trusted_now,
        updated_at=trusted_now,
        row_version=_INITIAL_ROW_VERSION,
    )
    email = models.VerifiedEmail(
        schema_version=_SCHEMA_VERSION,
        email_id=email_id,
        user_id=user_id,
        canonical_email=_CANONICAL_EMAIL,
        status=models.VerifiedEmailStatus.VERIFIED,
        verification_source=_VERIFICATION_SOURCE,
        created_at=trusted_now,
        verified_at=trusted_now,
        retired_at=None,
        row_version=_INITIAL_ROW_VERSION,
    )
    identity = models.AuthenticationIdentity(
        schema_version=_SCHEMA_VERSION,
        identity_id=identity_id,
        user_id=user_id,
        issuer=_AUTH_ISSUER,
        subject=_AUTH_SUBJECT,
        method=models.AuthenticationMethod.EMAIL_OTP,
        status=models.AuthenticationIdentityStatus.ACTIVE,
        verified_email_id=email_id,
        created_at=trusted_now,
        last_used_at=None,
        row_version=_INITIAL_ROW_VERSION,
    )
    workspace = models.Workspace(
        schema_version=_SCHEMA_VERSION,
        workspace_id=workspace_id,
        status=models.WorkspaceStatus.ACTIVE,
        created_by_user_id=user_id,
        created_at=trusted_now,
        updated_at=trusted_now,
        row_version=_INITIAL_ROW_VERSION,
    )
    membership = models.WorkspaceMembership(
        schema_version=_SCHEMA_VERSION,
        workspace_id=workspace_id,
        user_id=user_id,
        role=models.WorkspaceRole.OWNER,
        status=models.WorkspaceMembershipStatus.ACTIVE,
        created_at=trusted_now,
        updated_at=trusted_now,
        row_version=_INITIAL_ROW_VERSION,
    )
    evidence = account_contract.VerifiedAuthenticationEvidence(
        schema_version=_SCHEMA_VERSION,
        trust_domain=_TRUST_DOMAIN,
        verification_coordinator_id=_VERIFICATION_COORDINATOR,
        assertion_id=_base64url_entropy(32),
        issuer=_AUTH_ISSUER,
        subject=_AUTH_SUBJECT,
        authentication_method=models.AuthenticationMethod.EMAIL_OTP,
        canonical_verified_email=_CANONICAL_EMAIL,
        verified_at=trusted_now,
        issued_at=trusted_now,
        expires_at=trusted_now + _EVIDENCE_LIFETIME_SECONDS,
    )
    security_event = account_contract.InitialSecurityEventRequest(
        schema_version=_SCHEMA_VERSION,
        event_id="sev_" + _base64url_entropy(16),
        event_type=(
            account_contract.InitialSecurityEventType.INITIAL_ACCOUNT_CREATED
        ),
    )
    models.validate_user_primary_email(user, email)
    models.validate_identity_for_user(identity, user, email)
    models.validate_membership_for_user(membership, workspace, user)
    request = account_contract.InitialAccountCreationRequest(
        request_version=_SCHEMA_VERSION,
        operation_reference=operation,
        user=user,
        verified_email=email,
        authentication_identity=identity,
        workspace=workspace,
        workspace_membership=membership,
        authentication_evidence=evidence,
        security_event=security_event,
    )
    account_contract.validate_initial_account_creation_request(request)
    return request


class _BoundNewOperationAuthorizer:
    __slots__ = ("_request", "_trusted_now")

    def __init__(
        self,
        request: account_contract.InitialAccountCreationRequest,
        trusted_now: int,
    ) -> None:
        object.__setattr__(self, "_request", request)
        object.__setattr__(self, "_trusted_now", trusted_now)

    def authorize_new_operation(
        self, request: account_contract.InitialAccountCreationRequest
    ) -> postgresql_initial_account_repository.InitialAccountWriteContext | None:
        expected = object.__getattribute__(self, "_request")
        try:
            equivalent = account_contract.initial_account_creation_requests_are_replay_equivalent(
                request, expected
            )
        except Exception:
            equivalent = False
        if not equivalent:
            return None
        evidence = request.authentication_evidence
        return postgresql_initial_account_repository.InitialAccountWriteContext(
            context_version=1,
            trusted_now=object.__getattribute__(self, "_trusted_now"),
            operation_reference=request.operation_reference,
            evidence_assertion_id=evidence.assertion_id,
            trust_domain=evidence.trust_domain,
            verification_coordinator_id=evidence.verification_coordinator_id,
        )


class _GraphInspectorProtocol(Protocol):
    def inspect(self) -> GraphState:
        ...


class _InitialRepositoryProtocol(Protocol):
    def create_initial_account(
        self, request: account_contract.InitialAccountCreationRequest
    ) -> account_contract.InitialAccountCreationResult:
        ...


class OperatorStatus(Enum):
    READY_TO_PROVISION = "ready_to_provision"
    ALREADY_PROVISIONED = "already_provisioned"
    PROVISIONING_PASSED = "provisioning_passed"
    CONFLICT = "conflict"
    ROLLED_BACK = "rolled_back"
    UNKNOWN = "unknown"


class FirstAuth0AccountOperator:
    __slots__ = ("_inspector", "_repository_factory", "_clock")

    def __init__(
        self,
        inspector: _GraphInspectorProtocol,
        repository_factory: Callable[
            [account_contract.InitialAccountCreationRequest, int],
            _InitialRepositoryProtocol,
        ],
        clock: Callable[[], int],
    ) -> None:
        object.__setattr__(self, "_inspector", inspector)
        object.__setattr__(self, "_repository_factory", repository_factory)
        object.__setattr__(self, "_clock", clock)

    def check(self) -> OperatorStatus:
        state = object.__getattribute__(self, "_inspector").inspect()
        if state is GraphState.EMPTY:
            return OperatorStatus.READY_TO_PROVISION
        if state is GraphState.EXACT:
            return OperatorStatus.ALREADY_PROVISIONED
        return OperatorStatus.CONFLICT

    def apply(self) -> OperatorStatus:
        inspector = object.__getattribute__(self, "_inspector")
        try:
            before = inspector.inspect()
        except BaseException:
            return OperatorStatus.CONFLICT
        if before is GraphState.EXACT:
            return OperatorStatus.ALREADY_PROVISIONED
        if before is not GraphState.EMPTY:
            return OperatorStatus.CONFLICT
        write_invoked = False
        try:
            trusted_now = object.__getattribute__(self, "_clock")()
            request = _build_initial_request(trusted_now)
            repository = object.__getattribute__(
                self, "_repository_factory"
            )(request, trusted_now)
            write_invoked = True
            result = repository.create_initial_account(request)
        except BaseException:
            result = None
        try:
            after = inspector.inspect()
        except BaseException:
            return (
                OperatorStatus.UNKNOWN
                if write_invoked
                else OperatorStatus.CONFLICT
            )
        if after is GraphState.EXACT:
            if not write_invoked:
                return OperatorStatus.ALREADY_PROVISIONED
            if (
                type(result) is account_contract.InitialAccountCreationResult
                and result.outcome
                in (
                    account_contract.InitialAccountCreationOutcome.EXACT_REPLAY,
                    account_contract.InitialAccountCreationOutcome.CONFLICT,
                )
            ):
                return OperatorStatus.ALREADY_PROVISIONED
            return OperatorStatus.PROVISIONING_PASSED
        if after is GraphState.EMPTY:
            if not write_invoked:
                return OperatorStatus.CONFLICT
            if (
                type(result) is account_contract.InitialAccountCreationResult
                and result.outcome
                is account_contract.InitialAccountCreationOutcome.CONFLICT
            ):
                return OperatorStatus.CONFLICT
            if (
                type(result) is account_contract.InitialAccountCreationResult
                and result.outcome
                in (
                    account_contract.InitialAccountCreationOutcome.CREATED,
                    account_contract.InitialAccountCreationOutcome.EXACT_REPLAY,
                    account_contract.InitialAccountCreationOutcome.AMBIGUOUS,
                )
            ):
                return OperatorStatus.UNKNOWN
            return OperatorStatus.ROLLED_BACK
        if after is GraphState.CONFLICT:
            if (
                type(result) is account_contract.InitialAccountCreationResult
                and result.outcome
                is account_contract.InitialAccountCreationOutcome.CONFLICT
            ):
                return OperatorStatus.CONFLICT
            return (
                OperatorStatus.UNKNOWN
                if write_invoked
                else OperatorStatus.CONFLICT
            )
        return OperatorStatus.UNKNOWN


def _build_live_operator() -> FirstAuth0AccountOperator:
    database_url = _load_writer_database_url()
    connection_factory = WriterConnectionFactory(database_url)
    inventory = _InventoryReader(connection_factory)
    current_repository = (
        postgresql_current_account_repository.PostgreSQLCurrentAccountRepository(
            connection_factory
        )
    )
    authority = account_authority.RuntimeAccountAuthority(
        connection_factory,
        current_repository,
    )
    inspector = CurrentGraphInspector(inventory, authority)

    def repository_factory(
        request: account_contract.InitialAccountCreationRequest,
        trusted_now: int,
    ) -> postgresql_initial_account_repository.PostgreSQLInitialAccountRepository:
        authorizer = _BoundNewOperationAuthorizer(request, trusted_now)
        return postgresql_initial_account_repository.PostgreSQLInitialAccountRepository(
            connection_factory,
            authorizer,
        )

    return FirstAuth0AccountOperator(
        inspector,
        repository_factory,
        lambda: int(time.time()),
    )


def _parse_arguments(arguments: object) -> str | None:
    if type(arguments) is not list or any(type(value) is not str for value in arguments):
        return None
    if arguments in ([], ["check"]):
        return "check"
    if arguments == ["apply", _APPLY_CONFIRMATION]:
        return "apply"
    return None


def _print_status(mode: str, status: OperatorStatus, stream: object) -> int:
    if status is OperatorStatus.READY_TO_PROVISION and mode == "check":
        print("CHECK PASSED", file=stream)
        print("READY TO PROVISION", file=stream)
        return 0
    if status is OperatorStatus.ALREADY_PROVISIONED:
        if mode == "check":
            print("CHECK PASSED", file=stream)
        print("ALREADY PROVISIONED", file=stream)
        return 0
    if status is OperatorStatus.PROVISIONING_PASSED and mode == "apply":
        print("PROVISIONING PASSED", file=stream)
        return 0
    if status is OperatorStatus.ROLLED_BACK:
        print("ROLLED BACK", file=stream)
        return 1
    if status is OperatorStatus.UNKNOWN:
        print("STATE UNKNOWN — DO NOT RETRY", file=stream)
        return 1
    print("CONFLICT — NO CHANGES MADE", file=stream)
    return 1


def _run(
    arguments: list[str],
    operator_factory: Callable[[], FirstAuth0AccountOperator],
    stdout: object,
    stderr: object,
) -> int:
    mode = _parse_arguments(arguments)
    if mode is None:
        print("CONFLICT — NO CHANGES MADE", file=stderr)
        return 2
    try:
        operator = operator_factory()
        status = operator.check() if mode == "check" else operator.apply()
    except BaseException:
        if mode == "apply":
            print("STATE UNKNOWN — DO NOT RETRY", file=stderr)
        else:
            print("CONFLICT — NO CHANGES MADE", file=stderr)
        return 1
    return _print_status(mode, status, stdout)


def main(arguments: list[str] | None = None) -> int:
    selected = sys.argv[1:] if arguments is None else arguments
    return _run(selected, _build_live_operator, sys.stdout, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
