"""Inactive SQLAlchemy Core metadata for PostgreSQL account schema one."""

import hashlib as _hashlib
import re as _re

import sqlalchemy as _sa
from sqlalchemy.dialects import postgresql as _pg
from sqlalchemy.schema import conv as _conv

from api.auth import models as _models
from cuevion_auth import account_repository_contract as _account_contract
from cuevion_auth import relational_account_store_contract as _relational
from cuevion_db.metadata import metadata as _metadata


__all__ = (
    "ACCOUNT_SCHEMA",
    "ACCOUNT_SECURITY_EVENT_STREAM_NAME",
    "security_event_stream_position_sequence",
    "users",
    "verified_emails",
    "authentication_identities",
    "workspaces",
    "workspace_memberships",
    "initial_account_operations",
    "security_events",
    "ACCOUNT_TABLES",
)


ACCOUNT_SCHEMA = "cuevion_account"
ACCOUNT_SECURITY_EVENT_STREAM_NAME = (
    _relational.ACCOUNT_SECURITY_EVENT_STREAM_NAME
)

if (
    _metadata.schema != ACCOUNT_SCHEMA
    or not _relational.relational_schema_manifest_is_valid(
        _relational.RELATIONAL_ACCOUNT_SCHEMA_1
    )
):
    raise RuntimeError("invalid account schema foundation")


_MANIFESTS = {
    relation.relation.value: relation
    for relation in _relational.RELATIONAL_ACCOUNT_SCHEMA_1.relations
}
_RELATION_NAMES = tuple(item.value for item in _relational.RelationalAccountRelation)
if tuple(_MANIFESTS) != _RELATION_NAMES or len(_MANIFESTS) != 7:
    raise RuntimeError("invalid account schema foundation")


def _id_family(name: str) -> str | None:
    if (
        name in ("event_id", "security_event_id")
        or name.endswith("_security_event_id")
        or name.endswith("_event_event_id")
    ):
        return "sev"
    if (
        name in ("identity_id", "authentication_identity_id")
        or name.endswith("_authentication_identity_id")
        or name.endswith("_identity_identity_id")
    ):
        return "aid"
    if (
        name in ("email_id", "verified_email_id")
        or name.endswith("_verified_email_id")
        or name.endswith("_email_email_id")
    ):
        return "vem"
    if name == "workspace_id" or name.endswith("_workspace_id"):
        return "wsp"
    if name == "user_id" or name.endswith("_user_id"):
        return "usr"
    return None


def _column_type(relation_name: str, name: str) -> _sa.types.TypeEngine[object]:
    manifest = _MANIFESTS[relation_name]
    if name in manifest.timestamp_fields:
        return _pg.TIMESTAMP(timezone=True)
    if name in (
        "operation_digest",
        "snapshot_authentication_evidence_assertion_id",
    ):
        return _pg.BYTEA()
    if name == "row_version" or name.endswith("_row_version"):
        return _sa.BigInteger()
    if name == "security_epoch" or name.endswith("_security_epoch"):
        return _sa.BigInteger()
    if name in ("derivation_key_epoch", "event_stream_position"):
        return _sa.BigInteger()
    if name.endswith("_version"):
        return _sa.SmallInteger()
    if _id_family(name) is not None:
        return _sa.String(26, collation="C")
    if name.endswith("canonical_email") or name.endswith(
        "canonical_verified_email"
    ):
        return _sa.String(320, collation="C")
    if name.endswith("issuer") or name.endswith("subject"):
        return _sa.String(512, collation="C")
    if (
        name.endswith("trust_domain")
        or name.endswith("verification_coordinator_id")
        or name.endswith("verification_source")
    ):
        return _sa.String(128, collation="C")
    if name == "event_stream_name":
        return _sa.String(len(ACCOUNT_SECURITY_EVENT_STREAM_NAME), collation="C")
    if name.endswith("display_name"):
        return _sa.Text(collation="C")
    if (
        name == "status"
        or name.endswith("_status")
        or name == "role"
        or name.endswith("_role")
        or name == "authentication_method"
        or name.endswith("_authentication_method")
        or name == "event_type"
        or name.endswith("_event_type")
    ):
        return _sa.Text(collation="C")
    raise RuntimeError("invalid account schema foundation")


def _columns(relation_name: str) -> tuple[_sa.Column[object], ...]:
    return tuple(
        _sa.Column(
            field.name,
            _column_type(relation_name, field.name),
            nullable=field.nullable,
            info={"required": field.required, "immutable": field.immutable},
        )
        for field in _MANIFESTS[relation_name].fields
    )


_TABLES_BY_NAME = {
    name: _sa.Table(
        name,
        _metadata,
        *_columns(name),
        info={
            "append_only": name
            in ("initial_account_operations", "security_events"),
            "record_version": _MANIFESTS[name].record_version,
        },
    )
    for name in _RELATION_NAMES
}

users = _TABLES_BY_NAME["users"]
verified_emails = _TABLES_BY_NAME["verified_emails"]
authentication_identities = _TABLES_BY_NAME["authentication_identities"]
workspaces = _TABLES_BY_NAME["workspaces"]
workspace_memberships = _TABLES_BY_NAME["workspace_memberships"]
initial_account_operations = _TABLES_BY_NAME["initial_account_operations"]
security_events = _TABLES_BY_NAME["security_events"]
ACCOUNT_TABLES = tuple(_TABLES_BY_NAME[name] for name in _RELATION_NAMES)


security_event_stream_position_sequence = _sa.Sequence(
    "security_event_stream_position_seq",
    schema=ACCOUNT_SCHEMA,
    start=1,
    increment=1,
    minvalue=1,
    cycle=False,
    metadata=_metadata,
)


def _stable_name(kind: str, table_name: str, label: str) -> str:
    normalized = _re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
    candidate = f"{kind}_{table_name}_{normalized}"
    if len(candidate) <= 63:
        return candidate
    digest = _hashlib.sha256(candidate.encode("ascii")).hexdigest()[:8]
    available = 63 - len(kind) - len(table_name) - len(digest) - 3
    return f"{kind}_{table_name}_{normalized[:available]}_{digest}"


def _check(table: _sa.Table, expression: object, label: str) -> None:
    table.append_constraint(
        _sa.CheckConstraint(
            expression,
            name=_conv(_stable_name("ck", table.name, label)),
        )
    )


def _timestamp_expression(table: _sa.Table, name: str, nullable: bool) -> str:
    domain = (
        f"isfinite({name}) AND "
        f"{name} >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND "
        f"{name} <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND "
        f"{name} = date_trunc('second', {name})"
    )
    return f"{name} IS NULL OR ({domain})" if nullable else domain


def _enum_values(table_name: str, name: str) -> tuple[str, ...] | None:
    if name == "authentication_method" or name.endswith("_authentication_method"):
        return tuple(member.value for member in _models.AuthenticationMethod)
    if name == "role" or name.endswith("_role"):
        return tuple(member.value for member in _models.WorkspaceRole)
    if name == "event_type" or name.endswith("_event_type"):
        return tuple(member.value for member in _account_contract.InitialSecurityEventType)
    if name == "status":
        enum_type = {
            "users": _models.UserStatus,
            "verified_emails": _models.VerifiedEmailStatus,
            "authentication_identities": _models.AuthenticationIdentityStatus,
            "workspaces": _models.WorkspaceStatus,
            "workspace_memberships": _models.WorkspaceMembershipStatus,
        }.get(table_name)
        return None if enum_type is None else tuple(member.value for member in enum_type)
    status_types = (
        ("snapshot_verified_email_status", _models.VerifiedEmailStatus),
        (
            "snapshot_authentication_identity_status",
            _models.AuthenticationIdentityStatus,
        ),
        ("snapshot_workspace_membership_status", _models.WorkspaceMembershipStatus),
        ("snapshot_workspace_status", _models.WorkspaceStatus),
        ("snapshot_user_status", _models.UserStatus),
    )
    for suffix, enum_type in status_types:
        if name == suffix:
            return tuple(member.value for member in enum_type)
    return None


_ID_PATTERNS = {
    "usr": r"^usr_[A-Za-z0-9_-]{21}[AQgw]$",
    "vem": r"^vem_[A-Za-z0-9_-]{21}[AQgw]$",
    "aid": r"^aid_[A-Za-z0-9_-]{21}[AQgw]$",
    "wsp": r"^wsp_[A-Za-z0-9_-]{21}[AQgw]$",
    "sev": r"^sev_[A-Za-z0-9_-]{21}[AQgw]$",
}
_EMAIL_PATTERN = (
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:[.][a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:[.][a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


for _table in ACCOUNT_TABLES:
    _manifest = _MANIFESTS[_table.name]
    _field_by_name = {field.name: field for field in _manifest.fields}
    for _column in _table.c:
        _name = _column.name
        _field = _field_by_name[_name]
        _family = _id_family(_name)
        if _family is not None:
            _check(_table, _column.op("~")(_ID_PATTERNS[_family]), f"{_name}_canonical")
        if _name in (
            "operation_digest",
            "snapshot_authentication_evidence_assertion_id",
        ):
            _check(_table, _sa.func.octet_length(_column) == 32, f"{_name}_32_bytes")
        if _name in _manifest.timestamp_fields:
            _check(
                _table,
                _sa.text(_timestamp_expression(_table, _name, _field.nullable)),
                f"{_name}_timestamp",
            )
        if isinstance(_column.type, _sa.SmallInteger):
            _check(_table, _column == 1, f"{_name}_schema_one")
        if _name == "derivation_key_epoch":
            _check(
                _table,
                _column.between(1, 4_294_967_295),
                "derivation_key_epoch_range",
            )
        if _name == "event_stream_position":
            _check(_table, _column > 0, "event_stream_position_positive")
        if _name == "security_epoch" or _name.endswith("_security_epoch"):
            _check(_table, _column > 0, f"{_name}_positive")
        if _name == "row_version" or _name.endswith("_row_version"):
            if _table.name in ("initial_account_operations", "security_events") and _name == "row_version":
                _check(_table, _column == 1, "row_version_exact_one")
            else:
                _check(_table, _column > 0, f"{_name}_positive")
        _allowed = _enum_values(_table.name, _name)
        if _allowed is not None:
            _check(_table, _column.in_(_allowed), f"{_name}_closed")
        if _name.endswith("canonical_email") or _name.endswith("canonical_verified_email"):
            _check(
                _table,
                _sa.and_(
                    _column.op("~")(_EMAIL_PATTERN),
                    _sa.func.char_length(_sa.func.split_part(_column, "@", 1)) <= 64,
                    _sa.func.char_length(_sa.func.split_part(_column, "@", 2)) <= 253,
                ),
                f"{_name}_canonical",
            )
        if _name.endswith("verification_source"):
            _check(_table, _column.op("~")(r"^[!-~]{1,128}$"), f"{_name}_ascii")
        if _name.endswith("issuer") or _name.endswith("subject"):
            _check(_table, _column.op("~")(r"^[!-~]{1,512}$"), f"{_name}_ascii")
        if _name.endswith("trust_domain") or _name.endswith("verification_coordinator_id"):
            _check(_table, _column.op("~")(r"^[A-Za-z0-9._:-]{1,128}$"), f"{_name}_opaque")
        if _name.endswith("display_name"):
            _check(
                _table,
                _sa.and_(_sa.func.octet_length(_column) > 0, _sa.func.octet_length(_column) <= 256),
                f"{_name}_utf8_length",
            )


users.append_constraint(_sa.PrimaryKeyConstraint("user_id", name="pk_users"))
verified_emails.append_constraint(
    _sa.PrimaryKeyConstraint("email_id", name="pk_verified_emails")
)
authentication_identities.append_constraint(
    _sa.PrimaryKeyConstraint("identity_id", name="pk_auth_identities")
)
workspaces.append_constraint(
    _sa.PrimaryKeyConstraint("workspace_id", name="pk_workspaces")
)
workspace_memberships.append_constraint(
    _sa.PrimaryKeyConstraint("workspace_id", "user_id", name="pk_workspace_memberships")
)
initial_account_operations.append_constraint(
    _sa.PrimaryKeyConstraint(
        "reference_schema_version",
        "derivation_key_epoch",
        "operation_digest",
        name="pk_initial_account_operations",
    )
)
security_events.append_constraint(
    _sa.PrimaryKeyConstraint("event_id", name="pk_security_events")
)


verified_emails.append_constraint(
    _sa.UniqueConstraint("email_id", "user_id", name="uq_verified_emails_id_user")
)
authentication_identities.append_constraint(
    _sa.UniqueConstraint(
        "identity_id", "user_id", name="uq_auth_identities_id_user"
    )
)
authentication_identities.append_constraint(
    _sa.UniqueConstraint(
        "issuer", "subject", name="uq_auth_identities_issuer_subject"
    )
)
workspaces.append_constraint(
    _sa.UniqueConstraint(
        "workspace_id", "created_by_user_id", name="uq_workspaces_id_creator"
    )
)
initial_account_operations.append_constraint(
    _sa.UniqueConstraint(
        "snapshot_authentication_evidence_trust_domain",
        "snapshot_authentication_evidence_verification_coordinator_id",
        "snapshot_authentication_evidence_assertion_id",
        name="uq_initial_ops_evidence_assertion",
    )
)
initial_account_operations.append_constraint(
    _sa.UniqueConstraint(
        "receipt_security_event_id", name="uq_initial_ops_receipt_event"
    )
)

_OPERATION_EVENT_BINDING = (
    "reference_schema_version",
    "derivation_key_epoch",
    "operation_digest",
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
)
initial_account_operations.append_constraint(
    _sa.UniqueConstraint(
        *_OPERATION_EVENT_BINDING,
        name="uq_initial_ops_event_binding",
    )
)
security_events.append_constraint(
    _sa.UniqueConstraint(
        "reference_schema_version",
        "derivation_key_epoch",
        "operation_digest",
        name="uq_security_events_operation_ref",
    )
)
security_events.append_constraint(
    _sa.UniqueConstraint(
        "event_stream_name",
        "event_stream_position",
        name="uq_security_events_stream_position",
    )
)


_sa.Index(
    "ux_verified_emails_current_claim",
    verified_emails.c.canonical_email,
    unique=True,
    postgresql_where=_sa.and_(
        verified_emails.c.status == _models.VerifiedEmailStatus.VERIFIED.value,
        verified_emails.c.retired_at.is_(None),
    ),
)


def _foreign_key(
    table: _sa.Table,
    local: tuple[str, ...],
    target_table: str,
    target: tuple[str, ...],
    name: str,
    *,
    deferred: bool = False,
) -> None:
    table.append_constraint(
        _sa.ForeignKeyConstraint(
            local,
            tuple(f"{ACCOUNT_SCHEMA}.{target_table}.{column}" for column in target),
            name=name,
            onupdate="NO ACTION",
            ondelete="NO ACTION",
            deferrable=True if deferred else None,
            initially="DEFERRED" if deferred else None,
            use_alter=deferred,
            match="SIMPLE",
        )
    )


_foreign_key(
    users,
    ("primary_verified_email_id", "user_id"),
    "verified_emails",
    ("email_id", "user_id"),
    "fk_users_primary_email_same_user",
    deferred=True,
)
_foreign_key(verified_emails, ("user_id",), "users", ("user_id",), "fk_verified_emails_user")
_foreign_key(authentication_identities, ("user_id",), "users", ("user_id",), "fk_auth_identities_user")
_foreign_key(
    authentication_identities,
    ("verified_email_id", "user_id"),
    "verified_emails",
    ("email_id", "user_id"),
    "fk_auth_identities_verified_email_same_user",
)
_foreign_key(workspaces, ("created_by_user_id",), "users", ("user_id",), "fk_workspaces_creator")
_foreign_key(workspace_memberships, ("workspace_id",), "workspaces", ("workspace_id",), "fk_workspace_memberships_workspace")
_foreign_key(workspace_memberships, ("user_id",), "users", ("user_id",), "fk_workspace_memberships_user")
_foreign_key(initial_account_operations, ("receipt_user_id",), "users", ("user_id",), "fk_initial_ops_receipt_user")
_foreign_key(
    initial_account_operations,
    ("receipt_verified_email_id", "receipt_user_id"),
    "verified_emails",
    ("email_id", "user_id"),
    "fk_initial_ops_receipt_email_user",
)
_foreign_key(
    initial_account_operations,
    ("receipt_authentication_identity_id", "receipt_user_id"),
    "authentication_identities",
    ("identity_id", "user_id"),
    "fk_initial_ops_receipt_identity_user",
)
_foreign_key(
    initial_account_operations,
    ("receipt_workspace_id", "receipt_user_id"),
    "workspaces",
    ("workspace_id", "created_by_user_id"),
    "fk_initial_ops_receipt_workspace_creator",
)
_foreign_key(
    initial_account_operations,
    ("receipt_workspace_id", "receipt_user_id"),
    "workspace_memberships",
    ("workspace_id", "user_id"),
    "fk_initial_ops_receipt_membership",
)
_foreign_key(
    initial_account_operations,
    ("receipt_security_event_id",),
    "security_events",
    ("event_id",),
    "fk_initial_ops_receipt_event",
    deferred=True,
)

_EVENT_OPERATION_BINDING = (
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
)
_foreign_key(
    security_events,
    _EVENT_OPERATION_BINDING,
    "initial_account_operations",
    _OPERATION_EVENT_BINDING,
    "fk_security_events_operation_binding",
)
_foreign_key(security_events, ("user_id",), "users", ("user_id",), "fk_security_events_user")
_foreign_key(
    security_events,
    ("verified_email_id", "user_id"),
    "verified_emails",
    ("email_id", "user_id"),
    "fk_security_events_email_user",
)
_foreign_key(
    security_events,
    ("authentication_identity_id", "user_id"),
    "authentication_identities",
    ("identity_id", "user_id"),
    "fk_security_events_identity_user",
)
_foreign_key(
    security_events,
    ("workspace_id", "user_id"),
    "workspaces",
    ("workspace_id", "created_by_user_id"),
    "fk_security_events_workspace_creator",
)
_foreign_key(
    security_events,
    ("membership_workspace_id", "membership_user_id"),
    "workspace_memberships",
    ("workspace_id", "user_id"),
    "fk_security_events_membership",
)


_check(users, users.c.created_at <= users.c.updated_at, "timestamp_order")
_check(
    users,
    _sa.or_(users.c.status != _models.UserStatus.ACTIVE.value, users.c.primary_verified_email_id.is_not(None)),
    "active_primary_email",
)
_check(
    verified_emails,
    _sa.or_(
        _sa.and_(
            verified_emails.c.status == _models.VerifiedEmailStatus.PENDING.value,
            verified_emails.c.verified_at.is_(None),
            verified_emails.c.retired_at.is_(None),
        ),
        _sa.and_(
            verified_emails.c.status == _models.VerifiedEmailStatus.VERIFIED.value,
            verified_emails.c.verified_at.is_not(None),
            verified_emails.c.retired_at.is_(None),
            verified_emails.c.created_at <= verified_emails.c.verified_at,
        ),
        _sa.and_(
            verified_emails.c.status == _models.VerifiedEmailStatus.RETIRED.value,
            verified_emails.c.verified_at.is_not(None),
            verified_emails.c.retired_at.is_not(None),
            verified_emails.c.created_at <= verified_emails.c.verified_at,
            verified_emails.c.verified_at <= verified_emails.c.retired_at,
        ),
    ),
    "lifecycle",
)
_check(
    authentication_identities,
    _sa.or_(
        authentication_identities.c.last_used_at.is_(None),
        authentication_identities.c.created_at <= authentication_identities.c.last_used_at,
    ),
    "timestamp_order",
)
_check(workspaces, workspaces.c.created_at <= workspaces.c.updated_at, "timestamp_order")
_check(
    workspace_memberships,
    workspace_memberships.c.created_at <= workspace_memberships.c.updated_at,
    "timestamp_order",
)


_op = initial_account_operations.c
_check(
    initial_account_operations,
    _sa.and_(
        _op.snapshot_user_created_at <= _op.snapshot_user_updated_at,
        _op.snapshot_verified_email_created_at <= _op.snapshot_verified_email_verified_at,
        _op.snapshot_verified_email_retired_at.is_(None),
        _sa.or_(
            _op.snapshot_authentication_identity_last_used_at.is_(None),
            _op.snapshot_authentication_identity_created_at <= _op.snapshot_authentication_identity_last_used_at,
        ),
        _op.snapshot_workspace_created_at <= _op.snapshot_workspace_updated_at,
        _op.snapshot_workspace_membership_created_at <= _op.snapshot_workspace_membership_updated_at,
        _op.snapshot_authentication_evidence_verified_at <= _op.snapshot_authentication_evidence_issued_at,
        _op.snapshot_authentication_evidence_issued_at < _op.snapshot_authentication_evidence_expires_at,
    ),
    "timestamp_order",
)
_check(
    initial_account_operations,
    _sa.and_(
        _op.snapshot_user_status == _models.UserStatus.ACTIVE.value,
        _op.snapshot_user_security_epoch == 1,
        _op.snapshot_user_row_version == 1,
        _op.snapshot_verified_email_status == _models.VerifiedEmailStatus.VERIFIED.value,
        _op.snapshot_verified_email_verified_at.is_not(None),
        _op.snapshot_verified_email_retired_at.is_(None),
        _op.snapshot_verified_email_row_version == 1,
        _op.snapshot_authentication_identity_status == _models.AuthenticationIdentityStatus.ACTIVE.value,
        _op.snapshot_authentication_identity_row_version == 1,
        _op.snapshot_workspace_status == _models.WorkspaceStatus.ACTIVE.value,
        _op.snapshot_workspace_row_version == 1,
        _op.snapshot_workspace_membership_role == _models.WorkspaceRole.OWNER.value,
        _op.snapshot_workspace_membership_status == _models.WorkspaceMembershipStatus.ACTIVE.value,
        _op.snapshot_workspace_membership_row_version == 1,
        _op.snapshot_security_event_event_type == _account_contract.InitialSecurityEventType.INITIAL_ACCOUNT_CREATED.value,
    ),
    "initial_state",
)
_check(
    initial_account_operations,
    _sa.and_(
        _op.snapshot_user_primary_verified_email_id == _op.snapshot_verified_email_email_id,
        _op.snapshot_verified_email_user_id == _op.snapshot_user_user_id,
        _op.snapshot_authentication_identity_user_id == _op.snapshot_user_user_id,
        _op.snapshot_authentication_identity_verified_email_id == _op.snapshot_verified_email_email_id,
        _op.snapshot_authentication_identity_issuer == _op.snapshot_authentication_evidence_issuer,
        _op.snapshot_authentication_identity_subject == _op.snapshot_authentication_evidence_subject,
        _op.snapshot_authentication_identity_authentication_method == _op.snapshot_authentication_evidence_authentication_method,
        _op.snapshot_authentication_evidence_canonical_verified_email == _op.snapshot_verified_email_canonical_email,
        _op.snapshot_authentication_evidence_verified_at == _op.snapshot_verified_email_verified_at,
        _op.snapshot_workspace_created_by_user_id == _op.snapshot_user_user_id,
        _op.snapshot_workspace_membership_workspace_id == _op.snapshot_workspace_workspace_id,
        _op.snapshot_workspace_membership_user_id == _op.snapshot_user_user_id,
    ),
    "snapshot_graph",
)
_check(
    initial_account_operations,
    _sa.and_(
        _op.receipt_user_id == _op.snapshot_user_user_id,
        _op.receipt_verified_email_id == _op.snapshot_verified_email_email_id,
        _op.receipt_authentication_identity_id == _op.snapshot_authentication_identity_identity_id,
        _op.receipt_workspace_id == _op.snapshot_workspace_workspace_id,
        _op.receipt_security_event_id == _op.snapshot_security_event_event_id,
    ),
    "receipt_binding",
)


_event = security_events.c
_check(security_events, _event.event_at <= _event.recorded_at, "timestamp_order")
_check(
    security_events,
    _sa.and_(
        _event.membership_workspace_id == _event.workspace_id,
        _event.membership_user_id == _event.user_id,
    ),
    "membership_binding",
)
_check(
    security_events,
    _event.event_stream_name == ACCOUNT_SECURITY_EVENT_STREAM_NAME,
    "stream_name",
)


del _table, _manifest, _field_by_name, _column, _name, _field, _family, _allowed
del _op, _event
