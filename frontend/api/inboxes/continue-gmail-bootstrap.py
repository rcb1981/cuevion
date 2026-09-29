import importlib
import os
import time
from email import message_from_bytes
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
from api.inboxes.gmail_snapshot import recover_exact_gmail_inbox_message
from cuevion_mailbox.preview_active_write import (
    PreviewGmailHistoryRecovery,
    run_production_gmail_bootstrap_page,
)
from cuevion_mailbox.runtime import production_bootstrap_authority_enabled

_fetch_gmail = importlib.import_module("api.inboxes.fetch-gmail")


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            send_method_not_allowed(
                self,
                "Use POST for Gmail bootstrap continuation.",
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
        if not production_bootstrap_authority_enabled(os.environ):
            send_json(
                self,
                404,
                error_payload("not_found", "Bootstrap continuation is unavailable."),
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

        def recover(recovery_context, provider_message_id):
            recovered = recover_exact_gmail_inbox_message(
                recovery_context,
                provider_message_id=provider_message_id,
                request_with_one_refresh=_fetch_gmail._request_with_one_refresh,
                focus_preferences=focus_preferences,
                require_inbound_semantics=False,
                message_parser=message_from_bytes,
            )
            return PreviewGmailHistoryRecovery(
                recovered.result.value,
                recovered.context,
                recovered.preview,
                recovered.candidate_source,
            )

        try:
            result = run_production_gmail_bootstrap_page(
                environment=os.environ,
                workspace_id=getattr(member, "workspace_id"),
                owner_user_id=getattr(member, "user_id"),
                mailbox_id=context["mailbox_id"],
                mailbox_account_identity=context["mailbox_email"],
                context=context,
                request_with_one_refresh=_fetch_gmail._request_with_one_refresh,
                recover_exact_message=recover,
                committed_at_millis=time.time_ns() // 1_000_000,
                max_pages=5,
            )
        except Exception:
            send_json(
                self,
                503,
                error_payload(
                    "bootstrap_continuation_unavailable",
                    "Mailbox bootstrap continuation is temporarily unavailable.",
                ),
            )
            return

        print(
            "cuevion_mailbox_active_write gmail bootstrap_continuation_"
            + result.status
        )
        send_json(
            self,
            200,
            {
                "ok": True,
                "status": result.status,
                "providerCount": result.provider_count,
                "mutationCount": result.mutation_count,
                "complete": result.next_history_id is not None,
            },
        )

    def do_GET(self):
        send_method_not_allowed(self, "Use POST for Gmail bootstrap continuation.")

    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET

    def do_HEAD(self):
        send_method_not_allowed(
            self,
            "Use POST for Gmail bootstrap continuation.",
            write_body=False,
        )

    def log_message(self, format, *args):
        return
