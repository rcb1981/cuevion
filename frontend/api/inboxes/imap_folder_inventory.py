from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


_LIST_LITERAL_SUFFIX_PATTERN = re.compile(r"\{([0-9]+)\}\Z", re.ASCII)
_MAX_LIST_ENTRIES = 4_096
_MAX_LIST_LINE_LENGTH = 16_384

_NOSELECT_ATTRIBUTE = r"\noselect"
_NONEXISTENT_ATTRIBUTE = r"\nonexistent"


@dataclass(frozen=True)
class ImapListEntry:
    attributes: frozenset[str]
    delimiter: str | None
    mailbox: str


@dataclass(frozen=True)
class ImapListInventoryResult:
    entries: tuple[ImapListEntry, ...] | None
    error: Literal["list_unavailable"] | None


def _contains_control_characters(value: str) -> bool:
    return any(
        ord(character) < 32 or 127 <= ord(character) <= 159
        for character in value
    )


def _contains_list_syntax_control_characters(value: str) -> bool:
    """Preserve the established LIST parser contract used by Archive."""
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
        or _contains_list_syntax_control_characters(text)
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
            if _contains_list_syntax_control_characters(character):
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
        and not _contains_list_syntax_control_characters(value)
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
        value = prefix[:literal_match.start()] + '"' + escaped_literal + '"'

    text = _decode_list_line(value)
    if text is None or not text.startswith("("):
        return None

    attributes_end = text.find(")", 1)
    if attributes_end < 0:
        return None
    attributes_text = text[1:attributes_end]
    attribute_tokens = tuple(attributes_text.split(" ")) if attributes_text else ()
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


def read_imap_list_inventory(mailbox: object) -> ImapListInventoryResult:
    """Run one bounded LIST and accept the inventory only if every row parses."""
    try:
        response = mailbox.list()
    except Exception:
        return ImapListInventoryResult(entries=None, error="list_unavailable")

    parts = _response_parts(response)
    if parts is None or not _is_ok_status(parts[0]):
        return ImapListInventoryResult(entries=None, error="list_unavailable")
    response_entries = parts[1]
    if (
        type(response_entries) not in (list, tuple)
        or len(response_entries) > _MAX_LIST_ENTRIES
    ):
        return ImapListInventoryResult(entries=None, error="list_unavailable")

    entries: list[ImapListEntry] = []
    entry_index = 0
    while entry_index < len(response_entries):
        raw_entry = response_entries[entry_index]
        entry = parse_imap_list_entry(raw_entry)
        if entry is None:
            return ImapListInventoryResult(entries=None, error="list_unavailable")
        entries.append(entry)
        entry_index += 1
        if (
            type(raw_entry) is tuple
            and entry_index < len(response_entries)
            and response_entries[entry_index] in (b"", "")
        ):
            entry_index += 1

    return ImapListInventoryResult(entries=tuple(entries), error=None)


def is_selectable_imap_list_entry(entry: ImapListEntry) -> bool:
    return not bool(
        entry.attributes & {_NOSELECT_ATTRIBUTE, _NONEXISTENT_ATTRIBUTE}
    )


def is_runtime_compatible_mailbox_name(value: object) -> bool:
    """Match the exact folder contract used by snapshot and mutation commands."""
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
