"""IMAP provider adapter into the shared canonical mailbox records.

Only already-read MIME/flag data is projected here. RFC Message-ID is metadata;
the exact account, folder, UIDVALIDITY and UID identify a provider copy.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, replace
from datetime import timezone
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime

from api.inboxes.imap_uid_validity import is_canonical_uid_validity
from cuevion_mailbox.repository_contract import (
    BodyState, CachedBody, MailboxProvider, MailboxScope, MessageIdentity,
    MessageRecord, OutboxEventType,
)


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _opaque(prefix, value):
    token = base64.urlsafe_b64encode(bytes.fromhex(_digest(value))[:16]).decode("ascii")
    return prefix + token.rstrip("=")


def _authority(scope):
    if type(scope) is not MailboxScope or scope.provider is not MailboxProvider.CUSTOM_IMAP:
        raise ValueError("invalid IMAP scope")
    return [scope.workspace_id, scope.owner_user_id, scope.mailbox_id,
            scope.source_generation, scope.provider.value, scope.provider_account_identity]


def validate_imap_locator(folder, uid_validity, uid):
    if (
        type(folder) is not str or not folder or folder != folder.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in folder)
        or len(folder.encode("utf-8")) > 16_384
        or not is_canonical_uid_validity(uid_validity)
        or type(uid) is not int or not 1 <= uid <= 4_294_967_295
    ):
        raise ValueError("invalid IMAP locator")


def derive_imap_message_id(scope, folder, uid_validity, uid):
    validate_imap_locator(folder, uid_validity, uid)
    return _opaque("mbm_", _authority(scope) + [folder, uid_validity, uid])


def derive_imap_event_id(scope, message_id, row_version, event_type):
    if type(event_type) is not OutboxEventType or type(row_version) is not int or row_version < 1:
        raise ValueError("invalid IMAP outbox identity")
    return _opaque("mbe_", _authority(scope) + [message_id, row_version, event_type.value])


def _text(value):
    if type(value) is not str:
        raise ValueError("invalid IMAP text")
    return value.replace("\x00", "")


def project_imap_message(scope, *, folder, uid_validity, item):
    # Import lazily: the MIME parser also uses the authenticated IMAP modules.
    from imap_connect_preview import (
        clean_text, decode_mime_words, extract_message_thread_metadata,
        get_html_body, get_message_body,
    )

    if type(item) not in (tuple, list) or len(item) != 4:
        raise ValueError("invalid IMAP message")
    message, unread, uid_text, flagged = item
    if (
        not isinstance(message, Message) or type(unread) is not bool
        or type(flagged) is not bool or type(uid_text) is not str
        or not uid_text.isascii() or not uid_text.isdigit() or uid_text.startswith("0")
        or len(uid_text) > 10
    ):
        raise ValueError("invalid IMAP message")
    uid = int(uid_text)
    message_id = derive_imap_message_id(scope, folder, uid_validity, uid)
    html = get_html_body(message)
    text = _text(get_message_body(message, html_body=html))
    html = None if html is None else _text(html)
    content_hash = _digest([text, html])
    body = CachedBody(message_id, text, html, content_hash, 1, 1)
    metadata = extract_message_thread_metadata(message, uid_text, message_id)
    timestamp = 0
    try:
        date = parsedate_to_datetime(message.get("Date", ""))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        timestamp = max(0, int(date.timestamp() * 1000))
        if timestamp > 253_402_300_799_999:
            timestamp = 0
    except (ValueError, TypeError, OverflowError, IndexError):
        pass
    sender = getaddresses([decode_mime_words(message.get("From", ""))])
    sender_display, sender_address = sender[0] if sender else ("", "")
    sender_address = _text(sender_address) or None
    if sender_address is not None and len(sender_address.encode("utf-8")) > 320:
        raise ValueError("invalid IMAP sender")

    def recipients(header):
        return tuple(_text(address) for _, address in getaddresses([
            decode_mime_words(message.get(header, ""))
        ]) if address)

    record = MessageRecord(
        identity=MessageIdentity(message_id, None, folder, uid_validity, uid),
        provider_thread_id=None, provider_labels=(),
        rfc_message_id=metadata.get("message_id"),
        in_reply_to=metadata.get("in_reply_to"),
        references=tuple(metadata.get("references") or ()),
        sender_address=sender_address, sender_display=_text(sender_display) or None,
        to_recipients=recipients("To"), cc_recipients=recipients("Cc"),
        subject=_text(decode_mime_words(message.get("Subject", "Untitled message"))),
        snippet=clean_text(text.replace("\n", " "))[:220],
        provider_timestamp_millis=timestamp, unread=unread, starred=flagged,
        body_state=BodyState.CACHED, metadata_hash="0" * 64,
    )
    record = replace(record, metadata_hash=_digest([asdict(record), content_hash]))
    record.validate_for(MailboxProvider.CUSTOM_IMAP)
    return record, body
