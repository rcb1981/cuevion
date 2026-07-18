"""Inactive Psycopg 3 adapter for current-account authority reads.

The module contains fixed read-only SQL and a caller-injected synchronous
connection boundary.  It owns no DSN, pool, environment lookup, route,
session, logging, retry, or runtime bootstrap.
"""

import sys as _sys


if (
    __name__ != "cuevion_db.postgresql_current_account_repository"
    or __package__ != "cuevion_db"
):
    raise ImportError(
        "PostgreSQL current-account repository requires its canonical import identity"
    )
if (
    getattr(
        _sys.modules.get(
            "cuevion_db.postgresql_current_account_repository"
        ),
        "__dict__",
        None,
    )
    is not globals()
):
    raise ImportError(
        "PostgreSQL current-account repository requires its canonical module object"
    )
if "_POSTGRESQL_CURRENT_ACCOUNT_REPOSITORY_INITIALIZED" in globals():
    raise ImportError(
        "PostgreSQL current-account repository cannot initialize twice"
    )
_POSTGRESQL_CURRENT_ACCOUNT_REPOSITORY_INITIALIZED = True

import base64 as _base64
import re as _re
import unicodedata as _unicodedata
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
from datetime import timezone as _timezone
from typing import Callable as _Callable
from typing import Protocol as _Protocol

import psycopg as _psycopg

from api.auth import models as _models
from cuevion_auth import current_account_repository_contract as _contract


if _models is not _sys.modules.get("api.auth.models"):
    raise ImportError("account models require their canonical import identity")
if _contract is not _sys.modules.get(
    "cuevion_auth.current_account_repository_contract"
):
    raise ImportError(
        "current-account contract requires its canonical import identity"
    )


__all__ = (
    "PostgreSQLConnectionFactory",
    "PostgreSQLCurrentAccountRepository",
)


class PostgreSQLConnectionFactory(_Protocol):
    """Return one fresh synchronous Psycopg-compatible connection per call."""

    def __call__(self) -> object:
        ...


_EPOCH = _datetime(1970, 1, 1, tzinfo=_timezone.utc)
_MAX_TIMESTAMP = 253_402_300_799
_BASE64URL_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789-_"
)
_RECORD_ID_ENCODED_LENGTH = 22
_USER_ID_PREFIX = "usr_"
_VERIFIED_EMAIL_ID_PREFIX = "vem_"
_AUTHENTICATION_IDENTITY_ID_PREFIX = "aid_"
_WORKSPACE_ID_PREFIX = "wsp_"
_DISPLAY_NAME_MAX_UTF8_BYTES = 256
_EMAIL_MAX_CHARACTERS = 320
_EMAIL_LOCAL_MAX_CHARACTERS = 64
_EMAIL_DOMAIN_MAX_CHARACTERS = 253
_VERIFICATION_SOURCE_MAX_CHARACTERS = 128
_ISSUER_MAX_CHARACTERS = 512
_SUBJECT_MAX_CHARACTERS = 512
_EMAIL_RE = _re.compile(
    r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
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
_IDENTITY_RESULT_WIDTH = (
    len(_USER_COLUMNS)
    + len(_VERIFIED_EMAIL_COLUMNS)
    + len(_AUTHENTICATION_IDENTITY_COLUMNS)
    + len(_WORKSPACE_COLUMNS)
    + len(_WORKSPACE_MEMBERSHIP_COLUMNS)
)
_USER_RESULT_WIDTH = (
    len(_USER_COLUMNS)
    + len(_VERIFIED_EMAIL_COLUMNS)
    + len(_WORKSPACE_COLUMNS)
    + len(_WORKSPACE_MEMBERSHIP_COLUMNS)
)

_SET_TRANSACTION_SQL = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
)

_SELECT_CURRENT_ACCOUNT_BY_IDENTITY_SQL = """
WITH request(issuer, subject, workspace_id) AS (
    VALUES (%s, %s, %s)
)
SELECT
    user_record.schema_version,
    user_record.user_id,
    user_record.status,
    user_record.primary_verified_email_id,
    user_record.display_name,
    user_record.security_epoch,
    user_record.created_at,
    user_record.updated_at,
    user_record.row_version,
    email_record.schema_version,
    email_record.email_id,
    email_record.user_id,
    email_record.canonical_email,
    email_record.status,
    email_record.verification_source,
    email_record.created_at,
    email_record.verified_at,
    email_record.retired_at,
    email_record.row_version,
    identity_record.schema_version,
    identity_record.identity_id,
    identity_record.user_id,
    identity_record.issuer,
    identity_record.subject,
    identity_record.authentication_method,
    identity_record.status,
    identity_record.verified_email_id,
    identity_record.created_at,
    identity_record.last_used_at,
    identity_record.row_version,
    workspace_record.schema_version,
    workspace_record.workspace_id,
    workspace_record.status,
    workspace_record.created_by_user_id,
    workspace_record.created_at,
    workspace_record.updated_at,
    workspace_record.row_version,
    membership_record.schema_version,
    membership_record.workspace_id,
    membership_record.user_id,
    membership_record.role,
    membership_record.status,
    membership_record.created_at,
    membership_record.updated_at,
    membership_record.row_version
FROM request AS request_row
LEFT JOIN cuevion_account.authentication_identities AS identity_record
    ON identity_record.issuer = request_row.issuer
    AND identity_record.subject = request_row.subject
LEFT JOIN cuevion_account.users AS user_record
    ON user_record.user_id = identity_record.user_id
LEFT JOIN cuevion_account.verified_emails AS email_record
    ON email_record.email_id = user_record.primary_verified_email_id
    AND email_record.user_id = user_record.user_id
LEFT JOIN cuevion_account.workspaces AS workspace_record
    ON user_record.user_id IS NOT NULL
    AND workspace_record.workspace_id = request_row.workspace_id
LEFT JOIN cuevion_account.workspace_memberships AS membership_record
    ON membership_record.workspace_id = workspace_record.workspace_id
    AND membership_record.user_id = user_record.user_id
""".strip()

_SELECT_CURRENT_ACCOUNT_BY_USER_SQL = """
WITH request(user_id, workspace_id) AS (
    VALUES (%s, %s)
)
SELECT
    user_record.schema_version,
    user_record.user_id,
    user_record.status,
    user_record.primary_verified_email_id,
    user_record.display_name,
    user_record.security_epoch,
    user_record.created_at,
    user_record.updated_at,
    user_record.row_version,
    email_record.schema_version,
    email_record.email_id,
    email_record.user_id,
    email_record.canonical_email,
    email_record.status,
    email_record.verification_source,
    email_record.created_at,
    email_record.verified_at,
    email_record.retired_at,
    email_record.row_version,
    workspace_record.schema_version,
    workspace_record.workspace_id,
    workspace_record.status,
    workspace_record.created_by_user_id,
    workspace_record.created_at,
    workspace_record.updated_at,
    workspace_record.row_version,
    membership_record.schema_version,
    membership_record.workspace_id,
    membership_record.user_id,
    membership_record.role,
    membership_record.status,
    membership_record.created_at,
    membership_record.updated_at,
    membership_record.row_version
FROM request AS request_row
LEFT JOIN cuevion_account.users AS user_record
    ON user_record.user_id = request_row.user_id
LEFT JOIN cuevion_account.verified_emails AS email_record
    ON email_record.email_id = user_record.primary_verified_email_id
    AND email_record.user_id = user_record.user_id
LEFT JOIN cuevion_account.workspaces AS workspace_record
    ON user_record.user_id IS NOT NULL
    AND workspace_record.workspace_id = request_row.workspace_id
LEFT JOIN cuevion_account.workspace_memberships AS membership_record
    ON membership_record.workspace_id = workspace_record.workspace_id
    AND membership_record.user_id = user_record.user_id
""".strip()


_USER_STATUS_BY_TEXT = {
    "active": _models.UserStatus.ACTIVE,
    "suspended": _models.UserStatus.SUSPENDED,
    "disabled": _models.UserStatus.DISABLED,
}
_VERIFIED_EMAIL_STATUS_BY_TEXT = {
    "pending": _models.VerifiedEmailStatus.PENDING,
    "verified": _models.VerifiedEmailStatus.VERIFIED,
    "retired": _models.VerifiedEmailStatus.RETIRED,
}
_AUTHENTICATION_METHOD_BY_TEXT = {
    "email_otp": _models.AuthenticationMethod.EMAIL_OTP,
    "oidc": _models.AuthenticationMethod.OIDC,
    "webauthn": _models.AuthenticationMethod.WEBAUTHN,
}
_AUTHENTICATION_IDENTITY_STATUS_BY_TEXT = {
    "active": _models.AuthenticationIdentityStatus.ACTIVE,
    "disabled": _models.AuthenticationIdentityStatus.DISABLED,
    "revoked": _models.AuthenticationIdentityStatus.REVOKED,
}
_WORKSPACE_STATUS_BY_TEXT = {
    "active": _models.WorkspaceStatus.ACTIVE,
    "suspended": _models.WorkspaceStatus.SUSPENDED,
    "archived": _models.WorkspaceStatus.ARCHIVED,
}
_WORKSPACE_ROLE_BY_TEXT = {
    "owner": _models.WorkspaceRole.OWNER,
    "admin": _models.WorkspaceRole.ADMIN,
    "member": _models.WorkspaceRole.MEMBER,
}
_WORKSPACE_MEMBERSHIP_STATUS_BY_TEXT = {
    "active": _models.WorkspaceMembershipStatus.ACTIVE,
    "suspended": _models.WorkspaceMembershipStatus.SUSPENDED,
    "removed": _models.WorkspaceMembershipStatus.REMOVED,
}


class _StorageCorruption(Exception):
    __slots__ = ()

    def __init__(self) -> None:
        Exception.__init__(self)


def _text_from_database(value: object) -> str:
    if type(value) is not str:
        raise _StorageCorruption()
    return value


def _int_from_database(value: object) -> int:
    if type(value) is not int:
        raise _StorageCorruption()
    return value


def _schema_one_from_database(value: object) -> int:
    result = _int_from_database(value)
    if result != 1:
        raise _StorageCorruption()
    return result


def _positive_int_from_database(value: object) -> int:
    result = _int_from_database(value)
    if result <= 0:
        raise _StorageCorruption()
    return result


def _timestamp_to_database(value: object) -> _datetime:
    if type(value) is not int or value < 0 or value > _MAX_TIMESTAMP:
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
    if (
        difference.microseconds != 0
        or result < 0
        or result > _MAX_TIMESTAMP
    ):
        raise _StorageCorruption()
    if _timestamp_to_database(result) != utc_value:
        raise _StorageCorruption()
    return result


def _optional_timestamp_from_database(value: object) -> int | None:
    if value is None:
        return None
    return _timestamp_from_database(value)


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


def _identifier_from_database(value: object, prefix: str) -> str:
    result = _text_from_database(value)
    if (
        len(result) != len(prefix) + _RECORD_ID_ENCODED_LENGTH
        or result[: len(prefix)] != prefix
    ):
        raise _StorageCorruption()
    decoded = _decode_base64url(result[len(prefix) :])
    if len(decoded) != 16:
        raise _StorageCorruption()
    return result


def _optional_identifier_from_database(
    value: object, prefix: str
) -> str | None:
    if value is None:
        return None
    return _identifier_from_database(value, prefix)


def _canonical_email_from_database(value: object) -> str:
    result = _text_from_database(value)
    if (
        not result
        or not result.isascii()
        or len(result) > _EMAIL_MAX_CHARACTERS
        or _EMAIL_RE.fullmatch(result) is None
    ):
        raise _StorageCorruption()
    local_part, domain = result.split("@")
    if (
        len(local_part) > _EMAIL_LOCAL_MAX_CHARACTERS
        or len(domain) > _EMAIL_DOMAIN_MAX_CHARACTERS
    ):
        raise _StorageCorruption()
    return result


def _display_name_from_database(value: object) -> str:
    result = _text_from_database(value)
    if not result or any(
        _unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in result
    ):
        raise _StorageCorruption()
    try:
        encoded_length = len(result.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        raise _StorageCorruption() from None
    if encoded_length > _DISPLAY_NAME_MAX_UTF8_BYTES:
        raise _StorageCorruption()
    return result


def _ascii_security_identifier_from_database(
    value: object, maximum: int
) -> str:
    result = _text_from_database(value)
    if (
        not result
        or not result.isascii()
        or len(result) > maximum
        or any(ord(character) < 33 or ord(character) > 126 for character in result)
    ):
        raise _StorageCorruption()
    return result


def _enum_from_database(
    value: object, members_by_text: dict[str, object]
) -> object:
    text = _text_from_database(value)
    member = members_by_text.get(text)
    if member is None:
        raise _StorageCorruption()
    return member


def _decode_user(values: tuple[object, ...]) -> _models.CuevionUser:
    if type(values) is not tuple or len(values) != len(_USER_COLUMNS):
        raise _StorageCorruption()
    schema_version = _schema_one_from_database(values[0])
    user_id = _identifier_from_database(values[1], _USER_ID_PREFIX)
    status = _enum_from_database(values[2], _USER_STATUS_BY_TEXT)
    primary_email_id = _optional_identifier_from_database(
        values[3], _VERIFIED_EMAIL_ID_PREFIX
    )
    display_name = _display_name_from_database(values[4])
    security_epoch = _positive_int_from_database(values[5])
    created_at = _timestamp_from_database(values[6])
    updated_at = _timestamp_from_database(values[7])
    row_version = _positive_int_from_database(values[8])
    if (
        created_at > updated_at
        or status is _models.UserStatus.ACTIVE
        and primary_email_id is None
    ):
        raise _StorageCorruption()
    try:
        return _models.CuevionUser(
            schema_version=schema_version,
            user_id=user_id,
            status=status,
            primary_verified_email_id=primary_email_id,
            display_name=display_name,
            security_epoch=security_epoch,
            created_at=created_at,
            updated_at=updated_at,
            row_version=row_version,
        )
    except Exception:
        raise _StorageCorruption() from None


def _decode_verified_email(
    values: tuple[object, ...]
) -> _models.VerifiedEmail:
    if type(values) is not tuple or len(values) != len(_VERIFIED_EMAIL_COLUMNS):
        raise _StorageCorruption()
    schema_version = _schema_one_from_database(values[0])
    email_id = _identifier_from_database(
        values[1], _VERIFIED_EMAIL_ID_PREFIX
    )
    user_id = _identifier_from_database(values[2], _USER_ID_PREFIX)
    canonical_email = _canonical_email_from_database(values[3])
    status = _enum_from_database(values[4], _VERIFIED_EMAIL_STATUS_BY_TEXT)
    verification_source = _ascii_security_identifier_from_database(
        values[5], _VERIFICATION_SOURCE_MAX_CHARACTERS
    )
    created_at = _timestamp_from_database(values[6])
    verified_at = _optional_timestamp_from_database(values[7])
    retired_at = _optional_timestamp_from_database(values[8])
    row_version = _positive_int_from_database(values[9])
    if status is _models.VerifiedEmailStatus.PENDING:
        lifecycle_is_valid = verified_at is None and retired_at is None
    elif status is _models.VerifiedEmailStatus.VERIFIED:
        lifecycle_is_valid = verified_at is not None and retired_at is None
    elif status is _models.VerifiedEmailStatus.RETIRED:
        lifecycle_is_valid = verified_at is not None and retired_at is not None
    else:
        lifecycle_is_valid = False
    if (
        not lifecycle_is_valid
        or verified_at is not None
        and created_at > verified_at
        or retired_at is not None
        and (verified_at is None or verified_at > retired_at)
    ):
        raise _StorageCorruption()
    try:
        return _models.VerifiedEmail(
            schema_version=schema_version,
            email_id=email_id,
            user_id=user_id,
            canonical_email=canonical_email,
            status=status,
            verification_source=verification_source,
            created_at=created_at,
            verified_at=verified_at,
            retired_at=retired_at,
            row_version=row_version,
        )
    except Exception:
        raise _StorageCorruption() from None


def _decode_authentication_identity(
    values: tuple[object, ...]
) -> _models.AuthenticationIdentity:
    if (
        type(values) is not tuple
        or len(values) != len(_AUTHENTICATION_IDENTITY_COLUMNS)
    ):
        raise _StorageCorruption()
    schema_version = _schema_one_from_database(values[0])
    identity_id = _identifier_from_database(
        values[1], _AUTHENTICATION_IDENTITY_ID_PREFIX
    )
    user_id = _identifier_from_database(values[2], _USER_ID_PREFIX)
    issuer = _ascii_security_identifier_from_database(
        values[3], _ISSUER_MAX_CHARACTERS
    )
    subject = _ascii_security_identifier_from_database(
        values[4], _SUBJECT_MAX_CHARACTERS
    )
    method = _enum_from_database(values[5], _AUTHENTICATION_METHOD_BY_TEXT)
    status = _enum_from_database(
        values[6], _AUTHENTICATION_IDENTITY_STATUS_BY_TEXT
    )
    verified_email_id = _optional_identifier_from_database(
        values[7], _VERIFIED_EMAIL_ID_PREFIX
    )
    created_at = _timestamp_from_database(values[8])
    last_used_at = _optional_timestamp_from_database(values[9])
    row_version = _positive_int_from_database(values[10])
    if last_used_at is not None and created_at > last_used_at:
        raise _StorageCorruption()
    try:
        return _models.AuthenticationIdentity(
            schema_version=schema_version,
            identity_id=identity_id,
            user_id=user_id,
            issuer=issuer,
            subject=subject,
            method=method,
            status=status,
            verified_email_id=verified_email_id,
            created_at=created_at,
            last_used_at=last_used_at,
            row_version=row_version,
        )
    except Exception:
        raise _StorageCorruption() from None


def _decode_workspace(values: tuple[object, ...]) -> _models.Workspace:
    if type(values) is not tuple or len(values) != len(_WORKSPACE_COLUMNS):
        raise _StorageCorruption()
    schema_version = _schema_one_from_database(values[0])
    workspace_id = _identifier_from_database(values[1], _WORKSPACE_ID_PREFIX)
    status = _enum_from_database(values[2], _WORKSPACE_STATUS_BY_TEXT)
    created_by_user_id = _identifier_from_database(
        values[3], _USER_ID_PREFIX
    )
    created_at = _timestamp_from_database(values[4])
    updated_at = _timestamp_from_database(values[5])
    row_version = _positive_int_from_database(values[6])
    if created_at > updated_at:
        raise _StorageCorruption()
    try:
        return _models.Workspace(
            schema_version=schema_version,
            workspace_id=workspace_id,
            status=status,
            created_by_user_id=created_by_user_id,
            created_at=created_at,
            updated_at=updated_at,
            row_version=row_version,
        )
    except Exception:
        raise _StorageCorruption() from None


def _decode_workspace_membership(
    values: tuple[object, ...]
) -> _models.WorkspaceMembership:
    if (
        type(values) is not tuple
        or len(values) != len(_WORKSPACE_MEMBERSHIP_COLUMNS)
    ):
        raise _StorageCorruption()
    schema_version = _schema_one_from_database(values[0])
    workspace_id = _identifier_from_database(values[1], _WORKSPACE_ID_PREFIX)
    user_id = _identifier_from_database(values[2], _USER_ID_PREFIX)
    role = _enum_from_database(values[3], _WORKSPACE_ROLE_BY_TEXT)
    status = _enum_from_database(
        values[4], _WORKSPACE_MEMBERSHIP_STATUS_BY_TEXT
    )
    created_at = _timestamp_from_database(values[5])
    updated_at = _timestamp_from_database(values[6])
    row_version = _positive_int_from_database(values[7])
    if created_at > updated_at:
        raise _StorageCorruption()
    try:
        return _models.WorkspaceMembership(
            schema_version=schema_version,
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            row_version=row_version,
        )
    except Exception:
        raise _StorageCorruption() from None


def _optional_segment(
    values: tuple[object, ...],
    decoder: _Callable[[tuple[object, ...]], object],
) -> object | None:
    if type(values) is not tuple:
        raise _StorageCorruption()
    if all(value is None for value in values):
        return None
    return decoder(values)


def _exact_result_row(
    cursor: object,
    sql: str,
    parameters: tuple[object, ...],
    expected_width: int,
) -> tuple[object, ...]:
    getattr(cursor, "execute")(sql, parameters)
    rows = getattr(cursor, "fetchall")()
    if type(rows) is not list or len(rows) != 1:
        raise _StorageCorruption()
    row = rows[0]
    if type(row) is not tuple or len(row) != expected_width:
        raise _StorageCorruption()
    return row


def _split_identity_row(
    row: tuple[object, ...],
) -> tuple[
    tuple[object, ...],
    tuple[object, ...],
    tuple[object, ...],
    tuple[object, ...],
    tuple[object, ...],
]:
    if type(row) is not tuple or len(row) != _IDENTITY_RESULT_WIDTH:
        raise _StorageCorruption()
    user_end = len(_USER_COLUMNS)
    email_end = user_end + len(_VERIFIED_EMAIL_COLUMNS)
    identity_end = email_end + len(_AUTHENTICATION_IDENTITY_COLUMNS)
    workspace_end = identity_end + len(_WORKSPACE_COLUMNS)
    return (
        row[:user_end],
        row[user_end:email_end],
        row[email_end:identity_end],
        row[identity_end:workspace_end],
        row[workspace_end:],
    )


def _split_user_row(
    row: tuple[object, ...],
) -> tuple[
    tuple[object, ...],
    tuple[object, ...],
    tuple[object, ...],
    tuple[object, ...],
]:
    if type(row) is not tuple or len(row) != _USER_RESULT_WIDTH:
        raise _StorageCorruption()
    user_end = len(_USER_COLUMNS)
    email_end = user_end + len(_VERIFIED_EMAIL_COLUMNS)
    workspace_end = email_end + len(_WORKSPACE_COLUMNS)
    return (
        row[:user_end],
        row[user_end:email_end],
        row[email_end:workspace_end],
        row[workspace_end:],
    )


def _validate_user_email_graph(
    user: _models.CuevionUser,
    email: _models.VerifiedEmail | None,
) -> None:
    primary_email_id = user.primary_verified_email_id
    if primary_email_id is None:
        if email is not None:
            raise _StorageCorruption()
        return None
    if email is None:
        raise _StorageCorruption()
    if email.email_id != primary_email_id or email.user_id != user.user_id:
        raise _StorageCorruption()
    return None


def _validate_workspace_graph(
    user: _models.CuevionUser,
    workspace: _models.Workspace | None,
    membership: _models.WorkspaceMembership | None,
    requested_workspace_id: str,
) -> None:
    if workspace is None:
        if membership is not None:
            raise _StorageCorruption()
        return None
    if workspace.workspace_id != requested_workspace_id:
        raise _StorageCorruption()
    if membership is not None and (
        membership.workspace_id != workspace.workspace_id
        or membership.user_id != user.user_id
    ):
        raise _StorageCorruption()
    return None


def _not_authorized_decision() -> tuple[
    _contract.CurrentAccountReadOutcome, None
]:
    return (_contract.CurrentAccountReadOutcome.NOT_AUTHORIZED, None)


def _decode_identity_authority(
    row: tuple[object, ...],
    identity_key: _contract.AuthenticationIdentityLookupKey,
    workspace_id: str,
) -> tuple[_contract.CurrentAccountReadOutcome, object | None]:
    (
        user_values,
        email_values,
        identity_values,
        workspace_values,
        membership_values,
    ) = _split_identity_row(row)
    user = _optional_segment(user_values, _decode_user)
    email = _optional_segment(email_values, _decode_verified_email)
    identity = _optional_segment(
        identity_values, _decode_authentication_identity
    )
    workspace = _optional_segment(workspace_values, _decode_workspace)
    membership = _optional_segment(
        membership_values, _decode_workspace_membership
    )
    if identity is None:
        if any(
            record is not None
            for record in (user, email, workspace, membership)
        ):
            raise _StorageCorruption()
        return _not_authorized_decision()
    if type(identity) is not _models.AuthenticationIdentity:
        raise _StorageCorruption()
    if user is None:
        if any(
            record is not None for record in (email, workspace, membership)
        ):
            raise _StorageCorruption()
        raise _StorageCorruption()
    if type(user) is not _models.CuevionUser:
        raise _StorageCorruption()
    if email is not None and type(email) is not _models.VerifiedEmail:
        raise _StorageCorruption()
    if workspace is not None and type(workspace) is not _models.Workspace:
        raise _StorageCorruption()
    if (
        membership is not None
        and type(membership) is not _models.WorkspaceMembership
    ):
        raise _StorageCorruption()
    if (
        identity.user_id != user.user_id
        or identity.issuer != identity_key.issuer
        or identity.subject != identity_key.subject
    ):
        raise _StorageCorruption()
    _validate_user_email_graph(user, email)
    _validate_workspace_graph(user, workspace, membership, workspace_id)
    if (
        identity.status is not _models.AuthenticationIdentityStatus.ACTIVE
        or user.status is not _models.UserStatus.ACTIVE
        or email is None
        or email.status is not _models.VerifiedEmailStatus.VERIFIED
        or identity.verified_email_id is not None
        and identity.verified_email_id != email.email_id
        or workspace is None
        or workspace.status is not _models.WorkspaceStatus.ACTIVE
        or membership is None
        or membership.status
        is not _models.WorkspaceMembershipStatus.ACTIVE
    ):
        return _not_authorized_decision()
    try:
        authority = _contract.CurrentAccountAuthority(
            user=user,
            primary_verified_email=email,
            authentication_identity=identity,
            workspace=workspace,
            workspace_membership=membership,
        )
    except Exception:
        raise _StorageCorruption() from None
    return (_contract.CurrentAccountReadOutcome.FOUND, authority)


def _decode_user_authority(
    row: tuple[object, ...],
    requested_user_id: str,
    workspace_id: str,
) -> tuple[_contract.CurrentAccountReadOutcome, object | None]:
    (
        user_values,
        email_values,
        workspace_values,
        membership_values,
    ) = _split_user_row(row)
    user = _optional_segment(user_values, _decode_user)
    email = _optional_segment(email_values, _decode_verified_email)
    workspace = _optional_segment(workspace_values, _decode_workspace)
    membership = _optional_segment(
        membership_values, _decode_workspace_membership
    )
    if user is None:
        if any(
            record is not None for record in (email, workspace, membership)
        ):
            raise _StorageCorruption()
        return _not_authorized_decision()
    if type(user) is not _models.CuevionUser:
        raise _StorageCorruption()
    if email is not None and type(email) is not _models.VerifiedEmail:
        raise _StorageCorruption()
    if workspace is not None and type(workspace) is not _models.Workspace:
        raise _StorageCorruption()
    if (
        membership is not None
        and type(membership) is not _models.WorkspaceMembership
    ):
        raise _StorageCorruption()
    if user.user_id != requested_user_id:
        raise _StorageCorruption()
    _validate_user_email_graph(user, email)
    _validate_workspace_graph(user, workspace, membership, workspace_id)
    if (
        user.status is not _models.UserStatus.ACTIVE
        or email is None
        or email.status is not _models.VerifiedEmailStatus.VERIFIED
        or workspace is None
        or workspace.status is not _models.WorkspaceStatus.ACTIVE
        or membership is None
        or membership.status
        is not _models.WorkspaceMembershipStatus.ACTIVE
    ):
        return _not_authorized_decision()
    try:
        authority = _contract.CurrentAccountByUserAuthority(
            user=user,
            primary_verified_email=email,
            workspace=workspace,
            workspace_membership=membership,
        )
    except Exception:
        raise _StorageCorruption() from None
    return (_contract.CurrentAccountReadOutcome.FOUND, authority)


def _is_availability_failure(error: BaseException) -> bool:
    return isinstance(
        error,
        (
            _psycopg.OperationalError,
            _psycopg.errors.SerializationFailure,
            _psycopg.errors.DeadlockDetected,
        ),
    )


def _failure_outcome(
    error: BaseException,
) -> _contract.CurrentAccountReadOutcome:
    if _is_availability_failure(error):
        return _contract.CurrentAccountReadOutcome.UNAVAILABLE
    return _contract.CurrentAccountReadOutcome.INTERNAL_ERROR


def _combine_cleanup_outcomes(
    first: _contract.CurrentAccountReadOutcome | None,
    second: _contract.CurrentAccountReadOutcome | None,
) -> _contract.CurrentAccountReadOutcome | None:
    if (
        first is _contract.CurrentAccountReadOutcome.INTERNAL_ERROR
        or second is _contract.CurrentAccountReadOutcome.INTERNAL_ERROR
    ):
        return _contract.CurrentAccountReadOutcome.INTERNAL_ERROR
    if (
        first is _contract.CurrentAccountReadOutcome.UNAVAILABLE
        or second is _contract.CurrentAccountReadOutcome.UNAVAILABLE
    ):
        return _contract.CurrentAccountReadOutcome.UNAVAILABLE
    return None


def _attempt_cleanup_action(
    target: object, method_name: str
) -> tuple[_contract.CurrentAccountReadOutcome | None, BaseException | None]:
    try:
        getattr(target, method_name)()
    except Exception as error:
        return (_failure_outcome(error), None)
    except BaseException as error:
        return (None, error)
    return (None, None)


def _cleanup_read(
    connection: object, cursor: object | None
) -> tuple[_contract.CurrentAccountReadOutcome | None, BaseException | None]:
    cleanup_outcome: _contract.CurrentAccountReadOutcome | None = None
    fatal_error: BaseException | None = None
    actions: tuple[tuple[object, str], ...]
    if cursor is None:
        actions = ((connection, "rollback"), (connection, "close"))
    else:
        actions = (
            (cursor, "close"),
            (connection, "rollback"),
            (connection, "close"),
        )
    for target, method_name in actions:
        outcome, action_fatal = _attempt_cleanup_action(target, method_name)
        cleanup_outcome = _combine_cleanup_outcomes(
            cleanup_outcome, outcome
        )
        if fatal_error is None and action_fatal is not None:
            fatal_error = action_fatal
    return (cleanup_outcome, fatal_error)


class PostgreSQLCurrentAccountRepository:
    """Concrete but inert-until-called current-authority read adapter."""

    __slots__ = ("_connection_factory",)

    def __init__(
        self, connection_factory: PostgreSQLConnectionFactory
    ) -> None:
        object.__setattr__(self, "_connection_factory", connection_factory)

    def resolve_current_account_by_identity(
        self,
        identity_key: _contract.AuthenticationIdentityLookupKey,
        workspace_id: str,
    ) -> _contract.CurrentAccountAuthorityResult:
        _contract.validate_authentication_identity_lookup_key(identity_key)
        _contract.validate_current_account_workspace_id(workspace_id)
        issuer = identity_key.issuer
        subject = identity_key.subject
        decision = self._read_validated(
            _SELECT_CURRENT_ACCOUNT_BY_IDENTITY_SQL,
            (issuer, subject, workspace_id),
            _IDENTITY_RESULT_WIDTH,
            lambda row: _decode_identity_authority(
                row, identity_key, workspace_id
            ),
        )
        return _contract.CurrentAccountAuthorityResult(
            outcome=decision[0], authority=decision[1]
        )

    def read_current_account_by_user(
        self,
        user_id: str,
        workspace_id: str,
    ) -> _contract.CurrentAccountByUserAuthorityResult:
        _contract.validate_current_account_user_id(user_id)
        _contract.validate_current_account_workspace_id(workspace_id)
        decision = self._read_validated(
            _SELECT_CURRENT_ACCOUNT_BY_USER_SQL,
            (user_id, workspace_id),
            _USER_RESULT_WIDTH,
            lambda row: _decode_user_authority(
                row, user_id, workspace_id
            ),
        )
        return _contract.CurrentAccountByUserAuthorityResult(
            outcome=decision[0], authority=decision[1]
        )

    def _new_connection(self) -> object:
        factory = object.__getattribute__(self, "_connection_factory")
        return factory()

    def _read_validated(
        self,
        sql: str,
        parameters: tuple[object, ...],
        expected_width: int,
        decoder: _Callable[
            [tuple[object, ...]],
            tuple[_contract.CurrentAccountReadOutcome, object | None],
        ],
    ) -> tuple[_contract.CurrentAccountReadOutcome, object | None]:
        try:
            connection = self._new_connection()
        except Exception as error:
            return (_failure_outcome(error), None)

        cursor: object | None = None
        decision: tuple[
            _contract.CurrentAccountReadOutcome, object | None
        ] | None = None
        failure: _contract.CurrentAccountReadOutcome | None = None
        fatal_error: BaseException | None = None
        try:
            if getattr(connection, "autocommit") is not False:
                raise _StorageCorruption()
            connection_info = getattr(connection, "info")
            if (
                getattr(connection_info, "transaction_status")
                is not _psycopg.pq.TransactionStatus.IDLE
            ):
                raise _StorageCorruption()
            cursor = getattr(connection, "cursor")()
            getattr(cursor, "execute")(_SET_TRANSACTION_SQL)
            row = _exact_result_row(
                cursor, sql, parameters, expected_width
            )
            decision = decoder(row)
        except Exception as error:
            failure = _failure_outcome(error)
        except BaseException as error:
            fatal_error = error

        cleanup_outcome, cleanup_fatal = _cleanup_read(connection, cursor)
        if fatal_error is not None:
            raise fatal_error
        if cleanup_fatal is not None:
            raise cleanup_fatal
        combined_failure = _combine_cleanup_outcomes(
            failure, cleanup_outcome
        )
        if combined_failure is not None:
            return (combined_failure, None)
        if decision is None:
            return (_contract.CurrentAccountReadOutcome.INTERNAL_ERROR, None)
        return decision
