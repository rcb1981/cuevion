"""Minimal read-only, owner-authorized Microsoft Graph Inbox HTTP boundary.

No Graph URL, token, owner address, Graph OData paging URL or arbitrary
operation is accepted from the browser. Only POST is supported.
"""
from __future__ import annotations

import json
import re

from api.auth.http import (
    HttpBoundaryError,
    get_unique_header,
    json_response,
    snapshot_request_headers,
)
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.microsoft_production_pilot import production_pilot_writes_blocked
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.microsoft_inbox_projection import (project_inbox, project_message, project_sent, project_sent_message)

MAX_REQUEST_BYTES = 4096
_MAX_MAILBOX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$")


def _error(status: int, code: str):
    descriptions = {
        "invalid_request": "Invalid Microsoft Inbox request.",
        "request_too_large": "Microsoft Inbox request is too large.",
        "method_not_allowed": "Use POST for Microsoft Inbox requests.",
        "microsoft_pilot_read_only": "Microsoft mailbox changes are disabled during the read-only pilot.",
        "microsoft_unauthorized": "Please sign in again.",
        "microsoft_mailbox_not_found": "Microsoft mailbox not found.",
        "microsoft_reconnect_required": "Reconnect the Microsoft mailbox to continue.",
        "microsoft_graph_rate_limited": "Microsoft mailbox is temporarily rate limited.",
        "microsoft_write_permission_required": "Reconnect Microsoft to approve message changes.",
        "microsoft_mutation_in_progress": "Another mailbox change is still in progress.",
        "microsoft_mutation_conflict": "Microsoft message changed. Refresh the Inbox and try again.",
        "microsoft_mutation_unconfirmed": "The message change could not be confirmed. Refresh Inbox.",
        "microsoft_message_not_found": "The message is no longer in this Inbox.",
    }
    return json_response(
        status,
        {
            "ok": False,
            "error": {
                "code": code,
                "message": descriptions.get(code, "Microsoft mailbox is temporarily unavailable."),
            },
        },
        extra_headers=(("Allow", "POST"),) if status == 405 else (),
    )


def _parse_body(request, headers: tuple) -> dict:
    if get_unique_header(headers, "transfer-encoding") is not None:
        raise ValueError("invalid")
    content_type = get_unique_header(headers, "content-type", required=True)
    if content_type not in ("application/json", "application/json; charset=utf-8"):
        raise ValueError("invalid")
    length = get_unique_header(headers, "content-length", required=True)
    if not isinstance(length, str) or not re.fullmatch(r"[1-9][0-9]{0,3}", length):
        raise ValueError("invalid")
    size = int(length)
    if size > MAX_REQUEST_BYTES:
        raise OverflowError
    data = request.rfile.read(size)
    if type(data) is not bytes or len(data) != size:
        raise ValueError("invalid")
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid") from None
    if type(decoded) is not dict:
        raise ValueError("invalid")
    return decoded


def _validate_payload(payload: dict, *, mode: str) -> tuple[str, int | str | dict]:
    expected = (
        {"mailboxId", "limit", "page"} if mode in {"list", "sent"}
        else {"mailboxId", "providerMessageId", "unread"} if mode == "flag"
        else {"mailboxId", "providerMessageId"}
    )
    if not set(payload) <= expected or "mailboxId" not in payload:
        raise ValueError("invalid")
    mailbox = payload.get("mailboxId")
    if type(mailbox) is not str or _MAX_MAILBOX.fullmatch(mailbox) is None:
        raise ValueError("invalid")
    if mode in {"list", "sent"}:
        limit = payload.get("limit", 20)
        page = payload.get("page", 1)
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("invalid")
        if type(page) is not int or not 1 <= page <= 4:
            raise ValueError("invalid")
        return mailbox, limit if page == 1 else {"limit": limit, "page": page}
    if mode in {"exact", "sent_exact"}:
        message_id = payload.get("providerMessageId")
        if (
            type(message_id) is not str
            or re.fullmatch(r"[^\x00-\x20\x7f-\x9f?#]{1,512}", message_id) is None
            or len(message_id.encode("utf-8")) > 512
            or message_id == "."
            or ".." in message_id
        ):
            raise ValueError("invalid")
        return mailbox, message_id
    if mode == "flag":
        # Reuse Graph's immutable opaque-ID validation, not a browser path.
        from api.inboxes.microsoft_graph import _required_message_id
        value = payload.get("providerMessageId")
        try:
            exact_id = _required_message_id(value)
        except MicrosoftGraphError:
            raise ValueError("invalid") from None
        if set(payload) != expected or type(payload.get("unread")) is not bool:
            raise ValueError("invalid")
        return mailbox, {"providerMessageId": exact_id, "unread": payload["unread"]}
    raise ValueError("invalid")


def microsoft_inbox_response(request, *, mode: str):
    method = getattr(request, "command", "")
    if method != "POST":
        return _error(405, "method_not_allowed")
    if mode == "flag" and production_pilot_writes_blocked():
        # Explicitly deny read/unread changes before parsing, owner/token
        # resolution, Graph PATCH, and any other mailbox-side effects.
        return _error(403, "microsoft_pilot_read_only")

    try:
        headers = snapshot_request_headers(request)
        payload = _parse_body(request, headers)
        mailbox_id, selector = _validate_payload(payload, mode=mode)
    except HttpBoundaryError as error:
        return _error(error.status, "invalid_request")
    except OverflowError:
        return _error(413, "request_too_large")
    except (ValueError, TypeError, AttributeError, OSError):
        return _error(400, "invalid_request")

    try:
        if mode == "list":
            assert type(selector) is int or type(selector) is dict
            if type(selector) is dict:
                snapshot = owned.list_owned_microsoft_inbox(
                    headers, mailbox_id, page_size=selector["limit"], page=selector["page"],
                )
            else:
                snapshot = owned.list_owned_microsoft_inbox(
                    headers, mailbox_id, page_size=selector,
                )
            return json_response(200, project_inbox(snapshot, mailbox_id))
        if mode == "sent":
            assert type(selector) is int or type(selector) is dict
            if type(selector) is dict:
                snapshot = owned.list_owned_microsoft_sent(
                    headers, mailbox_id, page_size=selector["limit"], page=selector["page"],
                )
            else:
                snapshot = owned.list_owned_microsoft_sent(
                    headers, mailbox_id, page_size=selector,
                )
            return json_response(200, project_sent(snapshot, mailbox_id))
        if mode == "sent_exact":
            assert type(selector) is str
            message = owned.read_owned_microsoft_sent_message(
                headers, mailbox_id, selector,
            )
            if not isinstance(message, dict) or message.get("id") != selector:
                raise MicrosoftGraphError("microsoft_graph_invalid_response")
            return json_response(
                200, {"ok": True, "message": project_sent_message(
                    message, mailbox_id, exact_body=True,
                )},
            )
        if mode == "flag":
            assert type(selector) is dict
            confirmation = owned.mark_owned_microsoft_inbox_read(
                headers,
                mailbox_id,
                selector["providerMessageId"],
                unread=selector["unread"],
            )
            if (
                type(confirmation) is not dict
                or type(confirmation.get("providerMessageId")) is not str
                or confirmation["providerMessageId"] != selector["providerMessageId"]
                or confirmation.get("unread") is not selector["unread"]
                or type(confirmation.get("changed")) is not bool
            ):
                raise MicrosoftGraphError("microsoft_mutation_unconfirmed")
            return json_response(200, {"ok": True, **confirmation})
        if mode == "exact":
            assert type(selector) is str
            message = owned.read_owned_microsoft_message(
                headers, mailbox_id, selector,
            )
            if not isinstance(message, dict) or message.get("id") != selector:
                raise MicrosoftGraphError("microsoft_graph_invalid_response")
            return json_response(
                200,
                {"ok": True, "message": project_message(message, mailbox_id, exact_body=True)},
            )
    except owned.MicrosoftMailboxAccessError as error:
        code = error.code
        if code == "microsoft_unauthorized":
            return _error(401, code)
        if code == "microsoft_mailbox_not_found":
            return _error(404, code)
        if code in {"microsoft_reconnect_required", "microsoft_token_record_invalid"}:
            return _error(409, "microsoft_reconnect_required")
        if code == "microsoft_write_permission_required":
            return _error(403, code)
        if code == "microsoft_mutation_in_progress":
            return _error(409, code)
        if code == "microsoft_mutation_unconfirmed":
            return _error(503, code)
        return _error(503, "microsoft_mailbox_unavailable")
    except MicrosoftGraphError as error:
        if error.code == "microsoft_graph_rate_limited":
            return _error(429, error.code)
        if error.code == "microsoft_reconnect_required":
            return _error(409, "microsoft_reconnect_required")
        if error.code == "microsoft_graph_invalid_response":
            if mode == "list":
                stage = getattr(error, "safe_stage", None)
                if stage not in {"transport", "collection", "message", "pagination"}:
                    stage = "projection"
                # No mailbox identity, OAuth credential, Graph URL, message
                # metadata, or body ever enters these operational logs.
                print("microsoft_inbox_invalid_response_stage=" + stage, flush=True)
            return _error(502, "microsoft_graph_invalid_response")
        if error.code == "microsoft_message_not_found":
            return _error(404, error.code)
        if error.code == "microsoft_mutation_conflict":
            return _error(409, error.code)
        if error.code == "microsoft_mutation_unconfirmed":
            return _error(503, error.code)
        return _error(503, "microsoft_graph_unavailable")
    except Exception:
        return _error(503, "microsoft_mailbox_unavailable")
    return _error(400, "invalid_request")
