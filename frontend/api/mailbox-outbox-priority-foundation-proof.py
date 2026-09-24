"""TEMPORARY Preview-only proof for mailbox outbox -> Priority foundation."""

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

from api.priority.candidate_projection import PriorityCandidatePopulationAuthority
from api.priority.mailbox_outbox import (
    PriorityMailboxOutboxActionKind,
    plan_priority_mailbox_outbox_event,
)
from cuevion_mailbox.postgresql_repository import PostgreSQLMailboxReaderRepository
from cuevion_mailbox.repository_contract import (
    OutboxEvent,
    OutboxEventType,
    OutboxStorageScope,
)


_WORKSPACE_ID = "wsp_" + ("a" * 22)
_USER_ID = "usr_" + ("b" * 22)
_MAILBOX_ID = "gmail-1"
_MESSAGE_ID = "mbm_" + ("c" * 22)
_PROVIDER_MESSAGE_ID = "gmail-message-1"


def _authority():
    return PriorityCandidatePopulationAuthority(
        workspace_id=_WORKSPACE_ID,
        user_id=_USER_ID,
        mailbox_id=_MAILBOX_ID,
        mailbox_account_identity="verified@gmail.com",
        provider="google",
    )


def _event(event_type, *, row_version, generation=3, suffix="a"):
    return OutboxEvent(
        event_id="mbe_" + (suffix * 22),
        scope=OutboxStorageScope(
            workspace_id=_WORKSPACE_ID,
            owner_user_id=_USER_ID,
            mailbox_id=_MAILBOX_ID,
            source_generation=generation,
        ),
        message_id=_MESSAGE_ID,
        message_row_version=row_version,
        event_type=event_type,
        attempt_count=1,
        claim_token="proof-claim-token-" + suffix,
    )


def _message_row(*, row_version, deleted=False):
    return (
        "google",
        "verified@gmail.com",
        _MESSAGE_ID,
        _PROVIDER_MESSAGE_ID,
        "Inbox",
        None,
        None,
        "thread-1",
        ["INBOX", "UNREAD"],
        "rfc-1@example.test",
        None,
        [],
        "sender@example.test",
        "Proof Sender",
        ["owner@example.test"],
        [],
        "Proof subject",
        "Proof snippet",
        datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc),
        True,
        False,
        "stale" if deleted else "cached",
        "1" * 64,
        deleted,
        row_version,
    )


class _Cursor:
    def __init__(self, *, message_rows, state_rows=()):
        self.message_rows = list(message_rows)
        self.state_rows = list(state_rows)
        self.executions = []
        self.closed = False

    def execute(self, sql, parameters):
        self.executions.append((sql, parameters))

    def fetchall(self):
        if not self.executions:
            raise RuntimeError("proof fetch without execute")
        sql = self.executions[-1][0]
        if "FROM cuevion_mailbox.mailbox_messages AS m" in sql:
            return list(self.message_rows)
        if "FROM cuevion_mailbox.mailbox_sync_state" in sql:
            return list(self.state_rows)
        raise RuntimeError("unexpected proof SQL")

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


class _Factory:
    def __init__(self, connections):
        self.connections = list(connections)
        self.calls = 0

    def __call__(self):
        if not self.connections:
            raise RuntimeError("proof connection queue exhausted")
        self.calls += 1
        return self.connections.pop(0)


def _resolve(reader, event):
    return reader.resolve_outbox_message(event)


def _proof():
    if os.environ.get("VERCEL_ENV") != "preview":
        return 503, {"ok": False, "stage": "boundary"}

    current_cursor = _Cursor(
        message_rows=[_message_row(row_version=4, deleted=False)]
    )
    current_connection = _Connection(current_cursor)

    superseded_cursor = _Cursor(
        message_rows=[_message_row(row_version=5, deleted=True)]
    )
    superseded_connection = _Connection(superseded_cursor)

    stale_cursor = _Cursor(message_rows=[], state_rows=[])
    stale_connection = _Connection(stale_cursor)

    factory = _Factory(
        [
            current_connection,
            superseded_connection,
            stale_connection,
        ]
    )
    reader = PostgreSQLMailboxReaderRepository(factory)
    authority = _authority()

    current_event = _event(
        OutboxEventType.MESSAGE_CHANGED,
        row_version=4,
        suffix="a",
    )
    current_snapshot = _resolve(reader, current_event)
    current_action = plan_priority_mailbox_outbox_event(
        authority,
        current_event,
        current_snapshot,
    )

    delayed_event = _event(
        OutboxEventType.MESSAGE_CHANGED,
        row_version=4,
        suffix="b",
    )
    newer_snapshot = _resolve(reader, delayed_event)
    superseded_action = plan_priority_mailbox_outbox_event(
        authority,
        delayed_event,
        newer_snapshot,
    )

    stale_event = _event(
        OutboxEventType.MESSAGE_DELETED,
        row_version=1,
        generation=2,
        suffix="c",
    )
    stale_snapshot = _resolve(reader, stale_event)
    stale_action = plan_priority_mailbox_outbox_event(
        authority,
        stale_event,
        stale_snapshot,
    )

    expected = (
        current_snapshot is not None
        and current_snapshot.row_version == 4
        and current_snapshot.provider_deleted is False
        and current_snapshot.record.identity.provider_message_id
        == _PROVIDER_MESSAGE_ID
        and current_snapshot.record.provider_thread_id == "thread-1"
        and current_snapshot.record.subject == "Proof subject"
        and current_action.kind is PriorityMailboxOutboxActionKind.UPSERT
        and current_action.candidate_scope is not None
        and current_action.candidate_scope.identity.provider_message_id
        == _PROVIDER_MESSAGE_ID
        and current_action.source is not None
        and current_action.source["providerFolder"] == "INBOX"
        and current_action.source["providerTimestampMillis"]
        == "1790236800000"
        and newer_snapshot is not None
        and newer_snapshot.row_version == 5
        and newer_snapshot.provider_deleted is True
        and superseded_action.kind
        is PriorityMailboxOutboxActionKind.SUPERSEDED
        and stale_snapshot is None
        and stale_action.kind
        is PriorityMailboxOutboxActionKind.STALE_GENERATION
        and factory.calls == 3
        and current_connection.rollbacks == 1
        and superseded_connection.rollbacks == 1
        and stale_connection.rollbacks == 1
        and current_cursor.closed
        and superseded_cursor.closed
        and stale_cursor.closed
        and current_connection.closed
        and superseded_connection.closed
        and stale_connection.closed
        and len(stale_cursor.executions) == 2
    )
    if not expected:
        return 503, {"ok": False, "stage": "assertion"}

    return 200, {
        "ok": True,
        "current_action": current_action.kind.value,
        "current_row_version": current_snapshot.row_version,
        "current_provider_deleted": current_snapshot.provider_deleted,
        "candidate_provider_message_id": (
            current_action.candidate_scope.identity.provider_message_id
        ),
        "candidate_provider_folder": current_action.source["providerFolder"],
        "candidate_timestamp": current_action.source[
            "providerTimestampMillis"
        ],
        "superseded_action": superseded_action.kind.value,
        "superseded_current_row_version": newer_snapshot.row_version,
        "superseded_current_deleted": newer_snapshot.provider_deleted,
        "stale_generation_action": stale_action.kind.value,
        "stale_generation_snapshot": None,
        "resolver_connection_count": factory.calls,
        "stale_generation_query_count": len(stale_cursor.executions),
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
