import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from time import time

CURRENT_DIR = Path(__file__).resolve().parent
COLLABORATION_DIR = CURRENT_DIR.parent
if str(COLLABORATION_DIR) not in sys.path:
    sys.path.insert(0, str(COLLABORATION_DIR))

from models import (
    normalize_collaboration_mention_record,
    normalize_collaboration_participant_record,
    normalize_collaboration_thread_record,
)
from redis_store import get_thread, save_thread_if_expected


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


def _normalize_mentions(value) -> list[dict]:
    if not isinstance(value, list):
        return []

    normalized_mentions: list[dict] = []
    for mention in value:
        normalized_mention = normalize_collaboration_mention_record(mention)
        if normalized_mention:
            normalized_mentions.append(normalized_mention)

    return normalized_mentions


def _normalize_participants(value) -> list[dict] | None:
    if not isinstance(value, list):
        return None

    normalized_participants: list[dict] = []
    for participant in value:
        normalized_participant = normalize_collaboration_participant_record(participant)
        if not normalized_participant:
            return None
        normalized_participants.append(normalized_participant)

    return normalized_participants


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
        message_id = str(payload.get("messageId") or "").strip()
        expected_updated_at = payload.get("expectedUpdatedAt")
        action = payload.get("action")

        if not workspace_id or not message_id or not isinstance(action, dict):
            _send_json(
                self,
                400,
                _build_error("invalid_request", "workspaceId, messageId, and action are required."),
            )
            return

        if expected_updated_at is not None:
            if isinstance(expected_updated_at, bool):
                expected_updated_at = None
            elif isinstance(expected_updated_at, int):
                pass
            elif isinstance(expected_updated_at, float) and expected_updated_at.is_integer():
                expected_updated_at = int(expected_updated_at)
            elif isinstance(expected_updated_at, str) and expected_updated_at.strip():
                try:
                    expected_updated_at = int(expected_updated_at.strip())
                except ValueError:
                    expected_updated_at = None
            else:
                expected_updated_at = None

        current_thread = get_thread(workspace_id, message_id)
        if current_thread is None:
            _send_json(
                self,
                404,
                _build_error("thread_not_found", "Canonical collaboration thread was not found."),
            )
            return

        action_type = str(action.get("type") or "").strip()
        next_timestamp = int(time() * 1000)
        next_thread = {
            **current_thread,
            "collaboration": {
                **current_thread["collaboration"],
            },
        }
        next_collaboration = next_thread["collaboration"]
        next_collaboration["updatedAt"] = next_timestamp

        if action_type == "reply":
            author_id = str(action.get("authorId") or "").strip()
            author_name = str(action.get("authorName") or "").strip()
            text = str(action.get("text") or "").strip()
            visibility = str(action.get("visibility") or "").strip()

            if not author_id or not author_name or not text or visibility not in {"internal", "shared"}:
                _send_json(
                    self,
                    400,
                    _build_error("invalid_request", "Reply action requires authorId, authorName, text, and visibility."),
                )
                return

            next_collaboration["state"] = (
                "needs_review"
                if current_thread["collaboration"]["state"] == "resolved"
                else current_thread["collaboration"]["state"]
            )
            next_collaboration["previewText"] = text
            next_collaboration["messages"] = [
                *current_thread["collaboration"]["messages"],
                {
                    "id": f"{message_id}-collaboration-reply-{next_timestamp}",
                    "authorId": author_id,
                    "authorName": author_name,
                    "text": text,
                    "timestamp": next_timestamp,
                    "visibility": visibility,
                    "mentions": _normalize_mentions(action.get("mentions")),
                },
            ]
            next_thread["isShared"] = True
        elif action_type == "participants_set":
            normalized_participants = _normalize_participants(action.get("participants"))
            if normalized_participants is None:
                _send_json(
                    self,
                    400,
                    _build_error("invalid_request", "participants_set requires a valid participants array."),
                )
                return

            next_collaboration["participants"] = normalized_participants
        elif action_type == "resolve":
            resolved_by_user_id = str(action.get("resolvedByUserId") or "").strip()
            resolved_by_user_name = str(action.get("resolvedByUserName") or "").strip()

            if not resolved_by_user_id or not resolved_by_user_name:
                _send_json(
                    self,
                    400,
                    _build_error("invalid_request", "resolve requires resolvedByUserId and resolvedByUserName."),
                )
                return

            next_collaboration["state"] = "resolved"
            next_collaboration["resolvedAt"] = next_timestamp
            next_collaboration["resolvedByUserId"] = resolved_by_user_id
            next_collaboration["resolvedByUserName"] = resolved_by_user_name
            next_thread["isShared"] = False
        elif action_type == "reopen":
            next_collaboration["state"] = "needs_review"
            next_collaboration.pop("resolvedAt", None)
            next_collaboration.pop("resolvedByUserId", None)
            next_collaboration.pop("resolvedByUserName", None)
            next_thread["isShared"] = True
        else:
            _send_json(
                self,
                400,
                _build_error("invalid_request", "Unsupported collaboration action."),
            )
            return

        normalized_next_thread = normalize_collaboration_thread_record(next_thread)
        if normalized_next_thread is None:
            _send_json(
                self,
                400,
                _build_error("invalid_request", "Canonical collaboration mutation payload is invalid."),
            )
            return

        saved_thread, error = save_thread_if_expected(
            normalized_next_thread,
            expected_updated_at=expected_updated_at,
        )

        if error and error.get("code") == "stale_thread" and saved_thread is not None:
            _send_json(
                self,
                409,
                {
                    "ok": False,
                    "code": "stale_thread",
                    "thread": saved_thread,
                },
            )
            return

        if error or saved_thread is None:
            _send_json(
                self,
                503,
                _build_error(
                    error["code"] if error else "collaboration_store_unavailable",
                    error["message"] if error else "Could not mutate canonical collaboration thread.",
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
            _build_error("method_not_allowed", "Use POST to mutate a collaboration thread."),
        )

    def log_message(self, format, *args):
        return
