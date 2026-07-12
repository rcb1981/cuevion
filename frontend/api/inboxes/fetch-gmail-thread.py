from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
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
from oauth_token_store import (  # noqa: E402
    get_google_token_record_with_metadata,
    refresh_google_token_record,
)
from user_config_store import resolve_owned_managed_inbox  # noqa: E402

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_REQUEST_BODY_BYTES = 16 * 1024
MAX_GMAIL_RESPONSE_BYTES = 10 * 1024 * 1024
NETWORK_TIMEOUT_SECONDS = 20
SENSITIVE_REQUEST_FIELDS = {
    "email",
    "provider",
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


def _is_token_expired(token_record: dict) -> bool:
    expires_at = token_record.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at.strip():
        return False
    try:
        return datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= datetime.now(
            timezone.utc
        )
    except ValueError:
        return False


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
        if error.code in {401, 403}:
            return None, {"code": "gmail_token_invalid"}
        return None, {"code": "gmail_thread_fetch_failed"}
    except (URLError, TimeoutError):
        return None, {"code": "gmail_unavailable"}


def _token_error_response(error: dict | None) -> tuple[int, dict]:
    code = str((error or {}).get("code") or "gmail_token_invalid")
    if code not in {"gmail_token_missing", "gmail_refresh_token_missing"}:
        code = "gmail_token_invalid"
    messages = {
        "gmail_token_missing": "No stored Gmail authorization is available.",
        "gmail_refresh_token_missing": "Gmail authorization must be renewed.",
        "gmail_token_invalid": "Gmail authorization is no longer valid.",
    }
    return 401, _error(code, messages[code])


def _gmail_error_response(error: dict) -> tuple[int, dict]:
    code = error.get("code")
    mapping = {
        "gmail_thread_not_found": (404, "The requested Gmail conversation was not found."),
        "gmail_unavailable": (502, "Gmail is temporarily unavailable."),
        "gmail_response_invalid": (502, "Gmail returned an invalid conversation response."),
        "gmail_thread_too_large": (502, "The Gmail conversation is too large to load."),
        "gmail_thread_fetch_failed": (502, "The Gmail conversation could not be loaded."),
        "gmail_token_invalid": (401, "Gmail authorization is no longer valid."),
    }
    status, message = mapping.get(code, (502, "The Gmail conversation could not be loaded."))
    return status, _error(code if code in mapping else "gmail_thread_fetch_failed", message)


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
        payload, request_error = _read_json_body(self)
        if request_error:
            _send_json(self, 400, request_error)
            return

        mailbox_id = payload.get("mailboxId")
        provider_thread_id = payload.get("providerThreadId")
        if not _valid_identifier(mailbox_id) or not _valid_identifier(provider_thread_id):
            _send_json(self, 400, _error("invalid_request", "Valid mailbox and thread ids are required."))
            return

        ownership = resolve_owned_managed_inbox(self.headers, mailbox_id)
        if ownership["status"] != "ok" or not ownership["inbox"]:
            if ownership["status"] == "unauthorized":
                status, code, message = 401, "unauthorized", "An authenticated session is required."
            elif ownership["status"] == "not_found":
                status, code, message = 404, "gmail_connection_not_found", "Gmail connection was not found."
            else:
                status, code, message = 503, "user_config_store_unavailable", "Mailbox ownership could not be verified."
            _send_json(self, status, _error(code, message))
            return

        inbox = ownership["inbox"]
        if inbox["provider"] != "google":
            _send_json(self, 400, _error("unsupported_provider", "This mailbox is not a Gmail connection."))
            return
        if inbox["connected"] is not True or inbox["connectionStatus"] != "connected":
            _send_json(self, 409, _error("gmail_connection_not_ready", "Gmail connection is not ready."))
            return
        mailbox_email = inbox["email"]
        if not mailbox_email:
            _send_json(self, 503, _error("user_config_store_unavailable", "Mailbox ownership could not be verified."))
            return

        token_record = get_google_token_record_with_metadata(mailbox_email)
        if not token_record:
            status, response = _token_error_response({"code": "gmail_token_missing"})
            _send_json(self, status, response)
            return
        access_token = token_record.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            status, response = _token_error_response({"code": "gmail_token_missing"})
            _send_json(self, status, response)
            return

        did_refresh = False
        if _is_token_expired(token_record):
            token_record, refresh_error = refresh_google_token_record(mailbox_email)
            did_refresh = True
            if refresh_error or not token_record:
                status, response = _token_error_response(refresh_error)
                _send_json(self, status, response)
                return
            access_token = token_record.get("access_token")
            if not isinstance(access_token, str) or not access_token.strip():
                status, response = _token_error_response({"code": "gmail_token_invalid"})
                _send_json(self, status, response)
                return

        gmail_payload, gmail_error = _gmail_thread_request(access_token.strip(), provider_thread_id)
        if gmail_error and gmail_error.get("code") == "gmail_token_invalid" and not did_refresh:
            token_record, refresh_error = refresh_google_token_record(mailbox_email)
            did_refresh = True
            if refresh_error or not token_record:
                status, response = _token_error_response(refresh_error)
                _send_json(self, status, response)
                return
            refreshed_access_token = token_record.get("access_token")
            if not isinstance(refreshed_access_token, str) or not refreshed_access_token.strip():
                status, response = _token_error_response({"code": "gmail_token_invalid"})
                _send_json(self, status, response)
                return
            gmail_payload, gmail_error = _gmail_thread_request(
                refreshed_access_token.strip(), provider_thread_id
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
