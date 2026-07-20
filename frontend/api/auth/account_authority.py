"""Server-only current-account authority composition for the Auth0 lane.

The module reads no process environment at import time and opens no connection
until an authority method is called.  PostgreSQL and the concrete repository
are imported only by their default runtime factories, so offline tests can
inject inert fakes without either dependency.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
import re
from typing import Protocol
import unicodedata
from urllib.parse import unquote, urlsplit

from api.auth import models
from cuevion_auth import current_account_repository_contract as contract


__all__ = (
    "AccountAuthorityConfigurationError",
    "AccountAuthorityUnavailableError",
    "AccountReaderDatabaseUrl",
    "parse_account_reader_database_url",
    "AccountReaderConnectionFactory",
    "RuntimeAccountAuthority",
    "build_runtime_account_authority",
    "auth0_authority_matches",
)


_READER_DATABASE_URL_VARIABLE = "CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL"
_MAX_DATABASE_URL_CHARACTERS = 8_192
_CONNECT_TIMEOUT_SECONDS = 5
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_SET_TRANSACTION_SQL = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
)
_SELECT_UNIQUE_ACTIVE_WORKSPACE_SQL = """
WITH request(issuer, subject) AS (
    VALUES (%s, %s)
)
SELECT
    identity_record.user_id,
    user_record.row_version,
    user_record.security_epoch,
    email_record.email_id,
    email_record.row_version,
    identity_record.identity_id,
    identity_record.row_version,
    membership_record.workspace_id,
    workspace_record.row_version,
    membership_record.row_version
FROM request AS request_row
JOIN cuevion_account.authentication_identities AS identity_record
    ON identity_record.issuer = request_row.issuer
    AND identity_record.subject = request_row.subject
JOIN cuevion_account.users AS user_record
    ON user_record.user_id = identity_record.user_id
JOIN cuevion_account.verified_emails AS email_record
    ON email_record.email_id = user_record.primary_verified_email_id
    AND email_record.user_id = user_record.user_id
JOIN cuevion_account.workspace_memberships AS membership_record
    ON membership_record.user_id = user_record.user_id
JOIN cuevion_account.workspaces AS workspace_record
    ON workspace_record.workspace_id = membership_record.workspace_id
WHERE identity_record.status = 'active'
    AND user_record.status = 'active'
    AND email_record.status = 'verified'
    AND email_record.retired_at IS NULL
    AND membership_record.status = 'active'
    AND workspace_record.status = 'active'
LIMIT 2
""".strip()


class AccountAuthorityConfigurationError(ValueError):
    """A fixed configuration failure that retains no rejected URL."""

    __slots__ = ()

    def __new__(
        cls, *arguments: object, **keywords: object
    ) -> "AccountAuthorityConfigurationError":
        if cls is not AccountAuthorityConfigurationError:
            raise TypeError("account authority configuration errors are closed")
        del arguments, keywords
        return ValueError.__new__(cls)

    def __init__(self, *arguments: object, **keywords: object) -> None:
        del arguments, keywords
        ValueError.__init__(self)

    def __str__(self) -> str:
        return "invalid account authority configuration"

    def __repr__(self) -> str:
        return "AccountAuthorityConfigurationError()"

    @property
    def args(self) -> tuple[object, ...]:
        return ()

    @args.setter
    def args(self, value: object) -> None:
        del value


class AccountAuthorityUnavailableError(RuntimeError):
    """A fixed value-free failure for an unusable reader connection."""

    __slots__ = ()

    def __new__(
        cls, *arguments: object, **keywords: object
    ) -> "AccountAuthorityUnavailableError":
        if cls is not AccountAuthorityUnavailableError:
            raise TypeError("account authority availability errors are closed")
        del arguments, keywords
        return RuntimeError.__new__(cls)

    def __init__(self, *arguments: object, **keywords: object) -> None:
        del arguments, keywords
        RuntimeError.__init__(self)

    def __str__(self) -> str:
        return "account authority unavailable"

    def __repr__(self) -> str:
        return "AccountAuthorityUnavailableError()"

    @property
    def args(self) -> tuple[object, ...]:
        return ()

    @args.setter
    def args(self, value: object) -> None:
        del value


def _raise_configuration_error() -> None:
    error = AccountAuthorityConfigurationError()
    try:
        raise error
    finally:
        object.__setattr__(error, "__context__", None)
        object.__setattr__(error, "__cause__", None)


_DATABASE_URL_TOKEN = object()
_PARSE_FAILED = object()


class AccountReaderDatabaseUrl:
    """Parser-controlled reader URL whose display is always redacted."""

    __slots__ = ("_value",)

    def __init_subclass__(cls, **keywords: object) -> None:
        del cls, keywords
        _raise_configuration_error()

    def __init__(self, token: object, value: str) -> None:
        if token is not _DATABASE_URL_TOKEN or type(value) is not str:
            _raise_configuration_error()
        object.__setattr__(self, "_value", value)

    @property
    def value(self) -> str:
        return object.__getattribute__(self, "_value")

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        _raise_configuration_error()

    def __delattr__(self, name: str) -> None:
        del name
        _raise_configuration_error()

    def __repr__(self) -> str:
        return "AccountReaderDatabaseUrl(<redacted>)"

    __str__ = __repr__

    def __reduce__(self) -> object:
        _raise_configuration_error()

    def __reduce_ex__(self, protocol: object) -> object:
        del protocol
        _raise_configuration_error()


def _clean_text(value: object, *, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or value != value.strip()
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        _raise_configuration_error()
    return value


def _decode_url_component(value: str) -> str:
    if _INVALID_PERCENT_ESCAPE.search(value) is not None:
        _raise_configuration_error()
    try:
        decoded = unquote(value, encoding="utf-8", errors="strict")
    except Exception:
        _raise_configuration_error()
    return _clean_text(decoded, maximum=_MAX_DATABASE_URL_CHARACTERS)


def _parse_reader_database_url(value: object) -> AccountReaderDatabaseUrl:
    raw = _clean_text(value, maximum=_MAX_DATABASE_URL_CHARACTERS)
    scheme, separator, remainder = raw.partition("://")
    if separator != "://" or scheme != "postgresql" or not remainder:
        _raise_configuration_error()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except Exception:
        _raise_configuration_error()
    if (
        parsed.scheme != "postgresql"
        or parsed.fragment
        or not parsed.netloc
        or parsed.netloc.count("@") != 1
        or parsed.netloc.partition("@")[0].count(":") != 1
        or parsed.username is None
        or parsed.password is None
        or parsed.hostname is None
    ):
        _raise_configuration_error()
    _decode_url_component(parsed.username)
    _decode_url_component(parsed.password)
    if "%" in parsed.hostname:
        _raise_configuration_error()
    hostname = _clean_text(parsed.hostname, maximum=253)
    if not hostname.isascii() or any(character.isspace() for character in hostname):
        _raise_configuration_error()
    if parsed.path.count("/") != 1 or not parsed.path.startswith("/"):
        _raise_configuration_error()
    database = _decode_url_component(parsed.path[1:])
    if "/" in database:
        _raise_configuration_error()
    if port is not None and not 1 <= port <= 65_535:
        _raise_configuration_error()
    query_parts = parsed.query.split("&")
    if set(query_parts) != {
        "sslmode=require",
        "channel_binding=require",
    } or len(query_parts) != 2:
        _raise_configuration_error()
    return AccountReaderDatabaseUrl(_DATABASE_URL_TOKEN, raw)


def _parse_account_reader_database_url_worker(environment: object) -> object:
    try:
        if not isinstance(environment, Mapping):
            _raise_configuration_error()
        value = environment[_READER_DATABASE_URL_VARIABLE]
        result: object = _parse_reader_database_url(value)
    except Exception:
        result = _PARSE_FAILED
    return result


def parse_account_reader_database_url(
    environment: Mapping[str, str],
) -> AccountReaderDatabaseUrl:
    """Parse the dedicated reader URL from a caller-supplied mapping only."""

    result = _parse_account_reader_database_url_worker(environment)
    if type(result) is AccountReaderDatabaseUrl:
        return result
    del environment, result
    _raise_configuration_error()


class _ConnectCallable(Protocol):
    def __call__(
        self,
        conninfo: str,
        *,
        autocommit: bool,
        connect_timeout: int,
    ) -> object:
        ...


def _default_connect(
    conninfo: str, *, autocommit: bool, connect_timeout: int
) -> object:
    import psycopg

    return psycopg.connect(
        conninfo,
        autocommit=autocommit,
        connect_timeout=connect_timeout,
    )


class AccountReaderConnectionFactory:
    """Return one fresh non-autocommit, TLS-proven Psycopg connection."""

    __slots__ = ("_database_url", "_connect")

    def __init__(
        self,
        database_url: AccountReaderDatabaseUrl,
        connect: _ConnectCallable | None = None,
    ) -> None:
        if type(database_url) is not AccountReaderDatabaseUrl:
            _raise_configuration_error()
        if connect is not None and not callable(connect):
            _raise_configuration_error()
        object.__setattr__(self, "_database_url", database_url)
        object.__setattr__(self, "_connect", connect)

    def __repr__(self) -> str:
        return "AccountReaderConnectionFactory(<redacted>)"

    def __call__(self) -> object:
        database_url = object.__getattribute__(self, "_database_url")
        connect = object.__getattribute__(self, "_connect")
        connector = _default_connect if connect is None else connect
        connection = connector(
            database_url.value,
            autocommit=False,
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
        )
        try:
            if getattr(connection, "autocommit") is not False:
                raise AccountAuthorityUnavailableError()
            pgconn = getattr(connection, "pgconn")
            if getattr(pgconn, "ssl_in_use") is not True:
                raise AccountAuthorityUnavailableError()
        except BaseException:
            try:
                getattr(connection, "close")()
            except BaseException:
                pass
            raise
        return connection


class _CurrentAccountRepository(Protocol):
    def resolve_current_account_by_identity(
        self,
        identity_key: contract.AuthenticationIdentityLookupKey,
        workspace_id: str,
    ) -> contract.CurrentAccountAuthorityResult:
        ...

    def read_current_account_by_user(
        self,
        user_id: str,
        workspace_id: str,
    ) -> contract.CurrentAccountByUserAuthorityResult:
        ...


class _StorageProtocolError(Exception):
    __slots__ = ()


class _UnavailableConnectionState(Exception):
    __slots__ = ()


class _Candidate:
    __slots__ = (
        "user_id",
        "user_row_version",
        "security_epoch",
        "email_id",
        "email_row_version",
        "identity_id",
        "identity_row_version",
        "workspace_id",
        "workspace_row_version",
        "membership_row_version",
    )

    def __init__(
        self,
        user_id: str,
        user_row_version: int,
        security_epoch: int,
        email_id: str,
        email_row_version: int,
        identity_id: str,
        identity_row_version: int,
        workspace_id: str,
        workspace_row_version: int,
        membership_row_version: int,
    ) -> None:
        self.user_id = user_id
        self.user_row_version = user_row_version
        self.security_epoch = security_epoch
        self.email_id = email_id
        self.email_row_version = email_row_version
        self.identity_id = identity_id
        self.identity_row_version = identity_row_version
        self.workspace_id = workspace_id
        self.workspace_row_version = workspace_row_version
        self.membership_row_version = membership_row_version


class _DriverContract:
    __slots__ = (
        "idle_transaction_status",
        "unknown_transaction_status",
        "availability_exceptions",
    )

    def __init__(
        self,
        idle_transaction_status: object,
        unknown_transaction_status: object,
        availability_exceptions: tuple[type[BaseException], ...],
    ) -> None:
        if (
            idle_transaction_status is unknown_transaction_status
            or type(availability_exceptions) is not tuple
            or any(
                type(exception_type) is not type
                or not issubclass(exception_type, BaseException)
                for exception_type in availability_exceptions
            )
        ):
            raise TypeError("invalid account authority driver contract")
        self.idle_transaction_status = idle_transaction_status
        self.unknown_transaction_status = unknown_transaction_status
        self.availability_exceptions = availability_exceptions


def _default_driver_contract() -> _DriverContract:
    import psycopg

    return _DriverContract(
        psycopg.pq.TransactionStatus.IDLE,
        psycopg.pq.TransactionStatus.UNKNOWN,
        (
            psycopg.OperationalError,
            psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected,
        ),
    )


def _normalize_driver_contract(value: object | None) -> _DriverContract:
    if value is None:
        return _default_driver_contract()
    if type(value) is _DriverContract:
        return value
    if type(value) is tuple and len(value) == 3:
        return _DriverContract(value[0], value[1], value[2])
    raise TypeError("invalid account authority driver contract")


def _failure_outcome(
    error: BaseException,
    driver: _DriverContract,
) -> contract.CurrentAccountReadOutcome:
    if isinstance(
        error,
        (
            AccountAuthorityConfigurationError,
            AccountAuthorityUnavailableError,
            _UnavailableConnectionState,
            *driver.availability_exceptions,
        ),
    ):
        return contract.CurrentAccountReadOutcome.UNAVAILABLE
    return contract.CurrentAccountReadOutcome.INTERNAL_ERROR


def _combine_outcomes(
    first: contract.CurrentAccountReadOutcome | None,
    second: contract.CurrentAccountReadOutcome | None,
) -> contract.CurrentAccountReadOutcome | None:
    if (
        first is contract.CurrentAccountReadOutcome.INTERNAL_ERROR
        or second is contract.CurrentAccountReadOutcome.INTERNAL_ERROR
    ):
        return contract.CurrentAccountReadOutcome.INTERNAL_ERROR
    if (
        first is contract.CurrentAccountReadOutcome.UNAVAILABLE
        or second is contract.CurrentAccountReadOutcome.UNAVAILABLE
    ):
        return contract.CurrentAccountReadOutcome.UNAVAILABLE
    return None


def _cleanup_connection(
    connection: object,
    cursor: object | None,
    driver: _DriverContract,
) -> tuple[contract.CurrentAccountReadOutcome | None, BaseException | None]:
    outcome: contract.CurrentAccountReadOutcome | None = None
    fatal: BaseException | None = None
    actions: list[tuple[object, str]] = []
    if cursor is not None:
        actions.append((cursor, "close"))
    actions.extend(((connection, "rollback"), (connection, "close")))
    for target, method_name in actions:
        try:
            getattr(target, method_name)()
        except Exception as error:
            outcome = _combine_outcomes(
                outcome,
                _failure_outcome(error, driver),
            )
        except BaseException as error:
            if fatal is None:
                fatal = error
    return outcome, fatal


def _validate_transaction_status(connection: object, driver: _DriverContract) -> None:
    info = getattr(connection, "info")
    status = getattr(info, "transaction_status")
    if status is driver.idle_transaction_status:
        return
    if status is driver.unknown_transaction_status:
        raise _UnavailableConnectionState()
    raise _StorageProtocolError()


def _valid_record_id(value: object, prefix: str) -> bool:
    if (
        type(value) is not str
        or len(value) != len(prefix) + 22
        or not value.startswith(prefix)
    ):
        return False
    encoded = value[len(prefix) :]
    try:
        decoded = base64.b64decode(
            encoded.encode("ascii") + b"==",
            altchars=b"-_",
            validate=True,
        )
    except Exception:
        return False
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    return len(decoded) == 16 and canonical == encoded


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _decode_candidate_row(row: object) -> _Candidate:
    if type(row) is not tuple or len(row) != 10:
        raise _StorageProtocolError()
    (
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
    ) = row
    try:
        contract.validate_current_account_user_id(user_id)
        contract.validate_current_account_workspace_id(workspace_id)
    except Exception:
        raise _StorageProtocolError() from None
    if (
        not _positive_int(user_row_version)
        or not _positive_int(security_epoch)
        or not _valid_record_id(email_id, "vem_")
        or not _positive_int(email_row_version)
        or not _valid_record_id(identity_id, "aid_")
        or not _positive_int(identity_row_version)
        or not _positive_int(workspace_row_version)
        or not _positive_int(membership_row_version)
    ):
        raise _StorageProtocolError()
    return _Candidate(
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


def _read_candidate_rows(
    cursor: object,
    identity_key: contract.AuthenticationIdentityLookupKey,
) -> tuple[contract.CurrentAccountReadOutcome, _Candidate | None]:
    getattr(cursor, "execute")(_SET_TRANSACTION_SQL)
    getattr(cursor, "execute")(
        _SELECT_UNIQUE_ACTIVE_WORKSPACE_SQL,
        (identity_key.issuer, identity_key.subject),
    )
    rows = getattr(cursor, "fetchall")()
    if type(rows) is not list:
        raise _StorageProtocolError()
    candidates = tuple(_decode_candidate_row(row) for row in rows)
    if len(candidates) != 1:
        return contract.CurrentAccountReadOutcome.NOT_AUTHORIZED, None
    return contract.CurrentAccountReadOutcome.FOUND, candidates[0]


def _not_authorized_result() -> contract.CurrentAccountAuthorityResult:
    return contract.CurrentAccountAuthorityResult(
        contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
        None,
    )


def _failure_result(
    outcome: contract.CurrentAccountReadOutcome,
) -> contract.CurrentAccountAuthorityResult:
    if not any(
        outcome is accepted
        for accepted in (
            contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
            contract.CurrentAccountReadOutcome.UNAVAILABLE,
            contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
        )
    ):
        outcome = contract.CurrentAccountReadOutcome.INTERNAL_ERROR
    return contract.CurrentAccountAuthorityResult(outcome, None)


def _authority_matches_candidate(
    result: object,
    identity_key: contract.AuthenticationIdentityLookupKey,
    candidate: _Candidate,
) -> bool:
    try:
        if (
            type(result) is not contract.CurrentAccountAuthorityResult
            or result.outcome is not contract.CurrentAccountReadOutcome.FOUND
            or type(result.authority) is not contract.CurrentAccountAuthority
        ):
            return False
        authority = result.authority
        return (
            authority.user.user_id == candidate.user_id
            and authority.user.row_version == candidate.user_row_version
            and authority.user.security_epoch == candidate.security_epoch
            and authority.primary_verified_email.email_id == candidate.email_id
            and authority.primary_verified_email.row_version
            == candidate.email_row_version
            and authority.authentication_identity.identity_id
            == candidate.identity_id
            and authority.authentication_identity.row_version
            == candidate.identity_row_version
            and authority.workspace.workspace_id == candidate.workspace_id
            and authority.workspace.row_version == candidate.workspace_row_version
            and authority.workspace_membership.user_id == candidate.user_id
            and authority.workspace_membership.workspace_id
            == candidate.workspace_id
            and authority.workspace_membership.row_version
            == candidate.membership_row_version
            and authority.authentication_identity.user_id == candidate.user_id
            and authority.authentication_identity.issuer == identity_key.issuer
            and authority.authentication_identity.subject == identity_key.subject
            and authority.authentication_identity.method
            is models.AuthenticationMethod.EMAIL_OTP
            and authority.authentication_identity.status
            is models.AuthenticationIdentityStatus.ACTIVE
            and authority.authentication_identity.verified_email_id
            == authority.primary_verified_email.email_id
            and authority.user.status is models.UserStatus.ACTIVE
            and authority.primary_verified_email.status
            is models.VerifiedEmailStatus.VERIFIED
            and authority.workspace.status is models.WorkspaceStatus.ACTIVE
            and authority.workspace_membership.status
            is models.WorkspaceMembershipStatus.ACTIVE
        )
    except Exception:
        return False


def _same_candidate(first: _Candidate, second: _Candidate) -> bool:
    return all(
        getattr(first, field_name) == getattr(second, field_name)
        for field_name in _Candidate.__slots__
    )


class RuntimeAccountAuthority:
    """Unique-workspace identity resolver plus existing by-user revalidation."""

    __slots__ = (
        "_connection_factory",
        "_repository",
        "_driver_contract",
    )

    def __init__(
        self,
        connection_factory: Callable[[], object],
        repository: _CurrentAccountRepository,
        *,
        driver_contract: object | None = None,
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("account authority connection factory must be callable")
        object.__setattr__(self, "_connection_factory", connection_factory)
        object.__setattr__(self, "_repository", repository)
        object.__setattr__(self, "_driver_contract", driver_contract)

    def _driver(self) -> _DriverContract:
        current = object.__getattribute__(self, "_driver_contract")
        if type(current) is _DriverContract:
            return current
        resolved = _normalize_driver_contract(current)
        object.__setattr__(self, "_driver_contract", resolved)
        return resolved

    def _read_unique_candidate(
        self,
        identity_key: contract.AuthenticationIdentityLookupKey,
    ) -> tuple[contract.CurrentAccountReadOutcome, _Candidate | None]:
        try:
            driver = self._driver()
        except Exception:
            return contract.CurrentAccountReadOutcome.INTERNAL_ERROR, None
        factory = object.__getattribute__(self, "_connection_factory")
        try:
            connection = factory()
        except Exception as error:
            return _failure_outcome(error, driver), None

        cursor: object | None = None
        decision: tuple[
            contract.CurrentAccountReadOutcome,
            _Candidate | None,
        ] | None = None
        failure: contract.CurrentAccountReadOutcome | None = None
        fatal: BaseException | None = None
        try:
            if getattr(connection, "autocommit") is not False:
                raise _StorageProtocolError()
            _validate_transaction_status(connection, driver)
            cursor = getattr(connection, "cursor")()
            decision = _read_candidate_rows(cursor, identity_key)
        except Exception as error:
            failure = _failure_outcome(error, driver)
        except BaseException as error:
            fatal = error

        cleanup_outcome, cleanup_fatal = _cleanup_connection(
            connection,
            cursor,
            driver,
        )
        if fatal is not None:
            raise fatal
        if cleanup_fatal is not None:
            raise cleanup_fatal
        combined = _combine_outcomes(failure, cleanup_outcome)
        if combined is not None:
            return combined, None
        if decision is None:
            return contract.CurrentAccountReadOutcome.INTERNAL_ERROR, None
        return decision

    def resolve_current_account_by_identity(
        self,
        identity_key: contract.AuthenticationIdentityLookupKey,
    ) -> contract.CurrentAccountAuthorityResult:
        contract.validate_authentication_identity_lookup_key(identity_key)
        before_outcome, before = self._read_unique_candidate(identity_key)
        if before_outcome is not contract.CurrentAccountReadOutcome.FOUND:
            return _failure_result(before_outcome)
        if type(before) is not _Candidate:
            return _failure_result(contract.CurrentAccountReadOutcome.INTERNAL_ERROR)

        repository = object.__getattribute__(self, "_repository")
        try:
            result = repository.resolve_current_account_by_identity(
                identity_key,
                before.workspace_id,
            )
        except Exception as error:
            try:
                driver = self._driver()
            except Exception:
                return _failure_result(
                    contract.CurrentAccountReadOutcome.INTERNAL_ERROR
                )
            return _failure_result(_failure_outcome(error, driver))
        if type(result) is not contract.CurrentAccountAuthorityResult:
            return _failure_result(contract.CurrentAccountReadOutcome.INTERNAL_ERROR)
        try:
            result_outcome = result.outcome
        except Exception:
            return _failure_result(contract.CurrentAccountReadOutcome.INTERNAL_ERROR)
        if type(result_outcome) is not contract.CurrentAccountReadOutcome:
            return _failure_result(contract.CurrentAccountReadOutcome.INTERNAL_ERROR)
        if result_outcome is not contract.CurrentAccountReadOutcome.FOUND:
            return result
        if not _authority_matches_candidate(result, identity_key, before):
            return _not_authorized_result()

        after_outcome, after = self._read_unique_candidate(identity_key)
        if after_outcome is not contract.CurrentAccountReadOutcome.FOUND:
            return _failure_result(after_outcome)
        if (
            type(after) is not _Candidate
            or not _same_candidate(before, after)
        ):
            return _not_authorized_result()
        return result

    def read_current_account_by_user(
        self,
        user_id: str,
        workspace_id: str,
    ) -> contract.CurrentAccountByUserAuthorityResult:
        repository = object.__getattribute__(self, "_repository")
        return repository.read_current_account_by_user(user_id, workspace_id)


def _default_repository_factory(
    connection_factory: AccountReaderConnectionFactory,
) -> _CurrentAccountRepository:
    from cuevion_db.postgresql_current_account_repository import (
        PostgreSQLCurrentAccountRepository,
    )

    return PostgreSQLCurrentAccountRepository(connection_factory)


def build_runtime_account_authority(
    environment: Mapping[str, str],
    *,
    connect: _ConnectCallable | None = None,
    repository_factory: Callable[
        [AccountReaderConnectionFactory], _CurrentAccountRepository
    ]
    | None = None,
    driver_contract: object | None = None,
) -> RuntimeAccountAuthority:
    """Compose the runtime authority boundary from caller-supplied values."""

    database_url = parse_account_reader_database_url(environment)
    connection_factory = AccountReaderConnectionFactory(database_url, connect)
    factory = (
        _default_repository_factory
        if repository_factory is None
        else repository_factory
    )
    repository = factory(connection_factory)
    return RuntimeAccountAuthority(
        connection_factory,
        repository,
        driver_contract=driver_contract,
    )


def auth0_authority_matches(
    result: object,
    identity_key: object,
    canonical_email: object,
) -> bool:
    """Return whether a FOUND result exactly matches validated Auth0 claims."""

    try:
        contract.validate_authentication_identity_lookup_key(identity_key)
        if (
            type(canonical_email) is not str
            or not canonical_email
            or len(canonical_email) > 320
            or not canonical_email.isascii()
            or type(result) is not contract.CurrentAccountAuthorityResult
            or result.outcome is not contract.CurrentAccountReadOutcome.FOUND
            or type(result.authority) is not contract.CurrentAccountAuthority
        ):
            return False
        authority = result.authority
        return (
            authority.authentication_identity.issuer == identity_key.issuer
            and authority.authentication_identity.subject == identity_key.subject
            and authority.authentication_identity.method
            is models.AuthenticationMethod.EMAIL_OTP
            and authority.authentication_identity.status
            is models.AuthenticationIdentityStatus.ACTIVE
            and authority.authentication_identity.verified_email_id
            == authority.primary_verified_email.email_id
            and authority.primary_verified_email.canonical_email == canonical_email
            and authority.primary_verified_email.status
            is models.VerifiedEmailStatus.VERIFIED
            and authority.user.status is models.UserStatus.ACTIVE
            and authority.workspace.status is models.WorkspaceStatus.ACTIVE
            and authority.workspace_membership.status
            is models.WorkspaceMembershipStatus.ACTIVE
            and authority.authentication_identity.user_id == authority.user.user_id
            and authority.primary_verified_email.user_id == authority.user.user_id
            and authority.workspace_membership.user_id == authority.user.user_id
            and authority.workspace_membership.workspace_id
            == authority.workspace.workspace_id
        )
    except Exception:
        return False
