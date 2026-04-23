import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from beta_auth import build_beta_session_logout_cookie


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
        _send_json(
            self,
            200,
            {
                "ok": True,
            },
            cookie=build_beta_session_logout_cookie(self.headers),
        )

    def do_GET(self):
        _send_json(
            self,
            405,
            {
                "ok": False,
                "error": {
                    "code": "method_not_allowed",
                    "message": "Use POST for beta logout.",
                },
            },
        )

    def log_message(self, format, *args):
        return
