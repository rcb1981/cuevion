"""Pure Gmail snapshot projection into durable mailbox message records.

This module has no runtime configuration, network I/O, database access, or
provider calls. It only validates already-parsed Gmail snapshot data and
produces canonical MessageRecord values for a caller-owned MailboxScope.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Sequence

from cuevion_mailbox.repository_contract import (
    BodyState,
    MailboxProvider,
    MailboxScope,
    MessageIdentity,
    MessageRecord,
)


_MAX_PROVIDER_MESSAGE_ID_BYTES = 1_024
_MAX_PROVIDER_THREAD_ID_BYTES = 1_024
_MAX_PROVIDER_FOLDER_BYTES = 16_384
_MAX_SENDER_ADDRESS_BYTES = 320
_MAX_GMAIL_SNAPSHOT_MESSAGES = 100
_MAX_PROVIDER_TIMESTAMP_MILLIS = 253_402_300_799_999


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeError:
        raise ValueError("invalid Gmail durable projection") from None


def _required_text(value: object, *, maximum_bytes: int | None = None) -> str:
    if type(value) is not str or not value:
        raise ValueError("invalid Gmail durable projection")
    if maximum_bytes is not None and not 1 <= _utf8_size(value) <= maximum_bytes:
        raise ValueError("invalid Gmail durable projection")
    return value


def _optional_text(value: object, *, maximum_bytes: int | None = None) -> str | None:
    if value is None or value == "":
        return None
    return _required_text(value, maximum_bytes=maximum_bytes)


def _string(value: object) -> str:
    if type(value) is not str:
        raise ValueError("invalid Gmail durable projection")
    _utf8_size(value)
    return value


def _header_tuple(value: object) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    return (_required_text(value),)


def _provider_timestamp_millis(value: object) -> int:
    if type(value) is int and not isinstance(value, bool):
        result = value
    elif type(value) is str and value.isascii() and value.isdigit():
        result = int(value)
    else:
        raise ValueError("invalid Gmail durable projection")
    if not 0 <= result <= _MAX_PROVIDER_TIMESTAMP_MILLIS:
        raise ValueError("invalid Gmail durable projection")
    return result


def _labels(value: object) -> tuple[str, ...]:
    if type(value) not in (list, tuple):
        raise ValueError("invalid Gmail durable projection")
    result = tuple(
        sorted(
            {
                _required_text(item)
                for item in value
            }
        )
    )
    if len(result) != len(value):
        raise ValueError("invalid Gmail durable projection")
    return result


def derive_gmail_message_id(
    scope: MailboxScope,
    provider_message_id: str,
) -> str:
    """Derive one opaque 26-character internal id for one source generation."""

    if type(scope) is not MailboxScope or scope.provider is not MailboxProvider.GOOGLE:
        raise ValueError("invalid Gmail durable projection")
    provider_id = _required_text(
        provider_message_id,
        maximum_bytes=_MAX_PROVIDER_MESSAGE_ID_BYTES,
    )
    material = "\x1f".join(
        (
            scope.workspace_id,
            scope.owner_user_id,
            scope.mailbox_id,
            str(scope.source_generation),
            scope.provider.value,
            scope.provider_account_identity,
            provider_id,
        )
    ).encode("utf-8", errors="strict")
    digest = hashlib.sha256(material).digest()[:16]
    token = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    if len(token) != 22:
        raise RuntimeError("invalid Gmail durable projection")
    return "mbm_" + token


def _metadata_hash(
    *,
    provider_message_id: str,
    provider_thread_id: str | None,
    provider_folder: str,
    provider_labels: tuple[str, ...],
    rfc_message_id: str | None,
    sender_address: str | None,
    sender_display: str | None,
    to_recipients: tuple[str, ...],
    cc_recipients: tuple[str, ...],
    subject: str,
    snippet: str,
    provider_timestamp_millis: int,
    unread: bool,
    starred: bool,
) -> str:
    payload = {
        "provider_message_id": provider_message_id,
        "provider_thread_id": provider_thread_id,
        "provider_folder": provider_folder,
        "provider_labels": provider_labels,
        "rfc_message_id": rfc_message_id,
        "sender_address": sender_address,
        "sender_display": sender_display,
        "to_recipients": to_recipients,
        "cc_recipients": cc_recipients,
        "subject": subject,
        "snippet": snippet,
        "provider_timestamp_millis": provider_timestamp_millis,
        "unread": unread,
        "starred": starred,
        "body_state": BodyState.NOT_CACHED.value,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(encoded).hexdigest()


def project_gmail_snapshot_message(
    scope: MailboxScope,
    preview: object,
    source: object,
) -> MessageRecord:
    """Project one already-parsed Gmail snapshot row into durable metadata."""

    if (
        type(scope) is not MailboxScope
        or scope.provider is not MailboxProvider.GOOGLE
        or type(preview) is not dict
        or type(source) is not dict
        or source.get("provider") != "google"
    ):
        raise ValueError("invalid Gmail durable projection")

    provider_message_id = _required_text(
        source.get("providerMessageId"),
        maximum_bytes=_MAX_PROVIDER_MESSAGE_ID_BYTES,
    )
    if preview.get("providerMessageId") != provider_message_id:
        raise ValueError("invalid Gmail durable projection")

    provider_thread_id = _optional_text(
        source.get("providerThreadId"),
        maximum_bytes=_MAX_PROVIDER_THREAD_ID_BYTES,
    )
    preview_thread_id = preview.get("providerThreadId")
    if preview_thread_id is not None and preview_thread_id != provider_thread_id:
        raise ValueError("invalid Gmail durable projection")

    provider_folder = _required_text(
        source.get("providerFolder"),
        maximum_bytes=_MAX_PROVIDER_FOLDER_BYTES,
    )
    preview_folder = preview.get("providerFolder")
    if (
        type(preview_folder) is not str
        or preview_folder.casefold() != provider_folder.casefold()
    ):
        raise ValueError("invalid Gmail durable projection")

    provider_labels = _labels(source.get("labels"))
    preview_labels = _labels(preview.get("labelIds"))
    if provider_labels != preview_labels:
        raise ValueError("invalid Gmail durable projection")

    sender_address = _optional_text(
        source.get("senderAddress"),
        maximum_bytes=_MAX_SENDER_ADDRESS_BYTES,
    )
    sender_display = _optional_text(source.get("senderDisplay"))
    subject = _string(source.get("subject"))
    snippet = _string(source.get("snippet"))
    unread = source.get("unread")
    starred = source.get("flagged")
    if type(unread) is not bool or type(starred) is not bool:
        raise ValueError("invalid Gmail durable projection")

    provider_timestamp_millis = _provider_timestamp_millis(
        source.get("providerTimestampMillis")
    )
    rfc_message_id = _optional_text(preview.get("rfcMessageId"))
    to_recipients = _header_tuple(preview.get("to"))
    cc_recipients = _header_tuple(preview.get("cc"))

    identity = MessageIdentity(
        message_id=derive_gmail_message_id(scope, provider_message_id),
        provider_message_id=provider_message_id,
        provider_folder=provider_folder,
        imap_uid_validity=None,
        imap_uid=None,
    )
    record = MessageRecord(
        identity=identity,
        provider_thread_id=provider_thread_id,
        provider_labels=provider_labels,
        rfc_message_id=rfc_message_id,
        in_reply_to=None,
        references=(),
        sender_address=sender_address,
        sender_display=sender_display,
        to_recipients=to_recipients,
        cc_recipients=cc_recipients,
        subject=subject,
        snippet=snippet,
        provider_timestamp_millis=provider_timestamp_millis,
        unread=unread,
        starred=starred,
        body_state=BodyState.NOT_CACHED,
        metadata_hash=_metadata_hash(
            provider_message_id=provider_message_id,
            provider_thread_id=provider_thread_id,
            provider_folder=provider_folder,
            provider_labels=provider_labels,
            rfc_message_id=rfc_message_id,
            sender_address=sender_address,
            sender_display=sender_display,
            to_recipients=to_recipients,
            cc_recipients=cc_recipients,
            subject=subject,
            snippet=snippet,
            provider_timestamp_millis=provider_timestamp_millis,
            unread=unread,
            starred=starred,
        ),
    )
    record.validate_for(MailboxProvider.GOOGLE)
    return record


def project_gmail_snapshot(
    scope: MailboxScope,
    previews: Sequence[object],
    sources: Sequence[object],
) -> tuple[MessageRecord, ...]:
    """Project a bounded snapshot while preserving the preview ordering."""

    if (
        type(scope) is not MailboxScope
        or scope.provider is not MailboxProvider.GOOGLE
        or type(previews) not in (list, tuple)
        or type(sources) not in (list, tuple)
        or len(previews) > _MAX_GMAIL_SNAPSHOT_MESSAGES
        or len(sources) > _MAX_GMAIL_SNAPSHOT_MESSAGES
    ):
        raise ValueError("invalid Gmail durable projection")

    by_provider_id: dict[str, dict] = {}
    for source in sources:
        if type(source) is not dict:
            raise ValueError("invalid Gmail durable projection")
        provider_id = _required_text(
            source.get("providerMessageId"),
            maximum_bytes=_MAX_PROVIDER_MESSAGE_ID_BYTES,
        )
        if provider_id in by_provider_id:
            raise ValueError("invalid Gmail durable projection")
        by_provider_id[provider_id] = source

    projected: list[MessageRecord] = []
    seen_preview_ids: set[str] = set()
    for preview in previews:
        if type(preview) is not dict:
            raise ValueError("invalid Gmail durable projection")
        provider_id = _required_text(
            preview.get("providerMessageId"),
            maximum_bytes=_MAX_PROVIDER_MESSAGE_ID_BYTES,
        )
        if provider_id in seen_preview_ids:
            raise ValueError("invalid Gmail durable projection")
        seen_preview_ids.add(provider_id)
        source = by_provider_id.get(provider_id)
        if source is None:
            raise ValueError("invalid Gmail durable projection")
        projected.append(project_gmail_snapshot_message(scope, preview, source))
    return tuple(projected)


__all__ = (
    "derive_gmail_message_id",
    "project_gmail_snapshot",
    "project_gmail_snapshot_message",
)
