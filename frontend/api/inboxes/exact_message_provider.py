"""Read-only, bounded provider retrieval for one exact managed-mailbox source."""
from __future__ import annotations

import json
import re
from email import message_from_bytes
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from imap_connect_preview import (
    connect_mailbox_with_settings,
    extract_message_thread_metadata,
    resolve_custom_imap_thread_ids,
    to_message_preview,
)
from .gmail_snapshot import _base64url_decode
from .imap_uid_validity import read_selected_mailbox_uid_validity

MAX_MESSAGE_BYTES = 6 * 1024 * 1024
MAX_PROVIDER_RESPONSE_BYTES = 8 * 1024 * 1024
NETWORK_TIMEOUT_SECONDS = 20
IMAP_FETCH_ITEMS = f"(UID FLAGS RFC822.SIZE BODY.PEEK[]<0.{MAX_MESSAGE_BYTES + 1}>)"
_DISPLAY_STRINGS = {"id", "sender", "subject", "snippet", "from", "to", "cc", "timestamp", "createdAt"}
_DISPLAY_KEYS = _DISPLAY_STRINGS | {"body", "attachments", "unread", "flagged", "bodyHtml"}
_ATTACHMENT_KEYS = {"id", "name", "mimeType", "size", "contentId", "disposition", "inlineSrc"}
_GOOGLE_FOLDERS = {"Inbox", "Archive", "Trash", "Spam", "Drafts", "Sent"}


def _text(value: object, maximum: int = 65536, *, nonempty: bool = False) -> bool:
    if type(value) is not str or (nonempty and not value):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum
    except UnicodeError:
        return False


def _provider_identifier(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 512
        and value == value.strip()
        and all(32 <= ord(character) <= 126 for character in value)
    )


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate provider key")
        result[key] = value
    return result


def normalize_exact_message(message: object, mailbox_id: str, source: dict) -> dict | None:
    """Validate the closed display DTO and exact provider identity at publication."""
    if type(message) is not dict:
        return None
    provider_keys = (
        {"providerMessageId", "providerThreadId", "labelIds"}
        if source.get("provider") == "google"
        else {"imapUid", "uidValidity", "threadId"}
    )
    if set(message) - (_DISPLAY_KEYS | provider_keys | {"serverMailboxId", "providerFolder", "rfcMessageId"}):
        return None
    if any(not _text(message.get(key)) for key in _DISPLAY_STRINGS):
        return None
    if not message["id"] or message.get("serverMailboxId") != mailbox_id:
        return None
    if any(type(message.get(key)) is not bool for key in ("unread", "flagged")):
        return None
    body = message.get("body")
    if type(body) is not list or not 1 <= len(body) <= 10000 or any(not _text(part, MAX_MESSAGE_BYTES) for part in body):
        return None
    if "bodyHtml" in message and not _text(message["bodyHtml"], MAX_MESSAGE_BYTES):
        return None
    attachments = message.get("attachments")
    if type(attachments) is not list or len(attachments) > 100:
        return None
    for attachment in attachments:
        if type(attachment) is not dict or set(attachment) - _ATTACHMENT_KEYS:
            return None
        if any(not _text(attachment.get(key), nonempty=True) for key in ("id", "name")):
            return None
        for key, value in attachment.items():
            if key == "size":
                if type(value) is not int or not 0 <= value <= MAX_MESSAGE_BYTES:
                    return None
            elif not _text(value, MAX_MESSAGE_BYTES if key == "inlineSrc" else 65536):
                return None
    if "rfcMessageId" in message and not _text(message["rfcMessageId"], 65536, nonempty=True):
        return None
    if source.get("provider") == "google":
        if message.get("providerMessageId") != source.get("providerMessageId") or message.get("providerFolder") not in _GOOGLE_FOLDERS:
            return None
        labels = message.get("labelIds")
        if type(labels) is not list or len(labels) > 1000 or any(not _provider_identifier(label) for label in labels) or len(labels) != len(set(labels)):
            return None
        if message["providerFolder"] != _google_folder(labels):
            return None
        if "providerThreadId" in message and not _provider_identifier(message["providerThreadId"]):
            return None
    else:
        if source.get("provider") != "custom_imap" or source.get("folder") != "INBOX" or message.get("providerFolder") != "INBOX" or message.get("uidValidity") != source.get("uidValidity") or message.get("imapUid") != source.get("imapUid") or not _text(message.get("threadId"), 512, nonempty=True):
            return None
    return dict(message)


def _display_message(parsed, *, email: str, unread: bool, flagged: bool, uid: str | None) -> dict:
    # Reuse the workspace's MIME/body/attachment adapter; omit classification metadata.
    preview = to_message_preview(parsed, 0, email, unread, uid, flagged)
    return {key: value for key, value in preview.items() if key in _DISPLAY_KEYS}


def _google_folder(labels: list[str]) -> str:
    for label, folder in (("TRASH", "Trash"), ("SPAM", "Spam"), ("DRAFT", "Drafts"), ("INBOX", "Inbox"), ("SENT", "Sent")):
        if label in labels:
            return folder
    return "Archive"


def fetch_google_message(context: dict, source: dict) -> dict:
    """One messages.get by message ID. Token preflight belongs to the auth resolver."""
    try:
        requested_id = source["providerMessageId"]
        request = Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
            + quote(requested_id, safe="") + "?format=raw",
            headers={"Authorization": "Bearer " + context["access_token"], "Accept": "application/json"},
            method="GET",
        )
        with urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) > MAX_PROVIDER_RESPONSE_BYTES):
                return {"status": "invalid_response"}
            raw_response = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
        if len(raw_response) > MAX_PROVIDER_RESPONSE_BYTES:
            return {"status": "invalid_response"}
        payload = json.loads(raw_response.decode("utf-8"), object_pairs_hook=_unique_json_object)
        if type(payload) is not dict or payload.get("id") != requested_id:
            return {"status": "invalid_response"}
        labels = payload.get("labelIds")
        if type(labels) is not list or len(labels) > 1000 or any(not _provider_identifier(label) for label in labels) or len(labels) != len(set(labels)):
            return {"status": "invalid_response"}
        if not _text(payload.get("raw"), MAX_PROVIDER_RESPONSE_BYTES, nonempty=True):
            return {"status": "invalid_response"}
        raw_message = _base64url_decode(payload["raw"])
        if len(raw_message) > MAX_MESSAGE_BYTES:
            return {"status": "invalid_response"}
        parsed = message_from_bytes(raw_message)
        message = _display_message(parsed, email=context["mailbox_email"], unread="UNREAD" in labels, flagged="STARRED" in labels, uid=None)
        message.update(serverMailboxId=context["mailbox_id"], providerFolder=_google_folder(labels), providerMessageId=requested_id, labelIds=labels)
        if "threadId" in payload:
            if not _provider_identifier(payload["threadId"]):
                return {"status": "invalid_response"}
            message["providerThreadId"] = payload["threadId"]
        rfc_id = parsed.get("Message-ID")
        if isinstance(rfc_id, str) and rfc_id.strip().strip("<>"):
            message["rfcMessageId"] = rfc_id.strip().strip("<>")
        validated = normalize_exact_message(message, context["mailbox_id"], source)
        return {"status": "ok", "message": validated} if validated else {"status": "invalid_response"}
    except HTTPError as error:
        return {"status": "message_not_found" if error.code == 404 else "service_unavailable"}
    except (ValueError, UnicodeError, TypeError, KeyError):
        return {"status": "invalid_response"}
    except Exception:
        return {"status": "service_unavailable"}


def _parse_exact_imap_fetch(response: object, expected_uid: str) -> tuple[bytes, set[str]] | str:
    if type(response) not in (list, tuple) or len(response) != 2 or response[0] != "OK" or type(response[1]) not in (list, tuple):
        return "invalid_response"
    values = response[1]
    if not values or (len(values) == 1 and values[0] is None):
        return "message_not_found"
    if len(values) != 2 or type(values[0]) is not tuple or len(values[0]) != 2 or type(values[0][0]) is not bytes or type(values[0][1]) is not bytes or type(values[1]) is not bytes:
        return "invalid_response"
    metadata, raw = values[0]
    if len(metadata) + len(values[1]) > 4096 or len(raw) > MAX_MESSAGE_BYTES:
        return "invalid_response"
    try:
        text = metadata.decode("ascii", errors="strict")
        tail = values[1].decode("ascii", errors="strict")
    except UnicodeError:
        return "invalid_response"
    # Attributes may precede or follow the single literal. Every identity,
    # size, and flag attribute must occur exactly once across both segments.
    prefix = re.fullmatch(r"[1-9][0-9]* \((?:(.*) )?BODY\[\]<0> \{([0-9]+)\}", text, re.ASCII)
    if prefix is None or not tail.endswith(")"):
        return "invalid_response"
    before_fields, literal_size = prefix.groups()
    after_fields = tail[:-1]
    if after_fields and (not after_fields.startswith(" ") or after_fields != " " + after_fields.strip()):
        return "invalid_response"
    fields = " ".join(part for part in (before_fields, after_fields.strip()) if part)
    pieces = re.findall(r"(?:UID [1-9][0-9]*|RFC822\.SIZE [0-9]+|FLAGS \([^()\r\n]*\))", fields, re.ASCII)
    if len(pieces) != 3 or " ".join(pieces) != fields:
        return "invalid_response"
    by_name = {piece.split(" ", 1)[0]: piece.split(" ", 1)[1] for piece in pieces}
    if set(by_name) != {"UID", "RFC822.SIZE", "FLAGS"} or by_name["UID"] != expected_uid:
        return "invalid_response"
    if len(literal_size) > 10 or len(by_name["RFC822.SIZE"]) > 10 or int(literal_size) != len(raw) or int(by_name["RFC822.SIZE"]) != len(raw):
        return "invalid_response"
    flag_text = by_name["FLAGS"][1:-1]
    if flag_text and re.fullmatch(r"[A-Za-z0-9\\$_.-]+(?: [A-Za-z0-9\\$_.-]+)*", flag_text, re.ASCII) is None:
        return "invalid_response"
    return raw, set(flag_text.split())


def fetch_imap_message(resolved_mailbox: dict, source: dict) -> dict:
    """Read one UID in a read-only INBOX selection after live UIDVALIDITY."""
    mailbox = None
    try:
        imap = resolved_mailbox["imap"]
        mailbox = connect_mailbox_with_settings(host=imap["host"], port=imap["port"], username=imap["username"], password=imap["password"], ssl_enabled=imap["ssl"], timeout=NETWORK_TIMEOUT_SECONDS)
        selected = mailbox.select('"INBOX"', readonly=True)
        if type(selected) not in (list, tuple) or len(selected) != 2 or selected[0] != "OK":
            return {"status": "service_unavailable"}
        live_uid_validity = read_selected_mailbox_uid_validity(mailbox)
        if live_uid_validity is None:
            return {"status": "invalid_response"}
        if live_uid_validity != source["uidValidity"]:
            return {"status": "source_changed"}
        fetched = _parse_exact_imap_fetch(mailbox.uid("FETCH", source["imapUid"], IMAP_FETCH_ITEMS), source["imapUid"])
        if isinstance(fetched, str):
            return {"status": fetched}
        raw_message, flags = fetched
        parsed = message_from_bytes(raw_message)
        message = _display_message(parsed, email=resolved_mailbox["email"], unread="\\Seen" not in flags, flagged="\\Flagged" in flags, uid=source["imapUid"])
        metadata = extract_message_thread_metadata(parsed, source["imapUid"], message["id"])
        thread_ids = resolve_custom_imap_thread_ids([metadata], mailbox_key=resolved_mailbox["mailboxId"], folder="INBOX", uid_validity=live_uid_validity)
        message.update(serverMailboxId=resolved_mailbox["mailboxId"], providerFolder="INBOX", imapUid=source["imapUid"], uidValidity=live_uid_validity, threadId=thread_ids[0])
        if metadata.get("message_id"):
            message["rfcMessageId"] = metadata["message_id"]
        validated = normalize_exact_message(message, resolved_mailbox["mailboxId"], source)
        return {"status": "ok", "message": validated} if validated else {"status": "invalid_response"}
    except Exception:
        return {"status": "service_unavailable"}
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                pass
