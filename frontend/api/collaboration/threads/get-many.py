import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
COLLABORATION_DIR = CURRENT_DIR.parent
if str(COLLABORATION_DIR) not in sys.path:
    sys.path.insert(0, str(COLLABORATION_DIR))

from redis_store import MAX_COLLABORATION_THREAD_BATCH_SIZE, get_threads_many


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
        "threadsByMessageId": {},
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
        mailbox_id = payload.get("mailboxId")
        message_ids = payload.get("messageIds")

        if not workspace_id:
            _send_json(
                self,
                400,
                _build_error("invalid_request", "workspaceId is required."),
            )
            return

        if mailbox_id is not None and not isinstance(mailbox_id, str):
            _send_json(
                self,
                400,
                _build_error("invalid_request", "mailboxId must be a string when provided."),
            )
            return

        if not isinstance(message_ids, list):
            _send_json(
                self,
                400,
                _build_error("invalid_request", "messageIds must be an array of strings."),
            )
            return

        normalized_message_ids = [
            message_id.strip()
            for message_id in message_ids
            if isinstance(message_id, str) and message_id.strip()
        ]

        if len(normalized_message_ids) > MAX_COLLABORATION_THREAD_BATCH_SIZE:
            normalized_message_ids = normalized_message_ids[:MAX_COLLABORATION_THREAD_BATCH_SIZE]

        threads_by_message_id = get_threads_many(workspace_id, normalized_message_ids)

        _send_json(
            self,
            200,
            {
                "ok": True,
                "threadsByMessageId": threads_by_message_id,
            },
        )

    def do_GET(self):
        _send_json(
            self,
            405,
            _build_error(
                "method_not_allowed",
                "Use POST to fetch collaboration threads.",
            ),
        )

    def log_message(self, format, *args):
        return
