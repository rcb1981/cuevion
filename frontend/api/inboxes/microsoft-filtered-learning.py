"""Same-origin owner-only Microsoft Filtered/Learning preference endpoint."""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler

from api.auth.http import (
    HttpBoundaryError, get_unique_header, json_response, require_canonical_host,
    require_same_origin, send_public_response, snapshot_request_headers,
)
from api.inboxes.microsoft_filtered_learning import (
    MicrosoftFilteredLearningError, apply_owned_learning_action,
)

MAX_BODY_BYTES = 65536


def _failure(status: int, code: str):
    messages = {
        "invalid_learning": "Invalid Microsoft Learning preference.",
        "learning_limit_reached": "Too many Microsoft Learning senders.",
        "learning_conflict": "Microsoft Learning changed. Please try again.",
        "microsoft_unauthorized": "Please sign in again.",
        "microsoft_mailbox_not_found": "Microsoft mailbox not found.",
        "microsoft_reconnect_required": "Reconnect the Microsoft mailbox.",
        "learning_unavailable": "Microsoft Learning storage is temporarily unavailable.",
    }
    return json_response(status, {"ok": False, "error": {
        "code": code, "message": messages.get(code, messages["learning_unavailable"]),
    }}, extra_headers=(("Allow", "POST"),) if status == 405 else ())


def learning_response(request):
    if getattr(request, "command", "") != "POST":
        return _failure(405, "invalid_learning")
    try:
        headers = snapshot_request_headers(request)
        require_canonical_host(headers)
        require_same_origin(headers)
        if get_unique_header(headers, "transfer-encoding") is not None:
            raise ValueError
        content_type = get_unique_header(headers, "content-type", required=True)
        length = get_unique_header(headers, "content-length", required=True)
        if (content_type not in ("application/json", "application/json; charset=utf-8") or
                type(length) is not str or re.fullmatch(r"[1-9][0-9]{0,4}", length) is None):
            raise ValueError
        size = int(length)
        if size > MAX_BODY_BYTES:
            return _failure(413, "invalid_learning")
        data = request.rfile.read(size)
        if type(data) is not bytes or len(data) != size:
            raise ValueError
        payload = json.loads(data.decode("utf-8"))
        if type(payload) is not dict:
            raise ValueError
    except HttpBoundaryError as error:
        return _failure(error.status, "invalid_learning")
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, OSError, TypeError):
        return _failure(400, "invalid_learning")
    try:
        return json_response(200, apply_owned_learning_action(headers, payload))
    except MicrosoftFilteredLearningError as error:
        status = (400 if error.code == "invalid_learning" else
                  401 if error.code == "microsoft_unauthorized" else
                  404 if error.code == "microsoft_mailbox_not_found" else
                  409 if error.code in ("learning_limit_reached", "learning_conflict", "microsoft_reconnect_required")
                  else 503)
        return _failure(status, error.code)
    except Exception:
        return _failure(503, "learning_unavailable")


class handler(BaseHTTPRequestHandler):
    def _respond(self):
        send_public_response(self, learning_response(self))

    do_POST = _respond
    do_GET = _respond
    do_HEAD = _respond
    do_PUT = _respond
    do_PATCH = _respond
    do_DELETE = _respond
    do_OPTIONS = _respond

    def log_message(self, _format, *_args):
        return
