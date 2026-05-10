import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from time import time
from urllib.parse import parse_qs, urlsplit

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from models import (
    build_external_collaboration_thread_view,
    is_active_collaboration_invite_record,
    normalize_collaboration_mention_record,
    normalize_collaboration_participant_record,
    normalize_collaboration_thread_record,
)
from redis_store import get_invite, get_thread, issue_invite_for_thread, save_thread_if_expected


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


def _get_query(handler: BaseHTTPRequestHandler) -> dict[str, list[str]]:
    return parse_qs(urlsplit(handler.path).query)


def _get_operation(handler: BaseHTTPRequestHandler) -> str:
    return str((_get_query(handler).get("op") or [""])[0] or "").strip().lower()


def _get_token(handler: BaseHTTPRequestHandler) -> str:
    return str((_get_query(handler).get("token") or [""])[0] or "").strip()


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


def _build_invite_url(handler: BaseHTTPRequestHandler, *, message_id: str, invitee_email: str, token: str) -> str:
    forwarded_proto = str(handler.headers.get("x-forwarded-proto") or "").strip()
    forwarded_host = str(handler.headers.get("x-forwarded-host") or "").strip()
    host = forwarded_host or str(handler.headers.get("host") or "").strip()
    scheme = forwarded_proto or ("http" if host.startswith("localhost") or host.startswith("127.0.0.1") else "https")

    origin = f"{scheme}://{host}" if host else ""
    if not origin:
        return f"/?external_review={token}&message_id={message_id}&invitee={invitee_email}"

    return f"{origin}/?external_review={token}&message_id={message_id}&invitee={invitee_email}"


def _resolve_viewer(handler: BaseHTTPRequestHandler) -> str:
    viewer = str((_get_query(handler).get("viewer") or ["workspace"])[0] or "").strip().lower()
    return "external" if viewer == "external" else "workspace"


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


def _participant_identity_keys(participant: dict) -> list[str]:
    return [
        key
        for key in (
            str(participant.get("email") or "").strip().lower(),
            str(participant.get("id") or "").strip().lower(),
        )
        if key
    ]


def _merge_invite_participant(
    current_participants: list[dict],
    next_participant: dict,
) -> list[dict]:
    next_keys = set(_participant_identity_keys(next_participant))
    existing_index = next(
        (
            index
            for index, participant in enumerate(current_participants)
            if next_keys.intersection(_participant_identity_keys(participant))
        ),
        -1,
    )
    if existing_index < 0:
        return [*current_participants, next_participant]

    existing_participant = current_participants[existing_index]
    merged_participant = {**existing_participant, **next_participant}
    return [
        merged_participant if index == existing_index else participant
        for index, participant in enumerate(current_participants)
    ]


def _find_invite_participant(thread: dict, invite: dict) -> tuple[int, dict | None]:
    fallback_index = -1
    fallback_participant = None

    for index, candidate in enumerate(thread["collaboration"].get("participants", [])):
        if candidate.get("email", "").lower() != invite["inviteeEmail"]:
            continue

        external_review_token = candidate.get("externalReviewToken", "")
        if external_review_token == invite["token"]:
            return index, candidate

        if not external_review_token and fallback_participant is None:
            fallback_index = index
            fallback_participant = candidate

    return fallback_index, fallback_participant


def _resolve_active_invite_and_thread(token: str) -> tuple[dict | None, dict | None, dict | None]:
    invite = get_invite(token)
    if invite is None:
        return None, None, _build_error("invalid_invite", "Collaboration invite was not found.")

    if not is_active_collaboration_invite_record(invite):
        return invite, None, _build_error("expired_invite", "Collaboration invite is no longer active.")

    thread = get_thread(invite["workspaceId"], invite["messageId"])
    if thread is None:
        return invite, None, _build_error("thread_not_found", "Canonical collaboration thread was not found.")

    return invite, thread, None


def _handle_issue(handler: BaseHTTPRequestHandler, payload: dict):
    workspace_id = str(payload.get("workspaceId") or "").strip().lower()
    mailbox_id = str(payload.get("mailboxId") or "").strip()
    message_id = str(payload.get("messageId") or "").strip()
    invitee_email = str(payload.get("inviteeEmail") or "").strip().lower()
    created_by_user_id = str(payload.get("createdByUserId") or "").strip()
    created_by_user_name = str(payload.get("createdByUserName") or "").strip()

    if (
        not workspace_id
        or not mailbox_id
        or not message_id
        or not invitee_email
        or not created_by_user_id
        or not created_by_user_name
    ):
        _send_json(
            handler,
            400,
            _build_error(
                "invalid_request",
                "workspaceId, mailboxId, messageId, inviteeEmail, createdByUserId, and createdByUserName are required.",
            ),
        )
        return

    current_thread = get_thread(workspace_id, message_id)
    if current_thread is None:
        current_thread = normalize_collaboration_thread_record(
            {
                "v": 1,
                "workspaceId": workspace_id,
                "mailboxId": mailbox_id,
                "messageId": message_id,
                "sourceMessage": payload.get("sourceMessage"),
                "isShared": payload.get("isShared"),
                "collaboration": payload.get("collaboration"),
            }
        )
        if current_thread is None:
            _send_json(handler, 404, _build_error("thread_not_found", "Canonical collaboration thread was not found."))
            return

    current_participants = current_thread["collaboration"].get("participants", [])
    existing_participant = next(
        (
            participant
            for participant in current_participants
            if participant.get("email", "").lower() == invitee_email
        ),
        None,
    )

    participant_payload = normalize_collaboration_participant_record(
        existing_participant
        or {
            "id": invitee_email,
            "name": invitee_email.split("@")[0] or invitee_email,
            "email": invitee_email,
            "kind": "external",
            "status": "invited",
        }
    )
    if participant_payload is None:
        _send_json(handler, 400, _build_error("invalid_request", "Could not prepare invite participant."))
        return

    now_ms = max(
        int(datetime.now(timezone.utc).timestamp() * 1000),
        current_thread["collaboration"]["updatedAt"] + 1,
    )
    invite_record, invite_error = issue_invite_for_thread(
        workspace_id=workspace_id,
        mailbox_id=mailbox_id,
        message_id=message_id,
        invitee_email=invitee_email,
        participant_id=participant_payload["id"],
        created_by_user_id=created_by_user_id,
        created_by_user_name=created_by_user_name,
        created_at=now_ms,
        updated_at=now_ms,
    )
    if invite_error or invite_record is None:
        _send_json(
            handler,
            503,
            _build_error(
                invite_error["code"] if invite_error else "collaboration_store_unavailable",
                invite_error["message"] if invite_error else "Could not issue collaboration invite.",
            ),
        )
        return

    next_participant = {
        **participant_payload,
        "kind": "external",
        "status": (
            "invited"
            if participant_payload["status"] == "declined"
            else participant_payload["status"]
        ),
        "externalReviewToken": invite_record["token"],
    }

    next_participants = _merge_invite_participant(current_participants, next_participant)

    next_thread = {
        **current_thread,
        "collaboration": {
            **current_thread["collaboration"],
            "updatedAt": max(invite_record["updatedAt"], current_thread["collaboration"]["updatedAt"] + 1),
            "participants": next_participants,
        },
        "isShared": True,
    }

    normalized_next_thread = normalize_collaboration_thread_record(next_thread)
    if normalized_next_thread is None:
        _send_json(
            handler,
            400,
            _build_error("invalid_request", "Canonical thread payload is invalid after invite issuance."),
        )
        return

    saved_thread, thread_error = save_thread_if_expected(
        normalized_next_thread,
        expected_updated_at=current_thread["collaboration"]["updatedAt"],
        preserve_existing_participants=True,
    )
    if thread_error and thread_error.get("code") == "stale_thread" and saved_thread is not None:
        latest_thread = saved_thread
        latest_next_thread = {
            **latest_thread,
            "collaboration": {
                **latest_thread["collaboration"],
                "updatedAt": max(invite_record["updatedAt"], latest_thread["collaboration"]["updatedAt"] + 1),
                "participants": _merge_invite_participant(
                    latest_thread["collaboration"].get("participants", []),
                    next_participant,
                ),
            },
            "isShared": True,
        }
        normalized_latest_next_thread = normalize_collaboration_thread_record(latest_next_thread)
        if normalized_latest_next_thread is None:
            _send_json(
                handler,
                400,
                _build_error("invalid_request", "Canonical thread payload is invalid after invite retry."),
            )
            return
        saved_thread, thread_error = save_thread_if_expected(
            normalized_latest_next_thread,
            expected_updated_at=latest_thread["collaboration"]["updatedAt"],
            preserve_existing_participants=True,
        )

    if thread_error or saved_thread is None:
        _send_json(
            handler,
            503,
            _build_error(
                thread_error["code"] if thread_error else "collaboration_store_unavailable",
                thread_error["message"] if thread_error else "Could not persist canonical collaboration thread.",
            ),
        )
        return

    participant_index, participant = _find_invite_participant(saved_thread, invite_record)
    if participant_index < 0 or participant is None:
        _send_json(
            handler,
            503,
            _build_error("collaboration_store_unavailable", "Could not link invite to canonical collaboration thread."),
        )
        return

    _send_json(
        handler,
        200,
        {
            "ok": True,
            "invite": invite_record,
            "thread": saved_thread,
            "inviteUrl": _build_invite_url(
                handler,
                message_id=message_id,
                invitee_email=invitee_email,
                token=invite_record["token"],
            ),
        },
    )


def _handle_lookup(handler: BaseHTTPRequestHandler):
    token = _get_token(handler)
    if not token:
        _send_json(handler, 400, _build_error("invalid_request", "Invite token is required."))
        return

    invite, thread, error = _resolve_active_invite_and_thread(token)
    if error:
        status_code = 410 if error["error"]["code"] == "expired_invite" else 404
        if error["error"]["code"] == "invalid_request":
            status_code = 400
        _send_json(handler, status_code, error)
        return

    if thread["collaboration"]["state"] == "resolved":
        _send_json(
            handler,
            404,
            _build_error("unavailable", "Collaboration invite is no longer available."),
        )
        return

    _, participant = _find_invite_participant(thread, invite)
    if participant is None:
        _send_json(
            handler,
            404,
            _build_error("invalid_invite", "Collaboration invite is no longer linked to this thread."),
        )
        return

    viewer = _resolve_viewer(handler)
    response_thread = _build_external_thread_payload(thread) if viewer == "external" else thread
    if response_thread is None:
        _send_json(handler, 404, _build_error("thread_not_found", "Canonical collaboration thread was not found."))
        return

    _send_json(handler, 200, {"ok": True, "invite": invite, "thread": response_thread})


def _handle_action(handler: BaseHTTPRequestHandler, payload: dict):
    token = _get_token(handler)
    if not token:
        _send_json(handler, 400, _build_error("invalid_request", "Invite token is required."))
        return

    action = payload.get("action")
    if not isinstance(action, dict):
        _send_json(handler, 400, _build_error("invalid_request", "action is required."))
        return

    invite, current_thread, error = _resolve_active_invite_and_thread(token)
    if error:
        status_code = 410 if error["error"]["code"] == "expired_invite" else 404
        _send_json(handler, status_code, error)
        return

    if current_thread["collaboration"]["state"] == "resolved":
        _send_json(
            handler,
            404,
            _build_error("unavailable", "Collaboration invite is no longer available."),
        )
        return

    participant_index, participant = _find_invite_participant(current_thread, invite)
    if participant_index < 0 or participant is None:
        _send_json(
            handler,
            404,
            _build_error("invalid_invite", "Collaboration invite is no longer linked to this thread."),
        )
        return

    action_type = str(action.get("type") or "").strip()
    if action_type != "reply":
        _send_json(handler, 400, _build_error("invalid_request", "Unsupported collaboration invite action."))
        return

    text = str(action.get("text") or "").strip()
    if not text:
        _send_json(handler, 400, _build_error("invalid_request", "Reply action requires text."))
        return

    expected_updated_at = _normalize_expected_updated_at(payload.get("expectedUpdatedAt"))
    next_timestamp = max(int(time() * 1000), current_thread["collaboration"]["updatedAt"] + 1)
    author_name = str(action.get("authorName") or "").strip() or participant.get("name") or invite["inviteeEmail"]
    author_id = str(participant.get("id") or "").strip() or invite["participantId"]

    next_participants = [
        {
            **candidate,
            "status": "active" if index == participant_index else candidate.get("status", "active"),
            **(
                {"externalReviewToken": invite["token"]}
                if index == participant_index and not candidate.get("externalReviewToken")
                else {}
            ),
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
            handler,
            400,
            _build_error("invalid_request", "Canonical collaboration mutation payload is invalid."),
        )
        return

    saved_thread, save_error = save_thread_if_expected(
        normalized_next_thread,
        expected_updated_at=expected_updated_at,
        preserve_existing_participants=True,
    )
    if save_error and save_error.get("code") == "stale_thread" and saved_thread is not None:
        _send_json(
            handler,
            409,
            {"ok": False, "code": "stale_thread", "thread": _build_external_thread_payload(saved_thread)},
        )
        return

    if save_error or saved_thread is None:
        _send_json(
            handler,
            503,
            _build_error(
                save_error["code"] if save_error else "collaboration_store_unavailable",
                save_error["message"] if save_error else "Could not mutate collaboration invite thread.",
            ),
        )
        return

    _send_json(handler, 200, {"ok": True, "thread": _build_external_thread_payload(saved_thread)})


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if _get_operation(self) != "lookup":
            _send_json(self, 404, _build_error("invalid_request", "Unsupported collaboration invite operation."))
            return
        _handle_lookup(self)

    def do_POST(self):
        operation = _get_operation(self)
        payload, error = _read_json_body(self)
        if error:
            _send_json(self, 400, error)
            return

        if operation == "issue":
            _handle_issue(self, payload or {})
            return

        if operation == "action":
            _handle_action(self, payload or {})
            return

        _send_json(self, 404, _build_error("invalid_request", "Unsupported collaboration invite operation."))

    def log_message(self, format, *args):
        return
