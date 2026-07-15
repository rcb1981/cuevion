"""Inactive, provider-independent account and session record contracts.

This module validates immutable data only.  It performs no authentication,
storage, provider, HTTP, cookie, environment, or service work.
"""

from __future__ import annotations as _annotations

import sys as _sys

if __name__ != "api.auth.models" or __package__ != "api.auth":
    raise ImportError("account models require their canonical import identity")
if (
    getattr(_sys.modules.get("api.auth.models"), "__dict__", None)
    is not globals()
):
    raise ImportError("account models require their canonical module object")
if "_AUTH_A_MODELS_INITIALIZED" in globals():
    raise ImportError("account models cannot be initialized more than once")
_AUTH_A_MODELS_INITIALIZED = True

import base64 as _base64
import re as _re
import unicodedata as _unicodedata
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from enum import Enum as _Enum
from enum import EnumMeta as _EnumMeta


class ModelValidationError(ValueError):
    """A fixed account-model validation failure with no rejected value."""

    __slots__ = ()

    def __new__(
        cls, *_arguments: object, **_keywords: object
    ) -> ModelValidationError:
        return ValueError.__new__(cls)

    def __init__(self, *_arguments: object, **_keywords: object) -> None:
        ValueError.__init__(self)

    def __str__(self) -> str:
        return "account model validation failed"

    def __repr__(self) -> str:
        return "ModelValidationError()"


def _raise_validation_error() -> None:
    error = ModelValidationError()
    try:
        raise error
    finally:
        object.__setattr__(error, "__context__", None)
        object.__setattr__(error, "__cause__", None)


_ENUM_MISSING = object()


class _ClosedStringEnumMeta(_EnumMeta):
    """Resolve only exact strings or an already exact enum member."""

    def __call__(
        cls,
        value: object = _ENUM_MISSING,
        *_arguments: object,
        **_keywords: object,
    ) -> object:
        if _arguments or _keywords:
            _raise_validation_error()
        if type(value) is cls:
            return value
        if type(value) is not str:
            _raise_validation_error()
        member = cls._value2member_map_.get(value, _ENUM_MISSING)
        if type(member) is not cls:
            _raise_validation_error()
        return member


class UserStatus(str, _Enum, metaclass=_ClosedStringEnumMeta):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"


class VerifiedEmailStatus(str, _Enum, metaclass=_ClosedStringEnumMeta):
    PENDING = "pending"
    VERIFIED = "verified"
    RETIRED = "retired"


class AuthenticationMethod(str, _Enum, metaclass=_ClosedStringEnumMeta):
    EMAIL_OTP = "email_otp"
    OIDC = "oidc"
    WEBAUTHN = "webauthn"


class AuthenticationIdentityStatus(str, _Enum, metaclass=_ClosedStringEnumMeta):
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


class WorkspaceStatus(str, _Enum, metaclass=_ClosedStringEnumMeta):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class WorkspaceRole(str, _Enum, metaclass=_ClosedStringEnumMeta):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class WorkspaceMembershipStatus(str, _Enum, metaclass=_ClosedStringEnumMeta):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REMOVED = "removed"


class SessionStatus(str, _Enum, metaclass=_ClosedStringEnumMeta):
    ACTIVE = "active"
    REVOKED = "revoked"


class SessionRevocationReason(str, _Enum, metaclass=_ClosedStringEnumMeta):
    LOGOUT = "logout"
    ROTATED = "rotated"
    SECURITY_CHANGE = "security_change"
    ACCOUNT_DISABLED = "account_disabled"
    RECOVERY = "recovery"
    ADMINISTRATIVE = "administrative"


_RECORD_CLASS_DEFINITION_OPEN = True


class _RecordMeta(type):
    """Normalize generated-constructor failures without retaining input."""

    def __call__(cls, *arguments: object, **keywords: object) -> object:
        if not any(cls is record_type for record_type in _EXACT_RECORD_TYPES):
            _raise_validation_error()
        try:
            return super().__call__(*arguments, **keywords)
        except Exception:
            _raise_validation_error()


class _ImmutableRecord(metaclass=_RecordMeta):
    __slots__ = ()

    def __init_subclass__(cls, **keywords: object) -> None:
        if not _RECORD_CLASS_DEFINITION_OPEN:
            _raise_validation_error()
        super().__init_subclass__(**keywords)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(...)"


@_dataclass(frozen=True, slots=True, repr=False)
class CuevionUser(_ImmutableRecord):
    schema_version: int
    user_id: str
    status: UserStatus
    primary_verified_email_id: str | None
    display_name: str
    security_epoch: int
    created_at: int
    updated_at: int
    row_version: int

    def __post_init__(self) -> None:
        if _cuevion_user_values(self) is None:
            _raise_validation_error()


@_dataclass(frozen=True, slots=True, repr=False)
class VerifiedEmail(_ImmutableRecord):
    schema_version: int
    email_id: str
    user_id: str
    canonical_email: str
    status: VerifiedEmailStatus
    verification_source: str
    created_at: int
    verified_at: int | None
    retired_at: int | None
    row_version: int

    def __post_init__(self) -> None:
        if _verified_email_values(self) is None:
            _raise_validation_error()


@_dataclass(frozen=True, slots=True, repr=False)
class AuthenticationIdentity(_ImmutableRecord):
    schema_version: int
    identity_id: str
    user_id: str
    issuer: str
    subject: str
    method: AuthenticationMethod
    status: AuthenticationIdentityStatus
    verified_email_id: str | None
    created_at: int
    last_used_at: int | None
    row_version: int

    def __post_init__(self) -> None:
        if _authentication_identity_values(self) is None:
            _raise_validation_error()


@_dataclass(frozen=True, slots=True, repr=False)
class Workspace(_ImmutableRecord):
    schema_version: int
    workspace_id: str
    status: WorkspaceStatus
    created_by_user_id: str
    created_at: int
    updated_at: int
    row_version: int

    def __post_init__(self) -> None:
        if _workspace_values(self) is None:
            _raise_validation_error()


@_dataclass(frozen=True, slots=True, repr=False)
class WorkspaceMembership(_ImmutableRecord):
    schema_version: int
    workspace_id: str
    user_id: str
    role: WorkspaceRole
    status: WorkspaceMembershipStatus
    created_at: int
    updated_at: int
    row_version: int

    def __post_init__(self) -> None:
        if _workspace_membership_values(self) is None:
            _raise_validation_error()


@_dataclass(frozen=True, slots=True, repr=False)
class StoredSessionSnapshot(_ImmutableRecord):
    schema_version: int
    session_id: str
    user_id: str
    authentication_identity_id: str
    credential_lookup_digest: str = _field(repr=False)
    credential_binding_digest: str = _field(repr=False)
    credential_epoch: int
    security_epoch: int
    status: SessionStatus
    authenticated_at: int
    issued_at: int
    last_used_at: int
    idle_expires_at: int
    absolute_expires_at: int
    revoked_at: int | None
    revocation_reason: SessionRevocationReason | None
    row_version: int

    def __post_init__(self) -> None:
        if _stored_session_values(self) is None:
            _raise_validation_error()


_EXACT_RECORD_TYPES = (
    CuevionUser,
    VerifiedEmail,
    AuthenticationIdentity,
    Workspace,
    WorkspaceMembership,
    StoredSessionSnapshot,
)
_RECORD_CLASS_DEFINITION_OPEN = False


_BASE64URL_RE = _re.compile(r"[A-Za-z0-9_-]+")
_EMAIL_RE = _re.compile(
    r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)
_ASCII_SECURITY_IDENTIFIER_RE = _re.compile(r"[!-~]+")

_USER_ID_PREFIX = "usr_"
_VERIFIED_EMAIL_ID_PREFIX = "vem_"
_AUTHENTICATION_IDENTITY_ID_PREFIX = "aid_"
_WORKSPACE_ID_PREFIX = "wsp_"
_RECORD_ID_ENCODED_LENGTH = 22
_SESSION_DIGEST_ENCODED_LENGTH = 43
_DISPLAY_NAME_MAX_UTF8_BYTES = 256
_EMAIL_MAX_CHARACTERS = 320
_EMAIL_LOCAL_MAX_CHARACTERS = 64
_EMAIL_DOMAIN_MAX_CHARACTERS = 253
_VERIFICATION_SOURCE_MAX_CHARACTERS = 128
_ISSUER_MAX_CHARACTERS = 512
_SUBJECT_MAX_CHARACTERS = 512

_CUEVION_USER_FIELDS = (
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
_VERIFIED_EMAIL_FIELDS = (
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
_AUTHENTICATION_IDENTITY_FIELDS = (
    "schema_version",
    "identity_id",
    "user_id",
    "issuer",
    "subject",
    "method",
    "status",
    "verified_email_id",
    "created_at",
    "last_used_at",
    "row_version",
)
_WORKSPACE_FIELDS = (
    "schema_version",
    "workspace_id",
    "status",
    "created_by_user_id",
    "created_at",
    "updated_at",
    "row_version",
)
_WORKSPACE_MEMBERSHIP_FIELDS = (
    "schema_version",
    "workspace_id",
    "user_id",
    "role",
    "status",
    "created_at",
    "updated_at",
    "row_version",
)
_STORED_SESSION_FIELDS = (
    "schema_version",
    "session_id",
    "user_id",
    "authentication_identity_id",
    "credential_lookup_digest",
    "credential_binding_digest",
    "credential_epoch",
    "security_epoch",
    "status",
    "authenticated_at",
    "issued_at",
    "last_used_at",
    "idle_expires_at",
    "absolute_expires_at",
    "revoked_at",
    "revocation_reason",
    "row_version",
)


def _read_exact_slots(
    value: object, expected_type: type[object], field_names: tuple[str, ...]
) -> tuple[object, ...] | None:
    if type(value) is not expected_type:
        return None
    try:
        return tuple(object.__getattribute__(value, name) for name in field_names)
    except Exception:
        return None


def _encode_base64url(value: bytes) -> str:
    return _base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_canonical_base64url(value: object) -> bytes | None:
    if (
        type(value) is not str
        or not value
        or not value.isascii()
        or _BASE64URL_RE.fullmatch(value) is None
        or len(value) % 4 == 1
    ):
        return None
    decoded: bytes | None = None
    try:
        decoded = _base64.b64decode(
            value.encode("ascii") + (b"=" * ((-len(value)) % 4)),
            altchars=b"-_",
            validate=True,
        )
    except Exception:
        pass
    if decoded is None or _encode_base64url(decoded) != value:
        return None
    return decoded


def _valid_prefixed_identifier(
    value: object, prefix: str, decoded_length: int
) -> bool:
    if (
        type(value) is not str
        or len(value) != len(prefix) + _RECORD_ID_ENCODED_LENGTH
        or value[: len(prefix)] != prefix
    ):
        return False
    decoded = _decode_canonical_base64url(value[len(prefix) :])
    return decoded is not None and len(decoded) == decoded_length


def _valid_user_id(value: object) -> bool:
    return _valid_prefixed_identifier(value, _USER_ID_PREFIX, 16)


def _valid_verified_email_id(value: object) -> bool:
    return _valid_prefixed_identifier(value, _VERIFIED_EMAIL_ID_PREFIX, 16)


def _valid_authentication_identity_id(value: object) -> bool:
    return _valid_prefixed_identifier(
        value, _AUTHENTICATION_IDENTITY_ID_PREFIX, 16
    )


def _valid_workspace_id(value: object) -> bool:
    return _valid_prefixed_identifier(value, _WORKSPACE_ID_PREFIX, 16)


def _valid_session_or_digest(value: object) -> bool:
    if type(value) is not str or len(value) != _SESSION_DIGEST_ENCODED_LENGTH:
        return False
    decoded = _decode_canonical_base64url(value)
    return decoded is not None and len(decoded) == 32


def _valid_canonical_email(value: object) -> bool:
    if (
        type(value) is not str
        or not value.isascii()
        or not value
        or len(value) > _EMAIL_MAX_CHARACTERS
        or _EMAIL_RE.fullmatch(value) is None
    ):
        return False
    local_part, domain = value.split("@")
    return (
        len(local_part) <= _EMAIL_LOCAL_MAX_CHARACTERS
        and len(domain) <= _EMAIL_DOMAIN_MAX_CHARACTERS
    )


def _valid_display_name(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    if any(
        _unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in value
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= _DISPLAY_NAME_MAX_UTF8_BYTES
    except UnicodeEncodeError:
        return False


def _valid_ascii_security_identifier(value: object, maximum: int) -> bool:
    return (
        type(value) is str
        and value.isascii()
        and len(value) <= maximum
        and _ASCII_SECURITY_IDENTIFIER_RE.fullmatch(value) is not None
    )


def _is_schema_one(value: object) -> bool:
    return type(value) is int and value == 1


def _is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_optional_nonnegative_int(value: object) -> bool:
    return value is None or _is_nonnegative_int(value)


def _cuevion_user_values(value: object) -> tuple[object, ...] | None:
    fields = _read_exact_slots(value, CuevionUser, _CUEVION_USER_FIELDS)
    if fields is None:
        return None
    (
        schema_version,
        user_id,
        status,
        primary_verified_email_id,
        display_name,
        security_epoch,
        created_at,
        updated_at,
        row_version,
    ) = fields
    if (
        not _is_schema_one(schema_version)
        or not _valid_user_id(user_id)
        or type(status) is not UserStatus
        or (
            primary_verified_email_id is not None
            and not _valid_verified_email_id(primary_verified_email_id)
        )
        or (status is UserStatus.ACTIVE and primary_verified_email_id is None)
        or not _valid_display_name(display_name)
        or not _is_positive_int(security_epoch)
        or not _is_nonnegative_int(created_at)
        or not _is_nonnegative_int(updated_at)
        or created_at > updated_at
        or not _is_positive_int(row_version)
    ):
        return None
    return fields


def _verified_email_values(value: object) -> tuple[object, ...] | None:
    fields = _read_exact_slots(value, VerifiedEmail, _VERIFIED_EMAIL_FIELDS)
    if fields is None:
        return None
    (
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
    ) = fields
    if (
        not _is_schema_one(schema_version)
        or not _valid_verified_email_id(email_id)
        or not _valid_user_id(user_id)
        or not _valid_canonical_email(canonical_email)
        or type(status) is not VerifiedEmailStatus
        or not _valid_ascii_security_identifier(
            verification_source, _VERIFICATION_SOURCE_MAX_CHARACTERS
        )
        or not _is_nonnegative_int(created_at)
        or not _is_optional_nonnegative_int(verified_at)
        or not _is_optional_nonnegative_int(retired_at)
        or not _is_positive_int(row_version)
    ):
        return None
    if status is VerifiedEmailStatus.PENDING:
        if verified_at is not None or retired_at is not None:
            return None
    elif status is VerifiedEmailStatus.VERIFIED:
        if verified_at is None or retired_at is not None:
            return None
    elif status is VerifiedEmailStatus.RETIRED:
        if verified_at is None or retired_at is None:
            return None
    else:
        return None
    if verified_at is not None and created_at > verified_at:
        return None
    if retired_at is not None and (
        verified_at is None or verified_at > retired_at
    ):
        return None
    return fields


def _authentication_identity_values(value: object) -> tuple[object, ...] | None:
    fields = _read_exact_slots(
        value, AuthenticationIdentity, _AUTHENTICATION_IDENTITY_FIELDS
    )
    if fields is None:
        return None
    (
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
    ) = fields
    if (
        not _is_schema_one(schema_version)
        or not _valid_authentication_identity_id(identity_id)
        or not _valid_user_id(user_id)
        or not _valid_ascii_security_identifier(issuer, _ISSUER_MAX_CHARACTERS)
        or not _valid_ascii_security_identifier(subject, _SUBJECT_MAX_CHARACTERS)
        or type(method) is not AuthenticationMethod
        or type(status) is not AuthenticationIdentityStatus
        or (
            verified_email_id is not None
            and not _valid_verified_email_id(verified_email_id)
        )
        or not _is_nonnegative_int(created_at)
        or not _is_optional_nonnegative_int(last_used_at)
        or (last_used_at is not None and created_at > last_used_at)
        or not _is_positive_int(row_version)
    ):
        return None
    return fields


def _workspace_values(value: object) -> tuple[object, ...] | None:
    fields = _read_exact_slots(value, Workspace, _WORKSPACE_FIELDS)
    if fields is None:
        return None
    (
        schema_version,
        workspace_id,
        status,
        created_by_user_id,
        created_at,
        updated_at,
        row_version,
    ) = fields
    if (
        not _is_schema_one(schema_version)
        or not _valid_workspace_id(workspace_id)
        or type(status) is not WorkspaceStatus
        or not _valid_user_id(created_by_user_id)
        or not _is_nonnegative_int(created_at)
        or not _is_nonnegative_int(updated_at)
        or created_at > updated_at
        or not _is_positive_int(row_version)
    ):
        return None
    return fields


def _workspace_membership_values(value: object) -> tuple[object, ...] | None:
    fields = _read_exact_slots(
        value, WorkspaceMembership, _WORKSPACE_MEMBERSHIP_FIELDS
    )
    if fields is None:
        return None
    (
        schema_version,
        workspace_id,
        user_id,
        role,
        status,
        created_at,
        updated_at,
        row_version,
    ) = fields
    if (
        not _is_schema_one(schema_version)
        or not _valid_workspace_id(workspace_id)
        or not _valid_user_id(user_id)
        or type(role) is not WorkspaceRole
        or type(status) is not WorkspaceMembershipStatus
        or not _is_nonnegative_int(created_at)
        or not _is_nonnegative_int(updated_at)
        or created_at > updated_at
        or not _is_positive_int(row_version)
    ):
        return None
    return fields


def _stored_session_values(value: object) -> tuple[object, ...] | None:
    fields = _read_exact_slots(
        value, StoredSessionSnapshot, _STORED_SESSION_FIELDS
    )
    if fields is None:
        return None
    (
        schema_version,
        session_id,
        user_id,
        authentication_identity_id,
        credential_lookup_digest,
        credential_binding_digest,
        credential_epoch,
        security_epoch,
        status,
        authenticated_at,
        issued_at,
        last_used_at,
        idle_expires_at,
        absolute_expires_at,
        revoked_at,
        revocation_reason,
        row_version,
    ) = fields
    if (
        not _is_schema_one(schema_version)
        or not _valid_session_or_digest(session_id)
        or not _valid_user_id(user_id)
        or not _valid_authentication_identity_id(authentication_identity_id)
        or not _valid_session_or_digest(credential_lookup_digest)
        or not _valid_session_or_digest(credential_binding_digest)
        or not _is_positive_int(credential_epoch)
        or not _is_positive_int(security_epoch)
        or type(status) is not SessionStatus
        or not _is_nonnegative_int(authenticated_at)
        or not _is_nonnegative_int(issued_at)
        or not _is_nonnegative_int(last_used_at)
        or not _is_nonnegative_int(idle_expires_at)
        or not _is_nonnegative_int(absolute_expires_at)
        or not _is_optional_nonnegative_int(revoked_at)
        or (
            revocation_reason is not None
            and type(revocation_reason) is not SessionRevocationReason
        )
        or not _is_positive_int(row_version)
        or authenticated_at > issued_at
        or issued_at > last_used_at
        or last_used_at >= idle_expires_at
        or idle_expires_at > absolute_expires_at
    ):
        return None
    if status is SessionStatus.ACTIVE:
        if revoked_at is not None or revocation_reason is not None:
            return None
    elif status is SessionStatus.REVOKED:
        if revoked_at is None or revocation_reason is None:
            return None
        if last_used_at > revoked_at or revoked_at > absolute_expires_at:
            return None
    else:
        return None
    return fields


def validate_user_primary_email(
    user: CuevionUser, verified_email: VerifiedEmail
) -> None:
    """Validate one active user's immutable verified-primary-email link."""

    user_values = _cuevion_user_values(user)
    email_values = _verified_email_values(verified_email)
    if user_values is None or email_values is None:
        _raise_validation_error()
    user_id = user_values[1]
    user_status = user_values[2]
    primary_email_id = user_values[3]
    email_id = email_values[1]
    email_user_id = email_values[2]
    email_status = email_values[4]
    if (
        user_status is not UserStatus.ACTIVE
        or email_status is not VerifiedEmailStatus.VERIFIED
        or user_id != email_user_id
        or primary_email_id != email_id
    ):
        _raise_validation_error()
    return None


def validate_identity_for_user(
    identity: AuthenticationIdentity,
    user: CuevionUser,
    verified_email_or_none: VerifiedEmail | None,
) -> None:
    """Validate identity ownership solely through immutable record IDs."""

    identity_values = _authentication_identity_values(identity)
    user_values = _cuevion_user_values(user)
    email_values: tuple[object, ...] | None = None
    if verified_email_or_none is not None:
        email_values = _verified_email_values(verified_email_or_none)
        if email_values is None:
            _raise_validation_error()
    if identity_values is None or user_values is None:
        _raise_validation_error()
    identity_user_id = identity_values[2]
    identity_status = identity_values[6]
    linked_email_id = identity_values[7]
    user_id = user_values[1]
    if (
        identity_status is not AuthenticationIdentityStatus.ACTIVE
        or identity_user_id != user_id
    ):
        _raise_validation_error()
    if linked_email_id is not None:
        if email_values is None:
            _raise_validation_error()
        email_id = email_values[1]
        email_user_id = email_values[2]
        email_status = email_values[4]
        if (
            email_user_id != user_id
            or email_status is not VerifiedEmailStatus.VERIFIED
            or email_id != linked_email_id
        ):
            _raise_validation_error()
    return None


def validate_membership_for_user(
    membership: WorkspaceMembership, workspace: Workspace, user: CuevionUser
) -> None:
    """Validate active membership through immutable workspace and user IDs."""

    membership_values = _workspace_membership_values(membership)
    workspace_values = _workspace_values(workspace)
    user_values = _cuevion_user_values(user)
    if (
        membership_values is None
        or workspace_values is None
        or user_values is None
    ):
        _raise_validation_error()
    if (
        membership_values[1] != workspace_values[1]
        or membership_values[2] != user_values[1]
        or membership_values[4] is not WorkspaceMembershipStatus.ACTIVE
        or workspace_values[2] is not WorkspaceStatus.ACTIVE
        or user_values[2] is not UserStatus.ACTIVE
    ):
        _raise_validation_error()
    return None


def validate_session_snapshot(
    session: StoredSessionSnapshot,
    user: CuevionUser,
    identity: AuthenticationIdentity,
    now: int,
) -> None:
    """Validate one currently authenticated, unexpired session snapshot."""

    session_values = _stored_session_values(session)
    user_values = _cuevion_user_values(user)
    identity_values = _authentication_identity_values(identity)
    if (
        session_values is None
        or user_values is None
        or identity_values is None
        or not _is_nonnegative_int(now)
    ):
        _raise_validation_error()
    session_user_id = session_values[2]
    session_identity_id = session_values[3]
    session_security_epoch = session_values[7]
    session_status = session_values[8]
    issued_at = session_values[10]
    last_used_at = session_values[11]
    idle_expires_at = session_values[12]
    absolute_expires_at = session_values[13]
    user_id = user_values[1]
    user_status = user_values[2]
    user_security_epoch = user_values[5]
    identity_id = identity_values[1]
    identity_user_id = identity_values[2]
    identity_status = identity_values[6]
    if (
        session_user_id != user_id
        or identity_user_id != user_id
        or session_identity_id != identity_id
        or session_security_epoch != user_security_epoch
        or user_status is not UserStatus.ACTIVE
        or identity_status is not AuthenticationIdentityStatus.ACTIVE
        or session_status is not SessionStatus.ACTIVE
        or issued_at > now
        or last_used_at > now
        or now >= idle_expires_at
        or now >= absolute_expires_at
    ):
        _raise_validation_error()
    return None


__all__ = (
    "ModelValidationError",
    "UserStatus",
    "VerifiedEmailStatus",
    "AuthenticationMethod",
    "AuthenticationIdentityStatus",
    "WorkspaceStatus",
    "WorkspaceRole",
    "WorkspaceMembershipStatus",
    "SessionStatus",
    "SessionRevocationReason",
    "CuevionUser",
    "VerifiedEmail",
    "AuthenticationIdentity",
    "Workspace",
    "WorkspaceMembership",
    "StoredSessionSnapshot",
    "validate_user_primary_email",
    "validate_identity_for_user",
    "validate_membership_for_user",
    "validate_session_snapshot",
)
