"""Offline tests for the PostgreSQL mailbox repository boundary."""

from __future__ import annotations

from pathlib import Path
import unittest

from cuevion_mailbox import repository_contract as contract
from cuevion_mailbox.postgresql_access import (
    MAILBOX_READER_TABLES,
    MAILBOX_WRITER_TABLES,
    role_grant_statements,
)


_FRONTEND = Path(__file__).resolve().parents[2]
_REPOSITORY_SOURCE = (
    _FRONTEND / "cuevion_mailbox" / "postgresql_repository.py"
)
_ACCESS_SOURCE = (
    _FRONTEND / "cuevion_mailbox" / "postgresql_access.py"
)


class RoleGrantTests(unittest.TestCase):
    def test_reader_and_writer_privileges_are_exact_and_separate(self):
        self.assertEqual(
            MAILBOX_READER_TABLES,
            (
                "mailbox_sync_state",
                "mailbox_sync_cursor",
                "mailbox_messages",
                "mailbox_message_bodies",
            ),
        )
        self.assertEqual(
            MAILBOX_WRITER_TABLES,
            (
                *MAILBOX_READER_TABLES,
                "mailbox_change_outbox",
            ),
        )
        statements = role_grant_statements(
            "cuevion_test_mailbox_reader_v1",
            "cuevion_test_mailbox_writer_v1",
        )
        normalized = "\n".join(statements).casefold()
        self.assertIn(
            "grant usage on schema cuevion_mailbox to "
            "cuevion_test_mailbox_reader_v1",
            normalized,
        )
        self.assertIn(
            "grant select on cuevion_mailbox.mailbox_sync_state",
            normalized,
        )
        self.assertNotIn(
            "mailbox_change_outbox to cuevion_test_mailbox_reader_v1",
            normalized,
        )
        self.assertIn(
            "grant select, insert, update on "
            "cuevion_mailbox.mailbox_sync_state",
            normalized,
        )
        for forbidden in (
            "cuevion_account.",
            "delete",
            "truncate",
            "references",
            "create ",
            "alter ",
            "grant all",
            "default privileges",
        ):
            self.assertNotIn(forbidden, normalized)

    def test_role_names_are_closed_lowercase_postgresql_identifiers(self):
        for invalid in (
            "",
            "Reader",
            "reader-role",
            "1reader",
            "reader;drop",
            "r" * 64,
        ):
            with self.subTest(role=invalid):
                with self.assertRaises(ValueError):
                    role_grant_statements(invalid, "writer")
        with self.assertRaises(ValueError):
            role_grant_statements("same", "same")


class RepositorySourceBoundaryTests(unittest.TestCase):
    def test_adapter_is_inert_and_has_no_account_or_environment_access(self):
        source = _REPOSITORY_SOURCE.read_text(encoding="utf-8")
        normalized = source.casefold()
        for forbidden in (
            "os.environ",
            "getenv(",
            "psycopg.connect(",
            "cuevion_account.",
            " delete ",
            " truncate ",
        ):
            self.assertNotIn(forbidden, normalized)
        self.assertIn(
            "set transaction isolation level repeatable read read only",
            normalized,
        )
        self.assertIn(
            "set transaction isolation level serializable",
            normalized,
        )
        self.assertIn("for update of outbox skip locked", normalized)

    def test_adapter_sql_is_tenant_generation_scoped(self):
        source = _REPOSITORY_SOURCE.read_text(encoding="utf-8")
        for name in (
            "_SELECT_CURSOR",
            "_SELECT_BODY",
            "_SELECT_MESSAGE_FOR_BODY_UPDATE",
            "_SELECT_MESSAGE_FOR_DELTA",
            "_UPDATE_MESSAGE",
            "_TOMBSTONE_MESSAGE",
            "_UPDATE_SYNC_STATE_AFTER_DELTA",
        ):
            start = source.index(name + " = ")
            quote = source.index('"""', start)
            end = source.index('""".strip()', quote + 3)
            statement = source[quote + 3 : end].casefold()
            self.assertIn("workspace_id", statement, name)
            self.assertIn("owner_user_id", statement, name)
            self.assertIn("mailbox_id", statement, name)

    def test_access_module_is_pure_and_role_neutral(self):
        source = _ACCESS_SOURCE.read_text(encoding="utf-8").casefold()
        for forbidden in (
            "psycopg",
            "os.environ",
            "password",
            "cuevion_auth_writer",
            "cuevion_production_current_account_reader_v1",
        ):
            self.assertNotIn(forbidden, source)


class DeterministicIdentifierTests(unittest.TestCase):
    def setUp(self):
        self.scope = contract.MailboxScope(
            workspace_id="wsp_AAAAAAAAAAAAAAAAAAAAAA",
            owner_user_id="usr_BBBBBBBBBBBBBBBBBBBBBB",
            mailbox_id="main",
            source_generation=1,
            provider=contract.MailboxProvider.GOOGLE,
            provider_account_identity="owner@example.com",
        )

    def test_message_id_is_stable_and_generation_scoped(self):
        first = contract.derive_message_id(
            self.scope,
            provider_message_id="provider-1",
            provider_folder="INBOX",
            imap_uid_validity=None,
            imap_uid=None,
        )
        second = contract.derive_message_id(
            self.scope,
            provider_message_id="provider-1",
            provider_folder="Archive",
            imap_uid_validity=None,
            imap_uid=None,
        )
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("mbm_"))
        self.assertEqual(len(first), 26)

        next_generation = contract.MailboxScope(
            workspace_id=self.scope.workspace_id,
            owner_user_id=self.scope.owner_user_id,
            mailbox_id=self.scope.mailbox_id,
            source_generation=2,
            provider=self.scope.provider,
            provider_account_identity=self.scope.provider_account_identity,
        )
        self.assertNotEqual(
            first,
            contract.derive_message_id(
                next_generation,
                provider_message_id="provider-1",
                provider_folder="INBOX",
                imap_uid_validity=None,
                imap_uid=None,
            ),
        )

    def test_outbox_id_is_stable_and_event_version_scoped(self):
        message_id = contract.derive_message_id(
            self.scope,
            provider_message_id="provider-2",
            provider_folder="INBOX",
            imap_uid_validity=None,
            imap_uid=None,
        )
        first = contract.derive_outbox_event_id(
            self.scope,
            message_id=message_id,
            message_row_version=1,
            event_type=contract.OutboxEventType.MESSAGE_ADDED,
        )
        self.assertEqual(
            first,
            contract.derive_outbox_event_id(
                self.scope,
                message_id=message_id,
                message_row_version=1,
                event_type=contract.OutboxEventType.MESSAGE_ADDED,
            ),
        )
        self.assertNotEqual(
            first,
            contract.derive_outbox_event_id(
                self.scope,
                message_id=message_id,
                message_row_version=2,
                event_type=contract.OutboxEventType.MESSAGE_CHANGED,
            ),
        )
        self.assertTrue(first.startswith("mbe_"))
        self.assertEqual(len(first), 26)


if __name__ == "__main__":
    unittest.main()
