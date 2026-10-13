"""Fail-closed, read-only Microsoft Graph transport for Cuevion mailboxes.

This is a provider foundation, not an authenticated HTTP endpoint. Callers must
resolve mailbox ownership and token authority before using these functions.
No arbitrary URLs, pagination links or user supplied Graph paths are fetched.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from api.auth.email_address import is_valid_auth_email, normalize_auth_email

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
MAX_GRAPH_JSON_BYTES = 1024 * 1024
MAX_IDENTITY_JSON_BYTES = 64 * 1024
MAX_MESSAGES_PER_PAGE = 25
GRAPH_TIMEOUT_SECONDS = 20
MAX_MESSAGE_ID_LENGTH = 512
_GRAPH_MESSAGE_ID = re.compile(r"^[^\x00-\x20\x7f-\x9f?#]{1,512}$")

# The Graph immutable-id preference prevents IDs changing on mailbox moves.
_GRAPH_PREFER = 'IdType="ImmutableId"'
_MESSAGE_SELECT = (
    "id,conversationId,subject,from,bodyPreview,receivedDateTime,"
    "isRead,hasAttachments,parentFolderId,internetMessageId"
)


class MicrosoftGraphError(Exception):
    """Generic codes only. Never include access tokens or Graph response bodies."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _required_access_token(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 12 <= len(value) <= 16384
        or value != value.strip()
        or any(not 33 <= ord(c) <= 126 for c in value)
    ):
        raise MicrosoftGraphError("microsoft_reconnect_required")
    return value


def _response_payload(response, *, maximum_bytes: int) -> dict:
    try:
        data = response.read(maximum_bytes + 1)
    except (IncompleteRead, OSError, TimeoutError):
        raise MicrosoftGraphError("microsoft_graph_unavailable") from None
    if not isinstance(data, bytes) or len(data) > maximum_bytes:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
    if not isinstance(payload, dict):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return payload


def _get_json(path: str, access_token: str, *, maximum_bytes: int, opener=urlopen) -> dict:
    if not path.startswith("/me") or not (
        path == "/me?$select=id,displayName,mail,userPrincipalName"
        or path.startswith("/me/mailFolders/inbox/messages?")
        or path.startswith("/me/messages/")
        or path.startswith("/me/mailFolders/sentitems/messages?")
        or path.startswith("/me/mailFolders/sentitems/messages/")
    ):
        raise MicrosoftGraphError("microsoft_graph_path_invalid")
    token = _required_access_token(access_token)
    request = Request(
        GRAPH_ROOT + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Prefer": _GRAPH_PREFER,
        },
        method="GET",
    )
    try:
        with opener(request, timeout=GRAPH_TIMEOUT_SECONDS) as response:
            return _response_payload(response, maximum_bytes=maximum_bytes)
    except HTTPError as error:
        status = error.code
        try:
            error.close()
        except Exception:
            pass
        if status in (401, 403):
            raise MicrosoftGraphError("microsoft_reconnect_required") from None
        if status == 429:
            raise MicrosoftGraphError("microsoft_graph_rate_limited") from None
        raise MicrosoftGraphError("microsoft_graph_unavailable") from None
    except (URLError, TimeoutError, OSError, IncompleteRead):
        raise MicrosoftGraphError("microsoft_graph_unavailable") from None


def _safe_mail(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = normalize_auth_email(value)
    return normalized if is_valid_auth_email(normalized) else None


def fetch_verified_microsoft_identity(access_token: str, *, opener=urlopen) -> dict:
    """Get the real Graph account identity, never trust an OAuth email hint."""
    payload = _get_json(
        "/me?$select=id,displayName,mail,userPrincipalName",
        access_token,
        maximum_bytes=MAX_IDENTITY_JSON_BYTES,
        opener=opener,
    )
    account_id = payload.get("id")
    if (
        not isinstance(account_id, str)
        or not 1 <= len(account_id) <= 256
        or account_id != account_id.strip()
        or any(ord(c) < 33 or ord(c) == 127 for c in account_id)
    ):
        raise MicrosoftGraphError("microsoft_identity_invalid")
    mail = _safe_mail(payload.get("mail"))
    principal = _safe_mail(payload.get("userPrincipalName"))
    if not mail and not principal:
        raise MicrosoftGraphError("microsoft_identity_invalid")
    display = payload.get("displayName")
    return {
        "id": account_id,
        "email": mail or principal,
        "verifiedEmails": tuple(sorted({m for m in (mail, principal) if m})),
        "display_name": display.strip()[:256] if isinstance(display, str) and display.strip() else None,
    }


def verify_microsoft_identity_for_hint(
    access_token: str,
    email_hint: str,
    *,
    opener=urlopen,
) -> dict:
    """Fail closed if the signed-in Microsoft account is not the selected one."""
    expected = _safe_mail(email_hint)
    if not expected:
        raise MicrosoftGraphError("microsoft_identity_invalid")
    identity = fetch_verified_microsoft_identity(access_token, opener=opener)
    if expected not in identity["verifiedEmails"]:
        raise MicrosoftGraphError("microsoft_identity_mismatch")
    # Use the verified selected alias as mailbox identity, not a raw hint.
    return {**identity, "email": expected}


def _required_message_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not _GRAPH_MESSAGE_ID.fullmatch(value)
        or len(value.encode("utf-8")) > MAX_MESSAGE_ID_LENGTH
        or value == "."
        or ".." in value
    ):
        raise MicrosoftGraphError("microsoft_message_id_invalid")
    return value


def _message_summary(item: object) -> dict:
    if not isinstance(item, dict):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    message_id = _required_message_id(item.get("id"))
    sender = item.get("from")
    sender_address = sender.get("emailAddress") if isinstance(sender, dict) else None
    address = _safe_mail(sender_address.get("address")) if isinstance(sender_address, dict) else None
    name = sender_address.get("name") if isinstance(sender_address, dict) else None
    if not address:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    when = item.get("receivedDateTime")
    if not isinstance(when, str) or len(when) > 64:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    try:
        stamp = datetime.fromisoformat(when.replace("Z", "+00:00"))
    except ValueError:
        raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
    if stamp.tzinfo is None:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    subject = item.get("subject")
    preview = item.get("bodyPreview")
    is_read = item.get("isRead")
    has_attachments = item.get("hasAttachments")
    if (
        not isinstance(subject, str)
        or len(subject) > 998
        or not isinstance(preview, str)
        or len(preview) > 16384
        or not isinstance(is_read, bool)
        or not isinstance(has_attachments, bool)
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    conversation_id = item.get("conversationId")
    if conversation_id is not None:
        conversation_id = _required_message_id(conversation_id)
    return {
        "id": message_id,
        "conversationId": conversation_id,
        "subject": subject,
        "senderEmail": address,
        "senderName": name.strip()[:256] if isinstance(name, str) and name.strip() else address,
        "preview": preview,
        "receivedAt": stamp.isoformat(),
        "unread": not is_read,
        "hasAttachments": has_attachments,
    }


def list_microsoft_inbox(access_token: str, *, page_size: int = 20, opener=urlopen) -> dict:
    """Fetch bounded current INBOX page; no arbitrary nextLink following."""
    if type(page_size) is not int or not 1 <= page_size <= MAX_MESSAGES_PER_PAGE:
        raise MicrosoftGraphError("microsoft_page_size_invalid")
    query = urlencode({
        "$top": str(page_size),
        "$orderby": "receivedDateTime desc",
        "$select": _MESSAGE_SELECT,
    })
    payload = _get_json(
        "/me/mailFolders/inbox/messages?" + query,
        access_token,
        maximum_bytes=MAX_GRAPH_JSON_BYTES,
        opener=opener,
    )
    values = payload.get("value")
    if not isinstance(values, list) or len(values) > page_size:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    items = [_message_summary(item) for item in values]
    next_link = payload.get("@odata.nextLink")
    if next_link is not None and (
        not isinstance(next_link, str)
        or not next_link.startswith(GRAPH_ROOT + "/me/mailFolders/inbox/messages?")
        or len(next_link) > 8192
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return {"messages": items, "hasMore": bool(next_link)}


def get_microsoft_message(access_token: str, message_id: str, *, opener=urlopen) -> dict:
    """Read a single exact message ID without requesting arbitrary URLs."""
    encoded_id = quote(_required_message_id(message_id), safe="")
    query = urlencode({"$select": _MESSAGE_SELECT + ",body"})
    payload = _get_json(
        "/me/messages/" + encoded_id + "?" + query,
        access_token,
        maximum_bytes=MAX_GRAPH_JSON_BYTES,
        opener=opener,
    )
    if payload.get("id") != message_id:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    summary = _message_summary(payload)
    body = payload.get("body")
    if not isinstance(body, dict) or body.get("contentType") not in {"text", "html"}:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    content = body.get("content")
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_GRAPH_JSON_BYTES:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return {**summary, "body": {"contentType": body["contentType"], "content": content}}
