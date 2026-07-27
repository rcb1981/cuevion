from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TypedDict

from .imap_uid_validity import (
    is_canonical_uid_validity,
    read_selected_mailbox_uid_validity,
)


_IMAP_UID_PATTERN = re.compile(r"[1-9][0-9]*", re.ASCII)
_LIST_LITERAL_SUFFIX_PATTERN = re.compile(r"\{([0-9]+)\}\Z", re.ASCII)
_MAX_IMAP_UID = 4_294_967_295
_MAX_LIST_ENTRIES = 4_096
_MAX_LIST_LINE_LENGTH = 16_384

_ARCHIVE_ATTRIBUTE = r"\archive"
_NOSELECT_ATTRIBUTE = r"\noselect"
_NONEXISTENT_ATTRIBUTE = r"\nonexistent"
_CONFLICTING_SPECIAL_USE_ATTRIBUTES = frozenset(
    {
        r"\all",
        r"\drafts",
        r"\flagged",
        r"\junk",
        r"\sent",
        r"\trash",
    }
)

_ERROR_MESSAGES = {
    "invalid_source_folder": "The source mailbox folder is invalid.",
    "invalid_imap_uid": "The IMAP message UID is invalid.",
    "invalid_uid_validity": "The expected IMAP UIDVALIDITY is invalid.",
    "archive_folder_unavailable": "No selectable Archive mailbox is available.",
    "archive_folder_ambiguous": "More than one selectable Archive mailbox is available.",
    "archive_move_unsupported": "This IMAP server does not support safe message moves.",
    "source_folder_unavailable": "The source mailbox folder could not be opened.",
    "uid_validity_unavailable": "The source mailbox UIDVALIDITY could not be verified.",
    "uid_validity_changed": "The source mailbox changed since the message was fetched.",
    "archive_message_not_found": "The source message no longer exists.",
    "archive_move_failed": "The IMAP server did not confirm the Archive move.",
    "archive_move_unconfirmed": "The Archive move could not be confirmed.",
    "imap_archive_failed": "The message could not be archived through IMAP.",
}


class ImapArchiveError(TypedDict):
    code: str
    message: str
    stage: str


class ImapArchiveResult(TypedDict):
    ok: bool
    status: Literal["ok", "error"]
    source_folder: str | None
    archive_folder: str | None
    uid: str | None
    uid_validity: str | None
    confirmation: Literal["source_removed"] | None
    error: ImapArchiveError | None


@dataclass(frozen=True)
class ImapListEntry:
    attributes: frozenset[str]
    delimiter: str | None
    mailbox: str


def _failure(code: str, stage: str) -> ImapArchiveResult:
    return {
        "ok": False,
        "status": "error",
        "source_folder": None,
        "archive_folder": None,
        "uid": None,
        "uid_validity": None,
        "confirmation": None,
        "error": {
            "code": code,
            "message": _ERROR_MESSAGES[code],
            "stage": stage,
        },
    }


def _success(
    *,
    source_folder: str,
    archive_folder: str,
    uid: str,
    uid_validity: str,
) -> ImapArchiveResult:
    return {
        "ok": True,
        "status": "ok",
        "source_folder": source_folder,
        "archive_folder": archive_folder,
        "uid": uid,
        "uid_validity": uid_validity,
        "confirmation": "source_removed",
        "error": None,
    }


def _contains_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _decode_list_line(value: object) -> str | None:
    if type(value) is str:
        text = value
        try:
            encoded_length = len(value.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            return None
    elif type(value) is bytes:
        encoded_length = len(value)
        try:
            text = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
    else:
        return None

    if (
        not text
        or len(text) > _MAX_LIST_LINE_LENGTH
        or encoded_length > _MAX_LIST_LINE_LENGTH
        or _contains_control_characters(text)
    ):
        return None
    return text


def _parse_quoted_string(value: str, start: int) -> tuple[str, int] | None:
    if start >= len(value) or value[start] != '"':
        return None

    parsed: list[str] = []
    index = start + 1
    while index < len(value):
        character = value[index]
        if character == '"':
            return "".join(parsed), index + 1
        if character == "\\":
            index += 1
            if index >= len(value) or value[index] not in {'"', "\\"}:
                return None
            parsed.append(value[index])
        else:
            if ord(character) < 32 or ord(character) == 127:
                return None
            parsed.append(character)
        index += 1
    return None


def _skip_spaces(value: str, start: int) -> int | None:
    if start >= len(value) or value[start] != " ":
        return None
    index = start + 1
    if index < len(value) and value[index] == " ":
        return None
    return index


def _valid_attribute_token(value: str) -> bool:
    if not value.startswith("\\") or len(value) == 1 or not value.isascii():
        return False
    atom = value[1:]
    return not any(
        ord(character) < 33
        or ord(character) > 126
        or character in '(){}%*]"\\'
        for character in atom
    )


def _valid_mailbox_atom(value: str) -> bool:
    return (
        bool(value)
        and not _contains_control_characters(value)
        and not any(
            character.isspace() or character in '(){}"\\%*'
            for character in value
        )
    )


def parse_imap_list_entry(value: object) -> ImapListEntry | None:
    """Parse one complete IMAP LIST response without guessing mailbox syntax."""
    if type(value) is tuple:
        if len(value) != 2:
            return None
        prefix = _decode_list_line(value[0])
        literal = _decode_list_line(value[1])
        if prefix is None or literal is None:
            return None
        literal_match = _LIST_LITERAL_SUFFIX_PATTERN.search(prefix)
        if literal_match is None:
            return None
        expected_size = literal_match.group(1).lstrip("0") or "0"
        if type(value[1]) is bytes:
            actual_size = len(value[1])
        else:
            actual_size = len(value[1].encode("utf-8"))
        if expected_size != str(actual_size):
            return None
        escaped_literal = literal.replace("\\", "\\\\").replace('"', '\\"')
        value = (
            prefix[:literal_match.start()]
            + '"'
            + escaped_literal
            + '"'
        )

    text = _decode_list_line(value)
    if text is None or not text.startswith("("):
        return None

    attributes_end = text.find(")", 1)
    if attributes_end < 0:
        return None
    attributes_text = text[1:attributes_end]
    attribute_tokens = (
        tuple(attributes_text.split(" "))
        if attributes_text
        else ()
    )
    if any(not _valid_attribute_token(token) for token in attribute_tokens):
        return None
    attributes = frozenset(token.casefold() for token in attribute_tokens)

    index = _skip_spaces(text, attributes_end + 1)
    if index is None or index >= len(text):
        return None

    delimiter: str | None
    if text[index] == '"':
        parsed_delimiter = _parse_quoted_string(text, index)
        if parsed_delimiter is None:
            return None
        delimiter, index = parsed_delimiter
        if len(delimiter) != 1:
            return None
    else:
        delimiter_end = text.find(" ", index)
        if delimiter_end < 0 or text[index:delimiter_end].casefold() != "nil":
            return None
        delimiter = None
        index = delimiter_end

    index = _skip_spaces(text, index)
    if index is None or index >= len(text):
        return None

    if text[index] == '"':
        parsed_mailbox = _parse_quoted_string(text, index)
        if parsed_mailbox is None:
            return None
        mailbox, index = parsed_mailbox
        if index != len(text) or not mailbox:
            return None
    else:
        mailbox = text[index:]
        if not _valid_mailbox_atom(mailbox):
            return None

    return ImapListEntry(
        attributes=attributes,
        delimiter=delimiter,
        mailbox=mailbox,
    )


def _is_ok_status(value: object) -> bool:
    if type(value) is bytes:
        try:
            value = value.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            return False
    return type(value) is str and value.casefold() == "ok"


def _response_parts(response: object) -> tuple[object, object] | None:
    if type(response) not in (list, tuple) or len(response) != 2:
        return None
    return response[0], response[1]


def _discover_archive_folder(mailbox: object) -> tuple[str | None, str | None]:
    try:
        response = mailbox.list()
    except Exception:
        return None, "archive_folder_unavailable"

    parts = _response_parts(response)
    if parts is None or not _is_ok_status(parts[0]):
        return None, "archive_folder_unavailable"
    response_entries = parts[1]
    if (
        type(response_entries) not in (list, tuple)
        or len(response_entries) > _MAX_LIST_ENTRIES
    ):
        return None, "archive_folder_unavailable"

    candidates: list[ImapListEntry] = []
    entry_index = 0
    while entry_index < len(response_entries):
        raw_entry = response_entries[entry_index]
        entry = parse_imap_list_entry(raw_entry)
        if entry is None:
            return None, "archive_folder_unavailable"
        entry_index += 1
        if (
            type(raw_entry) is tuple
            and entry_index < len(response_entries)
            and response_entries[entry_index] in (b"", "")
        ):
            entry_index += 1
        if _ARCHIVE_ATTRIBUTE not in entry.attributes:
            continue
        if (
            _NOSELECT_ATTRIBUTE in entry.attributes
            or _NONEXISTENT_ATTRIBUTE in entry.attributes
        ):
            continue
        candidates.append(entry)

    if not candidates:
        return None, "archive_folder_unavailable"
    if len(candidates) != 1:
        return None, "archive_folder_ambiguous"
    candidate = candidates[0]
    if candidate.attributes & _CONFLICTING_SPECIAL_USE_ATTRIBUTES:
        return None, "archive_folder_unavailable"
    return candidate.mailbox, None


def _decode_ascii(value: object) -> str | None:
    if type(value) is bytes:
        try:
            return value.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            return None
    if type(value) is str:
        try:
            value.encode("ascii", errors="strict")
        except UnicodeEncodeError:
            return None
        return value
    return None


def _parse_capability_tokens(value: object) -> frozenset[str] | None:
    values = value if type(value) in (list, tuple, set, frozenset) else (value,)
    tokens: set[str] = set()
    for item in values:
        text = _decode_ascii(item)
        if text is None or _contains_control_characters(text):
            return None
        for token in text.split():
            if not token or any(ord(character) < 33 or ord(character) > 126 for character in token):
                return None
            tokens.add(token.casefold())
    return frozenset(tokens)


def _mailbox_supports_move(mailbox: object) -> bool:
    try:
        capability_method = getattr(mailbox, "capability", None)
    except Exception:
        return False

    if callable(capability_method):
        try:
            response = capability_method()
        except Exception:
            return False
        parts = _response_parts(response)
        if parts is None or not _is_ok_status(parts[0]):
            return False
        capabilities = _parse_capability_tokens(parts[1])
        return capabilities is not None and "move" in capabilities

    try:
        capabilities = _parse_capability_tokens(
            getattr(mailbox, "capabilities", None)
        )
    except Exception:
        return False
    return capabilities is not None and "move" in capabilities


def _valid_source_folder(value: object) -> bool:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or _contains_control_characters(value)
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= _MAX_LIST_LINE_LENGTH
    except UnicodeEncodeError:
        return False


def _valid_imap_uid(value: object) -> bool:
    if type(value) is not str or _IMAP_UID_PATTERN.fullmatch(value) is None:
        return False
    maximum = str(_MAX_IMAP_UID)
    return len(value) < len(maximum) or (
        len(value) == len(maximum) and value <= maximum
    )


def _same_mailbox_name(left: str, right: str) -> bool:
    return left.casefold() == right.casefold()


def _quote_mailbox_argument(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse_uid_search_data(value: object) -> tuple[str, ...] | None:
    if type(value) not in (list, tuple) or len(value) != 1:
        return None
    text = _decode_ascii(value[0])
    if text is None or _contains_control_characters(text):
        return None
    if not text:
        return ()
    if text != text.strip():
        return None
    tokens = tuple(text.split())
    if any(not _valid_imap_uid(token) for token in tokens):
        return None
    return tokens


def _source_uid_state(
    mailbox: object,
    uid: str,
) -> Literal["present", "absent", "indeterminate"]:
    try:
        response = mailbox.uid("SEARCH", None, "UID", uid)
    except Exception:
        return "indeterminate"
    parts = _response_parts(response)
    if parts is None or not _is_ok_status(parts[0]):
        return "indeterminate"
    matches = _parse_uid_search_data(parts[1])
    if matches == ():
        return "absent"
    if matches == (uid,):
        return "present"
    return "indeterminate"


def _archive_validated_imap_message(
    mailbox: object,
    *,
    source_folder: str,
    uid: str,
    expected_uid_validity: str,
) -> ImapArchiveResult:
    archive_folder, discovery_error = _discover_archive_folder(mailbox)
    if discovery_error is not None or archive_folder is None:
        return _failure(
            discovery_error or "archive_folder_unavailable",
            "archive_discovery",
        )

    if _same_mailbox_name(source_folder, archive_folder):
        return _failure("invalid_source_folder", "target_validation")

    if not _mailbox_supports_move(mailbox):
        return _failure("archive_move_unsupported", "move_capability")

    try:
        select_response = mailbox.select(
            _quote_mailbox_argument(source_folder)
        )
    except Exception:
        return _failure("source_folder_unavailable", "source_selection")
    select_parts = _response_parts(select_response)
    if select_parts is None or not _is_ok_status(select_parts[0]):
        return _failure("source_folder_unavailable", "source_selection")

    try:
        current_uid_validity = read_selected_mailbox_uid_validity(mailbox)
    except Exception:
        current_uid_validity = None
    if current_uid_validity is None:
        return _failure("uid_validity_unavailable", "uid_validity")
    if current_uid_validity != expected_uid_validity:
        return _failure("uid_validity_changed", "uid_validity")

    source_state = _source_uid_state(mailbox, uid)
    if source_state == "absent":
        return _failure("archive_message_not_found", "source_existence")
    if source_state != "present":
        return _failure("imap_archive_failed", "source_existence")

    try:
        move_response = mailbox.uid(
            "MOVE",
            uid,
            _quote_mailbox_argument(archive_folder),
        )
    except Exception:
        return _failure("archive_move_unconfirmed", "move")
    move_parts = _response_parts(move_response)
    if move_parts is None or not _is_ok_status(move_parts[0]):
        return _failure("archive_move_failed", "move")

    if _source_uid_state(mailbox, uid) != "absent":
        return _failure("archive_move_unconfirmed", "postcondition")

    return _success(
        source_folder=source_folder,
        archive_folder=archive_folder,
        uid=uid,
        uid_validity=expected_uid_validity,
    )


def archive_imap_message(
    mailbox: object,
    *,
    source_folder: str,
    uid: str,
    expected_uid_validity: str,
) -> ImapArchiveResult:
    """Move one exact IMAP UID and confirm removal from the selected source."""
    if not _valid_source_folder(source_folder):
        return _failure("invalid_source_folder", "input_validation")
    if not _valid_imap_uid(uid):
        return _failure("invalid_imap_uid", "input_validation")
    if not is_canonical_uid_validity(expected_uid_validity):
        return _failure("invalid_uid_validity", "input_validation")

    try:
        return _archive_validated_imap_message(
            mailbox,
            source_folder=source_folder,
            uid=uid,
            expected_uid_validity=expected_uid_validity,
        )
    except Exception:
        return _failure("imap_archive_failed", "archive")
