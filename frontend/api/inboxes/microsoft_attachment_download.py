"""Bounded, read-only file download from one exact Microsoft mail attachment.

No provider URLs, OAuth tokens, file contents, or temporary storage are
accepted from the browser. Only Graph file attachments at or below 2 MiB
are supported in this limited read-only pilot.
"""
from __future__ import annotations

import base64
import binascii
import unicodedata
from urllib.parse import quote
from urllib.request import urlopen

from api.inboxes.microsoft_graph import MicrosoftGraphError, _get_json, _required_message_id

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_GRAPH_DOWNLOAD_RESPONSE_BYTES = 3 * 1024 * 1024
_FILE_FOLDERS = {"Inbox": "inbox", "Sent": "sentitems", "Archive": "archive", "Drafts": "drafts", "Deleted": "deleteditems"}


def _download_filename(value: object) -> str:
    if type(value) is not str or not value:
        return "attachment.bin"
    # Treat both Unix and Windows separators as path delimiters.
    tail = value.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(
        "_" if c in '<>:"|?*' or ord(c) < 32
        or unicodedata.category(c) in {"Cc", "Cf", "Cs"}
        else c
        for c in tail
    ).strip(" .")
    return cleaned[:128] or "attachment.bin"


def read_microsoft_file_attachment(
    access_token: str, message_id: str, attachment_id: str,
    *, folder: str, opener=urlopen,
) -> dict:
    """Load one exact immutable attachment under one fixed mailbox folder."""
    mapped_folder = _FILE_FOLDERS.get(folder) if type(folder) is str else None
    if mapped_folder is None:
        raise MicrosoftGraphError("microsoft_graph_path_invalid")
    msg = quote(_required_message_id(message_id), safe="")
    att = quote(_required_message_id(attachment_id), safe="")
    path = f"/me/mailFolders/{mapped_folder}/messages/{msg}/attachments/{att}"
    payload = _get_json(
        path, access_token,
        maximum_bytes=MAX_GRAPH_DOWNLOAD_RESPONSE_BYTES,
        opener=opener,
    )
    if (
        payload.get("@odata.type") != "#microsoft.graph.fileAttachment"
        or payload.get("id") != attachment_id
        or type(payload.get("size")) is not int
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    byte_count = payload["size"]
    if byte_count > MAX_FILE_BYTES:
        raise MicrosoftGraphError("microsoft_attachment_too_large")
    if byte_count < 0:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    raw = payload.get("contentBytes")
    if type(raw) is not str or len(raw) > 4 * ((MAX_FILE_BYTES + 2) // 3):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    try:
        contents = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error):
        raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
    if len(contents) != byte_count:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return {
        "attachmentId": attachment_id,
        "fileName": _download_filename(payload.get("name")),
        "size": byte_count,
        # Canonical base64, never untrusted Graph JSON values or HTML.
        "base64": base64.b64encode(contents).decode("ascii"),
    }
