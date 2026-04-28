import base64
import json
import re
import sys
from datetime import datetime, timezone
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from imap_connect_preview import get_message_attachment_payload
from oauth_token_store import (
    get_google_token_record_with_metadata,
    refresh_google_token_record,
)

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def _json_response(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _is_token_expired(token_record: dict) -> bool:
    expires_at = token_record.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at.strip():
        return False

    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False

    return parsed <= datetime.now(timezone.utc)


def _gmail_request(access_token: str, path: str) -> tuple[dict | None, dict | None]:
    request = Request(
        f"{GMAIL_API_BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload) if payload else {}, None
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        try:
            parsed_error = json.loads(error_body) if error_body else {}
        except json.JSONDecodeError:
            parsed_error = {}

        error_message = (
            parsed_error.get("error", {}).get("message")
            if isinstance(parsed_error.get("error"), dict)
            else None
        ) or f"Gmail request failed with HTTP {error.code}."

        error_code = "gmail_attachment_download_failed"
        if error.code in {401, 403}:
            error_code = "gmail_token_invalid"

        return None, {
            "code": error_code,
            "message": error_message,
            "status_code": error.code,
        }
    except URLError as error:
        return None, {
            "code": "gmail_unavailable",
            "message": (
                str(error.reason)
                if getattr(error, "reason", None)
                else "Could not reach Gmail."
            ),
        }


def _safe_header_filename(value: str) -> str:
    normalized = re.sub(r"[\r\n\"\\]", "_", value).strip()
    return normalized or "attachment"


def _binary_response(
    handler: BaseHTTPRequestHandler,
    content: bytes,
    filename: str,
    mime_type: str,
):
    safe_filename = _safe_header_filename(filename)
    handler.send_response(200)
    handler.send_header("Content-Type", mime_type or "application/octet-stream")
    handler.send_header("Content-Length", str(len(content)))
    handler.send_header(
        "Content-Disposition",
        f'attachment; filename="{safe_filename}"; filename*=UTF-8\'\'{quote(filename or "attachment", safe="")}',
    )
    handler.end_headers()
    handler.wfile.write(content)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            _json_response(self, 400, _error("invalid_request", "Request body must be valid JSON."))
            return

        email_address = str(payload.get("email") or "").strip().lower()
        message_id = str(payload.get("messageId") or "").strip()
        attachment_id = str(payload.get("attachmentId") or "").strip()

        if not email_address or not message_id or not attachment_id:
            _json_response(
                self,
                400,
                _error("invalid_request", "Email, message id, and attachment id are required."),
            )
            return

        token_record = get_google_token_record_with_metadata(email_address)
        if not token_record:
            _json_response(
                self,
                401,
                _error("gmail_token_missing", "No stored Gmail token is available for this mailbox."),
            )
            return

        access_token = token_record.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            _json_response(
                self,
                401,
                _error("gmail_token_missing", "The stored Gmail token record is incomplete."),
            )
            return

        if _is_token_expired(token_record):
            refreshed_record, refresh_error = refresh_google_token_record(email_address)
            if refresh_error:
                _json_response(self, 401, _error(refresh_error["code"], refresh_error["message"]))
                return

            token_record = refreshed_record or token_record
            access_token = token_record.get("access_token")
            if not isinstance(access_token, str) or not access_token.strip():
                _json_response(
                    self,
                    401,
                    _error("gmail_token_missing", "The refreshed Gmail token record is incomplete."),
                )
                return

        message_payload, message_error = _gmail_request(
            access_token.strip(),
            f"/messages/{quote(message_id, safe='')}?format=raw",
        )
        if message_error:
            _json_response(
                self,
                401 if message_error.get("code") == "gmail_token_invalid" else 502,
                _error(message_error["code"], message_error["message"]),
            )
            return

        raw_message = message_payload.get("raw") if isinstance(message_payload, dict) else None
        if not isinstance(raw_message, str) or not raw_message:
            _json_response(
                self,
                404,
                _error("attachment_not_found", "The requested attachment could not be found."),
            )
            return

        try:
            parsed_message = message_from_bytes(_base64url_decode(raw_message))
        except Exception:
            _json_response(
                self,
                502,
                _error("gmail_attachment_download_failed", "Gmail returned an unreadable message."),
            )
            return

        attachment = get_message_attachment_payload(parsed_message, attachment_id)
        if not attachment:
            _json_response(
                self,
                404,
                _error("attachment_not_found", "The requested attachment could not be found."),
            )
            return

        _binary_response(
            self,
            attachment["content"],
            attachment["filename"],
            attachment["mimeType"],
        )

    def do_GET(self):
        _json_response(
            self,
            405,
            _error("method_not_allowed", "Use POST to download Gmail attachments."),
        )

    def log_message(self, format, *args):
        return
