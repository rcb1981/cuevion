"""Bounded read-only Microsoft Graph attachment metadata; never return file content."""
from __future__ import annotations

from urllib.parse import quote, urlencode
from urllib.request import urlopen

from api.inboxes.microsoft_graph import (
    MAX_GRAPH_JSON_BYTES, MicrosoftGraphError, _get_json, _required_message_id,
)

MAX_ATTACHMENT_ROWS = 25
MAX_ATTACHMENT_BYTES = 256 * 1024
_ATTACHMENT_SELECT = "id,name,size,contentType,isInline"
_FOLDER_PATHS = {"Inbox": "inbox", "Sent": "sentitems"}


def _attachment_summary(value: object) -> dict:
    if type(value) is not dict:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    identifier = _required_message_id(value.get("id"))
    name, size = value.get("name"), value.get("size")
    content_type, inline = value.get("contentType"), value.get("isInline")
    if (
        type(name) is not str or not 1 <= len(name) <= 256
        or type(size) is not int or not 0 <= size <= 1024**4
        or content_type is not None
        and (type(content_type) is not str or len(content_type) > 128)
        or type(inline) is not bool
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    graph_type = value.get("@odata.type")
    if graph_type is not None and type(graph_type) is not str:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    kind = {
        "#microsoft.graph.fileAttachment": "file",
        "#microsoft.graph.itemAttachment": "item",
        "#microsoft.graph.referenceAttachment": "reference",
    }.get(graph_type, "other")
    return {
        "id": identifier, "name": name, "size": size,
        "contentType": content_type or "application/octet-stream",
        "isInline": inline, "kind": kind,
    }


def list_microsoft_attachments(
    access_token: str, message_id: str, *, folder: str, opener=urlopen,
) -> dict:
    """Read first 25 attachments from one well-known folder without following links."""
    folder_key = _FOLDER_PATHS.get(folder) if type(folder) is str else None
    if not folder_key:
        raise MicrosoftGraphError("microsoft_graph_path_invalid")
    identifier = quote(_required_message_id(message_id), safe="")
    params = urlencode({"$top": MAX_ATTACHMENT_ROWS, "$select": _ATTACHMENT_SELECT})
    path = f"/me/mailFolders/{folder_key}/messages/{identifier}/attachments?{params}"
    payload = _get_json(
        path, access_token, maximum_bytes=MAX_ATTACHMENT_BYTES, opener=opener,
    )
    rows = payload.get("value")
    if type(rows) is not list or len(rows) > MAX_ATTACHMENT_ROWS:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    attachments = [_attachment_summary(item) for item in rows]
    if len({item["id"] for item in attachments}) != len(attachments):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    continuation = payload.get("@odata.nextLink")
    if continuation is not None and (
        type(continuation) is not str or not 1 <= len(continuation) <= 8192
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return {"attachments": attachments, "hasMore": bool(continuation)}
