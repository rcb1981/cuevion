"""Inactive, provider- and storage-independent initial-account contract.

This module defines immutable values and one repository Protocol only.  It
does not generate identifiers or digests and performs no provider, storage,
environment, clock, random, logging, network, filesystem, HTTP, session, or
product-authorization work.
"""

if (
    __name__ != "cuevion_auth.account_repository_contract"
    or __package__ != "cuevion_auth"
):
    raise ImportError(
        "account repository contract requires its canonical import identity"
    )
if "_ACCOUNT_REPOSITORY_CONTRACT_INITIALIZED" in globals():
    raise ImportError("account repository contract cannot initialize twice")
_ACCOUNT_REPOSITORY_CONTRACT_INITIALIZED = True

import base64 as _base64
from enum import Enum as _Enum
from enum import EnumMeta as _EnumMeta
from typing import Protocol as _Protocol

from api.auth import models as _models


__all__ = (
    "AccountRepositoryContractValidationError",
    "InitialAccountCreationOutcome",
    "InitialAccountConflictReason",
    "NEW_OPERATION_CONFLICT_PRECEDENCE",
    "InitialSecurityEventType",
    "InitialAccountOperationReference",
    "VerifiedAuthenticationEvidence",
    "InitialSecurityEventRequest",
    "InitialAccountCreationRequest",
    "InitialAccountCreationReceipt",
    "InitialAccountCreationResult",
    "validate_initial_account_creation_request",
    "initial_account_creation_requests_are_replay_equivalent",
    "InitialAccountRepository",
)


_ERROR_CONSTRUCTION_FAILURE = (
    "account repository contract validation errors accept no arguments"
)


class AccountRepositoryContractValidationError(ValueError):
    """A fixed, value-free initial-account contract validation failure."""

    __slots__ = ()

    def __new__(
        cls, *arguments: object, **keywords: object
    ) -> "AccountRepositoryContractValidationError":
        valid = (
            cls is AccountRepositoryContractValidationError
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
        return "invalid initial account repository contract value"

    def __repr__(self) -> str:
        return "AccountRepositoryContractValidationError()"


def _raise_validation_error() -> None:
    """Raise one fresh fixed error without retaining an underlying failure."""

    error = AccountRepositoryContractValidationError()
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


class InitialAccountCreationOutcome(
    str, _Enum, metaclass=_ClosedStringEnumMeta
):
    CREATED = "created"
    EXACT_REPLAY = "exact_replay"
    CONFLICT = "conflict"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"
    INTERNAL_ERROR = "internal_error"


class InitialAccountConflictReason(str, _Enum, metaclass=_ClosedStringEnumMeta):
    OPERATION_REFERENCE_MISMATCH = "operation_reference_mismatch"
    AUTHORITY_ALREADY_CLAIMED = "authority_already_claimed"
    EVIDENCE_ALREADY_CONSUMED = "evidence_already_consumed"
    RECORD_ID_COLLISION = "record_id_collision"


NEW_OPERATION_CONFLICT_PRECEDENCE = (
    InitialAccountConflictReason.EVIDENCE_ALREADY_CONSUMED,
    InitialAccountConflictReason.AUTHORITY_ALREADY_CLAIMED,
    InitialAccountConflictReason.RECORD_ID_COLLISION,
)


class InitialSecurityEventType(str, _Enum, metaclass=_ClosedStringEnumMeta):
    INITIAL_ACCOUNT_CREATED = "initial_account_created"


_RECORD_CLASS_DEFINITION_OPEN = True
_RECORD_METACLASS_DEFINITION_OPEN = True
_RECORD_VALID = object()
_RECORD_INVALID = object()


class _RecordMetaBoundary(type):
    """Reject derived record metaclasses before they can bypass the gate."""

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
    """Reject record subclasses before ``type.__new__`` creates them."""

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


class InitialAccountOperationReference(_ImmutableContractRecord):
    __signature__ = (
        "(schema_version, derivation_key_epoch, operation_digest)"
    )
    __slots__ = (
        "schema_version",
        "derivation_key_epoch",
        "operation_digest",
    )

    schema_version: int
    derivation_key_epoch: int
    operation_digest: str

    def __init__(
        self,
        schema_version: int,
        derivation_key_epoch: int,
        operation_digest: str,
    ) -> None:
        validation = _initialize_contract_record_worker(
            self,
            InitialAccountOperationReference,
            _OPERATION_REFERENCE_FIELDS,
            (schema_version, derivation_key_epoch, operation_digest),
        )
        if validation is _RECORD_VALID:
            return None
        del self, schema_version, derivation_key_epoch, operation_digest
        del validation
        _raise_validation_error()


class VerifiedAuthenticationEvidence(_ImmutableContractRecord):
    __signature__ = (
        "(schema_version, trust_domain, verification_coordinator_id, "
        "assertion_id, issuer, subject, authentication_method, "
        "canonical_verified_email, verified_at, issued_at, expires_at)"
    )
    __slots__ = (
        "schema_version",
        "trust_domain",
        "verification_coordinator_id",
        "assertion_id",
        "issuer",
        "subject",
        "authentication_method",
        "canonical_verified_email",
        "verified_at",
        "issued_at",
        "expires_at",
    )

    schema_version: int
    trust_domain: str
    verification_coordinator_id: str
    assertion_id: str
    issuer: str
    subject: str
    authentication_method: _models.AuthenticationMethod
    canonical_verified_email: str
    verified_at: int
    issued_at: int
    expires_at: int

    def __init__(
        self,
        schema_version: int,
        trust_domain: str,
        verification_coordinator_id: str,
        assertion_id: str,
        issuer: str,
        subject: str,
        authentication_method: _models.AuthenticationMethod,
        canonical_verified_email: str,
        verified_at: int,
        issued_at: int,
        expires_at: int,
    ) -> None:
        validation = _initialize_contract_record_worker(
            self,
            VerifiedAuthenticationEvidence,
            _AUTHENTICATION_EVIDENCE_FIELDS,
            (
                schema_version,
                trust_domain,
                verification_coordinator_id,
                assertion_id,
                issuer,
                subject,
                authentication_method,
                canonical_verified_email,
                verified_at,
                issued_at,
                expires_at,
            ),
        )
        if validation is _RECORD_VALID:
            return None
        del self, schema_version, trust_domain, verification_coordinator_id
        del assertion_id, issuer, subject, authentication_method
        del canonical_verified_email, verified_at, issued_at, expires_at
        del validation
        _raise_validation_error()


class InitialSecurityEventRequest(_ImmutableContractRecord):
    __signature__ = "(schema_version, event_id, event_type)"
    __slots__ = ("schema_version", "event_id", "event_type")

    schema_version: int
    event_id: str
    event_type: InitialSecurityEventType

    def __init__(
        self,
        schema_version: int,
        event_id: str,
        event_type: InitialSecurityEventType,
    ) -> None:
        validation = _initialize_contract_record_worker(
            self,
            InitialSecurityEventRequest,
            _SECURITY_EVENT_FIELDS,
            (schema_version, event_id, event_type),
        )
        if validation is _RECORD_VALID:
            return None
        del self, schema_version, event_id, event_type, validation
        _raise_validation_error()


class InitialAccountCreationRequest(_ImmutableContractRecord):
    __signature__ = (
        "(request_version, operation_reference, user, verified_email, "
        "authentication_identity, workspace, workspace_membership, "
        "authentication_evidence, security_event)"
    )
    __slots__ = (
        "request_version",
        "operation_reference",
        "user",
        "verified_email",
        "authentication_identity",
        "workspace",
        "workspace_membership",
        "authentication_evidence",
        "security_event",
    )

    request_version: int
    operation_reference: InitialAccountOperationReference
    user: _models.CuevionUser
    verified_email: _models.VerifiedEmail
    authentication_identity: _models.AuthenticationIdentity
    workspace: _models.Workspace
    workspace_membership: _models.WorkspaceMembership
    authentication_evidence: VerifiedAuthenticationEvidence
    security_event: InitialSecurityEventRequest

    def __init__(
        self,
        request_version: int,
        operation_reference: InitialAccountOperationReference,
        user: _models.CuevionUser,
        verified_email: _models.VerifiedEmail,
        authentication_identity: _models.AuthenticationIdentity,
        workspace: _models.Workspace,
        workspace_membership: _models.WorkspaceMembership,
        authentication_evidence: VerifiedAuthenticationEvidence,
        security_event: InitialSecurityEventRequest,
    ) -> None:
        validation = _initialize_contract_record_worker(
            self,
            InitialAccountCreationRequest,
            _CREATION_REQUEST_FIELDS,
            (
                request_version,
                operation_reference,
                user,
                verified_email,
                authentication_identity,
                workspace,
                workspace_membership,
                authentication_evidence,
                security_event,
            ),
        )
        if validation is _RECORD_VALID:
            return None
        del self, request_version, operation_reference, user, verified_email
        del authentication_identity, workspace, workspace_membership
        del authentication_evidence, security_event, validation
        _raise_validation_error()


class InitialAccountCreationReceipt(_ImmutableContractRecord):
    __signature__ = (
        "(schema_version, user_id, verified_email_id, "
        "authentication_identity_id, workspace_id, security_event_id)"
    )
    __slots__ = (
        "schema_version",
        "user_id",
        "verified_email_id",
        "authentication_identity_id",
        "workspace_id",
        "security_event_id",
    )

    schema_version: int
    user_id: str
    verified_email_id: str
    authentication_identity_id: str
    workspace_id: str
    security_event_id: str

    def __init__(
        self,
        schema_version: int,
        user_id: str,
        verified_email_id: str,
        authentication_identity_id: str,
        workspace_id: str,
        security_event_id: str,
    ) -> None:
        validation = _initialize_contract_record_worker(
            self,
            InitialAccountCreationReceipt,
            _CREATION_RECEIPT_FIELDS,
            (
                schema_version,
                user_id,
                verified_email_id,
                authentication_identity_id,
                workspace_id,
                security_event_id,
            ),
        )
        if validation is _RECORD_VALID:
            return None
        del self, schema_version, user_id, verified_email_id
        del authentication_identity_id, workspace_id, security_event_id
        del validation
        _raise_validation_error()


class InitialAccountCreationResult(_ImmutableContractRecord):
    __signature__ = "(outcome, conflict_reason, receipt)"
    __slots__ = ("outcome", "conflict_reason", "receipt")

    outcome: InitialAccountCreationOutcome
    conflict_reason: InitialAccountConflictReason | None
    receipt: InitialAccountCreationReceipt | None

    def __init__(
        self,
        outcome: InitialAccountCreationOutcome,
        conflict_reason: InitialAccountConflictReason | None,
        receipt: InitialAccountCreationReceipt | None,
    ) -> None:
        validation = _initialize_contract_record_worker(
            self,
            InitialAccountCreationResult,
            _CREATION_RESULT_FIELDS,
            (outcome, conflict_reason, receipt),
        )
        if validation is _RECORD_VALID:
            return None
        del self, outcome, conflict_reason, receipt, validation
        _raise_validation_error()


_EXACT_RECORD_TYPES = (
    InitialAccountOperationReference,
    VerifiedAuthenticationEvidence,
    InitialSecurityEventRequest,
    InitialAccountCreationRequest,
    InitialAccountCreationReceipt,
    InitialAccountCreationResult,
)
_RECORD_CLASS_DEFINITION_OPEN = False


_BASE64URL_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789-_"
)
_OPAQUE_IDENTIFIER_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789._:-"
)
_OPAQUE_IDENTIFIER_MAX_CHARACTERS = 128
_DIGEST_ENCODED_LENGTH = 43
_DIGEST_BYTE_LENGTH = 32
_SECURITY_EVENT_PREFIX = "sev_"
_SECURITY_EVENT_ENCODED_LENGTH = 22
_SECURITY_EVENT_BYTE_LENGTH = 16
_MAXIMUM_DERIVATION_KEY_EPOCH = 4_294_967_295

_OPERATION_REFERENCE_FIELDS = (
    "schema_version",
    "derivation_key_epoch",
    "operation_digest",
)
_AUTHENTICATION_EVIDENCE_FIELDS = (
    "schema_version",
    "trust_domain",
    "verification_coordinator_id",
    "assertion_id",
    "issuer",
    "subject",
    "authentication_method",
    "canonical_verified_email",
    "verified_at",
    "issued_at",
    "expires_at",
)
_SECURITY_EVENT_FIELDS = (
    "schema_version",
    "event_id",
    "event_type",
)
_CREATION_REQUEST_FIELDS = (
    "request_version",
    "operation_reference",
    "user",
    "verified_email",
    "authentication_identity",
    "workspace",
    "workspace_membership",
    "authentication_evidence",
    "security_event",
)
_CREATION_RECEIPT_FIELDS = (
    "schema_version",
    "user_id",
    "verified_email_id",
    "authentication_identity_id",
    "workspace_id",
    "security_event_id",
)
_CREATION_RESULT_FIELDS = (
    "outcome",
    "conflict_reason",
    "receipt",
)

_USER_FIELDS = (
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


def _is_schema_one(value: object) -> bool:
    return type(value) is int and value == 1


def _is_digest_identifier(value: object) -> bool:
    if type(value) is not str or len(value) != _DIGEST_ENCODED_LENGTH:
        return False
    decoded = _decode_canonical_base64url(value)
    return type(decoded) is bytes and len(decoded) == _DIGEST_BYTE_LENGTH


def _is_security_event_id(value: object) -> bool:
    if (
        type(value) is not str
        or len(value)
        != len(_SECURITY_EVENT_PREFIX) + _SECURITY_EVENT_ENCODED_LENGTH
        or not value.startswith(_SECURITY_EVENT_PREFIX)
    ):
        return False
    decoded = _decode_canonical_base64url(
        value[len(_SECURITY_EVENT_PREFIX) :]
    )
    return type(decoded) is bytes and len(decoded) == _SECURITY_EVENT_BYTE_LENGTH


def _is_opaque_identifier(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and len(value) <= _OPAQUE_IDENTIFIER_MAX_CHARACTERS
        and value.isascii()
        and all(
            character in _OPAQUE_IDENTIFIER_CHARACTERS for character in value
        )
    )


def _is_auth_security_identifier(value: object, maximum: int) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value.isascii()
        and len(value) <= maximum
        and all("!" <= character <= "~" for character in value)
    )


def _operation_reference_fields_are_valid(
    fields: tuple[object, ...],
) -> bool:
    schema_version, derivation_key_epoch, operation_digest = fields
    return not (
        not _is_schema_one(schema_version)
        or type(derivation_key_epoch) is not int
        or not 1
        <= derivation_key_epoch
        <= _MAXIMUM_DERIVATION_KEY_EPOCH
        or not _is_digest_identifier(operation_digest)
    )


def _operation_reference_values(
    value: object,
) -> tuple[object, ...] | None:
    fields = _read_exact_fields(
        value, InitialAccountOperationReference, _OPERATION_REFERENCE_FIELDS
    )
    return (
        fields
        if fields is not None
        and _operation_reference_fields_are_valid(fields)
        else None
    )


def _authentication_evidence_fields_are_valid(
    fields: tuple[object, ...],
) -> bool:
    (
        schema_version,
        trust_domain,
        verification_coordinator_id,
        assertion_id,
        issuer,
        subject,
        authentication_method,
        canonical_verified_email,
        verified_at,
        issued_at,
        expires_at,
    ) = fields
    return not (
        not _is_schema_one(schema_version)
        or not _is_opaque_identifier(trust_domain)
        or not _is_opaque_identifier(verification_coordinator_id)
        or not _is_digest_identifier(assertion_id)
        or not _is_auth_security_identifier(issuer, 512)
        or not _is_auth_security_identifier(subject, 512)
        or type(authentication_method) is not _models.AuthenticationMethod
        or not _models._valid_canonical_email(canonical_verified_email)
        or not _models._is_timestamp(verified_at)
        or not _models._is_timestamp(issued_at)
        or not _models._is_timestamp(expires_at)
        or not verified_at <= issued_at < expires_at
    )


def _authentication_evidence_values(
    value: object,
) -> tuple[object, ...] | None:
    fields = _read_exact_fields(
        value,
        VerifiedAuthenticationEvidence,
        _AUTHENTICATION_EVIDENCE_FIELDS,
    )
    return (
        fields
        if fields is not None
        and _authentication_evidence_fields_are_valid(fields)
        else None
    )


def _security_event_fields_are_valid(fields: tuple[object, ...]) -> bool:
    schema_version, event_id, event_type = fields
    return not (
        not _is_schema_one(schema_version)
        or not _is_security_event_id(event_id)
        or type(event_type) is not InitialSecurityEventType
        or event_type is not InitialSecurityEventType.INITIAL_ACCOUNT_CREATED
    )


def _security_event_values(value: object) -> tuple[object, ...] | None:
    fields = _read_exact_fields(
        value, InitialSecurityEventRequest, _SECURITY_EVENT_FIELDS
    )
    return (
        fields
        if fields is not None and _security_event_fields_are_valid(fields)
        else None
    )


def _receipt_fields_are_valid(fields: tuple[object, ...]) -> bool:
    (
        schema_version,
        user_id,
        verified_email_id,
        authentication_identity_id,
        workspace_id,
        security_event_id,
    ) = fields
    return not (
        not _is_schema_one(schema_version)
        or not _models._valid_user_id(user_id)
        or not _models._valid_verified_email_id(verified_email_id)
        or not _models._valid_authentication_identity_id(
            authentication_identity_id
        )
        or not _models._valid_workspace_id(workspace_id)
        or not _is_security_event_id(security_event_id)
    )


def _receipt_values(value: object) -> tuple[object, ...] | None:
    fields = _read_exact_fields(
        value, InitialAccountCreationReceipt, _CREATION_RECEIPT_FIELDS
    )
    return (
        fields
        if fields is not None and _receipt_fields_are_valid(fields)
        else None
    )


def _result_fields_are_valid(fields: tuple[object, ...]) -> bool:
    outcome, conflict_reason, receipt = fields
    if type(outcome) is not InitialAccountCreationOutcome:
        return False
    if outcome in (
        InitialAccountCreationOutcome.CREATED,
        InitialAccountCreationOutcome.EXACT_REPLAY,
    ):
        if conflict_reason is not None or _receipt_values(receipt) is None:
            return False
    elif outcome is InitialAccountCreationOutcome.CONFLICT:
        if (
            type(conflict_reason) is not InitialAccountConflictReason
            or receipt is not None
        ):
            return False
    elif outcome in (
        InitialAccountCreationOutcome.AMBIGUOUS,
        InitialAccountCreationOutcome.UNAVAILABLE,
        InitialAccountCreationOutcome.INTERNAL_ERROR,
    ):
        if conflict_reason is not None or receipt is not None:
            return False
    else:
        return False
    return True


def _result_values(value: object) -> tuple[object, ...] | None:
    fields = _read_exact_fields(
        value, InitialAccountCreationResult, _CREATION_RESULT_FIELDS
    )
    return (
        fields
        if fields is not None and _result_fields_are_valid(fields)
        else None
    )


def _request_fields_are_valid(fields: tuple[object, ...]) -> bool:
    (
        request_version,
        operation_reference,
        user,
        verified_email,
        authentication_identity,
        workspace,
        workspace_membership,
        authentication_evidence,
        security_event,
    ) = fields
    if (
        not _is_schema_one(request_version)
        or _operation_reference_values(operation_reference) is None
        or _authentication_evidence_values(authentication_evidence) is None
        or _security_event_values(security_event) is None
        or type(user) is not _models.CuevionUser
        or type(verified_email) is not _models.VerifiedEmail
        or type(authentication_identity) is not _models.AuthenticationIdentity
        or type(workspace) is not _models.Workspace
        or type(workspace_membership) is not _models.WorkspaceMembership
    ):
        return False
    try:
        if (
            _models.validate_user_primary_email(user, verified_email)
            is not None
            or _models.validate_identity_for_user(
                authentication_identity, user, verified_email
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

    user_values = _read_exact_fields(user, _models.CuevionUser, _USER_FIELDS)
    email_values = _read_exact_fields(
        verified_email, _models.VerifiedEmail, _VERIFIED_EMAIL_FIELDS
    )
    identity_values = _read_exact_fields(
        authentication_identity,
        _models.AuthenticationIdentity,
        _AUTHENTICATION_IDENTITY_FIELDS,
    )
    workspace_values = _read_exact_fields(
        workspace, _models.Workspace, _WORKSPACE_FIELDS
    )
    membership_values = _read_exact_fields(
        workspace_membership,
        _models.WorkspaceMembership,
        _WORKSPACE_MEMBERSHIP_FIELDS,
    )
    evidence_values = _authentication_evidence_values(
        authentication_evidence
    )
    if (
        user_values is None
        or email_values is None
        or identity_values is None
        or workspace_values is None
        or membership_values is None
        or evidence_values is None
    ):
        return False

    user_id = user_values[1]
    email_id = email_values[1]
    workspace_id = workspace_values[1]
    if (
        user_values[2] is not _models.UserStatus.ACTIVE
        or user_values[3] != email_id
        or user_values[5] != 1
        or user_values[8] != 1
        or email_values[2] != user_id
        or email_values[4] is not _models.VerifiedEmailStatus.VERIFIED
        or email_values[8] is not None
        or email_values[9] != 1
        or identity_values[2] != user_id
        or identity_values[6]
        is not _models.AuthenticationIdentityStatus.ACTIVE
        or identity_values[7] != email_id
        or identity_values[10] != 1
        or workspace_values[2] is not _models.WorkspaceStatus.ACTIVE
        or workspace_values[3] != user_id
        or workspace_values[6] != 1
        or membership_values[1] != workspace_id
        or membership_values[2] != user_id
        or membership_values[3] is not _models.WorkspaceRole.OWNER
        or membership_values[4]
        is not _models.WorkspaceMembershipStatus.ACTIVE
        or membership_values[7] != 1
        or evidence_values[4] != identity_values[3]
        or evidence_values[5] != identity_values[4]
        or evidence_values[6] is not identity_values[5]
        or evidence_values[7] != email_values[3]
        or evidence_values[8] != email_values[7]
    ):
        return False
    return True


def _request_values(value: object) -> tuple[object, ...] | None:
    fields = _read_exact_fields(
        value, InitialAccountCreationRequest, _CREATION_REQUEST_FIELDS
    )
    return (
        fields
        if fields is not None and _request_fields_are_valid(fields)
        else None
    )


def _contract_record_fields_are_valid(
    record_type: type[object], fields: tuple[object, ...]
) -> bool:
    if record_type is InitialAccountOperationReference:
        return _operation_reference_fields_are_valid(fields)
    if record_type is VerifiedAuthenticationEvidence:
        return _authentication_evidence_fields_are_valid(fields)
    if record_type is InitialSecurityEventRequest:
        return _security_event_fields_are_valid(fields)
    if record_type is InitialAccountCreationRequest:
        return _request_fields_are_valid(fields)
    if record_type is InitialAccountCreationReceipt:
        return _receipt_fields_are_valid(fields)
    if record_type is InitialAccountCreationResult:
        return _result_fields_are_valid(fields)
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
        if type(value) is InitialAccountOperationReference:
            valid = _operation_reference_values(value) is not None
        elif type(value) is VerifiedAuthenticationEvidence:
            valid = _authentication_evidence_values(value) is not None
        elif type(value) is InitialSecurityEventRequest:
            valid = _security_event_values(value) is not None
        elif type(value) is InitialAccountCreationRequest:
            valid = _request_values(value) is not None
        elif type(value) is InitialAccountCreationReceipt:
            valid = _receipt_values(value) is not None
        elif type(value) is InitialAccountCreationResult:
            valid = _result_values(value) is not None
        else:
            valid = False
    except Exception:
        valid = False
    return _RECORD_VALID if valid else _RECORD_INVALID


def validate_initial_account_creation_request(
    request: InitialAccountCreationRequest,
) -> None:
    """Revalidate one complete initial-account request without side effects."""

    validation = _validate_contract_record_worker(request)
    if validation is _RECORD_VALID and type(request) is InitialAccountCreationRequest:
        return None
    del request, validation
    _raise_validation_error()


_REPLAY_COMPARISON_FAILED = object()


def _field_values_are_equal(
    first_values: tuple[object, ...], second_values: tuple[object, ...]
) -> bool:
    if len(first_values) != len(second_values):
        return False
    for index in range(len(first_values)):
        first_value = first_values[index]
        second_value = second_values[index]
        if type(first_value) is not type(second_value):
            return False
        if isinstance(first_value, _Enum):
            if first_value is not second_value:
                return False
        elif first_value != second_value:
            return False
    return True


def _replay_equivalence_worker(
    first: InitialAccountCreationRequest,
    second: InitialAccountCreationRequest,
) -> object:
    """Compare every caller-controlled persisted field explicitly."""

    try:
        first_request = _read_exact_fields(
            first, InitialAccountCreationRequest, _CREATION_REQUEST_FIELDS
        )
        second_request = _read_exact_fields(
            second, InitialAccountCreationRequest, _CREATION_REQUEST_FIELDS
        )
        if first_request is None or second_request is None:
            return _REPLAY_COMPARISON_FAILED
        if first_request[0] != second_request[0]:
            return False
        comparisons = (
            (
                _read_exact_fields(
                    first_request[1],
                    InitialAccountOperationReference,
                    _OPERATION_REFERENCE_FIELDS,
                ),
                _read_exact_fields(
                    second_request[1],
                    InitialAccountOperationReference,
                    _OPERATION_REFERENCE_FIELDS,
                ),
            ),
            (
                _read_exact_fields(
                    first_request[2], _models.CuevionUser, _USER_FIELDS
                ),
                _read_exact_fields(
                    second_request[2], _models.CuevionUser, _USER_FIELDS
                ),
            ),
            (
                _read_exact_fields(
                    first_request[3],
                    _models.VerifiedEmail,
                    _VERIFIED_EMAIL_FIELDS,
                ),
                _read_exact_fields(
                    second_request[3],
                    _models.VerifiedEmail,
                    _VERIFIED_EMAIL_FIELDS,
                ),
            ),
            (
                _read_exact_fields(
                    first_request[4],
                    _models.AuthenticationIdentity,
                    _AUTHENTICATION_IDENTITY_FIELDS,
                ),
                _read_exact_fields(
                    second_request[4],
                    _models.AuthenticationIdentity,
                    _AUTHENTICATION_IDENTITY_FIELDS,
                ),
            ),
            (
                _read_exact_fields(
                    first_request[5], _models.Workspace, _WORKSPACE_FIELDS
                ),
                _read_exact_fields(
                    second_request[5], _models.Workspace, _WORKSPACE_FIELDS
                ),
            ),
            (
                _read_exact_fields(
                    first_request[6],
                    _models.WorkspaceMembership,
                    _WORKSPACE_MEMBERSHIP_FIELDS,
                ),
                _read_exact_fields(
                    second_request[6],
                    _models.WorkspaceMembership,
                    _WORKSPACE_MEMBERSHIP_FIELDS,
                ),
            ),
            (
                _read_exact_fields(
                    first_request[7],
                    VerifiedAuthenticationEvidence,
                    _AUTHENTICATION_EVIDENCE_FIELDS,
                ),
                _read_exact_fields(
                    second_request[7],
                    VerifiedAuthenticationEvidence,
                    _AUTHENTICATION_EVIDENCE_FIELDS,
                ),
            ),
            (
                _read_exact_fields(
                    first_request[8],
                    InitialSecurityEventRequest,
                    _SECURITY_EVENT_FIELDS,
                ),
                _read_exact_fields(
                    second_request[8],
                    InitialSecurityEventRequest,
                    _SECURITY_EVENT_FIELDS,
                ),
            ),
        )
        for first_values, second_values in comparisons:
            if (
                first_values is None
                or second_values is None
                or not _field_values_are_equal(first_values, second_values)
            ):
                return False
    except Exception:
        return _REPLAY_COMPARISON_FAILED
    return True


def initial_account_creation_requests_are_replay_equivalent(
    first: InitialAccountCreationRequest,
    second: InitialAccountCreationRequest,
) -> bool:
    """Return whether two valid requests contain exactly the same fields."""

    first_validation = _validate_contract_record_worker(first)
    second_validation = _validate_contract_record_worker(second)
    if (
        first_validation is not _RECORD_VALID
        or second_validation is not _RECORD_VALID
        or type(first) is not InitialAccountCreationRequest
        or type(second) is not InitialAccountCreationRequest
    ):
        del first, second, first_validation, second_validation
        _raise_validation_error()
    comparison = _replay_equivalence_worker(first, second)
    if type(comparison) is bool:
        return comparison
    del first, second, first_validation, second_validation, comparison
    _raise_validation_error()


class InitialAccountRepository(_Protocol):
    """Future atomic creation and exact-request reconciliation boundary."""

    def create_initial_account(
        self,
        request: InitialAccountCreationRequest,
    ) -> InitialAccountCreationResult:
        ...
