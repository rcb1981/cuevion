"""TEMPORARY Preview-only real Neon + Priority Redis outbox consumer proof."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler

from api.priority.candidate_projection import (
    PriorityCandidatePopulationAuthority,
    project_priority_candidate,
)
from api.priority.candidate_store import build_runtime_candidate_store
from api.priority.event_reference import resolve_priority_hmac_secret
from api.priority.mailbox_outbox_consumer import (
    consume_priority_mailbox_outbox,
    run_preview_priority_mailbox_outbox_consumer,
)
from api.priority.store import build_runtime_workflow_store
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
_IDENTITY = "preview-priority-outbox@example.invalid"

_PRIMARY_MAILBOX = "preview-priority-outbox-upsert-remove"
_SUPERSEDED_MAILBOX = "preview-priority-outbox-superseded"
_RETRY_MAILBOX = "preview-priority-outbox-retry"
_UNRELATED_MAILBOX = "preview-priority-outbox-unrelated"

_PRIMARY_ID = "preview-priority-message-primary"
_SUPERSEDED_ID = "preview-priority-message-superseded"
_RETRY_ID = "preview-priority-message-retry"
_UNRELATED_ID = "preview-priority-message-unrelated"

_PRIMARY_HISTORY = "6100"
_PRIMARY_DELETE_HISTORY = "6110"
_SUPERSEDED_HISTORY = "6200"
_SUPERSEDED_CHANGE_HISTORY = "6210"
_RETRY_HISTORY = "6300"
_UNRELATED_HISTORY = "6400"

_NOW = 1_790_250_000_000
_PROOF_STAGE = "not_started"


def _set_stage(value: str) -> None:
    global _PROOF_STAGE
    _PROOF_STAGE = value


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
        "providerTimestampMillis": "1790250000000",
        "rfcDate": None,
        "senderDisplay": "Proof Sender",
        "senderAddress": "proof-sender@example.invalid",
        "subject": subject,
        "snippet": "Synthetic Preview mailbox outbox Priority proof",
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


def _priority_authority(mailbox_id: str) -> PriorityCandidatePopulationAuthority:
    return PriorityCandidatePopulationAuthority(
        workspace_id=_WORKSPACE_ID,
        user_id=_OWNER_USER_ID,
        mailbox_id=mailbox_id,
        mailbox_account_identity=_IDENTITY,
        provider="google",
    )


def _bootstrap(
    repositories,
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


def _history_mutation(
    repositories,
    *,
    mailbox_id: str,
    message_id: str,
    start_history_id: str,
    next_history_id: str,
    recovery_status: str,
    subject: str,
    committed_at_millis: int,
) -> str:
    def request(context, path):
        if "startHistoryId=" + start_history_id not in path:
            return None, {"code": "unexpected_history_path"}, context, None
        return (
            {
                "historyId": next_history_id,
                "history": [
                    {
                        "id": next_history_id,
                        "messages": [{"id": message_id}],
                    }
                ],
            },
            None,
            context,
            None,
        )

    def recover(context, provider_message_id):
        if provider_message_id != message_id:
            return PreviewGmailHistoryRecovery("retry", context)
        if recovery_status == "terminal_absent":
            return PreviewGmailHistoryRecovery("terminal_absent", context)
        return PreviewGmailHistoryRecovery(
            "recovered",
            context,
            _preview(message_id, subject),
            _source(message_id, subject),
        )

    result = run_preview_gmail_history_sync(
        environment=os.environ,
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=mailbox_id,
        mailbox_account_identity=_IDENTITY,
        context={"mailbox_id": mailbox_id, "mailbox_email": _IDENTITY},
        request_with_one_refresh=request,
        recover_exact_message=recover,
        committed_at_millis=committed_at_millis,
        repositories=repositories,
    )
    if result.status != "applied" or result.mutation_count != 1:
        raise RuntimeError("proof History mutation failed")
    return result.status


def _outbox_rows(repositories, mailbox_id: str) -> list[tuple]:
    connection = repositories.writer._connection()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                event_type,
                message_row_version,
                attempt_count,
                processed_at IS NOT NULL,
                next_attempt_at IS NOT NULL,
                claim_token IS NOT NULL,
                coalesce(last_error_code, '')
            FROM cuevion_mailbox.mailbox_change_outbox
            WHERE workspace_id = %s
              AND owner_user_id = %s
              AND mailbox_id = %s
            ORDER BY created_at, event_id
            """,
            (_WORKSPACE_ID, _OWNER_USER_ID, mailbox_id),
        )
        rows = cursor.fetchall()
        return [tuple(row) for row in rows]
    finally:
        if cursor is not None:
            cursor.close()
        connection.rollback()
        connection.close()


def _proof():
    if not preview_active_write_enabled(os.environ):
        return 503, {"ok": False, "stage": "active_write_disabled"}

    _set_stage("build_repositories")
    repositories = build_active_write_mailbox_repositories(os.environ)
    _set_stage("resolve_priority_runtime")
    secret = resolve_priority_hmac_secret()
    candidate_store = build_runtime_candidate_store(hmac_secret=secret)
    workflow_store = build_runtime_workflow_store(hmac_secret=secret)

    _set_stage("primary_bootstrap")
    primary_bootstrap = _bootstrap(
        repositories,
        _PRIMARY_MAILBOX,
        _PRIMARY_ID,
        _PRIMARY_HISTORY,
        "Primary initial",
    )
    _set_stage("superseded_bootstrap")
    superseded_bootstrap = _bootstrap(
        repositories,
        _SUPERSEDED_MAILBOX,
        _SUPERSEDED_ID,
        _SUPERSEDED_HISTORY,
        "Superseded initial",
    )
    _set_stage("retry_bootstrap")
    retry_bootstrap = _bootstrap(
        repositories,
        _RETRY_MAILBOX,
        _RETRY_ID,
        _RETRY_HISTORY,
        "Retry initial",
    )
    _set_stage("unrelated_bootstrap")
    unrelated_bootstrap = _bootstrap(
        repositories,
        _UNRELATED_MAILBOX,
        _UNRELATED_ID,
        _UNRELATED_HISTORY,
        "Unrelated initial",
    )

    _set_stage("unrelated_read_before")
    unrelated_before = _outbox_rows(repositories, _UNRELATED_MAILBOX)
    if (
        len(unrelated_before) != 1
        or unrelated_before[0][2] != 0
        or unrelated_before[0][3] is not False
    ):
        return 503, {"ok": False, "stage": "unrelated_setup"}

    primary_authority = _priority_authority(_PRIMARY_MAILBOX)
    primary_source = _source(_PRIMARY_ID, "Primary initial")
    _set_stage("primary_project_candidate")
    primary_scope, _ = project_priority_candidate(
        primary_authority,
        primary_source,
    )

    _set_stage("primary_consume_add")
    primary_first = run_preview_priority_mailbox_outbox_consumer(
        environment=os.environ,
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=_PRIMARY_MAILBOX,
        mailbox_account_identity=_IDENTITY,
        now_millis=_NOW + 1_000,
        repositories=repositories,
        candidate_store=candidate_store,
        workflow_store=workflow_store,
    )
    _set_stage("primary_read_candidate_after_add")
    primary_candidate_after_add = candidate_store.read_candidate(primary_scope)
    if (
        primary_first.claimed != 1
        or primary_first.processed != 1
        or primary_first.retried != 0
        or primary_candidate_after_add is None
    ):
        return 503, {
            "ok": False,
            "stage": "primary_upsert",
            "claimed": primary_first.claimed,
            "processed": primary_first.processed,
            "retried": primary_first.retried,
            "candidate_present": primary_candidate_after_add is not None,
        }

    _set_stage("primary_delete_history")
    primary_delete_status = _history_mutation(
        repositories,
        mailbox_id=_PRIMARY_MAILBOX,
        message_id=_PRIMARY_ID,
        start_history_id=_PRIMARY_HISTORY,
        next_history_id=_PRIMARY_DELETE_HISTORY,
        recovery_status="terminal_absent",
        subject="Primary deleted",
        committed_at_millis=_NOW + 2_000,
    )

    _set_stage("primary_consume_delete")
    primary_second = run_preview_priority_mailbox_outbox_consumer(
        environment=os.environ,
        workspace_id=_WORKSPACE_ID,
        owner_user_id=_OWNER_USER_ID,
        mailbox_id=_PRIMARY_MAILBOX,
        mailbox_account_identity=_IDENTITY,
        now_millis=_NOW + 3_000,
        repositories=repositories,
        candidate_store=candidate_store,
        workflow_store=workflow_store,
    )
    _set_stage("primary_read_candidate_after_delete")
    primary_candidate_after_delete = candidate_store.read_candidate(primary_scope)
    if (
        primary_second.claimed != 1
        or primary_second.processed != 1
        or primary_second.retried != 0
        or primary_candidate_after_delete is not None
    ):
        return 503, {
            "ok": False,
            "stage": "primary_remove",
            "claimed": primary_second.claimed,
            "processed": primary_second.processed,
            "retried": primary_second.retried,
            "candidate_present": primary_candidate_after_delete is not None,
        }

    _set_stage("superseded_change_history")
    superseded_change_status = _history_mutation(
        repositories,
        mailbox_id=_SUPERSEDED_MAILBOX,
        message_id=_SUPERSEDED_ID,
        start_history_id=_SUPERSEDED_HISTORY,
        next_history_id=_SUPERSEDED_CHANGE_HISTORY,
        recovery_status="recovered",
        subject="Superseded changed",
        committed_at_millis=_NOW + 4_000,
    )
    superseded_authority = _priority_authority(_SUPERSEDED_MAILBOX)
    _set_stage("superseded_consume")
    superseded_report = consume_priority_mailbox_outbox(
        superseded_authority,
        reader=repositories.reader,
        writer=repositories.writer,
        apply_action=lambda _action: True,
        now_millis=_NOW + 5_000,
    )
    superseded_counts = dict(superseded_report.result_counts)
    if (
        superseded_report.claimed != 2
        or superseded_report.processed != 2
        or superseded_report.retried != 0
        or superseded_counts.get("superseded") != 1
        or superseded_counts.get("upserted") != 1
    ):
        return 503, {
            "ok": False,
            "stage": "superseded",
            "claimed": superseded_report.claimed,
            "processed": superseded_report.processed,
            "retried": superseded_report.retried,
            "result_counts": list(superseded_report.result_counts),
        }

    retry_authority = _priority_authority(_RETRY_MAILBOX)
    _set_stage("retry_first_consume")
    retry_first = consume_priority_mailbox_outbox(
        retry_authority,
        reader=repositories.reader,
        writer=repositories.writer,
        apply_action=lambda _action: False,
        now_millis=_NOW + 6_000,
    )
    _set_stage("retry_read_after_failure")
    retry_rows_after_failure = _outbox_rows(repositories, _RETRY_MAILBOX)
    if (
        retry_first.claimed != 1
        or retry_first.processed != 0
        or retry_first.retried != 1
        or len(retry_rows_after_failure) != 1
        or retry_rows_after_failure[0][2] != 1
        or retry_rows_after_failure[0][3] is not False
        or retry_rows_after_failure[0][4] is not True
        or retry_rows_after_failure[0][5] is not False
        or retry_rows_after_failure[0][6] != "priority_processing_failed"
    ):
        return 503, {
            "ok": False,
            "stage": "retry_scheduled",
            "report": list(retry_first.result_counts),
            "rows": retry_rows_after_failure,
        }

    _set_stage("retry_second_consume")
    retry_second = consume_priority_mailbox_outbox(
        retry_authority,
        reader=repositories.reader,
        writer=repositories.writer,
        apply_action=lambda _action: True,
        now_millis=_NOW + 20_000,
    )
    _set_stage("retry_read_after_success")
    retry_rows_after_success = _outbox_rows(repositories, _RETRY_MAILBOX)
    if (
        retry_second.claimed != 1
        or retry_second.processed != 1
        or retry_second.retried != 0
        or len(retry_rows_after_success) != 1
        or retry_rows_after_success[0][2] != 2
        or retry_rows_after_success[0][3] is not True
        or retry_rows_after_success[0][5] is not False
        or retry_rows_after_success[0][6] != ""
    ):
        return 503, {
            "ok": False,
            "stage": "retry_recovered",
            "report": list(retry_second.result_counts),
            "rows": retry_rows_after_success,
        }

    _set_stage("unrelated_read_after")
    unrelated_after = _outbox_rows(repositories, _UNRELATED_MAILBOX)
    if unrelated_after != unrelated_before:
        return 503, {
            "ok": False,
            "stage": "unrelated_changed",
            "before": unrelated_before,
            "after": unrelated_after,
        }

    _set_stage("final_outbox_read")
    primary_rows = _outbox_rows(repositories, _PRIMARY_MAILBOX)
    superseded_rows = _outbox_rows(repositories, _SUPERSEDED_MAILBOX)
    if (
        len(primary_rows) != 2
        or any(row[3] is not True for row in primary_rows)
        or len(superseded_rows) != 2
        or any(row[3] is not True for row in superseded_rows)
    ):
        return 503, {"ok": False, "stage": "final_outbox_verification"}

    _set_stage("complete")
    return 200, {
        "ok": True,
        "primary_bootstrap": primary_bootstrap,
        "primary_add_claimed": primary_first.claimed,
        "primary_add_processed": primary_first.processed,
        "primary_candidate_created": True,
        "primary_delete_history": primary_delete_status,
        "primary_delete_claimed": primary_second.claimed,
        "primary_delete_processed": primary_second.processed,
        "primary_candidate_removed": True,
        "superseded_bootstrap": superseded_bootstrap,
        "superseded_change_history": superseded_change_status,
        "superseded_claimed": superseded_report.claimed,
        "superseded_processed": superseded_report.processed,
        "superseded_count": superseded_counts.get("superseded", 0),
        "superseded_latest_upsert_count": superseded_counts.get("upserted", 0),
        "retry_bootstrap": retry_bootstrap,
        "retry_first_claimed": retry_first.claimed,
        "retry_first_retried": retry_first.retried,
        "retry_attempt_after_failure": retry_rows_after_failure[0][2],
        "retry_error_code": retry_rows_after_failure[0][6],
        "retry_second_claimed": retry_second.claimed,
        "retry_second_processed": retry_second.processed,
        "retry_attempt_after_success": retry_rows_after_success[0][2],
        "retry_processed": retry_rows_after_success[0][3],
        "unrelated_bootstrap": unrelated_bootstrap,
        "unrelated_attempt_count": unrelated_after[0][2],
        "unrelated_processed": unrelated_after[0][3],
        "primary_outbox_processed": sum(1 for row in primary_rows if row[3]),
        "superseded_outbox_processed": sum(
            1 for row in superseded_rows if row[3]
        ),
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            status, payload = _proof()
        except Exception as error:
            status, payload = 503, {
                "ok": False,
                "stage": _PROOF_STAGE,
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
