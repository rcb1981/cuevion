"""Bounded, read-only file download from one exact Microsoft mail attachment.

No provider URLs, OAuth tokens, file contents, or temporary storage are
accepted from the browser. Only Graph file attachments at or below 2 MiB
are supported in this limited read-only pilot.
"""
from __future__ import annotations

import base64
import binascii
import unicodedata
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from api.inboxes import microsoft_graph
from api.inboxes.microsoft_graph import (
    MicrosoftGraphError, _get_json, _required_message_id, _required_access_token,
)

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


def _raw_verified_file_attachment(
    access_token: str, message_id: str, attachment_id: str,
    *, folder: str, mapped_folder: str, opener=urlopen,
) -> dict:
    """Fallback only for invalid Graph JSON: verify exact Graph metadata, then $value.

    Graph can omit contentBytes in its attachment object. The raw $value route
    is documented for file attachments; it must never be used for unverified
    item or reference attachments, or browser-supplied URLs.
    """
    from api.inboxes.microsoft_attachments_graph import list_microsoft_attachments

    try:
        metadata = list_microsoft_attachments(
            access_token, message_id, folder=folder, opener=opener,
        )
    except MicrosoftGraphError as error:
        if error.code != "microsoft_graph_invalid_response":
            raise
        raise MicrosoftGraphError(
            "microsoft_graph_invalid_response", safe_stage="download_metadata",
        ) from None
    matching = [
        a for a in metadata["attachments"]
        if a["id"] == attachment_id and a["kind"] == "file"
    ]
    if len(matching) != 1:
        raise MicrosoftGraphError(
            "microsoft_graph_invalid_response", safe_stage="download_file_type",
        )
    item = matching[0]
    expected_size = item["size"]
    if expected_size > MAX_FILE_BYTES:
        raise MicrosoftGraphError("microsoft_attachment_too_large")
    if expected_size < 0:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")

    path = (
        f"/me/mailFolders/{mapped_folder}/messages/"
        f"{quote(_required_message_id(message_id), safe='')}/attachments/"
        f"{quote(_required_message_id(attachment_id), safe='')}/$value"
    )
    request = Request(
        microsoft_graph.GRAPH_ROOT + path,
        headers={
            "Authorization": "Bearer " + _required_access_token(access_token),
            "Accept": "application/octet-stream",
            "Prefer": microsoft_graph._GRAPH_PREFER,
        },
        method="GET",
    )
    try:
        with (
            microsoft_graph._GRAPH_OPENER.open if opener is urlopen else opener
        )(request, timeout=microsoft_graph.GRAPH_TIMEOUT_SECONDS) as response:
            data = response.read(MAX_FILE_BYTES + 1)
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
    if type(data) is not bytes or len(data) > MAX_FILE_BYTES or len(data) != expected_size:
        raise MicrosoftGraphError(
            "microsoft_graph_invalid_response", safe_stage="download_raw_size",
        )

    return {
        "attachmentId": attachment_id,
        "fileName": _download_filename(item["name"]),
        "size": len(data),
        "base64": base64.b64encode(data).decode("ascii"),
    }


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
    try:
        payload = _get_json(
            path, access_token,
            maximum_bytes=MAX_GRAPH_DOWNLOAD_RESPONSE_BYTES,
            opener=opener,
        )
    except MicrosoftGraphError as error:
        if error.code != "microsoft_graph_invalid_response":
            raise
        # The detail endpoint sent invalid/non-JSON content. Recover using
        # separately verified exact-message file metadata and raw Graph bytes.
        return _raw_verified_file_attachment(
            access_token, message_id, attachment_id,
            folder=folder, mapped_folder=mapped_folder, opener=opener,
        )

    if (
        payload.get("@odata.type") not in (
            "#microsoft.graph.fileAttachment", "microsoft.graph.fileAttachment"
        )
        or payload.get("id") != attachment_id
        or type(payload.get("size")) is not int
    ):
        return _raw_verified_file_attachment(
            access_token, message_id, attachment_id,
            folder=folder, mapped_folder=mapped_folder, opener=opener,
        )
    byte_count = payload["size"]
    if byte_count > MAX_FILE_BYTES:
        raise MicrosoftGraphError("microsoft_attachment_too_large")
    if byte_count < 0:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    raw = payload.get("contentBytes")
    if type(raw) is not str or len(raw) > 4 * ((MAX_FILE_BYTES + 2) // 3):
        return _raw_verified_file_attachment(
            access_token, message_id, attachment_id,
            folder=folder, mapped_folder=mapped_folder, opener=opener,
        )
    try:
        contents = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error):
        return _raw_verified_file_attachment(
            access_token, message_id, attachment_id,
            folder=folder, mapped_folder=mapped_folder, opener=opener,
        )
    if len(contents) != byte_count:
        return _raw_verified_file_attachment(
            access_token, message_id, attachment_id,
            folder=folder, mapped_folder=mapped_folder, opener=opener,
        )
    return {
        "attachmentId": attachment_id,
        "fileName": _download_filename(payload.get("name")),
        "size": byte_count,
        # Canonical base64, never untrusted Graph JSON values or HTML.
        "base64": base64.b64encode(contents).decode("ascii"),
    }
