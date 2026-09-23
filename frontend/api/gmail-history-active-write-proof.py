"""TEMPORARY Preview-only real PostgreSQL proof for Gmail History sync."""

import json
import os
from http.server import BaseHTTPRequestHandler

from cuevion_mailbox.preview_active_write import (
    PreviewGmailHistoryRecovery,
    preview_active_write_enabled,
    run_preview_gmail_durable_write,
    run_preview_gmail_history_sync,
)
from cuevion_mailbox.repository_contract import (
    MailboxProvider,
    MailboxReadAuthority,
)
from cuevion_mailbox.runtime import build_active_write_mailbox_repositories


_WORKSPACE_ID = "wsp_l44kMFQRDa7J3askwYxxbQ"
_OWNER_USER_ID = "usr_jDkwYBEn-6jawBwY_-Pzpg"
_MAILBOX_ID = "preview-gmail-history-sync-proof"
_MAILBOX_IDENTITY = "preview-gmail-history-sync@example.invalid"
_CHANGED_ID = "preview-history-message-changed"
_ABSENT_ID = "preview-history-message-absent"
_INITIAL_HISTORY_ID = "3000"
_NEXT_HISTORY_ID = "3010"


def _preview(message_id: str, subject: str) -> dict:
    return {
        "providerMessageId": message_id,
        "providerThreadId": "thread-" + message_id,
        "providerFolder": "Inbox",
        "labelIds": ["INBOX", "UNREAD"],
        "rfcMessageId": message_id + "@example.invalid",
        "to": "Proof <preview-gmail-history-sync@example.invalid>",
        "cc": "",
        "subject": subject,
    }


def _source(message_id: str, subject: str) -> dict:
    return {
        "provider": "google",
        "providerMessageId": message_id,
        "providerThreadId": "thread-" + message_id,
        "providerFolder": "INBOX",
        "labels": ["INBOX", "UNREAD"],
        "providerTimestampMillis": "1790167000000",
        "senderDisplay": "Proof Sender",
        "senderAddress": "proof-sender@example.invalid",
        "subject": subject,
        "snippet": "Synthetic Preview Gmail History proof",
        "unread": True,
        "flagged": False,
    }


def _outbox_summary(repositories) -> tuple[int, int, int, int, int, int]:
    connection = repositories.writer._connection()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                count(*)::int,
                count(*) FILTER (WHERE processed_at IS NULL)::int,
                coalesce(sum(attempt_count), 0)::int,
                count(*) FILTER (WHERE event_type = 'message_added')::int,
                count(*) FILTER (WHERE event_type = 'message_changed')::int,
                count(*) FILTER (WHERE event_type = 'message_deleted')::int
            FROM cuevion_mailbox.mailbox_change_outbox
            WHERE workspace_id = %s
              AND owner_user_id = %s
              AND mailbox_id = %s
              AND source_generation = 1
            """,
            (_WORKSPACE_ID, _OWNER_USER_ID, _MAILBOX_ID),
        )
        rows = cursor.fetchall()
        if len(rows) != 1 or len(rows[0]) != 6:
            raise RuntimeError("invalid proof outbox summary")
        return tuple(int(value) for value in rows[0])
    finally:
        if cursor is not None:
            cursor.close()
        connection.rollback()
        connection.close()


def _proof():
    if not preview_active_write_enabled(os.environ):
        return 503, {"ok": False, "stage": "active_write_disabled"}

    repositories = build_active_write_mailbox_repositories(os.environ)
    authority = MailboxReadAuthority(
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=_MAILBOX_ID,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity=_MAILBOX_IDENTITY,
    )

    state = repositories.reader.resolve_current_state(authority)
    bootstrap_status = "existing"
    if state is None:
        bootstrap = run_preview_gmail_durable_write(
            environment=os.environ,
            workspace_id=_WORKSPACE_ID,
            owner_user_id=_OWNER_USER_ID,
            mailbox_id=_MAILBOX_ID,
            mailbox_account_identity=_MAILBOX_IDENTITY,
            previews=[
                _preview(_CHANGED_ID, "Old subject"),
                _preview(_ABSENT_ID, "Absent subject"),
            ],
            candidate_sources=[
                _source(_CHANGED_ID, "Old subject"),
                _source(_ABSENT_ID, "Absent subject"),
            ],
            gmail_history_id=_INITIAL_HISTORY_ID,
            committed_at_millis=1790167000000,
            repositories=repositories,
        )
        bootstrap_status = bootstrap.status
        if bootstrap.status != "applied":
            return 503, {
                "ok": False,
                "stage": "bootstrap",
                "status": bootstrap.status,
            }

    state = repositories.reader.resolve_current_state(authority)
    if state is None:
        return 503, {"ok": False, "stage": "state_missing"}

    cursor = repositories.reader.read_cursor(state.scope, "gmail-account")
    if cursor is None:
        return 503, {"ok": False, "stage": "cursor_missing"}

    history_status = "already_applied"
    history_mutations = 0
    if cursor.gmail_history_id == _INITIAL_HISTORY_ID:
        def history_request(context, path):
            if "startHistoryId=" + _INITIAL_HISTORY_ID not in path:
                return None, {"code": "unexpected_history_path"}, context, None
            return (
                {
                    "historyId": _NEXT_HISTORY_ID,
                    "history": [
                        {
                            "id": "3008",
                            "messages": [
                                {"id": _CHANGED_ID},
                                {"id": _ABSENT_ID},
                            ],
                        }
                    ],
                },
                None,
                {**context, "proof_history_seen": True},
                None,
            )

        def recover(context, provider_message_id):
            if provider_message_id == _CHANGED_ID:
                return PreviewGmailHistoryRecovery(
                    "recovered",
                    {**context, "proof_changed_recovered": True},
                    _preview(_CHANGED_ID, "New subject"),
                    _source(_CHANGED_ID, "New subject"),
                )
            if provider_message_id == _ABSENT_ID:
                return PreviewGmailHistoryRecovery(
                    "terminal_absent",
                    {**context, "proof_absent_verified": True},
                )
            return PreviewGmailHistoryRecovery("retry", context)

        history = run_preview_gmail_history_sync(
            environment=os.environ,
            workspace_id=_WORKSPACE_ID,
            owner_user_id=_OWNER_USER_ID,
            mailbox_id=_MAILBOX_ID,
            mailbox_account_identity=_MAILBOX_IDENTITY,
            context={
                "mailbox_email": _MAILBOX_IDENTITY,
                "mailbox_id": _MAILBOX_ID,
                "refresh_attempted": False,
            },
            request_with_one_refresh=history_request,
            recover_exact_message=recover,
            committed_at_millis=1790167010000,
            repositories=repositories,
        )
        history_status = history.status
        history_mutations = history.mutation_count
        if (
            history.status != "applied"
            or history.mutation_count != 2
            or history.next_history_id != _NEXT_HISTORY_ID
        ):
            return 503, {
                "ok": False,
                "stage": "history",
                "status": history.status,
                "mutation_count": history.mutation_count,
                "next_history_id": history.next_history_id,
            }
    elif cursor.gmail_history_id != _NEXT_HISTORY_ID:
        return 503, {
            "ok": False,
            "stage": "unexpected_cursor",
            "cursor_history_id": cursor.gmail_history_id,
        }

    state = repositories.reader.resolve_current_state(authority)
    if state is None:
        return 503, {"ok": False, "stage": "verify_state_missing"}
    cursor = repositories.reader.read_cursor(state.scope, "gmail-account")
    projections = repositories.reader.read_messages_by_provider_message_ids(
        state.scope,
        [_CHANGED_ID, _ABSENT_ID],
    )
    by_provider_id = {
        projection.identity.provider_message_id: projection
        for projection in projections
    }
    changed = by_provider_id.get(_CHANGED_ID)
    absent = by_provider_id.get(_ABSENT_ID)
    outbox = _outbox_summary(repositories)

    expected = (
        cursor is not None
        and cursor.gmail_history_id == _NEXT_HISTORY_ID
        and cursor.row_version == 2
        and state.row_version == 3
        and len(projections) == 2
        and changed is not None
        and changed.provider_deleted is False
        and changed.row_version == 2
        and absent is not None
        and absent.provider_deleted is True
        and absent.row_version == 2
        and outbox == (4, 4, 0, 2, 1, 1)
    )
    if not expected:
        return 503, {
            "ok": False,
            "stage": "verification",
            "cursor_history_id": None if cursor is None else cursor.gmail_history_id,
            "cursor_row_version": None if cursor is None else cursor.row_version,
            "state_row_version": state.row_version,
            "projection_count": len(projections),
            "changed_deleted": None if changed is None else changed.provider_deleted,
            "changed_row_version": None if changed is None else changed.row_version,
            "absent_deleted": None if absent is None else absent.provider_deleted,
            "absent_row_version": None if absent is None else absent.row_version,
            "outbox": list(outbox),
        }

    return 200, {
        "ok": True,
        "status": (
            "already_applied"
            if history_status == "already_applied"
            else "applied"
        ),
        "bootstrap_status": bootstrap_status,
        "history_status": history_status,
        "history_mutations": history_mutations,
        "cursor_history_id": cursor.gmail_history_id,
        "cursor_row_version": cursor.row_version,
        "state_row_version": state.row_version,
        "projection_count": len(projections),
        "changed_row_version": changed.row_version,
        "absent_provider_deleted": absent.provider_deleted,
        "absent_row_version": absent.row_version,
        "outbox_count": outbox[0],
        "outbox_unprocessed": outbox[1],
        "outbox_attempt_sum": outbox[2],
        "outbox_message_added": outbox[3],
        "outbox_message_changed": outbox[4],
        "outbox_message_deleted": outbox[5],
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
