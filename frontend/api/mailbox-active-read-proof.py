"""TEMPORARY Preview-only active mailbox read proof. Remove before merge."""

import json
import os
from http.server import BaseHTTPRequestHandler

from cuevion_mailbox.repository_contract import (
    MailboxProvider,
    MailboxReadAuthority,
)
from cuevion_mailbox.runtime import (
    MailboxRuntimeMode,
    build_active_read_mailbox_reader,
    parse_mailbox_runtime_configuration,
)


_AUTHORITY = MailboxReadAuthority(
    workspace_id="wsp_l44kMFQRDa7J3askwYxxbQ",
    owner_user_id="usr_jDkwYBEn-6jawBwY_-Pzpg",
    mailbox_id="preview-active-read-proof",
    provider=MailboxProvider.GOOGLE,
    provider_account_identity="preview-proof@example.invalid",
)


def _proof():
    try:
        config = parse_mailbox_runtime_configuration(os.environ)
    except Exception:
        return 503, {"ok": False, "stage": "config"}
    if config.mode is not MailboxRuntimeMode.ACTIVE_READ:
        return 503, {"ok": False, "stage": "mode"}

    try:
        reader = build_active_read_mailbox_reader(os.environ)
    except Exception:
        return 503, {"ok": False, "stage": "build"}

    try:
        scope = reader.resolve_current_scope(_AUTHORITY)
    except Exception:
        return 503, {"ok": False, "stage": "scope"}
    if scope is None:
        return 503, {"ok": False, "stage": "scope_missing"}

    try:
        projections = reader.list_messages(scope, limit=10)
    except Exception:
        return 503, {"ok": False, "stage": "list"}
    if len(projections) != 1:
        return 503, {"ok": False, "stage": "count"}

    return 200, {
        "ok": True,
        "mode": "active_read",
        "scope_resolved": True,
        "projected_count": 1,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, payload = _proof()
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
        body = b'{"ok":false,"stage":"method"}'
        self.send_response(405 if code == 501 else code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return
