"""TEMPORARY Preview-only proof for stale Gmail History recovery foundation."""

import json
import os
from http.server import BaseHTTPRequestHandler

from cuevion_mailbox.gmail_delta_plan import build_gmail_stale_recovery_commit
from cuevion_mailbox.gmail_projection import project_gmail_snapshot_message
from cuevion_mailbox.gmail_recovery_inventory import (
    read_complete_gmail_inbox_recovery_inventory,
)
from cuevion_mailbox.postgresql_repository import PostgreSQLMailboxRepository
from cuevion_mailbox.repository_contract import (
    BackfillState,
    BodyState,
    BootstrapState,
    MailboxProvider,
    MailboxScope,
    MessageProjection,
    SyncCursor,
)


def _scope():
    return MailboxScope(
        workspace_id="wsp_" + ("a" * 22),
        owner_user_id="usr_" + ("b" * 22),
        mailbox_id="gmail-stale-recovery-proof",
        source_generation=1,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity="preview-stale-recovery@example.invalid",
    )


def _preview(message_id, subject):
    return {
        "providerMessageId": message_id,
        "providerThreadId": "thread-" + message_id,
        "providerFolder": "Inbox",
        "labelIds": ["INBOX", "UNREAD"],
        "rfcMessageId": message_id + "@example.invalid",
        "to": "Proof <preview-stale-recovery@example.invalid>",
        "cc": "",
        "subject": subject,
    }


def _source(message_id, subject):
    return {
        "provider": "google",
        "providerMessageId": message_id,
        "providerThreadId": "thread-" + message_id,
        "providerFolder": "INBOX",
        "labels": ["INBOX", "UNREAD"],
        "providerTimestampMillis": "1790175000000",
        "senderDisplay": "Proof Sender",
        "senderAddress": "proof-sender@example.invalid",
        "subject": subject,
        "snippet": "Synthetic stale recovery proof",
        "unread": True,
        "flagged": False,
    }


def _record(message_id, subject):
    return project_gmail_snapshot_message(
        _scope(),
        _preview(message_id, subject),
        _source(message_id, subject),
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


class _InventoryCursor:
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


class _InventoryConnection:
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


def _row(
    record,
    *,
    row_version,
    body_state=None,
):
    durable_body_state = body_state or record.body_state
    return (
        record.identity.message_id,
        record.identity.provider_message_id,
        record.identity.provider_folder,
        None,
        None,
        record.provider_thread_id,
        record.metadata_hash,
        durable_body_state.value,
        record.unread,
        record.starred,
        False,
        row_version,
    )


def _proof():
    if os.environ.get("VERCEL_ENV") != "preview":
        return 503, {"ok": False, "stage": "boundary"}

    changed_id = "gmail-message-changed"
    resurrected_id = "gmail-message-resurrected"
    absent_id = "gmail-message-absent"
    terminal_id = "gmail-message-terminal"

    provider_inventory = read_complete_gmail_inbox_recovery_inventory(
        {
            "mailbox_email": "preview-stale-recovery@example.invalid",
            "mailbox_id": "gmail-stale-recovery-proof",
            "refresh_attempted": False,
        },
        request_with_one_refresh=lambda context, path: (
            {
                "messages": [
                    {"id": changed_id},
                    {"id": resurrected_id},
                    {"id": terminal_id},
                ]
            },
            None,
            {**context, "inventory_seen": path},
            None,
        ),
    )
    overflow_inventory = read_complete_gmail_inbox_recovery_inventory(
        {
            "mailbox_email": "preview-stale-recovery@example.invalid",
            "mailbox_id": "gmail-stale-recovery-proof",
            "refresh_attempted": False,
        },
        request_with_one_refresh=lambda context, _path: (
            {
                "messages": [{"id": changed_id}],
                "nextPageToken": "page-2",
            },
            None,
            context,
            None,
        ),
    )

    changed_old = _record(changed_id, "Old subject")
    changed_new = _record(changed_id, "New subject")
    resurrected = _record(resurrected_id, "Resurrected")
    absent = _record(absent_id, "Absent")
    terminal = _record(terminal_id, "Terminal")

    inventory_cursor = _InventoryCursor(
        [
            _row(
                changed_old,
                row_version=4,
                body_state=BodyState.CACHED,
            ),
            _row(absent, row_version=2),
            _row(terminal, row_version=6),
        ]
    )
    inventory_connection = _InventoryConnection(inventory_cursor)
    repository = PostgreSQLMailboxRepository(
        lambda: inventory_connection
    )
    active_inventory = repository.read_active_message_inventory(
        _scope(),
        limit=100,
    )

    changed_current = _projection(
        changed_old,
        row_version=4,
        body_state=BodyState.CACHED,
    )
    resurrected_tombstone = _projection(
        resurrected,
        row_version=8,
        body_state=BodyState.STALE,
        provider_deleted=True,
    )

    commit = build_gmail_stale_recovery_commit(
        _scope(),
        list(provider_inventory.provider_message_ids),
        [changed_new, resurrected],
        [terminal_id],
        list(active_inventory.projections),
        [changed_current, resurrected_tombstone],
        expected_state_row_version=11,
        current_cursor=_cursor("100", 3),
        next_cursor=_cursor("500", 4),
        committed_at_millis=1790175000000,
        next_bootstrap_state=BootstrapState.RECENT_READY,
    )

    kinds = [mutation.kind.value for mutation in commit.mutations]
    provider_ids = [
        mutation.identity.provider_message_id
        for mutation in commit.mutations
    ]

    expected = (
        provider_inventory.status == "ok"
        and provider_inventory.provider_message_ids
        == (changed_id, resurrected_id, terminal_id)
        and overflow_inventory.status == "overflow"
        and not overflow_inventory.provider_message_ids
        and not active_inventory.overflow
        and len(active_inventory.projections) == 3
        and inventory_cursor.executions
        and inventory_cursor.executions[0][1][-1] == 101
        and inventory_connection.rollbacks == 1
        and inventory_connection.closed
        and inventory_cursor.closed
        and kinds == ["upsert", "upsert", "tombstone", "tombstone"]
        and provider_ids
        == [changed_id, resurrected_id, absent_id, terminal_id]
        and commit.mutations[0].expected_row_version == 4
        and commit.mutations[0].record.body_state is BodyState.CACHED
        and commit.mutations[1].expected_row_version == 8
        and commit.mutations[2].expected_row_version == 2
        and commit.mutations[3].expected_row_version == 6
        and commit.expected_state_row_version == 11
        and commit.expected_cursor_row_version == 3
        and commit.next_cursor.gmail_history_id == "500"
        and commit.next_cursor.row_version == 4
    )
    if not expected:
        return 503, {"ok": False, "stage": "assertion"}

    return 200, {
        "ok": True,
        "provider_inventory_status": provider_inventory.status,
        "provider_inventory_count": len(provider_inventory.provider_message_ids),
        "provider_overflow_status": overflow_inventory.status,
        "durable_inventory_count": len(active_inventory.projections),
        "durable_inventory_overflow": active_inventory.overflow,
        "mutation_count": len(commit.mutations),
        "mutation_kinds": kinds,
        "cached_body_preserved": (
            commit.mutations[0].record.body_state.value
        ),
        "resurrection_expected_row_version": (
            commit.mutations[1].expected_row_version
        ),
        "tombstone_expected_versions": [
            commit.mutations[2].expected_row_version,
            commit.mutations[3].expected_row_version,
        ],
        "expected_cursor_row_version": commit.expected_cursor_row_version,
        "next_history_id": commit.next_cursor.gmail_history_id,
        "next_cursor_row_version": commit.next_cursor.row_version,
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
