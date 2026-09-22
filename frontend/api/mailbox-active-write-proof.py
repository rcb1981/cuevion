"""TEMPORARY Preview-only active write proof. Remove before merge."""

import json
import os
from http.server import BaseHTTPRequestHandler

from cuevion_mailbox.gmail_delta_plan import build_gmail_delta_commit
from cuevion_mailbox.gmail_projection import project_gmail_snapshot_message
from cuevion_mailbox.repository_contract import (
    BackfillState,
    BootstrapState,
    DeltaCommitOutcome,
    MailboxProvider,
    MailboxReadAuthority,
    SyncCursor,
)
from cuevion_mailbox.runtime import build_active_write_mailbox_repositories


_AUTHORITY = MailboxReadAuthority(
    workspace_id="wsp_l44kMFQRDa7J3askwYxxbQ",
    owner_user_id="usr_jDkwYBEn-6jawBwY_-Pzpg",
    mailbox_id="preview-active-write-proof",
    provider=MailboxProvider.GOOGLE,
    provider_account_identity="preview-write-proof@example.invalid",
)

_PREVIEW = {
    "providerMessageId": "preview-write-message-1",
    "providerThreadId": "preview-write-thread-1",
    "providerFolder": "Inbox",
    "labelIds": ["INBOX", "UNREAD"],
    "rfcMessageId": "preview-write-message-1@example.invalid",
    "to": "Preview <preview-write-proof@example.invalid>",
    "cc": "",
}

_SOURCE = {
    "provider": "google",
    "providerMessageId": "preview-write-message-1",
    "providerThreadId": "preview-write-thread-1",
    "providerFolder": "INBOX",
    "labels": ["UNREAD", "INBOX"],
    "providerTimestampMillis": "1790109000000",
    "senderDisplay": "Synthetic Preview Sender",
    "senderAddress": "sender@example.invalid",
    "subject": "Preview active write proof",
    "snippet": "Synthetic Preview-only durable write",
    "unread": True,
    "flagged": False,
}


def _proof():
    try:
        repositories = build_active_write_mailbox_repositories(os.environ)
    except Exception:
        return 503, {"ok": False, "stage": "runtime"}

    try:
        scope = repositories.reader.resolve_current_scope(_AUTHORITY)
    except Exception:
        return 503, {"ok": False, "stage": "scope"}
    if scope is None:
        return 503, {"ok": False, "stage": "scope_missing"}

    try:
        current_cursor = repositories.reader.read_cursor(scope, "gmail-account")
        current_messages = repositories.reader.list_messages(scope, limit=10)
    except Exception:
        return 503, {"ok": False, "stage": "pre_read"}

    if current_cursor is not None or current_messages:
        if (
            current_cursor is not None
            and current_cursor.gmail_history_id == "1001"
            and current_cursor.row_version == 1
            and len(current_messages) == 1
            and current_messages[0].identity.provider_message_id
            == "preview-write-message-1"
        ):
            return 200, {
                "ok": True,
                "outcome": "already_applied",
                "projected_count": 1,
                "cursor_history_id": "1001",
            }
        return 503, {"ok": False, "stage": "pre_state_dirty"}

    try:
        record = project_gmail_snapshot_message(scope, _PREVIEW, _SOURCE)
        next_cursor = SyncCursor(
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
        commit = build_gmail_delta_commit(
            scope,
            [record],
            [],
            expected_state_row_version=1,
            current_cursor=None,
            next_cursor=next_cursor,
            committed_at_millis=1790109000000,
            next_bootstrap_state=BootstrapState.RECENT_READY,
        )
    except Exception:
        return 503, {"ok": False, "stage": "plan"}

    try:
        outcome = repositories.writer.commit_provider_delta(commit)
    except Exception:
        return 503, {"ok": False, "stage": "commit"}
    if outcome is not DeltaCommitOutcome.APPLIED:
        return 503, {"ok": False, "stage": "outcome", "outcome": outcome.value}

    try:
        persisted_cursor = repositories.reader.read_cursor(scope, "gmail-account")
        persisted_messages = repositories.reader.list_messages(scope, limit=10)
    except Exception:
        return 503, {"ok": False, "stage": "readback"}

    if (
        persisted_cursor is None
        or persisted_cursor.gmail_history_id != "1001"
        or persisted_cursor.row_version != 1
        or len(persisted_messages) != 1
        or persisted_messages[0].identity.provider_message_id
        != "preview-write-message-1"
        or persisted_messages[0].row_version != 1
    ):
        return 503, {"ok": False, "stage": "verify"}

    return 200, {
        "ok": True,
        "outcome": "applied",
        "projected_count": 1,
        "cursor_history_id": "1001",
    }


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
