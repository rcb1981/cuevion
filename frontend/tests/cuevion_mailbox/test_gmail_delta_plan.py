"""Tests for pure Gmail durable delta planning."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from cuevion_mailbox.gmail_delta_plan import (
    build_gmail_delta_commit,
    derive_gmail_event_id,
    plan_gmail_message_mutations,
)
from cuevion_mailbox.gmail_projection import project_gmail_snapshot_message
from cuevion_mailbox.repository_contract import (
    BackfillState,
    BodyState,
    BootstrapState,
    MailboxProvider,
    MailboxScope,
    MessageMutationKind,
    MessageProjection,
    OutboxEventType,
    SyncCursor,
)


_FRONTEND = Path(__file__).resolve().parents[2]
_MODULE = _FRONTEND / "cuevion_mailbox" / "gmail_delta_plan.py"


def _scope() -> MailboxScope:
    return MailboxScope(
        workspace_id="wsp_" + ("a" * 22),
        owner_user_id="usr_" + ("b" * 22),
        mailbox_id="gmail-1",
        source_generation=1,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity="verified@gmail.com",
    )


def _preview(message_id: str = "gmail-message-1", **overrides):
    return {
        "providerMessageId": message_id,
        "providerThreadId": f"thread-{message_id}",
        "providerFolder": "Inbox",
        "labelIds": ["INBOX", "UNREAD"],
        "rfcMessageId": f"{message_id}@example.test",
        "to": "Owner <owner@example.test>",
        "cc": "",
        **overrides,
    }


def _source(message_id: str = "gmail-message-1", **overrides):
    return {
        "provider": "google",
        "providerMessageId": message_id,
        "providerThreadId": f"thread-{message_id}",
        "providerFolder": "INBOX",
        "labels": ["UNREAD", "INBOX"],
        "providerTimestampMillis": "1790100000000",
        "senderDisplay": "Sender",
        "senderAddress": "sender@example.test",
        "subject": "Subject",
        "snippet": "Snippet",
        "unread": True,
        "flagged": False,
        **overrides,
    }


def _record(message_id: str = "gmail-message-1", **source_overrides):
    return project_gmail_snapshot_message(
        _scope(),
        _preview(message_id),
        _source(message_id, **source_overrides),
    )


def _projection(
    record,
    *,
    row_version: int = 1,
    body_state: BodyState | None = None,
    provider_deleted: bool = False,
):
    return MessageProjection(
        identity=record.identity,
        provider_thread_id=record.provider_thread_id,
        metadata_hash=record.metadata_hash,
        body_state=body_state or record.body_state,
        unread=record.unread,
        starred=record.starred,
        provider_deleted=provider_deleted,
        row_version=row_version,
    )


def _cursor(
    *,
    history_id: str,
    row_version: int,
    generation: int = 1,
) -> SyncCursor:
    return SyncCursor(
        scope_key="gmail-account",
        cursor_generation=generation,
        provider=MailboxProvider.GOOGLE,
        gmail_history_id=history_id,
        imap_uid_validity=None,
        imap_highest_uid=None,
        imap_uidnext_observed=None,
        backfill_state=BackfillState.NOT_STARTED,
        backfill_cursor=None,
        row_version=row_version,
    )


class GmailDurableDeltaPlanTests(unittest.TestCase):
    def test_new_record_becomes_added_upsert(self):
        record = _record()
        mutations = plan_gmail_message_mutations(_scope(), [record], [])
        self.assertEqual(len(mutations), 1)
        mutation = mutations[0]
        self.assertIs(mutation.kind, MessageMutationKind.UPSERT)
        self.assertIs(mutation.outbox_event_type, OutboxEventType.MESSAGE_ADDED)
        self.assertIsNone(mutation.expected_row_version)
        self.assertEqual(mutation.record, record)
        self.assertRegex(mutation.event_id, r"^mbe_[A-Za-z0-9_-]{22}$")

    def test_exact_existing_projection_is_noop(self):
        record = _record()
        self.assertEqual(
            plan_gmail_message_mutations(
                _scope(),
                [record],
                [_projection(record, row_version=7)],
            ),
            (),
        )

    def test_metadata_change_becomes_changed_upsert_with_cas(self):
        old = _record()
        changed = _record(subject="Changed subject")
        mutations = plan_gmail_message_mutations(
            _scope(),
            [changed],
            [_projection(old, row_version=7)],
        )
        self.assertEqual(len(mutations), 1)
        mutation = mutations[0]
        self.assertIs(mutation.outbox_event_type, OutboxEventType.MESSAGE_CHANGED)
        self.assertEqual(mutation.expected_row_version, 7)
        self.assertEqual(mutation.record.subject, "Changed subject")

    def test_existing_cached_body_state_survives_metadata_refresh(self):
        old = _record()
        changed = _record(subject="Changed subject")
        mutation = plan_gmail_message_mutations(
            _scope(),
            [changed],
            [
                _projection(
                    old,
                    row_version=7,
                    body_state=BodyState.CACHED,
                )
            ],
        )[0]
        self.assertIs(mutation.record.body_state, BodyState.CACHED)
        self.assertEqual(mutation.expected_row_version, 7)

    def test_cached_body_state_alone_does_not_force_metadata_mutation(self):
        record = _record()
        mutations = plan_gmail_message_mutations(
            _scope(),
            [record],
            [
                _projection(
                    record,
                    row_version=4,
                    body_state=BodyState.CACHED,
                )
            ],
        )
        self.assertEqual(mutations, ())

    def test_bounded_snapshot_never_tombstones_unseen_current_rows(self):
        visible = _record("gmail-message-visible")
        unseen = _record("gmail-message-unseen")
        mutations = plan_gmail_message_mutations(
            _scope(),
            [visible],
            [
                _projection(visible),
                _projection(unseen),
            ],
        )
        self.assertEqual(mutations, ())

    def test_event_id_is_stable_and_resulting_row_version_scoped(self):
        scope = _scope()
        first = derive_gmail_event_id(
            scope,
            _record().identity.message_id,
            2,
            OutboxEventType.MESSAGE_CHANGED,
        )
        again = derive_gmail_event_id(
            scope,
            _record().identity.message_id,
            2,
            OutboxEventType.MESSAGE_CHANGED,
        )
        next_version = derive_gmail_event_id(
            scope,
            _record().identity.message_id,
            3,
            OutboxEventType.MESSAGE_CHANGED,
        )
        self.assertEqual(first, again)
        self.assertNotEqual(first, next_version)
        self.assertEqual(len(first), 26)

    def test_caller_cannot_claim_cached_body_via_metadata_projection(self):
        record = _record()
        from dataclasses import replace

        with self.assertRaises(ValueError):
            plan_gmail_message_mutations(
                _scope(),
                [replace(record, body_state=BodyState.CACHED)],
                [],
            )

    def test_duplicate_records_or_current_projections_fail_closed(self):
        record = _record()
        with self.assertRaises(ValueError):
            plan_gmail_message_mutations(_scope(), [record, record], [])
        with self.assertRaises(ValueError):
            plan_gmail_message_mutations(
                _scope(),
                [record],
                [_projection(record), _projection(record)],
            )

    def test_commit_derives_cursor_cas_and_allows_empty_mutation_delta(self):
        record = _record()
        current = _projection(record, row_version=3)
        previous_cursor = _cursor(history_id="100", row_version=4)
        next_cursor = _cursor(history_id="125", row_version=5)
        commit = build_gmail_delta_commit(
            _scope(),
            [record],
            [current],
            expected_state_row_version=9,
            current_cursor=previous_cursor,
            next_cursor=next_cursor,
            committed_at_millis=1790100000000,
            next_bootstrap_state=BootstrapState.RECENT_READY,
        )
        self.assertEqual(commit.mutations, ())
        self.assertEqual(commit.scope_key, "gmail-account")
        self.assertEqual(commit.expected_state_row_version, 9)
        self.assertEqual(commit.expected_cursor_row_version, 4)
        self.assertEqual(commit.expected_cursor_generation, 1)
        self.assertEqual(commit.next_cursor, next_cursor)

    def test_first_cursor_insert_uses_none_expected_version(self):
        commit = build_gmail_delta_commit(
            _scope(),
            [_record()],
            [],
            expected_state_row_version=1,
            current_cursor=None,
            next_cursor=_cursor(history_id="100", row_version=1),
            committed_at_millis=1790100000000,
            next_bootstrap_state=BootstrapState.RECENT_READY,
        )
        self.assertIsNone(commit.expected_cursor_row_version)
        self.assertEqual(commit.expected_cursor_generation, 1)
        self.assertEqual(len(commit.mutations), 1)

    def test_cursor_rewind_generation_change_or_bad_row_transition_fails_closed(self):
        previous = _cursor(history_id="100", row_version=4)
        invalid_next = (
            _cursor(history_id="99", row_version=5),
            _cursor(history_id="125", row_version=5, generation=2),
            _cursor(history_id="125", row_version=6),
        )
        for next_cursor in invalid_next:
            with self.subTest(next_cursor=next_cursor):
                with self.assertRaises(ValueError):
                    build_gmail_delta_commit(
                        _scope(),
                        [],
                        [],
                        expected_state_row_version=1,
                        current_cursor=previous,
                        next_cursor=next_cursor,
                        committed_at_millis=1790100000000,
                        next_bootstrap_state=BootstrapState.RECENT_READY,
                    )


class GmailDurableDeltaPlanStaticTests(unittest.TestCase):
    def test_planner_has_no_runtime_io_or_writer_calls(self):
        source = _MODULE.read_text(encoding="utf-8")
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
        for forbidden in (
            "os",
            "random",
            "secrets",
            "uuid",
            "time",
            "socket",
            "urllib",
            "requests",
            "httpx",
            "psycopg",
        ):
            self.assertNotIn(forbidden, imported)
        for forbidden in (
            "commit_provider_delta(",
            "build_shadow_mailbox_repositories",
            "build_active_read_mailbox_reader",
            "DATABASE_URL",
            "os.environ",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
