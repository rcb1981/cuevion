"""TEMPORARY Preview-only real Neon proof for Gmail cache authority readiness."""

import json
import os
from http.server import BaseHTTPRequestHandler

from cuevion_mailbox.preview_active_read import run_preview_gmail_active_read
from cuevion_mailbox.preview_active_write import (
    PreviewGmailHistoryRecovery,
    preview_active_write_enabled,
    run_preview_gmail_durable_write,
    run_preview_gmail_stale_recovery,
)
from cuevion_mailbox.repository_contract import (
    BackfillState,
    BootstrapState,
    MailboxProvider,
    MailboxReadAuthority,
)
from cuevion_mailbox.runtime import build_active_write_mailbox_repositories


_WORKSPACE_ID = "wsp_l44kMFQRDa7J3askwYxxbQ"
_OWNER_USER_ID = "usr_jDkwYBEn-6jawBwY_-Pzpg"
_IDENTITY = "preview-cache-readiness@example.invalid"

_READY_MAILBOX = "preview-gmail-cache-readiness-proof"
_OVERFLOW_MAILBOX = "preview-gmail-cache-readiness-overflow-proof"

_READY_MESSAGE = "preview-cache-ready-message"
_OVERFLOW_MESSAGE = "preview-cache-overflow-message"

_READY_INITIAL_HISTORY = "7100"
_READY_FRESH_HISTORY = "7110"
_OVERFLOW_INITIAL_HISTORY = "7200"
_OVERFLOW_FRESH_HISTORY = "7210"

_NOW = 1_790_270_000_000


def _preview(message_id: str, subject: str) -> dict:
    return {
        "providerMessageId": message_id,
        "providerThreadId": "thread-" + message_id,
        "providerFolder": "Inbox",
        "labelIds": ["INBOX", "UNREAD"],
        "rfcMessageId": message_id + "@example.invalid",
        "to": "Proof <" + _IDENTITY + ">",
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
        "providerTimestampMillis": "1790270000000",
        "rfcDate": None,
        "senderDisplay": "Proof Sender",
        "senderAddress": "proof-sender@example.invalid",
        "subject": subject,
        "snippet": "Synthetic Gmail cache readiness proof",
        "unread": True,
        "flagged": False,
    }


def _authority(mailbox_id: str) -> MailboxReadAuthority:
    return MailboxReadAuthority(
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=mailbox_id,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity=_IDENTITY,
    )


def _bootstrap(
    repositories,
    *,
    mailbox_id: str,
    message_id: str,
    history_id: str,
    subject: str,
) -> str:
    state = repositories.reader.resolve_current_state(_authority(mailbox_id))
    if state is not None:
        return "existing"
    result = run_preview_gmail_durable_write(
        environment=os.environ,
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=mailbox_id,
        mailbox_account_identity=_IDENTITY,
        previews=[_preview(message_id, subject)],
        candidate_sources=[_source(message_id, subject)],
        gmail_history_id=history_id,
        committed_at_millis=_NOW,
        repositories=repositories,
    )
    if result.status != "applied":
        raise RuntimeError("proof bootstrap failed")
    return result.status


def _recover_current(context, provider_message_id):
    return PreviewGmailHistoryRecovery(
        "recovered",
        context,
        _preview(provider_message_id, "Ready message"),
        _source(provider_message_id, "Ready message"),
    )


def _read_state(repositories, mailbox_id: str):
    state = repositories.reader.resolve_current_state(_authority(mailbox_id))
    if state is None:
        raise RuntimeError("proof state missing")
    cursor = repositories.reader.read_cursor(state.scope, "gmail-account")
    if cursor is None:
        raise RuntimeError("proof cursor missing")
    return state, cursor


def _proof():
    if not preview_active_write_enabled(os.environ):
        return 503, {"ok": False, "stage": "active_write_disabled"}

    repositories = build_active_write_mailbox_repositories(os.environ)

    ready_bootstrap = _bootstrap(
        repositories,
        mailbox_id=_READY_MAILBOX,
        message_id=_READY_MESSAGE,
        history_id=_READY_INITIAL_HISTORY,
        subject="Ready message",
    )
    overflow_bootstrap = _bootstrap(
        repositories,
        mailbox_id=_OVERFLOW_MAILBOX,
        message_id=_OVERFLOW_MESSAGE,
        history_id=_OVERFLOW_INITIAL_HISTORY,
        subject="Overflow message",
    )

    ready_before, ready_cursor_before = _read_state(
        repositories,
        _READY_MAILBOX,
    )
    overflow_before, overflow_cursor_before = _read_state(
        repositories,
        _OVERFLOW_MAILBOX,
    )

    read_before = run_preview_gmail_active_read(
        environment={
            "VERCEL_ENV": "preview",
            "CUEVION_MAILBOX_POSTGRES_MODE": "active_read",
        },
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=_READY_MAILBOX,
        mailbox_account_identity=_IDENTITY,
        limit=50,
        reader=repositories.reader,
    )
    if (
        ready_before.bootstrap_state is not BootstrapState.RECENT_READY
        or ready_cursor_before.backfill_state is not BackfillState.NOT_STARTED
        or read_before.status != "not_ready"
    ):
        return 503, {
            "ok": False,
            "stage": "precondition",
            "bootstrap_state": ready_before.bootstrap_state.value,
            "backfill_state": ready_cursor_before.backfill_state.value,
            "read_status": read_before.status,
        }

    recovery = run_preview_gmail_stale_recovery(
        environment=os.environ,
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=_READY_MAILBOX,
        mailbox_account_identity=_IDENTITY,
        context={
            "mailbox_id": _READY_MAILBOX,
            "mailbox_email": _IDENTITY,
        },
        fresh_history_id=_READY_FRESH_HISTORY,
        request_with_one_refresh=lambda context, path: (
            {"messages": [{"id": _READY_MESSAGE}]}
            if path == "/messages?labelIds=INBOX&maxResults=100"
            else None,
            None if path == "/messages?labelIds=INBOX&maxResults=100" else {
                "code": "unexpected_path"
            },
            context,
            None,
        ),
        recover_exact_message=_recover_current,
        committed_at_millis=_NOW + 1_000,
        repositories=repositories,
    )
    if recovery.status != "applied":
        return 503, {
            "ok": False,
            "stage": "ready_recovery",
            "status": recovery.status,
        }

    ready_after, ready_cursor_after = _read_state(
        repositories,
        _READY_MAILBOX,
    )
    read_after = run_preview_gmail_active_read(
        environment={
            "VERCEL_ENV": "preview",
            "CUEVION_MAILBOX_POSTGRES_MODE": "active_read",
        },
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=_READY_MAILBOX,
        mailbox_account_identity=_IDENTITY,
        limit=50,
        reader=repositories.reader,
    )

    overflow = run_preview_gmail_stale_recovery(
        environment=os.environ,
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=_OVERFLOW_MAILBOX,
        mailbox_account_identity=_IDENTITY,
        context={
            "mailbox_id": _OVERFLOW_MAILBOX,
            "mailbox_email": _IDENTITY,
        },
        fresh_history_id=_OVERFLOW_FRESH_HISTORY,
        request_with_one_refresh=lambda context, _path: (
            {
                "messages": [{"id": _OVERFLOW_MESSAGE}],
                "nextPageToken": "page-2",
            },
            None,
            context,
            None,
        ),
        recover_exact_message=lambda *_args: (_ for _ in ()).throw(
            AssertionError("overflow must not exact-recover")
        ),
        committed_at_millis=_NOW + 2_000,
        repositories=repositories,
    )
    overflow_after, overflow_cursor_after = _read_state(
        repositories,
        _OVERFLOW_MAILBOX,
    )

    expected = (
        ready_after.bootstrap_state is BootstrapState.READY
        and ready_cursor_after.backfill_state is BackfillState.COMPLETE
        and ready_cursor_after.backfill_cursor is None
        and ready_cursor_after.gmail_history_id == _READY_FRESH_HISTORY
        and read_after.status == "resolved"
        and read_after.projected_count == 1
        and overflow.status == "provider_overflow"
        and overflow_after.bootstrap_state is BootstrapState.RECENT_READY
        and overflow_cursor_after.backfill_state is BackfillState.NOT_STARTED
        and overflow_cursor_after.gmail_history_id == _OVERFLOW_INITIAL_HISTORY
        and overflow_after.row_version == overflow_before.row_version
        and overflow_cursor_after.row_version == overflow_cursor_before.row_version
    )
    if not expected:
        return 503, {
            "ok": False,
            "stage": "assertion",
            "ready_state": ready_after.bootstrap_state.value,
            "ready_backfill": ready_cursor_after.backfill_state.value,
            "ready_history": ready_cursor_after.gmail_history_id,
            "ready_read_status": read_after.status,
            "ready_projected_count": read_after.projected_count,
            "overflow_status": overflow.status,
            "overflow_state": overflow_after.bootstrap_state.value,
            "overflow_backfill": overflow_cursor_after.backfill_state.value,
            "overflow_history": overflow_cursor_after.gmail_history_id,
            "overflow_state_row_unchanged": (
                overflow_after.row_version == overflow_before.row_version
            ),
            "overflow_cursor_row_unchanged": (
                overflow_cursor_after.row_version == overflow_cursor_before.row_version
            ),
        }

    return 200, {
        "ok": True,
        "ready_bootstrap": ready_bootstrap,
        "ready_before_state": ready_before.bootstrap_state.value,
        "ready_before_backfill": ready_cursor_before.backfill_state.value,
        "ready_before_read_status": read_before.status,
        "recovery_status": recovery.status,
        "ready_after_state": ready_after.bootstrap_state.value,
        "ready_after_backfill": ready_cursor_after.backfill_state.value,
        "ready_after_history": ready_cursor_after.gmail_history_id,
        "ready_after_read_status": read_after.status,
        "ready_after_projected_count": read_after.projected_count,
        "overflow_bootstrap": overflow_bootstrap,
        "overflow_status": overflow.status,
        "overflow_after_state": overflow_after.bootstrap_state.value,
        "overflow_after_backfill": overflow_cursor_after.backfill_state.value,
        "overflow_history_unchanged": True,
        "overflow_state_row_unchanged": True,
        "overflow_cursor_row_unchanged": True,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            status, payload = _proof()
        except Exception as error:
            status, payload = 503, {
                "ok": False,
                "stage": "exception",
                "exception_type": type(error).__name__,
            }
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
