"""TEMPORARY Preview-only cleanup for Gmail durable write hook proof. Remove immediately after use."""

import json
import os
from http.server import BaseHTTPRequestHandler

from cuevion_mailbox.runtime import build_active_write_mailbox_repositories


_WORKSPACE_ID = "wsp_l44kMFQRDa7J3askwYxxbQ"
_OWNER_USER_ID = "usr_jDkwYBEn-6jawBwY_-Pzpg"
_MAILBOX_ID = "preview-gmail-write-hook-proof"
_ACCOUNT_IDENTITY = "preview-gmail-write-hook@example.invalid"


def _cleanup():
    try:
        repositories = build_active_write_mailbox_repositories(os.environ)
        connection = repositories.writer._connection()
    except Exception:
        return 503, {"ok": False, "stage": "runtime"}

    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            DELETE FROM cuevion_mailbox.mailbox_sync_state
            WHERE workspace_id = %s
              AND owner_user_id = %s
              AND mailbox_id = %s
              AND provider = 'google'
              AND provider_account_identity = %s
              AND source_generation = 1
            """,
            (
                _WORKSPACE_ID,
                _OWNER_USER_ID,
                _MAILBOX_ID,
                _ACCOUNT_IDENTITY,
            ),
        )
        deleted = cursor.rowcount
        if deleted not in (0, 1):
            connection.rollback()
            return 503, {"ok": False, "stage": "delete_count"}

        connection.commit()

        cursor.execute(
            """
            SELECT
              (SELECT count(*) FROM cuevion_mailbox.mailbox_sync_state
               WHERE mailbox_id = %s) AS state_rows,
              (SELECT count(*) FROM cuevion_mailbox.mailbox_sync_cursor
               WHERE mailbox_id = %s) AS cursor_rows,
              (SELECT count(*) FROM cuevion_mailbox.mailbox_messages
               WHERE mailbox_id = %s) AS message_rows,
              (SELECT count(*) FROM cuevion_mailbox.mailbox_change_outbox
               WHERE mailbox_id = %s) AS outbox_rows
            """,
            (_MAILBOX_ID, _MAILBOX_ID, _MAILBOX_ID, _MAILBOX_ID),
        )
        row = cursor.fetchone()
        connection.rollback()

        if row != (0, 0, 0, 0):
            return 503, {
                "ok": False,
                "stage": "verify",
                "state_rows": row[0],
                "cursor_rows": row[1],
                "message_rows": row[2],
                "outbox_rows": row[3],
            }

        return 200, {
            "ok": True,
            "deleted_state_rows": deleted,
            "state_rows": 0,
            "cursor_rows": 0,
            "message_rows": 0,
            "outbox_rows": 0,
        }
    except Exception:
        connection.rollback()
        return 503, {"ok": False, "stage": "exception"}
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, payload = _cleanup()
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = b'{"ok":false,"stage":"method"}'
        self.send_response(405)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return
