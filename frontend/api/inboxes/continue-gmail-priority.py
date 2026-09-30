import importlib
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler

from api.inboxes.authenticated_gmail import (
    error_payload,
    read_json_body,
    reject_unknown_fields,
    resolve_authenticated_gmail,
    send_json,
    send_method_not_allowed,
    validate_focus_preferences,
)
from api.priority.mailbox_outbox_consumer import (
    OUTBOX_PRIORITY_MAX_BATCH,
    run_preview_priority_mailbox_outbox_consumer,
    run_production_priority_mailbox_outbox_consumer,
)
from cuevion_mailbox.preview_active_write import (
    gmail_durable_write_enabled,
    preview_active_write_enabled,
)
from cuevion_mailbox.runtime import production_bootstrap_authority_enabled

_fetch_gmail = importlib.import_module("api.inboxes.fetch-gmail")

_PRIORITY_MAINTENANCE_MAX_BATCHES = 5


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            send_method_not_allowed(
                self,
                "Use POST for Gmail Priority maintenance.",
                write_body=getattr(self, "command", "") != "HEAD",
            )
            return
        super().send_error(code, message, explain)

    def do_POST(self):
        payload, request_error = read_json_body(self)
        if request_error:
            send_json(self, 400, request_error)
            return
        field_error = reject_unknown_fields(
            payload,
            {"mailboxId", "focusPreferences"},
        )
        if field_error:
            send_json(self, 400, field_error)
            return
        if not gmail_durable_write_enabled(os.environ):
            send_json(
                self,
                404,
                error_payload(
                    "not_found",
                    "Gmail Priority maintenance is unavailable.",
                ),
            )
            return

        focus_preferences = None
        if "focusPreferences" in payload:
            focus_preferences, focus_error = validate_focus_preferences(
                payload.get("focusPreferences")
            )
            if focus_error:
                send_json(self, 400, focus_error)
                return

        resolution = resolve_authenticated_gmail(
            self.headers,
            payload.get("mailboxId"),
            include_member_authority=True,
        )
        if resolution["status"] != "ok":
            send_json(self, resolution["status_code"], resolution["error"])
            return

        context = resolution["context"]
        member = resolution.get("memberAuthority")
        consumer = (
            run_production_priority_mailbox_outbox_consumer
            if production_bootstrap_authority_enabled(os.environ)
            else run_preview_priority_mailbox_outbox_consumer
            if preview_active_write_enabled(os.environ)
            else None
        )
        if consumer is None:
            send_json(
                self,
                404,
                error_payload(
                    "not_found",
                    "Gmail Priority maintenance is unavailable.",
                ),
            )
            return

        total_claimed = 0
        total_processed = 0
        total_retried = 0
        complete = False
        batches = 0
        try:
            for batch_index in range(_PRIORITY_MAINTENANCE_MAX_BATCHES):
                report = consumer(
                    environment=os.environ,
                    workspace_id=getattr(member, "workspace_id"),
                    owner_user_id=getattr(member, "user_id"),
                    mailbox_id=context["mailbox_id"],
                    mailbox_account_identity=context["mailbox_email"],
                    now_millis=time.time_ns() // 1_000_000,
                    limit=OUTBOX_PRIORITY_MAX_BATCH,
                    reconcile_current_window=batch_index == 0,
                )
                batches += 1
                total_claimed += report.claimed
                total_processed += report.processed
                total_retried += report.retried
                if report.claimed < OUTBOX_PRIORITY_MAX_BATCH:
                    complete = True
                    break
        except Exception:
            print("cuevion_priority_maintenance outbox_failed")
            send_json(
                self,
                503,
                error_payload(
                    "priority_maintenance_unavailable",
                    "Priority maintenance is temporarily unavailable.",
                ),
            )
            return

        recovery_status = "ok"
        try:
            _fetch_gmail._run_gmail_priority_candidate_recovery(
                member=member,
                context=context,
                focus_preferences=focus_preferences,
            )
        except Exception:
            recovery_status = "failed"
            print("cuevion_priority_maintenance recovery_failed")

        print(
            "cuevion_priority_maintenance complete="
            + ("true" if complete else "false")
            + f" batches={batches}"
            + f" claimed={total_claimed}"
            + f" processed={total_processed}"
            + f" retried={total_retried}"
            + " recovery=" + recovery_status
        )
        send_json(
            self,
            200,
            {
                "ok": True,
                "complete": complete,
                "batches": batches,
                "claimed": total_claimed,
                "processed": total_processed,
                "retried": total_retried,
            },
        )

    def do_GET(self):
        send_method_not_allowed(self, "Use POST for Gmail Priority maintenance.")

    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET

    def do_HEAD(self):
        send_method_not_allowed(
            self,
            "Use POST for Gmail Priority maintenance.",
            write_body=False,
        )

    def log_message(self, format, *args):
        return
