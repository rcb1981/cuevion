"""POST-only same-origin status check of one owner-bound Microsoft send.

Read-only: this endpoint never sends, retries, or creates a message. An HTTP
202 from a prior send is not proof of Sent copy or recipient delivery.
"""
from __future__ import annotations

import json
import re

from api.auth.http import (
    HttpBoundaryError, get_unique_header, json_response,
    snapshot_request_headers,
)
from api.inboxes.microsoft_preview_origin import require_microsoft_request_origin
from api.inboxes import microsoft_sent_status_service as service
from api.inboxes import microsoft_outbound_ledger as ledger
from api.inboxes.microsoft_mailbox_access import MicrosoftMailboxAccessError
from api.inboxes.microsoft_graph import MicrosoftGraphError

MAX_REQUEST_BYTES = 4096
_MAILBOX_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$")
_ACCEPTED_STATES = frozenset((
    "sent_copy_confirmed", "sent_copy_pending", "send_not_started",
))
_ERRORS = {
    "invalid_request": "Invalid Microsoft send-status request.",
    "method_not_allowed": "Use POST to check Microsoft sending status.",
    "request_too_large": "Microsoft send-status request is too large.",
    "microsoft_unauthorized": "Please sign in again.",
    "microsoft_mailbox_not_found": "Microsoft mailbox not found.",
    "microsoft_reconnect_required": "Reconnect the Microsoft mailbox to continue.",
    "microsoft_send_attempt_not_found": "This send request is not available.",
    "microsoft_send_attempt_conflict": "This send request cannot be verified.",
    "microsoft_send_store_unavailable": "Secure send status is temporarily unavailable.",
    "microsoft_graph_rate_limited": "Microsoft is temporarily limiting requests.",
    "microsoft_sent_copy_unavailable": "Microsoft could not verify Sent items right now.",
    "microsoft_mailbox_unavailable": "Microsoft mailbox status is temporarily unavailable.",
}


def _error(status: int, code: str):
    selected = code if code in _ERRORS else "microsoft_mailbox_unavailable"
    return json_response(
        status,
        {"ok": False, "error": {"code": selected, "message": _ERRORS[selected]}},
        extra_headers=(("Allow", "POST"),) if status == 405 else (),
    )


def _read(request, headers: tuple) -> dict:
    if get_unique_header(headers, "transfer-encoding") is not None:
        raise ValueError("invalid")
    content_type = get_unique_header(headers, "content-type", required=True)
    if content_type not in ("application/json", "application/json; charset=utf-8"):
        raise ValueError("invalid")
    size_value = get_unique_header(headers, "content-length", required=True)
    if type(size_value) is not str or re.fullmatch(r"[1-9][0-9]{0,3}", size_value) is None:
        raise ValueError("invalid")
    size = int(size_value)
    if size > MAX_REQUEST_BYTES:
        raise OverflowError
    content = request.rfile.read(size)
    if type(content) is not bytes or len(content) != size:
        raise ValueError("invalid")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid") from None
    if (
        type(payload) is not dict
        or set(payload) != {"mailboxId", "attemptId"}
        or type(payload["mailboxId"]) is not str
        or _MAILBOX_ID.fullmatch(payload["mailboxId"]) is None
        or type(payload["attemptId"]) is not str
        or ledger.ATTEMPT_ID.fullmatch(payload["attemptId"]) is None
    ):
        raise ValueError("invalid")
    return payload


def microsoft_sent_status_response(request):
    if getattr(request, "command", "") != "POST":
        return _error(405, "method_not_allowed")
    try:
        headers = snapshot_request_headers(request)
        require_microsoft_request_origin(headers)
        body = _read(request, headers)
    except HttpBoundaryError as error:
        return _error(error.status, "invalid_request")
    except OverflowError:
        return _error(413, "request_too_large")
    except (ValueError, TypeError, OSError):
        return _error(400, "invalid_request")

    try:
        result = service.check_owned_microsoft_sent_status(
            headers, body["mailboxId"], attempt_id=body["attemptId"],
        )
        if (
            type(result) is not dict
            or result.get("attemptId") != body["attemptId"]
            or result.get("status") not in _ACCEPTED_STATES
            or type(result.get("sentCopyConfirmed")) is not bool
            or result.get("sentCopyConfirmed") is not (
                result["status"] == "sent_copy_confirmed"
            )
            or result.get("deliveryConfirmed") is not False
        ):
            return _error(503, "microsoft_sent_copy_unavailable")
        return json_response(200, {"ok": True, **result})
    except service.MicrosoftSentStatusError as error:
        code = error.code
        return _error(
            404 if code == "microsoft_send_attempt_not_found"
            else 409 if code == "microsoft_send_attempt_conflict"
            else 400 if code == "microsoft_send_attempt_invalid"
            else 503, code,
        )
    except MicrosoftMailboxAccessError as error:
        code = error.code
        return _error(
            401 if code == "microsoft_unauthorized"
            else 404 if code == "microsoft_mailbox_not_found"
            else 409 if code in {"microsoft_reconnect_required","microsoft_token_record_invalid"}
            else 503,
            code,
        )
    except MicrosoftGraphError as error:
        code = error.code
        return _error(
            409 if code == "microsoft_reconnect_required"
            else 429 if code == "microsoft_graph_rate_limited"
            else 503, code,
        )
    except Exception:
        return _error(503, "microsoft_mailbox_unavailable")
