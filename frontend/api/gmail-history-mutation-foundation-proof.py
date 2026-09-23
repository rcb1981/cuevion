"""TEMPORARY Preview-only runtime proof for Gmail History mutation foundation."""

import json
import os
from http.server import BaseHTTPRequestHandler

from cuevion_mailbox.gmail_delta_plan import (
    build_gmail_history_delta_commit,
    plan_gmail_history_message_mutations,
)
from cuevion_mailbox.gmail_projection import (
    derive_gmail_message_id,
    project_gmail_snapshot_message,
)
from cuevion_mailbox.postgresql_repository import PostgreSQLMailboxRepository
from cuevion_mailbox.repository_contract import (
    BackfillState,
    BodyState,
    BootstrapState,
    MailboxProvider,
    MailboxScope,
    MessageMutationKind,
    SyncCursor,
)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.executions = []
        self.closed = False

    def execute(self, sql, parameters):
        self.executions.append((sql, parameters))

    def fetchall(self):
        return list(self.rows)

    def close(self):
        self.closed = True


class _Connection:
    autocommit = False

    def __init__(self, cursor):
        self._cursor = cursor
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self._cursor

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _preview(message_id):
    return {
        "providerMessageId": message_id,
        "providerThreadId": "thread-" + message_id,
        "providerFolder": "Inbox",
        "labelIds": ["INBOX", "UNREAD"],
        "rfcMessageId": message_id + "@example.test",
        "to": "Owner <owner@example.test>",
        "cc": "",
    }


def _source(message_id, subject):
    return {
        "provider": "google",
        "providerMessageId": message_id,
        "providerThreadId": "thread-" + message_id,
        "providerFolder": "INBOX",
        "labels": ["UNREAD", "INBOX"],
        "providerTimestampMillis": "1790160000000",
        "senderDisplay": "Sender",
        "senderAddress": "sender@example.test",
        "subject": subject,
        "snippet": "Snippet",
        "unread": True,
        "flagged": False,
    }


def _cursor(history_id, row_version):
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


def _proof():
    if os.environ.get("VERCEL_ENV") != "preview":
        return 503, {"ok": False, "stage": "boundary"}

    scope = MailboxScope(
        workspace_id="wsp_" + ("a" * 22),
        owner_user_id="usr_" + ("b" * 22),
        mailbox_id="preview-history-foundation-proof",
        source_generation=1,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity="preview-history-proof@example.invalid",
    )

    changed_id = "gmail-message-changed"
    absent_id = "gmail-message-absent"
    old_changed = project_gmail_snapshot_message(
        scope,
        _preview(changed_id),
        _source(changed_id, "Old subject"),
    )
    changed = project_gmail_snapshot_message(
        scope,
        _preview(changed_id),
        _source(changed_id, "New subject"),
    )
    absent = project_gmail_snapshot_message(
        scope,
        _preview(absent_id),
        _source(absent_id, "Absent subject"),
    )

    rows = [
        (
            derive_gmail_message_id(scope, changed_id),
            changed_id,
            "Inbox",
            None,
            None,
            old_changed.provider_thread_id,
            old_changed.metadata_hash,
            BodyState.CACHED.value,
            old_changed.unread,
            old_changed.starred,
            False,
            4,
        ),
        (
            derive_gmail_message_id(scope, absent_id),
            absent_id,
            "Inbox",
            None,
            None,
            absent.provider_thread_id,
            absent.metadata_hash,
            BodyState.NOT_CACHED.value,
            absent.unread,
            absent.starred,
            False,
            2,
        ),
    ]
    cursor = _Cursor(rows)
    connection = _Connection(cursor)
    repository = PostgreSQLMailboxRepository(lambda: connection)
    projections = repository.read_messages_by_provider_message_ids(
        scope,
        [changed_id, absent_id],
    )

    commit = build_gmail_history_delta_commit(
        scope,
        [changed],
        [absent_id],
        list(projections),
        expected_state_row_version=11,
        current_cursor=_cursor("2000", 3),
        next_cursor=_cursor("2010", 4),
        committed_at_millis=1790160000000,
        next_bootstrap_state=BootstrapState.RECENT_READY,
    )
    unknown = plan_gmail_history_message_mutations(
        scope,
        [],
        ["gmail-message-unknown"],
        [],
    )

    expected = (
        len(projections) == 2
        and len(commit.mutations) == 2
        and commit.mutations[0].kind is MessageMutationKind.UPSERT
        and commit.mutations[0].expected_row_version == 4
        and commit.mutations[0].record is not None
        and commit.mutations[0].record.body_state is BodyState.CACHED
        and commit.mutations[1].kind is MessageMutationKind.TOMBSTONE
        and commit.mutations[1].expected_row_version == 2
        and commit.expected_cursor_row_version == 3
        and commit.next_cursor.gmail_history_id == "2010"
        and unknown == ()
        and connection.rollbacks == 1
        and connection.closed
        and cursor.closed
        and len(cursor.executions) == 1
    )
    if not expected:
        return 503, {"ok": False, "stage": "assertion"}

    return 200, {
        "ok": True,
        "exact_projection_count": len(projections),
        "mutation_count": len(commit.mutations),
        "upsert_kind": commit.mutations[0].kind.value,
        "upsert_body_state": commit.mutations[0].record.body_state.value,
        "tombstone_kind": commit.mutations[1].kind.value,
        "expected_cursor_row_version": commit.expected_cursor_row_version,
        "next_history_id": commit.next_cursor.gmail_history_id,
        "unknown_absence_mutations": len(unknown),
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            status, payload = _proof()
        except Exception:
            status, payload = 503, {"ok": False, "stage": "exception"}
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = b'{"ok":false,"stage":"method"}'
        self.send_response(405)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return
