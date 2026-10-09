from __future__ import annotations

from email import policy
import re
from email.message import EmailMessage

from api.inboxes.imap_folder_inventory import (
    is_runtime_compatible_mailbox_name,
    is_selectable_imap_list_entry,
    read_imap_list_inventory,
)

# Exact known folder names are a fallback only when LIST has no \Sent marker.
# Never create a folder or guess between multiple plausible matches.
_FALLBACK_SENT_NAMES = frozenset(
    {"sent", "sent messages", "sent items", "sent mail", "inbox.sent", "inbox/sent"}
)


class SentCopyStorageError(Exception):
    """Safe error code; never include user addresses or message bodies."""


def _imap_ok(value: object) -> bool:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeError:
            return False
    return isinstance(value, str) and value.casefold() == "ok"


def find_sent_folder(mailbox: object) -> str | None:
    r"""Resolve one selectable, server-listed folder; prefer RFC 6154 \Sent."""
    inventory = read_imap_list_inventory(mailbox)
    if inventory.error or inventory.entries is None:
        return None
    entries = tuple(
        entry
        for entry in inventory.entries
        if is_selectable_imap_list_entry(entry)
        and is_runtime_compatible_mailbox_name(entry.mailbox)
    )
    marked = {entry.mailbox for entry in entries if r"\sent" in entry.attributes}
    if marked:
        return next(iter(marked)) if len(marked) == 1 else None
    fallback = {
        entry.mailbox
        for entry in entries
        if entry.mailbox.casefold() in _FALLBACK_SENT_NAMES
    }
    return next(iter(fallback)) if len(fallback) == 1 else None


def select_sent_folder(mailbox: object, folder: str) -> None:
    if not is_runtime_compatible_mailbox_name(folder):
        raise SentCopyStorageError("sent_folder_invalid")
    try:
        status, _ = mailbox.select(folder, readonly=False)
        if not _imap_ok(status):
            raise SentCopyStorageError("sent_folder_unavailable")
    except SentCopyStorageError:
        raise
    except Exception:
        raise SentCopyStorageError("sent_folder_unavailable") from None


def append_sent_copy(mailbox: object, folder: str, message: EmailMessage) -> str:
    """Append exactly the SMTP MIME message; return stored/already_stored.

    A provider might save its own SMTP messages. Search the generated
    Message-ID first to avoid an obvious duplicate. A failed SEARCH should
    not prevent the explicit APPEND needed by IMAP/iCloud mailboxes.
    """
    if not is_runtime_compatible_mailbox_name(folder):
        raise SentCopyStorageError("sent_folder_invalid")
    message_id = message.get("Message-ID")
    if not isinstance(message_id, str) or not message_id.startswith("<") or not message_id.endswith(">"):
        raise SentCopyStorageError("sent_message_id_missing")

    try:
        # IMAP HEADER arguments are strings: quote the safe generated RFC
        # identifier rather than sending angle brackets as an invalid atom.
        quoted_id = '"' + message_id + '"'
        status, matches = mailbox.uid("SEARCH", None, "HEADER", "Message-ID", quoted_id)
        if _imap_ok(status) and isinstance(matches, (list, tuple)):
            if any(
                isinstance(v, bytes) and re.fullmatch(rb"[0-9]+(?: +[0-9]+)*", v.strip())
                for v in matches
            ):
                return "already_stored"
    except Exception:
        # APPEND remains the authoritative operation when SEARCH fails.
        pass

    try:
        status, _ = mailbox.append(folder, r"(\Seen)", None, message.as_bytes(policy=policy.SMTP))
        if not _imap_ok(status):
            raise SentCopyStorageError("sent_append_failed")
    except SentCopyStorageError:
        raise
    except Exception:
        raise SentCopyStorageError("sent_append_failed") from None
    return "stored"
