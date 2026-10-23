"""Bounded read-only Microsoft Graph attachment metadata; never return file content."""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode, urlsplit, parse_qsl
from urllib.request import urlopen

from api.inboxes.microsoft_graph import (
    MAX_GRAPH_JSON_BYTES, MicrosoftGraphError, _get_json, _required_message_id,
)

MAX_ATTACHMENT_ROWS = 25
MAX_ATTACHMENT_PAGES = 4
MAX_TOTAL_ATTACHMENT_ROWS = MAX_ATTACHMENT_ROWS * MAX_ATTACHMENT_PAGES
MAX_ATTACHMENT_BYTES = 256 * 1024
_ATTACHMENT_SELECT = "id,name,size,contentType,isInline,microsoft.graph.fileAttachment/contentId"
_FOLDER_PATHS = {"Inbox": "inbox", "Sent": "sentitems", "Archive": "archive", "Drafts": "drafts", "Deleted": "deleteditems", "Junk": "junkemail"}


def _attachment_summary(value: object) -> dict:
    if type(value) is not dict:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    identifier = _required_message_id(value.get("id"))
    name, size = value.get("name"), value.get("size")
    content_type, inline = value.get("contentType"), value.get("isInline")
    content_id = value.get("contentId")
    if (
        type(name) is not str or not 1 <= len(name) <= 256
        or type(size) is not int or not 0 <= size <= 1024**4
        or content_type is not None
        and (type(content_type) is not str or len(content_type) > 128)
        or type(inline) is not bool
        or content_id is not None
        and (type(content_id) is not str or len(content_id) > 202)
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    graph_type = value.get("@odata.type")
    if graph_type is not None and type(graph_type) is not str:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    # Graph examples use both '#microsoft.graph.fileAttachment' and the
    # unprefixed OData type. Keep classification fail-closed for unknown types.
    kind = {
        "microsoft.graph.fileAttachment": "file",
        "microsoft.graph.itemAttachment": "item",
        "microsoft.graph.referenceAttachment": "reference",
    }.get(graph_type.lstrip("#") if graph_type else None, "other")
    # A Graph $select projection can omit the concrete @odata.type. A valid
    # image contentId is an inert hint only; the separate download endpoint
    # STILL requires an exact authenticated Graph fileAttachment response.
    if kind == "other" and graph_type is None and content_id:
        # Metadata $select may omit the type and claim octet-stream. This
        # only allows an inert CID hint; exact download remains fail-closed
        # unless the Graph response verifies an actual fileAttachment.
        kind = "file"
    # Match only the exact CID from this message. isInline is advisory in
    # some Exchange-generated messages; the bytes route verifies file type.
    normalized_cid = None
    if kind == "file" and content_id:
        candidate = content_id[1:-1] if content_id.startswith("<") and content_id.endswith(">") else content_id
        if 1 <= len(candidate) <= 200 and re.fullmatch(r"[a-zA-Z0-9_.@+%\-]+", candidate):
            normalized_cid = candidate
    return {
        "id": identifier, "name": name, "size": size,
        "contentType": content_type or "application/octet-stream",
        "isInline": inline, "kind": kind, "contentId": normalized_cid,
    }


def _continuation_path(value: object, *, collection_path: str) -> str:
    """Accept only this exact mailbox-folder-message collection, never a URL from the browser."""
    if (type(value) is not str or not 1 <= len(value) <= 8192
            or any(ord(c) < 33 or ord(c) == 127 for c in value) or "\\" in value):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com"
                or parsed.fragment or parsed.path != "/v1.0" + collection_path):
            raise ValueError
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
        fields = dict(query)
        if (len(fields) != len(query) or not fields
                or not set(fields) <= {"$top", "$select", "$skip", "$skiptoken"}
                or ("$skip" in fields) == ("$skiptoken" in fields)
                or "$top" in fields and fields["$top"] != str(MAX_ATTACHMENT_ROWS)
                or "$select" in fields and fields["$select"] != _ATTACHMENT_SELECT
                or "$skip" in fields and re.fullmatch(r"[1-9][0-9]{0,8}", fields["$skip"]) is None
                or "$skiptoken" in fields and (not 1 <= len(fields["$skiptoken"]) <= 4096
                    or any(ord(c) < 33 or ord(c) == 127 for c in fields["$skiptoken"]))):
            raise ValueError
    except (ValueError, UnicodeError):
        raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
    return collection_path + "?" + urlencode(query)


def list_microsoft_attachments(
    access_token: str, message_id: str, *, folder: str, opener=urlopen,
) -> dict:
    """Read at most four pages / 100 rows / 1 MiB of exact-message metadata.

    Validate every continuation, including the final unvisited one. A broken
    later page never publishes a partially validated attachment inventory.
    """
    folder_key = _FOLDER_PATHS.get(folder) if type(folder) is str else None
    if not folder_key:
        raise MicrosoftGraphError("microsoft_graph_path_invalid")
    identifier = quote(_required_message_id(message_id), safe="")
    collection = f"/me/mailFolders/{folder_key}/messages/{identifier}/attachments"
    path = collection + "?" + urlencode({"$top": MAX_ATTACHMENT_ROWS, "$select": _ATTACHMENT_SELECT})
    attachments, ids, visited = [], set(), set()
    for _page in range(MAX_ATTACHMENT_PAGES):
        if path in visited:
            raise MicrosoftGraphError("microsoft_graph_invalid_response")
        visited.add(path)
        payload = _get_json(path, access_token, maximum_bytes=MAX_ATTACHMENT_BYTES, opener=opener)
        rows = payload.get("value")
        if type(rows) is not list or len(rows) > MAX_ATTACHMENT_ROWS:
            raise MicrosoftGraphError("microsoft_graph_invalid_response")
        for row in rows:
            item = _attachment_summary(row)
            if item["id"] in ids:
                raise MicrosoftGraphError("microsoft_graph_invalid_response")
            ids.add(item["id"])
            attachments.append(item)
        continuation = payload.get("@odata.nextLink")
        if continuation is None:
            return {"attachments": attachments, "hasMore": False}
        path = _continuation_path(continuation, collection_path=collection)
        if not rows or path in visited:
            raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return {"attachments": attachments, "hasMore": True}
