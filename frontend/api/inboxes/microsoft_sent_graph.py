"""Fixed-folder, read-only Microsoft Graph Sent Items transport.

Never accept a folder path, URL, mailbox account, or OData continuation from
a browser. Sent Items uses its own well-known folder and sentDateTime order.
The exact-message endpoint is folder-scoped, so Inbox/other-folder IDs cannot
silently return unrelated contents.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote, urlencode

from api.inboxes.microsoft_graph import (
    GRAPH_ROOT,
    MAX_GRAPH_JSON_BYTES,
    MAX_MESSAGES_PER_PAGE,
    MicrosoftGraphError,
    _get_json,
    _required_message_id,
    _safe_mail,
)
from urllib.request import urlopen

SENT_FOLDER_PATH = "/me/mailFolders/sentitems/messages"
SENT_MESSAGE_SELECT = (
    "id,conversationId,subject,from,toRecipients,bodyPreview,"
    "sentDateTime,hasAttachments"
)
MAX_TO_RECIPIENTS = 256
MAX_RECIPIENT_TEXT = 2048


def _normalized_sent_message(item: object) -> dict:
    if type(item) is not dict:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    identifier = _required_message_id(item.get("id"))
    subject = item.get("subject")
    preview = item.get("bodyPreview")
    timestamp = item.get("sentDateTime")
    attachments = item.get("hasAttachments")
    if (
        type(subject) is not str or len(subject) > 998
        or type(preview) is not str or len(preview) > 16384
        or type(timestamp) is not str or len(timestamp) > 64
        or type(attachments) is not bool
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    try:
        sent = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
    if sent.tzinfo is None:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")

    raw_to = item.get("toRecipients")
    if type(raw_to) is not list or len(raw_to) > MAX_TO_RECIPIENTS:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    # Sent items may legitimately contain only Bcc recipients; the message
    # will be shown as undisclosed recipients without leaking Bcc details.
    recipients: list[str] = []
    for entry in raw_to:
        email_address = entry.get("emailAddress") if type(entry) is dict else None
        if type(email_address) is not dict:
            raise MicrosoftGraphError("microsoft_graph_invalid_response")
        address = _safe_mail(email_address.get("address"))
        name = email_address.get("name")
        if (
            address is None
            or name is not None and (type(name) is not str or len(name) > 256)
        ):
            raise MicrosoftGraphError("microsoft_graph_invalid_response")
        normalized_name = name.strip() if type(name) is str else ""
        recipients.append(
            f"{normalized_name} <{address}>" if normalized_name and normalized_name != address
            else address
        )
    recipient_display = ", ".join(recipients[:8])
    if len(recipients) > 8:
        recipient_display += f" (+{len(recipients) - 8} more)"
    recipient_display = recipient_display[:MAX_RECIPIENT_TEXT] or "Undisclosed recipients"

    from_record = item.get("from")
    sender_email = from_record.get("emailAddress") if type(from_record) is dict else None
    if type(sender_email) is not dict:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    sender_address = _safe_mail(sender_email.get("address"))
    sender_name = sender_email.get("name")
    if (
        sender_address is None
        or sender_name is not None
        and (type(sender_name) is not str or len(sender_name) > 256)
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    sender_display_name = sender_name.strip() if type(sender_name) is str else ""
    sender_display = (
        f"{sender_display_name} <{sender_address}>"
        if sender_display_name and sender_display_name != sender_address
        else sender_address
    )
    conversation_id = item.get("conversationId")
    if conversation_id is not None:
        conversation_id = _required_message_id(conversation_id)
    return {
        "id": identifier,
        "conversationId": conversation_id,
        "subject": subject,
        "preview": preview,
        "sentAt": sent.isoformat(),
        "toDisplay": recipient_display,
        "fromDisplay": sender_display,
        "hasAttachments": attachments,
    }


def list_microsoft_sent(
    access_token: str,
    *,
    page_size: int = 20,
    opener=urlopen,
) -> dict:
    if type(page_size) is not int or not 1 <= page_size <= MAX_MESSAGES_PER_PAGE:
        raise MicrosoftGraphError("microsoft_page_size_invalid")
    query = urlencode({
        "$top": str(page_size),
        "$orderby": "sentDateTime desc",
        "$select": SENT_MESSAGE_SELECT,
    })
    payload = _get_json(
        SENT_FOLDER_PATH + "?" + query,
        access_token,
        maximum_bytes=MAX_GRAPH_JSON_BYTES,
        opener=opener,
    )
    raw = payload.get("value")
    if type(raw) is not list or len(raw) > page_size:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    values = [_normalized_sent_message(item) for item in raw]
    message_ids = [entry["id"] for entry in values]
    if len(set(message_ids)) != len(message_ids):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    next_link = payload.get("@odata.nextLink")
    if next_link is not None and (
        type(next_link) is not str
        or not next_link.startswith(GRAPH_ROOT + SENT_FOLDER_PATH + "?")
        or len(next_link) > 8192
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return {"messages": values, "hasMore": bool(next_link)}


def get_microsoft_sent_message(
    access_token: str,
    message_id: str,
    *,
    opener=urlopen,
) -> dict:
    exact_id = _required_message_id(message_id)
    query = urlencode({"$select": SENT_MESSAGE_SELECT + ",body"})
    payload = _get_json(
        SENT_FOLDER_PATH + "/" + quote(exact_id, safe="") + "?" + query,
        access_token,
        maximum_bytes=MAX_GRAPH_JSON_BYTES,
        opener=opener,
    )
    if payload.get("id") != exact_id:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    summary = _normalized_sent_message(payload)
    body = payload.get("body")
    if type(body) is not dict or body.get("contentType") not in ("text", "html"):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    content = body.get("content")
    if type(content) is not str or len(content.encode("utf-8")) > MAX_GRAPH_JSON_BYTES:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return {**summary, "body": {"contentType": body["contentType"], "content": content}}
