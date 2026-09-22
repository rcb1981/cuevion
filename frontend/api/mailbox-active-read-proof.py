"""TEMPORARY Preview-only active mailbox read proof. Remove before merge."""

import json
import os
from http.server import BaseHTTPRequestHandler

from cuevion_mailbox.preview_active_read import run_preview_gmail_active_read


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            result = run_preview_gmail_active_read(
                environment=os.environ,
                workspace_id="wsp_l44kMFQRDa7J3askwYxxbQ",
                owner_user_id="usr_jDkwYBEn-6jawBwY_-Pzpg",
                mailbox_id="preview-active-read-proof",
                mailbox_account_identity="preview-proof@example.invalid",
                limit=10,
            )
            if result.status != "resolved" or result.projected_count != 1:
                raise RuntimeError("preview active read proof failed")
            payload = {
                "ok": True,
                "mode": "active_read",
                "scope_resolved": True,
                "projected_count": 1,
            }
            status = 200
        except Exception:
            payload = {"ok": False}
            status = 503

        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.send_error(405)

    def send_error(self, code, message=None, explain=None):
        del message, explain
        body = b'{"ok":false}'
        self.send_response(405 if code == 501 else code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return
