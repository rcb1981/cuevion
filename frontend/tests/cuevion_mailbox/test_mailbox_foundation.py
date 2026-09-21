"""Tests for the inactive durable mailbox sync foundation."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateSchema, CreateTable

from cuevion_mailbox.repository_contract import (
    BackfillState,
    BodyState,
    BootstrapState,
    MailboxParty,
    MailboxProvider,
    MailboxScope,
    MessageIdentity,
    MessageMutation,
    MessageMutationKind,
    MessageProjection,
    MessageWrite,
    OutboxEventType,
    ProviderDeltaCommit,
    SyncCursor,
    derive_locator_digest,
    derive_message_id,
)
from cuevion_mailbox.schema import (
    MAILBOX_SCHEMA,
    MAILBOX_SCHEMA_VERSION,
    MAILBOX_TABLES,
    mailbox_change_outbox,
    mailbox_messages,
    metadata,
)


_FRONTEND = Path(__file__).resolve().parents[2]
_REVISION = _FRONTEND / "migrations" / "versions" / "0002_mailbox_schema_1.py"


def _canonical(statement: str) -> str:
    return " ".join(statement.replace("%%", "%").split())


def _compiled(element) -> str:
    return _canonical(str(element.compile(dialect=postgresql.dialect())))


def _revision_module():
    spec = importlib.util.spec_from_file_location("cuevion_revision_0002", _REVISION)
    if spec is None or spec.loader is None:
        raise AssertionError("revision cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MailboxSchemaTests(unittest.TestCase):
    def test_exact_mailbox_table_inventory_and_external_authority_stub(self):
        self.assertEqual(MAILBOX_SCHEMA, "cuevion_mailbox")
        self.assertEqual(MAILBOX_SCHEMA_VERSION, 1)
        self.assertEqual(
            tuple(table.name for table in MAILBOX_TABLES),
            (
                "mailbox_sync_state",
                "mailbox_sync_cursor",
                "mailbox_messages",
                "mailbox_message_bodies",
                "mailbox_change_outbox",
            ),
        )
        self.assertIn(
            "cuevion_account.workspace_memberships",
            metadata.tables,
        )
        self.assertNotIn(
            metadata.tables["cuevion_account.workspace_memberships"],
            MAILBOX_TABLES,
        )

    def test_exactly_one_current_generation_is_enforced_per_mailbox(self):
        state_indexes = {
            index.name: _compiled(CreateIndex(index)).casefold()
            for index in MAILBOX_TABLES[0].indexes
        }
        current = state_indexes["ux_mailbox_sync_state_current"]
        self.assertIn(
            "(workspace_id, owner_user_id, mailbox_id)",
            current,
        )
        self.assertIn("where is_current is true", current)

    def test_provider_identity_indexes_are_tenant_and_generation_scoped(self):
        indexes = {index.name: _compiled(CreateIndex(index)) for index in mailbox_messages.indexes}
        gmail = indexes["ux_mailbox_messages_gmail_identity"].casefold()
        imap = indexes["ux_mailbox_messages_imap_identity"].casefold()
        for sql in (gmail, imap):
            for column in (
                "workspace_id",
                "owner_user_id",
                "mailbox_id",
                "source_generation",
            ):
                self.assertIn(column, sql)
        self.assertIn("provider_message_id", gmail)
        self.assertIn("where provider = 'google'", gmail)
        self.assertIn("provider_folder_digest", imap)
        self.assertNotIn("provider_folder,", imap)
        self.assertIn("imap_uid_validity", imap)
        self.assertIn("imap_uid", imap)
        self.assertIn("where provider = 'custom_imap'", imap)

    def test_sync_state_is_bound_to_canonical_workspace_membership(self):
        sql = _compiled(CreateTable(MAILBOX_TABLES[0])).casefold()
        self.assertIn(
            "references cuevion_account.workspace_memberships "
            "(workspace_id, user_id)",
            sql,
        )
        self.assertIn("provider_account_identity = lower(provider_account_identity)", sql)
        self.assertIn("source_generation > 0", sql)

    def test_outbox_has_idempotent_message_version_event_key(self):
        sql = _compiled(CreateTable(mailbox_change_outbox)).casefold()
        self.assertIn(
            "unique (workspace_id, owner_user_id, mailbox_id, "
            "source_generation, message_id, message_row_version, event_type)",
            sql,
        )
        self.assertIn(
            "event_type in ('message_added','message_changed','message_deleted')",
            sql,
        )

    def test_wide_provider_locators_are_hashed_before_indexing(self):
        value = "Folder/" + ("é" * 4_000)
        digest = derive_locator_digest(value)
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, derive_locator_digest(value))
        cursor_sql = _compiled(CreateTable(MAILBOX_TABLES[1])).casefold()
        self.assertIn("scope_key_digest", cursor_sql)
        self.assertIn(
            "primary key (workspace_id, owner_user_id, mailbox_id, "
            "source_generation, scope_key_digest)",
            cursor_sql,
        )



class MailboxMigrationTests(unittest.TestCase):
    def test_revision_is_linear_forward_only_and_role_neutral(self):
        module = _revision_module()
        self.assertEqual(module.revision, "0002_mailbox_schema_1")
        self.assertEqual(module.down_revision, "0001_account_schema_1")
        self.assertIsNone(module.branch_labels)
        self.assertIsNone(module.depends_on)
        with self.assertRaisesRegex(
            RuntimeError,
            "^cuevion mailbox migrations are forward-only$",
        ):
            module.downgrade()

        source = _REVISION.read_text(encoding="utf-8")
        imported_roots = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_roots.add(node.module.split(".", 1)[0])
        self.assertEqual(imported_roots, {"alembic"})
        normalized = source.casefold()
        self.assertNotIn("grant ", normalized)
        self.assertNotIn("cuevion_auth_writer", normalized)
        self.assertIn("revoke all on schema cuevion_mailbox from public", normalized)

    def test_frozen_revision_ddl_matches_live_schema_definition(self):
        module = _revision_module()
        self.assertEqual(
            _canonical(module._SCHEMA_DDL),
            _compiled(CreateSchema(MAILBOX_SCHEMA)),
        )
        self.assertEqual(
            tuple(_canonical(statement) for statement in module._TABLE_DDL),
            tuple(_compiled(CreateTable(table)) for table in MAILBOX_TABLES),
        )

        live_indexes = tuple(
            _compiled(CreateIndex(index))
            for table in MAILBOX_TABLES
            for index in sorted(table.indexes, key=lambda item: item.name)
        )
        self.assertEqual(
            tuple(_canonical(statement) for statement in module._INDEX_DDL),
            live_indexes,
        )


class RepositoryContractTests(unittest.TestCase):
    def setUp(self):
        self.google_scope = MailboxScope(
            workspace_id="wsp_AAAAAAAAAAAAAAAAAAAAAA",
            owner_user_id="usr_BBBBBBBBBBBBBBBBBBBBBB",
            mailbox_id="main",
            source_generation=1,
            provider=MailboxProvider.GOOGLE,
            provider_account_identity="owner@example.com",
        )

    def test_google_and_imap_cursor_shapes_are_provider_specific(self):
        gmail = SyncCursor(
            scope_key="gmail-account",
            cursor_generation=1,
            provider=MailboxProvider.GOOGLE,
            gmail_history_id="123456789",
            imap_uid_validity=None,
            imap_highest_uid=None,
            imap_uidnext_observed=None,
            backfill_state=BackfillState.RUNNING,
            backfill_cursor="page-token",
            row_version=1,
        )
        self.assertEqual(gmail.gmail_history_id, "123456789")

        imap = SyncCursor(
            scope_key="INBOX",
            cursor_generation=2,
            provider=MailboxProvider.CUSTOM_IMAP,
            gmail_history_id=None,
            imap_uid_validity="98765",
            imap_highest_uid=0,
            imap_uidnext_observed=1,
            backfill_state=BackfillState.COMPLETE,
            backfill_cursor=None,
            row_version=4,
        )
        self.assertEqual(imap.imap_highest_uid, 0)

        with self.assertRaisesRegex(ValueError, "invalid sync cursor"):
            SyncCursor(
                scope_key="gmail-account",
                cursor_generation=1,
                provider=MailboxProvider.GOOGLE,
                gmail_history_id="123",
                imap_uid_validity="99",
                imap_highest_uid=1,
                imap_uidnext_observed=None,
                backfill_state=BackfillState.NOT_STARTED,
                backfill_cursor=None,
                row_version=1,
            )

    def test_tombstone_requires_deleted_projection_and_delete_outbox_event(self):
        message_id = derive_message_id(
            self.google_scope,
            provider_message_id="gmail-message-1",
            provider_folder="INBOX",
            imap_uid_validity=None,
            imap_uid=None,
        )
        identity = MessageIdentity(
            message_id=message_id,
            provider_message_id="gmail-message-1",
            provider_folder="INBOX",
            imap_uid_validity=None,
            imap_uid=None,
        )
        live_projection = MessageProjection(
            identity=identity,
            provider_thread_id="thread-1",
            metadata_hash="a" * 64,
            body_state=BodyState.NOT_CACHED,
            unread=False,
            starred=False,
            provider_deleted=False,
            row_version=1,
        )
        write = MessageWrite(
            projection=live_projection,
            provider_labels=("INBOX",),
            rfc_message_id="<message-1@example.com>",
            in_reply_to=None,
            references=(),
            sender=MailboxParty("sender@example.com", "Sender"),
            to=(MailboxParty("owner@example.com"),),
            cc=(),
            subject="Subject",
            snippet="Snippet",
            provider_timestamp_millis=1_700_000_000_000,
        )
        invalid_tombstone = MessageMutation(
            kind=MessageMutationKind.TOMBSTONE,
            projection=live_projection,
            write=None,
            outbox_event_type=OutboxEventType.MESSAGE_DELETED,
        )
        with self.assertRaisesRegex(ValueError, "invalid tombstone mutation"):
            invalid_tombstone.validate_for(
                MailboxProvider.GOOGLE,
                self.google_scope,
            )

        deleted_projection = MessageProjection(
            identity=identity,
            provider_thread_id="thread-1",
            metadata_hash="a" * 64,
            body_state=BodyState.NOT_CACHED,
            unread=False,
            starred=False,
            provider_deleted=True,
            row_version=2,
        )
        invalid_upsert = MessageMutation(
            kind=MessageMutationKind.UPSERT,
            projection=deleted_projection,
            write=None,
            outbox_event_type=OutboxEventType.MESSAGE_DELETED,
        )
        with self.assertRaisesRegex(ValueError, "invalid upsert mutation"):
            invalid_upsert.validate_for(
                MailboxProvider.GOOGLE,
                self.google_scope,
            )

        valid_upsert = MessageMutation(
            kind=MessageMutationKind.UPSERT,
            projection=live_projection,
            write=write,
            outbox_event_type=OutboxEventType.MESSAGE_ADDED,
        )
        valid_upsert.validate_for(
            MailboxProvider.GOOGLE,
            self.google_scope,
        )


    def test_delta_commit_binds_cursor_provider_scope_and_generation(self):
        message_id = derive_message_id(
            self.google_scope,
            provider_message_id="gmail-message-2",
            provider_folder="INBOX",
            imap_uid_validity=None,
            imap_uid=None,
        )
        identity = MessageIdentity(
            message_id=message_id,
            provider_message_id="gmail-message-2",
            provider_folder="INBOX",
            imap_uid_validity=None,
            imap_uid=None,
        )
        projection = MessageProjection(
            identity=identity,
            provider_thread_id="thread-2",
            metadata_hash="b" * 64,
            body_state=BodyState.NOT_CACHED,
            unread=True,
            starred=False,
            provider_deleted=False,
            row_version=2,
        )
        write = MessageWrite(
            projection=projection,
            provider_labels=("INBOX", "IMPORTANT"),
            rfc_message_id="<message-2@example.com>",
            in_reply_to=None,
            references=(),
            sender=MailboxParty("sender@example.com"),
            to=(MailboxParty("owner@example.com"),),
            cc=(),
            subject="Subject 2",
            snippet="Snippet 2",
            provider_timestamp_millis=1_700_000_100_000,
        )
        mutation = MessageMutation(
            kind=MessageMutationKind.UPSERT,
            projection=projection,
            write=write,
            outbox_event_type=OutboxEventType.MESSAGE_CHANGED,
        )
        cursor = SyncCursor(
            scope_key="gmail-account",
            cursor_generation=3,
            provider=MailboxProvider.GOOGLE,
            gmail_history_id="555",
            imap_uid_validity=None,
            imap_highest_uid=None,
            imap_uidnext_observed=None,
            backfill_state=BackfillState.RUNNING,
            backfill_cursor=None,
            row_version=2,
        )
        commit = ProviderDeltaCommit(
            scope=self.google_scope,
            scope_key="gmail-account",
            expected_state_row_version=4,
            expected_cursor_row_version=1,
            expected_cursor_generation=3,
            mutations=(mutation,),
            next_cursor=cursor,
            next_bootstrap_state=BootstrapState.RECENT_READY,
            committed_at_millis=1_700_000_200_000,
        )
        self.assertEqual(commit.next_cursor.gmail_history_id, "555")

        with self.assertRaisesRegex(ValueError, "invalid provider delta commit"):
            ProviderDeltaCommit(
                scope=self.google_scope,
                scope_key="different",
                expected_state_row_version=4,
                expected_cursor_row_version=1,
                expected_cursor_generation=3,
                mutations=(mutation,),
                next_cursor=cursor,
                next_bootstrap_state=BootstrapState.RECENT_READY,
                committed_at_millis=1_700_000_200_000,
            )



if __name__ == "__main__":
    unittest.main()
