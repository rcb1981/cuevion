import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from time import time
from urllib.parse import unquote, urlsplit

CURRENT_DIR = Path(__file__).resolve().parent
COLLABORATION_DIR = CURRENT_DIR.parent.parent
if str(COLLABORATION_DIR) not in sys.path:
    sys.path.insert(0, str(COLLABORATION_DIR))

from models import (
    build_external_collaboration_thread_view,
    is_active_collaboration_invite_record,
    normalize_collaboration_mention_record,
    normalize_collaboration_thread_record,
)
from redis_store import get_invite, get_thread, save_thread_if_expected


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


def _parse_token(handler: BaseHTTPRequestHandler) -> str:
    path = urlsplit(handler.path).path
    invite_prefix = "/api/collaboration/invite/"
    action_suffix = "/action"
    if not path.startswith(invite_prefix) or not path.endswith(action_suffix):
        return ""

    token = path[len(invite_prefix) : -len(action_suffix)]
    if "/" in token:
        token = token.split("/", 1)[0]

    return unquote(token).strip()


def _normalize_expected_updated_at(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _normalize_mentions(value) -> list[dict]:
    if not isinstance(value, list):
        return []

    normalized_mentions: list[dict] = []
    for mention in value:
        normalized_mention = normalize_collaboration_mention_record(mention)
        if normalized_mention:
            normalized_mentions.append(normalized_mention)

    return normalized_mentions


def _build_external_thread_payload(thread: dict | None) -> dict | None:
    if thread is None:
        return None

    return build_external_collaboration_thread_view(thread)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        token = _parse_token(self)
        if not token:
            _send_json(
                self,
                400,
                _build_error("invalid_request", "Invite token is required."),
            )
            return

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

        action = payload.get("action")
        if not isinstance(action, dict):
            _send_json(
                self,
                400,
                _build_error("invalid_request", "action is required."),
            )
            return

        invite = get_invite(token)
        if invite is None:
            _send_json(
                self,
                404,
                _build_error("invalid_invite", "Collaboration invite was not found."),
            )
            return

        if not is_active_collaboration_invite_record(invite):
            _send_json(
                self,
                410,
                _build_error("expired_invite", "Collaboration invite is no longer active."),
            )
            return

        current_thread = get_thread(invite["workspaceId"], invite["messageId"])
        if current_thread is None:
            _send_json(
                self,
                404,
                _build_error("thread_not_found", "Canonical collaboration thread was not found."),
            )
            return

        participant_index = next(
            (
                index
                for index, candidate in enumerate(current_thread["collaboration"].get("participants", []))
                if candidate.get("email", "").lower() == invite["inviteeEmail"]
                and candidate.get("externalReviewToken", "") == invite["token"]
            ),
            -1,
        )
        if participant_index < 0:
            _send_json(
                self,
                404,
                _build_error("invalid_invite", "Collaboration invite is no longer linked to this thread."),
            )
            return

        action_type = str(action.get("type") or "").strip()
        if action_type != "reply":
            _send_json(
                self,
                400,
                _build_error("invalid_request", "Unsupported collaboration invite action."),
            )
            return

        text = str(action.get("text") or "").strip()
        if not text:
            _send_json(
                self,
                400,
                _build_error("invalid_request", "Reply action requires text."),
            )
            return

        expected_updated_at = _normalize_expected_updated_at(payload.get("expectedUpdatedAt"))
        next_timestamp = int(time() * 1000)
        participant = current_thread["collaboration"]["participants"][participant_index]
        author_name = str(action.get("authorName") or "").strip() or participant.get("name") or invite["inviteeEmail"]
        author_id = str(participant.get("id") or "").strip() or invite["participantId"]

        next_participants = [
            {
                **candidate,
                "status": "active" if index == participant_index else candidate.get("status", "active"),
            }
            for index, candidate in enumerate(current_thread["collaboration"]["participants"])
        ]

        next_thread = {
            **current_thread,
            "isShared": True,
            "collaboration": {
                **current_thread["collaboration"],
                "state": (
                    "needs_review"
                    if current_thread["collaboration"]["state"] == "resolved"
                    else current_thread["collaboration"]["state"]
                ),
                "updatedAt": next_timestamp,
                "previewText": text,
                "participants": next_participants,
                "messages": [
                    *current_thread["collaboration"]["messages"],
                    {
                        "id": f"{invite['messageId']}-invite-reply-{next_timestamp}",
                        "authorId": author_id,
                        "authorName": author_name,
                        "text": text,
                        "timestamp": next_timestamp,
                        "visibility": "shared",
                        "mentions": _normalize_mentions(action.get("mentions")),
                    },
                ],
            },
        }
        next_thread["collaboration"].pop("resolvedAt", None)
        next_thread["collaboration"].pop("resolvedByUserId", None)
        next_thread["collaboration"].pop("resolvedByUserName", None)

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
                    "thread": _build_external_thread_payload(saved_thread),
                },
            )
            return

        if error or saved_thread is None:
            _send_json(
                self,
                503,
                _build_error(
                    error["code"] if error else "collaboration_store_unavailable",
                    error["message"] if error else "Could not mutate collaboration invite thread.",
                ),
            )
            return

        _send_json(
            self,
            200,
            {
                "ok": True,
                "thread": _build_external_thread_payload(saved_thread),
            },
        )

    def do_GET(self):
        _send_json(
            self,
            405,
            _build_error("method_not_allowed", "Use POST to mutate a collaboration invite."),
        )

    def log_message(self, format, *args):
        return
