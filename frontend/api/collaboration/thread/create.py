import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
COLLABORATION_DIR = CURRENT_DIR.parent
if str(COLLABORATION_DIR) not in sys.path:
    sys.path.insert(0, str(COLLABORATION_DIR))

from models import (
    COLLABORATION_THREAD_SCHEMA_VERSION,
    normalize_collaboration_record,
    normalize_collaboration_thread_record,
    normalize_source_message_snapshot,
)
from redis_store import create_thread_if_missing


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
                _build_error("invalid_request", "Request body must be valid JSON."),
            )
            return

        workspace_id = str(payload.get("workspaceId") or "").strip().lower()
        mailbox_id = str(payload.get("mailboxId") or "").strip()
        source_message = normalize_source_message_snapshot(payload.get("sourceMessage"))
        collaboration = normalize_collaboration_record(payload.get("collaboration"))
        is_shared = payload.get("isShared") is True

        if not workspace_id or not mailbox_id or source_message is None or collaboration is None:
            _send_json(
                self,
                400,
                _build_error("invalid_request", "workspaceId, mailboxId, sourceMessage, and collaboration are required."),
            )
            return

        thread_record = normalize_collaboration_thread_record(
            {
                "v": COLLABORATION_THREAD_SCHEMA_VERSION,
                "workspaceId": workspace_id,
                "mailboxId": mailbox_id,
                "messageId": source_message["id"],
                "sourceMessage": source_message,
                "isShared": is_shared,
                "collaboration": collaboration,
            }
        )

        if thread_record is None:
            _send_json(
                self,
                400,
                _build_error("invalid_request", "Canonical collaboration thread payload is invalid."),
            )
            return

        saved_thread, error = create_thread_if_missing(thread_record)
        if error or saved_thread is None:
            _send_json(
                self,
                503,
                _build_error(
                    error["code"] if error else "collaboration_store_unavailable",
                    error["message"] if error else "Could not save canonical collaboration thread.",
                ),
            )
            return

        _send_json(
            self,
            200,
            {
                "ok": True,
                "thread": saved_thread,
            },
        )

    def do_GET(self):
        _send_json(
            self,
            405,
            _build_error("method_not_allowed", "Use POST to create a collaboration thread."),
        )

    def log_message(self, format, *args):
        return
