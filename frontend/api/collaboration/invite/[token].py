import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

CURRENT_DIR = Path(__file__).resolve().parent
COLLABORATION_DIR = CURRENT_DIR.parent
if str(COLLABORATION_DIR) not in sys.path:
    sys.path.insert(0, str(COLLABORATION_DIR))

from models import (
    build_external_collaboration_thread_view,
    is_active_collaboration_invite_record,
)
from redis_store import get_invite, get_thread


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
    if not path.startswith(invite_prefix):
        return ""

    token = path[len(invite_prefix) :]
    if "/" in token:
        token = token.split("/", 1)[0]

    return unquote(token).strip()


def _resolve_viewer(handler: BaseHTTPRequestHandler) -> str:
    query = parse_qs(urlsplit(handler.path).query)
    viewer = str((query.get("viewer") or ["workspace"])[0] or "").strip().lower()
    return "external" if viewer == "external" else "workspace"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        token = _parse_token(self)
        if not token:
            _send_json(
                self,
                400,
                _build_error("invalid_request", "Invite token is required."),
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

        thread = get_thread(invite["workspaceId"], invite["messageId"])
        if thread is None:
            _send_json(
                self,
                404,
                _build_error("thread_not_found", "Canonical collaboration thread was not found."),
            )
            return

        participant = next(
            (
                candidate
                for candidate in thread["collaboration"].get("participants", [])
                if candidate.get("email", "").lower() == invite["inviteeEmail"]
                and candidate.get("externalReviewToken", "") == invite["token"]
            ),
            None,
        )
        if participant is None:
            _send_json(
                self,
                404,
                _build_error("invalid_invite", "Collaboration invite is no longer linked to this thread."),
            )
            return

        viewer = _resolve_viewer(self)
        response_thread = (
            build_external_collaboration_thread_view(thread)
            if viewer == "external"
            else thread
        )
        if response_thread is None:
            _send_json(
                self,
                404,
                _build_error("thread_not_found", "Canonical collaboration thread was not found."),
            )
            return

        _send_json(
            self,
            200,
            {
                "ok": True,
                "invite": invite,
                "thread": response_thread,
            },
        )

    def do_POST(self):
        _send_json(
            self,
            405,
            _build_error("method_not_allowed", "Use GET to read a collaboration invite."),
        )

    def log_message(self, format, *args):
        return
