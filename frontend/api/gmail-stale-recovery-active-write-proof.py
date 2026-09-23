"""TEMPORARY Preview-only real PostgreSQL proof for stale Gmail History recovery."""

import json
import os
from http.server import BaseHTTPRequestHandler

from cuevion_mailbox.gmail_history import read_gmail_account_history
from cuevion_mailbox.preview_active_write import (
    PreviewGmailHistoryRecovery,
    preview_active_write_enabled,
    run_preview_gmail_durable_write,
    run_preview_gmail_history_sync,
    run_preview_gmail_stale_recovery,
)
from cuevion_mailbox.repository_contract import (
    MailboxProvider,
    MailboxReadAuthority,
)
from cuevion_mailbox.runtime import build_active_write_mailbox_repositories


_WORKSPACE_ID = "wsp_l44kMFQRDa7J3askwYxxbQ"
_OWNER_USER_ID = "usr_jDkwYBEn-6jawBwY_-Pzpg"
_MAILBOX_ID = "preview-gmail-stale-recovery-route-proof"
_MAILBOX_IDENTITY = "preview-gmail-stale-recovery@example.invalid"

_CHANGED_ID = "preview-stale-message-changed"
_RESURRECTED_ID = "preview-stale-message-resurrected"
_ABSENT_ID = "preview-stale-message-absent"
_TERMINAL_ID = "preview-stale-message-terminal"

_INITIAL_HISTORY_ID = "3000"
_PREPARED_HISTORY_ID = "3010"
_FRESH_HISTORY_ID = "4000"
_OVERFLOW_HISTORY_ID = "5000"


def _preview(message_id: str, subject: str) -> dict:
    return {
        "providerMessageId": message_id,
        "providerThreadId": "thread-" + message_id,
        "providerFolder": "Inbox",
        "labelIds": ["INBOX", "UNREAD"],
        "rfcMessageId": message_id + "@example.invalid",
        "to": "Proof <preview-gmail-stale-recovery@example.invalid>",
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
        "providerTimestampMillis": "1790180000000",
        "senderDisplay": "Proof Sender",
        "senderAddress": "proof-sender@example.invalid",
        "subject": subject,
        "snippet": "Synthetic Preview stale Gmail recovery proof",
        "unread": True,
        "flagged": False,
    }


def _context() -> dict:
    return {
        "mailbox_email": _MAILBOX_IDENTITY,
        "mailbox_id": _MAILBOX_ID,
        "refresh_attempted": False,
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


def _recover_setup_tombstone(context, provider_message_id):
    if provider_message_id == _RESURRECTED_ID:
        return PreviewGmailHistoryRecovery(
            "terminal_absent",
            {**context, "proof_setup_tombstone": True},
        )
    return PreviewGmailHistoryRecovery("retry", context)


def _recover_stale(context, provider_message_id):
    if provider_message_id == _CHANGED_ID:
        return PreviewGmailHistoryRecovery(
            "recovered",
            {**context, "proof_changed_recovered": True},
            _preview(_CHANGED_ID, "Changed subject after stale recovery"),
            _source(_CHANGED_ID, "Changed subject after stale recovery"),
        )
    if provider_message_id == _RESURRECTED_ID:
        return PreviewGmailHistoryRecovery(
            "recovered",
            {**context, "proof_resurrected_recovered": True},
            _preview(_RESURRECTED_ID, "Resurrected subject"),
            _source(_RESURRECTED_ID, "Resurrected subject"),
        )
    if provider_message_id == _TERMINAL_ID:
        return PreviewGmailHistoryRecovery(
            "terminal_absent",
            {**context, "proof_terminal_absent": True},
        )
    return PreviewGmailHistoryRecovery("retry", context)


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
                _preview(_CHANGED_ID, "Old changed subject"),
                _preview(_RESURRECTED_ID, "Original resurrected subject"),
                _preview(_ABSENT_ID, "Absent subject"),
                _preview(_TERMINAL_ID, "Terminal subject"),
            ],
            candidate_sources=[
                _source(_CHANGED_ID, "Old changed subject"),
                _source(_RESURRECTED_ID, "Original resurrected subject"),
                _source(_ABSENT_ID, "Absent subject"),
                _source(_TERMINAL_ID, "Terminal subject"),
            ],
            gmail_history_id=_INITIAL_HISTORY_ID,
            committed_at_millis=1790180000000,
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

    setup_status = "already_prepared"
    if cursor.gmail_history_id == _INITIAL_HISTORY_ID:
        def setup_history_request(context, path):
            if "startHistoryId=" + _INITIAL_HISTORY_ID not in path:
                return None, {"code": "unexpected_history_path"}, context, None
            return (
                {
                    "historyId": _PREPARED_HISTORY_ID,
                    "history": [
                        {
                            "id": "3008",
                            "messages": [{"id": _RESURRECTED_ID}],
                        }
                    ],
                },
                None,
                {**context, "proof_setup_history_seen": True},
                None,
            )

        setup = run_preview_gmail_history_sync(
            environment=os.environ,
            workspace_id=_WORKSPACE_ID,
            owner_user_id=_OWNER_USER_ID,
            mailbox_id=_MAILBOX_ID,
            mailbox_account_identity=_MAILBOX_IDENTITY,
            context=_context(),
            request_with_one_refresh=setup_history_request,
            recover_exact_message=_recover_setup_tombstone,
            committed_at_millis=1790180010000,
            repositories=repositories,
        )
        setup_status = setup.status
        if (
            setup.status != "applied"
            or setup.mutation_count != 1
            or setup.next_history_id != _PREPARED_HISTORY_ID
        ):
            return 503, {
                "ok": False,
                "stage": "setup_history",
                "status": setup.status,
                "mutation_count": setup.mutation_count,
                "next_history_id": setup.next_history_id,
            }
    elif cursor.gmail_history_id not in {
        _PREPARED_HISTORY_ID,
        _FRESH_HISTORY_ID,
    }:
        return 503, {
            "ok": False,
            "stage": "unexpected_setup_cursor",
            "cursor_history_id": cursor.gmail_history_id,
        }

    state = repositories.reader.resolve_current_state(authority)
    if state is None:
        return 503, {"ok": False, "stage": "prepared_state_missing"}
    cursor = repositories.reader.read_cursor(state.scope, "gmail-account")
    if cursor is None:
        return 503, {"ok": False, "stage": "prepared_cursor_missing"}

    stale_status = "already_recovered"
    recovery_status = "already_applied"
    recovery_mutations = 0

    if cursor.gmail_history_id == _PREPARED_HISTORY_ID:
        def stale_history_request(context, path):
            if "startHistoryId=" + _PREPARED_HISTORY_ID not in path:
                return None, {"code": "unexpected_stale_history_path"}, context, None
            return (
                None,
                {"code": "gmail_message_not_found"},
                {**context, "proof_stale_seen": True},
                None,
            )

        stale = run_preview_gmail_history_sync(
            environment=os.environ,
            workspace_id=_WORKSPACE_ID,
            owner_user_id=_OWNER_USER_ID,
            mailbox_id=_MAILBOX_ID,
            mailbox_account_identity=_MAILBOX_IDENTITY,
            context=_context(),
            request_with_one_refresh=stale_history_request,
            recover_exact_message=lambda *_args: (_ for _ in ()).throw(
                AssertionError("stale History must not exact-recover")
            ),
            committed_at_millis=1790180020000,
            repositories=repositories,
        )
        stale_status = stale.status
        if stale.status != "full_sync_required":
            return 503, {
                "ok": False,
                "stage": "stale_detection",
                "status": stale.status,
            }

        def profile_request(context, path):
            if path != "/profile":
                return None, {"code": "unexpected_profile_path"}, context, None
            return (
                {
                    "emailAddress": _MAILBOX_IDENTITY,
                    "historyId": _FRESH_HISTORY_ID,
                },
                None,
                {**context, "proof_profile_seen": True},
                None,
            )

        fresh = read_gmail_account_history(
            stale.context,
            request_with_one_refresh=profile_request,
        )
        if fresh.status != "ok" or fresh.history_id != _FRESH_HISTORY_ID:
            return 503, {
                "ok": False,
                "stage": "fresh_profile",
                "status": fresh.status,
                "history_id": fresh.history_id,
            }

        def inventory_request(context, path):
            if path != "/messages?labelIds=INBOX&maxResults=100":
                return None, {"code": "unexpected_inventory_path"}, context, None
            return (
                {
                    "messages": [
                        {"id": _CHANGED_ID},
                        {"id": _RESURRECTED_ID},
                        {"id": _TERMINAL_ID},
                    ]
                },
                None,
                {**context, "proof_inventory_seen": True},
                None,
            )

        recovery = run_preview_gmail_stale_recovery(
            environment=os.environ,
            workspace_id=_WORKSPACE_ID,
            owner_user_id=_OWNER_USER_ID,
            mailbox_id=_MAILBOX_ID,
            mailbox_account_identity=_MAILBOX_IDENTITY,
            context=fresh.context,
            fresh_history_id=fresh.history_id,
            request_with_one_refresh=inventory_request,
            recover_exact_message=_recover_stale,
            committed_at_millis=1790180030000,
            repositories=repositories,
        )
        recovery_status = recovery.status
        recovery_mutations = recovery.mutation_count
        if (
            recovery.status != "applied"
            or recovery.mutation_count != 4
            or recovery.next_history_id != _FRESH_HISTORY_ID
            or recovery.provider_count != 3
        ):
            return 503, {
                "ok": False,
                "stage": "stale_recovery",
                "status": recovery.status,
                "mutation_count": recovery.mutation_count,
                "next_history_id": recovery.next_history_id,
                "provider_count": recovery.provider_count,
            }

    state = repositories.reader.resolve_current_state(authority)
    if state is None:
        return 503, {"ok": False, "stage": "verify_state_missing"}
    cursor = repositories.reader.read_cursor(state.scope, "gmail-account")
    projections = repositories.reader.read_messages_by_provider_message_ids(
        state.scope,
        [
            _CHANGED_ID,
            _RESURRECTED_ID,
            _ABSENT_ID,
            _TERMINAL_ID,
        ],
    )
    by_provider_id = {
        projection.identity.provider_message_id: projection
        for projection in projections
    }
    changed = by_provider_id.get(_CHANGED_ID)
    resurrected = by_provider_id.get(_RESURRECTED_ID)
    absent = by_provider_id.get(_ABSENT_ID)
    terminal = by_provider_id.get(_TERMINAL_ID)
    outbox_before_overflow = _outbox_summary(repositories)

    expected_recovery = (
        cursor is not None
        and cursor.gmail_history_id == _FRESH_HISTORY_ID
        and cursor.row_version == 3
        and state.row_version == 4
        and len(projections) == 4
        and changed is not None
        and changed.provider_deleted is False
        and changed.row_version == 2
        and resurrected is not None
        and resurrected.provider_deleted is False
        and resurrected.row_version == 3
        and absent is not None
        and absent.provider_deleted is True
        and absent.row_version == 2
        and terminal is not None
        and terminal.provider_deleted is True
        and terminal.row_version == 2
        and outbox_before_overflow == (9, 9, 0, 4, 2, 3)
    )
    if not expected_recovery:
        return 503, {
            "ok": False,
            "stage": "recovery_verification",
            "cursor_history_id": None if cursor is None else cursor.gmail_history_id,
            "cursor_row_version": None if cursor is None else cursor.row_version,
            "state_row_version": state.row_version,
            "projection_count": len(projections),
            "changed": None if changed is None else [changed.provider_deleted, changed.row_version],
            "resurrected": None if resurrected is None else [resurrected.provider_deleted, resurrected.row_version],
            "absent": None if absent is None else [absent.provider_deleted, absent.row_version],
            "terminal": None if terminal is None else [terminal.provider_deleted, terminal.row_version],
            "outbox": list(outbox_before_overflow),
        }

    overflow = run_preview_gmail_stale_recovery(
        environment=os.environ,
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=_MAILBOX_ID,
        mailbox_account_identity=_MAILBOX_IDENTITY,
        context=_context(),
        fresh_history_id=_OVERFLOW_HISTORY_ID,
        request_with_one_refresh=lambda context, _path: (
            {
                "messages": [{"id": _CHANGED_ID}],
                "nextPageToken": "page-2",
            },
            None,
            {**context, "proof_overflow_seen": True},
            None,
        ),
        recover_exact_message=lambda *_args: (_ for _ in ()).throw(
            AssertionError("overflow must not exact-recover")
        ),
        committed_at_millis=1790180040000,
        repositories=repositories,
    )

    state_after_overflow = repositories.reader.resolve_current_state(authority)
    if state_after_overflow is None:
        return 503, {"ok": False, "stage": "overflow_state_missing"}
    cursor_after_overflow = repositories.reader.read_cursor(
        state_after_overflow.scope,
        "gmail-account",
    )
    outbox_after_overflow = _outbox_summary(repositories)

    expected_overflow = (
        overflow.status == "provider_overflow"
        and overflow.mutation_count == 0
        and overflow.next_history_id is None
        and cursor_after_overflow is not None
        and cursor_after_overflow.gmail_history_id == _FRESH_HISTORY_ID
        and cursor_after_overflow.row_version == 3
        and state_after_overflow.row_version == 4
        and outbox_after_overflow == outbox_before_overflow
    )
    if not expected_overflow:
        return 503, {
            "ok": False,
            "stage": "overflow_verification",
            "overflow_status": overflow.status,
            "cursor_history_id": (
                None
                if cursor_after_overflow is None
                else cursor_after_overflow.gmail_history_id
            ),
            "cursor_row_version": (
                None
                if cursor_after_overflow is None
                else cursor_after_overflow.row_version
            ),
            "state_row_version": state_after_overflow.row_version,
            "outbox_before": list(outbox_before_overflow),
            "outbox_after": list(outbox_after_overflow),
        }

    return 200, {
        "ok": True,
        "bootstrap_status": bootstrap_status,
        "setup_status": setup_status,
        "stale_status": stale_status,
        "recovery_status": recovery_status,
        "recovery_mutations": recovery_mutations,
        "cursor_history_id": cursor_after_overflow.gmail_history_id,
        "cursor_row_version": cursor_after_overflow.row_version,
        "state_row_version": state_after_overflow.row_version,
        "projection_count": len(projections),
        "changed_row_version": changed.row_version,
        "resurrected_provider_deleted": resurrected.provider_deleted,
        "resurrected_row_version": resurrected.row_version,
        "absent_provider_deleted": absent.provider_deleted,
        "absent_row_version": absent.row_version,
        "terminal_provider_deleted": terminal.provider_deleted,
        "terminal_row_version": terminal.row_version,
        "outbox_count": outbox_after_overflow[0],
        "outbox_unprocessed": outbox_after_overflow[1],
        "outbox_attempt_sum": outbox_after_overflow[2],
        "outbox_message_added": outbox_after_overflow[3],
        "outbox_message_changed": outbox_after_overflow[4],
        "outbox_message_deleted": outbox_after_overflow[5],
        "overflow_status": overflow.status,
        "overflow_cursor_unchanged": True,
        "overflow_state_unchanged": True,
        "overflow_outbox_unchanged": True,
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
