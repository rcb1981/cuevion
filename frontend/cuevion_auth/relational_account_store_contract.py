"""Inactive, vendor-independent relational account-store requirements.

This module contains immutable logical manifests and pure validation only.  It
does not implement persistence, migrations, transactions, sessions, providers,
HTTP behavior, product authorization, configuration, or any other I/O.
"""

import sys as _sys


if (
    __name__ != "cuevion_auth.relational_account_store_contract"
    or __package__ != "cuevion_auth"
):
    raise ImportError(
        "relational account-store contract requires its canonical import identity"
    )
if (
    getattr(
        _sys.modules.get("cuevion_auth.relational_account_store_contract"),
        "__dict__",
        None,
    )
    is not globals()
):
    raise ImportError(
        "relational account-store contract requires its canonical module object"
    )
if "_RELATIONAL_ACCOUNT_STORE_CONTRACT_INITIALIZED" in globals():
    raise ImportError("relational account-store contract cannot initialize twice")
_RELATIONAL_ACCOUNT_STORE_CONTRACT_INITIALIZED = True

from enum import Enum as _Enum
from enum import EnumMeta as _EnumMeta

from api.auth import models as _models
from cuevion_auth import account_repository_contract as _account_contract


if _models is not _sys.modules.get("api.auth.models"):
    raise ImportError("account models require their canonical import identity")
if _account_contract is not _sys.modules.get(
    "cuevion_auth.account_repository_contract"
):
    raise ImportError(
        "initial-account contract requires its canonical import identity"
    )


__all__ = (
    "RelationalAccountRelation",
    "RelationalConstraintCategory",
    "RelationalVersionCategory",
    "ConsistentReadRequirementCategory",
    "RelationalFieldManifest",
    "RelationalPrimaryKeyManifest",
    "RelationalForeignKeyManifest",
    "RelationalUniqueConstraintManifest",
    "RelationalInvariantManifest",
    "RelationalVersionRequirement",
    "RequestSnapshotFieldManifest",
    "RequestSnapshotManifest",
    "InitialAccountTransactionManifest",
    "ConsistentReadRequirementManifest",
    "RelationalRelationManifest",
    "RelationalSchemaManifest",
    "ACCOUNT_SECURITY_EVENT_STREAM_NAME",
    "RELATIONAL_ACCOUNT_SCHEMA_1",
    "relational_schema_manifest_is_valid",
    "relational_version_is_supported",
    "request_snapshot_covers_initial_account_request",
)


class _RelationalContractValidationError(ValueError):
    __slots__ = ()

    def __init__(self) -> None:
        ValueError.__init__(self)

    def __str__(self) -> str:
        return "invalid relational account-store contract value"

    def __repr__(self) -> str:
        return "_RelationalContractValidationError()"


def _raise_contract_error() -> None:
    error = _RelationalContractValidationError()
    try:
        raise error
    finally:
        object.__setattr__(error, "__context__", None)
        object.__setattr__(error, "__cause__", None)


_ENUM_MISSING = object()


class _ClosedStringEnumMeta(_EnumMeta):
    def __call__(
        cls,
        value: object = _ENUM_MISSING,
        *arguments: object,
        **keywords: object,
    ) -> object:
        if arguments or keywords:
            _raise_contract_error()
        if type(value) is cls:
            return value
        if type(value) is str:
            member = cls._value2member_map_.get(value, _ENUM_MISSING)
            if type(member) is cls:
                return member
        _raise_contract_error()


class RelationalAccountRelation(str, _Enum, metaclass=_ClosedStringEnumMeta):
    USERS = "users"
    VERIFIED_EMAILS = "verified_emails"
    AUTHENTICATION_IDENTITIES = "authentication_identities"
    WORKSPACES = "workspaces"
    WORKSPACE_MEMBERSHIPS = "workspace_memberships"
    INITIAL_ACCOUNT_OPERATIONS = "initial_account_operations"
    SECURITY_EVENTS = "security_events"


class RelationalConstraintCategory(
    str, _Enum, metaclass=_ClosedStringEnumMeta
):
    PRIMARY_KEY = "primary_key"
    FOREIGN_KEY = "foreign_key"
    UNIQUE_AUTHORITY_CLAIM = "unique_authority_claim"
    UNIQUE_OPERATION_REFERENCE = "unique_operation_reference"
    UNIQUE_EVIDENCE_ASSERTION = "unique_evidence_assertion"
    UNIQUE_INITIAL_OPERATION_EVENT = "unique_initial_operation_event"
    UNIQUE_EVENT_STREAM_POSITION = "unique_event_stream_position"
    POSITIVE_VERSION = "positive_version"
    POSITIVE_SECURITY_EPOCH = "positive_security_epoch"
    VALID_STATUS = "valid_status"
    VALID_ROLE = "valid_role"
    VALID_AUTHENTICATION_METHOD = "valid_authentication_method"
    VALID_EVENT_TYPE = "valid_event_type"
    TIMESTAMP_ORDER = "timestamp_order"
    SAME_USER_REFERENCE = "same_user_reference"
    IMMUTABLE_VALUE = "immutable_value"
    APPEND_ONLY = "append_only"
    EXACT_CASE_SENSITIVE_VALUE = "exact_case_sensitive_value"
    EXACT_FIELD_EQUALITY = "exact_field_equality"
    CANONICAL_IDENTIFIER = "canonical_identifier"
    CANONICAL_DIGEST = "canonical_digest"


class RelationalVersionCategory(str, _Enum, metaclass=_ClosedStringEnumMeta):
    RELATIONAL_SCHEMA_CONTRACT = "relational_schema_contract"
    RELATION_RECORD = "relation_record"
    INITIAL_OPERATION_RECORD = "initial_operation_record"
    REQUEST_SNAPSHOT = "request_snapshot"
    RECEIPT = "receipt"
    EVIDENCE = "evidence"
    SECURITY_EVENT_RECORD = "security_event_record"
    EVENT_PAYLOAD = "event_payload"
    CONSISTENT_READ_CONTRACT = "consistent_read_contract"


class ConsistentReadRequirementCategory(
    str, _Enum, metaclass=_ClosedStringEnumMeta
):
    ATOMIC_SNAPSHOT = "atomic_snapshot"
    STORED_SESSION = "stored_session"
    EXACT_LINKED_USER = "exact_linked_user"
    EXACT_LINKED_AUTHENTICATION_IDENTITY = (
        "exact_linked_authentication_identity"
    )
    PRIMARY_VERIFIED_EMAIL = "primary_verified_email"
    CURRENT_AUTHENTICATION_STATUS = "current_authentication_status"
    SECURITY_EPOCH = "security_epoch"
    RECORD_VERSIONS = "record_versions"
    CREDENTIAL_DIGESTS_AND_EPOCHS = "credential_digests_and_epochs"
    SESSION_EXPIRY_AND_REVOCATION = "session_expiry_and_revocation"
    WORKSPACE_AUTHORIZATION_EXCLUDED = "workspace_authorization_excluded"
    PRODUCT_AUTHORIZATION_EXCLUDED = "product_authorization_excluded"


ACCOUNT_SECURITY_EVENT_STREAM_NAME = "cuevion.account.security"


_RECORD_DEFINITION_OPEN = True
_SERIALIZATION_ERROR = "relational account-store manifests cannot be serialized"


class _ImmutableManifestRecord:
    __slots__ = ()

    def __init_subclass__(cls, **keywords: object) -> None:
        if not _RECORD_DEFINITION_OPEN:
            _raise_contract_error()
        super().__init_subclass__(**keywords)

    def __setattr__(self, name: str, value: object) -> None:
        del self, name, value
        _raise_contract_error()

    def __delattr__(self, name: str) -> None:
        del self, name
        _raise_contract_error()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(...)"

    def __str__(self) -> str:
        return type(self).__name__

    def __copy__(self) -> "_ImmutableManifestRecord":
        return self

    def __deepcopy__(self, _memo: object) -> "_ImmutableManifestRecord":
        return self

    def __reduce__(self) -> object:
        raise TypeError(_SERIALIZATION_ERROR)

    def __reduce_ex__(self, protocol: object) -> object:
        del protocol
        raise TypeError(_SERIALIZATION_ERROR)

    def __getstate__(self) -> object:
        raise TypeError(_SERIALIZATION_ERROR)

    def __setstate__(self, state: object) -> None:
        del state
        raise TypeError(_SERIALIZATION_ERROR)


def _exact_string_tuple(value: object, *, allow_empty: bool = True) -> bool:
    return (
        type(value) is tuple
        and (allow_empty or bool(value))
        and all(type(item) is str and bool(item) for item in value)
        and len(set(value)) == len(value)
    )


def _exact_record_tuple(
    value: object, expected_type: type[object], *, allow_empty: bool = True
) -> bool:
    return (
        type(value) is tuple
        and (allow_empty or bool(value))
        and all(type(item) is expected_type for item in value)
    )


def _initialize_record(
    record: object, field_names: tuple[str, ...], values: tuple[object, ...]
) -> None:
    try:
        for name, value in zip(field_names, values, strict=True):
            object.__setattr__(record, name, value)
    except Exception:
        _raise_contract_error()


class RelationalFieldManifest(_ImmutableManifestRecord):
    __slots__ = ("name", "required", "nullable", "immutable")

    name: str
    required: bool
    nullable: bool
    immutable: bool

    def __init__(
        self, name: str, required: bool, nullable: bool, immutable: bool
    ) -> None:
        if (
            type(name) is not str
            or not name
            or type(required) is not bool
            or type(nullable) is not bool
            or type(immutable) is not bool
            or not required
        ):
            _raise_contract_error()
        _initialize_record(self, self.__slots__, (name, required, nullable, immutable))


class RelationalPrimaryKeyManifest(_ImmutableManifestRecord):
    __slots__ = ("category", "field_names")

    category: RelationalConstraintCategory
    field_names: tuple[str, ...]

    def __init__(
        self,
        category: RelationalConstraintCategory,
        field_names: tuple[str, ...],
    ) -> None:
        if (
            category is not RelationalConstraintCategory.PRIMARY_KEY
            or not _exact_string_tuple(field_names, allow_empty=False)
        ):
            _raise_contract_error()
        _initialize_record(self, self.__slots__, (category, field_names))


class RelationalForeignKeyManifest(_ImmutableManifestRecord):
    __slots__ = (
        "categories",
        "field_names",
        "referenced_relation",
        "referenced_field_names",
    )

    categories: tuple[RelationalConstraintCategory, ...]
    field_names: tuple[str, ...]
    referenced_relation: RelationalAccountRelation
    referenced_field_names: tuple[str, ...]

    def __init__(
        self,
        categories: tuple[RelationalConstraintCategory, ...],
        field_names: tuple[str, ...],
        referenced_relation: RelationalAccountRelation,
        referenced_field_names: tuple[str, ...],
    ) -> None:
        if (
            type(categories) is not tuple
            or not categories
            or any(
                type(category) is not RelationalConstraintCategory
                for category in categories
            )
            or len(set(categories)) != len(categories)
            or RelationalConstraintCategory.FOREIGN_KEY not in categories
            or any(
                category
                not in (
                    RelationalConstraintCategory.FOREIGN_KEY,
                    RelationalConstraintCategory.SAME_USER_REFERENCE,
                )
                for category in categories
            )
            or not _exact_string_tuple(field_names, allow_empty=False)
            or type(referenced_relation) is not RelationalAccountRelation
            or not _exact_string_tuple(
                referenced_field_names, allow_empty=False
            )
            or len(field_names) != len(referenced_field_names)
        ):
            _raise_contract_error()
        _initialize_record(
            self,
            self.__slots__,
            (
                categories,
                field_names,
                referenced_relation,
                referenced_field_names,
            ),
        )


_UNIQUE_CATEGORIES = (
    RelationalConstraintCategory.UNIQUE_AUTHORITY_CLAIM,
    RelationalConstraintCategory.UNIQUE_OPERATION_REFERENCE,
    RelationalConstraintCategory.UNIQUE_EVIDENCE_ASSERTION,
    RelationalConstraintCategory.UNIQUE_INITIAL_OPERATION_EVENT,
    RelationalConstraintCategory.UNIQUE_EVENT_STREAM_POSITION,
)


class RelationalUniqueConstraintManifest(_ImmutableManifestRecord):
    __slots__ = ("category", "field_names", "scope_field_names")

    category: RelationalConstraintCategory
    field_names: tuple[str, ...]
    scope_field_names: tuple[str, ...]

    def __init__(
        self,
        category: RelationalConstraintCategory,
        field_names: tuple[str, ...],
        scope_field_names: tuple[str, ...],
    ) -> None:
        if (
            type(category) is not RelationalConstraintCategory
            or category not in _UNIQUE_CATEGORIES
            or not _exact_string_tuple(field_names, allow_empty=False)
            or not _exact_string_tuple(scope_field_names)
        ):
            _raise_contract_error()
        _initialize_record(
            self, self.__slots__, (category, field_names, scope_field_names)
        )


class RelationalInvariantManifest(_ImmutableManifestRecord):
    __slots__ = ("category", "field_names")

    category: RelationalConstraintCategory
    field_names: tuple[str, ...]

    def __init__(
        self,
        category: RelationalConstraintCategory,
        field_names: tuple[str, ...],
    ) -> None:
        if (
            type(category) is not RelationalConstraintCategory
            or category
            in (
                RelationalConstraintCategory.PRIMARY_KEY,
                RelationalConstraintCategory.FOREIGN_KEY,
                *_UNIQUE_CATEGORIES,
            )
            or not _exact_string_tuple(field_names)
            or (
                category is RelationalConstraintCategory.EXACT_FIELD_EQUALITY
                and len(field_names) != 2
            )
        ):
            _raise_contract_error()
        _initialize_record(self, self.__slots__, (category, field_names))


class RelationalVersionRequirement(_ImmutableManifestRecord):
    __slots__ = (
        "category",
        "supported_version",
        "unknown_newer_fails_closed",
        "implicit_authority_defaults_allowed",
    )

    category: RelationalVersionCategory
    supported_version: int
    unknown_newer_fails_closed: bool
    implicit_authority_defaults_allowed: bool

    def __init__(
        self,
        category: RelationalVersionCategory,
        supported_version: int,
        unknown_newer_fails_closed: bool,
        implicit_authority_defaults_allowed: bool,
    ) -> None:
        if (
            type(category) is not RelationalVersionCategory
            or type(supported_version) is not int
            or supported_version != 1
            or type(unknown_newer_fails_closed) is not bool
            or not unknown_newer_fails_closed
            or type(implicit_authority_defaults_allowed) is not bool
            or implicit_authority_defaults_allowed
        ):
            _raise_contract_error()
        _initialize_record(
            self,
            self.__slots__,
            (
                category,
                supported_version,
                unknown_newer_fails_closed,
                implicit_authority_defaults_allowed,
            ),
        )


class RequestSnapshotFieldManifest(_ImmutableManifestRecord):
    __slots__ = ("source_path", "operation_field_name")

    source_path: str
    operation_field_name: str

    def __init__(self, source_path: str, operation_field_name: str) -> None:
        if (
            type(source_path) is not str
            or not source_path
            or type(operation_field_name) is not str
            or not operation_field_name
        ):
            _raise_contract_error()
        _initialize_record(
            self, self.__slots__, (source_path, operation_field_name)
        )


class RequestSnapshotManifest(_ImmutableManifestRecord):
    __slots__ = ("version", "request_field_names", "fields")

    version: int
    request_field_names: tuple[str, ...]
    fields: tuple[RequestSnapshotFieldManifest, ...]

    def __init__(
        self,
        version: int,
        request_field_names: tuple[str, ...],
        fields: tuple[RequestSnapshotFieldManifest, ...],
    ) -> None:
        if (
            type(version) is not int
            or version != 1
            or not _exact_string_tuple(request_field_names, allow_empty=False)
            or not _exact_record_tuple(
                fields, RequestSnapshotFieldManifest, allow_empty=False
            )
        ):
            _raise_contract_error()
        _initialize_record(
            self, self.__slots__, (version, request_field_names, fields)
        )


class InitialAccountTransactionManifest(_ImmutableManifestRecord):
    __slots__ = (
        "contract_version",
        "relations",
        "all_or_nothing_visibility",
        "operation_lookup_before_current_policy",
        "exact_replay_writes",
        "mismatch_outcome",
        "failure_rolls_back_all",
        "event_failure_rolls_back_all",
        "exactly_one_initial_event",
        "operation_and_aggregate_visible_together",
        "pending_record_allowed",
        "created_requires_confirmed_commit",
        "unknown_commit_outcome",
        "unavailable_requires_known_no_commit",
        "internal_error_requires_known_nonambiguous_failure",
    )

    contract_version: int
    relations: tuple[RelationalAccountRelation, ...]
    all_or_nothing_visibility: bool
    operation_lookup_before_current_policy: bool
    exact_replay_writes: bool
    mismatch_outcome: _account_contract.InitialAccountCreationOutcome
    failure_rolls_back_all: bool
    event_failure_rolls_back_all: bool
    exactly_one_initial_event: bool
    operation_and_aggregate_visible_together: bool
    pending_record_allowed: bool
    created_requires_confirmed_commit: bool
    unknown_commit_outcome: _account_contract.InitialAccountCreationOutcome
    unavailable_requires_known_no_commit: bool
    internal_error_requires_known_nonambiguous_failure: bool

    def __init__(
        self,
        contract_version: int,
        relations: tuple[RelationalAccountRelation, ...],
        all_or_nothing_visibility: bool,
        operation_lookup_before_current_policy: bool,
        exact_replay_writes: bool,
        mismatch_outcome: _account_contract.InitialAccountCreationOutcome,
        failure_rolls_back_all: bool,
        event_failure_rolls_back_all: bool,
        exactly_one_initial_event: bool,
        operation_and_aggregate_visible_together: bool,
        pending_record_allowed: bool,
        created_requires_confirmed_commit: bool,
        unknown_commit_outcome: _account_contract.InitialAccountCreationOutcome,
        unavailable_requires_known_no_commit: bool,
        internal_error_requires_known_nonambiguous_failure: bool,
    ) -> None:
        bool_values = (
            all_or_nothing_visibility,
            operation_lookup_before_current_policy,
            exact_replay_writes,
            failure_rolls_back_all,
            event_failure_rolls_back_all,
            exactly_one_initial_event,
            operation_and_aggregate_visible_together,
            pending_record_allowed,
            created_requires_confirmed_commit,
            unavailable_requires_known_no_commit,
            internal_error_requires_known_nonambiguous_failure,
        )
        if (
            type(contract_version) is not int
            or contract_version != 1
            or type(relations) is not tuple
            or any(type(item) is not RelationalAccountRelation for item in relations)
            or len(set(relations)) != len(relations)
            or any(type(item) is not bool for item in bool_values)
            or not all_or_nothing_visibility
            or not operation_lookup_before_current_policy
            or exact_replay_writes
            or mismatch_outcome
            is not _account_contract.InitialAccountCreationOutcome.CONFLICT
            or not failure_rolls_back_all
            or not event_failure_rolls_back_all
            or not exactly_one_initial_event
            or not operation_and_aggregate_visible_together
            or pending_record_allowed
            or not created_requires_confirmed_commit
            or unknown_commit_outcome
            is not _account_contract.InitialAccountCreationOutcome.AMBIGUOUS
            or not unavailable_requires_known_no_commit
            or not internal_error_requires_known_nonambiguous_failure
        ):
            _raise_contract_error()
        _initialize_record(
            self,
            self.__slots__,
            (
                contract_version,
                relations,
                all_or_nothing_visibility,
                operation_lookup_before_current_policy,
                exact_replay_writes,
                mismatch_outcome,
                failure_rolls_back_all,
                event_failure_rolls_back_all,
                exactly_one_initial_event,
                operation_and_aggregate_visible_together,
                pending_record_allowed,
                created_requires_confirmed_commit,
                unknown_commit_outcome,
                unavailable_requires_known_no_commit,
                internal_error_requires_known_nonambiguous_failure,
            ),
        )


class ConsistentReadRequirementManifest(_ImmutableManifestRecord):
    __slots__ = (
        "contract_version",
        "categories",
        "required_facts",
        "forbidden_facts",
        "future_blockers",
    )

    contract_version: int
    categories: tuple[ConsistentReadRequirementCategory, ...]
    required_facts: tuple[str, ...]
    forbidden_facts: tuple[str, ...]
    future_blockers: tuple[str, ...]

    def __init__(
        self,
        contract_version: int,
        categories: tuple[ConsistentReadRequirementCategory, ...],
        required_facts: tuple[str, ...],
        forbidden_facts: tuple[str, ...],
        future_blockers: tuple[str, ...],
    ) -> None:
        if (
            type(contract_version) is not int
            or contract_version != 1
            or type(categories) is not tuple
            or not categories
            or any(
                type(item) is not ConsistentReadRequirementCategory
                for item in categories
            )
            or len(set(categories)) != len(categories)
            or not _exact_string_tuple(required_facts, allow_empty=False)
            or not _exact_string_tuple(forbidden_facts, allow_empty=False)
            or not _exact_string_tuple(future_blockers, allow_empty=False)
            or set(required_facts).intersection(forbidden_facts)
        ):
            _raise_contract_error()
        _initialize_record(
            self,
            self.__slots__,
            (
                contract_version,
                categories,
                required_facts,
                forbidden_facts,
                future_blockers,
            ),
        )


class RelationalRelationManifest(_ImmutableManifestRecord):
    __slots__ = (
        "relation",
        "record_version",
        "fields",
        "primary_key",
        "foreign_keys",
        "unique_constraints",
        "invariants",
        "row_version_required",
        "security_epoch_required",
        "timestamp_fields",
        "initial_creation_requirements",
        "forbidden_fields",
    )

    relation: RelationalAccountRelation
    record_version: int
    fields: tuple[RelationalFieldManifest, ...]
    primary_key: RelationalPrimaryKeyManifest
    foreign_keys: tuple[RelationalForeignKeyManifest, ...]
    unique_constraints: tuple[RelationalUniqueConstraintManifest, ...]
    invariants: tuple[RelationalInvariantManifest, ...]
    row_version_required: bool
    security_epoch_required: bool
    timestamp_fields: tuple[str, ...]
    initial_creation_requirements: tuple[str, ...]
    forbidden_fields: tuple[str, ...]

    def __init__(
        self,
        relation: RelationalAccountRelation,
        record_version: int,
        fields: tuple[RelationalFieldManifest, ...],
        primary_key: RelationalPrimaryKeyManifest,
        foreign_keys: tuple[RelationalForeignKeyManifest, ...],
        unique_constraints: tuple[RelationalUniqueConstraintManifest, ...],
        invariants: tuple[RelationalInvariantManifest, ...],
        row_version_required: bool,
        security_epoch_required: bool,
        timestamp_fields: tuple[str, ...],
        initial_creation_requirements: tuple[str, ...],
        forbidden_fields: tuple[str, ...],
    ) -> None:
        if (
            type(relation) is not RelationalAccountRelation
            or type(record_version) is not int
            or record_version != 1
            or not _exact_record_tuple(
                fields, RelationalFieldManifest, allow_empty=False
            )
            or type(primary_key) is not RelationalPrimaryKeyManifest
            or not _exact_record_tuple(
                foreign_keys, RelationalForeignKeyManifest
            )
            or not _exact_record_tuple(
                unique_constraints, RelationalUniqueConstraintManifest
            )
            or not _exact_record_tuple(invariants, RelationalInvariantManifest)
            or type(row_version_required) is not bool
            or type(security_epoch_required) is not bool
            or not _exact_string_tuple(timestamp_fields)
            or not _exact_string_tuple(
                initial_creation_requirements, allow_empty=False
            )
            or not _exact_string_tuple(forbidden_fields)
        ):
            _raise_contract_error()
        _initialize_record(
            self,
            self.__slots__,
            (
                relation,
                record_version,
                fields,
                primary_key,
                foreign_keys,
                unique_constraints,
                invariants,
                row_version_required,
                security_epoch_required,
                timestamp_fields,
                initial_creation_requirements,
                forbidden_fields,
            ),
        )


class RelationalSchemaManifest(_ImmutableManifestRecord):
    __slots__ = (
        "contract_version",
        "relations",
        "version_requirements",
        "request_snapshot",
        "initial_account_transaction",
        "consistent_read_requirement",
        "migration_stages",
        "missing_authority_fields_receive_defaults",
        "application_rollback_requires_reader_writer_compatibility",
    )

    contract_version: int
    relations: tuple[RelationalRelationManifest, ...]
    version_requirements: tuple[RelationalVersionRequirement, ...]
    request_snapshot: RequestSnapshotManifest
    initial_account_transaction: InitialAccountTransactionManifest
    consistent_read_requirement: ConsistentReadRequirementManifest
    migration_stages: tuple[str, ...]
    missing_authority_fields_receive_defaults: bool
    application_rollback_requires_reader_writer_compatibility: bool

    def __init__(
        self,
        contract_version: int,
        relations: tuple[RelationalRelationManifest, ...],
        version_requirements: tuple[RelationalVersionRequirement, ...],
        request_snapshot: RequestSnapshotManifest,
        initial_account_transaction: InitialAccountTransactionManifest,
        consistent_read_requirement: ConsistentReadRequirementManifest,
        migration_stages: tuple[str, ...],
        missing_authority_fields_receive_defaults: bool,
        application_rollback_requires_reader_writer_compatibility: bool,
    ) -> None:
        if (
            type(contract_version) is not int
            or contract_version != 1
            or not _exact_record_tuple(
                relations, RelationalRelationManifest, allow_empty=False
            )
            or not _exact_record_tuple(
                version_requirements,
                RelationalVersionRequirement,
                allow_empty=False,
            )
            or type(request_snapshot) is not RequestSnapshotManifest
            or type(initial_account_transaction)
            is not InitialAccountTransactionManifest
            or type(consistent_read_requirement)
            is not ConsistentReadRequirementManifest
            or not _exact_string_tuple(migration_stages, allow_empty=False)
            or type(missing_authority_fields_receive_defaults) is not bool
            or missing_authority_fields_receive_defaults
            or type(application_rollback_requires_reader_writer_compatibility)
            is not bool
            or not application_rollback_requires_reader_writer_compatibility
        ):
            _raise_contract_error()
        _initialize_record(
            self,
            self.__slots__,
            (
                contract_version,
                relations,
                version_requirements,
                request_snapshot,
                initial_account_transaction,
                consistent_read_requirement,
                migration_stages,
                missing_authority_fields_receive_defaults,
                application_rollback_requires_reader_writer_compatibility,
            ),
        )


_RECORD_DEFINITION_OPEN = False


_RELATION_ORDER = tuple(RelationalAccountRelation)
_VERSION_ORDER = tuple(RelationalVersionCategory)

_REQUEST_RECORD_TYPES = (
    ("operation_reference", _account_contract.InitialAccountOperationReference),
    ("user", _models.CuevionUser),
    ("verified_email", _models.VerifiedEmail),
    ("authentication_identity", _models.AuthenticationIdentity),
    ("workspace", _models.Workspace),
    ("workspace_membership", _models.WorkspaceMembership),
    (
        "authentication_evidence",
        _account_contract.VerifiedAuthenticationEvidence,
    ),
    ("security_event", _account_contract.InitialSecurityEventRequest),
)


def _exact_slots(record_type: type[object]) -> tuple[str, ...]:
    slots = record_type.__slots__
    if not _exact_string_tuple(slots, allow_empty=False):
        _raise_contract_error()
    return slots


def _snapshot_operation_field_name(prefix: str, leaf: str) -> str:
    if prefix == "operation_reference":
        if leaf == "schema_version":
            return "reference_schema_version"
        return leaf
    if prefix == "authentication_identity" and leaf == "method":
        leaf = "authentication_method"
    return f"snapshot_{prefix}_{leaf}"


def _expected_snapshot_pairs() -> tuple[tuple[str, str], ...]:
    request_fields = _exact_slots(
        _account_contract.InitialAccountCreationRequest
    )
    record_types = dict(_REQUEST_RECORD_TYPES)
    pairs: list[tuple[str, str]] = []
    for request_field in request_fields:
        if request_field == "request_version":
            pairs.append((request_field, request_field))
            continue
        record_type = record_types.get(request_field)
        if record_type is None:
            _raise_contract_error()
        for leaf in _exact_slots(record_type):
            pairs.append(
                (
                    f"{request_field}.{leaf}",
                    _snapshot_operation_field_name(request_field, leaf),
                )
            )
    return tuple(pairs)


_REQUEST_SNAPSHOT = RequestSnapshotManifest(
    1,
    _exact_slots(_account_contract.InitialAccountCreationRequest),
    tuple(
        RequestSnapshotFieldManifest(source_path, operation_field_name)
        for source_path, operation_field_name in _expected_snapshot_pairs()
    ),
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
    "authentication_method",
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
_OPERATION_FIELDS = (
    "operation_record_version",
    "reference_schema_version",
    "derivation_key_epoch",
    "operation_digest",
    "request_snapshot_version",
    "request_version",
    *tuple(
        destination
        for source, destination in _expected_snapshot_pairs()
        if source
        not in (
            "request_version",
            "operation_reference.schema_version",
            "operation_reference.derivation_key_epoch",
            "operation_reference.operation_digest",
        )
    ),
    "receipt_version",
    "receipt_user_id",
    "receipt_verified_email_id",
    "receipt_authentication_identity_id",
    "receipt_workspace_id",
    "receipt_security_event_id",
    "committed_at",
    "row_version",
)
_SECURITY_EVENT_FIELDS = (
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

_EXPECTED_RELATION_FIELDS = (
    _USER_FIELDS,
    _VERIFIED_EMAIL_FIELDS,
    _AUTHENTICATION_IDENTITY_FIELDS,
    _WORKSPACE_FIELDS,
    _WORKSPACE_MEMBERSHIP_FIELDS,
    _OPERATION_FIELDS,
    _SECURITY_EVENT_FIELDS,
)


def _field_manifests(
    names: tuple[str, ...],
    *,
    nullable: tuple[str, ...] = (),
    immutable: tuple[str, ...] = (),
) -> tuple[RelationalFieldManifest, ...]:
    return tuple(
        RelationalFieldManifest(
            name,
            True,
            name in nullable,
            name in immutable,
        )
        for name in names
    )


def _pk(*fields: str) -> RelationalPrimaryKeyManifest:
    return RelationalPrimaryKeyManifest(
        RelationalConstraintCategory.PRIMARY_KEY, tuple(fields)
    )


def _fk(
    fields: tuple[str, ...],
    referenced_relation: RelationalAccountRelation,
    referenced_fields: tuple[str, ...],
    *,
    same_user: bool = False,
) -> RelationalForeignKeyManifest:
    categories = (
        (
            RelationalConstraintCategory.FOREIGN_KEY,
            RelationalConstraintCategory.SAME_USER_REFERENCE,
        )
        if same_user
        else (RelationalConstraintCategory.FOREIGN_KEY,)
    )
    return RelationalForeignKeyManifest(
        categories, fields, referenced_relation, referenced_fields
    )


def _unique(
    category: RelationalConstraintCategory,
    fields: tuple[str, ...],
    scope_fields: tuple[str, ...] = (),
) -> RelationalUniqueConstraintManifest:
    return RelationalUniqueConstraintManifest(category, fields, scope_fields)


def _invariant(
    category: RelationalConstraintCategory, *fields: str
) -> RelationalInvariantManifest:
    return RelationalInvariantManifest(category, tuple(fields))


_COMMON_FORBIDDEN_FIELDS = (
    "account_type",
    "product",
    "package",
    "bundle",
    "entitlement",
    "billing",
    "subscription",
    "seats",
    "session",
    "cookie",
)

_FORBIDDEN_FIELD_FRAGMENTS = (
    "raw_operation_key",
    "access_token",
    "refresh_token",
    "id_token",
    "provider_payload",
    "client_secret",
    "challenge_secret",
    "pkce_verifier",
    "secret",
    *_COMMON_FORBIDDEN_FIELDS,
)


def _identifier_tokens(value: str) -> tuple[str, ...]:
    normalized = "".join(
        character
        if ("a" <= character <= "z" or "0" <= character <= "9")
        else "_"
        for character in value.casefold()
    )
    return tuple(token for token in normalized.split("_") if token)


def _contains_forbidden_field_fragment(field_name: str) -> bool:
    field_tokens = _identifier_tokens(field_name)
    for fragment in _FORBIDDEN_FIELD_FRAGMENTS:
        fragment_tokens = _identifier_tokens(fragment)
        width = len(fragment_tokens)
        if any(
            field_tokens[index : index + width] == fragment_tokens
            for index in range(len(field_tokens) - width + 1)
        ):
            return True
    return False


_USERS = RelationalRelationManifest(
    RelationalAccountRelation.USERS,
    1,
    _field_manifests(
        _USER_FIELDS,
        nullable=("primary_verified_email_id",),
        immutable=("schema_version", "user_id", "created_at"),
    ),
    _pk("user_id"),
    (
        _fk(
            ("primary_verified_email_id", "user_id"),
            RelationalAccountRelation.VERIFIED_EMAILS,
            ("email_id", "user_id"),
            same_user=True,
        ),
    ),
    (),
    (
        _invariant(RelationalConstraintCategory.CANONICAL_IDENTIFIER, "user_id"),
        _invariant(
            RelationalConstraintCategory.POSITIVE_VERSION,
            "schema_version",
            "row_version",
        ),
        _invariant(
            RelationalConstraintCategory.POSITIVE_SECURITY_EPOCH,
            "security_epoch",
        ),
        _invariant(RelationalConstraintCategory.VALID_STATUS, "status"),
        _invariant(
            RelationalConstraintCategory.TIMESTAMP_ORDER,
            "created_at",
            "updated_at",
        ),
        _invariant(
            RelationalConstraintCategory.SAME_USER_REFERENCE,
            "primary_verified_email_id",
            "user_id",
        ),
        _invariant(
            RelationalConstraintCategory.IMMUTABLE_VALUE,
            "schema_version",
            "user_id",
            "created_at",
        ),
    ),
    True,
    True,
    ("created_at", "updated_at"),
    (
        "status_active",
        "primary_verified_email_id_present",
        "primary_email_owned_by_same_user",
        "security_epoch_exactly_one",
        "row_version_exactly_one",
    ),
    _COMMON_FORBIDDEN_FIELDS,
)


_VERIFIED_EMAILS = RelationalRelationManifest(
    RelationalAccountRelation.VERIFIED_EMAILS,
    1,
    _field_manifests(
        _VERIFIED_EMAIL_FIELDS,
        nullable=("verified_at", "retired_at"),
        immutable=(
            "schema_version",
            "email_id",
            "user_id",
            "canonical_email",
            "verification_source",
            "created_at",
            "verified_at",
        ),
    ),
    _pk("email_id"),
    (
        _fk(
            ("user_id",),
            RelationalAccountRelation.USERS,
            ("user_id",),
        ),
    ),
    (
        _unique(
            RelationalConstraintCategory.UNIQUE_AUTHORITY_CLAIM,
            ("canonical_email",),
            ("status", "retired_at"),
        ),
    ),
    (
        _invariant(
            RelationalConstraintCategory.CANONICAL_IDENTIFIER, "email_id"
        ),
        _invariant(
            RelationalConstraintCategory.POSITIVE_VERSION,
            "schema_version",
            "row_version",
        ),
        _invariant(RelationalConstraintCategory.VALID_STATUS, "status"),
        _invariant(
            RelationalConstraintCategory.TIMESTAMP_ORDER,
            "created_at",
            "verified_at",
            "retired_at",
        ),
        _invariant(
            RelationalConstraintCategory.IMMUTABLE_VALUE,
            "schema_version",
            "email_id",
            "user_id",
            "canonical_email",
            "verification_source",
            "created_at",
            "verified_at",
        ),
    ),
    True,
    False,
    ("created_at", "verified_at", "retired_at"),
    (
        "status_verified",
        "verified_at_present",
        "retired_at_absent",
        "is_primary_for_initial_user",
        "historical_ownership_immutable",
        "row_version_exactly_one",
        "retired_email_reuse_policy_deferred",
        "email_text_never_links_accounts",
    ),
    (*_COMMON_FORBIDDEN_FIELDS, "automatic_account_link", "provider_payload"),
)


_AUTHENTICATION_IDENTITIES = RelationalRelationManifest(
    RelationalAccountRelation.AUTHENTICATION_IDENTITIES,
    1,
    _field_manifests(
        _AUTHENTICATION_IDENTITY_FIELDS,
        nullable=("verified_email_id", "last_used_at"),
        immutable=(
            "schema_version",
            "identity_id",
            "user_id",
            "issuer",
            "subject",
            "authentication_method",
            "created_at",
        ),
    ),
    _pk("identity_id"),
    (
        _fk(
            ("user_id",),
            RelationalAccountRelation.USERS,
            ("user_id",),
        ),
        _fk(
            ("verified_email_id", "user_id"),
            RelationalAccountRelation.VERIFIED_EMAILS,
            ("email_id", "user_id"),
            same_user=True,
        ),
    ),
    (
        _unique(
            RelationalConstraintCategory.UNIQUE_AUTHORITY_CLAIM,
            ("issuer", "subject"),
        ),
    ),
    (
        _invariant(
            RelationalConstraintCategory.CANONICAL_IDENTIFIER, "identity_id"
        ),
        _invariant(
            RelationalConstraintCategory.POSITIVE_VERSION,
            "schema_version",
            "row_version",
        ),
        _invariant(RelationalConstraintCategory.VALID_STATUS, "status"),
        _invariant(
            RelationalConstraintCategory.VALID_AUTHENTICATION_METHOD,
            "authentication_method",
        ),
        _invariant(
            RelationalConstraintCategory.EXACT_CASE_SENSITIVE_VALUE,
            "issuer",
            "subject",
        ),
        _invariant(
            RelationalConstraintCategory.TIMESTAMP_ORDER,
            "created_at",
            "last_used_at",
        ),
        _invariant(
            RelationalConstraintCategory.SAME_USER_REFERENCE,
            "verified_email_id",
            "user_id",
        ),
        _invariant(
            RelationalConstraintCategory.IMMUTABLE_VALUE,
            "schema_version",
            "identity_id",
            "user_id",
            "issuer",
            "subject",
            "authentication_method",
            "created_at",
        ),
    ),
    True,
    False,
    ("created_at", "last_used_at"),
    (
        "status_active",
        "verified_email_id_present",
        "verified_email_owned_by_same_user",
        "issuer_normalized_before_storage",
        "subject_opaque_case_sensitive",
        "row_version_exactly_one",
    ),
    (
        *_COMMON_FORBIDDEN_FIELDS,
        "access_token",
        "refresh_token",
        "id_token",
        "provider_payload",
    ),
)


_WORKSPACES = RelationalRelationManifest(
    RelationalAccountRelation.WORKSPACES,
    1,
    _field_manifests(
        _WORKSPACE_FIELDS,
        immutable=(
            "schema_version",
            "workspace_id",
            "created_by_user_id",
            "created_at",
        ),
    ),
    _pk("workspace_id"),
    (
        _fk(
            ("created_by_user_id",),
            RelationalAccountRelation.USERS,
            ("user_id",),
        ),
    ),
    (),
    (
        _invariant(
            RelationalConstraintCategory.CANONICAL_IDENTIFIER, "workspace_id"
        ),
        _invariant(
            RelationalConstraintCategory.POSITIVE_VERSION,
            "schema_version",
            "row_version",
        ),
        _invariant(RelationalConstraintCategory.VALID_STATUS, "status"),
        _invariant(
            RelationalConstraintCategory.TIMESTAMP_ORDER,
            "created_at",
            "updated_at",
        ),
        _invariant(
            RelationalConstraintCategory.IMMUTABLE_VALUE,
            "schema_version",
            "workspace_id",
            "created_by_user_id",
            "created_at",
        ),
    ),
    True,
    False,
    ("created_at", "updated_at"),
    (
        "status_active",
        "creator_is_provenance_not_current_authority",
        "workspace_id_independent_of_email",
        "row_version_exactly_one",
    ),
    _COMMON_FORBIDDEN_FIELDS,
)


_WORKSPACE_MEMBERSHIPS = RelationalRelationManifest(
    RelationalAccountRelation.WORKSPACE_MEMBERSHIPS,
    1,
    _field_manifests(
        _WORKSPACE_MEMBERSHIP_FIELDS,
        immutable=("schema_version", "workspace_id", "user_id", "created_at"),
    ),
    _pk("workspace_id", "user_id"),
    (
        _fk(
            ("workspace_id",),
            RelationalAccountRelation.WORKSPACES,
            ("workspace_id",),
        ),
        _fk(
            ("user_id",),
            RelationalAccountRelation.USERS,
            ("user_id",),
        ),
    ),
    (),
    (
        _invariant(
            RelationalConstraintCategory.CANONICAL_IDENTIFIER,
            "workspace_id",
            "user_id",
        ),
        _invariant(
            RelationalConstraintCategory.POSITIVE_VERSION,
            "schema_version",
            "row_version",
        ),
        _invariant(RelationalConstraintCategory.VALID_ROLE, "role"),
        _invariant(RelationalConstraintCategory.VALID_STATUS, "status"),
        _invariant(
            RelationalConstraintCategory.TIMESTAMP_ORDER,
            "created_at",
            "updated_at",
        ),
        _invariant(
            RelationalConstraintCategory.IMMUTABLE_VALUE,
            "schema_version",
            "workspace_id",
            "user_id",
            "created_at",
        ),
    ),
    True,
    False,
    ("created_at", "updated_at"),
    (
        "status_active",
        "role_owner",
        "role_and_status_are_current_authority",
        "last_owner_protection_deferred_to_mutation_repository",
        "row_version_exactly_one",
    ),
    _COMMON_FORBIDDEN_FIELDS,
)


_OPERATION_IMMUTABLE_FIELDS = _OPERATION_FIELDS
_INITIAL_ACCOUNT_OPERATIONS = RelationalRelationManifest(
    RelationalAccountRelation.INITIAL_ACCOUNT_OPERATIONS,
    1,
    _field_manifests(
        _OPERATION_FIELDS,
        nullable=(
            "snapshot_verified_email_retired_at",
            "snapshot_authentication_identity_last_used_at",
        ),
        immutable=_OPERATION_IMMUTABLE_FIELDS,
    ),
    _pk("reference_schema_version", "derivation_key_epoch", "operation_digest"),
    (
        _fk(
            ("receipt_user_id",),
            RelationalAccountRelation.USERS,
            ("user_id",),
        ),
        _fk(
            ("receipt_verified_email_id", "receipt_user_id"),
            RelationalAccountRelation.VERIFIED_EMAILS,
            ("email_id", "user_id"),
            same_user=True,
        ),
        _fk(
            ("receipt_authentication_identity_id", "receipt_user_id"),
            RelationalAccountRelation.AUTHENTICATION_IDENTITIES,
            ("identity_id", "user_id"),
            same_user=True,
        ),
        _fk(
            ("receipt_workspace_id", "receipt_user_id"),
            RelationalAccountRelation.WORKSPACES,
            ("workspace_id", "created_by_user_id"),
            same_user=True,
        ),
        _fk(
            ("receipt_workspace_id", "receipt_user_id"),
            RelationalAccountRelation.WORKSPACE_MEMBERSHIPS,
            ("workspace_id", "user_id"),
            same_user=True,
        ),
        _fk(
            ("receipt_security_event_id",),
            RelationalAccountRelation.SECURITY_EVENTS,
            ("event_id",),
        ),
    ),
    (
        _unique(
            RelationalConstraintCategory.UNIQUE_OPERATION_REFERENCE,
            ("reference_schema_version", "derivation_key_epoch", "operation_digest"),
        ),
        _unique(
            RelationalConstraintCategory.UNIQUE_EVIDENCE_ASSERTION,
            (
                "snapshot_authentication_evidence_trust_domain",
                "snapshot_authentication_evidence_verification_coordinator_id",
                "snapshot_authentication_evidence_assertion_id",
            ),
        ),
        _unique(
            RelationalConstraintCategory.UNIQUE_INITIAL_OPERATION_EVENT,
            ("receipt_security_event_id",),
        ),
    ),
    (
        _invariant(
            RelationalConstraintCategory.POSITIVE_VERSION,
            "operation_record_version",
            "reference_schema_version",
            "derivation_key_epoch",
            "request_snapshot_version",
            "request_version",
            "receipt_version",
            "row_version",
        ),
        _invariant(
            RelationalConstraintCategory.CANONICAL_DIGEST,
            "operation_digest",
            "snapshot_authentication_evidence_assertion_id",
        ),
        _invariant(
            RelationalConstraintCategory.CANONICAL_IDENTIFIER,
            "receipt_user_id",
            "receipt_verified_email_id",
            "receipt_authentication_identity_id",
            "receipt_workspace_id",
            "receipt_security_event_id",
        ),
        _invariant(
            RelationalConstraintCategory.EXACT_CASE_SENSITIVE_VALUE,
            "snapshot_authentication_identity_subject",
            "snapshot_authentication_evidence_subject",
        ),
        _invariant(
            RelationalConstraintCategory.TIMESTAMP_ORDER,
            "snapshot_authentication_evidence_verified_at",
            "snapshot_authentication_evidence_issued_at",
            "snapshot_authentication_evidence_expires_at",
        ),
        _invariant(
            RelationalConstraintCategory.EXACT_FIELD_EQUALITY,
            "receipt_user_id",
            "snapshot_user_user_id",
        ),
        _invariant(
            RelationalConstraintCategory.EXACT_FIELD_EQUALITY,
            "receipt_verified_email_id",
            "snapshot_verified_email_email_id",
        ),
        _invariant(
            RelationalConstraintCategory.EXACT_FIELD_EQUALITY,
            "receipt_authentication_identity_id",
            "snapshot_authentication_identity_identity_id",
        ),
        _invariant(
            RelationalConstraintCategory.EXACT_FIELD_EQUALITY,
            "receipt_workspace_id",
            "snapshot_workspace_workspace_id",
        ),
        _invariant(
            RelationalConstraintCategory.EXACT_FIELD_EQUALITY,
            "receipt_security_event_id",
            "snapshot_security_event_event_id",
        ),
        _invariant(
            RelationalConstraintCategory.SAME_USER_REFERENCE,
            "receipt_user_id",
            "receipt_verified_email_id",
            "receipt_authentication_identity_id",
            "receipt_workspace_id",
        ),
        _invariant(
            RelationalConstraintCategory.IMMUTABLE_VALUE,
            *_OPERATION_IMMUTABLE_FIELDS,
        ),
        _invariant(
            RelationalConstraintCategory.APPEND_ONLY,
            *_OPERATION_IMMUTABLE_FIELDS,
        ),
    ),
    True,
    False,
    (
        "snapshot_user_created_at",
        "snapshot_user_updated_at",
        "snapshot_verified_email_created_at",
        "snapshot_verified_email_verified_at",
        "snapshot_verified_email_retired_at",
        "snapshot_authentication_identity_created_at",
        "snapshot_authentication_identity_last_used_at",
        "snapshot_workspace_created_at",
        "snapshot_workspace_updated_at",
        "snapshot_workspace_membership_created_at",
        "snapshot_workspace_membership_updated_at",
        "snapshot_authentication_evidence_verified_at",
        "snapshot_authentication_evidence_issued_at",
        "snapshot_authentication_evidence_expires_at",
        "committed_at",
    ),
    (
        "complete_lossless_request_snapshot",
        "all_caller_controlled_persisted_fields_present",
        "receipt_is_committed_creation_result",
        "aggregate_and_operation_visible_in_same_transaction",
        "no_separate_pending_record",
        "row_version_exactly_one",
    ),
    (
        *_COMMON_FORBIDDEN_FIELDS,
        "raw_operation_key",
        "pending",
        "serialization_format",
        "storage_encoding",
        "access_token",
        "refresh_token",
        "id_token",
        "provider_payload",
        "secret",
    ),
)


_SECURITY_EVENTS = RelationalRelationManifest(
    RelationalAccountRelation.SECURITY_EVENTS,
    1,
    _field_manifests(
        _SECURITY_EVENT_FIELDS,
        immutable=_SECURITY_EVENT_FIELDS,
    ),
    _pk("event_id"),
    (
        _fk(
            (
                "reference_schema_version",
                "derivation_key_epoch",
                "operation_digest",
                "event_payload_version",
                "event_id",
                "event_type",
                "actor_trust_domain",
                "actor_verification_coordinator_id",
                "user_id",
                "verified_email_id",
                "authentication_identity_id",
                "workspace_id",
                "membership_workspace_id",
                "membership_user_id",
                "security_epoch",
            ),
            RelationalAccountRelation.INITIAL_ACCOUNT_OPERATIONS,
            (
                "reference_schema_version",
                "derivation_key_epoch",
                "operation_digest",
                # The request event schema is the durable event payload version.
                "snapshot_security_event_schema_version",
                "snapshot_security_event_event_id",
                "snapshot_security_event_event_type",
                "snapshot_authentication_evidence_trust_domain",
                "snapshot_authentication_evidence_verification_coordinator_id",
                "snapshot_user_user_id",
                "snapshot_verified_email_email_id",
                "snapshot_authentication_identity_identity_id",
                "snapshot_workspace_workspace_id",
                "snapshot_workspace_membership_workspace_id",
                "snapshot_workspace_membership_user_id",
                "snapshot_user_security_epoch",
            ),
            same_user=True,
        ),
        _fk(("user_id",), RelationalAccountRelation.USERS, ("user_id",)),
        _fk(
            ("verified_email_id", "user_id"),
            RelationalAccountRelation.VERIFIED_EMAILS,
            ("email_id", "user_id"),
            same_user=True,
        ),
        _fk(
            ("authentication_identity_id", "user_id"),
            RelationalAccountRelation.AUTHENTICATION_IDENTITIES,
            ("identity_id", "user_id"),
            same_user=True,
        ),
        _fk(
            ("workspace_id", "user_id"),
            RelationalAccountRelation.WORKSPACES,
            ("workspace_id", "created_by_user_id"),
            same_user=True,
        ),
        _fk(
            ("membership_workspace_id", "membership_user_id"),
            RelationalAccountRelation.WORKSPACE_MEMBERSHIPS,
            ("workspace_id", "user_id"),
            same_user=True,
        ),
    ),
    (
        _unique(
            RelationalConstraintCategory.UNIQUE_INITIAL_OPERATION_EVENT,
            ("reference_schema_version", "derivation_key_epoch", "operation_digest"),
        ),
        _unique(
            RelationalConstraintCategory.UNIQUE_EVENT_STREAM_POSITION,
            ("event_stream_name", "event_stream_position"),
        ),
    ),
    (
        _invariant(
            RelationalConstraintCategory.CANONICAL_IDENTIFIER,
            "event_id",
            "user_id",
            "verified_email_id",
            "authentication_identity_id",
            "workspace_id",
        ),
        _invariant(
            RelationalConstraintCategory.CANONICAL_DIGEST, "operation_digest"
        ),
        _invariant(
            RelationalConstraintCategory.POSITIVE_VERSION,
            "event_record_version",
            "event_payload_version",
            "reference_schema_version",
            "derivation_key_epoch",
            "event_stream_position",
            "row_version",
        ),
        _invariant(
            RelationalConstraintCategory.POSITIVE_SECURITY_EPOCH,
            "security_epoch",
        ),
        _invariant(RelationalConstraintCategory.VALID_EVENT_TYPE, "event_type"),
        _invariant(
            RelationalConstraintCategory.EXACT_CASE_SENSITIVE_VALUE,
            "event_stream_name",
        ),
        _invariant(
            RelationalConstraintCategory.TIMESTAMP_ORDER,
            "event_at",
            "recorded_at",
        ),
        _invariant(
            RelationalConstraintCategory.SAME_USER_REFERENCE,
            "user_id",
            "verified_email_id",
            "authentication_identity_id",
            "workspace_id",
            "membership_workspace_id",
            "membership_user_id",
        ),
        _invariant(
            RelationalConstraintCategory.IMMUTABLE_VALUE,
            *_SECURITY_EVENT_FIELDS,
        ),
        _invariant(
            RelationalConstraintCategory.APPEND_ONLY,
            *_SECURITY_EVENT_FIELDS,
        ),
    ),
    True,
    True,
    ("event_at", "recorded_at"),
    (
        "event_type_initial_account_created",
        "exactly_one_event_per_initial_operation",
        "event_id_from_validated_request",
        "event_time_repository_generated",
        "append_position_repository_generated",
        "event_stream_name_repository_generated",
        f"event_stream_name_exact_{ACCOUNT_SECURITY_EVENT_STREAM_NAME}",
        "commit_metadata_repository_generated",
        "row_version_exactly_one",
    ),
    (
        *_COMMON_FORBIDDEN_FIELDS,
        "raw_operation_key",
        "raw_evidence",
        "access_token",
        "refresh_token",
        "id_token",
        "provider_payload",
        "secret",
    ),
)


_VERSION_REQUIREMENTS = tuple(
    RelationalVersionRequirement(category, 1, True, False)
    for category in _VERSION_ORDER
)


_INITIAL_ACCOUNT_TRANSACTION = InitialAccountTransactionManifest(
    1,
    _RELATION_ORDER,
    True,
    True,
    False,
    _account_contract.InitialAccountCreationOutcome.CONFLICT,
    True,
    True,
    True,
    True,
    False,
    True,
    _account_contract.InitialAccountCreationOutcome.AMBIGUOUS,
    True,
    True,
)


_CONSISTENT_READ = ConsistentReadRequirementManifest(
    1,
    tuple(ConsistentReadRequirementCategory),
    (
        "stored_session",
        "user",
        "authentication_identity",
        "primary_verified_email",
        "stored_session.status",
        "user.status",
        "authentication_identity.status",
        "primary_verified_email.status",
        "stored_session.security_epoch",
        "user.security_epoch",
        "stored_session.schema_version",
        "stored_session.row_version",
        "user.schema_version",
        "user.row_version",
        "authentication_identity.schema_version",
        "authentication_identity.row_version",
        "primary_verified_email.schema_version",
        "primary_verified_email.row_version",
        "stored_session.credential_lookup_digest",
        "stored_session.credential_binding_digest",
        "stored_session.credential_epoch",
        "stored_session.lookup_key_epoch",
        "stored_session.binding_key_epoch",
        "stored_session.authenticated_at",
        "stored_session.issued_at",
        "stored_session.last_used_at",
        "stored_session.idle_expires_at",
        "stored_session.absolute_expires_at",
        "stored_session.revoked_at",
        "stored_session.revocation_reason",
    ),
    (
        "workspace",
        "workspace_membership",
        "role",
        "product",
        "entitlement",
        "billing",
        "subscription",
        "seats",
    ),
    (
        "stored_session_missing_lookup_key_epoch",
        "stored_session_missing_binding_key_epoch",
        "session_contract_not_modified_by_this_slice",
    ),
)


RELATIONAL_ACCOUNT_SCHEMA_1 = RelationalSchemaManifest(
    1,
    (
        _USERS,
        _VERIFIED_EMAILS,
        _AUTHENTICATION_IDENTITIES,
        _WORKSPACES,
        _WORKSPACE_MEMBERSHIPS,
        _INITIAL_ACCOUNT_OPERATIONS,
        _SECURITY_EVENTS,
    ),
    _VERSION_REQUIREMENTS,
    _REQUEST_SNAPSHOT,
    _INITIAL_ACCOUNT_TRANSACTION,
    _CONSISTENT_READ,
    ("expand", "migrate", "verify", "contract"),
    False,
    True,
)


class _ControlledManifestInvalidity(Exception):
    __slots__ = ()

    def __init__(self) -> None:
        Exception.__init__(self)


_MISSING_MANIFEST_DESCRIPTOR = object()
_MANIFEST_SLOT_DESCRIPTOR_TYPE = type(RelationalFieldManifest.name)


def _raise_controlled_manifest_invalidity() -> None:
    raise _ControlledManifestInvalidity()


def _manifest_attribute(
    record: object, expected_type: type[object], name: str
) -> object:
    if type(record) is not expected_type:
        _raise_controlled_manifest_invalidity()
    descriptor = vars(expected_type).get(name, _MISSING_MANIFEST_DESCRIPTOR)
    if type(descriptor) is not _MANIFEST_SLOT_DESCRIPTOR_TYPE:
        return object.__getattribute__(record, name)
    try:
        return descriptor.__get__(record, expected_type)
    except AttributeError:
        _raise_controlled_manifest_invalidity()


def _record_signature(
    record: object, expected_type: type[object]
) -> tuple[object, ...]:
    if type(record) is not expected_type:
        _raise_controlled_manifest_invalidity()
    return tuple(
        _manifest_attribute(record, expected_type, name)
        for name in expected_type.__slots__
    )


def _exact_tuple_attribute(
    record: object, expected_type: type[object], name: str
) -> tuple[object, ...]:
    value = _manifest_attribute(record, expected_type, name)
    if type(value) is not tuple:
        _raise_controlled_manifest_invalidity()
    return value


def _schema_signature(manifest: object) -> tuple[object, ...]:
    if type(manifest) is not RelationalSchemaManifest:
        _raise_controlled_manifest_invalidity()

    relation_signatures = []
    relations = _exact_tuple_attribute(
        manifest, RelationalSchemaManifest, "relations"
    )
    for relation in relations:
        if type(relation) is not RelationalRelationManifest:
            _raise_controlled_manifest_invalidity()
        relation_fields = _exact_tuple_attribute(
            relation, RelationalRelationManifest, "fields"
        )
        relation_foreign_keys = _exact_tuple_attribute(
            relation, RelationalRelationManifest, "foreign_keys"
        )
        relation_unique_constraints = _exact_tuple_attribute(
            relation, RelationalRelationManifest, "unique_constraints"
        )
        relation_invariants = _exact_tuple_attribute(
            relation, RelationalRelationManifest, "invariants"
        )
        fields = tuple(
            _record_signature(item, RelationalFieldManifest)
            for item in relation_fields
        )
        primary_key = _record_signature(
            _manifest_attribute(
                relation, RelationalRelationManifest, "primary_key"
            ),
            RelationalPrimaryKeyManifest,
        )
        foreign_keys = tuple(
            _record_signature(item, RelationalForeignKeyManifest)
            for item in relation_foreign_keys
        )
        unique_constraints = tuple(
            _record_signature(item, RelationalUniqueConstraintManifest)
            for item in relation_unique_constraints
        )
        invariants = tuple(
            _record_signature(item, RelationalInvariantManifest)
            for item in relation_invariants
        )
        relation_signatures.append(
            (
                _manifest_attribute(
                    relation, RelationalRelationManifest, "relation"
                ),
                _manifest_attribute(
                    relation, RelationalRelationManifest, "record_version"
                ),
                fields,
                primary_key,
                foreign_keys,
                unique_constraints,
                invariants,
                _manifest_attribute(
                    relation,
                    RelationalRelationManifest,
                    "row_version_required",
                ),
                _manifest_attribute(
                    relation,
                    RelationalRelationManifest,
                    "security_epoch_required",
                ),
                _manifest_attribute(
                    relation, RelationalRelationManifest, "timestamp_fields"
                ),
                _manifest_attribute(
                    relation,
                    RelationalRelationManifest,
                    "initial_creation_requirements",
                ),
                _manifest_attribute(
                    relation, RelationalRelationManifest, "forbidden_fields"
                ),
            )
        )

    version_requirements = _exact_tuple_attribute(
        manifest, RelationalSchemaManifest, "version_requirements"
    )
    versions = tuple(
        _record_signature(item, RelationalVersionRequirement)
        for item in version_requirements
    )
    snapshot = _manifest_attribute(
        manifest, RelationalSchemaManifest, "request_snapshot"
    )
    if type(snapshot) is not RequestSnapshotManifest:
        _raise_controlled_manifest_invalidity()
    snapshot_fields = _exact_tuple_attribute(
        snapshot, RequestSnapshotManifest, "fields"
    )
    snapshot_signature = (
        _manifest_attribute(snapshot, RequestSnapshotManifest, "version"),
        _manifest_attribute(
            snapshot, RequestSnapshotManifest, "request_field_names"
        ),
        tuple(
            _record_signature(item, RequestSnapshotFieldManifest)
            for item in snapshot_fields
        ),
    )
    transaction = _record_signature(
        _manifest_attribute(
            manifest,
            RelationalSchemaManifest,
            "initial_account_transaction",
        ),
        InitialAccountTransactionManifest,
    )
    consistent_read = _record_signature(
        _manifest_attribute(
            manifest,
            RelationalSchemaManifest,
            "consistent_read_requirement",
        ),
        ConsistentReadRequirementManifest,
    )
    return (
        _manifest_attribute(
            manifest, RelationalSchemaManifest, "contract_version"
        ),
        tuple(relation_signatures),
        versions,
        snapshot_signature,
        transaction,
        consistent_read,
        _manifest_attribute(
            manifest, RelationalSchemaManifest, "migration_stages"
        ),
        _manifest_attribute(
            manifest,
            RelationalSchemaManifest,
            "missing_authority_fields_receive_defaults",
        ),
        _manifest_attribute(
            manifest,
            RelationalSchemaManifest,
            "application_rollback_requires_reader_writer_compatibility",
        ),
    )


_EXPECTED_SCHEMA_SIGNATURE = _schema_signature(RELATIONAL_ACCOUNT_SCHEMA_1)


def _safe_exact_value_matches(value: object, expected: object) -> bool:
    """Compare a signature only after closing every caller-controlled type."""

    if type(value) is not type(expected):
        return False
    if type(expected) is tuple:
        if len(value) != len(expected):
            return False
        return all(
            _safe_exact_value_matches(value[index], expected[index])
            for index in range(len(expected))
        )
    if isinstance(expected, _Enum):
        return value is expected
    if type(expected) is bool:
        return value is expected
    if type(expected) is int or type(expected) is str:
        return value == expected
    if expected is None:
        return value is None
    return False


def request_snapshot_covers_initial_account_request(
    snapshot: RequestSnapshotManifest,
) -> bool:
    """Return whether one manifest losslessly covers the current request fields."""

    try:
        if type(snapshot) is not RequestSnapshotManifest:
            return False
        version = _manifest_attribute(
            snapshot, RequestSnapshotManifest, "version"
        )
        request_field_names = _manifest_attribute(
            snapshot, RequestSnapshotManifest, "request_field_names"
        )
        fields = _manifest_attribute(
            snapshot, RequestSnapshotManifest, "fields"
        )
        if (
            type(version) is not int
            or version != 1
            or not _exact_string_tuple(
                request_field_names, allow_empty=False
            )
            or type(fields) is not tuple
            or any(type(item) is not RequestSnapshotFieldManifest for item in fields)
        ):
            return False
        pairs = []
        for item in fields:
            source_path = _manifest_attribute(
                item, RequestSnapshotFieldManifest, "source_path"
            )
            operation_field_name = _manifest_attribute(
                item,
                RequestSnapshotFieldManifest,
                "operation_field_name",
            )
            if (
                type(source_path) is not str
                or not source_path
                or type(operation_field_name) is not str
                or not operation_field_name
            ):
                return False
            pairs.append((source_path, operation_field_name))
        actual_pairs = tuple(pairs)
        expected_request_fields = _exact_slots(
            _account_contract.InitialAccountCreationRequest
        )
        expected_pairs = _expected_snapshot_pairs()
        return (
            _safe_exact_value_matches(
                request_field_names, expected_request_fields
            )
            and _safe_exact_value_matches(actual_pairs, expected_pairs)
            and len({source for source, _destination in actual_pairs})
            == len(actual_pairs)
            and len({destination for _source, destination in actual_pairs})
            == len(actual_pairs)
        )
    except _ControlledManifestInvalidity as error:
        if type(error) is not _ControlledManifestInvalidity:
            raise
        return False


def relational_version_is_supported(
    category: RelationalVersionCategory, version: int
) -> bool:
    """Return whether one exact version is supported by this closed contract."""

    return (
        type(category) is RelationalVersionCategory
        and type(version) is int
        and version == 1
        and any(category is item for item in _VERSION_ORDER)
    )


def relational_schema_manifest_is_valid(
    manifest: RelationalSchemaManifest,
) -> bool:
    """Return whether a manifest is exactly the complete schema-one contract."""

    try:
        signature = _schema_signature(manifest)
        if not _safe_exact_value_matches(
            signature, _EXPECTED_SCHEMA_SIGNATURE
        ):
            return False
        if not _exact_string_tuple(
            _FORBIDDEN_FIELD_FRAGMENTS, allow_empty=False
        ):
            return False
        relations = object.__getattribute__(manifest, "relations")
        if tuple(
            object.__getattribute__(relation, "relation")
            for relation in relations
        ) != _RELATION_ORDER:
            return False
        for relation, expected_fields in zip(
            relations, _EXPECTED_RELATION_FIELDS, strict=True
        ):
            fields = object.__getattribute__(relation, "fields")
            field_names = tuple(
                object.__getattribute__(field, "name") for field in fields
            )
            if field_names != expected_fields:
                return False
            primary_key = object.__getattribute__(relation, "primary_key")
            primary_fields = object.__getattribute__(primary_key, "field_names")
            for field in fields:
                name = object.__getattribute__(field, "name")
                if (
                    type(name) is not str
                    or object.__getattribute__(field, "required") is not True
                    or type(object.__getattribute__(field, "nullable")) is not bool
                    or type(object.__getattribute__(field, "immutable")) is not bool
                    or (
                        name in primary_fields
                        and (
                            object.__getattribute__(field, "nullable") is not False
                            or object.__getattribute__(field, "immutable") is not True
                        )
                    )
                ):
                    return False
            for foreign_key in object.__getattribute__(relation, "foreign_keys"):
                categories = object.__getattribute__(
                    foreign_key, "categories"
                )
                local_fields = object.__getattribute__(foreign_key, "field_names")
                referenced_relation = object.__getattribute__(
                    foreign_key, "referenced_relation"
                )
                referenced_fields = object.__getattribute__(
                    foreign_key, "referenced_field_names"
                )
                if (
                    type(categories) is not tuple
                    or not categories
                    or any(
                        type(category) is not RelationalConstraintCategory
                        for category in categories
                    )
                    or not _exact_string_tuple(
                        local_fields, allow_empty=False
                    )
                    or type(referenced_relation)
                    is not RelationalAccountRelation
                    or not _exact_string_tuple(
                        referenced_fields, allow_empty=False
                    )
                    or len(local_fields) != len(referenced_fields)
                    or any(field not in field_names for field in local_fields)
                ):
                    return False
                target = next(
                    (
                        item
                        for item in relations
                        if object.__getattribute__(item, "relation")
                        is referenced_relation
                    ),
                    None,
                )
                if target is None:
                    return False
                target_fields = tuple(
                    object.__getattribute__(field, "name")
                    for field in object.__getattribute__(target, "fields")
                )
                if any(field not in target_fields for field in referenced_fields):
                    return False
            for invariant in object.__getattribute__(relation, "invariants"):
                category = object.__getattribute__(invariant, "category")
                invariant_fields = object.__getattribute__(
                    invariant, "field_names"
                )
                if (
                    type(category) is not RelationalConstraintCategory
                    or not _exact_string_tuple(invariant_fields)
                    or (
                        category
                        is RelationalConstraintCategory.EXACT_FIELD_EQUALITY
                        and len(invariant_fields) != 2
                    )
                    or any(field not in field_names for field in invariant_fields)
                ):
                    return False
            forbidden = object.__getattribute__(relation, "forbidden_fields")
            if not _exact_string_tuple(forbidden):
                return False
            if set(field_names).intersection(forbidden):
                return False
            if any(
                _contains_forbidden_field_fragment(field_name)
                for field_name in field_names
            ):
                return False
        versions = object.__getattribute__(manifest, "version_requirements")
        if tuple(
            object.__getattribute__(item, "category") for item in versions
        ) != _VERSION_ORDER:
            return False
        if not all(
            relational_version_is_supported(
                object.__getattribute__(item, "category"),
                object.__getattribute__(item, "supported_version"),
            )
            and object.__getattribute__(item, "unknown_newer_fails_closed")
            is True
            and object.__getattribute__(
                item, "implicit_authority_defaults_allowed"
            )
            is False
            for item in versions
        ):
            return False
        if not request_snapshot_covers_initial_account_request(
            object.__getattribute__(manifest, "request_snapshot")
        ):
            return False
        operation_fields = set(_OPERATION_FIELDS)
        if any(
            destination not in operation_fields
            for _source, destination in _expected_snapshot_pairs()
        ):
            return False
        return True
    except _ControlledManifestInvalidity as error:
        if type(error) is not _ControlledManifestInvalidity:
            raise
        return False
