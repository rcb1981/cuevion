"""TEMPORARY Preview proof for Gmail cache-authoritative reads.

active_write mode seeds two isolated synthetic mailboxes:
- one promoted through complete stale recovery to ready/complete;
- one left recent_ready/not_started as a negative control.

active_read mode proves the ready mailbox becomes durable membership authority
only with matching provider history before/after exact detail rendering, while
history mismatch and non-ready state remain provider-authoritative.
"""

import base64
import json
import os
from http.server import BaseHTTPRequestHandler

from api.inboxes.gmail_snapshot import read_gmail_folder_snapshot
from cuevion_mailbox.gmail_history import read_gmail_account_history
from cuevion_mailbox.preview_active_read import (
    plan_preview_gmail_authoritative_read,
    preview_active_read_enabled,
)
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
from cuevion_mailbox.runtime import (
    build_active_read_mailbox_reader,
    build_active_write_mailbox_repositories,
)


_WORKSPACE_ID = "wsp_l44kMFQRDa7J3askwYxxbQ"
_OWNER_USER_ID = "usr_jDkwYBEn-6jawBwY_-Pzpg"
_IDENTITY = "preview-cache-authority@example.invalid"

_READY_MAILBOX = "preview-gmail-cache-authority-ready-proof"
_RECENT_MAILBOX = "preview-gmail-cache-authority-recent-proof"

_READY_MESSAGE = "preview-cache-authority-ready-message"
_RECENT_MESSAGE = "preview-cache-authority-recent-message"

_READY_INITIAL_HISTORY = "8100"
_READY_CURRENT_HISTORY = "8110"
_READY_MISMATCH_HISTORY = "8111"
_RECENT_HISTORY = "8200"

_NOW = 1_790_277_000_000


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
        "providerTimestampMillis": "1790277000000",
        "rfcDate": "Thu, 24 Sep 2026 20:30:00 +0200",
        "senderDisplay": "Proof Sender",
        "senderAddress": "proof-sender@example.invalid",
        "subject": subject,
        "snippet": "Synthetic cache authority proof",
        "unread": True,
        "flagged": False,
    }


def _detail(message_id: str, subject: str) -> dict:
    raw = (
        "Message-Id: <" + message_id + "@example.invalid>\r\n"
        "Date: Thu, 24 Sep 2026 20:30:00 +0200\r\n"
        "From: Proof Sender <proof-sender@example.invalid>\r\n"
        "To: Proof <" + _IDENTITY + ">\r\n"
        "Subject: " + subject + "\r\n"
        "\r\n"
        "Synthetic cache authority proof body"
    ).encode("utf-8")
    return {
        "id": message_id,
        "threadId": "thread-" + message_id,
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": "1790277000000",
        "raw": base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
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
        raise RuntimeError("cache authority proof bootstrap failed")
    return result.status


def _read_state(repositories, mailbox_id: str):
    state = repositories.reader.resolve_current_state(_authority(mailbox_id))
    if state is None:
        raise RuntimeError("cache authority proof state missing")
    cursor = repositories.reader.read_cursor(state.scope, "gmail-account")
    if cursor is None:
        raise RuntimeError("cache authority proof cursor missing")
    return state, cursor


def _recover_ready(context, provider_message_id):
    if provider_message_id != _READY_MESSAGE:
        raise RuntimeError("unexpected cache authority proof message")
    return PreviewGmailHistoryRecovery(
        "recovered",
        context,
        _preview(_READY_MESSAGE, "Ready cache message"),
        _source(_READY_MESSAGE, "Ready cache message"),
    )


def _seed():
    repositories = build_active_write_mailbox_repositories(os.environ)

    ready_bootstrap = _bootstrap(
        repositories,
        mailbox_id=_READY_MAILBOX,
        message_id=_READY_MESSAGE,
        history_id=_READY_INITIAL_HISTORY,
        subject="Ready cache message",
    )
    recent_bootstrap = _bootstrap(
        repositories,
        mailbox_id=_RECENT_MAILBOX,
        message_id=_RECENT_MESSAGE,
        history_id=_RECENT_HISTORY,
        subject="Recent cache message",
    )

    ready_before, ready_cursor_before = _read_state(
        repositories,
        _READY_MAILBOX,
    )
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
        fresh_history_id=_READY_CURRENT_HISTORY,
        request_with_one_refresh=lambda context, path: (
            {"messages": [{"id": _READY_MESSAGE}]}
            if path == "/messages?labelIds=INBOX&maxResults=100"
            else None,
            None
            if path == "/messages?labelIds=INBOX&maxResults=100"
            else {"code": "unexpected_path"},
            context,
            None,
        ),
        recover_exact_message=_recover_ready,
        committed_at_millis=_NOW + 1_000,
        repositories=repositories,
    )
    ready_after, ready_cursor_after = _read_state(
        repositories,
        _READY_MAILBOX,
    )
    recent_after, recent_cursor_after = _read_state(
        repositories,
        _RECENT_MAILBOX,
    )

    ok = (
        ready_before.bootstrap_state is BootstrapState.RECENT_READY
        and ready_cursor_before.backfill_state is BackfillState.NOT_STARTED
        and recovery.status in {"applied", "unchanged"}
        and ready_after.bootstrap_state is BootstrapState.READY
        and ready_cursor_after.backfill_state is BackfillState.COMPLETE
        and ready_cursor_after.backfill_cursor is None
        and ready_cursor_after.gmail_history_id == _READY_CURRENT_HISTORY
        and recent_after.bootstrap_state is BootstrapState.RECENT_READY
        and recent_cursor_after.backfill_state is BackfillState.NOT_STARTED
        and recent_cursor_after.gmail_history_id == _RECENT_HISTORY
    )
    if not ok:
        return 503, {
            "ok": False,
            "mode": "seed",
            "stage": "assertion",
            "ready_before_state": ready_before.bootstrap_state.value,
            "ready_before_backfill": ready_cursor_before.backfill_state.value,
            "recovery_status": recovery.status,
            "ready_after_state": ready_after.bootstrap_state.value,
            "ready_after_backfill": ready_cursor_after.backfill_state.value,
            "ready_after_history": ready_cursor_after.gmail_history_id,
            "recent_state": recent_after.bootstrap_state.value,
            "recent_backfill": recent_cursor_after.backfill_state.value,
            "recent_history": recent_cursor_after.gmail_history_id,
        }

    return 200, {
        "ok": True,
        "mode": "seed",
        "ready_bootstrap": ready_bootstrap,
        "recent_bootstrap": recent_bootstrap,
        "recovery_status": recovery.status,
        "ready_state": ready_after.bootstrap_state.value,
        "ready_backfill": ready_cursor_after.backfill_state.value,
        "ready_history": ready_cursor_after.gmail_history_id,
        "recent_state": recent_after.bootstrap_state.value,
        "recent_backfill": recent_cursor_after.backfill_state.value,
        "recent_history": recent_cursor_after.gmail_history_id,
    }


def _profile_request(history_id: str, paths: list[str]):
    def request(context, path):
        paths.append(path)
        if path != "/profile":
            return None, {"code": "unexpected_path"}, context, None
        return {
            "emailAddress": _IDENTITY,
            "historyId": history_id,
        }, None, context, None

    return request


def _exact_detail_request(paths: list[str]):
    def request(context, path):
        paths.append(path)
        expected = f"/messages/{_READY_MESSAGE}?format=raw"
        if path != expected:
            return None, {"code": "unexpected_path"}, context, None
        return _detail(_READY_MESSAGE, "Ready cache message"), None, context, None

    return request


def _fallback_request(paths: list[str]):
    def request(context, path):
        paths.append(path)
        if path.startswith("/messages?") and "labelIds=INBOX" in path:
            return {"messages": [{"id": _READY_MESSAGE}]}, None, context, None
        if path == f"/messages/{_READY_MESSAGE}?format=raw":
            return _detail(_READY_MESSAGE, "Ready cache message"), None, context, None
        return None, {"code": "unexpected_path"}, context, None

    return request


def _prove():
    reader = build_active_read_mailbox_reader(os.environ)
    ready_state = reader.resolve_current_state(_authority(_READY_MAILBOX))
    recent_state = reader.resolve_current_state(_authority(_RECENT_MAILBOX))
    if ready_state is None or recent_state is None:
        return 503, {
            "ok": False,
            "mode": "proof",
            "stage": "seed_missing",
        }

    ready_cursor = reader.read_cursor(ready_state.scope, "gmail-account")
    recent_cursor = reader.read_cursor(recent_state.scope, "gmail-account")
    if ready_cursor is None or recent_cursor is None:
        return 503, {
            "ok": False,
            "mode": "proof",
            "stage": "cursor_missing",
        }

    context = {
        "mailbox_id": _READY_MAILBOX,
        "mailbox_email": _IDENTITY,
        "access_token": "synthetic-proof-token",
        "refresh_attempted": False,
    }

    before_paths = []
    before = read_gmail_account_history(
        context,
        request_with_one_refresh=_profile_request(
            _READY_CURRENT_HISTORY,
            before_paths,
        ),
    )
    if before.status != "ok" or before.history_id is None:
        return 503, {
            "ok": False,
            "mode": "proof",
            "stage": "history_before",
            "status": before.status,
        }

    plan = plan_preview_gmail_authoritative_read(
        environment=os.environ,
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=_READY_MAILBOX,
        mailbox_account_identity=_IDENTITY,
        provider_history_id=before.history_id,
        limit=50,
        reader=reader,
    )

    detail_paths = []
    exact = read_gmail_folder_snapshot(
        before.context,
        provider_folder="Inbox",
        request_with_one_refresh=_exact_detail_request(detail_paths),
        limit=50,
        strict=True,
        authoritative_message_ids=plan.provider_message_ids,
    )
    exact_snapshot = exact.get("snapshot")
    if (
        plan.status != "cache_authoritative"
        or plan.provider_message_ids != (_READY_MESSAGE,)
        or exact.get("error") is not None
        or exact.get("refresh_failure") is not None
        or not isinstance(exact_snapshot, dict)
        or len(exact_snapshot.get("messages", [])) != 1
    ):
        return 503, {
            "ok": False,
            "mode": "proof",
            "stage": "cache_exact_details",
            "plan_status": plan.status,
            "provider_message_ids": list(plan.provider_message_ids),
            "detail_paths": detail_paths,
        }

    after_paths = []
    after = read_gmail_account_history(
        exact.get("context", before.context),
        request_with_one_refresh=_profile_request(
            _READY_CURRENT_HISTORY,
            after_paths,
        ),
    )
    history_stable = (
        after.status == "ok"
        and after.history_id == before.history_id
    )

    mismatch = plan_preview_gmail_authoritative_read(
        environment=os.environ,
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=_READY_MAILBOX,
        mailbox_account_identity=_IDENTITY,
        provider_history_id=_READY_MISMATCH_HISTORY,
        limit=50,
        reader=reader,
    )
    fallback_paths = []
    fallback = read_gmail_folder_snapshot(
        context,
        provider_folder="Inbox",
        request_with_one_refresh=_fallback_request(fallback_paths),
        limit=50,
        strict=False,
    )
    fallback_snapshot = fallback.get("snapshot")

    recent_plan = plan_preview_gmail_authoritative_read(
        environment=os.environ,
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=_RECENT_MAILBOX,
        mailbox_account_identity=_IDENTITY,
        provider_history_id=_RECENT_HISTORY,
        limit=50,
        reader=reader,
    )

    exact_skipped_list = (
        all("labelIds=INBOX" not in path for path in detail_paths)
        and detail_paths == [
            f"/messages/{_READY_MESSAGE}?format=raw"
        ]
    )
    fallback_used_list = any(
        "labelIds=INBOX" in path
        for path in fallback_paths
    )
    fallback_ok = (
        fallback.get("error") is None
        and fallback.get("refresh_failure") is None
        and isinstance(fallback_snapshot, dict)
        and len(fallback_snapshot.get("messages", [])) == 1
    )

    ok = (
        ready_state.bootstrap_state is BootstrapState.READY
        and ready_cursor.backfill_state is BackfillState.COMPLETE
        and ready_cursor.backfill_cursor is None
        and ready_cursor.gmail_history_id == _READY_CURRENT_HISTORY
        and before_paths == ["/profile"]
        and exact_skipped_list
        and history_stable
        and after_paths == ["/profile"]
        and mismatch.status == "provider_required"
        and mismatch.provider_message_ids == ()
        and fallback_used_list
        and fallback_ok
        and recent_state.bootstrap_state is BootstrapState.RECENT_READY
        and recent_cursor.backfill_state is BackfillState.NOT_STARTED
        and recent_plan.status == "provider_required"
        and recent_plan.provider_message_ids == ()
    )
    if not ok:
        return 503, {
            "ok": False,
            "mode": "proof",
            "stage": "assertion",
            "ready_state": ready_state.bootstrap_state.value,
            "ready_backfill": ready_cursor.backfill_state.value,
            "ready_history": ready_cursor.gmail_history_id,
            "plan_status": plan.status,
            "history_stable": history_stable,
            "exact_skipped_list": exact_skipped_list,
            "mismatch_status": mismatch.status,
            "fallback_used_list": fallback_used_list,
            "fallback_ok": fallback_ok,
            "recent_state": recent_state.bootstrap_state.value,
            "recent_backfill": recent_cursor.backfill_state.value,
            "recent_plan_status": recent_plan.status,
        }

    return 200, {
        "ok": True,
        "mode": "proof",
        "ready_state": ready_state.bootstrap_state.value,
        "ready_backfill": ready_cursor.backfill_state.value,
        "ready_history": ready_cursor.gmail_history_id,
        "cache_plan": plan.status,
        "cache_provider_message_ids": list(plan.provider_message_ids),
        "history_before": before.history_id,
        "history_after": after.history_id,
        "history_stable": history_stable,
        "exact_detail_count": len(exact_snapshot["messages"]),
        "exact_provider_list_skipped": exact_skipped_list,
        "history_mismatch_plan": mismatch.status,
        "fallback_provider_list_used": fallback_used_list,
        "fallback_message_count": len(fallback_snapshot["messages"]),
        "recent_state": recent_state.bootstrap_state.value,
        "recent_backfill": recent_cursor.backfill_state.value,
        "recent_plan": recent_plan.status,
    }


def _run():
    if preview_active_write_enabled(os.environ):
        return _seed()
    if preview_active_read_enabled(os.environ):
        return _prove()
    return 503, {
        "ok": False,
        "mode": "disabled",
        "stage": "mailbox_mode_disabled",
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            status, payload = _run()
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
