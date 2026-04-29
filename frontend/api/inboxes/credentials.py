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

from beta_auth import parse_beta_session_token, read_beta_session_cookie  # noqa: E402
from mailbox_secret_store import (  # noqa: E402
    get_mailbox_secret_statuses,
    save_mailbox_secret,
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


def _get_authenticated_user(headers) -> dict | None:
    session_token = read_beta_session_cookie(headers)
    return parse_beta_session_token(session_token or "")


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
        session_user = _get_authenticated_user(self.headers)
        if not session_user:
            _send_json(self, 401, _build_error("unauthorized", "A valid beta session is required."))
            return

        mailbox_ids = _parse_mailbox_ids_from_query(self.path)
        _send_json(
            self,
            200,
            {
                "ok": True,
                "credentials": get_mailbox_secret_statuses(
                    session_user["email"],
                    mailbox_ids,
                ),
            },
        )

    def do_POST(self):
        session_user = _get_authenticated_user(self.headers)
        if not session_user:
            _send_json(self, 401, _build_error("unauthorized", "A valid beta session is required."))
            return

        payload, error = _read_json_body(self)
        if error:
            _send_json(self, 400, error)
            return

        mailbox_id = str((payload or {}).get("mailboxId") or "").strip()
        imap_password = (payload or {}).get("imapPassword")
        smtp_password = (payload or {}).get("smtpPassword")

        if not mailbox_id:
            _send_json(self, 400, _build_error("invalid_request", "Mailbox id is required."))
            return

        saved_record, save_error = save_mailbox_secret(
            session_user["email"],
            mailbox_id,
            imap_password if isinstance(imap_password, str) and imap_password else None,
            smtp_password if isinstance(smtp_password, str) and smtp_password else None,
        )
        if save_error:
            _send_json(self, 503, {"ok": False, "error": save_error})
            return

        _send_json(
            self,
            200,
            {
                "ok": True,
                "mailboxId": mailbox_id,
                "imapPasswordSet": bool(saved_record and saved_record.get("imapPassword")),
                "smtpPasswordSet": bool(saved_record and saved_record.get("smtpPassword")),
            },
        )

    def do_OPTIONS(self):
        _send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
