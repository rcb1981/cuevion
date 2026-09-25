"""Tests for Preview-only Gmail History durable synchronization."""

from __future__ import annotations

import unittest

from cuevion_mailbox.gmail_projection import project_gmail_snapshot_message
from cuevion_mailbox.preview_active_write import (
    PreviewGmailHistoryRecovery,
    run_preview_gmail_history_sync,
)
from cuevion_mailbox.repository_contract import (
    BackfillState,
    BodyState,
    BootstrapState,
    DeltaCommitOutcome,
    MailboxProvider,
    MailboxScope,
    MailboxStateSnapshot,
    MessageProjection,
    SyncCursor,
)


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


def _state(*, row_version=7, bootstrap_state=BootstrapState.RECENT_READY):
    return MailboxStateSnapshot(
        scope=_scope(),
        bootstrap_state=bootstrap_state,
        row_version=row_version,
    )


def _cursor(history_id="1000", row_version=3):
    return SyncCursor(
        scope_key="gmail-account",
        cursor_generation=1,
        provider=MailboxProvider.GOOGLE,
        gmail_history_id=history_id,
        imap_uid_validity=None,
        imap_highest_uid=None,
        imap_uidnext_observed=None,
        backfill_state=BackfillState.NOT_STARTED,
        backfill_cursor=None,
        row_version=row_version,
    )


def _preview(message_id, *, labels=None):
    return {
        "providerMessageId": message_id,
        "providerThreadId": "thread-" + message_id,
        "providerFolder": "Inbox",
        "labelIds": labels or ["INBOX", "UNREAD"],
        "rfcMessageId": message_id + "@example.test",
        "to": "Owner <owner@example.test>",
        "cc": "",
    }


def _source(message_id, *, subject="Subject", unread=True):
    labels = ["INBOX", "UNREAD"] if unread else ["INBOX"]
    return {
        "provider": "google",
        "providerMessageId": message_id,
        "providerThreadId": "thread-" + message_id,
        "providerFolder": "INBOX",
        "labels": labels,
        "providerTimestampMillis": "1790160000000",
        "senderDisplay": "Sender",
        "senderAddress": "sender@example.test",
        "subject": subject,
        "snippet": "Snippet",
        "unread": unread,
        "flagged": False,
    }


def _record(message_id, *, subject="Subject"):
    return project_gmail_snapshot_message(
        _scope(),
        _preview(message_id),
        _source(message_id, subject=subject),
    )


def _projection(
    record,
    *,
    row_version,
    body_state=BodyState.NOT_CACHED,
    provider_deleted=False,
):
    return MessageProjection(
        identity=record.identity,
        provider_thread_id=record.provider_thread_id,
        metadata_hash=record.metadata_hash,
        body_state=body_state,
        unread=record.unread,
        starred=record.starred,
        provider_deleted=provider_deleted,
        row_version=row_version,
    )


class _Reader:
    def __init__(self, *, state=None, cursor=None, projections=()):
        self.state = state
        self.cursor = cursor
        self.projections = tuple(projections)
        self.state_calls = []
        self.cursor_calls = []
        self.exact_calls = []

    def resolve_current_state(self, authority):
        self.state_calls.append(authority)
        return self.state

    def read_cursor(self, scope, scope_key):
        self.cursor_calls.append((scope, scope_key))
        return self.cursor

    def read_messages_by_provider_message_ids(self, scope, provider_message_ids):
        self.exact_calls.append((scope, tuple(provider_message_ids)))
        return self.projections

    def list_messages(self, scope, *, limit, before_timestamp_millis=None):
        raise AssertionError("History sync must not use recency-based list_messages")


class _Writer:
    def __init__(self, outcome=DeltaCommitOutcome.APPLIED):
        self.outcome = outcome
        self.commits = []

    def initialize_current_state(self, authority, *, initialized_at_millis):
        raise AssertionError("History sync must not initialize durable state")

    def commit_provider_delta(self, commit):
        self.commits.append(commit)
        return self.outcome


class _Repositories:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer


def _run(
    repositories,
    request,
    recover,
    *,
    environment=None,
):
    return run_preview_gmail_history_sync(
        environment=_environment() if environment is None else environment,
        workspace_id="wsp_" + ("a" * 22),
        owner_user_id="usr_" + ("b" * 22),
        mailbox_id="gmail-1",
        mailbox_account_identity="Verified@Gmail.com",
        context={
            "mailbox_email": "Verified@Gmail.com",
            "mailbox_id": "gmail-1",
            "refresh_attempted": False,
        },
        request_with_one_refresh=request,
        recover_exact_message=recover,
        committed_at_millis=1_790_160_000_000,
        repositories=repositories,
    )


class PreviewGmailHistorySyncTests(unittest.TestCase):
    def test_missing_state_or_cursor_requires_snapshot_bootstrap_without_provider_history(self):
        for reader in (
            _Reader(state=None),
            _Reader(state=_state(), cursor=None),
        ):
            with self.subTest(state=reader.state, cursor=reader.cursor):
                calls = []
                writer = _Writer()
                result = _run(
                    _Repositories(reader, writer),
                    lambda *_args: calls.append("history"),
                    lambda *_args: (_ for _ in ()).throw(
                        AssertionError("recovery called")
                    ),
                )
                self.assertEqual(result.status, "bootstrap_required")
                self.assertEqual(result.mutation_count, 0)
                self.assertEqual(calls, [])
                self.assertEqual(reader.exact_calls, [])
                self.assertEqual(writer.commits, [])

    def test_existing_cursor_in_nonready_state_never_calls_provider_or_writer(self):
        reader = _Reader(
            state=_state(bootstrap_state=BootstrapState.RECOVERING),
            cursor=_cursor(),
        )
        writer = _Writer()
        provider_calls = []

        result = _run(
            _Repositories(reader, writer),
            lambda *_args: provider_calls.append("history"),
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("recovery called")
            ),
        )

        self.assertEqual(result.status, "state_not_ready")
        self.assertEqual(provider_calls, [])
        self.assertEqual(reader.exact_calls, [])
        self.assertEqual(writer.commits, [])

    def test_production_bootstrap_forces_complete_reconciliation_before_history_delta(self):
        reader = _Reader(state=_state(), cursor=_cursor())
        writer = _Writer()
        provider_calls = []
        environment = {
            "VERCEL_ENV": "production",
            "CUEVION_MAILBOX_POSTGRES_MODE": "production_bootstrap",
            "CUEVION_MAILBOX_PRODUCTION_BOOTSTRAP_AUTHORITY": "enabled",
        }
        result = _run(
            _Repositories(reader, writer),
            lambda *_args: provider_calls.append("history"),
            lambda *_args: (_ for _ in ()).throw(AssertionError("recovery called")),
            environment=environment,
        )
        self.assertEqual(result.status, "full_sync_required")
        self.assertEqual(provider_calls, [])
        self.assertEqual(reader.exact_calls, [])
        self.assertEqual(writer.commits, [])

    def test_empty_history_advance_commits_cursor_only(self):
        reader = _Reader(state=_state(), cursor=_cursor())
        writer = _Writer()

        result = _run(
            _Repositories(reader, writer),
            lambda context, path: (
                {"historyId": "1005"},
                None,
                {**context, "history_seen": True},
                None,
            ),
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("recovery called")
            ),
        )

        self.assertEqual(result.status, "applied")
        self.assertEqual(result.mutation_count, 0)
        self.assertEqual(result.next_history_id, "1005")
        self.assertEqual(result.affected_count, 0)
        self.assertTrue(result.context["history_seen"])
        self.assertEqual(reader.exact_calls[0][1], ())
        self.assertEqual(len(writer.commits), 1)
        commit = writer.commits[0]
        self.assertEqual(commit.expected_cursor_row_version, 3)
        self.assertEqual(commit.next_cursor.row_version, 4)
        self.assertEqual(commit.next_cursor.gmail_history_id, "1005")
        self.assertIs(commit.next_bootstrap_state, BootstrapState.RECENT_READY)
        self.assertEqual(commit.mutations, ())

    def test_recovered_change_and_verified_absence_commit_atomically(self):
        changed_id = "gmail-message-changed"
        absent_id = "gmail-message-absent"
        old_changed = _record(changed_id, subject="Old subject")
        changed = _record(changed_id, subject="New subject")
        absent = _record(absent_id)
        reader = _Reader(
            state=_state(row_version=11),
            cursor=_cursor(history_id="2000", row_version=5),
            projections=(
                _projection(
                    old_changed,
                    row_version=4,
                    body_state=BodyState.CACHED,
                ),
                _projection(absent, row_version=2),
            ),
        )
        writer = _Writer()
        recover_calls = []

        def request(context, path):
            self.assertIn("startHistoryId=2000", path)
            return (
                {
                    "historyId": "2010",
                    "history": [
                        {
                            "id": "2008",
                            "messages": [
                                {"id": changed_id},
                                {"id": absent_id},
                            ],
                        }
                    ],
                },
                None,
                {**context, "history_context": True},
                None,
            )

        def recover(context, provider_message_id):
            recover_calls.append((dict(context), provider_message_id))
            if provider_message_id == changed_id:
                return PreviewGmailHistoryRecovery(
                    "recovered",
                    {**context, "recovered_changed": True},
                    _preview(changed_id),
                    _source(changed_id, subject="New subject"),
                )
            return PreviewGmailHistoryRecovery(
                "terminal_absent",
                {**context, "recovered_absent": True},
            )

        result = _run(
            _Repositories(reader, writer),
            request,
            recover,
        )

        self.assertEqual(result.status, "applied")
        self.assertEqual(result.mutation_count, 2)
        self.assertEqual(result.next_history_id, "2010")
        self.assertEqual(result.affected_count, 2)
        self.assertTrue(result.context["recovered_absent"])
        self.assertEqual(
            [provider_id for _context, provider_id in recover_calls],
            [changed_id, absent_id],
        )
        self.assertEqual(
            reader.exact_calls[0][1],
            (changed_id, absent_id),
        )
        self.assertEqual(len(writer.commits), 1)
        commit = writer.commits[0]
        self.assertEqual(commit.expected_state_row_version, 11)
        self.assertEqual(commit.expected_cursor_row_version, 5)
        self.assertEqual(len(commit.mutations), 2)
        self.assertEqual(commit.mutations[0].kind.value, "upsert")
        self.assertEqual(
            commit.mutations[0].record.body_state,
            BodyState.CACHED,
        )
        self.assertEqual(commit.mutations[1].kind.value, "tombstone")

    def test_retryable_exact_recovery_never_reads_or_commits_durable_mutations(self):
        reader = _Reader(state=_state(), cursor=_cursor())
        writer = _Writer()

        result = _run(
            _Repositories(reader, writer),
            lambda context, _path: (
                {
                    "historyId": "1005",
                    "history": [
                        {
                            "id": "1004",
                            "messages": [{"id": "gmail-message-1"}],
                        }
                    ],
                },
                None,
                context,
                None,
            ),
            lambda context, _provider_message_id: PreviewGmailHistoryRecovery(
                "retry",
                {**context, "retry_seen": True},
            ),
        )

        self.assertEqual(result.status, "recovery_unavailable")
        self.assertIsNone(result.next_history_id)
        self.assertTrue(result.context["retry_seen"])
        self.assertEqual(reader.exact_calls, [])
        self.assertEqual(writer.commits, [])

    def test_stale_history_requires_full_sync_without_recovery_or_commit(self):
        reader = _Reader(state=_state(), cursor=_cursor())
        writer = _Writer()

        result = _run(
            _Repositories(reader, writer),
            lambda context, _path: (
                None,
                {"code": "gmail_message_not_found"},
                context,
                None,
            ),
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("recovery called")
            ),
        )

        self.assertEqual(result.status, "full_sync_required")
        self.assertIsNone(result.next_history_id)
        self.assertEqual(reader.exact_calls, [])
        self.assertEqual(writer.commits, [])

    def test_exact_same_history_without_changes_is_noop(self):
        reader = _Reader(state=_state(), cursor=_cursor(history_id="1000"))
        writer = _Writer()

        result = _run(
            _Repositories(reader, writer),
            lambda context, _path: (
                {"historyId": "1000"},
                None,
                context,
                None,
            ),
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("recovery called")
            ),
        )

        self.assertEqual(result.status, "unchanged")
        self.assertEqual(result.mutation_count, 0)
        self.assertEqual(result.next_history_id, "1000")
        self.assertEqual(writer.commits, [])

    def test_writer_conflict_is_observational_result(self):
        reader = _Reader(state=_state(), cursor=_cursor())
        writer = _Writer(DeltaCommitOutcome.CONFLICT)

        result = _run(
            _Repositories(reader, writer),
            lambda context, _path: (
                {"historyId": "1005"},
                None,
                context,
                None,
            ),
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("recovery called")
            ),
        )

        self.assertEqual(result.status, "conflict")
        self.assertEqual(len(writer.commits), 1)


if __name__ == "__main__":
    unittest.main()
