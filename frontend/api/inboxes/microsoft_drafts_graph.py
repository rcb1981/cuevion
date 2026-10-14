"""Fixed-folder Microsoft Drafts read transport. No draft writes or sending."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from api.inboxes.microsoft_graph import (
    GRAPH_ROOT, MAX_GRAPH_JSON_BYTES, MAX_MESSAGES_PER_PAGE, MAX_UI_PAGES,
    MicrosoftGraphError, _get_json, _required_message_id, _safe_mail,
)
from api.inboxes.microsoft_graph_paging import (
    InvalidMicrosoftContinuation, validated_microsoft_continuation_path,
)

DRAFTS_FOLDER_PATH = "/me/mailFolders/drafts/messages"
DRAFTS_SELECT = (
    "id,conversationId,subject,toRecipients,bodyPreview,"
    "lastModifiedDateTime,isDraft,hasAttachments"
)


def _normalize_draft(item: object) -> dict:
    if type(item) is not dict or item.get("isDraft") is not True:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    identifier = _required_message_id(item.get("id"))
    subject, preview = item.get("subject"), item.get("bodyPreview")
    stamp, has_attachments = item.get("lastModifiedDateTime"), item.get("hasAttachments")
    if subject is None:
        subject = ""
    if preview is None:
        preview = ""
    if (
        type(subject) is not str or len(subject) > 998
        or type(preview) is not str or len(preview) > 16384
        or type(stamp) is not str or len(stamp) > 64
        or type(has_attachments) is not bool
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    try:
        modified = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
    if modified.tzinfo is None:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    raw_recipients = item.get("toRecipients")
    if raw_recipients is None:
        raw_recipients = []
    if type(raw_recipients) is not list or len(raw_recipients) > 256:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    recipients = []
    for recipient in raw_recipients:
        address_object = recipient.get("emailAddress") if type(recipient) is dict else None
        if type(address_object) is not dict:
            raise MicrosoftGraphError("microsoft_graph_invalid_response")
        email_address = _safe_mail(address_object.get("address"))
        display_name = address_object.get("name")
        if (
            email_address is None
            or display_name is not None
            and (type(display_name) is not str or len(display_name) > 256)
        ):
            raise MicrosoftGraphError("microsoft_graph_invalid_response")
        name = display_name.strip() if type(display_name) is str else ""
        recipients.append(
            f"{name} <{email_address}>"
            if name and name != email_address else email_address
        )
    to_display = ", ".join(recipients[:8])
    if len(recipients) > 8:
        to_display += f" (+{len(recipients) - 8} more)"
    to_display = to_display[:2048] or "(No recipients)"
    conversation_id = item.get("conversationId")
    if conversation_id is not None:
        conversation_id = _required_message_id(conversation_id)
    return {
        "id": identifier,
        "conversationId": conversation_id,
        "subject": subject,
        "preview": preview,
        "modifiedAt": modified.isoformat(),
        "toDisplay": to_display,
        "hasAttachments": has_attachments,
    }


def list_microsoft_drafts(access_token: str, *, page_size: int = 20, page: int = 1, opener=urlopen) -> dict:
    """Fetch one bounded page; Graph continuation never leaves this server."""
    if type(page_size) is not int or not 1 <= page_size <= MAX_MESSAGES_PER_PAGE:
        raise MicrosoftGraphError("microsoft_page_size_invalid")
    if type(page) is not int or not 1 <= page <= MAX_UI_PAGES:
        raise MicrosoftGraphError("microsoft_page_invalid")
    path = DRAFTS_FOLDER_PATH + "?" + urlencode({
        "$top": page_size,
        "$orderby": "lastModifiedDateTime desc",
        "$select": DRAFTS_SELECT,
    })
    for index in range(page):
        payload = _get_json(
            path, access_token, maximum_bytes=MAX_GRAPH_JSON_BYTES,
            opener=opener, internal_paging=index > 0, paging_folder="drafts",
        )
        rows = payload.get("value")
        if type(rows) is not list or len(rows) > page_size:
            raise MicrosoftGraphError("microsoft_graph_invalid_response")
        result = [_normalize_draft(row) for row in rows]
        ids = [item["id"] for item in result]
        if len(set(ids)) != len(ids):
            raise MicrosoftGraphError("microsoft_graph_invalid_response")
        link = payload.get("@odata.nextLink")
        next_path = None
        if link is not None:
            try:
                next_path = validated_microsoft_continuation_path(
                    link, expected_folder="drafts",
                )
            except InvalidMicrosoftContinuation:
                raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
        if index + 1 == page:
            return {"messages": result, "hasMore": bool(next_path)}
        if next_path is None:
            return {"messages": [], "hasMore": False}
        path = next_path
    raise MicrosoftGraphError("microsoft_graph_invalid_response")


def get_microsoft_draft(access_token: str, message_id: str, *, opener=urlopen) -> dict:
    """Exact draft from the fixed Drafts folder; does not send or mark read."""
    identifier = _required_message_id(message_id)
    path = DRAFTS_FOLDER_PATH + "/" + quote(identifier, safe="") + "?" + urlencode({
        "$select": DRAFTS_SELECT + ",body",
    })
    item = _get_json(path, access_token, maximum_bytes=MAX_GRAPH_JSON_BYTES, opener=opener)
    if item.get("id") != identifier:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    draft = _normalize_draft(item)
    body = item.get("body")
    if type(body) is not dict or body.get("contentType") not in ("text", "html"):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    content = body.get("content")
    if type(content) is not str or len(content.encode("utf-8")) > MAX_GRAPH_JSON_BYTES:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return {**draft, "body": {"contentType": body["contentType"], "content": content}}
