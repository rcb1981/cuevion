import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from beta_auth import parse_beta_session_token, read_beta_session_cookie  # noqa: E402
from mailbox_secret_store import get_mailbox_secret, save_mailbox_secret  # noqa: E402


def _get_authenticated_user(headers) -> dict | None:
    session_token = read_beta_session_cookie(headers)
    return parse_beta_session_token(session_token or "")


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, payload: dict):
        response_body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def do_POST(self):
        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_request",
                        "message": "Request body must be valid JSON",
                    },
                },
            )
            return

        session_user = _get_authenticated_user(self.headers)
        mailbox_id = str(payload.get("mailboxId") or "").strip()
        provided_password = str(payload.get("password") or "")

        if not provided_password and session_user and mailbox_id:
            secret_record = get_mailbox_secret(session_user["email"], mailbox_id)
            stored_imap_password = (
                secret_record.get("imapPassword")
                if isinstance(secret_record, dict)
                else None
            )
            if isinstance(stored_imap_password, str) and stored_imap_password:
                payload["password"] = stored_imap_password

        internal_role = payload.get("internalRole", None)
        focus_preferences = payload.get("focusPreferences", None)
        selected_inboxes = payload.get("selectedInboxes", None)
        print("[DEBUG] Backend received selectedInboxes:", selected_inboxes)
        payload["internalRole"] = internal_role
        payload["focusPreferences"] = focus_preferences

        try:
            from imap_connect_preview import build_connect_preview_response

            status_code, response_payload = build_connect_preview_response(payload)
            if (
                status_code < 400
                and response_payload.get("ok") is True
                and provided_password
                and session_user
                and mailbox_id
            ):
                save_mailbox_secret(
                    session_user["email"],
                    mailbox_id,
                    imap_password=provided_password,
                )
            self._send_json(status_code, response_payload)
        except Exception:
            self._send_json(
                500,
                {
                    "ok": False,
                    "error": {
                        "code": "server_error",
                        "message": "Could not connect to inbox.",
                    },
                },
            )

    def do_GET(self):
        self._send_json(
            405,
            {
                "ok": False,
                "error": {
                    "code": "method_not_allowed",
                    "message": "Use POST for inbox connection tests",
                },
            },
        )

    def log_message(self, format, *args):
        return
