"""Read-only proof of a sent copy by the original immutable draft message ID.

Microsoft documents that a draft created/sent using immutable IDs can be
retrieved by the same ID in Sent Items. This may become available later, not
immediately after 202 Accepted.

Never search by subject/recipient/body, traverse arbitrary OData nextLinks,
or treat a 404 as a failed send. Only verified Sent folder membership counts.
"""
from __future__ import annotations

import json
from datetime import datetime
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from api.inboxes.microsoft_graph import (
    GRAPH_ROOT, GRAPH_TIMEOUT_SECONDS, MicrosoftGraphError,
    _GRAPH_PREFER, _required_access_token, _required_message_id,
)

MAX_PROOF_RESPONSE_BYTES = 16 * 1024
_SENT_FOLDER = "/me/mailFolders/sentitems"
_SENT_FIELDS = "id,isDraft,sentDateTime,parentFolderId"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        return None


def _open_no_redirect(request, *, timeout):
    return build_opener(_NoRedirect).open(request, timeout=timeout)


def _read_json(path: str, token: str, *, not_found_pending: bool, opener) -> dict | None:
    if not (
        path == _SENT_FOLDER + "?$select=id"
        or path.startswith(_SENT_FOLDER + "/messages/")
        and path.endswith("?$select=" + _SENT_FIELDS)
    ):
        raise MicrosoftGraphError("microsoft_graph_path_invalid")
    request = Request(
        GRAPH_ROOT + path,
        method="GET",
        headers={
            "Authorization": "Bearer " + _required_access_token(token),
            "Prefer": _GRAPH_PREFER,
            "Accept": "application/json",
        },
    )
    try:
        with opener(request, timeout=GRAPH_TIMEOUT_SECONDS) as response:
            if getattr(response, "status", None) != 200:
                raise MicrosoftGraphError("microsoft_sent_copy_unavailable")
            data = response.read(MAX_PROOF_RESPONSE_BYTES + 1)
    except HTTPError as error:
        status = error.code
        try:
            error.close()
        except Exception:
            pass
        if status == 404 and not_found_pending:
            return None
        if status in (401, 403):
            raise MicrosoftGraphError("microsoft_reconnect_required") from None
        if status == 429:
            raise MicrosoftGraphError("microsoft_graph_rate_limited") from None
        raise MicrosoftGraphError("microsoft_sent_copy_unavailable") from None
    except (URLError, OSError, TimeoutError, IncompleteRead):
        raise MicrosoftGraphError("microsoft_sent_copy_unavailable") from None
    if type(data) is not bytes or len(data) > MAX_PROOF_RESPONSE_BYTES:
        raise MicrosoftGraphError("microsoft_sent_copy_unavailable")
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MicrosoftGraphError("microsoft_sent_copy_unavailable") from None
    if type(parsed) is not dict:
        raise MicrosoftGraphError("microsoft_sent_copy_unavailable")
    return parsed


def verify_microsoft_sent_copy(
    token: str, immutable_draft_id: str, *, opener=_open_no_redirect,
) -> bool:
    """One read-only probe. True is exact Sent membership, False is pending.

    False does not imply send failure; Microsoft can create the Sent copy
    asynchronously. This function never sends or retries a message.
    """
    message_id = _required_message_id(immutable_draft_id)
    access = _required_access_token(token)
    sent = _read_json(
        _SENT_FOLDER + "/messages/" + quote(message_id, safe="")
        + "?$select=" + _SENT_FIELDS,
        access, not_found_pending=True, opener=opener,
    )
    if sent is None:
        return False
    if sent.get("id") != message_id or sent.get("isDraft") is not False:
        raise MicrosoftGraphError("microsoft_sent_copy_unavailable")
    sent_at = sent.get("sentDateTime")
    parent_id = sent.get("parentFolderId")
    if type(sent_at) is not str or type(parent_id) is not str or not parent_id:
        raise MicrosoftGraphError("microsoft_sent_copy_unavailable")
    try:
        stamp = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
    except ValueError:
        raise MicrosoftGraphError("microsoft_sent_copy_unavailable") from None
    if stamp.tzinfo is None:
        raise MicrosoftGraphError("microsoft_sent_copy_unavailable")

    folder = _read_json(
        _SENT_FOLDER + "?$select=id",
        access, not_found_pending=False, opener=opener,
    )
    if (
        type(folder) is not dict
        or type(folder.get("id")) is not str
        or not 1 <= len(folder["id"]) <= 512
        or parent_id != folder["id"]
    ):
        raise MicrosoftGraphError("microsoft_sent_copy_unavailable")
    return True
