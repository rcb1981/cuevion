import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
COLLABORATION_DIR = CURRENT_DIR.parent
if str(COLLABORATION_DIR) not in sys.path:
    sys.path.insert(0, str(COLLABORATION_DIR))

from models import normalize_collaboration_participant_record, normalize_collaboration_thread_record
from redis_store import get_thread, issue_invite_for_thread, save_thread


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


def _build_invite_url(handler: BaseHTTPRequestHandler, *, message_id: str, invitee_email: str, token: str) -> str:
    forwarded_proto = str(handler.headers.get("x-forwarded-proto") or "").strip()
    forwarded_host = str(handler.headers.get("x-forwarded-host") or "").strip()
    host = forwarded_host or str(handler.headers.get("host") or "").strip()
    scheme = forwarded_proto or ("http" if host.startswith("localhost") or host.startswith("127.0.0.1") else "https")

    origin = f"{scheme}://{host}" if host else ""
    if not origin:
        return f"/?external_review={token}&message_id={message_id}&invitee={invitee_email}"

    return f"{origin}/?external_review={token}&message_id={message_id}&invitee={invitee_email}"


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
                self,
                400,
                _build_error(
                    "invalid_request",
                    "workspaceId, mailboxId, messageId, inviteeEmail, createdByUserId, and createdByUserName are required.",
                ),
            )
            return

        current_thread = get_thread(workspace_id, message_id)
        if current_thread is None:
            _send_json(
                self,
                404,
                _build_error("thread_not_found", "Canonical collaboration thread was not found."),
            )
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
            _send_json(
                self,
                400,
                _build_error("invalid_request", "Could not prepare invite participant."),
            )
            return

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
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
                self,
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

        existing_index = next(
            (
                index
                for index, participant in enumerate(current_participants)
                if participant.get("email", "").lower() == invitee_email
            ),
            -1,
        )
        if existing_index >= 0:
            next_participants = [
                next_participant if index == existing_index else participant
                for index, participant in enumerate(current_participants)
            ]
        else:
            next_participants = [*current_participants, next_participant]

        next_thread = {
            **current_thread,
            "collaboration": {
                **current_thread["collaboration"],
                "updatedAt": max(current_thread["collaboration"]["updatedAt"], invite_record["updatedAt"]),
                "participants": next_participants,
            },
            "isShared": True,
        }

        normalized_next_thread = normalize_collaboration_thread_record(next_thread)
        if normalized_next_thread is None:
            _send_json(
                self,
                400,
                _build_error("invalid_request", "Canonical thread payload is invalid after invite issuance."),
            )
            return

        saved_thread, thread_error = save_thread(normalized_next_thread)
        if thread_error or saved_thread is None:
            _send_json(
                self,
                503,
                _build_error(
                    thread_error["code"] if thread_error else "collaboration_store_unavailable",
                    thread_error["message"] if thread_error else "Could not persist canonical collaboration thread.",
                ),
            )
            return

        _send_json(
            self,
            200,
            {
                "ok": True,
                "invite": invite_record,
                "thread": saved_thread,
                "inviteUrl": _build_invite_url(
                    self,
                    message_id=message_id,
                    invitee_email=invitee_email,
                    token=invite_record["token"],
                ),
            },
        )

    def do_GET(self):
        _send_json(
            self,
            405,
            _build_error("method_not_allowed", "Use POST to issue a collaboration invite."),
        )

    def log_message(self, format, *args):
        return
