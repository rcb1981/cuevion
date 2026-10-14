"""Fail-closed validation of server-originated Microsoft Graph pagination links.

The caller supplies the expected, fixed mailbox folder. Continuation URLs
never cross the HTTP boundary, and cannot select an arbitrary mailbox or host.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit


class InvalidMicrosoftContinuation(ValueError):
    pass


_FOLDER_PATH = re.compile(
    r"^/v1[.]0/(?:me|users\([^/)]{1,256}\))/mailfolders"
    r"(?:/([^/?]+)|\(([^/?]+)\))/messages$",
    re.IGNORECASE,
)
_WELL_KNOWN_FOLDERS = frozenset({
    "inbox", "sentitems", "drafts", "deleteditems", "archive",
    "junkemail", "outbox", "recoverableitemsdeletions",
})
_UNSAFE_PATH_ESCAPES = re.compile(r"%(?:2e|2f|5c|25|3f|23)", re.IGNORECASE)


def validated_microsoft_continuation_path(value: object, *, expected_folder: str) -> str:
    """Validate a Graph-produced nextLink for exactly the requested mail folder.

    The well-known Inbox/Sent paths are accepted, plus opaque Graph folder IDs.
    A user path, cross-folder route, custom host, userinfo, or encoded path
    traversal must fail closed. A continuation marker is mandatory.
    """
    if expected_folder not in {"inbox", "sentitems"}:
        raise InvalidMicrosoftContinuation
    if not isinstance(value, str) or not 1 <= len(value) <= 8192:
        raise InvalidMicrosoftContinuation
    if "\\" in value or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise InvalidMicrosoftContinuation
    try:
        parsed = urlsplit(value)
        match = _FOLDER_PATH.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "graph.microsoft.com"
            or not parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or match is None
            or _UNSAFE_PATH_ESCAPES.search(parsed.path)
        ):
            raise InvalidMicrosoftContinuation
        folder = (match.group(1) or match.group(2)).strip("'")
        normalized_folder = folder.casefold()
        if normalized_folder != expected_folder:
            # Graph can substitute an opaque immutable folder ID for the
            # well-known name. Short names and known other folders are denied.
            if normalized_folder in _WELL_KNOWN_FOLDERS or (
                len(folder) < 20 and not folder.lower().startswith("aamk")
            ):
                raise InvalidMicrosoftContinuation
        if len(parsed.query) > 6144:
            raise InvalidMicrosoftContinuation
        markers = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        if "$skiptoken" not in markers and "$skip" not in markers:
            raise InvalidMicrosoftContinuation
    except (ValueError, UnicodeError):
        raise InvalidMicrosoftContinuation from None
    return parsed.path[len("/v1.0"):] + "?" + parsed.query
