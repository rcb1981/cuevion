import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from beta_auth import (
    build_beta_session_cookie,
    build_beta_session_token,
    is_valid_auth_email,
    normalize_auth_email,
    resolve_allowed_beta_emails,
    resolve_beta_invite_codes,
    resolve_beta_session_secret,
)


def _send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict, cookie: str | None = None):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    if cookie:
        handler.send_header("Set-Cookie", cookie)
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            _send_json(
                self,
                400,
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_request",
                        "message": "Request body must be valid JSON.",
                    },
                },
            )
            return

        session_secret = resolve_beta_session_secret()
        invite_codes = resolve_beta_invite_codes()
        allowed_emails = resolve_allowed_beta_emails()

        if not session_secret or not invite_codes:
            _send_json(
                self,
                503,
                {
                    "ok": False,
                    "error": {
                        "code": "beta_not_configured",
                        "message": "Private beta access is not configured.",
                    },
                },
            )
            return

        name = str(payload.get("name") or "").strip()
        email = normalize_auth_email(str(payload.get("email") or ""))
        invite_code = str(payload.get("inviteCode") or "").strip()

        if not name or not is_valid_auth_email(email) or not invite_code:
            _send_json(
                self,
                400,
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_request",
                        "message": "Name, email, and invite code are required.",
                    },
                },
            )
            return

        if invite_code not in invite_codes:
            _send_json(
                self,
                401,
                {
                    "ok": False,
                    "error": {
                        "code": "access_denied",
                        "message": "That invite code is not valid.",
                    },
                },
            )
            return

        if allowed_emails and email not in allowed_emails:
            _send_json(
                self,
                403,
                {
                    "ok": False,
                    "error": {
                        "code": "access_denied",
                        "message": "That email is not enabled for this beta.",
                    },
                },
            )
            return

        session_token = build_beta_session_token(name=name, email=email)
        _send_json(
            self,
            200,
            {
                "ok": True,
                "user": {
                    "name": name,
                    "email": email,
                    "userType": "member",
                },
            },
            cookie=build_beta_session_cookie(session_token, self.headers),
        )

    def do_GET(self):
        _send_json(
            self,
            405,
            {
                "ok": False,
                "error": {
                    "code": "method_not_allowed",
                    "message": "Use POST for beta login.",
                },
            },
        )

    def log_message(self, format, *args):
        return
