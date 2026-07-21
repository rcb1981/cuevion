import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from mailbox_secret_store import (  # noqa: E402
    read_mailbox_secret,
)
from user_config_store import (  # noqa: E402
    resolve_authenticated_user,
    resolve_owned_managed_inbox,
)


def _send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
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


def _read_json_body(handler: BaseHTTPRequestHandler) -> tuple[dict | None, dict | None]:
    content_length = int(handler.headers.get("content-length", "0"))
    raw_body = handler.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

    try:
        payload = json.loads(raw_body or "{}")
    except json.JSONDecodeError:
        return None, _build_error("invalid_request", "Request body must be valid JSON.")

    if not isinstance(payload, dict):
        return None, _build_error("invalid_request", "Request body must be a JSON object.")

    return payload, None


def _parse_mailbox_ids_from_query(path: str) -> list[str]:
    query = parse_qs(urlsplit(path).query)
    raw_mailbox_ids = query.get("mailboxIds") or []
    mailbox_ids: list[str] = []

    for raw_value in raw_mailbox_ids:
        mailbox_ids.extend(
            mailbox_id.strip()
            for mailbox_id in raw_value.split(",")
            if mailbox_id.strip()
        )

    return mailbox_ids


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        session_user, auth_error = resolve_authenticated_user(self.headers)
        if not session_user:
            if auth_error and auth_error.get("code") == "session_auth_unavailable":
                _send_json(
                    self,
                    503,
                    _build_error(
                        "session_auth_unavailable",
                        "Authentication is temporarily unavailable.",
                    ),
                )
            else:
                _send_json(
                    self,
                    401,
                    _build_error(
                        "unauthorized",
                        "A valid member session is required.",
                    ),
                )
            return

        mailbox_ids = _parse_mailbox_ids_from_query(self.path)
        credentials: dict[str, dict] = {}
        for mailbox_id in mailbox_ids:
            owned_result = resolve_owned_managed_inbox(self.headers, mailbox_id)
            if owned_result["status"] != "ok":
                status_code = 503 if owned_result["status"] == "unavailable" else 404
                _send_json(
                    self,
                    status_code,
                    _build_error(
                        "mailbox_status_unavailable"
                        if status_code == 503
                        else "managed_inbox_not_found",
                        "Mailbox credential status is unavailable."
                        if status_code == 503
                        else "The requested mailbox was not found.",
                    ),
                )
                return

            secret_result = read_mailbox_secret(session_user["email"], mailbox_id)
            if secret_result["status"] in {"unavailable", "malformed"}:
                _send_json(
                    self,
                    503,
                    _build_error(
                        "mailbox_secret_store_unavailable",
                        "Mailbox credential status is temporarily unavailable.",
                    ),
                )
                return
            secret_record = secret_result["record"]
            credentials[mailbox_id] = {
                "imapPasswordSet": bool(secret_record and secret_record.get("imapPassword")),
                "smtpPasswordSet": bool(secret_record and secret_record.get("smtpPassword")),
            }

        _send_json(
            self,
            200,
            {
                "ok": True,
                "credentials": credentials,
            },
        )

    def do_POST(self):
        _send_json(
            self,
            405,
            _build_error(
                "method_not_allowed",
                "Mailbox credentials can only be saved during authenticated connection.",
            ),
        )

    def do_OPTIONS(self):
        _send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
