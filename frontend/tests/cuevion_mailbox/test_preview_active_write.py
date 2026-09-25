"""Tests for the Preview-only Gmail durable write hook."""

from __future__ import annotations

from pathlib import Path
import unittest

from cuevion_mailbox.preview_active_write import (
    gmail_durable_write_enabled,
    preview_active_write_enabled,
    run_preview_gmail_durable_write,
)
from cuevion_mailbox.repository_contract import (
    BackfillState,
    BootstrapState,
    CurrentStateInitializationOutcome,
    CurrentStateInitializationResult,
    DeltaCommitOutcome,
    MailboxProvider,
    MailboxScope,
    MailboxStateSnapshot,
    SyncCursor,
)


_FRONTEND = Path(__file__).resolve().parents[2]
_GMAIL_ROUTE = _FRONTEND / "api" / "inboxes" / "fetch-gmail.py"
_IMAP_ROUTE = _FRONTEND / "api" / "inboxes" / "connect-imap.py"


def _environment():
    return {
        "VERCEL_ENV": "preview",
        "CUEVION_MAILBOX_POSTGRES_MODE": "active_write",
    }


def _scope():
    return MailboxScope(
        workspace_id="wsp_" + ("a" * 22),
        owner_user_id="usr_" + ("b" * 22),
        mailbox_id="gmail-1",
        source_generation=1,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity="verified@gmail.com",
    )


def _preview():
    return {
        "providerMessageId": "gmail-message-1",
        "providerThreadId": "gmail-thread-1",
        "providerFolder": "Inbox",
        "labelIds": ["INBOX", "UNREAD"],
        "rfcMessageId": "rfc-1@example.test",
        "to": "Owner <owner@example.test>",
        "cc": "",
    }


def _source():
    return {
        "provider": "google",
        "providerMessageId": "gmail-message-1",
        "providerThreadId": "gmail-thread-1",
        "providerFolder": "INBOX",
        "labels": ["UNREAD", "INBOX"],
        "providerTimestampMillis": "1790115000000",
        "senderDisplay": "Sender",
        "senderAddress": "sender@example.test",
        "subject": "Subject",
        "snippet": "Snippet",
        "unread": True,
        "flagged": False,
    }


class _Reader:
    def __init__(self, *, cursor=None, projections=()):
        self.cursor = cursor
        self.projections = tuple(projections)
        self.cursor_calls = []
        self.list_calls = []

    def read_cursor(self, scope, scope_key):
        self.cursor_calls.append((scope, scope_key))
        return self.cursor

    def list_messages(self, scope, *, limit, before_timestamp_millis=None):
        self.list_calls.append((scope, limit, before_timestamp_millis))
        return self.projections


class _Writer:
    def __init__(
        self,
        *,
        initialization,
        outcome=DeltaCommitOutcome.APPLIED,
    ):
        self.initialization = initialization
        self.outcome = outcome
        self.initializations = []
        self.commits = []

    def initialize_current_state(self, authority, *, initialized_at_millis):
        self.initializations.append((authority, initialized_at_millis))
        return self.initialization

    def commit_provider_delta(self, commit):
        self.commits.append(commit)
        return self.outcome


class _Repositories:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer


def _initialization(
    outcome=CurrentStateInitializationOutcome.CREATED,
    *,
    row_version=1,
):
    return CurrentStateInitializationResult(
        outcome,
        MailboxStateSnapshot(
            scope=_scope(),
            bootstrap_state=BootstrapState.NOT_STARTED,
            row_version=row_version,
        ),
    )


class PreviewGmailDurableWriteTests(unittest.TestCase):
    def _run(self, repositories, *, environment=None, history_id="1001"):
        return run_preview_gmail_durable_write(
            environment=_environment() if environment is None else environment,
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            mailbox_account_identity="Verified@Gmail.com",
            previews=[_preview()],
            candidate_sources=[_source()],
            gmail_history_id=history_id,
            committed_at_millis=1_790_115_000_000,
            repositories=repositories,
        )

    def test_activation_requires_exact_preview_active_write(self):
        self.assertTrue(preview_active_write_enabled(_environment()))
        for environment in (
            {},
            {
                "VERCEL_ENV": "preview",
                "CUEVION_MAILBOX_POSTGRES_MODE": "active_read",
            },
            {
                "VERCEL_ENV": "production",
                "CUEVION_MAILBOX_POSTGRES_MODE": "active_write",
            },
        ):
            with self.subTest(environment=environment):
                self.assertFalse(preview_active_write_enabled(environment))
                with self.assertRaises(RuntimeError):
                    self._run(
                        _Repositories(_Reader(), _Writer(initialization=_initialization())),
                        environment=environment,
                    )

    def test_production_bootstrap_gate_reuses_provider_authoritative_write_logic(self):
        environment = {
            "VERCEL_ENV": "production",
            "CUEVION_MAILBOX_POSTGRES_MODE": "production_bootstrap",
            "CUEVION_MAILBOX_PRODUCTION_BOOTSTRAP_AUTHORITY": "enabled",
        }
        self.assertTrue(gmail_durable_write_enabled(environment))
        self.assertFalse(preview_active_write_enabled(environment))
        reader = _Reader()
        writer = _Writer(initialization=_initialization())
        result = self._run(_Repositories(reader, writer), environment=environment)
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.mutation_count, 1)
        self.assertEqual(len(writer.commits), 1)

    def test_production_bootstrap_refuses_read_authority_coactivation(self):
        environment = {
            "VERCEL_ENV": "production",
            "CUEVION_MAILBOX_POSTGRES_MODE": "production_bootstrap",
            "CUEVION_MAILBOX_PRODUCTION_BOOTSTRAP_AUTHORITY": "enabled",
            "CUEVION_MAILBOX_PRODUCTION_READ_AUTHORITY": "enabled",
        }
        self.assertFalse(gmail_durable_write_enabled(environment))
        with self.assertRaises(RuntimeError):
            self._run(_Repositories(_Reader(), _Writer(initialization=_initialization())), environment=environment)

    def test_route_keeps_priority_outbox_preview_only_during_production_bootstrap(self):
        gmail = _GMAIL_ROUTE.read_text(encoding="utf-8")
        outbox_call = gmail.index("outbox_report = run_preview_priority_mailbox_outbox_consumer")
        preview_gate = gmail.rfind("if preview_active_write_enabled(os.environ):", 0, outbox_call)
        self.assertGreaterEqual(preview_gate, 0)

    def test_first_write_bootstraps_state_and_commits_one_added_message(self):
        reader = _Reader()
        writer = _Writer(initialization=_initialization())
        result = self._run(_Repositories(reader, writer))
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.mutation_count, 1)
        self.assertEqual(result.source_generation, 1)
        self.assertEqual(len(writer.initializations), 1)
        self.assertEqual(
            writer.initializations[0][0].provider_account_identity,
            "verified@gmail.com",
        )
        self.assertEqual(len(writer.commits), 1)
        commit = writer.commits[0]
        self.assertEqual(commit.expected_state_row_version, 1)
        self.assertIsNone(commit.expected_cursor_row_version)
        self.assertEqual(commit.next_cursor.gmail_history_id, "1001")
        self.assertEqual(commit.next_cursor.row_version, 1)
        self.assertEqual(len(commit.mutations), 1)

    def test_exact_repeat_with_same_history_and_no_metadata_change_is_noop(self):
        first_reader = _Reader()
        first_writer = _Writer(initialization=_initialization())
        self._run(_Repositories(first_reader, first_writer))
        first_commit = first_writer.commits[0]
        inserted = first_commit.mutations[0].record
        from cuevion_mailbox.repository_contract import MessageProjection

        projection = MessageProjection(
            identity=inserted.identity,
            provider_thread_id=inserted.provider_thread_id,
            metadata_hash=inserted.metadata_hash,
            body_state=inserted.body_state,
            unread=inserted.unread,
            starred=inserted.starred,
            provider_deleted=False,
            row_version=1,
        )
        cursor = SyncCursor(
            scope_key="gmail-account",
            cursor_generation=1,
            provider=MailboxProvider.GOOGLE,
            gmail_history_id="1001",
            imap_uid_validity=None,
            imap_highest_uid=None,
            imap_uidnext_observed=None,
            backfill_state=BackfillState.NOT_STARTED,
            backfill_cursor=None,
            row_version=1,
        )
        reader = _Reader(cursor=cursor, projections=(projection,))
        writer = _Writer(
            initialization=_initialization(
                CurrentStateInitializationOutcome.EXISTING,
                row_version=2,
            )
        )
        result = self._run(_Repositories(reader, writer))
        self.assertEqual(result.status, "unchanged")
        self.assertEqual(result.mutation_count, 0)
        self.assertEqual(writer.commits, [])

    def test_history_advance_without_message_change_commits_cursor_only(self):
        from cuevion_mailbox.gmail_projection import project_gmail_snapshot_message
        from cuevion_mailbox.repository_contract import MessageProjection

        record = project_gmail_snapshot_message(_scope(), _preview(), _source())
        projection = MessageProjection(
            identity=record.identity,
            provider_thread_id=record.provider_thread_id,
            metadata_hash=record.metadata_hash,
            body_state=record.body_state,
            unread=record.unread,
            starred=record.starred,
            provider_deleted=False,
            row_version=1,
        )
        cursor = SyncCursor(
            scope_key="gmail-account",
            cursor_generation=1,
            provider=MailboxProvider.GOOGLE,
            gmail_history_id="1001",
            imap_uid_validity=None,
            imap_highest_uid=None,
            imap_uidnext_observed=None,
            backfill_state=BackfillState.NOT_STARTED,
            backfill_cursor=None,
            row_version=1,
        )
        reader = _Reader(cursor=cursor, projections=(projection,))
        writer = _Writer(
            initialization=_initialization(
                CurrentStateInitializationOutcome.EXISTING,
                row_version=2,
            )
        )
        result = self._run(
            _Repositories(reader, writer),
            history_id="1002",
        )
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.mutation_count, 0)
        self.assertEqual(len(writer.commits), 1)
        self.assertEqual(writer.commits[0].next_cursor.row_version, 2)
        self.assertEqual(writer.commits[0].next_cursor.gmail_history_id, "1002")

    def test_state_conflict_never_reads_or_commits_messages(self):
        initialization = CurrentStateInitializationResult(
            CurrentStateInitializationOutcome.CONFLICT,
            None,
        )
        reader = _Reader()
        writer = _Writer(initialization=initialization)
        result = self._run(_Repositories(reader, writer))
        self.assertEqual(result.status, "state_conflict")
        self.assertEqual(reader.cursor_calls, [])
        self.assertEqual(reader.list_calls, [])
        self.assertEqual(writer.commits, [])

    def test_history_rewind_fails_before_commit(self):
        cursor = SyncCursor(
            scope_key="gmail-account",
            cursor_generation=1,
            provider=MailboxProvider.GOOGLE,
            gmail_history_id="1002",
            imap_uid_validity=None,
            imap_highest_uid=None,
            imap_uidnext_observed=None,
            backfill_state=BackfillState.NOT_STARTED,
            backfill_cursor=None,
            row_version=1,
        )
        reader = _Reader(cursor=cursor)
        writer = _Writer(
            initialization=_initialization(
                CurrentStateInitializationOutcome.EXISTING,
                row_version=2,
            )
        )
        with self.assertRaises(ValueError):
            self._run(
                _Repositories(reader, writer),
                history_id="1001",
            )
        self.assertEqual(writer.commits, [])


class PreviewGmailDurableWriteStaticTests(unittest.TestCase):
    def test_route_runs_history_before_snapshot_and_bootstraps_only_without_cursor(self):
        gmail = _GMAIL_ROUTE.read_text(encoding="utf-8")
        history_sync_index = gmail.index(
            "history_sync = run_preview_gmail_history_sync"
        )
        bootstrap_gate_index = gmail.index(
            'history_sync.status == "bootstrap_required"'
        )
        profile_index = gmail.index(
            "durable_history = read_gmail_account_history"
        )
        snapshot_index = gmail.index("snapshot_result = read_gmail_folder_snapshot")
        write_index = gmail.index("durable_write = run_preview_gmail_durable_write")
        self.assertLess(history_sync_index, bootstrap_gate_index)
        self.assertLess(bootstrap_gate_index, profile_index)
        self.assertLess(profile_index, snapshot_index)
        self.assertLess(snapshot_index, write_index)

    def test_stale_history_captures_fresh_profile_before_recovery_and_snapshot(self):
        gmail = _GMAIL_ROUTE.read_text(encoding="utf-8")
        stale_gate_index = gmail.index(
            'history_sync.status == "full_sync_required"'
        )
        stale_profile_index = gmail.index(
            "stale_recovery_history = read_gmail_account_history"
        )
        stale_recovery_index = gmail.index(
            "stale_recovery = run_preview_gmail_stale_recovery"
        )
        snapshot_index = gmail.index("snapshot_result = read_gmail_folder_snapshot")
        self.assertLess(stale_gate_index, stale_profile_index)
        self.assertLess(stale_profile_index, stale_recovery_index)
        self.assertLess(stale_recovery_index, snapshot_index)

    def test_route_hook_is_gmail_only(self):
        gmail = _GMAIL_ROUTE.read_text(encoding="utf-8")
        imap = _IMAP_ROUTE.read_text(encoding="utf-8")
        self.assertIn("gmail_durable_write_enabled", gmail)
        self.assertIn("run_preview_gmail_history_sync", gmail)
        self.assertIn("run_preview_gmail_stale_recovery", gmail)
        self.assertIn("run_preview_gmail_durable_write", gmail)
        self.assertNotIn("run_preview_gmail_history_sync", imap)
        self.assertNotIn("run_preview_gmail_stale_recovery", imap)
        self.assertNotIn("run_preview_gmail_durable_write", imap)
        self.assertNotIn("gmail_durable_write_enabled", imap)


if __name__ == "__main__":
    unittest.main()
