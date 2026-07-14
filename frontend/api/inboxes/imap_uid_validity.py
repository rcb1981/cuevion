from __future__ import annotations

import re


_CANONICAL_MODULE_NAME = "api.inboxes.imap_uid_validity"
if __name__ != _CANONICAL_MODULE_NAME:
    raise ImportError(
        "IMAP UIDVALIDITY helpers must be imported as "
        "api.inboxes.imap_uid_validity"
    )


_UIDVALIDITY_PATTERN = re.compile(r"[1-9][0-9]{0,19}", re.ASCII)


def parse_uid_validity(value: object) -> str | None:
    """Return an exact canonical UIDVALIDITY string, or fail closed."""
    if type(value) is bytes:
        try:
            value = value.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            return None
    if type(value) is not str or _UIDVALIDITY_PATTERN.fullmatch(value) is None:
        return None
    return value


def is_canonical_uid_validity(value: object) -> bool:
    """Validate request text without accepting byte or string subclasses."""
    return type(value) is str and parse_uid_validity(value) == value


def parse_uid_validity_response(response_tag: object, values: object) -> str | None:
    """Parse the exact imaplib ``response('UIDVALIDITY')`` result shape."""
    if type(response_tag) is not str or response_tag != "UIDVALIDITY":
        return None
    if type(values) not in (list, tuple) or len(values) != 1:
        return None
    return parse_uid_validity(values[0])


def read_selected_mailbox_uid_validity(mailbox: object) -> str | None:
    """Read the UIDVALIDITY response code captured by the mailbox SELECT."""
    try:
        response_tag, values = mailbox.response("UIDVALIDITY")
    except Exception:
        return None
    return parse_uid_validity_response(response_tag, values)
