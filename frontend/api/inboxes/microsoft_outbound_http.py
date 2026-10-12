"""Strict same-origin owner-authorized Microsoft outbound HTTP boundary.

The browser selects a managed mailbox, supplies text/recipients or a verified
Inbox source ID, and an unpredictable attempt ID. No token, sender identity,
Graph URL, folder, HTML, or attachments can be supplied.
"""
from __future__ import annotations

import json
import re

from api.auth.http import (
    HttpBoundaryError, get_unique_header, json_response,
    snapshot_request_headers,
)
from api.inboxes.microsoft_preview_origin import require_microsoft_request_origin
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes import microsoft_outbound_service
from api.inboxes import microsoft_outbound_ledger
from api.inboxes import microsoft_outbound_graph
from api.inboxes.microsoft_mailbox_access import MicrosoftMailboxAccessError

MAX_REQUEST_BYTES = 128 * 1024
_MAILBOX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$")
_ERROR_DESCRIPTIONS = {
    "invalid_request": "Invalid Microsoft message request.",
    "request_too_large": "Message request exceeds the allowed size.",
    "method_not_allowed": "Use POST to send Microsoft messages.",
    "microsoft_unauthorized": "Please sign in again.",
    "microsoft_mailbox_not_found": "Microsoft mailbox not found.",
    "microsoft_reconnect_required": "Reconnect the Microsoft mailbox to continue.",
    "microsoft_write_permission_required": "Reconnect Microsoft to authorize draft creation.",
    "microsoft_send_permission_required": "Reconnect Microsoft to authorize sending.",
    "microsoft_mutation_in_progress": "Another mailbox operation is in progress.",
    "microsoft_send_attempt_already_started": "This request was already submitted. Check Sent before trying another message.",
    "microsoft_send_attempt_conflict": "This sending request cannot be reused.",
    "microsoft_send_attempt_invalid": "The sending request identifier is invalid.",
    "microsoft_send_store_unavailable": "Secure send tracking is temporarily unavailable.",
    "microsoft_graph_rate_limited": "Microsoft is temporarily limiting requests.",
    "microsoft_draft_unconfirmed": "Microsoft could not confirm the draft. Review Drafts before retrying.",
    "microsoft_send_unconfirmed": "Sending status is uncertain. Check Sent before trying again.",
    "microsoft_draft_conflict": "The Microsoft draft changed. Please review the mailbox.",
    "microsoft_reply_source_invalid": "The original Inbox message cannot be verified.",
    "microsoft_mailbox_unavailable": "Microsoft mailbox is temporarily unavailable.",
}


def _error(status: int, code: str):
    public_code = code if code in _ERROR_DESCRIPTIONS else "microsoft_mailbox_unavailable"
    return json_response(
        status,
        {"ok": False, "error": {
            "code": public_code, "message": _ERROR_DESCRIPTIONS[public_code],
        }},
        extra_headers=(("Allow", "POST"),) if status == 405 else (),
    )


def _read_body(request, headers: tuple) -> dict:
    if get_unique_header(headers, "transfer-encoding") is not None:
        raise ValueError("invalid")
    ct = get_unique_header(headers, "content-type", required=True)
    if ct not in ("application/json", "application/json; charset=utf-8"):
        raise ValueError("invalid")
    length = get_unique_header(headers, "content-length", required=True)
    if type(length) is not str or re.fullmatch(r"[1-9][0-9]{0,5}", length) is None:
        raise ValueError("invalid")
    size = int(length)
    if size > MAX_REQUEST_BYTES:
        raise OverflowError
    content = request.rfile.read(size)
    if type(content) is not bytes or len(content) != size:
        raise ValueError("invalid")
    try:
        result = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid") from None
    if type(result) is not dict or set(result) != {"mailboxId", "attemptId", "compose"}:
        raise ValueError("invalid")
    if (
        type(result["mailboxId"]) is not str
        or _MAILBOX.fullmatch(result["mailboxId"]) is None
        or type(result["attemptId"]) is not str
        or microsoft_outbound_ledger.ATTEMPT_ID.fullmatch(result["attemptId"]) is None
    ):
        raise ValueError("invalid")
    try:
        compose = microsoft_outbound_graph._validated_compose(result["compose"])
    except MicrosoftGraphError:
        raise ValueError("invalid") from None
    return {"mailboxId": result["mailboxId"], "attemptId": result["attemptId"], "compose": compose}


def microsoft_send_response(request):
    if getattr(request, "command", "") != "POST":
        return _error(405, "method_not_allowed")
    try:
        headers = snapshot_request_headers(request)
        # Sending is state changing; byte-exact canonical Origin/Host are
        # mandatory. Do not rely solely on session-cookie SameSite settings.
        require_microsoft_request_origin(headers)
        payload = _read_body(request, headers)
    except HttpBoundaryError as error:
        return _error(error.status, "invalid_request")
    except OverflowError:
        return _error(413, "request_too_large")
    except (ValueError, TypeError, OSError):
        return _error(400, "invalid_request")

    try:
        confirmation = microsoft_outbound_service.send_owned_microsoft_message(
            headers, payload["mailboxId"], attempt_id=payload["attemptId"],
            compose=payload["compose"],
        )
        if (
            type(confirmation) is not dict
            or confirmation.get("status") != "accepted_by_microsoft"
            or confirmation.get("attemptId") != payload["attemptId"]
            or confirmation.get("deliveryConfirmed") is not False
            or confirmation.get("sentCopyConfirmed") is not False
            or type(confirmation.get("replayed")) is not bool
        ):
            return _error(503, "microsoft_send_unconfirmed")
        return json_response(202, {"ok": True, **confirmation})
    except MicrosoftMailboxAccessError as error:
        code = error.code
        if code == "microsoft_unauthorized":
            return _error(401, code)
        if code == "microsoft_mailbox_not_found":
            return _error(404, code)
        if code in {"microsoft_reconnect_required", "microsoft_token_record_invalid"}:
            return _error(409, "microsoft_reconnect_required")
        if code in {"microsoft_write_permission_required","microsoft_send_permission_required"}:
            return _error(403, code)
        if code == "microsoft_mutation_in_progress":
            return _error(409, code)
        return _error(503, "microsoft_mailbox_unavailable")
    except microsoft_outbound_service.MicrosoftOutboundError as error:
        code = error.code
        if code in {"microsoft_send_attempt_invalid"}:
            return _error(400, code)
        if code in {"microsoft_mutation_in_progress", "microsoft_send_attempt_already_started", "microsoft_send_attempt_conflict"}:
            return _error(409, code)
        return _error(503, code)
    except MicrosoftGraphError as error:
        code = error.code
        if code == "microsoft_graph_rate_limited":
            return _error(429, code)
        if code == "microsoft_reconnect_required":
            return _error(409, code)
        if code in {"microsoft_draft_conflict", "microsoft_reply_source_invalid"}:
            return _error(409, code)
        if code in {"microsoft_draft_unconfirmed", "microsoft_send_unconfirmed"}:
            return _error(503, code)
        return _error(503, "microsoft_mailbox_unavailable")
    except Exception:
        return _error(503, "microsoft_mailbox_unavailable")
