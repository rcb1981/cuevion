import imaplib
import json
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from authenticated_imap import (  # noqa: E402
    find_forbidden_custom_request_fields,
    resolve_authenticated_imap_mailbox,
)
from imap_connect_preview import connect_mailbox_with_settings  # noqa: E402
from authenticated_gmail import (  # noqa: E402
    MAX_GMAIL_RESPONSE_BYTES,
    error_payload,
    gmail_http_error_code,
    read_bounded_response,
    read_json_body,
    refresh_gmail_context,
    reject_unknown_fields,
    resolve_gmail_context,
    resolve_owned_mailbox,
    send_json,
    send_method_not_allowed,
    valid_identifier,
)

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
SUPPORTED_ACTIONS = {"mark_read", "mark_unread", "star", "unstar"}
GMAIL_ACTION_LABELS = {
    "mark_read": {"removeLabelIds": ["UNREAD"]},
    "mark_unread": {"addLabelIds": ["UNREAD"]},
    "star": {"addLabelIds": ["STARRED"]},
    "unstar": {"removeLabelIds": ["STARRED"]},
}
IMAP_ACTION_FLAGS = {
    "mark_read": ("+FLAGS.SILENT", "\\Seen"),
    "mark_unread": ("-FLAGS.SILENT", "\\Seen"),
    "star": ("+FLAGS.SILENT", "\\Flagged"),
    "unstar": ("-FLAGS.SILENT", "\\Flagged"),
}


def _json_response(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _has_unsafe_auth_chars(value: str) -> bool:
    return "\r" in value or "\n" in value


def _token_record_has_known_modify_scope(token_record: dict) -> bool | None:
    scope_value = token_record.get("scope")
    if not isinstance(scope_value, str) or not scope_value.strip():
        return None

    scopes = set(scope_value.split())
    return GMAIL_MODIFY_SCOPE in scopes or "https://mail.google.com/" in scopes


def _gmail_modify_request(
    access_token: str,
    message_id: str,
    action: str,
) -> tuple[dict | None, dict | None]:
    request = Request(
        f"{GMAIL_API_BASE_URL}/messages/{quote(message_id, safe='')}/modify",
        data=json.dumps(GMAIL_ACTION_LABELS[action]).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            body = read_bounded_response(response, MAX_GMAIL_RESPONSE_BYTES)
            if body is None:
                return None, {"code": "gmail_response_too_large"}
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None, {"code": "gmail_response_invalid"}
            if not isinstance(payload, dict):
                return None, {"code": "gmail_response_invalid"}
            return payload, None
    except HTTPError as error:
        return None, {
            "code": gmail_http_error_code(
                error.code,
                "gmail_message_action_failed",
            )
        }
    except (URLError, TimeoutError):
        return None, {"code": "gmail_unavailable"}


def _perform_gmail_action(handler: BaseHTTPRequestHandler, payload: dict, action: str, context: dict):
    message_id = payload.get("messageId")
    if not valid_identifier(message_id):
        send_json(handler, 400, error_payload("invalid_request", "Message id is invalid."))
        return

    _, modify_error = _gmail_modify_request(context["access_token"], message_id, action)
    if modify_error and modify_error.get("code") == "gmail_token_invalid" and not context["refresh_attempted"]:
        refreshed = refresh_gmail_context(context)
        if refreshed["status"] != "ok":
            send_json(handler, refreshed["status_code"], refreshed["error"])
            return
        context = refreshed["context"]
        _, modify_error = _gmail_modify_request(context["access_token"], message_id, action)

    if modify_error:
        code = modify_error.get("code")
        if code == "gmail_token_invalid":
            send_json(handler, 401, error_payload("reconnect_required", "Reconnect this Gmail inbox to continue."))
        elif code == "gmail_permission_denied":
            send_json(handler, 403, error_payload("gmail_permission_denied", "Gmail did not permit this mailbox action."))
        elif code == "gmail_rate_limited":
            send_json(handler, 502, error_payload("gmail_rate_limited", "Gmail is temporarily rate limited."))
        elif code == "gmail_unavailable":
            send_json(handler, 502, error_payload("gmail_unavailable", "Gmail is temporarily unavailable."))
        else:
            send_json(handler, 502, error_payload("gmail_message_action_failed", "Gmail could not update this message."))
        return

    send_json(handler, 200, {"ok": True, "action": action})


def _read_uid_validity(mailbox, folder: str) -> str | None:
    status, data = mailbox.status(folder, "(UIDVALIDITY)")
    if status != "OK" or not data or not data[0]:
        return None

    metadata = data[0].decode("utf-8", errors="ignore") if isinstance(data[0], bytes) else str(data[0])
    match = re.search(r"UIDVALIDITY\s+(\d+)", metadata)
    return match.group(1) if match else None


def _perform_imap_action(handler: BaseHTTPRequestHandler, payload: dict, action: str):
    if set(payload) - {"mailboxId", "folder", "uid", "uidValidity", "action"}:
        _json_response(
            handler,
            400,
            _error("forbidden_connection_fields", "Connection details are not accepted."),
        )
        return
    if find_forbidden_custom_request_fields(payload):
        _json_response(
            handler,
            400,
            _error("forbidden_connection_fields", "Connection details are not accepted."),
        )
        return

    mailbox_id = payload.get("mailboxId")
    folder = payload.get("folder")
    uid = payload.get("uid")
    uid_validity = payload.get("uidValidity")

    if (
        not isinstance(folder, str)
        or not folder
        or folder != folder.strip()
        or "\r" in folder
        or "\n" in folder
    ):
        _json_response(
            handler,
            400,
            _error("missing_folder", "IMAP mailbox actions require the source folder."),
        )
        return

    if not isinstance(uid, str) or not uid.isdigit() or uid == "0":
        _json_response(
            handler,
            400,
            _error("missing_imap_uid", "IMAP mailbox actions require the message UID."),
        )
        return

    if uid_validity is not None and (
        not isinstance(uid_validity, str) or not uid_validity.isdigit()
    ):
        _json_response(
            handler,
            400,
            _error("invalid_request", "IMAP UIDVALIDITY is invalid."),
        )
        return

    resolved = resolve_authenticated_imap_mailbox(handler.headers, mailbox_id)
    if resolved["status"] != "ok" or not resolved["mailbox"]:
        error = resolved["error"] or {
            "code": "mailbox_configuration_malformed",
            "message": "Mailbox configuration is invalid.",
            "status_code": 500,
        }
        _json_response(handler, error["status_code"], _error(error["code"], error["message"]))
        return
    imap = resolved["mailbox"]["imap"]

    mailbox = None
    try:
        mailbox = connect_mailbox_with_settings(
            host=imap["host"],
            port=imap["port"],
            username=imap["username"],
            password=imap["password"],
            ssl_enabled=imap["ssl"],
        )

        select_status, _ = mailbox.select(folder)
        if select_status != "OK":
            _json_response(
                handler,
                404,
                _error("mailbox_not_found", "The source mailbox folder could not be opened."),
            )
            return

        if uid_validity:
            current_uid_validity = _read_uid_validity(mailbox, folder)
            if current_uid_validity and current_uid_validity != uid_validity:
                _json_response(
                    handler,
                    409,
                    _error("uid_validity_changed", "This mailbox changed since the message was fetched."),
                )
                return

        operation, flag = IMAP_ACTION_FLAGS[action]
        status, _ = mailbox.uid("store", uid, operation, f"({flag})")
        if status != "OK":
            _json_response(
                handler,
                404,
                _error("message_action_failed", "The source message could not be updated."),
            )
            return

        _json_response(handler, 200, {"ok": True, "action": action})
    except imaplib.IMAP4.error:
        _json_response(
            handler,
            401,
            _error("invalid_credentials", "Stored IMAP credentials were rejected."),
        )
    except Exception:
        _json_response(
            handler,
            502,
            _error("imap_message_action_failed", "Could not update this message through IMAP."),
        )
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                pass


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            send_method_not_allowed(
                self,
                "Use POST for mailbox message actions.",
                write_body=getattr(self, "command", "") != "HEAD",
            )
            return
        super().send_error(code, message, explain)

    def do_POST(self):
        try:
            handler._handle_post(self)
        except Exception:
            send_json(
                self,
                500,
                error_payload("internal_error", "The mailbox action could not be completed."),
            )

    def _handle_post(self):
        payload, error = read_json_body(self)
        if error:
            send_json(self, 413 if error["error"]["code"] == "request_too_large" else 400, error)
            return

        action = payload.get("action")

        if action not in SUPPORTED_ACTIONS:
            send_json(self, 400, error_payload("unsupported_action", "This mailbox action is not supported."))
            return

        field_error = reject_unknown_fields(
            payload,
            {"mailboxId", "messageId", "folder", "uid", "uidValidity", "action"},
        )
        if field_error or find_forbidden_custom_request_fields(payload):
            send_json(self, 400, error_payload("forbidden_connection_fields", "Connection and identity details are not accepted."))
            return

        owned = resolve_owned_mailbox(self.headers, payload.get("mailboxId"))
        if owned["status"] != "ok":
            send_json(self, owned["status_code"], owned["error"])
            return
        provider = owned["inbox"].get("provider")
        if provider == "google":
            field_error = reject_unknown_fields(payload, {"mailboxId", "messageId", "action"})
            if field_error:
                send_json(self, 400, field_error)
                return
            gmail = resolve_gmail_context(owned)
            if gmail["status"] != "ok":
                send_json(self, gmail["status_code"], gmail["error"])
                return
            _perform_gmail_action(self, payload, action, gmail["context"])
            return
        if provider == "custom_imap":
            _perform_imap_action(self, payload, action)
            return
        send_json(self, 400, error_payload("unsupported_provider", "Mailbox actions require Gmail or IMAP."))

    def do_GET(self):
        send_method_not_allowed(self, "Use POST for mailbox message actions.")

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        send_method_not_allowed(self, "Use POST for mailbox message actions.", write_body=False)

    def do_OPTIONS(self):
        send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
