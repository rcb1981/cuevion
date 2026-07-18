"""Inactive, storage-independent current-account authority contract.

This module defines immutable values and one repository Protocol only.  It
performs no storage, provider, environment, clock, logging, network,
filesystem, HTTP, session, or product-authorization work.  Identity issuers
are accepted only as exact security identifiers; callers remain responsible
for supplying an already-reviewed canonical issuer.
"""

import sys as _sys


if (
    __name__ != "cuevion_auth.current_account_repository_contract"
    or __package__ != "cuevion_auth"
):
    raise ImportError(
        "current-account repository contract requires its canonical import identity"
    )
if (
    getattr(
        _sys.modules.get(
            "cuevion_auth.current_account_repository_contract"
        ),
        "__dict__",
        None,
    )
    is not globals()
):
    raise ImportError(
        "current-account repository contract requires its canonical module object"
    )
if "_CURRENT_ACCOUNT_REPOSITORY_CONTRACT_INITIALIZED" in globals():
    raise ImportError(
        "current-account repository contract cannot initialize twice"
    )
_CURRENT_ACCOUNT_REPOSITORY_CONTRACT_INITIALIZED = True

import base64 as _base64
from enum import Enum as _Enum
from enum import EnumMeta as _EnumMeta
from typing import Protocol as _Protocol

from api.auth import models as _models


if _models is not _sys.modules.get("api.auth.models"):
    raise ImportError("account models require their canonical import identity")


__all__ = (
    "CurrentAccountRepositoryContractValidationError",
    "CurrentAccountReadOutcome",
    "AuthenticationIdentityLookupKey",
    "CurrentAccountAuthority",
    "CurrentAccountByUserAuthority",
    "CurrentAccountAuthorityResult",
    "CurrentAccountByUserAuthorityResult",
    "validate_authentication_identity_lookup_key",
    "validate_current_account_user_id",
    "validate_current_account_workspace_id",
    "CurrentAccountAuthorityRepository",
)


_ERROR_CONSTRUCTION_FAILURE = (
    "current-account repository contract validation errors accept no arguments"
)


class CurrentAccountRepositoryContractValidationError(ValueError):
    """One fixed, value-free current-account contract validation failure."""

    __slots__ = ()

    def __new__(
        cls, *arguments: object, **keywords: object
    ) -> "CurrentAccountRepositoryContractValidationError":
        valid = (
            cls is CurrentAccountRepositoryContractValidationError
            and not arguments
            and not keywords
        )
        if not valid:
            del arguments, keywords
            raise TypeError(_ERROR_CONSTRUCTION_FAILURE)
        return ValueError.__new__(cls)

    def __init__(self) -> None:
        ValueError.__init__(self)

    def __init_subclass__(cls, **_keywords: object) -> None:
        raise TypeError(_ERROR_CONSTRUCTION_FAILURE)

    @property
    def args(self) -> tuple[object, ...]:
        return ()

    @args.setter
    def args(self, _value: object) -> None:
        return None

    def __str__(self) -> str:
        return "invalid current account repository contract value"

    def __repr__(self) -> str:
        return "CurrentAccountRepositoryContractValidationError()"


def _raise_validation_error() -> None:
    """Raise one fresh fixed error without retaining rejected input."""

    error = CurrentAccountRepositoryContractValidationError()
    try:
        raise error
    finally:
        object.__setattr__(error, "__context__", None)
        object.__setattr__(error, "__cause__", None)


_ENUM_VALUE_MISSING = object()


class _ClosedStringEnumMeta(_EnumMeta):
    """Resolve only exact strings or an already exact enum member."""

    def __call__(
        cls,
        value: object = _ENUM_VALUE_MISSING,
        *arguments: object,
        **keywords: object,
    ) -> object:
        candidate = _ENUM_VALUE_MISSING
        try:
            if not arguments and not keywords:
                if type(value) is cls:
                    return value
                if type(value) is str:
                    candidate = cls._value2member_map_.get(
                        value, _ENUM_VALUE_MISSING
                    )
        except Exception:
            candidate = _ENUM_VALUE_MISSING
        if type(candidate) is cls:
            return candidate
        del value, arguments, keywords, candidate
        _raise_validation_error()


class CurrentAccountReadOutcome(
    str, _Enum, metaclass=_ClosedStringEnumMeta
):
    """Closed public outcomes for both current-authority read boundaries."""

    FOUND = "found"
    NOT_AUTHORIZED = "not_authorized"
    UNAVAILABLE = "unavailable"
    INTERNAL_ERROR = "internal_error"


_RECORD_CLASS_DEFINITION_OPEN = True
_RECORD_METACLASS_DEFINITION_OPEN = True
_RECORD_VALID = object()
_RECORD_INVALID = object()


class _RecordMetaBoundary(type):
    """Reject derived record metaclasses before they bypass the gate."""

    def __new__(
        metaclass: type,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, object],
        **keywords: object,
    ) -> type:
        if not _RECORD_METACLASS_DEFINITION_OPEN:
            del metaclass, name, bases, namespace, keywords
            _raise_validation_error()
        return type.__new__(metaclass, name, bases, namespace, **keywords)


class _RecordMeta(type, metaclass=_RecordMetaBoundary):
    """Construct only the exact closed contract record types."""

    def __new__(
        metaclass: type,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, object],
        **keywords: object,
    ) -> type:
        if not _RECORD_CLASS_DEFINITION_OPEN:
            del metaclass, name, bases, namespace, keywords
            _raise_validation_error()
        return type.__new__(metaclass, name, bases, namespace, **keywords)

    def __call__(cls, *arguments: object, **keywords: object) -> object:
        candidate: object = _RECORD_INVALID
        initialization: object = _RECORD_INVALID
        validation: object = _RECORD_INVALID
        initializer: object = _RECORD_INVALID
        try:
            if any(cls is record_type for record_type in _EXACT_RECORD_TYPES):
                candidate = object.__new__(cls)
                initializer = type.__getattribute__(cls, "__init__")
                initialization = initializer(
                    candidate, *arguments, **keywords
                )
                if initialization is None:
                    validation = _validate_contract_record_worker(candidate)
        except Exception:
            validation = _RECORD_INVALID
        except BaseException:
            del cls, candidate, initialization, validation, initializer
            del arguments, keywords
            raise
        if validation is _RECORD_VALID:
            return candidate
        del cls, candidate, initialization, validation, initializer
        del arguments, keywords
        _raise_validation_error()


_RECORD_METACLASS_DEFINITION_OPEN = False


class _ImmutableContractRecord(metaclass=_RecordMeta):
    __slots__ = ()

    def __new__(
        cls, *arguments: object, **keywords: object
    ) -> "_ImmutableContractRecord":
        del cls, arguments, keywords
        _raise_validation_error()

    def __setattr__(self, name: str, value: object) -> None:
        del self, name, value
        _raise_validation_error()

    def __delattr__(self, name: str) -> None:
        del self, name
        _raise_validation_error()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(...)"

    def __str__(self) -> str:
        return f"{type(self).__name__}(...)"

    def __reduce__(self) -> object:
        del self
        _raise_validation_error()

    def __reduce_ex__(self, protocol: object) -> object:
        del self, protocol
        _raise_validation_error()

    def __getstate__(self) -> object:
        del self
        _raise_validation_error()

    def __setstate__(self, state: object) -> None:
        del self, state
        _raise_validation_error()


class AuthenticationIdentityLookupKey(_ImmutableContractRecord):
    """Exact identity key; ``issuer`` must already be canonicalized upstream."""

    __signature__ = "(issuer, subject)"
    __slots__ = ("issuer", "subject")

    issuer: str
    subject: str

    def __init__(self, issuer: str, subject: str) -> None:
        validation = _initialize_contract_record_worker(
            self,
            AuthenticationIdentityLookupKey,
            _IDENTITY_LOOKUP_KEY_FIELDS,
            (issuer, subject),
        )
        if validation is _RECORD_VALID:
            return None
        del self, issuer, subject, validation
        _raise_validation_error()


class CurrentAccountAuthority(_ImmutableContractRecord):
    """One complete active identity, account, and workspace authority graph."""

    __signature__ = (
        "(user, primary_verified_email, authentication_identity, workspace, "
        "workspace_membership)"
    )
    __slots__ = (
        "user",
        "primary_verified_email",
        "authentication_identity",
        "workspace",
        "workspace_membership",
    )

    user: _models.CuevionUser
    primary_verified_email: _models.VerifiedEmail
    authentication_identity: _models.AuthenticationIdentity
    workspace: _models.Workspace
    workspace_membership: _models.WorkspaceMembership

    def __init__(
        self,
        user: _models.CuevionUser,
        primary_verified_email: _models.VerifiedEmail,
        authentication_identity: _models.AuthenticationIdentity,
        workspace: _models.Workspace,
        workspace_membership: _models.WorkspaceMembership,
    ) -> None:
        validation = _initialize_contract_record_worker(
            self,
            CurrentAccountAuthority,
            _CURRENT_ACCOUNT_AUTHORITY_FIELDS,
            (
                user,
                primary_verified_email,
                authentication_identity,
                workspace,
                workspace_membership,
            ),
        )
        if validation is _RECORD_VALID:
            return None
        del self, user, primary_verified_email, authentication_identity
        del workspace, workspace_membership, validation
        _raise_validation_error()


class CurrentAccountByUserAuthority(_ImmutableContractRecord):
    """One complete active account and workspace authority graph by user ID."""

    __signature__ = (
        "(user, primary_verified_email, workspace, workspace_membership)"
    )
    __slots__ = (
        "user",
        "primary_verified_email",
        "workspace",
        "workspace_membership",
    )

    user: _models.CuevionUser
    primary_verified_email: _models.VerifiedEmail
    workspace: _models.Workspace
    workspace_membership: _models.WorkspaceMembership

    def __init__(
        self,
        user: _models.CuevionUser,
        primary_verified_email: _models.VerifiedEmail,
        workspace: _models.Workspace,
        workspace_membership: _models.WorkspaceMembership,
    ) -> None:
        validation = _initialize_contract_record_worker(
            self,
            CurrentAccountByUserAuthority,
            _CURRENT_ACCOUNT_BY_USER_AUTHORITY_FIELDS,
            (
                user,
                primary_verified_email,
                workspace,
                workspace_membership,
            ),
        )
        if validation is _RECORD_VALID:
            return None
        del self, user, primary_verified_email, workspace
        del workspace_membership, validation
        _raise_validation_error()


class CurrentAccountAuthorityResult(_ImmutableContractRecord):
    """Value-free outcome envelope for identity-based authority reads."""

    __signature__ = "(outcome, authority)"
    __slots__ = ("outcome", "authority")

    outcome: CurrentAccountReadOutcome
    authority: CurrentAccountAuthority | None

    def __init__(
        self,
        outcome: CurrentAccountReadOutcome,
        authority: CurrentAccountAuthority | None,
    ) -> None:
        validation = _initialize_contract_record_worker(
            self,
            CurrentAccountAuthorityResult,
            _CURRENT_ACCOUNT_AUTHORITY_RESULT_FIELDS,
            (outcome, authority),
        )
        if validation is _RECORD_VALID:
            return None
        del self, outcome, authority, validation
        _raise_validation_error()


class CurrentAccountByUserAuthorityResult(_ImmutableContractRecord):
    """Value-free outcome envelope for user-ID authority reads."""

    __signature__ = "(outcome, authority)"
    __slots__ = ("outcome", "authority")

    outcome: CurrentAccountReadOutcome
    authority: CurrentAccountByUserAuthority | None

    def __init__(
        self,
        outcome: CurrentAccountReadOutcome,
        authority: CurrentAccountByUserAuthority | None,
    ) -> None:
        validation = _initialize_contract_record_worker(
            self,
            CurrentAccountByUserAuthorityResult,
            _CURRENT_ACCOUNT_BY_USER_AUTHORITY_RESULT_FIELDS,
            (outcome, authority),
        )
        if validation is _RECORD_VALID:
            return None
        del self, outcome, authority, validation
        _raise_validation_error()


_EXACT_RECORD_TYPES = (
    AuthenticationIdentityLookupKey,
    CurrentAccountAuthority,
    CurrentAccountByUserAuthority,
    CurrentAccountAuthorityResult,
    CurrentAccountByUserAuthorityResult,
)
_RECORD_CLASS_DEFINITION_OPEN = False


_IDENTITY_LOOKUP_KEY_FIELDS = ("issuer", "subject")
_CURRENT_ACCOUNT_AUTHORITY_FIELDS = (
    "user",
    "primary_verified_email",
    "authentication_identity",
    "workspace",
    "workspace_membership",
)
_CURRENT_ACCOUNT_BY_USER_AUTHORITY_FIELDS = (
    "user",
    "primary_verified_email",
    "workspace",
    "workspace_membership",
)
_CURRENT_ACCOUNT_AUTHORITY_RESULT_FIELDS = ("outcome", "authority")
_CURRENT_ACCOUNT_BY_USER_AUTHORITY_RESULT_FIELDS = ("outcome", "authority")

_BASE64URL_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789-_"
)
_RECORD_ID_ENCODED_LENGTH = 22
_RECORD_ID_BYTE_LENGTH = 16
_USER_ID_PREFIX = "usr_"
_WORKSPACE_ID_PREFIX = "wsp_"
_ISSUER_MAX_CHARACTERS = 512
_SUBJECT_MAX_CHARACTERS = 512


def _read_exact_fields(
    value: object,
    expected_type: type[object],
    field_names: tuple[str, ...],
) -> tuple[object, ...] | None:
    if type(value) is not expected_type:
        return None
    try:
        return tuple(
            object.__getattribute__(value, name) for name in field_names
        )
    except Exception:
        return None


def _encode_base64url(value: bytes) -> str:
    return _base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_canonical_base64url(value: object) -> bytes | None:
    if (
        type(value) is not str
        or not value
        or not value.isascii()
        or any(character not in _BASE64URL_CHARACTERS for character in value)
        or len(value) % 4 == 1
    ):
        return None
    decoded: object = None
    try:
        decoded = _base64.b64decode(
            value.encode("ascii") + (b"=" * ((-len(value)) % 4)),
            altchars=b"-_",
            validate=True,
        )
        if type(decoded) is not bytes or _encode_base64url(decoded) != value:
            return None
    except Exception:
        return None
    return decoded


def _valid_prefixed_identifier(value: object, prefix: str) -> bool:
    if (
        type(value) is not str
        or len(value) != len(prefix) + _RECORD_ID_ENCODED_LENGTH
        or value[: len(prefix)] != prefix
    ):
        return False
    decoded = _decode_canonical_base64url(value[len(prefix) :])
    return type(decoded) is bytes and len(decoded) == _RECORD_ID_BYTE_LENGTH


def _valid_security_identifier(value: object, maximum: int) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value.isascii()
        and len(value) <= maximum
        and all("!" <= character <= "~" for character in value)
    )


def _identity_lookup_key_fields_are_valid(
    fields: tuple[object, ...],
) -> bool:
    issuer, subject = fields
    return _valid_security_identifier(
        issuer, _ISSUER_MAX_CHARACTERS
    ) and _valid_security_identifier(subject, _SUBJECT_MAX_CHARACTERS)


def _identity_lookup_key_values(
    value: object,
) -> tuple[object, ...] | None:
    fields = _read_exact_fields(
        value, AuthenticationIdentityLookupKey, _IDENTITY_LOOKUP_KEY_FIELDS
    )
    return (
        fields
        if fields is not None
        and _identity_lookup_key_fields_are_valid(fields)
        else None
    )


def _current_account_authority_fields_are_valid(
    fields: tuple[object, ...],
) -> bool:
    (
        user,
        primary_verified_email,
        authentication_identity,
        workspace,
        workspace_membership,
    ) = fields
    if (
        type(user) is not _models.CuevionUser
        or type(primary_verified_email) is not _models.VerifiedEmail
        or type(authentication_identity)
        is not _models.AuthenticationIdentity
        or type(workspace) is not _models.Workspace
        or type(workspace_membership) is not _models.WorkspaceMembership
    ):
        return False
    try:
        if (
            _models.validate_user_primary_email(
                user, primary_verified_email
            )
            is not None
            or _models.validate_identity_for_user(
                authentication_identity, user, primary_verified_email
            )
            is not None
            or _models.validate_membership_for_user(
                workspace_membership, workspace, user
            )
            is not None
        ):
            return False
    except Exception:
        return False
    return True


def _current_account_authority_values(
    value: object,
) -> tuple[object, ...] | None:
    fields = _read_exact_fields(
        value, CurrentAccountAuthority, _CURRENT_ACCOUNT_AUTHORITY_FIELDS
    )
    return (
        fields
        if fields is not None
        and _current_account_authority_fields_are_valid(fields)
        else None
    )


def _current_account_by_user_authority_fields_are_valid(
    fields: tuple[object, ...],
) -> bool:
    user, primary_verified_email, workspace, workspace_membership = fields
    if (
        type(user) is not _models.CuevionUser
        or type(primary_verified_email) is not _models.VerifiedEmail
        or type(workspace) is not _models.Workspace
        or type(workspace_membership) is not _models.WorkspaceMembership
    ):
        return False
    try:
        if (
            _models.validate_user_primary_email(
                user, primary_verified_email
            )
            is not None
            or _models.validate_membership_for_user(
                workspace_membership, workspace, user
            )
            is not None
        ):
            return False
    except Exception:
        return False
    return True


def _current_account_by_user_authority_values(
    value: object,
) -> tuple[object, ...] | None:
    fields = _read_exact_fields(
        value,
        CurrentAccountByUserAuthority,
        _CURRENT_ACCOUNT_BY_USER_AUTHORITY_FIELDS,
    )
    return (
        fields
        if fields is not None
        and _current_account_by_user_authority_fields_are_valid(fields)
        else None
    )


def _current_account_authority_result_fields_are_valid(
    fields: tuple[object, ...],
) -> bool:
    outcome, authority = fields
    if type(outcome) is not CurrentAccountReadOutcome:
        return False
    if outcome is CurrentAccountReadOutcome.FOUND:
        return _current_account_authority_values(authority) is not None
    if outcome in (
        CurrentAccountReadOutcome.NOT_AUTHORIZED,
        CurrentAccountReadOutcome.UNAVAILABLE,
        CurrentAccountReadOutcome.INTERNAL_ERROR,
    ):
        return authority is None
    return False


def _current_account_authority_result_values(
    value: object,
) -> tuple[object, ...] | None:
    fields = _read_exact_fields(
        value,
        CurrentAccountAuthorityResult,
        _CURRENT_ACCOUNT_AUTHORITY_RESULT_FIELDS,
    )
    return (
        fields
        if fields is not None
        and _current_account_authority_result_fields_are_valid(fields)
        else None
    )


def _current_account_by_user_authority_result_fields_are_valid(
    fields: tuple[object, ...],
) -> bool:
    outcome, authority = fields
    if type(outcome) is not CurrentAccountReadOutcome:
        return False
    if outcome is CurrentAccountReadOutcome.FOUND:
        return _current_account_by_user_authority_values(authority) is not None
    if outcome in (
        CurrentAccountReadOutcome.NOT_AUTHORIZED,
        CurrentAccountReadOutcome.UNAVAILABLE,
        CurrentAccountReadOutcome.INTERNAL_ERROR,
    ):
        return authority is None
    return False


def _current_account_by_user_authority_result_values(
    value: object,
) -> tuple[object, ...] | None:
    fields = _read_exact_fields(
        value,
        CurrentAccountByUserAuthorityResult,
        _CURRENT_ACCOUNT_BY_USER_AUTHORITY_RESULT_FIELDS,
    )
    return (
        fields
        if fields is not None
        and _current_account_by_user_authority_result_fields_are_valid(fields)
        else None
    )


def _contract_record_fields_are_valid(
    record_type: type[object], fields: tuple[object, ...]
) -> bool:
    if record_type is AuthenticationIdentityLookupKey:
        return _identity_lookup_key_fields_are_valid(fields)
    if record_type is CurrentAccountAuthority:
        return _current_account_authority_fields_are_valid(fields)
    if record_type is CurrentAccountByUserAuthority:
        return _current_account_by_user_authority_fields_are_valid(fields)
    if record_type is CurrentAccountAuthorityResult:
        return _current_account_authority_result_fields_are_valid(fields)
    if record_type is CurrentAccountByUserAuthorityResult:
        return _current_account_by_user_authority_result_fields_are_valid(
            fields
        )
    return False


def _initialize_contract_record_worker(
    record: object,
    record_type: type[object],
    field_names: tuple[str, ...],
    fields: tuple[object, ...],
) -> object:
    """Validate first, then initialize exact slots, returning one sentinel."""

    try:
        if (
            type(record) is not record_type
            or type(field_names) is not tuple
            or type(fields) is not tuple
            or len(field_names) != len(fields)
        ):
            return _RECORD_INVALID
        for field_name in field_names:
            try:
                object.__getattribute__(record, field_name)
            except AttributeError:
                continue
            return _RECORD_INVALID
        if not _contract_record_fields_are_valid(record_type, fields):
            return _RECORD_INVALID
        for field_name, field_value in zip(field_names, fields):
            object.__setattr__(record, field_name, field_value)
    except Exception:
        return _RECORD_INVALID
    return _RECORD_VALID


def _validate_contract_record_worker(value: object) -> object:
    """Return only a fixed sentinel after every sensitive frame unwinds."""

    try:
        if type(value) is AuthenticationIdentityLookupKey:
            valid = _identity_lookup_key_values(value) is not None
        elif type(value) is CurrentAccountAuthority:
            valid = _current_account_authority_values(value) is not None
        elif type(value) is CurrentAccountByUserAuthority:
            valid = _current_account_by_user_authority_values(value) is not None
        elif type(value) is CurrentAccountAuthorityResult:
            valid = _current_account_authority_result_values(value) is not None
        elif type(value) is CurrentAccountByUserAuthorityResult:
            valid = (
                _current_account_by_user_authority_result_values(value)
                is not None
            )
        else:
            valid = False
    except Exception:
        valid = False
    return _RECORD_VALID if valid else _RECORD_INVALID


def validate_authentication_identity_lookup_key(
    identity_key: AuthenticationIdentityLookupKey,
) -> None:
    """Revalidate one exact issuer/subject key without transforming it."""

    validation = _validate_contract_record_worker(identity_key)
    if (
        validation is _RECORD_VALID
        and type(identity_key) is AuthenticationIdentityLookupKey
    ):
        return None
    del identity_key, validation
    _raise_validation_error()


def _validate_prefixed_identifier_worker(
    value: object, expected_prefix: str
) -> object:
    try:
        valid = _valid_prefixed_identifier(value, expected_prefix)
    except Exception:
        valid = False
    return _RECORD_VALID if valid else _RECORD_INVALID


def validate_current_account_user_id(user_id: str) -> None:
    """Validate one exact canonical immutable Cuevion user ID."""

    validation = _validate_prefixed_identifier_worker(
        user_id, _USER_ID_PREFIX
    )
    if validation is _RECORD_VALID and type(user_id) is str:
        return None
    del user_id, validation
    _raise_validation_error()


def validate_current_account_workspace_id(workspace_id: str) -> None:
    """Validate one exact canonical immutable Cuevion workspace ID."""

    validation = _validate_prefixed_identifier_worker(
        workspace_id, _WORKSPACE_ID_PREFIX
    )
    if validation is _RECORD_VALID and type(workspace_id) is str:
        return None
    del workspace_id, validation
    _raise_validation_error()


class CurrentAccountAuthorityRepository(_Protocol):
    """Future inactive boundary for complete current-authority snapshots."""

    def resolve_current_account_by_identity(
        self,
        identity_key: AuthenticationIdentityLookupKey,
        workspace_id: str,
    ) -> CurrentAccountAuthorityResult:
        ...

    def read_current_account_by_user(
        self,
        user_id: str,
        workspace_id: str,
    ) -> CurrentAccountByUserAuthorityResult:
        ...
