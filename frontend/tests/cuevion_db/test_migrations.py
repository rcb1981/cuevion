"""Offline Alembic tests for the account schema-one revision."""

import ast
import importlib.util
import io
from pathlib import Path
import re
import socket
import unittest
from unittest import mock

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import AddConstraint
from sqlalchemy.schema import CreateIndex
from sqlalchemy.schema import CreateSchema
from sqlalchemy.schema import CreateSequence
from sqlalchemy.schema import CreateTable

from cuevion_db.account_schema import ACCOUNT_TABLES
from cuevion_db.account_schema import security_event_stream_position_sequence
from cuevion_db.metadata import metadata


_FRONTEND = Path(__file__).resolve().parents[2]
_INI = _FRONTEND / "alembic.ini"
_ENV = _FRONTEND / "migrations" / "env.py"
_REVISION = _FRONTEND / "migrations" / "versions" / "0001_account_schema_1.py"

_EXPECTED_EMAIL_PATTERN = (
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:[.][a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:[.][a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_EXPECTED_EMAIL_CHECKS = {
    "ck_verified_emails_canonical_email_canonical": (
        "canonical_email",
        _EXPECTED_EMAIL_PATTERN,
    ),
    "ck_initial_account_operations_snapshot_verified_email__48e09ecb": (
        "snapshot_verified_email_canonical_email",
        _EXPECTED_EMAIL_PATTERN,
    ),
    "ck_initial_account_operations_snapshot_authentication__90799ed9": (
        "snapshot_authentication_evidence_canonical_verified_email",
        _EXPECTED_EMAIL_PATTERN,
    ),
}
_EXPECTED_AUTHORITY_ASCII_CHECKS = {
    "ck_authentication_identities_issuer_ascii": "issuer",
    "ck_authentication_identities_subject_ascii": "subject",
    "ck_initial_account_operations_snapshot_authentication__04890265": (
        "snapshot_authentication_identity_issuer"
    ),
    "ck_initial_account_operations_snapshot_authentication__b9d2e30f": (
        "snapshot_authentication_identity_subject"
    ),
    "ck_initial_account_operations_snapshot_authentication__0c556b23": (
        "snapshot_authentication_evidence_issuer"
    ),
    "ck_initial_account_operations_snapshot_authentication__525f7f77": (
        "snapshot_authentication_evidence_subject"
    ),
}
_EXPECTED_TRIGGERS = {
    (
        "trg_initial_ops_append_only",
        "initial_account_operations",
        "BEFORE",
        ("UPDATE", "DELETE"),
        "ROW",
        "fn_reject_append_only_change",
        False,
        None,
        None,
    ),
    (
        "trg_initial_ops_no_truncate",
        "initial_account_operations",
        "BEFORE",
        ("TRUNCATE",),
        "STATEMENT",
        "fn_reject_append_only_change",
        False,
        None,
        None,
    ),
    (
        "trg_security_events_append_only",
        "security_events",
        "BEFORE",
        ("UPDATE", "DELETE"),
        "ROW",
        "fn_reject_append_only_change",
        False,
        None,
        None,
    ),
    (
        "trg_security_events_no_truncate",
        "security_events",
        "BEFORE",
        ("TRUNCATE",),
        "STATEMENT",
        "fn_reject_append_only_change",
        False,
        None,
        None,
    ),
    (
        "trg_users_mutation_guard",
        "users",
        "BEFORE",
        ("UPDATE",),
        "ROW",
        "fn_enforce_mutable_account_update",
        False,
        None,
        None,
    ),
    (
        "trg_verified_emails_mutation_guard",
        "verified_emails",
        "BEFORE",
        ("UPDATE",),
        "ROW",
        "fn_enforce_mutable_account_update",
        False,
        None,
        None,
    ),
    (
        "trg_auth_identities_mutation_guard",
        "authentication_identities",
        "BEFORE",
        ("UPDATE",),
        "ROW",
        "fn_enforce_mutable_account_update",
        False,
        None,
        None,
    ),
    (
        "trg_workspaces_mutation_guard",
        "workspaces",
        "BEFORE",
        ("UPDATE",),
        "ROW",
        "fn_enforce_mutable_account_update",
        False,
        None,
        None,
    ),
    (
        "trg_workspace_memberships_mutation_guard",
        "workspace_memberships",
        "BEFORE",
        ("UPDATE",),
        "ROW",
        "fn_enforce_mutable_account_update",
        False,
        None,
        None,
    ),
    (
        "ct_initial_account_graph_consistent",
        "initial_account_operations",
        "AFTER",
        ("INSERT",),
        "ROW",
        "fn_validate_initial_account_graph",
        True,
        True,
        "DEFERRED",
    ),
}

_EMAIL_CHECK_PATTERN = re.compile(
    r"CONSTRAINT\s+(?P<name>[a-z0-9_]+)\s+CHECK\s+\(\("
    r"(?P<column>[a-z0-9_]+)\s+~\s+'(?P<pattern>(?:''|[^'])+)'",
    re.IGNORECASE,
)
_AUTHORITY_ASCII_CHECK_PATTERN = re.compile(
    r"CONSTRAINT\s+(?P<name>[a-z0-9_]+)\s+CHECK\s+\(\("
    r"(?P<column>[a-z0-9_]+)\s+~\s+'(?P<pattern>(?:''|[^'])+)'\)\s+AND\s+"
    r"octet_length\((?P<length_column>[a-z0-9_]+)\)\s*<=\s*"
    r"(?P<maximum>\d+)\)",
    re.IGNORECASE,
)
_POSTGRESQL_ARE_MAX_COUNT = 255
_POSTGRESQL_REGEX_LITERAL_PATTERN = re.compile(
    r"~\s*'(?P<pattern>(?:''|[^'])*)'",
)
_POSTGRESQL_COUNTED_REPETITION_PATTERN = re.compile(
    r"(?<!\\)\{(?P<minimum>\d+)(?:(?P<comma>,)(?P<maximum>\d*))?\}",
)
_TRIGGER_PATTERN = re.compile(
    r"CREATE\s+(?P<constraint>CONSTRAINT\s+)?TRIGGER\s+"
    r"(?P<name>[a-z0-9_]+)\s+"
    r"(?P<timing>BEFORE|AFTER)\s+"
    r"(?P<events>[A-Z]+(?:\s+OR\s+[A-Z]+)*)\s+ON\s+"
    r"cuevion_account\.(?P<table>[a-z0-9_]+)\s+"
    r"(?:(?P<deferred>DEFERRABLE\s+INITIALLY\s+DEFERRED)\s+)?"
    r"FOR\s+EACH\s+(?P<level>ROW|STATEMENT)\s+"
    r"EXECUTE\s+FUNCTION\s+cuevion_account\.(?P<function>[a-z0-9_]+)\(\)",
    re.IGNORECASE,
)


def _email_check_inventory(sql: str):
    return {
        match.group("name").casefold(): (
            match.group("column").casefold(),
            match.group("pattern").replace("''", "'"),
        )
        for match in _EMAIL_CHECK_PATTERN.finditer(sql)
        if match.group("name").casefold() in _EXPECTED_EMAIL_CHECKS
    }


def _authority_ascii_check_inventory(sql: str):
    normalized = " ".join(sql.split())
    return {
        match.group("name").casefold(): (
            match.group("column").casefold(),
            match.group("pattern").replace("''", "'"),
            match.group("length_column").casefold(),
            int(match.group("maximum")),
        )
        for match in _AUTHORITY_ASCII_CHECK_PATTERN.finditer(normalized)
        if match.group("name").casefold() in _EXPECTED_AUTHORITY_ASCII_CHECKS
    }


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


def _trigger_inventory(sql: str):
    normalized = " ".join(sql.split())
    return {
        (
            match.group("name").casefold(),
            match.group("table").casefold(),
            match.group("timing").upper(),
            tuple(
                event.upper()
                for event in re.split(r"\s+OR\s+", match.group("events"), flags=re.I)
            ),
            match.group("level").upper(),
            match.group("function").casefold(),
            match.group("constraint") is not None,
            True if match.group("deferred") is not None else None,
            "DEFERRED" if match.group("deferred") is not None else None,
        )
        for match in _TRIGGER_PATTERN.finditer(normalized)
    }


def _revision_module():
    spec = importlib.util.spec_from_file_location("cuevion_revision_0001", _REVISION)
    if spec is None or spec.loader is None:
        raise AssertionError("revision cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _offline_sql() -> str:
    output = io.StringIO()
    configuration = Config(str(_INI), output_buffer=output)
    with (
        mock.patch.object(socket, "socket", side_effect=AssertionError("socket")),
        mock.patch.object(socket, "create_connection", side_effect=AssertionError("socket")),
    ):
        command.upgrade(configuration, "head", sql=True)
    return output.getvalue()


def _canonical_ddl(statement: str) -> str:
    return "\n".join(line.rstrip() for line in statement.strip().splitlines())


def _compiled_ddl(element) -> str:
    statement = str(element.compile(dialect=postgresql.dialect()))
    return _canonical_ddl(statement.replace("%%", "%"))


def _constraint(table, name: str):
    return next(item for item in table.constraints if item.name == name)


class MigrationHistoryTests(unittest.TestCase):
    def test_exact_one_base_head_and_revision_identity(self):
        configuration = Config(str(_INI))
        scripts = ScriptDirectory.from_config(configuration)
        revisions = tuple(scripts.walk_revisions())
        self.assertEqual(len(revisions), 1)
        self.assertEqual(scripts.get_heads(), ["0001_account_schema_1"])
        self.assertEqual(scripts.get_bases(), ["0001_account_schema_1"])
        revision = revisions[0]
        self.assertEqual(revision.revision, "0001_account_schema_1")
        self.assertIsNone(revision.down_revision)
        module = _revision_module()
        self.assertIsNone(module.branch_labels)
        self.assertIsNone(module.depends_on)

    def test_forward_only_downgrade_is_fixed_and_value_free(self):
        with self.assertRaisesRegex(RuntimeError, "^cuevion account authority migrations are forward-only$"):
            _revision_module().downgrade()

    def test_ini_and_environment_have_no_url_or_online_connector(self):
        ini = _INI.read_text(encoding="utf-8")
        env = _ENV.read_text(encoding="utf-8")
        self.assertNotIn("sqlalchemy.url", ini.casefold())
        self.assertNotIn("password", ini.casefold())
        self.assertIn('version_table="cuevion_account_alembic_version"', env)
        self.assertIn('version_table_schema="public"', env)
        self.assertIn("transaction_per_migration=True", env)
        for forbidden in ("create_engine", "engine_from_config", "os.environ", "psycopg.connect"):
            self.assertNotIn(forbidden, env)

    def test_ordinary_environment_import_runs_no_migration(self):
        spec = importlib.util.spec_from_file_location("inactive_migration_env", _ENV)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec is not None else None)
        module = importlib.util.module_from_spec(spec)
        with mock.patch("alembic.context.run_migrations", side_effect=AssertionError("migration")):
            spec.loader.exec_module(module)

    def test_revision_imports_no_live_schema_or_contract_module(self):
        source = _REVISION.read_text(encoding="utf-8")
        imported_roots = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_roots.add(node.module.split(".", 1)[0])
        self.assertEqual(imported_roots, {"alembic"})

        real_import = __import__

        def reject_live_schema(name, *args, **kwargs):
            if name.startswith(("cuevion_db", "cuevion_auth", "api.auth")):
                raise AssertionError(f"live schema import: {name}")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=reject_live_schema):
            module = _revision_module()
        self.assertEqual(module.revision, "0001_account_schema_1")

    def test_frozen_revision_email_checks_use_independent_safe_oracle(self):
        source = _REVISION.read_text(encoding="utf-8")
        inventory = _email_check_inventory(source)
        self.assertEqual(inventory, _EXPECTED_EMAIL_CHECKS)
        for name, (_column, pattern) in inventory.items():
            with self.subTest(constraint=name):
                self.assertIn("[.]", pattern)
                self.assertNotIn("\\", pattern)

    def test_frozen_revision_authority_ascii_checks_are_postgresql_safe(self):
        module = _revision_module()
        ddl = "\n".join(module._TABLE_DDL)
        inventory = _authority_ascii_check_inventory(ddl)
        self.assertEqual(
            inventory,
            {
                name: (column, r"^[!-~]+$", column, 512)
                for name, column in _EXPECTED_AUTHORITY_ASCII_CHECKS.items()
            },
        )
        self.assertNotIn(r"{1,512}", ddl)

    def test_frozen_postgresql_check_regex_bounds_do_not_exceed_are_limit(self):
        ddl = "\n".join(_revision_module()._TABLE_DDL)
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

    def test_frozen_revision_defines_exact_trigger_architecture(self):
        module = _revision_module()
        inventory = _trigger_inventory("\n".join(module._TRIGGER_SQL))
        self.assertEqual(inventory, _EXPECTED_TRIGGERS)


class OfflineMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = _offline_sql()
        cls.normalized = " ".join(cls.sql.casefold().split())

    def test_offline_upgrade_creates_exactly_seven_authority_tables_once(self):
        created = re.findall(r"create table cuevion_account\.([a-z_]+)", self.sql.casefold())
        self.assertEqual(created, [table.name for table in ACCOUNT_TABLES])
        self.assertEqual(set(metadata.tables), {f"cuevion_account.{name}" for name in created})

    def test_sql_contains_schema_ledger_constraints_index_and_sequence(self):
        self.assertIn("create schema cuevion_account", self.normalized)
        self.assertIn("create table public.cuevion_account_alembic_version", self.normalized)
        self.assertIn("create sequence cuevion_account.security_event_stream_position_seq", self.normalized)
        self.assertIn("ux_verified_emails_current_claim", self.normalized)
        self.assertIn("fk_security_events_operation_binding", self.normalized)
        self.assertIn("uq_initial_ops_event_binding", self.normalized)

    def test_sql_contains_all_functions_and_exact_trigger_architecture(self):
        for function in (
            "fn_reject_append_only_change",
            "fn_enforce_mutable_account_update",
            "fn_validate_initial_account_graph",
        ):
            self.assertIn(f"create function cuevion_account.{function}", self.normalized)
        self.assertEqual(_trigger_inventory(self.sql), _EXPECTED_TRIGGERS)

    def test_offline_email_checks_use_independent_safe_oracle(self):
        inventory = _email_check_inventory(self.sql)
        self.assertEqual(inventory, _EXPECTED_EMAIL_CHECKS)
        for name, (_column, pattern) in inventory.items():
            with self.subTest(constraint=name):
                self.assertIn("[.]", pattern)
                self.assertNotIn("\\", pattern)

    def test_offline_authority_ascii_checks_are_postgresql_safe(self):
        inventory = _authority_ascii_check_inventory(self.sql)
        self.assertEqual(
            inventory,
            {
                name: (column, r"^[!-~]+$", column, 512)
                for name, column in _EXPECTED_AUTHORITY_ASCII_CHECKS.items()
            },
        )
        self.assertNotIn(r"{1,512}", self.sql)

    def test_offline_postgresql_check_regex_bounds_do_not_exceed_are_limit(self):
        patterns = _postgresql_check_regex_patterns(self.sql)
        inventory = _postgresql_counted_repetition_bounds(self.sql)
        self.assertEqual(len(patterns), 50)
        self.assertEqual(len(inventory), 47)
        for pattern, minimum, maximum in inventory:
            with self.subTest(pattern=pattern, minimum=minimum, maximum=maximum):
                self.assertLessEqual(minimum, _POSTGRESQL_ARE_MAX_COUNT)
                if maximum is not None:
                    self.assertLessEqual(maximum, _POSTGRESQL_ARE_MAX_COUNT)

    def test_append_only_mutation_cas_epoch_and_graph_guards_are_present(self):
        for phrase in (
            "before update or delete",
            "before truncate",
            "new.row_version <> old.row_version + 1",
            "new.security_epoch < old.security_epoch",
            "is not distinct from",
            "deferrable initially deferred",
        ):
            self.assertIn(phrase, self.normalized)

    def test_upgrade_has_no_seed_or_destructive_authority_sql(self):
        self.assertNotRegex(self.normalized, r"insert into cuevion_account\.")
        self.assertNotRegex(self.normalized, r"copy cuevion_account\.")
        self.assertNotIn("drop ", self.normalized)
        self.assertIn("insert into public.cuevion_account_alembic_version", self.normalized)

    def test_frozen_revision_ddl_and_metadata_ddl_are_exact(self):
        module = _revision_module()
        self.assertEqual(
            module._ACCOUNT_TABLE_NAMES,
            tuple(table.name for table in ACCOUNT_TABLES),
        )
        self.assertEqual(
            _canonical_ddl(module._SCHEMA_DDL),
            _compiled_ddl(CreateSchema("cuevion_account")),
        )
        self.assertEqual(
            _canonical_ddl(module._SEQUENCE_DDL),
            _compiled_ddl(CreateSequence(security_event_stream_position_sequence)),
        )
        self.assertEqual(
            tuple(_canonical_ddl(statement) for statement in module._TABLE_DDL),
            tuple(_compiled_ddl(CreateTable(table)) for table in ACCOUNT_TABLES),
        )
        self.assertEqual(
            tuple(_canonical_ddl(statement) for statement in module._INDEX_DDL),
            tuple(
                _compiled_ddl(CreateIndex(index))
                for index in ACCOUNT_TABLES[1].indexes
            ),
        )
        deferred_constraints = (
            _constraint(ACCOUNT_TABLES[0], "fk_users_primary_email_same_user"),
            _constraint(ACCOUNT_TABLES[5], "fk_initial_ops_receipt_event"),
        )
        self.assertEqual(
            tuple(
                _canonical_ddl(statement)
                for statement in module._DEFERRED_FOREIGN_KEY_DDL
            ),
            tuple(
                _compiled_ddl(AddConstraint(constraint))
                for constraint in deferred_constraints
            ),
        )


if __name__ == "__main__":
    unittest.main()
