"""Exact Inbox -> Archive Graph move, never retried after an indeterminate POST.

No browser-supplied destination, Graph URL, folder, or query parameters.
The immutable message ID must be confirmed at the exact Archive endpoint before
the caller can remove it from the visible Inbox.
"""
from __future__ import annotations

import json
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

from api.inboxes.microsoft_graph import (
    GRAPH_ROOT, GRAPH_TIMEOUT_SECONDS, MicrosoftGraphError,
    _GRAPH_OPENER, _GRAPH_PREFER, _required_access_token, _required_message_id,
)
from api.inboxes.microsoft_message_flags import _error_from_http

_MAX_BODY = 16384


def _json_graph(access_token: str, path: str, *, method: str = "GET",
                data: bytes | None = None, expected_status: int = 200,
                opener=None) -> dict:
    if opener is None:
        opener = _GRAPH_OPENER.open  # Graph redirects must never carry bearer tokens.
    request = Request(
        GRAPH_ROOT + path,
        data=data,
        headers={
            "Authorization": "Bearer " + _required_access_token(access_token),
            "Accept": "application/json",
            "Prefer": _GRAPH_PREFER,
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
        method=method,
    )
    try:
        with opener(request, timeout=GRAPH_TIMEOUT_SECONDS) as response:
            if getattr(response, "status", None) != expected_status:
                raise MicrosoftGraphError("microsoft_graph_unavailable")
            raw = response.read(_MAX_BODY + 1)
    except HTTPError as err:
        raise _error_from_http(err) from None
    except (URLError, TimeoutError, OSError, IncompleteRead):
        raise MicrosoftGraphError("microsoft_graph_unavailable") from None
    if type(raw) is not bytes or len(raw) > _MAX_BODY:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
    if type(parsed) is not dict:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return parsed


def move_exact_microsoft_inbox_to_archive(
    access_token: str, message_id: str, *, before_write=None, opener=None,
) -> dict:
    exact_id = _required_message_id(message_id)
    encoded = quote(exact_id, safe="")
    # Resolve the authoritative destination folder ID using a fixed Graph path.
    dest = _json_graph(access_token, "/me/mailFolders/archive?$select=id", opener=opener)
    archive_folder_id = dest.get("id")
    if type(archive_folder_id) is not str:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    _required_message_id(archive_folder_id)

    # A folder-scoped GET confirms membership in the *real* Inbox; this is not
    # inferred from the selected UI row or guessed from a subject.
    source_path = "/me/mailFolders/inbox/messages/" + encoded
    source = _json_graph(access_token, source_path + "?$select=id,parentFolderId", opener=opener)
    if source.get("id") != exact_id or type(source.get("parentFolderId")) is not str:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    if before_write is not None:
        before_write()

    # Exactly one move request, regardless of exceptions or uncertain response.
    moved = _json_graph(
        access_token, source_path + "/move",
        method="POST", data=b'{"destinationId":"archive"}',
        expected_status=201, opener=opener,
    )
    if moved.get("id") != exact_id or moved.get("parentFolderId") != archive_folder_id:
        raise MicrosoftGraphError("microsoft_mutation_unconfirmed")

    # Independently prove the immutable ID is now in the authorized Archive.
    try:
        confirmed = _json_graph(
            access_token,
            "/me/mailFolders/archive/messages/" + encoded + "?$select=id,parentFolderId",
            opener=opener,
        )
    except MicrosoftGraphError:
        raise MicrosoftGraphError("microsoft_mutation_unconfirmed") from None
    if confirmed.get("id") != exact_id or confirmed.get("parentFolderId") != archive_folder_id:
        raise MicrosoftGraphError("microsoft_mutation_unconfirmed")
    return {"providerMessageId": exact_id, "archived": True}
