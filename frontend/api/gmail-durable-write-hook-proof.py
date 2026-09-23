"""TEMPORARY Preview-only Gmail durable write-hook proof. Remove before merge."""

import json
import os
from http.server import BaseHTTPRequestHandler

from cuevion_mailbox.gmail_history import read_gmail_account_history
from cuevion_mailbox.preview_active_write import run_preview_gmail_durable_write
from cuevion_mailbox.repository_contract import MailboxProvider, MailboxReadAuthority
from cuevion_mailbox.runtime import build_active_write_mailbox_repositories


_WORKSPACE_ID = "wsp_l44kMFQRDa7J3askwYxxbQ"
_OWNER_USER_ID = "usr_jDkwYBEn-6jawBwY_-Pzpg"
_MAILBOX_ID = "preview-gmail-write-hook-proof"
_ACCOUNT_IDENTITY = "preview-gmail-write-hook@example.invalid"

_AUTHORITY = MailboxReadAuthority(
    workspace_id=_WORKSPACE_ID,
    owner_user_id=_OWNER_USER_ID,
    mailbox_id=_MAILBOX_ID,
    provider=MailboxProvider.GOOGLE,
    provider_account_identity=_ACCOUNT_IDENTITY,
)

_PREVIEW = {
    "providerMessageId": "preview-hook-message-1",
    "providerThreadId": "preview-hook-thread-1",
    "providerFolder": "Inbox",
    "labelIds": ["INBOX", "UNREAD"],
    "rfcMessageId": "preview-hook-message-1@example.invalid",
    "to": "Preview <preview-gmail-write-hook@example.invalid>",
    "cc": "",
}

_SOURCE = {
    "provider": "google",
    "providerMessageId": "preview-hook-message-1",
    "providerThreadId": "preview-hook-thread-1",
    "providerFolder": "INBOX",
    "labels": ["UNREAD", "INBOX"],
    "providerTimestampMillis": "1790118000000",
    "senderDisplay": "Synthetic Preview Sender",
    "senderAddress": "sender@example.invalid",
    "subject": "Preview Gmail durable write hook",
    "snippet": "Synthetic Preview-only durable write hook",
    "unread": True,
    "flagged": False,
}


def _read_outbox_verification(repositories):
    connection = repositories.writer._connection()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT event_type, message_row_version, attempt_count,
                   processed_at IS NULL
            FROM cuevion_mailbox.mailbox_change_outbox
            WHERE workspace_id = %s
              AND owner_user_id = %s
              AND mailbox_id = %s
              AND source_generation = 1
            ORDER BY event_id
            """,
            (_WORKSPACE_ID, _OWNER_USER_ID, _MAILBOX_ID),
        )
        rows = cursor.fetchall()
        connection.rollback()
        return rows
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def _history(history_id: str):
    context = {"mailbox_email": _ACCOUNT_IDENTITY}
    return read_gmail_account_history(
        context,
        request_with_one_refresh=lambda request_context, path: (
            {
                "emailAddress": _ACCOUNT_IDENTITY,
                "historyId": history_id,
            },
            None,
            request_context,
            None,
        ),
    )


def _run_write(repositories, history_id: str, committed_at_millis: int):
    history = _history(history_id)
    if history.status != "ok" or history.history_id is None:
        raise RuntimeError("synthetic history invalid")
    return run_preview_gmail_durable_write(
        environment=os.environ,
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=_MAILBOX_ID,
        mailbox_account_identity=_ACCOUNT_IDENTITY,
        previews=[_PREVIEW],
        candidate_sources=[_SOURCE],
        gmail_history_id=history.history_id,
        committed_at_millis=committed_at_millis,
        repositories=repositories,
    )


def _proof():
    try:
        repositories = build_active_write_mailbox_repositories(os.environ)
    except Exception:
        return 503, {"ok": False, "stage": "runtime"}

    try:
        existing_state = repositories.reader.resolve_current_state(_AUTHORITY)
        if existing_state is not None:
            cursor = repositories.reader.read_cursor(
                existing_state.scope,
                "gmail-account",
            )
            messages = repositories.reader.list_messages(
                existing_state.scope,
                limit=10,
            )
            outbox_rows = _read_outbox_verification(repositories)
            if (
                cursor is not None
                and cursor.gmail_history_id == "2002"
                and cursor.row_version == 2
                and existing_state.row_version == 3
                and len(messages) == 1
                and messages[0].identity.provider_message_id
                == "preview-hook-message-1"
                and outbox_rows == [("message_added", 1, 0, True)]
            ):
                return 200, {
                    "ok": True,
                    "status": "already_applied",
                    "first": "applied",
                    "repeat": "unchanged",
                    "advance": "applied",
                    "projected_count": 1,
                    "cursor_history_id": "2002",
                    "outbox_count": 1,
                    "outbox_event_type": "message_added",
                    "outbox_unprocessed": True,
                }
            return 503, {"ok": False, "stage": "pre_state_dirty"}

        first = _run_write(repositories, "2001", 1790118000000)
        if first.status != "applied" or first.mutation_count != 1:
            return 503, {
                "ok": False,
                "stage": "first",
                "status": first.status,
            }

        repeat = _run_write(repositories, "2001", 1790118000001)
        if repeat.status != "unchanged" or repeat.mutation_count != 0:
            return 503, {
                "ok": False,
                "stage": "repeat",
                "status": repeat.status,
            }

        advance = _run_write(repositories, "2002", 1790118000002)
        if advance.status != "applied" or advance.mutation_count != 0:
            return 503, {
                "ok": False,
                "stage": "advance",
                "status": advance.status,
            }

        state = repositories.reader.resolve_current_state(_AUTHORITY)
        if state is None:
            return 503, {"ok": False, "stage": "state_readback"}
        cursor = repositories.reader.read_cursor(state.scope, "gmail-account")
        messages = repositories.reader.list_messages(state.scope, limit=10)
        outbox_rows = _read_outbox_verification(repositories)
        if (
            cursor is None
            or cursor.gmail_history_id != "2002"
            or cursor.row_version != 2
            or state.row_version != 3
            or len(messages) != 1
            or messages[0].identity.provider_message_id
            != "preview-hook-message-1"
            or messages[0].row_version != 1
            or outbox_rows != [("message_added", 1, 0, True)]
        ):
            return 503, {"ok": False, "stage": "verify"}

        return 200, {
            "ok": True,
            "status": "applied",
            "first": first.status,
            "repeat": repeat.status,
            "advance": advance.status,
            "projected_count": 1,
            "cursor_history_id": "2002",
            "outbox_count": 1,
            "outbox_event_type": "message_added",
            "outbox_unprocessed": True,
        }
    except Exception:
        return 503, {"ok": False, "stage": "exception"}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, payload = _proof()
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
