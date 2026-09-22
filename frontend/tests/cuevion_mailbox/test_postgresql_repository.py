"""Security and structure tests for the inert PostgreSQL mailbox adapter."""

import ast
from pathlib import Path
import unittest
from unittest.mock import patch

from cuevion_mailbox import postgresql_repository as repository
from cuevion_mailbox.repository_contract import (
    MailboxProvider,
    MailboxReadAuthority,
    OutboxStorageScope,
)
from cuevion_mailbox.role_policy import build_mailbox_role_plan


_FRONTEND = Path(__file__).resolve().parents[2]
_ADAPTER = _FRONTEND / "cuevion_mailbox" / "postgresql_repository.py"
_ROLE_POLICY = _FRONTEND / "cuevion_mailbox" / "role_policy.py"


class MailboxRolePolicyTests(unittest.TestCase):
    def test_creation_is_restricted_and_password_free(self):
        plan = build_mailbox_role_plan(
            "cuevion_test_mailbox_reader_v1",
            "cuevion_test_mailbox_writer_v1",
        )
        self.assertEqual(len(plan.creation_statements), 2)
        for statement in plan.creation_statements:
            normalized = statement.casefold()
            self.assertIn(" login ", normalized)
            self.assertIn(" nosuperuser ", normalized)
            self.assertIn(" nocreatedb ", normalized)
            self.assertIn(" nocreaterole ", normalized)
            self.assertIn(" noinherit ", normalized)
            self.assertIn(" nobypassrls", normalized)
            self.assertNotIn("password", normalized)
            self.assertNotIn("neon_superuser", normalized)

    def test_reader_and_writer_grants_are_exactly_bounded(self):
        plan = build_mailbox_role_plan(
            "cuevion_test_mailbox_reader_v1",
            "cuevion_test_mailbox_writer_v1",
        )
        sql = "\n".join(plan.grant_statements).casefold()
        self.assertIn("grant usage on schema cuevion_mailbox", sql)
        self.assertIn("grant select on cuevion_mailbox.mailbox_sync_state", sql)
        self.assertIn("grant select, insert, update on cuevion_mailbox.mailbox_sync_state", sql)
        reader_select = next(
            statement.casefold()
            for statement in plan.grant_statements
            if statement.casefold().startswith("grant select on ")
        )
        self.assertNotIn("mailbox_change_outbox", reader_select)
        writer_grant = next(
            statement.casefold()
            for statement in plan.grant_statements
            if statement.casefold().startswith("grant select, insert, update on ")
        )
        self.assertIn("mailbox_change_outbox", writer_grant)
        for forbidden in (
            " delete ",
            " truncate ",
            " references ",
            " trigger ",
            "cuevion_account.",
            "grant create",
        ):
            self.assertNotIn(forbidden, sql)

    def test_invalid_or_equal_roles_fail_closed(self):
        with self.assertRaises(ValueError):
            build_mailbox_role_plan("reader", "reader")
        with self.assertRaises(ValueError):
            build_mailbox_role_plan("Reader-Unsafe", "writer_safe")


class MailboxReadAuthorityTests(unittest.TestCase):
    def test_authority_rejects_non_enum_provider_and_out_of_bounds_identity(self):
        common = {
            "workspace_id": "wsp_" + ("a" * 22),
            "owner_user_id": "usr_" + ("b" * 22),
            "mailbox_id": "mailbox-1",
        }
        with self.assertRaises(ValueError):
            MailboxReadAuthority(
                **common,
                provider="google",  # type: ignore[arg-type]
                provider_account_identity="user@example.com",
            )
        with self.assertRaises(ValueError):
            MailboxReadAuthority(
                **common,
                provider=MailboxProvider.GOOGLE,
                provider_account_identity="x",
            )


class PostgreSQLMailboxReaderDelegationTests(unittest.TestCase):
    def test_current_scope_lookup_delegates_to_repository(self):
        authority = MailboxReadAuthority(
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            provider=MailboxProvider.GOOGLE,
            provider_account_identity="verified@gmail.com",
        )
        expected = object()
        reader = repository.PostgreSQLMailboxReaderRepository(lambda: None)
        with patch.object(
            repository.PostgreSQLMailboxRepository,
            "resolve_current_scope",
            return_value=expected,
        ) as resolve:
            self.assertIs(reader.resolve_current_scope(authority), expected)
        resolve.assert_called_once_with(authority)


class PostgreSQLMailboxAdapterTests(unittest.TestCase):
    def test_module_has_no_runtime_configuration_or_network_boundary(self):
        source = _ADAPTER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertNotIn("os", imported)
        self.assertNotIn("socket", imported)
        self.assertNotIn("urllib", imported)
        for forbidden in (
            "DATABASE_URL",
            "os.environ",
            "psycopg.connect(",
            "requests.",
            "httpx.",
        ):
            self.assertNotIn(forbidden, source)

    def test_reader_public_surface_exposes_no_write_methods(self):
        reader = repository.PostgreSQLMailboxReaderRepository
        for forbidden in (
            "commit_provider_delta",
            "claim_outbox_batch",
            "mark_outbox_processed",
            "mark_outbox_retry",
        ):
            self.assertFalse(hasattr(reader, forbidden))

    def test_read_sql_reproves_current_mailbox_authority(self):
        for sql in (
            repository._SELECT_CURRENT_SCOPE_SQL,
            repository._SELECT_CURSOR_SQL,
            repository._LIST_MESSAGES_SQL,
            repository._SELECT_BODY_SQL,
        ):
            normalized = " ".join(sql.casefold().split())
            self.assertIn("provider_account_identity = %s", normalized)
            self.assertIn("is_current = true", normalized)

    def test_current_scope_lookup_is_exact_and_current_only(self):
        normalized = " ".join(
            repository._SELECT_CURRENT_SCOPE_SQL.casefold().split()
        )
        for required in (
            "workspace_id = %s",
            "owner_user_id = %s",
            "mailbox_id = %s",
            "provider = %s",
            "provider_account_identity = %s",
            "is_current = true",
        ):
            self.assertIn(required, normalized)

    def test_mutation_sql_uses_cas_and_exact_provider_identity(self):
        for sql in (
            repository._UPDATE_MESSAGE_SQL,
            repository._TOMBSTONE_MESSAGE_SQL,
        ):
            normalized = " ".join(sql.casefold().split())
            self.assertIn("row_version = %s", normalized)
            self.assertIn("provider_message_id = %s", normalized)
            self.assertIn("provider_folder_digest = %s", normalized)
            self.assertIn("imap_uid_validity = %s", normalized)
            self.assertIn("imap_uid = %s", normalized)

    def test_outbox_claims_are_skip_locked_unique_and_expiring(self):
        claim = " ".join(repository._CLAIM_OUTBOX_SQL.casefold().split())
        self.assertIn("for update skip locked", claim)
        self.assertIn("gen_random_uuid()", claim)
        self.assertIn("claim_expires_at", claim)
        for sql in (
            repository._MARK_OUTBOX_PROCESSED_SQL,
            repository._MARK_OUTBOX_RETRY_SQL,
        ):
            self.assertIn(
                "claim_expires_at > %s",
                " ".join(sql.casefold().split()),
            )

    def test_fixed_sql_placeholder_inventory(self):
        expected = {
            "_SELECT_CURRENT_SCOPE_SQL": 5,
            "_SELECT_CURSOR_SQL": 7,
            "_LIST_MESSAGES_SQL": 9,
            "_SELECT_BODY_SQL": 7,
            "_LOCK_STATE_SQL": 4,
            "_LOCK_CURSOR_SQL": 6,
            "_INSERT_CURSOR_SQL": 17,
            "_UPDATE_CURSOR_SQL": 17,
            "_UPDATE_STATE_SQL": 10,
            "_INSERT_MESSAGE_SQL": 29,
            "_UPDATE_MESSAGE_SQL": 31,
            "_TOMBSTONE_MESSAGE_SQL": 13,
            "_INSERT_OUTBOX_SQL": 9,
            "_CLAIM_OUTBOX_SQL": 4,
            "_MARK_OUTBOX_PROCESSED_SQL": 4,
            "_MARK_OUTBOX_RETRY_SQL": 5,
        }
        self.assertEqual(
            {
                name: getattr(repository, name).count("%s")
                for name in expected
            },
            expected,
        )

    def test_outbox_storage_scope_has_no_provider_authority(self):
        fields = set(OutboxStorageScope.__dataclass_fields__)
        self.assertEqual(
            fields,
            {"workspace_id", "owner_user_id", "mailbox_id", "source_generation"},
        )


if __name__ == "__main__":
    unittest.main()
