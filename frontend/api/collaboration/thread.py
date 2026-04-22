import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from time import time
from urllib.parse import parse_qs, urlsplit

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from models import (
    COLLABORATION_THREAD_SCHEMA_VERSION,
    normalize_collaboration_mention_record,
    normalize_collaboration_participant_record,
    normalize_collaboration_record,
    normalize_collaboration_thread_record,
    normalize_source_message_snapshot,
)
from redis_store import (
    MAX_COLLABORATION_THREAD_BATCH_SIZE,
    create_thread_if_missing,
    get_thread,
    get_threads_many,
    save_thread_if_expected,
)


def _send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


def _build_error(code: str, message: str, extra: dict | None = None) -> dict:
    payload = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
    if extra:
        payload.update(extra)
    return payload


def _get_operation(handler: BaseHTTPRequestHandler) -> str:
    query = parse_qs(urlsplit(handler.path).query)
    return str((query.get("op") or [""])[0] or "").strip().lower()


def _read_json_body(handler: BaseHTTPRequestHandler) -> tuple[dict | None, dict | None]:
    content_length = int(handler.headers.get("content-length", "0"))
    raw_body = handler.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

    try:
        payload = json.loads(raw_body or "{}")
    except json.JSONDecodeError:
        return None, _build_error("invalid_request", "Request body must be valid JSON.")

    if not isinstance(payload, dict):
        return None, _build_error("invalid_request", "Request body must be a JSON object.")

    return payload, None


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


def _handle_get_many(handler: BaseHTTPRequestHandler, payload: dict):
    workspace_id = str(payload.get("workspaceId") or "").strip().lower()
    mailbox_id = payload.get("mailboxId")
    message_ids = payload.get("messageIds")

    if not workspace_id:
        _send_json(handler, 400, _build_error("invalid_request", "workspaceId is required.", {"threadsByMessageId": {}}))
        return

    if mailbox_id is not None and not isinstance(mailbox_id, str):
        _send_json(
            handler,
            400,
            _build_error("invalid_request", "mailboxId must be a string when provided.", {"threadsByMessageId": {}}),
        )
        return

    if not isinstance(message_ids, list):
        _send_json(
            handler,
            400,
            _build_error("invalid_request", "messageIds must be an array of strings.", {"threadsByMessageId": {}}),
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
    _send_json(handler, 200, {"ok": True, "threadsByMessageId": threads_by_message_id})


def _handle_create(handler: BaseHTTPRequestHandler, payload: dict):
    workspace_id = str(payload.get("workspaceId") or "").strip().lower()
    mailbox_id = str(payload.get("mailboxId") or "").strip()
    source_message = normalize_source_message_snapshot(payload.get("sourceMessage"))
    collaboration = normalize_collaboration_record(payload.get("collaboration"))
    is_shared = payload.get("isShared") is True

    if not workspace_id or not mailbox_id or source_message is None or collaboration is None:
        _send_json(
            handler,
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
            handler,
            400,
            _build_error("invalid_request", "Canonical collaboration thread payload is invalid."),
        )
        return

    saved_thread, error = create_thread_if_missing(thread_record)
    if error or saved_thread is None:
        _send_json(
            handler,
            503,
            _build_error(
                error["code"] if error else "collaboration_store_unavailable",
                error["message"] if error else "Could not save canonical collaboration thread.",
            ),
        )
        return

    _send_json(handler, 200, {"ok": True, "thread": saved_thread})


def _handle_action(handler: BaseHTTPRequestHandler, payload: dict):
    workspace_id = str(payload.get("workspaceId") or "").strip().lower()
    message_id = str(payload.get("messageId") or "").strip()
    expected_updated_at = _normalize_expected_updated_at(payload.get("expectedUpdatedAt"))
    action = payload.get("action")

    if not workspace_id or not message_id or not isinstance(action, dict):
        _send_json(
            handler,
            400,
            _build_error("invalid_request", "workspaceId, messageId, and action are required."),
        )
        return

    current_thread = get_thread(workspace_id, message_id)
    if current_thread is None:
        _send_json(
            handler,
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
                handler,
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
                handler,
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
                handler,
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
        _send_json(handler, 400, _build_error("invalid_request", "Unsupported collaboration action."))
        return

    normalized_next_thread = normalize_collaboration_thread_record(next_thread)
    if normalized_next_thread is None:
        _send_json(
            handler,
            400,
            _build_error("invalid_request", "Canonical collaboration mutation payload is invalid."),
        )
        return

    saved_thread, error = save_thread_if_expected(
        normalized_next_thread,
        expected_updated_at=expected_updated_at,
    )
    if error and error.get("code") == "stale_thread" and saved_thread is not None:
        _send_json(handler, 409, {"ok": False, "code": "stale_thread", "thread": saved_thread})
        return

    if error or saved_thread is None:
        _send_json(
            handler,
            503,
            _build_error(
                error["code"] if error else "collaboration_store_unavailable",
                error["message"] if error else "Could not mutate canonical collaboration thread.",
            ),
        )
        return

    _send_json(handler, 200, {"ok": True, "thread": saved_thread})


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        operation = _get_operation(self)
        payload, error = _read_json_body(self)
        if error:
            status_code = 400
            if operation == "get-many":
                error["threadsByMessageId"] = {}
            _send_json(self, status_code, error)
            return

        if operation == "get-many":
            _handle_get_many(self, payload or {})
            return

        if operation == "create":
            _handle_create(self, payload or {})
            return

        if operation == "action":
            _handle_action(self, payload or {})
            return

        _send_json(
            self,
            404,
            _build_error("invalid_request", "Unsupported collaboration thread operation."),
        )

    def do_GET(self):
        _send_json(
            self,
            405,
            _build_error("method_not_allowed", "Use POST with a collaboration thread operation."),
        )

    def log_message(self, format, *args):
        return
