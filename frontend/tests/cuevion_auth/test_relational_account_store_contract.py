"""Pure tests for the inactive relational account-store contract."""

import ast
import copy
import dataclasses
import inspect
import json
import pickle
import re
from pathlib import Path, PurePosixPath
import subprocess
import sys
import typing
import unittest

from api.auth import models as auth_models
from cuevion_auth import account_repository_contract as account_contract
from cuevion_auth import relational_account_store_contract as contract


_TEST_DIRECTORY = Path(__file__).resolve().parent
_FRONTEND_DIRECTORY = _TEST_DIRECTORY.parents[1]
_SOURCE_DIRECTORY = _FRONTEND_DIRECTORY / "cuevion_auth"
_SOURCE_PATH = _SOURCE_DIRECTORY / "relational_account_store_contract.py"
_DOCUMENTATION_PATH = (
    _SOURCE_DIRECTORY / "RELATIONAL_ACCOUNT_STORE_ACTIVATION_REQUIREMENTS.md"
)
_ACCOUNT_DOCUMENTATION_PATH = (
    _SOURCE_DIRECTORY / "ACCOUNT_REPOSITORY_ACTIVATION_REQUIREMENTS.md"
)


EXPECTED_ALL = (
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
    "RELATIONAL_ACCOUNT_SCHEMA_1",
    "relational_schema_manifest_is_valid",
    "relational_version_is_supported",
    "request_snapshot_covers_initial_account_request",
)

EXPECTED_ENUMS = {
    contract.RelationalAccountRelation: (
        ("USERS", "users"),
        ("VERIFIED_EMAILS", "verified_emails"),
        ("AUTHENTICATION_IDENTITIES", "authentication_identities"),
        ("WORKSPACES", "workspaces"),
        ("WORKSPACE_MEMBERSHIPS", "workspace_memberships"),
        ("INITIAL_ACCOUNT_OPERATIONS", "initial_account_operations"),
        ("SECURITY_EVENTS", "security_events"),
    ),
    contract.RelationalConstraintCategory: (
        ("PRIMARY_KEY", "primary_key"),
        ("FOREIGN_KEY", "foreign_key"),
        ("UNIQUE_AUTHORITY_CLAIM", "unique_authority_claim"),
        ("UNIQUE_OPERATION_REFERENCE", "unique_operation_reference"),
        ("UNIQUE_EVIDENCE_ASSERTION", "unique_evidence_assertion"),
        ("UNIQUE_INITIAL_OPERATION_EVENT", "unique_initial_operation_event"),
        ("UNIQUE_EVENT_STREAM_POSITION", "unique_event_stream_position"),
        ("POSITIVE_VERSION", "positive_version"),
        ("POSITIVE_SECURITY_EPOCH", "positive_security_epoch"),
        ("VALID_STATUS", "valid_status"),
        ("VALID_ROLE", "valid_role"),
        ("VALID_AUTHENTICATION_METHOD", "valid_authentication_method"),
        ("VALID_EVENT_TYPE", "valid_event_type"),
        ("TIMESTAMP_ORDER", "timestamp_order"),
        ("SAME_USER_REFERENCE", "same_user_reference"),
        ("IMMUTABLE_VALUE", "immutable_value"),
        ("APPEND_ONLY", "append_only"),
        ("EXACT_CASE_SENSITIVE_VALUE", "exact_case_sensitive_value"),
        ("EXACT_FIELD_EQUALITY", "exact_field_equality"),
        ("CANONICAL_IDENTIFIER", "canonical_identifier"),
        ("CANONICAL_DIGEST", "canonical_digest"),
    ),
    contract.RelationalVersionCategory: (
        ("RELATIONAL_SCHEMA_CONTRACT", "relational_schema_contract"),
        ("RELATION_RECORD", "relation_record"),
        ("INITIAL_OPERATION_RECORD", "initial_operation_record"),
        ("REQUEST_SNAPSHOT", "request_snapshot"),
        ("RECEIPT", "receipt"),
        ("EVIDENCE", "evidence"),
        ("SECURITY_EVENT_RECORD", "security_event_record"),
        ("EVENT_PAYLOAD", "event_payload"),
        ("CONSISTENT_READ_CONTRACT", "consistent_read_contract"),
    ),
    contract.ConsistentReadRequirementCategory: (
        ("ATOMIC_SNAPSHOT", "atomic_snapshot"),
        ("STORED_SESSION", "stored_session"),
        ("EXACT_LINKED_USER", "exact_linked_user"),
        (
            "EXACT_LINKED_AUTHENTICATION_IDENTITY",
            "exact_linked_authentication_identity",
        ),
        ("PRIMARY_VERIFIED_EMAIL", "primary_verified_email"),
        ("CURRENT_AUTHENTICATION_STATUS", "current_authentication_status"),
        ("SECURITY_EPOCH", "security_epoch"),
        ("RECORD_VERSIONS", "record_versions"),
        (
            "CREDENTIAL_DIGESTS_AND_EPOCHS",
            "credential_digests_and_epochs",
        ),
        ("SESSION_EXPIRY_AND_REVOCATION", "session_expiry_and_revocation"),
        (
            "WORKSPACE_AUTHORIZATION_EXCLUDED",
            "workspace_authorization_excluded",
        ),
        (
            "PRODUCT_AUTHORIZATION_EXCLUDED",
            "product_authorization_excluded",
        ),
    ),
}

RECORD_SLOTS = {
    contract.RelationalFieldManifest: (
        "name",
        "required",
        "nullable",
        "immutable",
    ),
    contract.RelationalPrimaryKeyManifest: ("category", "field_names"),
    contract.RelationalForeignKeyManifest: (
        "categories",
        "field_names",
        "referenced_relation",
        "referenced_field_names",
    ),
    contract.RelationalUniqueConstraintManifest: (
        "category",
        "field_names",
        "scope_field_names",
    ),
    contract.RelationalInvariantManifest: ("category", "field_names"),
    contract.RelationalVersionRequirement: (
        "category",
        "supported_version",
        "unknown_newer_fails_closed",
        "implicit_authority_defaults_allowed",
    ),
    contract.RequestSnapshotFieldManifest: (
        "source_path",
        "operation_field_name",
    ),
    contract.RequestSnapshotManifest: (
        "version",
        "request_field_names",
        "fields",
    ),
    contract.InitialAccountTransactionManifest: (
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
    ),
    contract.ConsistentReadRequirementManifest: (
        "contract_version",
        "categories",
        "required_facts",
        "forbidden_facts",
        "future_blockers",
    ),
    contract.RelationalRelationManifest: (
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
    ),
    contract.RelationalSchemaManifest: (
        "contract_version",
        "relations",
        "version_requirements",
        "request_snapshot",
        "initial_account_transaction",
        "consistent_read_requirement",
        "migration_stages",
        "missing_authority_fields_receive_defaults",
        "application_rollback_requires_reader_writer_compatibility",
    ),
}

EXPECTED_CORE_RELATION_FIELDS = {
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
}

EXPECTED_SECURITY_EVENT_FIELDS = (
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


def _relations_by_name():
    return {
        relation.relation.value: relation
        for relation in contract.RELATIONAL_ACCOUNT_SCHEMA_1.relations
    }


def _record_graph():
    seen = set()
    records = []

    def visit(value):
        if id(value) in seen:
            return
        seen.add(id(value))
        if type(value) in RECORD_SLOTS:
            records.append(value)
            for slot in type(value).__slots__:
                visit(object.__getattribute__(value, slot))
        elif type(value) is tuple:
            for item in value:
                visit(item)

    visit(contract.RELATIONAL_ACCOUNT_SCHEMA_1)
    return tuple(records)


def _expected_snapshot_pairs():
    records = {
        "operation_reference": account_contract.InitialAccountOperationReference,
        "user": auth_models.CuevionUser,
        "verified_email": auth_models.VerifiedEmail,
        "authentication_identity": auth_models.AuthenticationIdentity,
        "workspace": auth_models.Workspace,
        "workspace_membership": auth_models.WorkspaceMembership,
        "authentication_evidence": account_contract.VerifiedAuthenticationEvidence,
        "security_event": account_contract.InitialSecurityEventRequest,
    }
    pairs = []
    for request_field in account_contract.InitialAccountCreationRequest.__slots__:
        if request_field == "request_version":
            pairs.append((request_field, request_field))
            continue
        for leaf in records[request_field].__slots__:
            if request_field == "operation_reference":
                destination = (
                    "reference_schema_version"
                    if leaf == "schema_version"
                    else leaf
                )
            else:
                destination_leaf = (
                    "authentication_method"
                    if request_field == "authentication_identity"
                    and leaf == "method"
                    else leaf
                )
                destination = f"snapshot_{request_field}_{destination_leaf}"
            pairs.append((f"{request_field}.{leaf}", destination))
    return tuple(pairs)


def _expected_operation_fields():
    excluded_sources = {
        "request_version",
        "operation_reference.schema_version",
        "operation_reference.derivation_key_epoch",
        "operation_reference.operation_digest",
    }
    return (
        "operation_record_version",
        "reference_schema_version",
        "derivation_key_epoch",
        "operation_digest",
        "request_snapshot_version",
        "request_version",
        *tuple(
            destination
            for source, destination in _expected_snapshot_pairs()
            if source not in excluded_sources
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


EXPECTED_RELATION_FIELDS = {
    **EXPECTED_CORE_RELATION_FIELDS,
    "initial_account_operations": _expected_operation_fields(),
    "security_events": EXPECTED_SECURITY_EVENT_FIELDS,
}

EXPECTED_NULLABLE_FIELDS = {
    "users": ("primary_verified_email_id",),
    "verified_emails": ("verified_at", "retired_at"),
    "authentication_identities": ("verified_email_id", "last_used_at"),
    "workspaces": (),
    "workspace_memberships": (),
    "initial_account_operations": (
        "snapshot_verified_email_retired_at",
        "snapshot_authentication_identity_last_used_at",
    ),
    "security_events": (),
}

EXPECTED_IMMUTABLE_FIELDS = {
    "users": ("schema_version", "user_id", "created_at"),
    "verified_emails": (
        "schema_version",
        "email_id",
        "user_id",
        "canonical_email",
        "verification_source",
        "created_at",
        "verified_at",
    ),
    "authentication_identities": (
        "schema_version",
        "identity_id",
        "user_id",
        "issuer",
        "subject",
        "authentication_method",
        "created_at",
    ),
    "workspaces": (
        "schema_version",
        "workspace_id",
        "created_by_user_id",
        "created_at",
    ),
    "workspace_memberships": (
        "schema_version",
        "workspace_id",
        "user_id",
        "created_at",
    ),
    "initial_account_operations": _expected_operation_fields(),
    "security_events": EXPECTED_SECURITY_EVENT_FIELDS,
}

EXPECTED_PRIMARY_KEYS = {
    "users": ("user_id",),
    "verified_emails": ("email_id",),
    "authentication_identities": ("identity_id",),
    "workspaces": ("workspace_id",),
    "workspace_memberships": ("workspace_id", "user_id"),
    "initial_account_operations": (
        "reference_schema_version",
        "derivation_key_epoch",
        "operation_digest",
    ),
    "security_events": ("event_id",),
}

EXPECTED_CONSISTENT_READ_REQUIRED_FACTS = (
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
)

EXPECTED_CONSISTENT_READ_FORBIDDEN_FACTS = (
    "workspace",
    "workspace_membership",
    "role",
    "product",
    "entitlement",
    "billing",
    "subscription",
    "seats",
)

EXPECTED_CONSISTENT_READ_FUTURE_BLOCKERS = (
    "stored_session_missing_lookup_key_epoch",
    "stored_session_missing_binding_key_epoch",
    "session_contract_not_modified_by_this_slice",
)

SENSITIVE_SCHEMA_IDENTIFIERS = (
    "raw_operation_key",
    "access_token",
    "refresh_token",
    "id_token",
    "provider_payload",
    "secret",
    "client_secret",
    "challenge_secret",
    "pkce_verifier",
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


def _identifier_tokens(value):
    return tuple(
        token
        for token in re.split(r"[^a-z0-9]+", value.casefold())
        if token
    )


def _contains_identifier(value, forbidden_identifier):
    tokens = _identifier_tokens(value)
    forbidden_tokens = _identifier_tokens(forbidden_identifier)
    width = len(forbidden_tokens)
    return any(
        tokens[index : index + width] == forbidden_tokens
        for index in range(len(tokens) - width + 1)
    )


def _record_values(record):
    return tuple(
        object.__getattribute__(record, slot) for slot in type(record).__slots__
    )


def _clone_record(record):
    return type(record)(*_record_values(record))


def _copy_record_with(record, **replacements):
    unexpected = set(replacements).difference(type(record).__slots__)
    if unexpected:
        raise AssertionError(f"unknown record fields: {sorted(unexpected)!r}")
    values = tuple(
        replacements.get(
            slot,
            object.__getattribute__(record, slot),
        )
        for slot in type(record).__slots__
    )
    return type(record)(*values)


def _corrupt_record(record, field_name, value):
    candidate = _clone_record(record)
    object.__setattr__(candidate, field_name, value)
    return candidate


def _schema_with_relation(replacement):
    schema = contract.RELATIONAL_ACCOUNT_SCHEMA_1
    relations = tuple(
        replacement if relation.relation is replacement.relation else relation
        for relation in schema.relations
    )
    return _copy_record_with(schema, relations=relations)


_FOREIGN_KEY = (contract.RelationalConstraintCategory.FOREIGN_KEY,)
_SAME_USER_FOREIGN_KEY = (
    contract.RelationalConstraintCategory.FOREIGN_KEY,
    contract.RelationalConstraintCategory.SAME_USER_REFERENCE,
)

EXPECTED_FOREIGN_KEYS = {
    "users": (
        (
            _SAME_USER_FOREIGN_KEY,
            ("primary_verified_email_id", "user_id"),
            "verified_emails",
            ("email_id", "user_id"),
        ),
    ),
    "verified_emails": (
        (_FOREIGN_KEY, ("user_id",), "users", ("user_id",)),
    ),
    "authentication_identities": (
        (_FOREIGN_KEY, ("user_id",), "users", ("user_id",)),
        (
            _SAME_USER_FOREIGN_KEY,
            ("verified_email_id", "user_id"),
            "verified_emails",
            ("email_id", "user_id"),
        ),
    ),
    "workspaces": (
        (
            _FOREIGN_KEY,
            ("created_by_user_id",),
            "users",
            ("user_id",),
        ),
    ),
    "workspace_memberships": (
        (_FOREIGN_KEY, ("workspace_id",), "workspaces", ("workspace_id",)),
        (_FOREIGN_KEY, ("user_id",), "users", ("user_id",)),
    ),
    "initial_account_operations": (
        (_FOREIGN_KEY, ("receipt_user_id",), "users", ("user_id",)),
        (
            _SAME_USER_FOREIGN_KEY,
            ("receipt_verified_email_id", "receipt_user_id"),
            "verified_emails",
            ("email_id", "user_id"),
        ),
        (
            _SAME_USER_FOREIGN_KEY,
            ("receipt_authentication_identity_id", "receipt_user_id"),
            "authentication_identities",
            ("identity_id", "user_id"),
        ),
        (
            _SAME_USER_FOREIGN_KEY,
            ("receipt_workspace_id", "receipt_user_id"),
            "workspaces",
            ("workspace_id", "created_by_user_id"),
        ),
        (
            _SAME_USER_FOREIGN_KEY,
            ("receipt_workspace_id", "receipt_user_id"),
            "workspace_memberships",
            ("workspace_id", "user_id"),
        ),
        (
            _FOREIGN_KEY,
            ("receipt_security_event_id",),
            "security_events",
            ("event_id",),
        ),
    ),
    "security_events": (
        (
            _SAME_USER_FOREIGN_KEY,
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
            "initial_account_operations",
            (
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
            ),
        ),
        (_FOREIGN_KEY, ("user_id",), "users", ("user_id",)),
        (
            _SAME_USER_FOREIGN_KEY,
            ("verified_email_id", "user_id"),
            "verified_emails",
            ("email_id", "user_id"),
        ),
        (
            _SAME_USER_FOREIGN_KEY,
            ("authentication_identity_id", "user_id"),
            "authentication_identities",
            ("identity_id", "user_id"),
        ),
        (
            _SAME_USER_FOREIGN_KEY,
            ("workspace_id", "user_id"),
            "workspaces",
            ("workspace_id", "created_by_user_id"),
        ),
        (
            _SAME_USER_FOREIGN_KEY,
            ("membership_workspace_id", "membership_user_id"),
            "workspace_memberships",
            ("workspace_id", "user_id"),
        ),
    ),
}

EXPECTED_UNIQUE_CONSTRAINTS = {
    "users": (),
    "verified_emails": (
        (
            contract.RelationalConstraintCategory.UNIQUE_AUTHORITY_CLAIM,
            ("canonical_email",),
            ("status", "retired_at"),
        ),
    ),
    "authentication_identities": (
        (
            contract.RelationalConstraintCategory.UNIQUE_AUTHORITY_CLAIM,
            ("issuer", "subject"),
            (),
        ),
    ),
    "workspaces": (),
    "workspace_memberships": (),
    "initial_account_operations": (
        (
            contract.RelationalConstraintCategory.UNIQUE_OPERATION_REFERENCE,
            (
                "reference_schema_version",
                "derivation_key_epoch",
                "operation_digest",
            ),
            (),
        ),
        (
            contract.RelationalConstraintCategory.UNIQUE_EVIDENCE_ASSERTION,
            (
                "snapshot_authentication_evidence_trust_domain",
                "snapshot_authentication_evidence_verification_coordinator_id",
                "snapshot_authentication_evidence_assertion_id",
            ),
            (),
        ),
        (
            contract.RelationalConstraintCategory.UNIQUE_INITIAL_OPERATION_EVENT,
            ("receipt_security_event_id",),
            (),
        ),
    ),
    "security_events": (
        (
            contract.RelationalConstraintCategory.UNIQUE_INITIAL_OPERATION_EVENT,
            (
                "reference_schema_version",
                "derivation_key_epoch",
                "operation_digest",
            ),
            (),
        ),
        (
            contract.RelationalConstraintCategory.UNIQUE_EVENT_STREAM_POSITION,
            ("event_stream_name", "event_stream_position"),
            (),
        ),
    ),
}


def _expected_invariant(category, *field_names):
    return (category, field_names)


EXPECTED_INVARIANTS = {
    "users": (
        _expected_invariant(
            contract.RelationalConstraintCategory.CANONICAL_IDENTIFIER,
            "user_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.POSITIVE_VERSION,
            "schema_version",
            "row_version",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.POSITIVE_SECURITY_EPOCH,
            "security_epoch",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.VALID_STATUS,
            "status",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.TIMESTAMP_ORDER,
            "created_at",
            "updated_at",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.SAME_USER_REFERENCE,
            "primary_verified_email_id",
            "user_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.IMMUTABLE_VALUE,
            "schema_version",
            "user_id",
            "created_at",
        ),
    ),
    "verified_emails": (
        _expected_invariant(
            contract.RelationalConstraintCategory.CANONICAL_IDENTIFIER,
            "email_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.POSITIVE_VERSION,
            "schema_version",
            "row_version",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.VALID_STATUS,
            "status",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.TIMESTAMP_ORDER,
            "created_at",
            "verified_at",
            "retired_at",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.IMMUTABLE_VALUE,
            "schema_version",
            "email_id",
            "user_id",
            "canonical_email",
            "verification_source",
            "created_at",
            "verified_at",
        ),
    ),
    "authentication_identities": (
        _expected_invariant(
            contract.RelationalConstraintCategory.CANONICAL_IDENTIFIER,
            "identity_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.POSITIVE_VERSION,
            "schema_version",
            "row_version",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.VALID_STATUS,
            "status",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.VALID_AUTHENTICATION_METHOD,
            "authentication_method",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.EXACT_CASE_SENSITIVE_VALUE,
            "issuer",
            "subject",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.TIMESTAMP_ORDER,
            "created_at",
            "last_used_at",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.SAME_USER_REFERENCE,
            "verified_email_id",
            "user_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.IMMUTABLE_VALUE,
            "schema_version",
            "identity_id",
            "user_id",
            "issuer",
            "subject",
            "authentication_method",
            "created_at",
        ),
    ),
    "workspaces": (
        _expected_invariant(
            contract.RelationalConstraintCategory.CANONICAL_IDENTIFIER,
            "workspace_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.POSITIVE_VERSION,
            "schema_version",
            "row_version",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.VALID_STATUS,
            "status",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.TIMESTAMP_ORDER,
            "created_at",
            "updated_at",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.IMMUTABLE_VALUE,
            "schema_version",
            "workspace_id",
            "created_by_user_id",
            "created_at",
        ),
    ),
    "workspace_memberships": (
        _expected_invariant(
            contract.RelationalConstraintCategory.CANONICAL_IDENTIFIER,
            "workspace_id",
            "user_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.POSITIVE_VERSION,
            "schema_version",
            "row_version",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.VALID_ROLE,
            "role",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.VALID_STATUS,
            "status",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.TIMESTAMP_ORDER,
            "created_at",
            "updated_at",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.IMMUTABLE_VALUE,
            "schema_version",
            "workspace_id",
            "user_id",
            "created_at",
        ),
    ),
    "initial_account_operations": (
        _expected_invariant(
            contract.RelationalConstraintCategory.POSITIVE_VERSION,
            "operation_record_version",
            "reference_schema_version",
            "derivation_key_epoch",
            "request_snapshot_version",
            "request_version",
            "receipt_version",
            "row_version",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.CANONICAL_DIGEST,
            "operation_digest",
            "snapshot_authentication_evidence_assertion_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.CANONICAL_IDENTIFIER,
            "receipt_user_id",
            "receipt_verified_email_id",
            "receipt_authentication_identity_id",
            "receipt_workspace_id",
            "receipt_security_event_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.EXACT_CASE_SENSITIVE_VALUE,
            "snapshot_authentication_identity_subject",
            "snapshot_authentication_evidence_subject",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.TIMESTAMP_ORDER,
            "snapshot_authentication_evidence_verified_at",
            "snapshot_authentication_evidence_issued_at",
            "snapshot_authentication_evidence_expires_at",
            "committed_at",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.EXACT_FIELD_EQUALITY,
            "receipt_user_id",
            "snapshot_user_user_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.EXACT_FIELD_EQUALITY,
            "receipt_verified_email_id",
            "snapshot_verified_email_email_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.EXACT_FIELD_EQUALITY,
            "receipt_authentication_identity_id",
            "snapshot_authentication_identity_identity_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.EXACT_FIELD_EQUALITY,
            "receipt_workspace_id",
            "snapshot_workspace_workspace_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.EXACT_FIELD_EQUALITY,
            "receipt_security_event_id",
            "snapshot_security_event_event_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.SAME_USER_REFERENCE,
            "receipt_user_id",
            "receipt_verified_email_id",
            "receipt_authentication_identity_id",
            "receipt_workspace_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.IMMUTABLE_VALUE,
            *_expected_operation_fields(),
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.APPEND_ONLY,
            *_expected_operation_fields(),
        ),
    ),
    "security_events": (
        _expected_invariant(
            contract.RelationalConstraintCategory.CANONICAL_IDENTIFIER,
            "event_id",
            "user_id",
            "verified_email_id",
            "authentication_identity_id",
            "workspace_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.CANONICAL_DIGEST,
            "operation_digest",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.POSITIVE_VERSION,
            "event_record_version",
            "event_payload_version",
            "reference_schema_version",
            "derivation_key_epoch",
            "event_stream_position",
            "row_version",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.POSITIVE_SECURITY_EPOCH,
            "security_epoch",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.VALID_EVENT_TYPE,
            "event_type",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.TIMESTAMP_ORDER,
            "event_at",
            "recorded_at",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.SAME_USER_REFERENCE,
            "user_id",
            "verified_email_id",
            "authentication_identity_id",
            "workspace_id",
            "membership_workspace_id",
            "membership_user_id",
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.IMMUTABLE_VALUE,
            *EXPECTED_SECURITY_EVENT_FIELDS,
        ),
        _expected_invariant(
            contract.RelationalConstraintCategory.APPEND_ONLY,
            *EXPECTED_SECURITY_EVENT_FIELDS,
        ),
    ),
}

COMMON_FORBIDDEN_FIELDS = (
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

EXPECTED_RELATION_METADATA = {
    "users": (
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
        COMMON_FORBIDDEN_FIELDS,
    ),
    "verified_emails": (
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
        (*COMMON_FORBIDDEN_FIELDS, "automatic_account_link", "provider_payload"),
    ),
    "authentication_identities": (
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
            *COMMON_FORBIDDEN_FIELDS,
            "access_token",
            "refresh_token",
            "id_token",
            "provider_payload",
        ),
    ),
    "workspaces": (
        True,
        False,
        ("created_at", "updated_at"),
        (
            "status_active",
            "creator_is_provenance_not_current_authority",
            "workspace_id_independent_of_email",
            "row_version_exactly_one",
        ),
        COMMON_FORBIDDEN_FIELDS,
    ),
    "workspace_memberships": (
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
        COMMON_FORBIDDEN_FIELDS,
    ),
    "initial_account_operations": (
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
            *COMMON_FORBIDDEN_FIELDS,
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
    ),
    "security_events": (
        True,
        True,
        ("event_at", "recorded_at"),
        (
            "event_type_initial_account_created",
            "exactly_one_event_per_initial_operation",
            "event_id_from_validated_request",
            "event_time_repository_generated",
            "append_position_repository_generated",
            "commit_metadata_repository_generated",
            "row_version_exactly_one",
        ),
        (
            *COMMON_FORBIDDEN_FIELDS,
            "raw_operation_key",
            "raw_evidence",
            "access_token",
            "refresh_token",
            "id_token",
            "provider_payload",
            "secret",
        ),
    ),
}


class PublicSurfaceTests(unittest.TestCase):
    def test_exact_all_and_canonical_identity(self):
        self.assertEqual(contract.__all__, EXPECTED_ALL)
        self.assertEqual(
            {name for name in vars(contract) if not name.startswith("_")},
            set(EXPECTED_ALL),
        )
        self.assertEqual(
            contract.__name__,
            "cuevion_auth.relational_account_store_contract",
        )
        self.assertEqual(contract.__package__, "cuevion_auth")
        self.assertIs(
            contract,
            sys.modules["cuevion_auth.relational_account_store_contract"],
        )
        self.assertFalse((_SOURCE_DIRECTORY / "__init__.py").exists())

    def test_closed_enums_have_exact_members_and_values(self):
        class StringSubclass(str):
            pass

        for enum_type, expected in EXPECTED_ENUMS.items():
            with self.subTest(enum=enum_type.__name__):
                self.assertEqual(
                    tuple((member.name, member.value) for member in enum_type),
                    expected,
                )
                for member in enum_type:
                    self.assertIs(enum_type(member), member)
                    self.assertIs(enum_type(member.value), member)
                with self.assertRaises(ValueError):
                    enum_type("not-declared")
                with self.assertRaises(ValueError):
                    enum_type(StringSubclass(expected[0][1]))

    def test_manifest_records_have_exact_slots_hints_and_signatures(self):
        for record_type, slots in RECORD_SLOTS.items():
            with self.subTest(record=record_type.__name__):
                self.assertFalse(dataclasses.is_dataclass(record_type))
                self.assertEqual(tuple(record_type.__slots__), slots)
                self.assertEqual(tuple(typing.get_type_hints(record_type)), slots)
                signature = inspect.signature(record_type.__init__)
                self.assertEqual(tuple(signature.parameters), ("self", *slots))
                for parameter in signature.parameters.values():
                    self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_public_validators_have_exact_signatures(self):
        expected = {
            contract.relational_schema_manifest_is_valid: ("manifest",),
            contract.relational_version_is_supported: ("category", "version"),
            contract.request_snapshot_covers_initial_account_request: (
                "snapshot",
            ),
        }
        for function, parameters in expected.items():
            with self.subTest(function=function.__name__):
                self.assertEqual(
                    tuple(inspect.signature(function).parameters), parameters
                )
                self.assertIs(
                    typing.get_type_hints(function)["return"], bool
                )

    def test_no_repository_adapter_builder_or_executor_surface(self):
        self.assertFalse(
            any(
                getattr(value, "_is_protocol", False)
                for value in vars(contract).values()
            )
        )
        for name in (
            "repository",
            "adapter",
            "connection",
            "execute",
            "builder",
            "migration_executor",
            "handler",
            "route",
            "router",
            "app",
            "server",
        ):
            self.assertNotIn(name, contract.__all__)
            self.assertNotIn(name, vars(contract))


class ImmutabilityTests(unittest.TestCase):
    def test_complete_manifest_graph_is_slotted_and_contains_no_mutable_containers(self):
        records = _record_graph()
        self.assertTrue(records)
        for record in records:
            with self.subTest(record=type(record).__name__):
                self.assertFalse(hasattr(record, "__dict__"))
                self.assertFalse(dataclasses.is_dataclass(record))
                for slot in type(record).__slots__:
                    value = object.__getattribute__(record, slot)
                    self.assertNotIn(type(value), (dict, list, set, bytearray))
                    if type(value) is tuple:
                        self.assertIs(type(value), tuple)

    def test_records_are_immutable_nonsubclassable_and_not_serializable(self):
        for record in _record_graph():
            record_type = type(record)
            field_name = record_type.__slots__[0]
            original = object.__getattribute__(record, field_name)
            with self.subTest(record=record_type.__name__):
                with self.assertRaises(ValueError):
                    setattr(record, field_name, object())
                with self.assertRaises(ValueError):
                    delattr(record, field_name)
                self.assertIs(object.__getattribute__(record, field_name), original)
                with self.assertRaises(ValueError):
                    type("Derived", (record_type,), {})
                self.assertIs(copy.copy(record), record)
                self.assertIs(copy.deepcopy(record), record)
                self.assertEqual(str(record), record_type.__name__)
                self.assertEqual(repr(record), f"{record_type.__name__}(...)")
                with self.assertRaises(TypeError):
                    dataclasses.asdict(record)
                for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
                    with self.assertRaises(TypeError):
                        pickle.dumps(record, protocol=protocol)
                with self.assertRaises(TypeError):
                    record.__getstate__()

    def test_every_tuple_constructor_input_is_exact(self):
        class TupleSubclass(tuple):
            pass

        for record in _record_graph():
            values = _record_values(record)
            for index, value in enumerate(values):
                if type(value) is not tuple:
                    continue
                for replacement in (list(value), TupleSubclass(value)):
                    arguments = list(values)
                    arguments[index] = replacement
                    with self.subTest(
                        record=type(record).__name__,
                        slot=type(record).__slots__[index],
                        replacement=type(replacement).__name__,
                    ):
                        with self.assertRaises(ValueError):
                            type(record)(*arguments)

    def test_constructor_failures_are_fixed_and_ignore_adversarial_values(self):
        private_marker = "private-adversarial-constructor-value"

        class AdversarialValue:
            def forbidden(self, *_arguments, **_keywords):
                raise AssertionError(private_marker)

            __bool__ = forbidden
            __eq__ = forbidden
            __hash__ = forbidden
            __iter__ = forbidden
            __repr__ = forbidden
            __str__ = forbidden

        value = AdversarialValue()
        factories = (
            lambda: contract.RelationalAccountRelation(value),
            lambda: contract.RelationalFieldManifest(value, True, False, True),
            lambda: contract.RequestSnapshotFieldManifest(value, "destination"),
            lambda: contract.RelationalVersionRequirement(value, 1, True, False),
        )
        for factory in factories:
            try:
                factory()
            except ValueError as error:
                self.assertEqual(error.args, ())
                self.assertEqual(
                    str(error),
                    "invalid relational account-store contract value",
                )
                self.assertNotIn(private_marker, repr(error))
                self.assertIsNone(error.__context__)
                self.assertIsNone(error.__cause__)
            else:
                self.fail("adversarial constructor value was accepted")

    def test_no_constructor_has_defaults(self):
        for record_type in RECORD_SLOTS:
            for parameter in inspect.signature(record_type.__init__).parameters.values():
                self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_exact_field_equality_requires_exactly_two_distinct_fields(self):
        category = contract.RelationalConstraintCategory.EXACT_FIELD_EQUALITY
        for field_names in ((), ("one",), ("one", "two", "three"), ("one", "one")):
            with self.subTest(field_names=field_names):
                with self.assertRaises(ValueError):
                    contract.RelationalInvariantManifest(category, field_names)
        manifest = contract.RelationalInvariantManifest(
            category,
            ("receipt_id", "snapshot_id"),
        )
        self.assertIs(manifest.category, category)
        self.assertEqual(manifest.field_names, ("receipt_id", "snapshot_id"))


class SchemaManifestTests(unittest.TestCase):
    def test_schema_and_all_version_axes_are_exactly_one(self):
        schema = contract.RELATIONAL_ACCOUNT_SCHEMA_1
        self.assertEqual(schema.contract_version, 1)
        self.assertEqual(schema.migration_stages, ("expand", "migrate", "verify", "contract"))
        self.assertFalse(schema.missing_authority_fields_receive_defaults)
        self.assertTrue(
            schema.application_rollback_requires_reader_writer_compatibility
        )
        self.assertEqual(
            tuple(item.category for item in schema.version_requirements),
            tuple(contract.RelationalVersionCategory),
        )
        for requirement in schema.version_requirements:
            self.assertEqual(requirement.supported_version, 1)
            self.assertTrue(requirement.unknown_newer_fails_closed)
            self.assertFalse(requirement.implicit_authority_defaults_allowed)
            self.assertTrue(
                contract.relational_version_is_supported(
                    requirement.category, requirement.supported_version
                )
            )
            self.assertFalse(
                contract.relational_version_is_supported(requirement.category, 2)
            )
        self.assertFalse(
            contract.relational_version_is_supported(
                contract.RelationalVersionCategory.RELATION_RECORD, True
            )
        )

        class EqualToEverything:
            def __init__(self):
                self.calls = 0

            def __eq__(self, _other):
                self.calls += 1
                return True

        category_equal = EqualToEverything()
        for category in (object(), [], "relation_record", category_equal):
            self.assertFalse(
                contract.relational_version_is_supported(category, 1)
            )
        self.assertEqual(category_equal.calls, 0)
        version_equal = EqualToEverything()
        for version in (
            0,
            -1,
            2,
            True,
            1.0,
            [],
            version_equal,
        ):
            self.assertFalse(
                contract.relational_version_is_supported(
                    contract.RelationalVersionCategory.RELATION_RECORD,
                    version,
                )
            )
        self.assertEqual(version_equal.calls, 0)

    def test_exact_seven_relations_and_all_exact_field_metadata(self):
        relations = contract.RELATIONAL_ACCOUNT_SCHEMA_1.relations
        self.assertEqual(
            tuple(relation.relation for relation in relations),
            tuple(contract.RelationalAccountRelation),
        )
        self.assertEqual(len(relations), 7)
        by_name = _relations_by_name()
        for name, expected_fields in EXPECTED_RELATION_FIELDS.items():
            with self.subTest(relation=name):
                relation = by_name[name]
                self.assertEqual(
                    tuple(
                        (
                            field.name,
                            field.required,
                            field.nullable,
                            field.immutable,
                        )
                        for field in relation.fields
                    ),
                    tuple(
                        (
                            field_name,
                            True,
                            field_name in EXPECTED_NULLABLE_FIELDS[name],
                            field_name in EXPECTED_IMMUTABLE_FIELDS[name],
                        )
                        for field_name in expected_fields
                    ),
                )
                for field in relation.fields:
                    self.assertIs(type(field.name), str)
                    self.assertIs(type(field.required), bool)
                    self.assertIs(type(field.nullable), bool)
                    self.assertIs(type(field.immutable), bool)

    def test_exact_primary_keys(self):
        for name, fields in EXPECTED_PRIMARY_KEYS.items():
            relation = _relations_by_name()[name]
            self.assertIs(
                relation.primary_key.category,
                contract.RelationalConstraintCategory.PRIMARY_KEY,
            )
            self.assertEqual(relation.primary_key.field_names, fields)
            field_map = {field.name: field for field in relation.fields}
            for field in fields:
                self.assertFalse(field_map[field].nullable)
                self.assertTrue(field_map[field].immutable)

    def test_foreign_keys_are_exact_and_same_user_links_are_explicit(self):
        for relation_name, expected_keys in EXPECTED_FOREIGN_KEYS.items():
            actual = tuple(
                (
                    foreign_key.categories,
                    foreign_key.field_names,
                    foreign_key.referenced_relation.value,
                    foreign_key.referenced_field_names,
                )
                for foreign_key in _relations_by_name()[relation_name].foreign_keys
            )
            self.assertEqual(actual, expected_keys)

    def test_unique_constraints_are_exact_for_every_relation(self):
        for relation_name, expected in EXPECTED_UNIQUE_CONSTRAINTS.items():
            actual = tuple(
                (
                    item.category,
                    item.field_names,
                    item.scope_field_names,
                )
                for item in _relations_by_name()[relation_name].unique_constraints
            )
            self.assertEqual(actual, expected)

    def test_invariants_and_every_receipt_snapshot_equality_are_exact(self):
        for relation_name, expected in EXPECTED_INVARIANTS.items():
            relation = _relations_by_name()[relation_name]
            self.assertEqual(
                tuple((item.category, item.field_names) for item in relation.invariants),
                expected,
            )

        operation_equalities = tuple(
            item.field_names
            for item in _relations_by_name()[
                "initial_account_operations"
            ].invariants
            if item.category
            is contract.RelationalConstraintCategory.EXACT_FIELD_EQUALITY
        )
        self.assertEqual(
            operation_equalities,
            (
                ("receipt_user_id", "snapshot_user_user_id"),
                (
                    "receipt_verified_email_id",
                    "snapshot_verified_email_email_id",
                ),
                (
                    "receipt_authentication_identity_id",
                    "snapshot_authentication_identity_identity_id",
                ),
                ("receipt_workspace_id", "snapshot_workspace_workspace_id"),
                (
                    "receipt_security_event_id",
                    "snapshot_security_event_event_id",
                ),
            ),
        )
        self.assertTrue(all(len(fields) == 2 for fields in operation_equalities))

    def test_relation_versions_and_all_other_metadata_are_exact(self):
        for relation_name, expected in EXPECTED_RELATION_METADATA.items():
            relation = _relations_by_name()[relation_name]
            self.assertEqual(relation.record_version, 1)
            self.assertEqual(
                (
                    relation.row_version_required,
                    relation.security_epoch_required,
                    relation.timestamp_fields,
                    relation.initial_creation_requirements,
                    relation.forbidden_fields,
                ),
                expected,
            )
            self.assertIs(type(relation.row_version_required), bool)
            self.assertIs(type(relation.security_epoch_required), bool)
            for values in (
                relation.timestamp_fields,
                relation.initial_creation_requirements,
                relation.forbidden_fields,
            ):
                self.assertIs(type(values), tuple)
                self.assertTrue(all(type(value) is str for value in values))
                self.assertEqual(len(values), len(set(values)))

    def test_constraint_categories_are_exact_and_bound_to_exact_fields(self):
        self.assertEqual(
            {member.name for member in contract.RelationalConstraintCategory},
            {
                name
                for name, _value in EXPECTED_ENUMS[
                    contract.RelationalConstraintCategory
                ]
            },
        )
        used = set()
        for relation in contract.RELATIONAL_ACCOUNT_SCHEMA_1.relations:
            used.add(relation.primary_key.category)
            for foreign_key in relation.foreign_keys:
                used.update(foreign_key.categories)
            used.update(item.category for item in relation.unique_constraints)
            used.update(item.category for item in relation.invariants)
        self.assertEqual(used, set(contract.RelationalConstraintCategory))

    def test_operation_and_event_relations_are_complete_and_append_only(self):
        relations = _relations_by_name()
        operation = relations["initial_account_operations"]
        event = relations["security_events"]
        self.assertEqual(
            tuple(field.name for field in operation.fields),
            _expected_operation_fields(),
        )
        self.assertEqual(
            tuple(field.name for field in event.fields),
            EXPECTED_SECURITY_EVENT_FIELDS,
        )
        self.assertEqual(len(operation.fields), 73)
        self.assertEqual(len(event.fields), 21)
        self.assertTrue(all(field.immutable for field in operation.fields))
        self.assertTrue(all(field.immutable for field in event.fields))
        for relation in (operation, event):
            self.assertIn(
                contract.RelationalConstraintCategory.APPEND_ONLY,
                {item.category for item in relation.invariants},
            )
            self.assertIn(
                "row_version_exactly_one",
                relation.initial_creation_requirements,
            )

    def test_sensitive_identifier_matching_is_fragment_aware(self):
        detected = (
            "snapshot_authentication_evidence_access_token",
            "snapshot_provider_refresh_token_digest",
            "snapshot_oauth_id_token_hash",
            "snapshot_raw_provider_payload",
            "snapshot_client_secret_digest",
            "snapshot_challenge_secret_digest",
            "snapshot_pkce_verifier_digest",
            "workspace_product",
        )
        for identifier in detected:
            self.assertTrue(
                any(
                    _contains_identifier(identifier, forbidden)
                    for forbidden in SENSITIVE_SCHEMA_IDENTIFIERS
                ),
                identifier,
            )

        legitimate = (
            "operation_digest",
            "verification_coordinator_id",
            "security_event_id",
            "secretary_display_name",
            "productivity_label",
            "subscriptionless_marker",
            "sessionless_operation",
        )
        for identifier in legitimate:
            self.assertFalse(
                any(
                    _contains_identifier(identifier, forbidden)
                    for forbidden in SENSITIVE_SCHEMA_IDENTIFIERS
                ),
                identifier,
            )

    def test_forbidden_authority_product_and_secret_fields_are_absent(self):
        inspected_names = tuple(
            field.name
            for relation in contract.RELATIONAL_ACCOUNT_SCHEMA_1.relations
            for field in relation.fields
        ) + tuple(
            value
            for snapshot_field in contract.RELATIONAL_ACCOUNT_SCHEMA_1.request_snapshot.fields
            for value in (
                snapshot_field.source_path,
                snapshot_field.operation_field_name,
            )
        )
        for name in inspected_names:
            for forbidden in SENSITIVE_SCHEMA_IDENTIFIERS:
                self.assertFalse(
                    _contains_identifier(name, forbidden),
                    f"{name!r} contains forbidden identifier {forbidden!r}",
                )
        self.assertEqual(
            set(contract.RelationalAccountRelation),
            {
                contract.RelationalAccountRelation.USERS,
                contract.RelationalAccountRelation.VERIFIED_EMAILS,
                contract.RelationalAccountRelation.AUTHENTICATION_IDENTITIES,
                contract.RelationalAccountRelation.WORKSPACES,
                contract.RelationalAccountRelation.WORKSPACE_MEMBERSHIPS,
                contract.RelationalAccountRelation.INITIAL_ACCOUNT_OPERATIONS,
                contract.RelationalAccountRelation.SECURITY_EVENTS,
            },
        )

    def test_schema_validator_rejects_unknown_version_and_duck_types(self):
        class DuckRelation:
            relation = contract.RelationalAccountRelation.USERS

        schema = contract.RELATIONAL_ACCOUNT_SCHEMA_1
        self.assertTrue(contract.relational_schema_manifest_is_valid(schema))
        candidate = _corrupt_record(schema, "contract_version", 2)
        self.assertFalse(contract.relational_schema_manifest_is_valid(candidate))
        for rejected in (object(), [], (), {"contract_version": 1}):
            self.assertFalse(
                contract.relational_schema_manifest_is_valid(rejected)
            )
        wrong_record_type = _corrupt_record(
            schema,
            "relations",
            (schema.version_requirements[0], *schema.relations[1:]),
        )
        duck_record = _corrupt_record(
            schema,
            "relations",
            (DuckRelation(), *schema.relations[1:]),
        )
        for candidate in (wrong_record_type, duck_record):
            self.assertFalse(
                contract.relational_schema_manifest_is_valid(candidate)
            )

    def test_schema_validator_rejects_same_version_nested_corruption(self):
        schema = contract.RELATIONAL_ACCOUNT_SCHEMA_1
        relations = _relations_by_name()

        user = relations["users"]
        corrupt_user_field = _corrupt_record(user.fields[0], "nullable", True)
        corrupt_user_fields = _copy_record_with(
            user,
            fields=(corrupt_user_field, *user.fields[1:]),
        )

        corrupt_user_primary_key = _copy_record_with(
            user,
            primary_key=_corrupt_record(
                user.primary_key,
                "field_names",
                ("status",),
            ),
        )

        corrupt_user_foreign_key = _corrupt_record(
            user.foreign_keys[0],
            "categories",
            _FOREIGN_KEY,
        )
        corrupt_user_foreign_keys = _copy_record_with(
            user,
            foreign_keys=(corrupt_user_foreign_key,),
        )

        operation = relations["initial_account_operations"]
        corrupt_operation_unique = _corrupt_record(
            operation.unique_constraints[0],
            "field_names",
            ("operation_digest",),
        )
        corrupt_operation_uniques = _copy_record_with(
            operation,
            unique_constraints=(
                corrupt_operation_unique,
                *operation.unique_constraints[1:],
            ),
        )

        corrupt_operation_invariant = _corrupt_record(
            operation.invariants[0],
            "field_names",
            ("row_version",),
        )
        corrupt_operation_invariants = _copy_record_with(
            operation,
            invariants=(
                corrupt_operation_invariant,
                *operation.invariants[1:],
            ),
        )

        corrupt_event_metadata = _corrupt_record(
            relations["security_events"],
            "timestamp_fields",
            ("recorded_at", "event_at"),
        )

        version = _corrupt_record(
            schema.version_requirements[0],
            "unknown_newer_fails_closed",
            False,
        )
        corrupt_versions = _copy_record_with(
            schema,
            version_requirements=(version, *schema.version_requirements[1:]),
        )

        snapshot_field = _corrupt_record(
            schema.request_snapshot.fields[0],
            "operation_field_name",
            "row_version",
        )
        snapshot = _copy_record_with(
            schema.request_snapshot,
            fields=(snapshot_field, *schema.request_snapshot.fields[1:]),
        )
        corrupt_snapshot = _copy_record_with(schema, request_snapshot=snapshot)

        transaction = _corrupt_record(
            schema.initial_account_transaction,
            "exact_replay_writes",
            True,
        )
        corrupt_transaction = _copy_record_with(
            schema,
            initial_account_transaction=transaction,
        )

        read_manifest = schema.consistent_read_requirement
        corrupt_required_content = _copy_record_with(
            schema,
            consistent_read_requirement=_corrupt_record(
                read_manifest,
                "required_facts",
                (*EXPECTED_CONSISTENT_READ_REQUIRED_FACTS, "workspace"),
            ),
        )
        corrupt_required_type = _copy_record_with(
            schema,
            consistent_read_requirement=_corrupt_record(
                read_manifest,
                "required_facts",
                list(EXPECTED_CONSISTENT_READ_REQUIRED_FACTS),
            ),
        )
        corrupt_forbidden_content = _copy_record_with(
            schema,
            consistent_read_requirement=_corrupt_record(
                read_manifest,
                "forbidden_facts",
                EXPECTED_CONSISTENT_READ_FORBIDDEN_FACTS[:-1],
            ),
        )
        corrupt_forbidden_type = _copy_record_with(
            schema,
            consistent_read_requirement=_corrupt_record(
                read_manifest,
                "forbidden_facts",
                list(EXPECTED_CONSISTENT_READ_FORBIDDEN_FACTS),
            ),
        )

        corrupt_migration_stages = _corrupt_record(
            schema,
            "migration_stages",
            ("expand", "verify", "migrate", "contract"),
        )

        candidates = (
            _schema_with_relation(corrupt_user_fields),
            _schema_with_relation(corrupt_user_primary_key),
            _schema_with_relation(corrupt_user_foreign_keys),
            _schema_with_relation(corrupt_operation_uniques),
            _schema_with_relation(corrupt_operation_invariants),
            _schema_with_relation(corrupt_event_metadata),
            corrupt_versions,
            corrupt_snapshot,
            corrupt_transaction,
            corrupt_required_content,
            corrupt_required_type,
            corrupt_forbidden_content,
            corrupt_forbidden_type,
            corrupt_migration_stages,
        )
        for index, candidate in enumerate(candidates):
            with self.subTest(candidate_index=index):
                self.assertEqual(candidate.contract_version, 1)
                self.assertFalse(
                    contract.relational_schema_manifest_is_valid(candidate)
                )

    def test_schema_validator_rejects_list_container_corruption(self):
        schema = contract.RELATIONAL_ACCOUNT_SCHEMA_1
        candidates = []
        for slot in ("relations", "version_requirements"):
            candidate = _clone_record(schema)
            object.__setattr__(candidate, slot, list(getattr(candidate, slot)))
            candidates.append(candidate)

        user = _relations_by_name()["users"]
        for slot in (
            "fields",
            "foreign_keys",
            "unique_constraints",
            "invariants",
        ):
            relation = _clone_record(user)
            object.__setattr__(relation, slot, list(getattr(relation, slot)))
            candidates.append(_schema_with_relation(relation))

        for candidate in candidates:
            self.assertFalse(contract.relational_schema_manifest_is_valid(candidate))

    def test_schema_validator_rejects_deleted_candidate_slots(self):
        schema = _clone_record(contract.RELATIONAL_ACCOUNT_SCHEMA_1)
        object.__delattr__(schema, "contract_version")
        self.assertFalse(contract.relational_schema_manifest_is_valid(schema))

        user = _clone_record(_relations_by_name()["users"])
        object.__delattr__(user, "fields")
        self.assertFalse(
            contract.relational_schema_manifest_is_valid(
                _schema_with_relation(user)
            )
        )

        user = _relations_by_name()["users"]
        field = _clone_record(user.fields[0])
        object.__delattr__(field, "nullable")
        relation = _copy_record_with(
            user,
            fields=(field, *user.fields[1:]),
        )
        self.assertFalse(
            contract.relational_schema_manifest_is_valid(
                _schema_with_relation(relation)
            )
        )

    def test_schema_validator_cannot_be_spoofed_by_adversarial_equality(self):
        class EqualToEverything:
            def __init__(self):
                self.calls = 0

            def __eq__(self, _other):
                self.calls += 1
                return True

            def __ne__(self, _other):
                self.calls += 1
                return False

        schema = contract.RELATIONAL_ACCOUNT_SCHEMA_1
        value = EqualToEverything()
        user = _corrupt_record(_relations_by_name()["users"], "record_version", value)
        transaction = _corrupt_record(
            schema.initial_account_transaction,
            "contract_version",
            value,
        )
        consistent_read = _corrupt_record(
            schema.consistent_read_requirement,
            "contract_version",
            value,
        )
        candidates = (
            _corrupt_record(schema, "contract_version", value),
            _schema_with_relation(user),
            _copy_record_with(schema, initial_account_transaction=transaction),
            _copy_record_with(schema, consistent_read_requirement=consistent_read),
        )
        for candidate in candidates:
            self.assertFalse(contract.relational_schema_manifest_is_valid(candidate))
        self.assertEqual(value.calls, 0)

    def test_schema_validator_does_not_invoke_failing_wrong_type_equality(self):
        class EqualityStop(BaseException):
            pass

        class FailingEquality:
            def __init__(self, failure):
                self.calls = 0
                self.failure = failure

            def fail(self, _other):
                self.calls += 1
                raise self.failure

            __eq__ = fail
            __ne__ = fail

        schema = contract.RELATIONAL_ACCOUNT_SCHEMA_1
        failures = (
            RuntimeError("wrong-type ordinary equality was invoked"),
            EqualityStop("wrong-type base equality was invoked"),
        )
        for failure in failures:
            with self.subTest(failure_type=type(failure).__name__):
                value = FailingEquality(failure)
                candidate = _corrupt_record(schema, "contract_version", value)
                self.assertFalse(
                    contract.relational_schema_manifest_is_valid(candidate)
                )
                self.assertEqual(value.calls, 0)

    def _request_type_with_slots_failure(self, exception, calls):
        class SlotsFailureMeta(type):
            def __getattribute__(cls, name):
                if name == "__slots__":
                    calls.append(name)
                    raise exception
                return type.__getattribute__(cls, name)

        return SlotsFailureMeta("FailingInitialAccountCreationRequest", (), {})

    def _assert_snapshot_dependency_failure_propagates(self, failure):
        calls = []
        snapshot = contract.RELATIONAL_ACCOUNT_SCHEMA_1.request_snapshot
        original = account_contract.InitialAccountCreationRequest
        try:
            account_contract.InitialAccountCreationRequest = (
                self._request_type_with_slots_failure(failure, calls)
            )
            with self.assertRaises(type(failure)) as caught:
                contract.request_snapshot_covers_initial_account_request(snapshot)
            self.assertIs(caught.exception, failure)
        finally:
            account_contract.InitialAccountCreationRequest = original
        self.assertEqual(calls, ["__slots__"])

    def _assert_schema_helper_failure_propagates(self, failure):
        calls = []
        schema = contract.RELATIONAL_ACCOUNT_SCHEMA_1
        original = contract.request_snapshot_covers_initial_account_request

        def fail(snapshot):
            calls.append(snapshot)
            raise failure

        try:
            contract.request_snapshot_covers_initial_account_request = fail
            with self.assertRaises(type(failure)) as caught:
                contract.relational_schema_manifest_is_valid(schema)
            self.assertIs(caught.exception, failure)
        finally:
            contract.request_snapshot_covers_initial_account_request = original
        self.assertEqual(calls, [schema.request_snapshot])

    def test_snapshot_validator_propagates_unexpected_exception_by_identity(self):
        failure = RuntimeError("unexpected snapshot dependency failure")
        self._assert_snapshot_dependency_failure_propagates(failure)

    def test_schema_validator_propagates_unexpected_exception_by_identity(self):
        failure = RuntimeError("unexpected schema helper failure")
        self._assert_schema_helper_failure_propagates(failure)

    def test_snapshot_validator_propagates_baseexception_by_identity(self):
        class StopValidation(BaseException):
            pass

        failure = StopValidation("stop snapshot validation")
        self._assert_snapshot_dependency_failure_propagates(failure)

    def test_schema_validator_propagates_baseexception_by_identity(self):
        class StopValidation(BaseException):
            pass

        failure = StopValidation("stop schema validation")
        self._assert_schema_helper_failure_propagates(failure)

    def test_public_validators_have_only_narrow_exception_handlers(self):
        tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))
        validator_names = {
            "request_snapshot_covers_initial_account_request",
            "relational_schema_manifest_is_valid",
        }
        validators = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in validator_names
        }
        self.assertEqual(set(validators), validator_names)

        for validator_name, validator in validators.items():
            for handler in (
                node
                for node in ast.walk(validator)
                if isinstance(node, ast.ExceptHandler)
            ):
                with self.subTest(validator=validator_name):
                    self.assertIsNotNone(handler.type)
                    caught = (
                        tuple(handler.type.elts)
                        if isinstance(handler.type, ast.Tuple)
                        else (handler.type,)
                    )
                    self.assertEqual(len(caught), 1)
                    names = {
                        node.id
                        for node in ast.walk(handler.type)
                        if isinstance(node, ast.Name)
                    } | {
                        node.attr
                        for node in ast.walk(handler.type)
                        if isinstance(node, ast.Attribute)
                    }
                    self.assertTrue(
                        names.isdisjoint(
                            {
                                "Exception",
                                "BaseException",
                                "RuntimeError",
                                "ValueError",
                                "TypeError",
                            }
                        )
                    )
                    caught_name = caught[0]
                    self.assertIsInstance(caught_name, (ast.Name, ast.Attribute))
                    terminal_name = (
                        caught_name.id
                        if isinstance(caught_name, ast.Name)
                        else caught_name.attr
                    )
                    self.assertTrue(terminal_name.startswith("_"))


class RequestSnapshotTests(unittest.TestCase):
    def test_snapshot_covers_every_current_request_leaf_exactly_once(self):
        snapshot = contract.RELATIONAL_ACCOUNT_SCHEMA_1.request_snapshot
        expected = _expected_snapshot_pairs()
        actual = tuple(
            (field.source_path, field.operation_field_name)
            for field in snapshot.fields
        )
        self.assertEqual(snapshot.version, 1)
        self.assertEqual(
            snapshot.request_field_names,
            account_contract.InitialAccountCreationRequest.__slots__,
        )
        self.assertEqual(len(actual), 63)
        self.assertEqual(actual, expected)
        self.assertEqual(len({source for source, _destination in actual}), 63)
        self.assertEqual(len({destination for _source, destination in actual}), 63)
        self.assertTrue(
            contract.request_snapshot_covers_initial_account_request(snapshot)
        )
        self.assertIn(
            (
                "authentication_identity.method",
                "snapshot_authentication_identity_authentication_method",
            ),
            actual,
        )

    def test_snapshot_destinations_are_operation_fields_and_have_no_blob_encoding(self):
        relations = _relations_by_name()
        operation_fields = {
            field.name for field in relations["initial_account_operations"].fields
        }
        for item in contract.RELATIONAL_ACCOUNT_SCHEMA_1.request_snapshot.fields:
            self.assertIn(item.operation_field_name, operation_fields)
        for forbidden in (
            "request_blob",
            "request_json",
            "serialized_request",
            "storage_encoding",
            "request_digest_only",
        ):
            self.assertNotIn(forbidden, operation_fields)

    def test_snapshot_helper_rejects_same_version_corruption_lists_and_ducks(self):
        snapshot = contract.RELATIONAL_ACCOUNT_SCHEMA_1.request_snapshot
        wrong_mapping = _corrupt_record(
            snapshot.fields[0],
            "operation_field_name",
            "row_version",
        )
        duplicate_mapping = _corrupt_record(
            snapshot.fields[1],
            "operation_field_name",
            snapshot.fields[0].operation_field_name,
        )
        candidates = (
            _corrupt_record(snapshot, "version", 2),
            _corrupt_record(
                snapshot,
                "request_field_names",
                tuple(reversed(snapshot.request_field_names)),
            ),
            _corrupt_record(
                snapshot,
                "fields",
                (wrong_mapping, *snapshot.fields[1:]),
            ),
            _corrupt_record(
                snapshot,
                "fields",
                (snapshot.fields[0], duplicate_mapping, *snapshot.fields[2:]),
            ),
            _corrupt_record(snapshot, "fields", snapshot.fields[:-1]),
            _corrupt_record(snapshot, "fields", list(snapshot.fields)),
            _corrupt_record(
                snapshot,
                "fields",
                (
                    contract.RELATIONAL_ACCOUNT_SCHEMA_1.version_requirements[0],
                    *snapshot.fields[1:],
                ),
            ),
            _corrupt_record(
                snapshot,
                "request_field_names",
                list(snapshot.request_field_names),
            ),
        )
        for candidate in candidates:
            self.assertFalse(
                contract.request_snapshot_covers_initial_account_request(candidate)
            )
        for rejected in (object(), [], (), {"version": 1}):
            self.assertFalse(
                contract.request_snapshot_covers_initial_account_request(rejected)
            )

    def test_snapshot_helper_rejects_deleted_candidate_slots(self):
        snapshot = _clone_record(
            contract.RELATIONAL_ACCOUNT_SCHEMA_1.request_snapshot
        )
        object.__delattr__(snapshot, "fields")
        self.assertFalse(
            contract.request_snapshot_covers_initial_account_request(snapshot)
        )

        original = contract.RELATIONAL_ACCOUNT_SCHEMA_1.request_snapshot
        field = _clone_record(original.fields[0])
        object.__delattr__(field, "source_path")
        snapshot = _copy_record_with(
            original,
            fields=(field, *original.fields[1:]),
        )
        self.assertFalse(
            contract.request_snapshot_covers_initial_account_request(snapshot)
        )

    def test_snapshot_helper_cannot_be_spoofed_by_adversarial_equality(self):
        class EqualToEverything:
            def __init__(self):
                self.calls = 0

            def __eq__(self, _other):
                self.calls += 1
                return True

            def __ne__(self, _other):
                self.calls += 1
                return False

        value = EqualToEverything()
        candidate = _corrupt_record(
            contract.RELATIONAL_ACCOUNT_SCHEMA_1.request_snapshot,
            "request_field_names",
            value,
        )
        self.assertFalse(
            contract.request_snapshot_covers_initial_account_request(candidate)
        )
        self.assertEqual(value.calls, 0)

    def test_snapshot_helper_does_not_invoke_failing_wrong_type_equality(self):
        class EqualityStop(BaseException):
            pass

        class FailingEquality:
            def __init__(self, failure):
                self.calls = 0
                self.failure = failure

            def fail(self, _other):
                self.calls += 1
                raise self.failure

            __eq__ = fail
            __ne__ = fail

        snapshot = contract.RELATIONAL_ACCOUNT_SCHEMA_1.request_snapshot
        failures = (
            RuntimeError("wrong-type ordinary equality was invoked"),
            EqualityStop("wrong-type base equality was invoked"),
        )
        for failure in failures:
            with self.subTest(failure_type=type(failure).__name__):
                value = FailingEquality(failure)
                candidate = _corrupt_record(
                    snapshot,
                    "request_field_names",
                    value,
                )
                self.assertFalse(
                    contract.request_snapshot_covers_initial_account_request(
                        candidate
                    )
                )
                self.assertEqual(value.calls, 0)


class TransactionAndReadTests(unittest.TestCase):
    def test_initial_account_transaction_has_closed_safe_semantics(self):
        transaction = (
            contract.RELATIONAL_ACCOUNT_SCHEMA_1.initial_account_transaction
        )
        self.assertEqual(transaction.contract_version, 1)
        self.assertEqual(transaction.relations, tuple(contract.RelationalAccountRelation))
        self.assertTrue(transaction.all_or_nothing_visibility)
        self.assertTrue(transaction.operation_lookup_before_current_policy)
        self.assertFalse(transaction.exact_replay_writes)
        self.assertIs(
            transaction.mismatch_outcome,
            account_contract.InitialAccountCreationOutcome.CONFLICT,
        )
        self.assertTrue(transaction.failure_rolls_back_all)
        self.assertTrue(transaction.event_failure_rolls_back_all)
        self.assertTrue(transaction.exactly_one_initial_event)
        self.assertTrue(transaction.operation_and_aggregate_visible_together)
        self.assertFalse(transaction.pending_record_allowed)
        self.assertTrue(transaction.created_requires_confirmed_commit)
        self.assertIs(
            transaction.unknown_commit_outcome,
            account_contract.InitialAccountCreationOutcome.AMBIGUOUS,
        )
        self.assertTrue(transaction.unavailable_requires_known_no_commit)
        self.assertTrue(
            transaction.internal_error_requires_known_nonambiguous_failure
        )

    def test_consistent_read_manifest_contains_only_authentication_facts(self):
        read = contract.RELATIONAL_ACCOUNT_SCHEMA_1.consistent_read_requirement
        self.assertEqual(read.contract_version, 1)
        self.assertEqual(
            read.categories, tuple(contract.ConsistentReadRequirementCategory)
        )
        self.assertEqual(
            read.required_facts,
            EXPECTED_CONSISTENT_READ_REQUIRED_FACTS,
        )
        self.assertEqual(
            read.forbidden_facts,
            EXPECTED_CONSISTENT_READ_FORBIDDEN_FACTS,
        )
        self.assertEqual(
            read.future_blockers,
            EXPECTED_CONSISTENT_READ_FUTURE_BLOCKERS,
        )
        self.assertIs(type(read.categories), tuple)
        self.assertTrue(
            all(
                type(category) is contract.ConsistentReadRequirementCategory
                for category in read.categories
            )
        )
        for values in (
            read.required_facts,
            read.forbidden_facts,
            read.future_blockers,
        ):
            self.assertIs(type(values), tuple)
            self.assertTrue(all(type(value) is str for value in values))
            self.assertEqual(len(values), len(set(values)))
        self.assertTrue(
            set(read.required_facts).isdisjoint(read.forbidden_facts)
        )
        self.assertFalse(
            hasattr(auth_models.StoredSessionSnapshot, "lookup_key_epoch")
        )
        self.assertFalse(
            hasattr(auth_models.StoredSessionSnapshot, "binding_key_epoch")
        )


class InactivityAndDocumentationTests(unittest.TestCase):
    def test_source_imports_and_active_surfaces_are_closed(self):
        source = _SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(("import", alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.extend(
                    ("from", node.level, node.module, alias.name)
                    for alias in node.names
                )
        self.assertCountEqual(
            imports,
            (
                ("import", "sys"),
                ("from", 0, "enum", "Enum"),
                ("from", 0, "enum", "EnumMeta"),
                ("from", 0, "api.auth", "models"),
                (
                    "from",
                    0,
                    "cuevion_auth",
                    "account_repository_contract",
                ),
            ),
        )
        forbidden_import_roots = {
            "os",
            "pathlib",
            "time",
            "datetime",
            "random",
            "secrets",
            "logging",
            "socket",
            "subprocess",
            "urllib",
            "http",
            "requests",
            "redis",
            "sqlalchemy",
        }
        for item in imports:
            module = item[1] if item[0] == "import" else item[2]
            if module:
                self.assertNotIn(module.split(".")[0], forbidden_import_roots)
        for name in (
            "handler",
            "route",
            "router",
            "app",
            "server",
            "connection",
            "repository_adapter",
        ):
            self.assertNotIn(name, vars(contract))

    def test_no_tracked_active_api_module_imports_relational_contract(self):
        repository = _FRONTEND_DIRECTORY.parent
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--", "frontend/api"],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )
        tracked_python_paths = tuple(
            Path(value)
            for value in completed.stdout.split("\0")
            if value
            and value.endswith(".py")
            and not Path(value).name.startswith("test_")
            and "tests" not in Path(value).parts
        )
        self.assertTrue(tracked_python_paths)

        canonical_name = "cuevion_auth.relational_account_store_contract"
        short_name = "relational_account_store_contract"
        schema_name = "RELATIONAL_ACCOUNT_SCHEMA_1"
        imports = []
        for relative_path in tracked_python_paths:
            source = (repository / relative_path).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(relative_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(alias.name == canonical_name for alias in node.names):
                        imports.append(str(relative_path))
                elif isinstance(node, ast.ImportFrom):
                    if node.module == canonical_name or (
                        node.module == "cuevion_auth"
                        and any(alias.name == short_name for alias in node.names)
                    ) or any(alias.name == schema_name for alias in node.names):
                        imports.append(str(relative_path))
                elif isinstance(node, ast.Name) and node.id == schema_name:
                    imports.append(str(relative_path))
                elif isinstance(node, ast.Attribute) and node.attr == schema_name:
                    imports.append(str(relative_path))
                elif (
                    isinstance(node, ast.Constant)
                    and type(node.value) is str
                    and node.value in (canonical_name, short_name, schema_name)
                ):
                    imports.append(str(relative_path))
        self.assertEqual(imports, [])

    def test_cold_import_has_no_output_or_forbidden_runtime_access(self):
        program = """
import importlib
import io
import logging
import os
import random
import secrets
import socket
import sys
import time

def blocked(*_arguments, **_keywords):
    raise AssertionError('forbidden side effect')

os.getenv = blocked
os.urandom = blocked
time.time = blocked
time.monotonic = blocked
random.random = blocked
secrets.token_bytes = blocked
socket.socket = blocked
logging.getLogger = blocked

captured_out = io.StringIO()
captured_err = io.StringIO()
original_out = sys.stdout
original_err = sys.stderr
try:
    sys.stdout = captured_out
    sys.stderr = captured_err
    module = importlib.import_module(
        'cuevion_auth.relational_account_store_contract'
    )
finally:
    sys.stdout = original_out
    sys.stderr = original_err

assert module.relational_schema_manifest_is_valid(
    module.RELATIONAL_ACCOUNT_SCHEMA_1
)
assert captured_out.getvalue() == ''
assert captured_err.getvalue() == ''
for name in ('handler', 'route', 'router', 'app', 'server'):
    assert not hasattr(module, name)
"""
        completed = subprocess.run(
            [sys.executable, "-c", program],
            cwd=_FRONTEND_DIRECTORY,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

    def test_module_test_and_document_are_outside_vercel_function_glob(self):
        configuration = json.loads(
            (_FRONTEND_DIRECTORY / "vercel.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(configuration["functions"]), {"api/**/*.py"})
        for relative_path in (
            "cuevion_auth/relational_account_store_contract.py",
            "cuevion_auth/RELATIONAL_ACCOUNT_STORE_ACTIVATION_REQUIREMENTS.md",
            "tests/cuevion_auth/test_relational_account_store_contract.py",
        ):
            self.assertTrue(
                all(
                    not PurePosixPath(relative_path).match(pattern)
                    for pattern in configuration["functions"]
                )
            )

    def test_requirements_are_unchanged_and_have_no_storage_dependency(self):
        requirements = tuple(
            line.strip()
            for line in (_FRONTEND_DIRECTORY / "requirements.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        )
        self.assertEqual(requirements, ("cryptography~=46.0.0",))
        normalized = " ".join(requirements).casefold()
        for forbidden in (
            "sqlalchemy",
            "django",
            "psycopg",
            "mysql",
            "redis",
            "alembic",
        ):
            self.assertNotIn(forbidden, normalized)

    def test_activation_document_contains_all_required_boundaries(self):
        documentation = _DOCUMENTATION_PATH.read_text(encoding="utf-8")
        normalized = " ".join(documentation.casefold().split())
        required = (
            "completely inactive",
            "exactly seven authority relations",
            "database-enforced invariants",
            "repository-enforced invariants",
            "coordinator and policy invariants outside storage",
            "atomic initial-account creation",
            "complete operation reference",
            "lossless",
            "security_events",
            "event write failure fails the transaction",
            "`created`",
            "`exact_replay`",
            "`conflict`",
            "`ambiguous`",
            "`unavailable`",
            "`internal_error`",
            "ambiguous commit reconciliation",
            "forward-only expand, migrate, and contract",
            "reader/writer compatibility",
            "consistent account-authentication read boundary",
            "lookup-key and binding-key epochs",
            "email client",
            "organizer",
            "bundle",
            "multi-host login and session boundary",
            "database vendor",
            "orm",
            "driver",
            "ddl",
            "concrete repository adapter",
            "nothing in this document activates authentication",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, normalized)

    def test_existing_activation_document_has_only_the_required_cross_reference(self):
        documentation = _ACCOUNT_DOCUMENTATION_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "`RELATIONAL_ACCOUNT_STORE_ACTIVATION_REQUIREMENTS.md`",
            documentation,
        )
        self.assertIn("inactive logical schema", documentation)
        self.assertIn("migration-compatibility", documentation)
        self.assertIn("consistent-read requirements", documentation)


if __name__ == "__main__":
    unittest.main()
