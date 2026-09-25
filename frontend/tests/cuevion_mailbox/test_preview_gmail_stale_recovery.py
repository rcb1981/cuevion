"""Tests for Preview-only stale Gmail History recovery."""

from __future__ import annotations

import unittest

from cuevion_mailbox.gmail_projection import project_gmail_snapshot_message
from cuevion_mailbox.preview_active_write import (
    PreviewGmailHistoryRecovery,
    run_production_gmail_bootstrap_backfill,
    run_preview_gmail_stale_recovery,
)
from cuevion_mailbox.repository_contract import (
    BackfillState,
    BodyState,
    BootstrapState,
    BoundedMessageProjectionInventory,
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


def _state(*, row_version=11, bootstrap_state=BootstrapState.RECENT_READY):
    return MailboxStateSnapshot(
        scope=_scope(),
        bootstrap_state=bootstrap_state,
        row_version=row_version,
    )


def _cursor(history_id="100", row_version=3):
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


def _preview(message_id, *, subject="Subject"):
    return {
        "providerMessageId": message_id,
        "providerThreadId": "thread-" + message_id,
        "providerFolder": "Inbox",
        "labelIds": ["INBOX", "UNREAD"],
        "rfcMessageId": message_id + "@example.test",
        "to": "Owner <owner@example.test>",
        "cc": "",
        "subject": subject,
    }


def _source(message_id, *, subject="Subject"):
    return {
        "provider": "google",
        "providerMessageId": message_id,
        "providerThreadId": "thread-" + message_id,
        "providerFolder": "INBOX",
        "labels": ["INBOX", "UNREAD"],
        "providerTimestampMillis": "1790179000000",
        "senderDisplay": "Sender",
        "senderAddress": "sender@example.test",
        "subject": subject,
        "snippet": "Snippet",
        "unread": True,
        "flagged": False,
    }


def _record(message_id, *, subject="Subject"):
    return project_gmail_snapshot_message(
        _scope(),
        _preview(message_id, subject=subject),
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
    def __init__(
        self,
        *,
        state=None,
        cursor=None,
        active_inventory=None,
        exact_projections=(),
    ):
        self.state = state
        self.cursor = cursor
        self.active_inventory = (
            BoundedMessageProjectionInventory((), False)
            if active_inventory is None
            else active_inventory
        )
        self.exact_projections = tuple(exact_projections)
        self.state_calls = []
        self.cursor_calls = []
        self.active_calls = []
        self.exact_calls = []

    def resolve_current_state(self, authority):
        self.state_calls.append(authority)
        return self.state

    def read_cursor(self, scope, scope_key):
        self.cursor_calls.append((scope, scope_key))
        return self.cursor

    def read_active_message_inventory(self, scope, *, limit):
        self.active_calls.append((scope, limit))
        return self.active_inventory

    def read_messages_by_provider_message_ids(self, scope, provider_message_ids):
        self.exact_calls.append((scope, tuple(provider_message_ids)))
        return self.exact_projections

    def list_messages(self, scope, *, limit, before_timestamp_millis=None):
        raise AssertionError("stale recovery must not use recency list_messages")


class _Writer:
    def __init__(self, outcome=DeltaCommitOutcome.APPLIED):
        self.outcome = outcome
        self.commits = []

    def initialize_current_state(self, authority, *, initialized_at_millis):
        raise AssertionError("stale recovery must not initialize state")

    def commit_provider_delta(self, commit):
        self.commits.append(commit)
        return self.outcome


class _Repositories:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer


def _run(repositories, request, recover, *, fresh_history_id="500"):
    return run_preview_gmail_stale_recovery(
        environment=_environment(),
        workspace_id="wsp_" + ("a" * 22),
        owner_user_id="usr_" + ("b" * 22),
        mailbox_id="gmail-1",
        mailbox_account_identity="Verified@Gmail.com",
        context={
            "mailbox_email": "Verified@Gmail.com",
            "mailbox_id": "gmail-1",
            "refresh_attempted": False,
        },
        fresh_history_id=fresh_history_id,
        request_with_one_refresh=request,
        recover_exact_message=recover,
        committed_at_millis=1_790_179_000_000,
        repositories=repositories,
    )


class PreviewGmailStaleRecoveryTests(unittest.TestCase):
    def test_complete_recovery_commits_update_resurrection_and_deletions(self):
        changed_id = "gmail-message-changed"
        resurrected_id = "gmail-message-resurrected"
        absent_id = "gmail-message-absent"
        terminal_id = "gmail-message-terminal"

        changed_old = _record(changed_id, subject="Old")
        changed_new = _record(changed_id, subject="New")
        resurrected = _record(resurrected_id)
        absent = _record(absent_id)
        terminal = _record(terminal_id)

        changed_current = _projection(
            changed_old,
            row_version=4,
            body_state=BodyState.CACHED,
        )
        reader = _Reader(
            state=_state(),
            cursor=_cursor(),
            active_inventory=BoundedMessageProjectionInventory(
                (
                    changed_current,
                    _projection(absent, row_version=2),
                    _projection(terminal, row_version=6),
                ),
                False,
            ),
            exact_projections=(
                changed_current,
                _projection(
                    resurrected,
                    row_version=8,
                    body_state=BodyState.STALE,
                    provider_deleted=True,
                ),
            ),
        )
        writer = _Writer()

        def request(context, path):
            self.assertEqual(path, "/messages?labelIds=INBOX&maxResults=100")
            return (
                {
                    "messages": [
                        {"id": changed_id},
                        {"id": resurrected_id},
                        {"id": terminal_id},
                    ]
                },
                None,
                {**context, "inventory_seen": True},
                None,
            )

        def recover(context, provider_message_id):
            if provider_message_id == changed_id:
                return PreviewGmailHistoryRecovery(
                    "recovered",
                    {**context, "changed_seen": True},
                    _preview(changed_id, subject="New"),
                    _source(changed_id, subject="New"),
                )
            if provider_message_id == resurrected_id:
                return PreviewGmailHistoryRecovery(
                    "recovered",
                    {**context, "resurrected_seen": True},
                    _preview(resurrected_id),
                    _source(resurrected_id),
                )
            return PreviewGmailHistoryRecovery(
                "terminal_absent",
                {**context, "terminal_seen": True},
            )

        result = _run(_Repositories(reader, writer), request, recover)

        self.assertEqual(result.status, "applied")
        self.assertEqual(result.mutation_count, 4)
        self.assertEqual(result.provider_count, 3)
        self.assertEqual(result.next_history_id, "500")
        self.assertTrue(result.context["terminal_seen"])
        self.assertEqual(reader.active_calls[0][1], 100)
        self.assertEqual(
            reader.exact_calls[0][1],
            (changed_id, resurrected_id),
        )
        self.assertEqual(len(writer.commits), 1)
        commit = writer.commits[0]
        self.assertEqual(commit.expected_state_row_version, 11)
        self.assertEqual(commit.expected_cursor_row_version, 3)
        self.assertEqual(commit.next_cursor.gmail_history_id, "500")
        self.assertEqual(commit.next_cursor.row_version, 4)
        self.assertIs(commit.next_cursor.backfill_state, BackfillState.COMPLETE)
        self.assertIsNone(commit.next_cursor.backfill_cursor)
        self.assertIs(commit.next_bootstrap_state, BootstrapState.READY)
        self.assertEqual(
            [mutation.kind.value for mutation in commit.mutations],
            ["upsert", "upsert", "tombstone", "tombstone"],
        )
        self.assertIs(commit.mutations[0].record.body_state, BodyState.CACHED)
        self.assertEqual(commit.mutations[1].expected_row_version, 8)
        self.assertEqual(commit.mutations[2].expected_row_version, 2)
        self.assertEqual(commit.mutations[3].expected_row_version, 6)

    def test_provider_overflow_stops_before_durable_inventory_or_recovery(self):
        reader = _Reader(state=_state(), cursor=_cursor())
        writer = _Writer()
        recover_calls = []

        result = _run(
            _Repositories(reader, writer),
            lambda context, _path: (
                {
                    "messages": [{"id": "gmail-message-1"}],
                    "nextPageToken": "page-2",
                },
                None,
                context,
                None,
            ),
            lambda *_args: recover_calls.append("recover"),
        )

        self.assertEqual(result.status, "provider_overflow")
        self.assertEqual(reader.active_calls, [])
        self.assertEqual(reader.exact_calls, [])
        self.assertEqual(recover_calls, [])
        self.assertEqual(writer.commits, [])

    def test_durable_overflow_stops_before_exact_recovery(self):
        reader = _Reader(
            state=_state(),
            cursor=_cursor(),
            active_inventory=BoundedMessageProjectionInventory((), True),
        )
        writer = _Writer()
        recover_calls = []

        result = _run(
            _Repositories(reader, writer),
            lambda context, _path: (
                {"messages": [{"id": "gmail-message-1"}]},
                None,
                context,
                None,
            ),
            lambda *_args: recover_calls.append("recover"),
        )

        self.assertEqual(result.status, "durable_overflow")
        self.assertEqual(recover_calls, [])
        self.assertEqual(reader.exact_calls, [])
        self.assertEqual(writer.commits, [])

    def test_retryable_exact_recovery_never_reads_exact_rows_or_commits(self):
        reader = _Reader(state=_state(), cursor=_cursor())
        writer = _Writer()

        result = _run(
            _Repositories(reader, writer),
            lambda context, _path: (
                {"messages": [{"id": "gmail-message-1"}]},
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
        self.assertTrue(result.context["retry_seen"])
        self.assertEqual(reader.exact_calls, [])
        self.assertEqual(writer.commits, [])

    def test_missing_or_nonready_state_never_reads_provider_inventory(self):
        for reader in (
            _Reader(state=None),
            _Reader(
                state=_state(bootstrap_state=BootstrapState.RECOVERING),
                cursor=_cursor(),
            ),
        ):
            with self.subTest(state=reader.state):
                provider_calls = []
                writer = _Writer()
                result = _run(
                    _Repositories(reader, writer),
                    lambda *_args: provider_calls.append("provider"),
                    lambda *_args: (_ for _ in ()).throw(
                        AssertionError("recovery called")
                    ),
                )
                self.assertIn(result.status, {"state_missing", "state_not_ready"})
                self.assertEqual(provider_calls, [])
                self.assertEqual(writer.commits, [])

    def test_missing_cursor_never_reads_provider_inventory(self):
        reader = _Reader(state=_state(), cursor=None)
        writer = _Writer()
        provider_calls = []

        result = _run(
            _Repositories(reader, writer),
            lambda *_args: provider_calls.append("provider"),
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("recovery called")
            ),
        )

        self.assertEqual(result.status, "cursor_missing")
        self.assertEqual(provider_calls, [])
        self.assertEqual(writer.commits, [])

    def test_complete_unchanged_inventory_promotes_recent_ready_to_ready(self):
        reader = _Reader(
            state=_state(bootstrap_state=BootstrapState.RECENT_READY),
            cursor=_cursor(history_id="500"),
            active_inventory=BoundedMessageProjectionInventory((), False),
            exact_projections=(),
        )
        writer = _Writer()

        result = _run(
            _Repositories(reader, writer),
            lambda context, path: (
                {"messages": []},
                None,
                context,
                None,
            ),
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("empty inventory must not recover messages")
            ),
            fresh_history_id="500",
        )

        self.assertEqual(result.status, "applied")
        self.assertEqual(result.mutation_count, 0)
        self.assertEqual(len(writer.commits), 1)
        commit = writer.commits[0]
        self.assertIs(commit.next_bootstrap_state, BootstrapState.READY)
        self.assertIs(commit.next_cursor.backfill_state, BackfillState.COMPLETE)
        self.assertIsNone(commit.next_cursor.backfill_cursor)
        self.assertEqual(commit.next_cursor.row_version, 4)

    def test_already_ready_complete_unchanged_inventory_is_observational(self):
        ready_cursor = SyncCursor(
            scope_key="gmail-account",
            cursor_generation=1,
            provider=MailboxProvider.GOOGLE,
            gmail_history_id="500",
            imap_uid_validity=None,
            imap_highest_uid=None,
            imap_uidnext_observed=None,
            backfill_state=BackfillState.COMPLETE,
            backfill_cursor=None,
            row_version=3,
        )
        reader = _Reader(
            state=_state(bootstrap_state=BootstrapState.READY),
            cursor=ready_cursor,
            active_inventory=BoundedMessageProjectionInventory((), False),
            exact_projections=(),
        )
        writer = _Writer()

        result = _run(
            _Repositories(reader, writer),
            lambda context, _path: ({"messages": []}, None, context, None),
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("empty inventory must not recover messages")
            ),
            fresh_history_id="500",
        )

        self.assertEqual(result.status, "unchanged")
        self.assertEqual(result.mutation_count, 0)
        self.assertEqual(writer.commits, [])

    def test_writer_conflict_is_observational(self):
        reader = _Reader(state=_state(), cursor=_cursor())
        writer = _Writer(DeltaCommitOutcome.CONFLICT)

        result = _run(
            _Repositories(reader, writer),
            lambda context, _path: ({}, None, context, None),
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("recovery called")
            ),
        )

        self.assertEqual(result.status, "conflict")
        self.assertEqual(result.mutation_count, 0)
        self.assertEqual(len(writer.commits), 1)


if __name__ == "__main__":
    unittest.main()
