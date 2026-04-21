import base64
import json
import sys
from datetime import datetime, timezone
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from oauth_token_store import (
    get_google_token_record_with_metadata,
)

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_FETCH_LIMIT = 20
MAX_FETCH_LIMIT = 25


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def _send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


def _build_error(code: str, message: str) -> dict:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }


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

        error_code = "gmail_fetch_failed"
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


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            _send_json(self, 400, _build_error("invalid_request", "Request body must be valid JSON."))
            return

        provider = str(payload.get("provider") or "").strip().lower()
        email_address = str(payload.get("email") or "").strip().lower()
        internal_role = payload.get("internalRole", None)
        focus_preferences = payload.get("focusPreferences", None)
        limit = max(1, min(int(payload.get("limit") or DEFAULT_FETCH_LIMIT), MAX_FETCH_LIMIT))

        if provider != "google":
            _send_json(
                self,
                400,
                _build_error("unsupported_provider", "Only Gmail OAuth fetch is supported by this endpoint."),
            )
            return

        if not email_address:
            _send_json(
                self,
                400,
                _build_error("invalid_request", "A connected Gmail address is required."),
            )
            return

        token_record = get_google_token_record_with_metadata(email_address)
        if not token_record:
            _send_json(
                self,
                401,
                _build_error(
                    "gmail_token_missing",
                    "No stored Gmail token is available for this mailbox.",
                ),
            )
            return

        access_token = token_record.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            _send_json(
                self,
                401,
                _build_error(
                    "gmail_token_missing",
                    "The stored Gmail token record is incomplete.",
                ),
            )
            return

        if _is_token_expired(token_record):
            _send_json(
                self,
                401,
                _build_error(
                    "gmail_token_expired",
                    "The stored Gmail token has expired. Gmail refresh is not available in this runtime yet.",
                ),
            )
            return

        list_payload, list_error = _gmail_request(
            access_token,
            f"/messages?{urlencode({'labelIds': 'INBOX', 'maxResults': limit})}",
        )
        if list_error:
            _send_json(self, 401 if list_error.get("code") == "gmail_token_invalid" else 502, _build_error(list_error["code"], list_error["message"]))
            return

        message_refs = list_payload.get("messages") if isinstance(list_payload, dict) else None
        if not isinstance(message_refs, list):
            message_refs = []
        decode_parse_errors = []
        debug = {
            "listed_message_refs": len(message_refs),
            "fetched_message_payloads": 0,
            "raw_messages_present": 0,
            "decoded_messages": 0,
            "previews_created": 0,
            "skipped_missing_message_id": 0,
            "skipped_missing_raw": 0,
            "skipped_decode_parse_error": 0,
            "skipped_preview_error": 0,
            "decode_parse_errors": decode_parse_errors,
        }

        try:
            from imap_connect_preview import to_message_preview
        except Exception:
            _send_json(
                self,
                500,
                _build_error(
                    "server_error",
                    "Cuevion could not load the mailbox preview pipeline for Gmail fetch.",
                ),
            )
            return

        previews = []
        inbox_uid_set: list[str] = []

        for index, message_ref in enumerate(message_refs):
            message_id = str((message_ref or {}).get("id") or "").strip()
            if not message_id:
                debug["skipped_missing_message_id"] += 1
                continue

            message_payload, message_error = _gmail_request(
                access_token,
                f"/messages/{quote(message_id, safe='')}?format=raw",
            )
            if message_error:
                _send_json(
                    self,
                    401 if message_error.get("code") == "gmail_token_invalid" else 502,
                    _build_error(message_error["code"], message_error["message"]),
                )
                return
            debug["fetched_message_payloads"] += 1

            raw_message = message_payload.get("raw") if isinstance(message_payload, dict) else None
            if not isinstance(raw_message, str) or not raw_message:
                debug["skipped_missing_raw"] += 1
                continue
            debug["raw_messages_present"] += 1

            try:
                message_bytes = _base64url_decode(raw_message)
                parsed_message = message_from_bytes(message_bytes)
            except Exception as error:
                debug["skipped_decode_parse_error"] += 1
                if len(decode_parse_errors) < 5:
                    decode_parse_errors.append(str(error)[:200])
                continue
            debug["decoded_messages"] += 1

            label_ids = message_payload.get("labelIds") if isinstance(message_payload, dict) else None
            unread = isinstance(label_ids, list) and "UNREAD" in label_ids
            gmail_internal_id = str(message_payload.get("id") or "").strip() or None
            if gmail_internal_id:
                inbox_uid_set.append(gmail_internal_id)

            try:
                previews.append(
                    to_message_preview(
                        parsed_message,
                        index,
                        email_address,
                        unread,
                        gmail_internal_id,
                        internal_role=internal_role,
                        focus_preferences=focus_preferences,
                    ),
                )
                debug["previews_created"] += 1
            except Exception:
                debug["skipped_preview_error"] += 1
                continue

        _send_json(
            self,
            200,
            {
                "ok": True,
                "messages": previews,
                "inboxUidSet": inbox_uid_set,
                "uidValidity": "gmail-api",
                "debug": debug,
            },
        )

    def do_GET(self):
        _send_json(
            self,
            405,
            _build_error("method_not_allowed", "Use POST for Gmail mailbox fetch."),
        )

    def log_message(self, format, *args):
        return
