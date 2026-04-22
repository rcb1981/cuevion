import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from beta_auth import parse_beta_session_token, read_beta_session_cookie, resolve_beta_session_secret


def _send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        session_secret = resolve_beta_session_secret()
        if not session_secret:
            _send_json(
                self,
                503,
                {
                    "ok": False,
                    "authenticated": False,
                    "error": {
                        "code": "beta_not_configured",
                        "message": "Private beta access is not configured.",
                    },
                },
            )
            return

        session_token = read_beta_session_cookie(self.headers)
        session_user = parse_beta_session_token(session_token or "")

        if not session_user:
            _send_json(
                self,
                401,
                {
                    "ok": False,
                    "authenticated": False,
                    "error": {
                        "code": "unauthorized",
                        "message": "A valid beta session is required.",
                    },
                },
            )
            return

        _send_json(
            self,
            200,
            {
                "ok": True,
                "authenticated": True,
                "user": session_user,
            },
        )

    def do_POST(self):
        _send_json(
            self,
            405,
            {
                "ok": False,
                "error": {
                    "code": "method_not_allowed",
                    "message": "Use GET to read the beta session.",
                },
            },
        )

    def log_message(self, format, *args):
        return
