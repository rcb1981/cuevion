import base64
import binascii
import json
import sys
from email import message_from_bytes
from email.errors import MessageError
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from authenticated_gmail import (  # noqa: E402
    MAX_GMAIL_RESPONSE_BYTES,
    error_payload,
    gmail_http_error_code,
    read_bounded_response,
    read_json_body,
    refresh_gmail_context,
    reject_unknown_fields,
    resolve_authenticated_gmail,
    send_json,
    send_method_not_allowed,
    validate_focus_preferences,
    valid_identifier,
)

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_FETCH_LIMIT = 50
MAX_FETCH_LIMIT = 100
def _validate_focus_preferences(value: object) -> tuple[dict | None, dict | None]:
    return validate_focus_preferences(value)


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(
        f"{value}{padding}".encode("ascii"),
        altchars=b"-_",
        validate=True,
    )


def _gmail_request(access_token: str, path: str) -> tuple[dict | None, dict | None]:
    request = Request(
        f"{GMAIL_API_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        method="GET",
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
        return None, {"code": gmail_http_error_code(error.code, "gmail_fetch_failed")}
    except (URLError, TimeoutError):
        return None, {"code": "gmail_unavailable"}


def _send_gmail_error(handler, error: dict):
    code = error.get("code")
    mapping = {
        "gmail_token_invalid": (401, "reconnect_required", "Reconnect this Gmail inbox to continue."),
        "gmail_permission_denied": (403, "gmail_permission_denied", "Gmail did not permit this operation."),
        "gmail_rate_limited": (502, "gmail_rate_limited", "Gmail is temporarily rate limited."),
        "gmail_unavailable": (502, "gmail_unavailable", "Gmail is temporarily unavailable."),
        "gmail_response_invalid": (502, "gmail_response_invalid", "Gmail returned an invalid response."),
        "gmail_response_too_large": (502, "gmail_response_too_large", "Gmail returned a response that is too large."),
    }
    status, safe_code, message = mapping.get(
        code,
        (502, "gmail_fetch_failed", "Gmail inbox could not be loaded."),
    )
    send_json(handler, status, error_payload(safe_code, message))


def _request_with_one_refresh(context: dict, path: str):
    payload, error = _gmail_request(context["access_token"], path)
    if error and error.get("code") == "gmail_token_invalid" and not context["refresh_attempted"]:
        refreshed = refresh_gmail_context(context)
        if refreshed["status"] != "ok":
            return None, error, context, refreshed
        context = refreshed["context"]
        payload, error = _gmail_request(context["access_token"], path)
    return payload, error, context, None


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            send_method_not_allowed(
                self,
                "Use POST for Gmail mailbox fetch.",
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
                error_payload("internal_error", "The Gmail request could not be completed."),
            )

    def _handle_post(self):
        payload, request_error = read_json_body(self)
        if request_error:
            send_json(self, 400 if request_error["error"]["code"] != "request_too_large" else 413, request_error)
            return
        field_error = reject_unknown_fields(payload, {"mailboxId", "focusPreferences", "limit"})
        if field_error:
            send_json(self, 400, field_error)
            return

        limit_value = payload.get("limit", DEFAULT_FETCH_LIMIT)
        if limit_value is None:
            limit_value = DEFAULT_FETCH_LIMIT
        if not isinstance(limit_value, int) or isinstance(limit_value, bool):
            send_json(self, 400, error_payload("invalid_request", "Fetch limit must be an integer."))
            return
        limit = max(1, min(limit_value, MAX_FETCH_LIMIT))

        focus_preferences = None
        if "focusPreferences" in payload:
            focus_preferences, focus_error = _validate_focus_preferences(
                payload.get("focusPreferences")
            )
            if focus_error:
                send_json(self, 400, focus_error)
                return

        resolution = resolve_authenticated_gmail(self.headers, payload.get("mailboxId"))
        if resolution["status"] != "ok":
            send_json(self, resolution["status_code"], resolution["error"])
            return
        context = resolution["context"]

        list_path = f"/messages?{urlencode({'labelIds': 'INBOX', 'maxResults': limit})}"
        list_payload, list_error, context, refresh_failure = _request_with_one_refresh(context, list_path)
        if refresh_failure:
            send_json(self, refresh_failure["status_code"], refresh_failure["error"])
            return
        if list_error:
            _send_gmail_error(self, list_error)
            return

        message_refs = list_payload.get("messages") if isinstance(list_payload, dict) else None
        if not isinstance(message_refs, list):
            message_refs = []
        message_refs = message_refs[:MAX_FETCH_LIMIT]

        from imap_connect_preview import to_message_preview

        previews = []
        inbox_uid_set: list[str] = []
        result_bytes = 0
        for index, message_ref in enumerate(message_refs):
            message_id = (message_ref or {}).get("id") if isinstance(message_ref, dict) else None
            if not valid_identifier(message_id):
                continue
            message_payload, message_error, context, refresh_failure = _request_with_one_refresh(
                context,
                f"/messages/{quote(message_id, safe='')}?format=raw",
            )
            if refresh_failure:
                send_json(self, refresh_failure["status_code"], refresh_failure["error"])
                return
            if message_error:
                _send_gmail_error(self, message_error)
                return
            raw_message = message_payload.get("raw") if isinstance(message_payload, dict) else None
            if not isinstance(raw_message, str) or not raw_message:
                continue
            try:
                decoded_message = _base64url_decode(raw_message)
            except (binascii.Error, UnicodeEncodeError):
                continue
            try:
                parsed_message = message_from_bytes(decoded_message)
            except MessageError:
                continue
            label_ids = message_payload.get("labelIds") if isinstance(message_payload, dict) else None
            unread = isinstance(label_ids, list) and "UNREAD" in label_ids
            flagged = isinstance(label_ids, list) and "STARRED" in label_ids
            gmail_internal_id = str(message_payload.get("id") or "").strip() or None
            preview = to_message_preview(
                parsed_message,
                index,
                context["mailbox_email"],
                unread,
                gmail_internal_id,
                flagged,
                internal_role=None,
                focus_preferences=focus_preferences,
            )
            gmail_thread_id = message_payload.get("threadId")
            if valid_identifier(gmail_thread_id):
                preview["providerThreadId"] = gmail_thread_id
            preview_size = len(json.dumps(preview).encode("utf-8"))
            if result_bytes + preview_size > MAX_GMAIL_RESPONSE_BYTES:
                break
            result_bytes += preview_size
            previews.append(preview)
            if gmail_internal_id:
                inbox_uid_set.append(gmail_internal_id)

        send_json(
            self,
            200,
            {"ok": True, "messages": previews, "inboxUidSet": inbox_uid_set, "uidValidity": "gmail-api"},
        )

    def do_GET(self):
        send_method_not_allowed(self, "Use POST for Gmail mailbox fetch.")

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        send_method_not_allowed(self, "Use POST for Gmail mailbox fetch.", write_body=False)

    def do_OPTIONS(self):
        send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
