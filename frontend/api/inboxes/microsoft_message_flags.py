"""Exact Inbox-only Microsoft Graph mark-read/unread transport.

Only a fixed /me/mailFolders/inbox/messages/{immutable-id} URL is accepted.
The requested flag is applied at most once and confirmed with an independent
read. No partial/unconfirmed write triggers an automatic retry of PATCH.
"""
from __future__ import annotations

import json
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from api.inboxes.microsoft_graph import (
    GRAPH_ROOT,
    GRAPH_TIMEOUT_SECONDS,
    MicrosoftGraphError,
    _GRAPH_PREFER,
    _required_access_token,
    _required_message_id,
)

MAX_FLAG_RESPONSE_BYTES = 16 * 1024


def _error_from_http(error: HTTPError) -> MicrosoftGraphError:
    status = error.code
    try:
        error.close()
    except Exception:
        pass
    if status in (401, 403):
        return MicrosoftGraphError("microsoft_reconnect_required")
    if status == 404:
        return MicrosoftGraphError("microsoft_message_not_found")
    if status in (409, 412):
        return MicrosoftGraphError("microsoft_mutation_conflict")
    if status == 429:
        return MicrosoftGraphError("microsoft_graph_rate_limited")
    return MicrosoftGraphError("microsoft_graph_unavailable")


def _path(message_id: str) -> str:
    exact_id = _required_message_id(message_id)
    return "/me/mailFolders/inbox/messages/" + quote(exact_id, safe="")


def _read_exact_flag(
    access_token: str,
    message_id: str,
    *,
    opener=urlopen,
) -> bool:
    path = _path(message_id)
    request = Request(
        GRAPH_ROOT + path + "?$select=id,isRead",
        headers={
            "Authorization": "Bearer " + _required_access_token(access_token),
            "Accept": "application/json",
            "Prefer": _GRAPH_PREFER,
        },
        method="GET",
    )
    try:
        with opener(request, timeout=GRAPH_TIMEOUT_SECONDS) as response:
            if getattr(response, "status", None) != 200:
                raise MicrosoftGraphError("microsoft_graph_unavailable")
            data = response.read(MAX_FLAG_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise _error_from_http(error) from None
    except (URLError, TimeoutError, OSError, IncompleteRead):
        raise MicrosoftGraphError("microsoft_graph_unavailable") from None

    if type(data) is not bytes or len(data) > MAX_FLAG_RESPONSE_BYTES:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
    if (
        type(payload) is not dict
        or payload.get("id") != message_id
        or type(payload.get("isRead")) is not bool
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return payload["isRead"]


def set_exact_microsoft_inbox_read(
    access_token: str,
    message_id: str,
    *,
    unread: bool,
    before_write=None,
    opener=urlopen,
) -> dict:
    """Apply a boolean flag to one Inbox message, then verify exact readback.

    before_write is the caller's authority/generation check. Called after
    preflight and before the one and only PATCH. No rollback or retry occurs
    on a lost PATCH response; the client must refresh authoritative state.
    """
    if type(unread) is not bool:
        raise MicrosoftGraphError("microsoft_mutation_invalid")
    exact_id = _required_message_id(message_id)
    token = _required_access_token(access_token)
    desired_read = not unread
    prior_read = _read_exact_flag(token, exact_id, opener=opener)
    if prior_read == desired_read:
        # An already-satisfied request still needs the current authority.
        if before_write is not None:
            before_write()
        return {"providerMessageId": exact_id, "unread": unread, "changed": False}

    if before_write is not None:
        before_write()

    request = Request(
        GRAPH_ROOT + _path(exact_id),
        data=json.dumps(
            {"isRead": desired_read},
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": _GRAPH_PREFER,
        },
        method="PATCH",
    )
    try:
        with opener(request, timeout=GRAPH_TIMEOUT_SECONDS) as response:
            if getattr(response, "status", None) != 200:
                raise MicrosoftGraphError("microsoft_mutation_unconfirmed")
            # Do not parse a possibly large body from PATCH. The exact
            # Inbox-scoped post-write GET below proves the resulting state.
    except HTTPError as error:
        raise _error_from_http(error) from None
    except (URLError, TimeoutError, OSError, IncompleteRead):
        raise MicrosoftGraphError("microsoft_mutation_unconfirmed") from None

    confirmed_read = _read_exact_flag(token, exact_id, opener=opener)
    if confirmed_read != desired_read:
        raise MicrosoftGraphError("microsoft_mutation_unconfirmed")
    return {"providerMessageId": exact_id, "unread": unread, "changed": True}
