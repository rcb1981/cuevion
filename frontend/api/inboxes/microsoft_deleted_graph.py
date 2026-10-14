"""Fixed-folder, read-only Microsoft Graph Deleted Items transport.

Includes incoming, sent and draft messages; sorts by the Graph modification
timestamp, not an invented deletion time. No message mutation occurs.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from api.inboxes.microsoft_graph import (
    MAX_GRAPH_JSON_BYTES, MAX_MESSAGES_PER_PAGE, MAX_UI_PAGES,
    MicrosoftGraphError, _get_json, _required_message_id, _safe_mail,
)
from api.inboxes.microsoft_graph_paging import (
    InvalidMicrosoftContinuation, validated_microsoft_continuation_path,
)

DELETED_FOLDER_PATH = "/me/mailFolders/deleteditems/messages"
DELETED_SELECT = (
    "id,conversationId,subject,from,toRecipients,bodyPreview,"
    "lastModifiedDateTime,isRead,isDraft,hasAttachments"
)


def _safe_display_contact(item: object) -> str:
    if item is None:
        return ""
    entry = item.get("emailAddress") if type(item) is dict else None
    if type(entry) is not dict:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    address = _safe_mail(entry.get("address"))
    name = entry.get("name")
    if address is None or name is not None and (type(name) is not str or len(name) > 256):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    display_name = name.strip() if type(name) is str else ""
    return f"{display_name} <{address}>" if display_name and display_name != address else address


def _normalize_deleted(item: object) -> dict:
    if type(item) is not dict:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    message_id = _required_message_id(item.get("id"))
    subject = item.get("subject")
    preview = item.get("bodyPreview")
    if subject is None:
        subject = ""
    if preview is None:
        preview = ""
    modified = item.get("lastModifiedDateTime")
    is_read, is_draft, attachments = item.get("isRead"), item.get("isDraft"), item.get("hasAttachments")
    if (
        type(subject) is not str or len(subject) > 998
        or type(preview) is not str or len(preview) > 16384
        or type(modified) is not str or len(modified) > 64
        or type(is_read) is not bool or type(is_draft) is not bool
        or type(attachments) is not bool
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    try:
        timestamp = datetime.fromisoformat(modified.replace("Z", "+00:00"))
    except ValueError:
        raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
    if timestamp.tzinfo is None:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")

    from_display = _safe_display_contact(item.get("from"))
    raw_to = item.get("toRecipients")
    if raw_to is None:
        raw_to = []
    if type(raw_to) is not list or len(raw_to) > 256:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    recipients = [_safe_display_contact(entry) for entry in raw_to]
    if any(not recipient for recipient in recipients):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    to_display = ", ".join(recipients[:8])
    if len(recipients) > 8:
        to_display += f" (+{len(recipients) - 8} more)"
    to_display = to_display[:2048]

    conversation_id = item.get("conversationId")
    if conversation_id is not None:
        conversation_id = _required_message_id(conversation_id)
    return {
        "id": message_id,
        "conversationId": conversation_id,
        "subject": subject,
        "preview": preview,
        "modifiedAt": timestamp.isoformat(),
        "fromDisplay": from_display,
        "toDisplay": to_display,
        "unread": (not is_read) and not is_draft,
        "isDraft": is_draft,
        "hasAttachments": attachments,
    }


def list_microsoft_deleted(
    access_token: str, *, page_size: int = 20, page: int = 1, opener=urlopen,
) -> dict:
    """Load one of four bounded pages, never accept or expose a Graph URL."""
    if type(page_size) is not int or not 1 <= page_size <= MAX_MESSAGES_PER_PAGE:
        raise MicrosoftGraphError("microsoft_page_size_invalid")
    if type(page) is not int or not 1 <= page <= MAX_UI_PAGES:
        raise MicrosoftGraphError("microsoft_page_invalid")
    path = DELETED_FOLDER_PATH + "?" + urlencode({
        "$top": page_size,
        "$orderby": "lastModifiedDateTime desc",
        "$select": DELETED_SELECT,
    })
    for index in range(page):
        payload = _get_json(
            path, access_token, maximum_bytes=MAX_GRAPH_JSON_BYTES,
            opener=opener, internal_paging=index > 0,
            paging_folder="deleteditems",
        )
        values = payload.get("value")
        if type(values) is not list or len(values) > page_size:
            raise MicrosoftGraphError("microsoft_graph_invalid_response")
        messages = [_normalize_deleted(row) for row in values]
        ids = [message["id"] for message in messages]
        if len(ids) != len(set(ids)):
            raise MicrosoftGraphError("microsoft_graph_invalid_response")
        next_link = payload.get("@odata.nextLink")
        next_path = None
        if next_link is not None:
            try:
                next_path = validated_microsoft_continuation_path(
                    next_link, expected_folder="deleteditems",
                )
            except InvalidMicrosoftContinuation:
                raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
        if index + 1 == page:
            return {"messages": messages, "hasMore": bool(next_path)}
        if next_path is None:
            return {"messages": [], "hasMore": False}
        path = next_path
    raise MicrosoftGraphError("microsoft_graph_invalid_response")


def get_microsoft_deleted_message(access_token: str, message_id: str, *, opener=urlopen) -> dict:
    """Read one exact Deleted Items message; never cross folder boundaries."""
    exact_id = _required_message_id(message_id)
    path = DELETED_FOLDER_PATH + "/" + quote(exact_id, safe="") + "?" + urlencode({
        "$select": DELETED_SELECT + ",body",
    })
    payload = _get_json(path, access_token, maximum_bytes=MAX_GRAPH_JSON_BYTES, opener=opener)
    if payload.get("id") != exact_id:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    result = _normalize_deleted(payload)
    body = payload.get("body")
    if type(body) is not dict or body.get("contentType") not in ("text", "html"):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    content = body.get("content")
    if type(content) is not str or len(content.encode("utf-8")) > MAX_GRAPH_JSON_BYTES:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return {**result, "body": {"contentType": body["contentType"], "content": content}}
