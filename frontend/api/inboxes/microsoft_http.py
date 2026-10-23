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
    require_canonical_host,
    require_same_origin,
)
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.microsoft_production_pilot import (
    production_pilot_read_flags_blocked,
    production_pilot_mark_unread_blocked,
    production_pilot_archive_blocked,
    production_pilot_delete_blocked,
    production_pilot_draft_discard_blocked,
    production_pilot_writes_blocked,
)
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.microsoft_inbox_projection import (
    project_inbox, project_message, project_sent, project_sent_message,
    project_archive, project_drafts, project_draft_message,
    project_deleted, project_deleted_message, project_junk,
)

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
        "microsoft_draft_discard_not_performed": "Microsoft did not discard the draft. Refresh Drafts before trying again.",
        "microsoft_mutation_unconfirmed": "The message change could not be confirmed. Refresh Inbox.",
        "microsoft_message_not_found": "The message is no longer in this Inbox.",
        "microsoft_attachment_too_large": "This attachment exceeds the 2 MB read-only download limit.",
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
        {"mailboxId", "limit", "page"} if mode in {"list", "sent", "archive", "drafts", "deleted", "junk"}
        else {"mailboxId", "providerMessageId", "folder"} if mode == "attachments"
        else {"mailboxId", "providerMessageId", "attachmentId", "folder"} if mode == "download"
        else {"mailboxId", "providerMessageId", "unread"} if mode == "flag"
        else {"mailboxId", "providerMessageId"}
    )
    if not set(payload) <= expected or "mailboxId" not in payload:
        raise ValueError("invalid")
    mailbox = payload.get("mailboxId")
    if type(mailbox) is not str or _MAX_MAILBOX.fullmatch(mailbox) is None:
        raise ValueError("invalid")
    if mode in {"list", "sent", "archive", "drafts", "deleted", "junk"}:
        limit = payload.get("limit", 20)
        page = payload.get("page", 1)
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("invalid")
        if type(page) is not int or not 1 <= page <= 4:
            raise ValueError("invalid")
        return mailbox, limit if page == 1 else {"limit": limit, "page": page}
    if mode in {"exact", "sent_exact", "archive_exact", "archive_move", "delete_move", "draft_discard", "draft_exact", "deleted_exact", "junk_exact", "attachments", "download"}:
        message_id = payload.get("providerMessageId")
        if (
            type(message_id) is not str
            or re.fullmatch(r"[^\x00-\x20\x7f-\x9f?#]{1,512}", message_id) is None
            or len(message_id.encode("utf-8")) > 512
            or message_id == "."
            or ".." in message_id
        ):
            raise ValueError("invalid")
        if mode in {"attachments", "download"}:
            folder = payload.get("folder")
            if type(folder) is not str or folder not in {"Inbox", "Sent", "Archive", "Drafts", "Deleted", "Junk"}:
                raise ValueError("invalid")
            if mode == "download" and folder == "Junk":
                raise ValueError("invalid")
            if mode == "download":
                from api.inboxes.microsoft_graph import _required_message_id
                try:
                    attachment_id = _required_message_id(payload.get("attachmentId"))
                except MicrosoftGraphError:
                    raise ValueError("invalid") from None
                return mailbox, {
                    "providerMessageId": message_id,
                    "attachmentId": attachment_id,
                    "folder": folder,
                }
            return mailbox, {"providerMessageId": message_id, "folder": folder}
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
    if mode == "draft_discard" and production_pilot_draft_discard_blocked():
        # Deny before body parsing, token resolution and all Graph traffic.
        return _error(403, "microsoft_pilot_read_only")
    if mode == "delete_move" and production_pilot_delete_blocked():
        # Denied before reading any request body or resolving owner credentials.
        return _error(403, "microsoft_pilot_read_only")
    if mode == "archive_move" and production_pilot_archive_blocked():
        # Source-only permission gate before body, owner or Graph I/O.
        return _error(403, "microsoft_pilot_read_only")
    if mode == "flag" and production_pilot_read_flags_blocked():
        # Explicitly deny read/unread changes before parsing, owner/token
        # resolution, Graph PATCH, and any other mailbox-side effects.
        return _error(403, "microsoft_pilot_read_only")

    try:
        headers = snapshot_request_headers(request)
        if mode in {"flag", "archive_move", "delete_move", "draft_discard"} and production_pilot_writes_blocked():
            # This future Production mutation requires exact canonical host
            # and same-origin POST, even for an already owned mailbox.
            require_canonical_host(headers)
            require_same_origin(headers)
        payload = _parse_body(request, headers)
        mailbox_id, selector = _validate_payload(payload, mode=mode)
        if (
            mode == "flag"
            and production_pilot_mark_unread_blocked()
            and type(selector) is dict
            and selector["unread"] is True
        ):
            return _error(403, "microsoft_pilot_read_only")
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
        if mode == "attachments":
            assert type(selector) is dict
            snapshot = owned.list_owned_microsoft_attachments(
                headers, mailbox_id, selector["providerMessageId"],
                folder=selector["folder"],
            )
            if (
                type(snapshot) is not dict
                or type(snapshot.get("attachments")) is not list
                or type(snapshot.get("hasMore")) is not bool
            ):
                raise MicrosoftGraphError("microsoft_graph_invalid_response")
            return json_response(200, {"ok": True, **snapshot})
        if mode == "download":
            assert type(selector) is dict
            record = owned.download_owned_microsoft_attachment(
                headers,
                mailbox_id,
                selector["providerMessageId"],
                selector["attachmentId"],
                folder=selector["folder"],
            )
            if (
                type(record) is not dict
                or record.get("attachmentId") != selector["attachmentId"]
                or type(record.get("base64")) is not str
                or type(record.get("fileName")) is not str
                or type(record.get("size")) is not int
            ):
                raise MicrosoftGraphError("microsoft_graph_invalid_response")
            # Explicit output projection: never pass Graph JSON or tokens through.
            return json_response(200, {
                "ok": True,
                "attachmentId": record["attachmentId"],
                "fileName": record["fileName"],
                "size": record["size"],
                "base64": record["base64"],
            })
        if mode == "junk":
            assert type(selector) is int or type(selector) is dict
            if type(selector) is dict:
                snapshot = owned.list_owned_microsoft_junk(
                    headers, mailbox_id, page_size=selector["limit"], page=selector["page"],
                )
            else:
                snapshot = owned.list_owned_microsoft_junk(
                    headers, mailbox_id, page_size=selector,
                )
            return json_response(200, project_junk(snapshot, mailbox_id))
        if mode == "junk_exact":
            assert type(selector) is str
            message = owned.read_owned_microsoft_junk_message(
                headers, mailbox_id, selector,
            )
            if type(message) is not dict or message.get("id") != selector:
                raise MicrosoftGraphError("microsoft_graph_invalid_response")
            return json_response(200, {
                "ok": True, "message": project_message(
                    message, mailbox_id, exact_body=True, folder="JUNK",
                ),
            })
        if mode == "deleted":
            assert type(selector) is int or type(selector) is dict
            if type(selector) is dict:
                snapshot = owned.list_owned_microsoft_deleted(
                    headers, mailbox_id, page_size=selector["limit"], page=selector["page"],
                )
            else:
                snapshot = owned.list_owned_microsoft_deleted(
                    headers, mailbox_id, page_size=selector,
                )
            return json_response(200, project_deleted(snapshot, mailbox_id))
        if mode == "deleted_exact":
            assert type(selector) is str
            message = owned.read_owned_microsoft_deleted(headers, mailbox_id, selector)
            if type(message) is not dict or message.get("id") != selector:
                raise MicrosoftGraphError("microsoft_graph_invalid_response")
            return json_response(
                200, {"ok": True, "message": project_deleted_message(
                    message, mailbox_id, exact_body=True,
                )},
            )
        if mode == "drafts":
            assert type(selector) is int or type(selector) is dict
            if type(selector) is dict:
                snapshot = owned.list_owned_microsoft_drafts(
                    headers, mailbox_id, page_size=selector["limit"], page=selector["page"],
                )
            else:
                snapshot = owned.list_owned_microsoft_drafts(
                    headers, mailbox_id, page_size=selector,
                )
            return json_response(200, project_drafts(snapshot, mailbox_id))
        if mode == "draft_exact":
            assert type(selector) is str
            draft = owned.read_owned_microsoft_draft(headers, mailbox_id, selector)
            if type(draft) is not dict or draft.get("id") != selector:
                raise MicrosoftGraphError("microsoft_graph_invalid_response")
            return json_response(
                200, {"ok": True, "message": project_draft_message(
                    draft, mailbox_id, exact_body=True,
                )},
            )
        if mode == "archive":
            assert type(selector) is int or type(selector) is dict
            if type(selector) is dict:
                snapshot = owned.list_owned_microsoft_archive(
                    headers, mailbox_id, page_size=selector["limit"], page=selector["page"],
                )
            else:
                snapshot = owned.list_owned_microsoft_archive(
                    headers, mailbox_id, page_size=selector,
                )
            return json_response(200, project_archive(snapshot, mailbox_id))
        if mode == "archive_exact":
            assert type(selector) is str
            message = owned.read_owned_microsoft_archive_message(
                headers, mailbox_id, selector,
            )
            if type(message) is not dict or message.get("id") != selector:
                raise MicrosoftGraphError("microsoft_graph_invalid_response")
            return json_response(
                200, {"ok": True, "message": project_message(
                    message, mailbox_id, exact_body=True, folder="ARCHIVE",
                )},
            )
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
        if mode == "draft_discard":
            assert type(selector) is str
            confirmation = owned.discard_owned_microsoft_draft(
                headers, mailbox_id, selector,
            )
            if (
                type(confirmation) is not dict
                or set(confirmation) != {"providerMessageId", "discarded"}
                or confirmation.get("providerMessageId") != selector
                or confirmation.get("discarded") is not True
            ):
                raise MicrosoftGraphError("microsoft_mutation_unconfirmed")
            return json_response(200, {"ok": True, **confirmation})
        if mode == "delete_move":
            assert type(selector) is str
            confirmation = owned.delete_owned_microsoft_inbox_message(
                headers, mailbox_id, selector,
            )
            if (type(confirmation) is not dict
                or confirmation.get("providerMessageId") != selector
                or confirmation.get("deleted") is not True
                or set(confirmation) != {"providerMessageId", "deleted"}):
                raise MicrosoftGraphError("microsoft_mutation_unconfirmed")
            return json_response(200, {"ok": True, **confirmation})
        if mode == "archive_move":
            assert type(selector) is str
            confirmation = owned.archive_owned_microsoft_inbox_message(
                headers, mailbox_id, selector,
            )
            if (type(confirmation) is not dict
                or confirmation.get("providerMessageId") != selector
                or confirmation.get("archived") is not True
                or set(confirmation) != {"providerMessageId", "archived"}):
                raise MicrosoftGraphError("microsoft_mutation_unconfirmed")
            return json_response(200, {"ok": True, **confirmation})
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
        if mode == "draft_discard" and error.code == "microsoft_draft_discard_not_performed":
            return _error(409, error.code)
        if error.code == "microsoft_attachment_too_large":
            return _error(413, error.code)
        if error.code == "microsoft_graph_rate_limited":
            return _error(429, error.code)
        if error.code == "microsoft_reconnect_required":
            return _error(409, "microsoft_reconnect_required")
        if error.code == "microsoft_graph_invalid_response":
            if mode == "download":
                # Only static failure categories, never owner, message ID,
                # Graph path, token, subject, attachment name or bytes.
                stage = getattr(error, "safe_stage", None)
                if stage not in {
                    "download_metadata", "download_file_type", "download_raw_size",
                    "download_raw_invalid_png", "download_raw_other_format",
                }:
                    stage = "download_other"
                print("microsoft_attachment_invalid_response_stage=" + stage, flush=True)
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
