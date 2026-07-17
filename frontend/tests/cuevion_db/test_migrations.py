"""Offline Alembic tests for the account schema-one revision."""

import ast
from dataclasses import dataclass
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
_EXPECTED_FUNCTIONS = (
    "fn_reject_append_only_change",
    "fn_enforce_mutable_account_update",
    "fn_validate_initial_account_graph",
)
_EXPECTED_FUNCTION_PRIVILEGE_STATEMENTS = (
    "alter default privileges revoke execute on functions from public",
    "revoke execute on all functions in schema cuevion_account from public",
)
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
_DOLLAR_QUOTE_DELIMITER_PATTERN = re.compile(
    r"\$(?:(?:[A-Za-z_]|[^\x00-\x7f])"
    r"(?:[A-Za-z0-9_]|[^\x00-\x7f])*)?\$"
)
_CREATE_FUNCTION_NAME_PATTERN = re.compile(
    r"^create\s+function\s+cuevion_account\s*\.\s*"
    r"(?P<name>[a-z_][a-z0-9_]*)\s*\(\s*\)"
)
_IDENTIFIER_CONTINUATION_PATTERN = r"(?:[A-Za-z0-9_$]|[^\x00-\x7f])"
_KEYWORD_START_PATTERN = r"(?<![A-Za-z0-9_$])(?<![^\x00-\x7f])"
_KEYWORD_END_PATTERN = rf"(?!{_IDENTIFIER_CONTINUATION_PATTERN})"
_REVISION_INSERT_PATTERN = re.compile(
    rf"^insert{_KEYWORD_END_PATTERN}\s+"
    rf"into{_KEYWORD_END_PATTERN}\s+"
    r"public\.cuevion_account_alembic_version\s*"
    r"\(\s*version_num\s*\)\s+values\s*"
    r"\(\s*'0001_account_schema_1'\s*\)"
    rf"(?:\s+returning{_KEYWORD_END_PATTERN}[\s\S]*)?$",
)
_SAFE_SEARCH_PATH_PATTERN = re.compile(
    rf"{_KEYWORD_START_PATTERN}set{_KEYWORD_END_PATTERN}\s+"
    rf"search_path{_KEYWORD_END_PATTERN}\s*=\s*"
    rf"pg_catalog{_KEYWORD_END_PATTERN}\s*,\s*"
    rf"pg_temp{_KEYWORD_END_PATTERN}(?!\s*[,\.])"
)
_STATEMENT_KIND_PREFIXES = (
    ("CREATE FUNCTION", ("create", "function")),
    ("CREATE FUNCTION", ("create", "or", "replace", "function")),
    ("CREATE PROCEDURE", ("create", "procedure")),
    ("CREATE PROCEDURE", ("create", "or", "replace", "procedure")),
    ("CREATE ROUTINE", ("create", "routine")),
    ("CREATE ROUTINE", ("create", "or", "replace", "routine")),
    ("ALTER FUNCTION", ("alter", "function")),
    ("ALTER PROCEDURE", ("alter", "procedure")),
    ("ALTER ROUTINE", ("alter", "routine")),
    ("ALTER DEFAULT PRIVILEGES", ("alter", "default", "privileges")),
    ("GRANT", ("grant",)),
    ("REVOKE", ("revoke",)),
    ("START TRANSACTION", ("start", "transaction")),
    ("PREPARE TRANSACTION", ("prepare", "transaction")),
    ("SAVEPOINT", ("savepoint",)),
    ("RELEASE SAVEPOINT", ("release",)),
    ("SET CONSTRAINTS", ("set", "constraints")),
    ("SET TRANSACTION", ("set", "transaction")),
    (
        "SET SESSION TRANSACTION",
        ("set", "session", "characteristics", "as", "transaction"),
    ),
    ("BEGIN", ("begin",)),
    ("COMMIT", ("commit",)),
    ("END", ("end",)),
    ("ROLLBACK", ("rollback",)),
    ("ABORT", ("abort",)),
)
_TRANSACTION_STATEMENT_KINDS = frozenset(
    {
        "START TRANSACTION",
        "PREPARE TRANSACTION",
        "SAVEPOINT",
        "RELEASE SAVEPOINT",
        "SET CONSTRAINTS",
        "SET TRANSACTION",
        "SET SESSION TRANSACTION",
        "BEGIN",
        "COMMIT",
        "END",
        "ROLLBACK",
        "ABORT",
    }
)
_ROUTINE_ALTER_KINDS = frozenset(
    {"ALTER FUNCTION", "ALTER PROCEDURE", "ALTER ROUTINE"}
)
_ROUTINE_SECURITY_KINDS = frozenset(
    {
        "CREATE FUNCTION",
        "CREATE PROCEDURE",
        "CREATE ROUTINE",
        "ALTER FUNCTION",
        "ALTER PROCEDURE",
        "ALTER ROUTINE",
    }
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


class _SqlScanError(AssertionError):
    """Raised when offline SQL cannot be inventoried safely."""


@dataclass(frozen=True, slots=True)
class _SqlStatement:
    index: int
    start: int
    end: int
    original: str
    canonical: str
    keyword_sql: str


def _normalize_sql_whitespace(fragment: str) -> str:
    return " ".join(fragment.split())


def _ascii_fold(fragment: str) -> str:
    return "".join(
        chr(ord(character) + 32)
        if "A" <= character <= "Z"
        else character
        for character in fragment
    )


def _is_identifier_continuation(character: str) -> bool:
    return (
        "A" <= character <= "Z"
        or "a" <= character <= "z"
        or "0" <= character <= "9"
        or character in "_$"
        or ord(character) >= 128
    )


def _keyword_lexemes(keyword_sql: str) -> tuple[str, ...]:
    lexemes = []
    offset = 0
    while offset < len(keyword_sql):
        character = keyword_sql[offset]
        if _is_identifier_continuation(character):
            start = offset
            offset += 1
            while (
                offset < len(keyword_sql)
                and _is_identifier_continuation(keyword_sql[offset])
            ):
                offset += 1
            lexemes.append(_ascii_fold(keyword_sql[start:offset]))
            continue
        if character in ".,()=&":
            lexemes.append(character)
        offset += 1
    return tuple(lexemes)


def _keyword_tokens(keyword_sql: str) -> tuple[str, ...]:
    return tuple(
        lexeme
        for lexeme in _keyword_lexemes(keyword_sql)
        if lexeme not in {".", ",", "(", ")", "=", "&"}
    )


def _contains_keyword_sequence(keyword_sql: str, *keywords: str) -> bool:
    tokens = _keyword_tokens(keyword_sql)
    width = len(keywords)
    return any(
        tokens[offset : offset + width] == keywords
        for offset in range(len(tokens) - width + 1)
    )


def _revision_table_insert_count(statement: _SqlStatement) -> int:
    lexemes = _keyword_lexemes(statement.keyword_sql)
    count = 0
    for offset in range(len(lexemes) - 2):
        if lexemes[offset : offset + 2] != ("insert", "into"):
            continue
        target_offset = offset + 2
        if (
            target_offset < len(lexemes)
            and lexemes[target_offset] == "only"
        ):
            target_offset += 1
        qualified_target = lexemes[target_offset : target_offset + 3]
        if (
            len(qualified_target) == 3
            and qualified_target[0]
            in {"public", "__quoted_public__"}
            and qualified_target[1] == "."
            and qualified_target[2]
            in {
                "cuevion_account_alembic_version",
                "__quoted_revision_table__",
            }
        ):
            count += 1
            continue
        if lexemes[target_offset : target_offset + 1] in {
            ("cuevion_account_alembic_version",),
            ("__quoted_revision_table__",),
        }:
            count += 1
    return count


def _scan_sql(sql: str) -> tuple[_SqlStatement, ...]:
    """Split PostgreSQL SQL on lexical top-level semicolons, fail-closed."""

    statements = []
    canonical_parts = []
    keyword_parts = []
    statement_start = 0
    offset = 0

    def append_statement(end: int) -> None:
        nonlocal statement_start
        canonical = _normalize_sql_whitespace("".join(canonical_parts))
        keyword_sql = _normalize_sql_whitespace("".join(keyword_parts))
        if canonical:
            statements.append(
                _SqlStatement(
                    index=len(statements),
                    start=statement_start,
                    end=end,
                    original=sql[statement_start:end].strip(),
                    canonical=canonical,
                    keyword_sql=keyword_sql,
                )
            )
        canonical_parts.clear()
        keyword_parts.clear()
        statement_start = end + 1

    while offset < len(sql):
        if sql.startswith("--", offset):
            canonical_parts.append(" ")
            keyword_parts.append(" ")
            offset += 2
            while offset < len(sql) and sql[offset] not in "\r\n":
                offset += 1
            continue

        if sql.startswith("/*", offset):
            comment_start = offset
            depth = 1
            offset += 2
            while offset < len(sql) and depth:
                if sql.startswith("/*", offset):
                    depth += 1
                    offset += 2
                elif sql.startswith("*/", offset):
                    depth -= 1
                    offset += 2
                else:
                    offset += 1
            if depth:
                raise _SqlScanError(
                    f"unterminated block comment at offset {comment_start}"
                )
            canonical_parts.append(" ")
            keyword_parts.append(" ")
            continue

        character = sql[offset]
        if character in {"'", '"'}:
            literal_start = offset
            delimiter = character
            escape_backslashes = (
                delimiter == "'"
                and literal_start > 0
                and sql[literal_start - 1] in "Ee"
                and (
                    literal_start == 1
                    or not _is_identifier_continuation(
                        sql[literal_start - 2]
                    )
                )
            )
            offset += 1
            while offset < len(sql):
                if escape_backslashes and sql[offset] == "\\":
                    offset += 2
                    continue
                if sql[offset] != delimiter:
                    offset += 1
                    continue
                if offset + 1 < len(sql) and sql[offset + 1] == delimiter:
                    offset += 2
                    continue
                offset += 1
                break
            else:
                description = (
                    "single-quoted string"
                    if delimiter == "'"
                    else "double-quoted identifier"
                )
                raise _SqlScanError(
                    f"unterminated {description} at offset {literal_start}"
                )
            canonical_parts.append(sql[literal_start:offset])
            if delimiter == "'":
                marker = " __sql_string__ "
            else:
                identifier = sql[literal_start + 1 : offset - 1].replace(
                    '""', '"'
                )
                if _ascii_fold(identifier) == "search_path":
                    marker = " __quoted_search_path__ "
                elif identifier == "public":
                    marker = " __quoted_public__ "
                elif identifier == "cuevion_account_alembic_version":
                    marker = " __quoted_revision_table__ "
                else:
                    marker = " __quoted_identifier__ "
            keyword_parts.append(marker)
            continue

        dollar_match = _DOLLAR_QUOTE_DELIMITER_PATTERN.match(sql, offset)
        preceded_by_identifier = (
            offset > 0 and _is_identifier_continuation(sql[offset - 1])
        )
        if dollar_match is not None and not preceded_by_identifier:
            literal_start = offset
            delimiter = dollar_match.group(0)
            body_end = sql.find(delimiter, dollar_match.end())
            if body_end < 0:
                raise _SqlScanError(
                    f"unterminated dollar quote {delimiter} at offset {literal_start}"
                )
            offset = body_end + len(delimiter)
            canonical_parts.append(sql[literal_start:offset])
            keyword_parts.append(" __dollar_quoted_body__ ")
            continue

        if character == ";":
            append_statement(offset)
            offset += 1
            continue

        folded_character = _ascii_fold(character)
        canonical_parts.append(folded_character)
        keyword_parts.append(folded_character)
        offset += 1

    append_statement(len(sql))
    return tuple(statements)


def _statement_kind(statement: _SqlStatement) -> str | None:
    tokens = _keyword_tokens(statement.keyword_sql)
    for kind, prefix in _STATEMENT_KIND_PREFIXES:
        if tokens[: len(prefix)] == prefix:
            return kind
    return None


def _statements_of_kind(
    statements: tuple[_SqlStatement, ...], *kinds: str
) -> tuple[_SqlStatement, ...]:
    expected = frozenset(kinds)
    return tuple(
        statement
        for statement in statements
        if _statement_kind(statement) in expected
    )


def _assert_no_top_level_grants(statements: tuple[_SqlStatement, ...]) -> None:
    grants = _statements_of_kind(statements, "GRANT")
    if grants:
        raise AssertionError(
            "unexpected top-level GRANT statement(s): "
            + repr(tuple(statement.canonical for statement in grants))
        )


def _assert_exact_function_acl(statements: tuple[_SqlStatement, ...]) -> None:
    default_privileges = _statements_of_kind(
        statements, "ALTER DEFAULT PRIVILEGES"
    )
    for statement in default_privileges:
        if _contains_keyword_sequence(statement.keyword_sql, "for", "role"):
            raise AssertionError("ALTER DEFAULT PRIVILEGES must not use FOR ROLE")
        if _contains_keyword_sequence(statement.keyword_sql, "in", "schema"):
            raise AssertionError("ALTER DEFAULT PRIVILEGES must not use IN SCHEMA")
    if tuple(statement.canonical for statement in default_privileges) != (
        _EXPECTED_FUNCTION_PRIVILEGE_STATEMENTS[0],
    ):
        raise AssertionError(
            "unexpected ALTER DEFAULT PRIVILEGES inventory: "
            + repr(tuple(statement.canonical for statement in default_privileges))
        )

    revokes = _statements_of_kind(statements, "REVOKE")
    if tuple(statement.canonical for statement in revokes) != (
        _EXPECTED_FUNCTION_PRIVILEGE_STATEMENTS[1],
    ):
        raise AssertionError(
            "unexpected top-level REVOKE inventory: "
            + repr(tuple(statement.canonical for statement in revokes))
        )


def _assert_no_routine_owner_changes(
    statements: tuple[_SqlStatement, ...],
) -> None:
    owner_changes = tuple(
        statement
        for statement in statements
        if _statement_kind(statement) in _ROUTINE_ALTER_KINDS
        and _contains_keyword_sequence(statement.keyword_sql, "owner", "to")
    )
    if owner_changes:
        raise AssertionError(
            "unexpected routine OWNER TO statement(s): "
            + repr(tuple(statement.canonical for statement in owner_changes))
        )


def _assert_no_unexpected_routine_alters(
    statements: tuple[_SqlStatement, ...],
) -> None:
    _assert_no_routine_owner_changes(statements)
    alters = tuple(
        statement
        for statement in statements
        if _statement_kind(statement) in _ROUTINE_ALTER_KINDS
    )
    if alters:
        raise AssertionError(
            "unexpected top-level routine ALTER statement(s): "
            + repr(tuple(statement.canonical for statement in alters))
        )


def _assert_no_security_definer(
    statements: tuple[_SqlStatement, ...],
) -> None:
    offenders = tuple(
        statement
        for statement in statements
        if _statement_kind(statement) in _ROUTINE_SECURITY_KINDS
        if _contains_keyword_sequence(
            statement.keyword_sql, "security", "definer"
        )
    )
    if offenders:
        raise AssertionError(
            "unexpected SECURITY DEFINER function(s): "
            + repr(tuple(statement.canonical for statement in offenders))
        )


def _assert_safe_function_attributes(
    statements: tuple[_SqlStatement, ...],
) -> None:
    functions = _statements_of_kind(statements, "CREATE FUNCTION")
    _assert_no_security_definer(statements)
    _assert_no_unexpected_routine_alters(statements)
    for statement in functions:
        set_option_count = _keyword_tokens(statement.keyword_sql).count("set")
        safe_search_path_count = len(
            tuple(_SAFE_SEARCH_PATH_PATTERN.finditer(statement.keyword_sql))
        )
        if (set_option_count, safe_search_path_count) != (1, 1):
            raise AssertionError(
                "function must define exactly one SET option: "
                "SET search_path = "
                f"pg_catalog, pg_temp: {statement.canonical!r}"
            )


def _assert_exact_function_definitions(
    statements: tuple[_SqlStatement, ...],
) -> None:
    functions = _statements_of_kind(statements, "CREATE FUNCTION")
    procedures = _statements_of_kind(statements, "CREATE PROCEDURE")
    routines = _statements_of_kind(statements, "CREATE ROUTINE")
    if procedures or routines:
        raise AssertionError(
            "unexpected CREATE PROCEDURE/ROUTINE statement(s): "
            + repr(
                tuple(
                    statement.canonical for statement in procedures + routines
                )
            )
        )
    if len(functions) != len(_EXPECTED_FUNCTIONS):
        raise AssertionError(
            f"expected exactly {len(_EXPECTED_FUNCTIONS)} CREATE FUNCTION "
            f"statements, found {len(functions)}"
        )
    names = tuple(
        match.group("name") if match is not None else None
        for statement in functions
        for match in [_CREATE_FUNCTION_NAME_PATTERN.search(statement.canonical)]
    )
    if names != _EXPECTED_FUNCTIONS:
        raise AssertionError(f"unexpected CREATE FUNCTION inventory: {names!r}")
    _assert_safe_function_attributes(statements)


def _assert_exact_transaction_structure(
    statements: tuple[_SqlStatement, ...],
) -> None:
    transaction_statements = tuple(
        statement
        for statement in statements
        if _statement_kind(statement) in _TRANSACTION_STATEMENT_KINDS
    )
    inventory = tuple(
        (_statement_kind(statement), statement.canonical)
        for statement in transaction_statements
    )
    if inventory != (("BEGIN", "begin"), ("COMMIT", "commit")):
        raise AssertionError(f"unexpected transaction inventory: {inventory!r}")
    if transaction_statements[0].index != 0:
        raise AssertionError("BEGIN must be the first top-level statement")
    if transaction_statements[-1].index != len(statements) - 1:
        raise AssertionError("COMMIT must be the last top-level statement")


def _assert_offline_statement_order(
    statements: tuple[_SqlStatement, ...],
) -> None:
    default_privileges = _statements_of_kind(
        statements, "ALTER DEFAULT PRIVILEGES"
    )
    functions = _statements_of_kind(statements, "CREATE FUNCTION")
    revokes = _statements_of_kind(statements, "REVOKE")
    revision_table_inserts = tuple(
        statement
        for statement in statements
        for _ in range(_revision_table_insert_count(statement))
    )
    if not (
        len(default_privileges) == 1
        and len(functions) == len(_EXPECTED_FUNCTIONS)
        and len(revokes) == 1
        and len(revision_table_inserts) == 1
    ):
        raise AssertionError("required statements are missing or duplicated")
    revision_insert = revision_table_inserts[0]
    if _REVISION_INSERT_PATTERN.fullmatch(revision_insert.canonical) is None:
        raise AssertionError(
            f"unexpected Alembic revision insert: {revision_insert.canonical!r}"
        )

    function_indexes = tuple(statement.index for statement in functions)
    expected_function_indexes = tuple(
        range(function_indexes[0], function_indexes[0] + len(functions))
    )
    if function_indexes != expected_function_indexes:
        raise AssertionError("CREATE FUNCTION statements must be consecutive")
    if not (
        default_privileges[0].index
        < function_indexes[0]
        <= function_indexes[-1]
        < revokes[0].index
        < revision_insert.index
    ):
        raise AssertionError("function ACL and revision statements are out of order")


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

    def test_frozen_revision_defines_exact_function_acl_order(self):
        module = _revision_module()
        with mock.patch.object(module.op, "execute") as execute:
            module.upgrade()
        statements = _scan_sql(
            "\n;\n".join(str(call.args[0]) for call in execute.call_args_list)
        )
        _assert_no_top_level_grants(statements)
        _assert_exact_function_acl(statements)
        _assert_no_routine_owner_changes(statements)
        _assert_exact_function_definitions(statements)

        default_revoke = _statements_of_kind(
            statements, "ALTER DEFAULT PRIVILEGES"
        )[0]
        functions = _statements_of_kind(statements, "CREATE FUNCTION")
        schema_revoke = _statements_of_kind(statements, "REVOKE")[0]
        self.assertLess(default_revoke.index, functions[0].index)
        self.assertEqual(
            tuple(statement.index for statement in functions),
            tuple(range(functions[0].index, functions[0].index + 3)),
        )
        self.assertLess(functions[-1].index, schema_revoke.index)

    def test_global_default_privilege_oracle_rejects_scope_modifiers(self):
        modifiers = (
            ("FOR ROLE harmless_role", "FOR ROLE"),
            ("IN SCHEMA cuevion_account", "IN SCHEMA"),
        )
        for modifier, message in modifiers:
            with self.subTest(modifier=modifier):
                sql = f"""ALTER DEFAULT PRIVILEGES {modifier}
REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA cuevion_account FROM PUBLIC;"""
                statements = _scan_sql(sql)
                self.assertEqual(
                    _statement_kind(statements[0]),
                    "ALTER DEFAULT PRIVILEGES",
                )
                with self.assertRaisesRegex(AssertionError, message):
                    _assert_exact_function_acl(statements)

    def test_function_acl_inventory_captures_every_grant_form(self):
        unsafe = """GRANT
ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA cuevion_account
TO application_runtime;
GRANT EXECUTE
ON FUNCTION cuevion_account.fn_reject_append_only_change()
TO PUBLIC;"""
        self.assertEqual(
            tuple(
                statement.canonical
                for statement in _statements_of_kind(_scan_sql(unsafe), "GRANT")
            ),
            (
                "grant all privileges on all functions in schema "
                "cuevion_account to application_runtime",
                "grant execute on function "
                "cuevion_account.fn_reject_append_only_change() to public",
            ),
        )
        with self.assertRaisesRegex(AssertionError, "top-level GRANT"):
            _assert_no_top_level_grants(_scan_sql(unsafe))

    def test_function_and_revision_oracles_reject_alternate_objects(self):
        def expected_function(name: str) -> str:
            return f"""CREATE FUNCTION cuevion_account.{name}()
RETURNS void
LANGUAGE sql
SET search_path = pg_catalog, pg_temp
AS $body$ SELECT 1 $body$;"""

        alternate_function = """CREATE
OR REPLACE
FUNCTION "cuevion_account"."unexpected"(value integer)
RETURNS integer
LANGUAGE sql
SET search_path = pg_catalog, pg_temp
AS $other$ SELECT value $other$;"""
        function_sql = "\n".join(
            (
                expected_function(_EXPECTED_FUNCTIONS[0]),
                alternate_function,
                expected_function(_EXPECTED_FUNCTIONS[2]),
            )
        )
        statements = _scan_sql(function_sql)
        self.assertEqual(_statement_kind(statements[1]), "CREATE FUNCTION")
        with self.assertRaisesRegex(
            AssertionError, "unexpected CREATE FUNCTION inventory"
        ):
            _assert_exact_function_definitions(statements)
        self.assertIsNone(
            _CREATE_FUNCTION_NAME_PATTERN.search(statements[1].canonical)
        )

        def ordered_revision_sql(*revision_inserts: str) -> str:
            return "\n".join(
                (
                    _EXPECTED_FUNCTION_PRIVILEGE_STATEMENTS[0] + ";",
                    *(expected_function(name) for name in _EXPECTED_FUNCTIONS),
                    _EXPECTED_FUNCTION_PRIVILEGE_STATEMENTS[1] + ";",
                    *revision_inserts,
                )
            )

        for revision in ("0001_ACCOUNT_SCHEMA_1", "wrong_revision"):
            with self.subTest(revision=revision):
                ordered_sql = ordered_revision_sql(
                    "INSERT INTO public.cuevion_account_alembic_version "
                    f"(version_num) VALUES ('{revision}');"
                )
                with self.assertRaisesRegex(
                    AssertionError, "unexpected Alembic revision insert"
                ):
                    _assert_offline_statement_order(_scan_sql(ordered_sql))

        exact_revision_insert = (
            "INSERT INTO public.cuevion_account_alembic_version "
            "(version_num) VALUES ('0001_account_schema_1');"
        )
        alternate_revision_inserts = (
            'INSERT INTO "public"."cuevion_account_alembic_version" '
            "(version_num) VALUES ('wrong_revision');",
            "INSERT INTO cuevion_account_alembic_version "
            "(version_num) VALUES ('wrong_revision');",
            'INSERT INTO "cuevion_account_alembic_version" '
            "(version_num) VALUES ('wrong_revision');",
            "WITH source AS (SELECT 'wrong_revision' AS version_num) "
            "INSERT INTO public.cuevion_account_alembic_version "
            "(version_num) SELECT version_num FROM source;",
            "WITH RECURSIVE source(version_num) AS ("
            "VALUES ('wrong_revision')) "
            "INSERT INTO public.cuevion_account_alembic_version "
            "(version_num) SELECT version_num FROM source;",
            "WITH inserted AS ("
            "INSERT INTO public.cuevion_account_alembic_version "
            "(version_num) VALUES ('wrong_revision') RETURNING version_num"
            ") SELECT version_num FROM inserted;",
            "INSERT/* ledger */INTO public."
            "/* table */cuevion_account_alembic_version "
            "(version_num) VALUES ('wrong_revision');",
            'WITH source AS (SELECT \'wrong_revision\' AS version_num) '
            'INSERT INTO "public"."cuevion_account_alembic_version" '
            "(version_num) SELECT version_num FROM source;",
            "SELECT 1; INSERT/* ledger */INTO "
            '"public"."cuevion_account_alembic_version" '
            "(version_num) VALUES ('wrong_revision');",
        )
        for extra_insert in alternate_revision_inserts:
            with self.subTest(extra_insert=extra_insert):
                with self.assertRaisesRegex(
                    AssertionError, "missing or duplicated"
                ):
                    _assert_offline_statement_order(
                        _scan_sql(
                            ordered_revision_sql(
                                exact_revision_insert, extra_insert
                            )
                        )
                    )


class SqlScannerTests(unittest.TestCase):
    def test_scanner_detects_comment_prefixed_grant(self):
        statements = _scan_sql(
            "/* acl */ GRANT EXECUTE ON FUNCTION x() TO PUBLIC;"
        )
        self.assertEqual(len(statements), 1)
        self.assertEqual(_statement_kind(statements[0]), "GRANT")
        self.assertTrue(statements[0].original.startswith("/* acl */"))
        self.assertEqual(
            statements[0].canonical,
            "grant execute on function x() to public",
        )
        with self.assertRaisesRegex(AssertionError, "top-level GRANT"):
            _assert_no_top_level_grants(statements)

    def test_line_comments_end_at_all_postgresql_newlines(self):
        for newline in ("\n", "\r", "\r\n"):
            with self.subTest(newline=repr(newline)):
                statements = _scan_sql(
                    "-- acl; COMMIT"
                    + newline
                    + "GRANT SELECT ON TABLE x TO PUBLIC;"
                )
                self.assertEqual(len(statements), 1)
                self.assertEqual(_statement_kind(statements[0]), "GRANT")
                with self.assertRaisesRegex(AssertionError, "top-level GRANT"):
                    _assert_no_top_level_grants(statements)

    def test_scanner_detects_grant_after_same_line_statement(self):
        statements = _scan_sql(
            "SELECT 1; GRANT EXECUTE ON FUNCTION x() TO PUBLIC;"
        )
        self.assertEqual(len(statements), 2)
        self.assertEqual(statements[1].index, 1)
        self.assertEqual(_statement_kind(statements[1]), "GRANT")
        with self.assertRaisesRegex(AssertionError, "top-level GRANT"):
            _assert_no_top_level_grants(statements)

    def test_keyword_detection_uses_ascii_identifier_boundaries(self):
        fixtures = (
            ("GRANT SELECT ON x TO y;", "GRANT"),
            ("grant SELECT ON x TO y;", "GRANT"),
            ("xGRANT SELECT ON x TO y;", None),
            ("GRANTx SELECT ON x TO y;", None),
            ("ΩGRANT SELECT ON x TO y;", None),
            ("GRANTΩ SELECT ON x TO y;", None),
            ("(GRANT SELECT ON x TO y);", "GRANT"),
            (";GRANT SELECT ON x TO y;", "GRANT"),
        )
        for sql, expected_kind in fixtures:
            with self.subTest(sql=sql):
                statements = _scan_sql(sql)
                self.assertEqual(len(statements), 1)
                self.assertEqual(_statement_kind(statements[0]), expected_kind)

    def test_scanner_detects_comment_prefixed_revoke(self):
        extra_revoke = "/* acl */ REVOKE EXECUTE ON FUNCTION x() FROM PUBLIC;"
        statements = _scan_sql(
            "ALTER DEFAULT PRIVILEGES "
            "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;"
            + extra_revoke
        )
        self.assertEqual(_statement_kind(statements[1]), "REVOKE")
        self.assertEqual(
            statements[1].canonical,
            "revoke execute on function x() from public",
        )
        with self.assertRaisesRegex(AssertionError, "REVOKE inventory"):
            _assert_exact_function_acl(statements)

    def test_scanner_accepts_comment_prefixed_exact_default_acl(self):
        statements = _scan_sql(
            "/* acl */ ALTER DEFAULT PRIVILEGES "
            "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;"
            "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA cuevion_account "
            "FROM PUBLIC;"
        )
        self.assertEqual(
            tuple(statement.canonical for statement in statements),
            _EXPECTED_FUNCTION_PRIVILEGE_STATEMENTS,
        )
        _assert_exact_function_acl(statements)

    def test_scanner_detects_comment_prefixed_create_function(self):
        statements = _scan_sql(
            "/* acl */ CREATE FUNCTION x() RETURNS void "
            "LANGUAGE sql AS $$SELECT 1;$$;"
        )
        self.assertEqual(len(statements), 1)
        self.assertEqual(_statement_kind(statements[0]), "CREATE FUNCTION")

    def test_scanner_keeps_same_line_function_body_semicolons_opaque(self):
        statements = _scan_sql(
            "SELECT 1; CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "AS $$SELECT 1; SELECT 2;$$;"
        )
        self.assertEqual(len(statements), 2)
        self.assertEqual(statements[0].canonical, "select 1")
        self.assertEqual(_statement_kind(statements[1]), "CREATE FUNCTION")
        self.assertNotIn("select 1", statements[1].keyword_sql)
        self.assertNotIn("select 2", statements[1].keyword_sql)

    def test_security_oracle_rejects_all_attribute_formatting(self):
        separators = (" ", "\n", " /* acl */ ")
        for separator in separators:
            with self.subTest(separator=separator):
                sql = f"""CREATE FUNCTION x()
RETURNS void
LANGUAGE plpgsql
SECURITY{separator}DEFINER
AS $body$
BEGIN
    PERFORM 1;
END;
$body$;"""
                with self.assertRaisesRegex(
                    AssertionError, "SECURITY DEFINER"
                ):
                    _assert_no_security_definer(_scan_sql(sql))

    def test_security_oracle_rejects_alter_routine_escalation(self):
        fixtures = (
            "ALTER FUNCTION x() SECURITY DEFINER;",
            "ALTER FUNCTION x() SECURITY /* acl */ DEFINER;",
            "SELECT 1; ALTER PROCEDURE x() SECURITY DEFINER;",
            "ALTER ROUTINE x() SECURITY\nDEFINER;",
        )
        for sql in fixtures:
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(
                    AssertionError, "SECURITY DEFINER"
                ):
                    _assert_no_security_definer(_scan_sql(sql))

    def test_function_attribute_oracle_preserves_fixed_search_path(self):
        allowed = (
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "SET search_path = pg_catalog, pg_temp AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql SECURITY INVOKER "
            "SET /* scope */ search_path = pg_catalog, pg_temp "
            "AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "/* SECURITY DEFINER */ "
            "SET search_path = pg_catalog, pg_temp AS $$SELECT 1$$;",
        )
        for sql in allowed:
            with self.subTest(allowed=sql):
                _assert_safe_function_attributes(_scan_sql(sql))

        unsafe = (
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "SET search_path = pg_catalog, pg_temp, public AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "SET search_path = pg_catalog AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "SET search_path = pg_catalog, pg_temp$evil AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "SET search_path = pg_catalog, pg_temp\u0301 AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "AS $$SET search_path = pg_catalog, pg_temp;$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "SET search_path = pg_catalog, pg_temp "
            "SET \"search_path\" = public AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "SET search_path = pg_catalog, pg_temp "
            "SET \"SEARCH_PATH\" = public AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "SET search_path = pg_catalog, pg_temp "
            "SET U&\"search_path\" = public AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "SET search_path = pg_catalog, pg_temp "
            "SET work_mem = '64MB' AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "SET U&\"search_path\" = public AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "SET U&\"search\\005Fpath\" = public AS $$SELECT 1$$;",
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "SET U&\"search!005Fpath\" UESCAPE '!' = public "
            "AS $$SELECT 1$$;",
        )
        for sql in unsafe:
            with self.subTest(unsafe=sql):
                with self.assertRaisesRegex(AssertionError, "search_path"):
                    _assert_safe_function_attributes(_scan_sql(sql))

    def test_owner_oracle_rejects_every_role_and_routine_kind(self):
        fixtures = (
            "ALTER FUNCTION x() OWNER TO application_runtime;",
            "ALTER FUNCTION x() OWNER TO cuevion_preview_runtime;",
            "ALTER FUNCTION x() OWNER TO harmless_role;",
            "ALTER FUNCTION x() OWNER /* comment */ TO application_runtime;",
            "SELECT 1; ALTER FUNCTION x() OWNER TO harmless_role;",
            "ALTER PROCEDURE x() OWNER TO harmless_role;",
            "ALTER ROUTINE x() OWNER TO harmless_role;",
        )
        for sql in fixtures:
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(AssertionError, "OWNER TO"):
                    _assert_no_routine_owner_changes(_scan_sql(sql))

    def test_routine_alter_inventory_is_fail_closed(self):
        fixtures = (
            "ALTER FUNCTION x() RENAME TO y;",
            "ALTER FUNCTION x() SET search_path = public;",
            'ALTER FUNCTION x() SET "search_path" TO public;',
            'ALTER FUNCTION x() RESET "search_path";',
            "ALTER PROCEDURE x() SET SCHEMA public;",
            "ALTER ROUTINE x() RESET ALL;",
        )
        for sql in fixtures:
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(AssertionError, "routine ALTER"):
                    _assert_no_unexpected_routine_alters(_scan_sql(sql))

    def test_function_inventory_rejects_procedures_and_routines(self):
        fixtures = (
            "/* ddl */ CREATE PROCEDURE x() LANGUAGE sql AS $$SELECT 1$$;",
            "SELECT 1; CREATE ROUTINE x() LANGUAGE sql AS $$SELECT 1$$;",
        )
        for sql in fixtures:
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(
                    AssertionError, "CREATE PROCEDURE/ROUTINE"
                ):
                    _assert_exact_function_definitions(_scan_sql(sql))

    def test_transaction_oracle_accepts_only_exact_boundary_pair(self):
        statements = _scan_sql("/* tx */ BEGIN; SELECT 1; COMMIT;")
        _assert_exact_transaction_structure(statements)
        self.assertEqual(statements[0].index, 0)
        self.assertEqual(statements[-1].index, 2)

    def test_transaction_oracle_rejects_all_alternate_forms(self):
        fixtures = (
            ("START TRANSACTION;", "START TRANSACTION", "start transaction"),
            ("BEGIN WORK;", "BEGIN", "begin work"),
            ("BEGIN TRANSACTION;", "BEGIN", "begin transaction"),
            ("/* tx */ COMMIT WORK;", "COMMIT", "commit work"),
            ("COMMIT TRANSACTION;", "COMMIT", "commit transaction"),
            ("SELECT 1; END;", "END", "end"),
            ("END WORK;", "END", "end work"),
            ("END TRANSACTION;", "END", "end transaction"),
            ("ROLLBACK;", "ROLLBACK", "rollback"),
            ("ABORT;", "ABORT", "abort"),
            (
                "PREPARE TRANSACTION 'migration_x';",
                "PREPARE TRANSACTION",
                "prepare transaction 'migration_x'",
            ),
            ("SAVEPOINT migration_x;", "SAVEPOINT", "savepoint migration_x"),
            (
                "RELEASE SAVEPOINT migration_x;",
                "RELEASE SAVEPOINT",
                "release savepoint migration_x",
            ),
            (
                "SET TRANSACTION READ ONLY;",
                "SET TRANSACTION",
                "set transaction read only",
            ),
            (
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;",
                "SET SESSION TRANSACTION",
                "set session characteristics as transaction read only",
            ),
            (
                "/* tx */ SET CONSTRAINTS ALL IMMEDIATE;",
                "SET CONSTRAINTS",
                "set constraints all immediate",
            ),
            (
                "SELECT 1; SET CONSTRAINTS ALL DEFERRED;",
                "SET CONSTRAINTS",
                "set constraints all deferred",
            ),
            (
                "SET /* tx */ CONSTRAINTS constraint_a IMMEDIATE;",
                "SET CONSTRAINTS",
                "set constraints constraint_a immediate",
            ),
            (
                "SET CONSTRAINTS constraint_a, constraint_b DEFERRED;",
                "SET CONSTRAINTS",
                "set constraints constraint_a, constraint_b deferred",
            ),
            (
                "ROLLBACK TO SAVEPOINT migration_x;",
                "ROLLBACK",
                "rollback to savepoint migration_x",
            ),
        )
        for sql, expected_kind, expected_canonical in fixtures:
            with self.subTest(sql=sql):
                forbidden = _scan_sql(sql)[-1]
                self.assertEqual(_statement_kind(forbidden), expected_kind)
                self.assertEqual(forbidden.canonical, expected_canonical)
                with self.assertRaisesRegex(
                    AssertionError, "transaction inventory"
                ):
                    _assert_exact_transaction_structure(
                        _scan_sql(f"BEGIN; SELECT 0; {sql} COMMIT;")
                    )

        duplicates = (
            "BEGIN; BEGIN; SELECT 1; COMMIT;",
            "BEGIN; SELECT 1; COMMIT; COMMIT;",
        )
        for sql in duplicates:
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(
                    AssertionError, "transaction inventory"
                ):
                    _assert_exact_transaction_structure(_scan_sql(sql))

    def test_transaction_oracle_uses_statement_indexes(self):
        fixtures = (
            "SELECT 1; BEGIN; SELECT 2; COMMIT;",
            "BEGIN; SELECT 1; COMMIT; SELECT 2;",
            "SELECT 1; COMMIT;",
            "BEGIN; SELECT 1; SELECT 2; END; COMMIT;",
        )
        for sql in fixtures:
            with self.subTest(sql=sql):
                with self.assertRaises(AssertionError):
                    _assert_exact_transaction_structure(_scan_sql(sql))

    def test_dollar_bodies_hide_statement_and_attribute_keywords(self):
        sql = """CREATE FUNCTION x()
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $body$
BEGIN
    PERFORM 'GRANT; REVOKE; COMMIT; SECURITY DEFINER';
    -- GRANT EXECUTE; COMMIT;
    /* REVOKE /* nested */ SECURITY DEFINER */
    PERFORM 1;
END;
$body$;"""
        statements = _scan_sql(sql)
        self.assertEqual(len(statements), 1)
        self.assertEqual(_statement_kind(statements[0]), "CREATE FUNCTION")
        _assert_no_top_level_grants(statements)
        _assert_no_security_definer(statements)
        self.assertEqual(
            len(tuple(_SAFE_SEARCH_PATH_PATTERN.finditer(statements[0].keyword_sql))),
            1,
        )
        for hidden in ("grant", "revoke", "commit"):
            self.assertNotIn(hidden, statements[0].keyword_sql)

    def test_scanner_supports_both_dollar_quote_forms(self):
        statements = _scan_sql(
            "SELECT $$one; -- /* SECURITY DEFINER$$; "
            "SELECT $tag$two; GRANT; COMMIT$tag$;"
        )
        self.assertEqual(len(statements), 2)
        self.assertEqual(
            tuple(statement.keyword_sql for statement in statements),
            (
                "select __dollar_quoted_body__",
                "select __dollar_quoted_body__",
            ),
        )

    def test_scanner_supports_postgresql_unicode_dollar_tags(self):
        statements = _scan_sql(
            "SELECT $täg$GRANT; COMMIT; SECURITY DEFINER$täg$; SELECT 1;"
        )
        self.assertEqual(len(statements), 2)
        self.assertEqual(
            statements[0].keyword_sql, "select __dollar_quoted_body__"
        )
        self.assertIsNone(_statement_kind(statements[0]))
        self.assertEqual(statements[1].canonical, "select 1")

    def test_strings_identifiers_and_comments_do_not_create_false_positives(self):
        sql = """-- GRANT EXECUTE; COMMIT;
/* outer /* REVOKE; */ SECURITY DEFINER */
SELECT 'GRANT EXECUTE; COMMIT; SECURITY DEFINER',
       'it''s still one string',
       "GRANT;""SECURITY DEFINER";"""
        statements = _scan_sql(sql)
        self.assertEqual(len(statements), 1)
        self.assertIsNone(_statement_kind(statements[0]))
        self.assertEqual(
            statements[0].keyword_sql,
            "select __sql_string__ , __sql_string__ , __quoted_identifier__",
        )
        _assert_no_top_level_grants(statements)
        _assert_no_security_definer(statements)

    def test_single_quoted_function_body_does_not_fake_security_attribute(self):
        statements = _scan_sql(
            "CREATE FUNCTION x() RETURNS void LANGUAGE sql "
            "AS 'SELECT ''SECURITY DEFINER; GRANT; COMMIT''';"
        )
        self.assertEqual(len(statements), 1)
        self.assertEqual(_statement_kind(statements[0]), "CREATE FUNCTION")
        _assert_no_security_definer(statements)

    def test_postgresql_escape_strings_cannot_hide_security_statements(self):
        grant_sql = (
            r"SELECT E'one\''; GRANT harmless_role TO application_runtime; "
            r"SELECT e'two\''; SELECT 1;"
        )
        grant_statements = _scan_sql(grant_sql)
        self.assertEqual(len(grant_statements), 4)
        self.assertEqual(_statement_kind(grant_statements[1]), "GRANT")
        with self.assertRaisesRegex(AssertionError, "top-level GRANT"):
            _assert_no_top_level_grants(grant_statements)

        transaction_sql = (
            r"BEGIN; SELECT E'one\''; COMMIT WORK; "
            r"SELECT E'two\''; SELECT 1; COMMIT;"
        )
        transaction_statements = _scan_sql(transaction_sql)
        self.assertEqual(
            tuple(
                statement.canonical
                for statement in _statements_of_kind(
                    transaction_statements, "BEGIN", "COMMIT"
                )
            ),
            ("begin", "commit work", "commit"),
        )
        with self.assertRaisesRegex(AssertionError, "transaction inventory"):
            _assert_exact_transaction_structure(transaction_statements)

    def test_scanner_fails_closed_on_unterminated_constructs(self):
        fixtures = (
            ("SELECT 'unterminated", "single-quoted string"),
            (r"SELECT E'unterminated\'", "single-quoted string"),
            ('SELECT "unterminated', "double-quoted identifier"),
            ("SELECT 1 /* outer /* nested */", "block comment"),
            ("SELECT $tag$unterminated;", "dollar quote \\$tag\\$"),
            ("SELECT $täg$unterminated;", "dollar quote \\$täg\\$"),
        )
        for sql, message in fixtures:
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(_SqlScanError, message):
                    _scan_sql(sql)


class OfflineMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = _offline_sql()
        cls.statements = _scan_sql(cls.sql)
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
        _assert_exact_function_definitions(self.statements)
        self.assertEqual(_trigger_inventory(self.sql), _EXPECTED_TRIGGERS)

    def test_function_execute_acl_statements_are_exact_and_role_neutral(self):
        _assert_no_top_level_grants(self.statements)
        _assert_exact_function_acl(self.statements)
        _assert_no_routine_owner_changes(self.statements)

    def test_function_execute_acl_is_ordered_in_one_revision_transaction(self):
        _assert_exact_transaction_structure(self.statements)
        _assert_offline_statement_order(self.statements)

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
