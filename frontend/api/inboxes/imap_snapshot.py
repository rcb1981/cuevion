from __future__ import annotations

import hashlib
import re
from email import policy
from email.message import Message
from email.parser import BytesParser
from typing import Any, Literal, TypedDict

from imap_connect_preview import (
    build_bounded_thread_identity,
    extract_message_thread_metadata,
    fetch_recent_messages,
    resolve_custom_imap_thread_ids,
    to_message_preview,
)

from .imap_uid_validity import (
    is_canonical_uid_validity,
    read_selected_mailbox_uid_validity,
)


_IMAP_UID_PATTERN = re.compile(r"[1-9][0-9]*", re.ASCII)
_RFC_DOT_ATOM_PATTERN = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*",
    re.ASCII,
)
_UID_FETCH_METADATA_PATTERN = re.compile(
    r"\A([1-9][0-9]*) \(UID ([1-9][0-9]*) "
    r"BODY\[\] \{(0|[1-9][0-9]*)\}(\))?\Z",
    re.ASCII,
)
_MAX_IMAP_UID = 4_294_967_295
_MAX_FOLDER_BYTES = 16_384
_MAX_CONTEXT_BYTES = 4_096
_MAX_FETCH_METADATA_BYTES = 4_096
_MAX_MESSAGE_BYTES = 25 * 1024 * 1024
_MAX_UID_SEARCH_BYTES = 1024 * 1024
_MAX_UID_SEARCH_RESULTS = 100_000
_MAX_SNAPSHOT_LIMIT = 100
_MAX_SEMANTIC_THREAD_SCAN = 25
_MAX_SEMANTIC_HEADER_BYTES = 64 * 1024
_SEMANTIC_HEADER_FETCH_PATTERN = re.compile(
    r"\A([1-9][0-9]*) \(UID ([1-9][0-9]*) BODY\[HEADER\] "
    r"\{(0|[1-9][0-9]*)\}(\))?\Z",
    re.ASCII,
)

_FINGERPRINT_POLICY = policy.default.clone(
    linesep="\r\n",
    max_line_length=0,
    refold_source="none",
)

_ERROR_MESSAGES = {
    "invalid_folder": "The IMAP mailbox folder is invalid.",
    "invalid_imap_uid": "The IMAP message UID is invalid.",
    "invalid_uid_validity": "The expected IMAP UIDVALIDITY is invalid.",
    "invalid_snapshot_context": "The IMAP snapshot context is invalid.",
    "invalid_snapshot_limit": "The IMAP snapshot limit is invalid.",
    "folder_unavailable": "The IMAP mailbox folder could not be opened.",
    "uid_validity_unavailable": "The IMAP mailbox UIDVALIDITY could not be verified.",
    "uid_validity_changed": "The IMAP mailbox changed since the message was fetched.",
    "message_not_found": "The IMAP message no longer exists.",
    "message_identity_unconfirmed": "The IMAP message identity could not be confirmed.",
    "provider_unavailable": "The IMAP provider is temporarily unavailable.",
    "imap_reply_source_unthreadable": "The IMAP message cannot be used as a reply source.",
    "snapshot_fetch_failed": "The IMAP mailbox snapshot could not be read.",
    "snapshot_fetch_incomplete": "The IMAP mailbox snapshot was incomplete.",
    "snapshot_uid_set_unavailable": "The IMAP mailbox UID set could not be verified.",
    "snapshot_serialization_failed": "The IMAP mailbox snapshot could not be prepared.",
    "semantic_thread_scan_unavailable": "The IMAP thread freshness could not be verified.",
}


class ImapSnapshotError(TypedDict):
    code: str
    message: str
    stage: str


class ImapInternalMessageIdentity(TypedDict):
    providerFolder: str
    imapUid: str
    uidValidity: str
    rfcMessageId: str | None
    fingerprint: str


class ImapMessageIdentityResult(TypedDict):
    ok: bool
    status: Literal["ok", "error"]
    identity: ImapInternalMessageIdentity | None
    error: ImapSnapshotError | None


class ImapReplySource(TypedDict):
    providerFolder: str
    imapUid: str
    uidValidity: str
    messageId: str
    references: list[str]
    inReplyTo: str | None


class ImapReplySourceResult(TypedDict):
    ok: bool
    status: Literal["ok", "error"]
    source: ImapReplySource | None
    error: ImapSnapshotError | None


class ImapFolderSnapshot(TypedDict):
    serverMailboxId: str
    providerFolder: str
    uidValidity: str
    imapUidSet: list[str]
    messages: list[dict[str, Any]]


class ImapFolderSnapshotResult(TypedDict):
    ok: bool
    status: Literal["ok", "error"]
    snapshot: ImapFolderSnapshot | None
    identities: dict[str, ImapInternalMessageIdentity]
    error: ImapSnapshotError | None


def _identity_failure(code: str, stage: str) -> ImapMessageIdentityResult:
    return {
        "ok": False,
        "status": "error",
        "identity": None,
        "error": {
            "code": code,
            "message": _ERROR_MESSAGES[code],
            "stage": stage,
        },
    }


def _reply_source_failure(code: str, stage: str) -> ImapReplySourceResult:
    return {
        "ok": False,
        "status": "error",
        "source": None,
        "error": {
            "code": code,
            "message": _ERROR_MESSAGES[code],
            "stage": stage,
        },
    }


def _snapshot_failure(code: str, stage: str) -> ImapFolderSnapshotResult:
    return {
        "ok": False,
        "status": "error",
        "snapshot": None,
        "identities": {},
        "error": {
            "code": code,
            "message": _ERROR_MESSAGES[code],
            "stage": stage,
        },
    }


def _contains_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _valid_bounded_text(value: object, *, maximum_bytes: int) -> bool:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or _contains_control_characters(value)
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum_bytes
    except UnicodeEncodeError:
        return False


def _valid_folder(value: object) -> bool:
    return _valid_bounded_text(value, maximum_bytes=_MAX_FOLDER_BYTES)


def _valid_imap_uid(value: object) -> bool:
    if type(value) is not str or _IMAP_UID_PATTERN.fullmatch(value) is None:
        return False
    maximum = str(_MAX_IMAP_UID)
    return len(value) < len(maximum) or (
        len(value) == len(maximum) and value <= maximum
    )


def _quote_mailbox_argument(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


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


def _decode_bounded_ascii(value: object, maximum_bytes: int) -> str | None:
    if type(value) is bytes:
        if len(value) > maximum_bytes:
            return None
        try:
            return value.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            return None
    if type(value) is str:
        try:
            encoded = value.encode("ascii", errors="strict")
        except UnicodeEncodeError:
            return None
        if len(encoded) > maximum_bytes:
            return None
        return value
    return None


def _parse_uid_search_response(response: object) -> list[str] | None:
    parts = _response_parts(response)
    if parts is None or not _is_ok_status(parts[0]):
        return None
    values = parts[1]
    if type(values) not in (list, tuple) or len(values) != 1:
        return None
    text = _decode_bounded_ascii(values[0], _MAX_UID_SEARCH_BYTES)
    if text is None or _contains_control_characters(text):
        return None
    if not text:
        return []
    if text != text.strip() or " ".join(text.split(" ")) != text:
        return None

    uids = text.split(" ")
    if (
        len(uids) > _MAX_UID_SEARCH_RESULTS
        or any(not _valid_imap_uid(uid) for uid in uids)
        or len(set(uids)) != len(uids)
    ):
        return None
    if any(int(left) >= int(right) for left, right in zip(uids, uids[1:])):
        return None
    return uids


def _read_selected_uid_set(mailbox: object) -> list[str] | None:
    try:
        response = mailbox.uid("SEARCH", None, "ALL")
    except Exception:
        return None
    return _parse_uid_search_response(response)


def _parse_uid_fetch_response(
    response: object,
    *,
    expected_uid: str,
) -> bytes | None:
    parts = _response_parts(response)
    if parts is None or not _is_ok_status(parts[0]):
        return None
    values = parts[1]
    if type(values) not in (list, tuple) or len(values) not in (1, 2):
        return None
    literal = values[0]
    if type(literal) is not tuple or len(literal) != 2:
        return None

    metadata = _decode_bounded_ascii(
        literal[0],
        _MAX_FETCH_METADATA_BYTES,
    )
    raw_message = literal[1]
    if metadata is None or type(raw_message) is not bytes:
        return None

    match = _UID_FETCH_METADATA_PATTERN.fullmatch(metadata)
    if match is None:
        return None
    sequence_number, fetched_uid, literal_size_text, inline_close = match.groups()
    if (
        not _valid_imap_uid(sequence_number)
        or fetched_uid != expected_uid
        or not _valid_imap_uid(fetched_uid)
    ):
        return None

    literal_size = int(literal_size_text)
    if literal_size > _MAX_MESSAGE_BYTES or len(raw_message) != literal_size:
        return None

    if len(values) == 1:
        if inline_close != ")":
            return None
    else:
        if inline_close is not None or values[1] not in (b")", ")"):
            return None
    return raw_message


def _is_absent_uid_fetch_response(response: object) -> bool:
    parts = _response_parts(response)
    return (
        parts is not None
        and _is_ok_status(parts[0])
        and type(parts[1]) in (list, tuple)
        and len(parts[1]) == 1
        and parts[1][0] is None
    )


def _message_fingerprint(message: Message) -> str | None:
    try:
        parsed_bytes = message.as_bytes(policy=_FINGERPRINT_POLICY)
    except Exception:
        return None
    if len(parsed_bytes) > _MAX_MESSAGE_BYTES:
        return None
    return hashlib.sha256(parsed_bytes).hexdigest()


def _build_internal_identity(
    message: Message,
    *,
    folder: str,
    uid: str,
    uid_validity: str,
    fallback_id: str,
) -> ImapInternalMessageIdentity | None:
    fingerprint = _message_fingerprint(message)
    if fingerprint is None:
        return None
    try:
        metadata = extract_message_thread_metadata(
            message,
            uid,
            fallback_id,
        )
    except Exception:
        return None
    if type(metadata) is not dict:
        return None

    rfc_message_id = metadata.get("message_id")
    if rfc_message_id is not None and type(rfc_message_id) is not str:
        return None
    return {
        "providerFolder": folder,
        "imapUid": uid,
        "uidValidity": uid_validity,
        "rfcMessageId": rfc_message_id,
        "fingerprint": fingerprint,
    }


def read_imap_message_identity(
    mailbox: object,
    *,
    folder: str,
    uid: str,
    expected_uid_validity: str,
) -> ImapMessageIdentityResult:
    """Read one exact UID without changing flags or recreating mutation checks."""
    if not _valid_folder(folder):
        return _identity_failure("invalid_folder", "input_validation")
    if not _valid_imap_uid(uid):
        return _identity_failure("invalid_imap_uid", "input_validation")
    if not is_canonical_uid_validity(expected_uid_validity):
        return _identity_failure("invalid_uid_validity", "input_validation")

    try:
        select_response = mailbox.select(_quote_mailbox_argument(folder))
    except Exception:
        return _identity_failure("folder_unavailable", "folder_selection")
    select_parts = _response_parts(select_response)
    if select_parts is None or not _is_ok_status(select_parts[0]):
        return _identity_failure("folder_unavailable", "folder_selection")

    uid_validity = read_selected_mailbox_uid_validity(mailbox)
    if uid_validity is None:
        return _identity_failure(
            "uid_validity_unavailable",
            "uid_validity",
        )
    if uid_validity != expected_uid_validity:
        return _identity_failure("uid_validity_changed", "uid_validity")

    try:
        fetch_response = mailbox.uid(
            "FETCH",
            uid,
            "(UID BODY.PEEK[])",
        )
    except Exception:
        return _identity_failure(
            "message_identity_unconfirmed",
            "message_fetch",
        )
    if _is_absent_uid_fetch_response(fetch_response):
        return _identity_failure("message_not_found", "message_fetch")
    raw_message = _parse_uid_fetch_response(
        fetch_response,
        expected_uid=uid,
    )
    if raw_message is None:
        return _identity_failure(
            "message_identity_unconfirmed",
            "message_fetch",
        )

    try:
        message = BytesParser().parsebytes(raw_message)
    except Exception:
        return _identity_failure(
            "message_identity_unconfirmed",
            "message_parsing",
        )
    identity = _build_internal_identity(
        message,
        folder=folder,
        uid=uid,
        uid_validity=uid_validity,
        fallback_id=f"imap-uid-{uid}",
    )
    if identity is None:
        return _identity_failure(
            "message_identity_unconfirmed",
            "message_identity",
        )
    return {
        "ok": True,
        "status": "ok",
        "identity": identity,
        "error": None,
    }


def _angle_bracket_message_id(value: object) -> str | None:
    if (
        type(value) is not str
        or not _valid_bounded_text(value, maximum_bytes=_MAX_CONTEXT_BYTES)
    ):
        return None
    return f"<{value}>"


def _consume_safe_header_whitespace(
    value: str,
    start: int,
) -> tuple[int, bool] | None:
    index = start
    while index < len(value):
        if value[index] in " \t":
            index += 1
            continue
        if value.startswith("\r\n", index):
            fold_end = index + 2
        elif value[index] == "\n":
            fold_end = index + 1
        else:
            break
        if fold_end >= len(value) or value[fold_end] not in " \t":
            return None
        index = fold_end
    return index, index != start


def _consume_safe_header_comment(value: str, start: int) -> int | None:
    if start >= len(value) or value[start] != "(":
        return None

    index = start + 1
    depth = 1
    while index < len(value):
        character = value[index]
        if character == "\\":
            index += 1
            if index >= len(value):
                return None
            escaped = value[index]
            if escaped in "\r\n" or ord(escaped) < 32 or ord(escaped) == 127:
                return None
            index += 1
            continue
        if character == "(":
            depth += 1
            index += 1
            continue
        if character == ")":
            depth -= 1
            index += 1
            if depth == 0:
                return index
            continue
        if value.startswith("\r\n", index):
            fold_end = index + 2
            if fold_end >= len(value) or value[fold_end] not in " \t":
                return None
            index = fold_end
            continue
        if character == "\n" or (
            ord(character) < 32 and character != "\t"
        ) or ord(character) == 127:
            return None
        index += 1
    return None


def _consume_safe_header_cfws(
    value: str,
    start: int,
) -> tuple[int, bool] | None:
    index = start
    while index < len(value):
        whitespace = _consume_safe_header_whitespace(value, index)
        if whitespace is None:
            return None
        index, _ = whitespace
        if index >= len(value) or value[index] != "(":
            break
        comment_end = _consume_safe_header_comment(value, index)
        if comment_end is None:
            return None
        index = comment_end
    return index, index != start


def _find_raw_angle_token_end(value: str, start: int) -> int | None:
    if start >= len(value) or value[start] != "<":
        return None

    index = start + 1
    in_quoted_string = False
    in_domain_literal = False
    while index < len(value):
        character = value[index]
        if value.startswith("\r\n", index):
            fold_end = index + 2
            if fold_end >= len(value) or value[fold_end] not in " \t":
                return None
            index = fold_end
            continue
        if character == "\n" or (
            ord(character) < 32 and character != "\t"
        ) or ord(character) == 127:
            return None

        if in_quoted_string:
            if character == "\\":
                index += 1
                if index >= len(value):
                    return None
                escaped = value[index]
                if (
                    escaped in "\r\n"
                    or ord(escaped) < 32
                    or ord(escaped) == 127
                ):
                    return None
            elif character == '"':
                in_quoted_string = False
            index += 1
            continue

        if in_domain_literal:
            if character == "\\":
                index += 1
                if index >= len(value):
                    return None
                escaped = value[index]
                if (
                    escaped in "\r\n"
                    or ord(escaped) < 32
                    or ord(escaped) == 127
                ):
                    return None
            elif character == "]":
                in_domain_literal = False
            index += 1
            continue

        if character == '"':
            in_quoted_string = True
        elif character == "[":
            in_domain_literal = True
        elif character == ">":
            return index + 1
        elif character in "<()":
            return None
        index += 1
    return None


def _parse_raw_angle_bracket_tokens(value: object) -> list[str] | None:
    if type(value) is not str:
        return None
    consumed = _consume_safe_header_cfws(value, 0)
    if consumed is None:
        return None
    index, _ = consumed
    tokens: list[str] = []
    while index < len(value):
        token_end = _find_raw_angle_token_end(value, index)
        if token_end is None:
            return None
        tokens.append(value[index:token_end])
        consumed = _consume_safe_header_cfws(value, token_end)
        if consumed is None:
            return None
        index, had_separator = consumed
        if index < len(value) and not had_separator:
            return None
    return tokens or None


def _read_raw_angle_header(
    message: Message,
    name: str,
) -> tuple[int, list[str]] | None:
    try:
        values = [
            value
            for header_name, value in message.raw_items()
            if type(header_name) is str and header_name.casefold() == name
        ]
    except Exception:
        return None

    tokens: list[str] = []
    for value in values:
        parsed = _parse_raw_angle_bracket_tokens(value)
        if parsed is None:
            return None
        tokens.extend(parsed)
    return len(values), tokens


def _read_compat_quoted_local(value: str) -> tuple[str, int] | None:
    if not value.startswith('"'):
        return None
    index = 1
    while index < len(value):
        character = value[index]
        if character == '"':
            return value[: index + 1], index + 1
        if character == "\\":
            index += 1
            if index >= len(value):
                return None
            character = value[index]
        codepoint = ord(character)
        if codepoint < 32 or codepoint > 126:
            return None
        index += 1
    return None


def _normalize_compat_message_id_token(token: str) -> str | None:
    if (
        not token.startswith("<")
        or not token.endswith(">")
        or len(token) > _MAX_CONTEXT_BYTES + 2
    ):
        return None
    value = token[1:-1]
    if not value or any(
        ord(character) < 32
        or ord(character) == 127
        or ord(character) > 126
        for character in value
    ):
        return None

    if value.startswith('"'):
        quoted_local = _read_compat_quoted_local(value)
        if quoted_local is None:
            return None
        local_part, separator_index = quoted_local
        if separator_index >= len(value) or value[separator_index] != "@":
            return None
        domain = value[separator_index + 1 :]
    else:
        separator_index = value.find("@")
        if separator_index < 1:
            return None
        local_part = value[:separator_index]
        domain = value[separator_index + 1 :]
        if _RFC_DOT_ATOM_PATTERN.fullmatch(local_part) is None:
            return None

    if domain.startswith("["):
        if not domain.endswith("]"):
            return None
        literal = domain[1:-1]
        if any(
            ord(character) < 33
            or ord(character) > 126
            or character in "[\\]"
            for character in literal
        ):
            return None
        normalized_domain = domain
    else:
        if _RFC_DOT_ATOM_PATTERN.fullmatch(domain) is None:
            return None
        normalized_domain = domain.lower()
    return f"{local_part}@{normalized_domain}"


def _normalize_isolated_ancestry_token(
    token: str,
    *,
    header_name: str,
    metadata_key: str,
    uid: str,
) -> str | None:
    message = Message()
    message[header_name] = token
    try:
        metadata = extract_message_thread_metadata(
            message,
            uid,
            f"imap-uid-{uid}",
        )
    except Exception:
        metadata = None

    normalized: object = None
    if type(metadata) is dict:
        candidate = metadata.get(metadata_key)
        if metadata_key == "references":
            if (
                type(candidate) is list
                and len(candidate) == 1
                and type(candidate[0]) is str
            ):
                normalized = candidate[0]
        elif type(candidate) is str:
            normalized = candidate
    strict_normalized = _normalize_compat_message_id_token(token)
    if strict_normalized is None:
        return None
    if normalized is None:
        normalized = strict_normalized
    return _angle_bracket_message_id(normalized)


def read_imap_reply_source(
    mailbox: object,
    *,
    folder: str,
    uid: str,
    expected_uid_validity: str,
) -> ImapReplySourceResult:
    """Read trusted reply headers for one exact UID without mutating mailbox state.

    Returned message-id tokens use a canonical angle-bracket representation.
    """
    if not _valid_folder(folder):
        return _reply_source_failure("invalid_folder", "input_validation")
    if not _valid_imap_uid(uid):
        return _reply_source_failure("invalid_imap_uid", "input_validation")
    if not is_canonical_uid_validity(expected_uid_validity):
        return _reply_source_failure(
            "invalid_uid_validity",
            "input_validation",
        )

    try:
        select_response = mailbox.select(
            _quote_mailbox_argument(folder),
            readonly=True,
        )
    except Exception:
        return _reply_source_failure("provider_unavailable", "folder_selection")
    select_parts = _response_parts(select_response)
    if select_parts is None or not _is_ok_status(select_parts[0]):
        return _reply_source_failure("folder_unavailable", "folder_selection")

    uid_validity = read_selected_mailbox_uid_validity(mailbox)
    if not is_canonical_uid_validity(uid_validity):
        return _reply_source_failure(
            "uid_validity_unavailable",
            "uid_validity",
        )
    if uid_validity != expected_uid_validity:
        return _reply_source_failure("uid_validity_changed", "uid_validity")

    try:
        fetch_response = mailbox.uid(
            "FETCH",
            uid,
            "(UID BODY.PEEK[])",
        )
    except Exception:
        return _reply_source_failure(
            "message_identity_unconfirmed",
            "message_fetch",
        )
    if _is_absent_uid_fetch_response(fetch_response):
        return _reply_source_failure("message_not_found", "message_fetch")
    raw_message = _parse_uid_fetch_response(
        fetch_response,
        expected_uid=uid,
    )
    if raw_message is None:
        return _reply_source_failure(
            "message_identity_unconfirmed",
            "message_fetch",
        )

    try:
        message = BytesParser().parsebytes(raw_message)
    except Exception:
        return _reply_source_failure(
            "message_identity_unconfirmed",
            "message_parsing",
        )

    raw_message_id = _read_raw_angle_header(message, "message-id")
    if (
        raw_message_id is None
        or raw_message_id[0] != 1
        or len(raw_message_id[1]) != 1
    ):
        return _reply_source_failure(
            "imap_reply_source_unthreadable",
            "message_threading",
        )

    raw_reference_header = _read_raw_angle_header(message, "references")
    raw_in_reply_to_header = _read_raw_angle_header(message, "in-reply-to")
    threading_message = Message()
    threading_message["Message-ID"] = raw_message_id[1][0]
    if raw_reference_header is not None and raw_reference_header[0] > 0:
        threading_message["References"] = " ".join(raw_reference_header[1])
    if (
        raw_in_reply_to_header is not None
        and raw_in_reply_to_header[0] == 1
        and len(raw_in_reply_to_header[1]) == 1
    ):
        threading_message["In-Reply-To"] = raw_in_reply_to_header[1][0]

    try:
        metadata = extract_message_thread_metadata(
            threading_message,
            uid,
            f"imap-uid-{uid}",
        )
    except Exception:
        return _reply_source_failure(
            "message_identity_unconfirmed",
            "message_threading",
        )
    if type(metadata) is not dict:
        return _reply_source_failure(
            "message_identity_unconfirmed",
            "message_identity",
        )

    strict_message_id = _normalize_compat_message_id_token(
        raw_message_id[1][0]
    )
    if strict_message_id is None:
        return _reply_source_failure(
            "imap_reply_source_unthreadable",
            "message_threading",
        )
    normalized_message_id = metadata.get("message_id")
    used_compat_normalizer = normalized_message_id is None
    if used_compat_normalizer:
        normalized_message_id = strict_message_id
    message_id = _angle_bracket_message_id(normalized_message_id)
    if (
        message_id is None
        or (
            not used_compat_normalizer
            and metadata.get("message_id_ambiguous") is not False
        )
    ):
        return _reply_source_failure(
            "imap_reply_source_unthreadable",
            "message_threading",
        )

    references: list[str] = []
    if raw_reference_header is not None and raw_reference_header[0] > 0:
        normalized_references: list[str] = []
        for token in raw_reference_header[1]:
            reference = _normalize_isolated_ancestry_token(
                token,
                header_name="References",
                metadata_key="references",
                uid=uid,
            )
            if reference is None:
                normalized_references = []
                break
            normalized_references.append(reference)
        references = normalized_references
    in_reply_to = None
    if (
        raw_reference_header is not None
        and (raw_reference_header[0] == 0 or bool(references))
        and raw_in_reply_to_header is not None
        and raw_in_reply_to_header[0] == 1
        and len(raw_in_reply_to_header[1]) == 1
    ):
        in_reply_to = _normalize_isolated_ancestry_token(
            raw_in_reply_to_header[1][0],
            header_name="In-Reply-To",
            metadata_key="in_reply_to",
            uid=uid,
        )

    return {
        "ok": True,
        "status": "ok",
        "source": {
            "providerFolder": folder,
            "imapUid": uid,
            "uidValidity": uid_validity,
            "messageId": message_id,
            "references": references,
            "inReplyTo": in_reply_to,
        },
        "error": None,
    }


def _parse_semantic_header_fetch_response(
    response: object,
    *,
    expected_uid: str,
) -> bytes | None:
    parts = _response_parts(response)
    if parts is None or not _is_ok_status(parts[0]):
        return None
    values = parts[1]
    if type(values) not in (list, tuple) or len(values) not in (1, 2):
        return None
    literal = values[0]
    if type(literal) is not tuple or len(literal) != 2:
        return None
    metadata = _decode_bounded_ascii(literal[0], _MAX_FETCH_METADATA_BYTES)
    raw_headers = literal[1]
    if (
        metadata is None
        or type(raw_headers) is not bytes
        or len(raw_headers) > _MAX_SEMANTIC_HEADER_BYTES
    ):
        return None
    match = _SEMANTIC_HEADER_FETCH_PATTERN.fullmatch(metadata)
    if match is None:
        return None
    sequence_number, fetched_uid, literal_size_text, inline_close = match.groups()
    if (
        not _valid_imap_uid(sequence_number)
        or fetched_uid != expected_uid
        or not _valid_imap_uid(fetched_uid)
        or int(literal_size_text) != len(raw_headers)
    ):
        return None
    if len(values) == 1:
        if inline_close != ")":
            return None
    elif inline_close is not None or values[1] not in (b")", ")"):
        return None
    return raw_headers


def _record_touches_expected_rfc_thread(
    record: dict[str, Any],
    *,
    mailbox_key: str,
    expected_thread_id: str,
) -> bool:
    references = record.get("references")
    candidates = list(references) if type(references) is list else []
    candidates.extend((record.get("in_reply_to"), record.get("message_id")))
    return any(
        type(candidate) is str
        and bool(candidate)
        and build_bounded_thread_identity(
            "imap:rfc",
            mailbox_key,
            candidate,
        )
        == expected_thread_id
        for candidate in candidates
    )


def read_imap_latest_thread_identity(
    mailbox: object,
    *,
    mailbox_key: str,
    folder: str,
    expected_uid_validity: str,
    target_uid: str,
    expected_thread_id: str,
    require_predecessor: bool = False,
) -> dict[str, object]:
    """Prove the target is the newest same-RFC-root UID in one exact folder.

    The folder + UIDVALIDITY pair is the complete provider-authority stream.
    At most 25 headers are read. When ``require_predecessor`` is true, the
    bounded window must include an earlier UID resolving to the same RFC root;
    singleton/root-only messages therefore fail closed.
    """
    if (
        type(mailbox_key) is not str
        or not mailbox_key
        or not _valid_folder(folder)
        or not is_canonical_uid_validity(expected_uid_validity)
        or not _valid_imap_uid(target_uid)
        or type(expected_thread_id) is not str
        or not expected_thread_id.startswith("imap:rfc:")
        or type(require_predecessor) is not bool
    ):
        return _snapshot_failure("semantic_thread_scan_unavailable", "input_validation")
    try:
        select_response = mailbox.select(_quote_mailbox_argument(folder), readonly=True)
    except Exception:
        return _snapshot_failure("semantic_thread_scan_unavailable", "folder_selection")
    select_parts = _response_parts(select_response)
    if select_parts is None or not _is_ok_status(select_parts[0]):
        return _snapshot_failure("semantic_thread_scan_unavailable", "folder_selection")
    uid_validity = read_selected_mailbox_uid_validity(mailbox)
    if uid_validity != expected_uid_validity:
        return _snapshot_failure("semantic_thread_scan_unavailable", "uid_validity")
    uid_set = _read_selected_uid_set(mailbox)
    if uid_set is None or target_uid not in uid_set:
        return _snapshot_failure("semantic_thread_scan_unavailable", "uid_search")
    target_index = uid_set.index(target_uid)
    later_count = len(uid_set) - target_index - 1
    target_and_later_count = later_count + 1
    if target_and_later_count > _MAX_SEMANTIC_THREAD_SCAN:
        return _snapshot_failure("semantic_thread_scan_unavailable", "scan_bound")
    scan_start = target_index
    if require_predecessor:
        prior_budget = _MAX_SEMANTIC_THREAD_SCAN - target_and_later_count
        if target_index < 1 or prior_budget < 1:
            return _snapshot_failure("semantic_thread_scan_unavailable", "predecessor")
        scan_start = target_index - min(target_index, prior_budget)
    scan_uids = uid_set[scan_start:]
    target_offset = target_index - scan_start

    records: list[dict[str, Any]] = []
    for uid in scan_uids:
        try:
            response = mailbox.uid("FETCH", uid, "(UID BODY.PEEK[HEADER])")
        except Exception:
            return _snapshot_failure("semantic_thread_scan_unavailable", "header_fetch")
        raw_headers = _parse_semantic_header_fetch_response(
            response,
            expected_uid=uid,
        )
        if raw_headers is None:
            return _snapshot_failure("semantic_thread_scan_unavailable", "header_fetch")
        try:
            message = BytesParser().parsebytes(raw_headers)
            metadata = extract_message_thread_metadata(
                message,
                uid,
                f"imap-uid-{uid}",
            )
        except Exception:
            return _snapshot_failure("semantic_thread_scan_unavailable", "header_parsing")
        if type(metadata) is not dict:
            return _snapshot_failure("semantic_thread_scan_unavailable", "header_parsing")
        records.append(metadata)

    try:
        thread_ids = resolve_custom_imap_thread_ids(
            records,
            mailbox_key=mailbox_key,
            folder=folder,
            uid_validity=expected_uid_validity,
        )
    except Exception:
        return _snapshot_failure("semantic_thread_scan_unavailable", "thread_resolution")
    if (
        type(thread_ids) is not list
        or len(thread_ids) != len(scan_uids)
        or thread_ids[target_offset] != expected_thread_id
    ):
        return _snapshot_failure("semantic_thread_scan_unavailable", "thread_resolution")
    for index in range(target_offset + 1, len(records)):
        record = records[index]
        if (
            record.get("message_id_ambiguous") is True
            or (
                _record_touches_expected_rfc_thread(
                    record,
                    mailbox_key=mailbox_key,
                    expected_thread_id=expected_thread_id,
                )
                and (
                    record.get("message_id_ambiguous") is not False
                    or type(record.get("message_id")) is not str
                    or thread_ids[index] != expected_thread_id
                )
            )
        ):
            # A later message that directly names this RFC root but lacks one
            # unambiguous identity cannot be treated as unrelated UID fallback.
            return _snapshot_failure(
                "semantic_thread_scan_unavailable",
                "thread_resolution",
            )
    matching_indexes = [
        index for index, thread_id in enumerate(thread_ids) if thread_id == expected_thread_id
    ]
    if not matching_indexes:
        return _snapshot_failure("semantic_thread_scan_unavailable", "thread_resolution")
    if require_predecessor and not any(
        index < target_offset for index in matching_indexes
    ):
        return _snapshot_failure("semantic_thread_scan_unavailable", "predecessor")
    latest_index = matching_indexes[-1]
    latest_record = records[latest_index]
    latest_message_id = latest_record.get("message_id")
    if type(latest_message_id) is not str or not latest_message_id:
        return _snapshot_failure("semantic_thread_scan_unavailable", "thread_resolution")
    return {
        "ok": True,
        "status": "ok",
        "latest": {
            "providerFolder": folder,
            "uidValidity": expected_uid_validity,
            "imapUid": scan_uids[latest_index],
            "threadId": expected_thread_id,
            "rfcMessageId": latest_message_id,
        },
        "error": None,
    }


def read_imap_folder_snapshot(
    mailbox: object,
    *,
    folder: str,
    mailbox_key: str,
    email_address: str,
    limit: int = _MAX_SNAPSHOT_LIMIT,
    readonly: bool = False,
) -> ImapFolderSnapshotResult:
    """Read a complete, UID-scoped custom-IMAP preview snapshot."""
    if not _valid_folder(folder):
        return _snapshot_failure("invalid_folder", "input_validation")
    if not (
        _valid_bounded_text(mailbox_key, maximum_bytes=_MAX_CONTEXT_BYTES)
        and _valid_bounded_text(email_address, maximum_bytes=_MAX_CONTEXT_BYTES)
    ):
        return _snapshot_failure(
            "invalid_snapshot_context",
            "input_validation",
        )
    if type(limit) is not int or limit < 1 or limit > _MAX_SNAPSHOT_LIMIT:
        return _snapshot_failure(
            "invalid_snapshot_limit",
            "input_validation",
        )

    provider_folder_argument = _quote_mailbox_argument(folder)
    try:
        select_response = (
            mailbox.select(provider_folder_argument, readonly=True)
            if readonly
            else mailbox.select(provider_folder_argument)
        )
    except Exception:
        return _snapshot_failure("folder_unavailable", "folder_selection")
    select_parts = _response_parts(select_response)
    if select_parts is None or not _is_ok_status(select_parts[0]):
        return _snapshot_failure("folder_unavailable", "folder_selection")

    initial_uid_validity = read_selected_mailbox_uid_validity(mailbox)
    if not is_canonical_uid_validity(initial_uid_validity):
        return _snapshot_failure(
            "uid_validity_unavailable",
            "uid_validity",
        )

    try:
        fetch_result = (
            fetch_recent_messages(
                mailbox,
                folder=provider_folder_argument,
                limit=limit,
                readonly=True,
            )
            if readonly
            else fetch_recent_messages(
                mailbox,
                folder=provider_folder_argument,
                limit=limit,
            )
        )
    except Exception:
        return _snapshot_failure("snapshot_fetch_failed", "message_fetch")
    if type(fetch_result) is not dict or any(
        key not in fetch_result for key in ("messages", "warnings", "error")
    ):
        return _snapshot_failure("snapshot_fetch_failed", "message_fetch")

    messages = fetch_result.get("messages")
    warnings = fetch_result.get("warnings")
    fetch_error = fetch_result.get("error")
    if fetch_error is not None:
        return _snapshot_failure("snapshot_fetch_failed", "message_fetch")
    if type(warnings) is not list or warnings:
        return _snapshot_failure(
            "snapshot_fetch_incomplete",
            "message_fetch",
        )
    if type(messages) is not list:
        return _snapshot_failure("snapshot_fetch_failed", "message_fetch")

    normalized_messages: list[
        tuple[Message, bool, str, bool]
    ] = []
    fetched_uids: list[str] = []
    for item in messages:
        if type(item) not in (list, tuple) or len(item) != 4:
            return _snapshot_failure(
                "snapshot_fetch_incomplete",
                "message_shape",
            )
        message, unread, imap_uid, flagged = item
        if (
            not isinstance(message, Message)
            or type(unread) is not bool
            or type(flagged) is not bool
            or not _valid_imap_uid(imap_uid)
        ):
            return _snapshot_failure(
                "snapshot_fetch_incomplete",
                "message_shape",
            )
        normalized_messages.append((message, unread, imap_uid, flagged))
        fetched_uids.append(imap_uid)

    imap_uid_set = _read_selected_uid_set(mailbox)
    if imap_uid_set is None:
        return _snapshot_failure(
            "snapshot_uid_set_unavailable",
            "uid_set",
        )

    final_uid_validity = read_selected_mailbox_uid_validity(mailbox)
    if not is_canonical_uid_validity(final_uid_validity):
        return _snapshot_failure(
            "uid_validity_unavailable",
            "uid_validity",
        )
    if initial_uid_validity != final_uid_validity:
        return _snapshot_failure(
            "uid_validity_changed",
            "uid_validity",
        )
    confirmed_uid_validity = final_uid_validity

    expected_uids = list(reversed(imap_uid_set[-limit:]))
    if len(messages) != len(expected_uids):
        return _snapshot_failure(
            "snapshot_fetch_incomplete",
            "message_count",
        )

    if fetched_uids != expected_uids:
        return _snapshot_failure(
            "snapshot_fetch_incomplete",
            "message_uid_scope",
        )

    previews: list[dict[str, Any]] = []
    identities: dict[str, ImapInternalMessageIdentity] = {}
    threading_records: list[dict[str, Any]] = []
    try:
        for index, (message, unread, imap_uid, flagged) in enumerate(
            normalized_messages
        ):
            preview = to_message_preview(
                message,
                index,
                email_address,
                unread,
                imap_uid,
                flagged,
            )
            if type(preview) is not dict:
                raise ValueError("invalid preview")
            preview.pop("fingerprint", None)
            preview.pop("rfcMessageId", None)
            preview["imapUid"] = imap_uid
            preview["providerFolder"] = folder
            preview["uidValidity"] = confirmed_uid_validity
            preview["serverMailboxId"] = mailbox_key

            record = extract_message_thread_metadata(
                message,
                imap_uid,
                str(preview.get("id") or ""),
            )
            if type(record) is not dict:
                raise ValueError("invalid thread metadata")
            identity = _build_internal_identity(
                message,
                folder=folder,
                uid=imap_uid,
                uid_validity=confirmed_uid_validity,
                fallback_id=str(preview.get("id") or ""),
            )
            if identity is None:
                raise ValueError("invalid message identity")
            if identity["rfcMessageId"] is not None:
                preview["rfcMessageId"] = identity["rfcMessageId"]

            previews.append(preview)
            threading_records.append(record)
            identities[imap_uid] = identity

        thread_ids = resolve_custom_imap_thread_ids(
            threading_records,
            mailbox_key=mailbox_key,
            folder=folder,
            uid_validity=confirmed_uid_validity,
        )
        if (
            type(thread_ids) is not list
            or len(thread_ids) != len(previews)
            or any(type(thread_id) is not str or not thread_id for thread_id in thread_ids)
        ):
            raise ValueError("invalid thread identity")
        for preview, thread_id in zip(previews, thread_ids):
            preview["threadId"] = thread_id
    except Exception:
        return _snapshot_failure(
            "snapshot_serialization_failed",
            "snapshot_serialization",
        )

    return {
        "ok": True,
        "status": "ok",
        "snapshot": {
            "serverMailboxId": mailbox_key,
            "providerFolder": folder,
            "uidValidity": confirmed_uid_validity,
            "imapUidSet": imap_uid_set,
            "messages": previews,
        },
        "identities": identities,
        "error": None,
    }
