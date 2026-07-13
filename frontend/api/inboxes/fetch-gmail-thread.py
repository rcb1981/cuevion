from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from gmail_thread_parser import GmailThreadParseError, parse_gmail_thread  # noqa: E402
from authenticated_gmail import (  # noqa: E402
    gmail_http_error_code,
    refresh_gmail_context,
    resolve_authenticated_gmail,
)

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_REQUEST_BODY_BYTES = 16 * 1024
MAX_GMAIL_RESPONSE_BYTES = 10 * 1024 * 1024
NETWORK_TIMEOUT_SECONDS = 20
SENSITIVE_REQUEST_FIELDS = {
    "email",
    "provider",
    "authMode",
    "from",
    "username",
    "password",
    "host",
    "port",
    "smtpHost",
    "smtpPort",
    "smtpUsername",
    "smtpPassword",
    "accessToken",
    "access_token",
    "refreshToken",
    "refresh_token",
    "userId",
    "user_id",
    "ownerEmail",
    "owner_email",
}


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _send_json(
    handler: BaseHTTPRequestHandler,
    status_code: int,
    payload: dict,
    *,
    write_body: bool = True,
):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    if write_body:
        handler.wfile.write(response_body)


def _send_method_not_allowed(handler: BaseHTTPRequestHandler, *, write_body: bool = True):
    _send_json(
        handler,
        405,
        _error("method_not_allowed", "Use POST to fetch a Gmail conversation."),
        write_body=write_body,
    )


def _read_json_body(handler: BaseHTTPRequestHandler) -> tuple[dict | None, dict | None]:
    try:
        content_length = int(handler.headers.get("content-length", "0"))
    except (TypeError, ValueError):
        return None, _error("invalid_request", "Content-Length must be valid.")
    if content_length < 0 or content_length > MAX_REQUEST_BODY_BYTES:
        return None, _error("invalid_request", "Request body is too large.")
    raw_body = handler.rfile.read(content_length) if content_length else b""
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _error("invalid_request", "Request body must be valid JSON.")
    if not isinstance(payload, dict):
        return None, _error("invalid_request", "Request body must be a JSON object.")
    if any(field in payload for field in SENSITIVE_REQUEST_FIELDS):
        return None, _error("invalid_request", "Request contains unsupported security fields.")
    return payload, None


def _valid_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 256
        and value == value.strip()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _gmail_thread_request(access_token: str, thread_id: str) -> tuple[object | None, dict | None]:
    request = Request(
        f"{GMAIL_API_BASE_URL}/threads/{quote(thread_id, safe='')}?format=full",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_GMAIL_RESPONSE_BYTES:
                        return None, {"code": "gmail_thread_too_large"}
                except ValueError:
                    pass
            body = response.read(MAX_GMAIL_RESPONSE_BYTES + 1)
            if len(body) > MAX_GMAIL_RESPONSE_BYTES:
                return None, {"code": "gmail_thread_too_large"}
            try:
                return json.loads(body.decode("utf-8")), None
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None, {"code": "gmail_response_invalid"}
    except HTTPError as error:
        if error.code == 404:
            return None, {"code": "gmail_thread_not_found"}
        return None, {
            "code": gmail_http_error_code(
                error.code,
                "gmail_thread_fetch_failed",
            )
        }
    except (URLError, TimeoutError):
        return None, {"code": "gmail_unavailable"}


def _gmail_error_response(error: dict) -> tuple[int, dict]:
    code = error.get("code")
    mapping = {
        "gmail_thread_not_found": (404, "The requested Gmail conversation was not found."),
        "gmail_unavailable": (502, "Gmail is temporarily unavailable."),
        "gmail_response_invalid": (502, "Gmail returned an invalid conversation response."),
        "gmail_thread_too_large": (502, "The Gmail conversation is too large to load."),
        "gmail_thread_fetch_failed": (502, "The Gmail conversation could not be loaded."),
        "gmail_token_invalid": (401, "Gmail authorization must be renewed."),
        "gmail_permission_denied": (403, "Gmail did not permit this operation."),
        "gmail_rate_limited": (502, "Gmail is temporarily rate limited."),
    }
    status, message = mapping.get(code, (502, "The Gmail conversation could not be loaded."))
    response_code = "reconnect_required" if code == "gmail_token_invalid" else code
    return status, _error(response_code if code in mapping else "gmail_thread_fetch_failed", message)


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if (
            code == HTTPStatus.NOT_IMPLEMENTED
            and getattr(self, "command", "") not in {"POST", "OPTIONS"}
        ):
            self.close_connection = True
            _send_method_not_allowed(
                self,
                write_body=getattr(self, "command", "") != "HEAD",
            )
            return
        super().send_error(code, message, explain)

    def do_POST(self):
        try:
            handler._handle_post(self)
        except Exception:
            _send_json(
                self,
                500,
                _error("internal_error", "The Gmail conversation could not be loaded."),
            )

    def _handle_post(self):
        payload, request_error = _read_json_body(self)
        if request_error:
            _send_json(self, 400, request_error)
            return

        mailbox_id = payload.get("mailboxId")
        provider_thread_id = payload.get("providerThreadId")
        if not _valid_identifier(mailbox_id) or not _valid_identifier(provider_thread_id):
            _send_json(self, 400, _error("invalid_request", "Valid mailbox and thread ids are required."))
            return

        resolution = resolve_authenticated_gmail(self.headers, mailbox_id)
        if resolution["status"] != "ok":
            _send_json(self, int(resolution["status_code"]), resolution["error"])
            return
        context = resolution["context"]

        gmail_payload, gmail_error = _gmail_thread_request(
            context["access_token"], provider_thread_id
        )
        if gmail_error and gmail_error.get("code") == "gmail_token_invalid" and not context["refresh_attempted"]:
            refreshed = refresh_gmail_context(context)
            if refreshed["status"] != "ok":
                _send_json(self, int(refreshed["status_code"]), refreshed["error"])
                return
            context = refreshed["context"]
            gmail_payload, gmail_error = _gmail_thread_request(
                context["access_token"], provider_thread_id
            )

        if gmail_error:
            status, response = _gmail_error_response(gmail_error)
            _send_json(self, status, response)
            return

        try:
            messages = parse_gmail_thread(gmail_payload, provider_thread_id)
        except OverflowError:
            _send_json(self, 502, _error("gmail_thread_too_large", "The Gmail conversation is too large to load."))
            return
        except GmailThreadParseError:
            _send_json(self, 502, _error("gmail_response_invalid", "Gmail returned an invalid conversation response."))
            return

        _send_json(
            self,
            200,
            {"ok": True, "providerThreadId": provider_thread_id, "messages": messages},
        )

    def do_GET(self):
        _send_method_not_allowed(self)

    def do_PUT(self):
        _send_method_not_allowed(self)

    def do_PATCH(self):
        _send_method_not_allowed(self)

    def do_DELETE(self):
        _send_method_not_allowed(self)

    def do_HEAD(self):
        _send_method_not_allowed(self, write_body=False)

    def do_OPTIONS(self):
        _send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
