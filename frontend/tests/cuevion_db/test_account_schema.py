"""Static PostgreSQL-dialect tests for account schema one."""

import ast
from pathlib import Path
import re
import unittest

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import BindParameter

from cuevion_auth import relational_account_store_contract as relational
from cuevion_db import account_schema as schema
from cuevion_db.metadata import metadata


_FRONTEND = Path(__file__).resolve().parents[2]
_SOURCE = _FRONTEND / "cuevion_db" / "account_schema.py"

_EXPECTED_EMAIL_PATTERN = (
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:[.][a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:[.][a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_EXPECTED_EMAIL_CHECKS = {
    ("verified_emails", "ck_verified_emails_canonical_email_canonical"),
    (
        "initial_account_operations",
        "ck_initial_account_operations_snapshot_verified_email__48e09ecb",
    ),
    (
        "initial_account_operations",
        "ck_initial_account_operations_snapshot_authentication__90799ed9",
    ),
}
_EXPECTED_AUTHORITY_ASCII_CHECKS = {
    (
        "authentication_identities",
        "ck_authentication_identities_issuer_ascii",
    ): "issuer",
    (
        "authentication_identities",
        "ck_authentication_identities_subject_ascii",
    ): "subject",
    (
        "initial_account_operations",
        "ck_initial_account_operations_snapshot_authentication__04890265",
    ): "snapshot_authentication_identity_issuer",
    (
        "initial_account_operations",
        "ck_initial_account_operations_snapshot_authentication__b9d2e30f",
    ): "snapshot_authentication_identity_subject",
    (
        "initial_account_operations",
        "ck_initial_account_operations_snapshot_authentication__0c556b23",
    ): "snapshot_authentication_evidence_issuer",
    (
        "initial_account_operations",
        "ck_initial_account_operations_snapshot_authentication__525f7f77",
    ): "snapshot_authentication_evidence_subject",
}
_POSTGRESQL_ARE_MAX_COUNT = 255
_POSTGRESQL_REGEX_LITERAL_PATTERN = re.compile(
    r"~\s*'(?P<pattern>(?:''|[^'])*)'",
)
_POSTGRESQL_COUNTED_REPETITION_PATTERN = re.compile(
    r"(?<!\\)\{(?P<minimum>\d+)(?:(?P<comma>,)(?P<maximum>\d*))?\}",
)

_EXPECTED_PRIMARY_KEYS = {
    "users": ("pk_users", ("user_id",)),
    "verified_emails": ("pk_verified_emails", ("email_id",)),
    "authentication_identities": ("pk_auth_identities", ("identity_id",)),
    "workspaces": ("pk_workspaces", ("workspace_id",)),
    "workspace_memberships": (
        "pk_workspace_memberships",
        ("workspace_id", "user_id"),
    ),
    "initial_account_operations": (
        "pk_initial_account_operations",
        ("reference_schema_version", "derivation_key_epoch", "operation_digest"),
    ),
    "security_events": ("pk_security_events", ("event_id",)),
}
_EXPECTED_UNIQUES = {
    ("verified_emails", "uq_verified_emails_id_user"): ("email_id", "user_id"),
    ("authentication_identities", "uq_auth_identities_id_user"): (
        "identity_id",
        "user_id",
    ),
    ("authentication_identities", "uq_auth_identities_issuer_subject"): (
        "issuer",
        "subject",
    ),
    ("workspaces", "uq_workspaces_id_creator"): (
        "workspace_id",
        "created_by_user_id",
    ),
    ("initial_account_operations", "uq_initial_ops_evidence_assertion"): (
        "snapshot_authentication_evidence_trust_domain",
        "snapshot_authentication_evidence_verification_coordinator_id",
        "snapshot_authentication_evidence_assertion_id",
    ),
    ("initial_account_operations", "uq_initial_ops_receipt_event"): (
        "receipt_security_event_id",
    ),
    ("initial_account_operations", "uq_initial_ops_event_binding"): (
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
    ("security_events", "uq_security_events_operation_ref"): (
        "reference_schema_version",
        "derivation_key_epoch",
        "operation_digest",
    ),
    ("security_events", "uq_security_events_stream_position"): (
        "event_stream_name",
        "event_stream_position",
    ),
}
_EXPECTED_FOREIGN_KEYS = {
    ("users", "fk_users_primary_email_same_user"): (
        ("primary_verified_email_id", "user_id"),
        "verified_emails",
        ("email_id", "user_id"),
        True,
        "DEFERRED",
    ),
    ("verified_emails", "fk_verified_emails_user"): (
        ("user_id",), "users", ("user_id",), None, None,
    ),
    ("authentication_identities", "fk_auth_identities_user"): (
        ("user_id",), "users", ("user_id",), None, None,
    ),
    (
        "authentication_identities",
        "fk_auth_identities_verified_email_same_user",
    ): (
        ("verified_email_id", "user_id"),
        "verified_emails",
        ("email_id", "user_id"),
        None,
        None,
    ),
    ("workspaces", "fk_workspaces_creator"): (
        ("created_by_user_id",), "users", ("user_id",), None, None,
    ),
    ("workspace_memberships", "fk_workspace_memberships_workspace"): (
        ("workspace_id",), "workspaces", ("workspace_id",), None, None,
    ),
    ("workspace_memberships", "fk_workspace_memberships_user"): (
        ("user_id",), "users", ("user_id",), None, None,
    ),
    ("initial_account_operations", "fk_initial_ops_receipt_user"): (
        ("receipt_user_id",), "users", ("user_id",), None, None,
    ),
    ("initial_account_operations", "fk_initial_ops_receipt_email_user"): (
        ("receipt_verified_email_id", "receipt_user_id"),
        "verified_emails",
        ("email_id", "user_id"),
        None,
        None,
    ),
    ("initial_account_operations", "fk_initial_ops_receipt_identity_user"): (
        ("receipt_authentication_identity_id", "receipt_user_id"),
        "authentication_identities",
        ("identity_id", "user_id"),
        None,
        None,
    ),
    ("initial_account_operations", "fk_initial_ops_receipt_workspace_creator"): (
        ("receipt_workspace_id", "receipt_user_id"),
        "workspaces",
        ("workspace_id", "created_by_user_id"),
        None,
        None,
    ),
    ("initial_account_operations", "fk_initial_ops_receipt_membership"): (
        ("receipt_workspace_id", "receipt_user_id"),
        "workspace_memberships",
        ("workspace_id", "user_id"),
        None,
        None,
    ),
    ("initial_account_operations", "fk_initial_ops_receipt_event"): (
        ("receipt_security_event_id",),
        "security_events",
        ("event_id",),
        True,
        "DEFERRED",
    ),
    ("security_events", "fk_security_events_operation_binding"): (
        (
            "reference_schema_version", "derivation_key_epoch", "operation_digest",
            "event_payload_version", "event_id", "event_type", "actor_trust_domain",
            "actor_verification_coordinator_id", "user_id", "verified_email_id",
            "authentication_identity_id", "workspace_id", "membership_workspace_id",
            "membership_user_id", "security_epoch",
        ),
        "initial_account_operations",
        (
            "reference_schema_version", "derivation_key_epoch", "operation_digest",
            "snapshot_security_event_schema_version", "snapshot_security_event_event_id",
            "snapshot_security_event_event_type",
            "snapshot_authentication_evidence_trust_domain",
            "snapshot_authentication_evidence_verification_coordinator_id",
            "snapshot_user_user_id", "snapshot_verified_email_email_id",
            "snapshot_authentication_identity_identity_id",
            "snapshot_workspace_workspace_id",
            "snapshot_workspace_membership_workspace_id",
            "snapshot_workspace_membership_user_id", "snapshot_user_security_epoch",
        ),
        None,
        None,
    ),
    ("security_events", "fk_security_events_user"): (
        ("user_id",), "users", ("user_id",), None, None,
    ),
    ("security_events", "fk_security_events_email_user"): (
        ("verified_email_id", "user_id"),
        "verified_emails",
        ("email_id", "user_id"),
        None,
        None,
    ),
    ("security_events", "fk_security_events_identity_user"): (
        ("authentication_identity_id", "user_id"),
        "authentication_identities",
        ("identity_id", "user_id"),
        None,
        None,
    ),
    ("security_events", "fk_security_events_workspace_creator"): (
        ("workspace_id", "user_id"),
        "workspaces",
        ("workspace_id", "created_by_user_id"),
        None,
        None,
    ),
    ("security_events", "fk_security_events_membership"): (
        ("membership_workspace_id", "membership_user_id"),
        "workspace_memberships",
        ("workspace_id", "user_id"),
        None,
        None,
    ),
}
_EXPECTED_CHECKS = {
    "users": frozenset("""
        ck_users_active_primary_email ck_users_created_at_timestamp
        ck_users_display_name_utf8_length ck_users_primary_verified_email_id_canonical
        ck_users_row_version_positive ck_users_schema_version_schema_one
        ck_users_security_epoch_positive ck_users_status_closed ck_users_timestamp_order
        ck_users_updated_at_timestamp ck_users_user_id_canonical
    """.split()),
    "verified_emails": frozenset("""
        ck_verified_emails_canonical_email_canonical ck_verified_emails_created_at_timestamp
        ck_verified_emails_email_id_canonical ck_verified_emails_lifecycle
        ck_verified_emails_retired_at_timestamp ck_verified_emails_row_version_positive
        ck_verified_emails_schema_version_schema_one ck_verified_emails_status_closed
        ck_verified_emails_user_id_canonical ck_verified_emails_verification_source_ascii
        ck_verified_emails_verified_at_timestamp
    """.split()),
    "authentication_identities": frozenset("""
        ck_authentication_identities_authentication_method_closed
        ck_authentication_identities_created_at_timestamp
        ck_authentication_identities_identity_id_canonical ck_authentication_identities_issuer_ascii
        ck_authentication_identities_last_used_at_timestamp
        ck_authentication_identities_row_version_positive
        ck_authentication_identities_schema_version_schema_one
        ck_authentication_identities_status_closed ck_authentication_identities_subject_ascii
        ck_authentication_identities_timestamp_order ck_authentication_identities_user_id_canonical
        ck_authentication_identities_verified_email_id_canonical
    """.split()),
    "workspaces": frozenset("""
        ck_workspaces_created_at_timestamp ck_workspaces_created_by_user_id_canonical
        ck_workspaces_row_version_positive ck_workspaces_schema_version_schema_one
        ck_workspaces_status_closed ck_workspaces_timestamp_order
        ck_workspaces_updated_at_timestamp ck_workspaces_workspace_id_canonical
    """.split()),
    "workspace_memberships": frozenset("""
        ck_workspace_memberships_created_at_timestamp ck_workspace_memberships_role_closed
        ck_workspace_memberships_row_version_positive
        ck_workspace_memberships_schema_version_schema_one
        ck_workspace_memberships_status_closed ck_workspace_memberships_timestamp_order
        ck_workspace_memberships_updated_at_timestamp
        ck_workspace_memberships_user_id_canonical
        ck_workspace_memberships_workspace_id_canonical
    """.split()),
    "initial_account_operations": frozenset("""
        ck_initial_account_operations_committed_at_timestamp
        ck_initial_account_operations_derivation_key_epoch_range
        ck_initial_account_operations_initial_state
        ck_initial_account_operations_operation_digest_32_bytes
        ck_initial_account_operations_operation_record_version_717c860b
        ck_initial_account_operations_receipt_authentication_i_6d692d22
        ck_initial_account_operations_receipt_binding
        ck_initial_account_operations_receipt_security_event_i_46e21c74
        ck_initial_account_operations_receipt_user_id_canonical
        ck_initial_account_operations_receipt_verified_email_i_3ea619c8
        ck_initial_account_operations_receipt_version_schema_one
        ck_initial_account_operations_receipt_workspace_id_canonical
        ck_initial_account_operations_reference_schema_version_58bfbb0c
        ck_initial_account_operations_request_snapshot_version_2dead7f1
        ck_initial_account_operations_request_version_schema_one
        ck_initial_account_operations_row_version_exact_one
        ck_initial_account_operations_snapshot_authentication__04890265
        ck_initial_account_operations_snapshot_authentication__0c556b23
        ck_initial_account_operations_snapshot_authentication__26840c19
        ck_initial_account_operations_snapshot_authentication__2746c6e2
        ck_initial_account_operations_snapshot_authentication__32720722
        ck_initial_account_operations_snapshot_authentication__39f7b050
        ck_initial_account_operations_snapshot_authentication__4e5b11de
        ck_initial_account_operations_snapshot_authentication__4e7c4c93
        ck_initial_account_operations_snapshot_authentication__525f7f77
        ck_initial_account_operations_snapshot_authentication__5b8ccdf4
        ck_initial_account_operations_snapshot_authentication__70b93be6
        ck_initial_account_operations_snapshot_authentication__791f98f2
        ck_initial_account_operations_snapshot_authentication__8881744a
        ck_initial_account_operations_snapshot_authentication__90799ed9
        ck_initial_account_operations_snapshot_authentication__af70db7a
        ck_initial_account_operations_snapshot_authentication__b60d01cc
        ck_initial_account_operations_snapshot_authentication__b6f25eec
        ck_initial_account_operations_snapshot_authentication__b9d2e30f
        ck_initial_account_operations_snapshot_authentication__be407d1a
        ck_initial_account_operations_snapshot_authentication__c5f896be
        ck_initial_account_operations_snapshot_authentication__cbe842e9
        ck_initial_account_operations_snapshot_authentication__efd795de
        ck_initial_account_operations_snapshot_graph
        ck_initial_account_operations_snapshot_security_event__55191972
        ck_initial_account_operations_snapshot_security_event__ca5fddc9
        ck_initial_account_operations_snapshot_security_event__f3243318
        ck_initial_account_operations_snapshot_user_created_at_48d820c3
        ck_initial_account_operations_snapshot_user_display_na_85f67a65
        ck_initial_account_operations_snapshot_user_primary_ve_19a6ef52
        ck_initial_account_operations_snapshot_user_row_versio_c3e16972
        ck_initial_account_operations_snapshot_user_schema_ver_dd642baf
        ck_initial_account_operations_snapshot_user_security_e_f77bd85b
        ck_initial_account_operations_snapshot_user_status_closed
        ck_initial_account_operations_snapshot_user_updated_at_e91a817b
        ck_initial_account_operations_snapshot_user_user_id_canonical
        ck_initial_account_operations_snapshot_verified_email__31cd66be
        ck_initial_account_operations_snapshot_verified_email__3b35dea2
        ck_initial_account_operations_snapshot_verified_email__48e09ecb
        ck_initial_account_operations_snapshot_verified_email__53a41d3f
        ck_initial_account_operations_snapshot_verified_email__7b6932de
        ck_initial_account_operations_snapshot_verified_email__889a5621
        ck_initial_account_operations_snapshot_verified_email__896234ca
        ck_initial_account_operations_snapshot_verified_email__a95325a3
        ck_initial_account_operations_snapshot_verified_email__d98fad96
        ck_initial_account_operations_snapshot_verified_email__ec633247
        ck_initial_account_operations_snapshot_workspace_creat_63908cb1
        ck_initial_account_operations_snapshot_workspace_creat_f5479908
        ck_initial_account_operations_snapshot_workspace_membe_2acc29ab
        ck_initial_account_operations_snapshot_workspace_membe_2b6b0fc7
        ck_initial_account_operations_snapshot_workspace_membe_5c1fef8f
        ck_initial_account_operations_snapshot_workspace_membe_6b3196ad
        ck_initial_account_operations_snapshot_workspace_membe_6fda33ce
        ck_initial_account_operations_snapshot_workspace_membe_7da77d48
        ck_initial_account_operations_snapshot_workspace_membe_d60051a7
        ck_initial_account_operations_snapshot_workspace_membe_f506b382
        ck_initial_account_operations_snapshot_workspace_row_v_080ea77d
        ck_initial_account_operations_snapshot_workspace_schem_05a09e5f
        ck_initial_account_operations_snapshot_workspace_status_closed
        ck_initial_account_operations_snapshot_workspace_updat_90d99b6f
        ck_initial_account_operations_snapshot_workspace_works_5c608029
        ck_initial_account_operations_timestamp_order
    """.split()),
    "security_events": frozenset("""
        ck_security_events_actor_trust_domain_opaque
        ck_security_events_actor_verification_coordinator_id_opaque
        ck_security_events_authentication_identity_id_canonical
        ck_security_events_derivation_key_epoch_range ck_security_events_event_at_timestamp
        ck_security_events_event_id_canonical ck_security_events_event_payload_version_schema_one
        ck_security_events_event_record_version_schema_one
        ck_security_events_event_stream_position_positive ck_security_events_event_type_closed
        ck_security_events_membership_binding ck_security_events_membership_user_id_canonical
        ck_security_events_membership_workspace_id_canonical
        ck_security_events_operation_digest_32_bytes ck_security_events_recorded_at_timestamp
        ck_security_events_reference_schema_version_schema_one
        ck_security_events_row_version_exact_one ck_security_events_security_epoch_positive
        ck_security_events_stream_name ck_security_events_timestamp_order
        ck_security_events_user_id_canonical ck_security_events_verified_email_id_canonical
        ck_security_events_workspace_id_canonical
    """.split()),
}
_EXPECTED_INDEXES = {
    ("verified_emails", "ux_verified_emails_current_claim"): (
        ("canonical_email",),
        True,
        "status = 'verified' AND retired_at IS NULL",
    ),
}


def _compiled_account_table_ddl() -> str:
    return "\n".join(
        str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for table in schema.ACCOUNT_TABLES
    )


def _postgresql_check_regex_patterns(sql: str) -> tuple[str, ...]:
    return tuple(
        match.group("pattern").replace("''", "'")
        for line in sql.splitlines()
        if " CHECK " in line.upper()
        for match in _POSTGRESQL_REGEX_LITERAL_PATTERN.finditer(line)
    )


def _postgresql_counted_repetition_bounds(
    sql: str,
) -> tuple[tuple[str, int, int | None], ...]:
    inventory = []
    for pattern in _postgresql_check_regex_patterns(sql):
        for match in _POSTGRESQL_COUNTED_REPETITION_PATTERN.finditer(pattern):
            minimum = int(match.group("minimum"))
            maximum_text = match.group("maximum")
            maximum = (
                minimum
                if match.group("comma") is None
                else int(maximum_text)
                if maximum_text
                else None
            )
            inventory.append((pattern, minimum, maximum))
    return tuple(inventory)


class AccountSchemaInventoryTests(unittest.TestCase):
    def test_exact_seven_schema_tables_and_dynamic_manifest_parity(self):
        manifests = relational.RELATIONAL_ACCOUNT_SCHEMA_1.relations
        self.assertEqual(len(schema.ACCOUNT_TABLES), 7)
        self.assertEqual(tuple(table.name for table in schema.ACCOUNT_TABLES), tuple(item.relation.value for item in manifests))
        self.assertEqual(set(metadata.tables), {f"cuevion_account.{item.relation.value}" for item in manifests})
        for manifest, table in zip(manifests, schema.ACCOUNT_TABLES, strict=True):
            with self.subTest(table=table.name):
                self.assertEqual(table.schema, "cuevion_account")
                self.assertEqual(tuple(table.c), tuple(table.c[name] for name in (field.name for field in manifest.fields)))
                self.assertEqual(tuple(table.c.keys()), tuple(field.name for field in manifest.fields))
                self.assertEqual(tuple(column.nullable for column in table.c), tuple(field.nullable for field in manifest.fields))
                self.assertTrue(all(column.default is None and column.server_default is None and column.onupdate is None for column in table.c))

    def test_primary_keys_and_uniques_match_independent_schema_one_oracle(self):
        primary_keys = {
            table.name: (
                table.primary_key.name,
                tuple(column.name for column in table.primary_key.columns),
            )
            for table in schema.ACCOUNT_TABLES
        }
        self.assertEqual(primary_keys, _EXPECTED_PRIMARY_KEYS)
        uniques = {
            (table.name, constraint.name): tuple(
                column.name for column in constraint.columns
            )
            for table in schema.ACCOUNT_TABLES
            for constraint in table.constraints
            if isinstance(constraint, sa.UniqueConstraint)
        }
        self.assertEqual(uniques, _EXPECTED_UNIQUES)

    def test_foreign_keys_match_independent_schema_one_oracle(self):
        foreign_keys = {
            (table.name, constraint.name): (
                tuple(element.parent.name for element in constraint.elements),
                constraint.referred_table.name,
                tuple(element.column.name for element in constraint.elements),
                constraint.deferrable,
                constraint.initially,
            )
            for table in schema.ACCOUNT_TABLES
            for constraint in table.constraints
            if isinstance(constraint, sa.ForeignKeyConstraint)
        }
        self.assertEqual(foreign_keys, _EXPECTED_FOREIGN_KEYS)
        constraints = (
            constraint
            for table in schema.ACCOUNT_TABLES
            for constraint in table.constraints
            if isinstance(constraint, sa.ForeignKeyConstraint)
        )
        for constraint in constraints:
            self.assertEqual(constraint.ondelete, "NO ACTION")
            self.assertEqual(constraint.onupdate, "NO ACTION")
            self.assertEqual(constraint.match, "SIMPLE")

    def test_check_constraints_match_independent_schema_one_oracle(self):
        checks = {
            table.name: frozenset(
                constraint.name
                for constraint in table.constraints
                if isinstance(constraint, sa.CheckConstraint)
            )
            for table in schema.ACCOUNT_TABLES
        }
        self.assertEqual(checks, _EXPECTED_CHECKS)

    def test_authority_ascii_checks_use_postgresql_safe_length_guard(self):
        tables = {table.name: table for table in schema.ACCOUNT_TABLES}
        for identity, column_name in _EXPECTED_AUTHORITY_ASCII_CHECKS.items():
            table_name, constraint_name = identity
            table = tables[table_name]
            constraint = next(
                item
                for item in table.constraints
                if item.name == constraint_name
            )
            with self.subTest(identity=identity):
                self.assertIsInstance(constraint, sa.CheckConstraint)
                self.assertEqual(table.c[column_name].type.length, 512)
                patterns = tuple(
                    node.value
                    for node in visitors.iterate(constraint.sqltext)
                    if isinstance(node, BindParameter)
                    and isinstance(node.value, str)
                )
                self.assertEqual(patterns, (r"^[!-~]+$",))
                ddl = str(
                    CreateTable(table).compile(dialect=postgresql.dialect())
                )
                self.assertIn(
                    f"CONSTRAINT {constraint_name} CHECK "
                    f"(({column_name} ~ '^[!-~]+$') AND "
                    f"octet_length({column_name}) <= 512)",
                    ddl,
                )
                self.assertNotIn(r"{1,512}", ddl)

    def test_compiled_postgresql_check_regex_bounds_do_not_exceed_are_limit(self):
        ddl = _compiled_account_table_ddl()
        patterns = _postgresql_check_regex_patterns(ddl)
        inventory = _postgresql_counted_repetition_bounds(ddl)
        self.assertEqual(len(patterns), 50)
        self.assertEqual(len(inventory), 47)
        for pattern, minimum, maximum in inventory:
            with self.subTest(pattern=pattern, minimum=minimum, maximum=maximum):
                self.assertLessEqual(minimum, _POSTGRESQL_ARE_MAX_COUNT)
                if maximum is not None:
                    self.assertLessEqual(maximum, _POSTGRESQL_ARE_MAX_COUNT)

    def test_postgresql_counted_repetition_oracle_detects_unsafe_bounds(self):
        ddl = "\n".join(
            (
                r"CONSTRAINT minimum CHECK (value ~ '^x{256,}$')",
                r"CONSTRAINT maximum CHECK (value ~ '^x{1,256}$')",
            )
        )
        self.assertEqual(
            _postgresql_counted_repetition_bounds(ddl),
            ((r"^x{256,}$", 256, None), (r"^x{1,256}$", 1, 256)),
        )

    def test_indexes_match_independent_schema_one_oracle(self):
        indexes = {}
        for table in schema.ACCOUNT_TABLES:
            for index in table.indexes:
                compiled = str(
                    CreateIndex(index).compile(dialect=postgresql.dialect())
                )
                predicate = (
                    compiled.split(" WHERE ", 1)[1]
                    if " WHERE " in compiled
                    else None
                )
                indexes[(table.name, index.name)] = (
                    tuple(column.name for column in index.columns),
                    index.unique,
                    predicate,
                )
        self.assertEqual(indexes, _EXPECTED_INDEXES)

    def test_metadata_email_checks_use_independent_session_safe_pattern(self):
        checks = {
            (table.name, constraint.name): constraint
            for table in schema.ACCOUNT_TABLES
            for constraint in table.constraints
            if isinstance(constraint, sa.CheckConstraint)
            and (table.name, constraint.name) in _EXPECTED_EMAIL_CHECKS
        }
        self.assertEqual(set(checks), _EXPECTED_EMAIL_CHECKS)
        for identity, constraint in checks.items():
            with self.subTest(identity=identity):
                patterns = tuple(
                    node.value
                    for node in visitors.iterate(constraint.sqltext)
                    if isinstance(node, BindParameter)
                    and isinstance(node.value, str)
                    and node.value.startswith("^")
                )
                self.assertEqual(patterns, (_EXPECTED_EMAIL_PATTERN,))
                self.assertIn("[.]", patterns[0])
                self.assertNotIn("\\", patterns[0])

    def test_independent_email_pattern_accepts_and_rejects_contract_vectors(self):
        validator = re.compile(_EXPECTED_EMAIL_PATTERN).fullmatch
        for value in ("a@example.com", "user.name+tag@example.co.uk"):
            with self.subTest(valid=value):
                self.assertIsNotNone(validator(value))
        for value in ("a@example", "@example.com", "a@.com", "a@example..com"):
            with self.subTest(invalid=value):
                self.assertIsNone(validator(value))

    def test_eventstream_contract(self):
        checks = " ".join(
            str(constraint.sqltext)
            for constraint in schema.security_events.constraints
            if isinstance(constraint, sa.CheckConstraint)
        )
        self.assertIn("event_stream_name", checks)
        ddl = str(CreateTable(schema.security_events).compile(dialect=postgresql.dialect()))
        self.assertIn(schema.ACCOUNT_SECURITY_EVENT_STREAM_NAME, ddl)
        self.assertEqual(schema.ACCOUNT_SECURITY_EVENT_STREAM_NAME, "cuevion.account.security")
        self.assertIsNone(schema.security_events.c.event_stream_position.server_default)


class AccountSchemaTypeTests(unittest.TestCase):
    def test_postgresql_types_collations_versions_and_timestamps(self):
        for table in schema.ACCOUNT_TABLES:
            manifest = next(item for item in relational.RELATIONAL_ACCOUNT_SCHEMA_1.relations if item.relation.value == table.name)
            for column in table.c:
                with self.subTest(table=table.name, column=column.name):
                    if schema._id_family(column.name) is not None:
                        self.assertIsInstance(column.type, sa.String)
                        self.assertEqual(column.type.length, 26)
                        self.assertEqual(column.type.collation, "C")
                    if column.name in manifest.timestamp_fields:
                        self.assertIsInstance(column.type, postgresql.TIMESTAMP)
                        self.assertTrue(column.type.timezone)
                    if (
                        column.name.endswith("_version")
                        and column.name != "row_version"
                        and not column.name.endswith("_row_version")
                    ):
                        self.assertIsInstance(column.type, sa.SmallInteger)
        self.assertIsInstance(schema.initial_account_operations.c.operation_digest.type, postgresql.BYTEA)
        self.assertIsInstance(schema.initial_account_operations.c.snapshot_authentication_evidence_assertion_id.type, postgresql.BYTEA)

    def test_no_uuid_json_array_native_enum_or_defaults(self):
        forbidden = (postgresql.UUID, postgresql.JSON, postgresql.JSONB, postgresql.ARRAY, postgresql.ENUM)
        for table in schema.ACCOUNT_TABLES:
            for column in table.c:
                self.assertFalse(isinstance(column.type, forbidden))
                self.assertIsNone(column.server_default)
                self.assertIsNone(column.default)

    def test_snapshot_is_scalar_and_has_receipt_and_event_bindings(self):
        operation_columns = set(schema.initial_account_operations.c.keys())
        expected_snapshot = {item.operation_field_name for item in relational.RELATIONAL_ACCOUNT_SCHEMA_1.request_snapshot.fields}
        self.assertTrue(expected_snapshot.issubset(operation_columns))
        self.assertTrue({"receipt_user_id", "receipt_verified_email_id", "receipt_authentication_identity_id", "receipt_workspace_id", "receipt_security_event_id"}.issubset(operation_columns))
        self.assertEqual(len(schema.initial_account_operations.c), len(next(item for item in relational.RELATIONAL_ACCOUNT_SCHEMA_1.relations if item.relation.value == "initial_account_operations").fields))

    def test_named_checks_compile_with_postgresql_only(self):
        for table in schema.ACCOUNT_TABLES:
            sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
            self.assertIn(f"CREATE TABLE cuevion_account.{table.name}", sql)
            for constraint in table.constraints:
                self.assertIsNotNone(constraint.name)
                self.assertLessEqual(len(constraint.name), 63)


class AccountSchemaInactivityTests(unittest.TestCase):
    def test_source_is_core_only_and_has_no_runtime_activation(self):
        source = _SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = tuple(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertFalse(any(module.startswith("sqlalchemy.orm") for module in imports))
        normalized = source.casefold()
        for forbidden in ("create_engine", "create_all", "metadata.create_all", "session(", "connect(", "os.environ"):
            self.assertNotIn(forbidden, normalized)
        forbidden_fields = ("token", "cookie", "billing", "subscription", "entitlement", "session_id")
        all_names = {column.name.casefold() for table in schema.ACCOUNT_TABLES for column in table.c}
        for fragment in forbidden_fields:
            self.assertTrue(all(fragment not in name for name in all_names))


if __name__ == "__main__":
    unittest.main()
