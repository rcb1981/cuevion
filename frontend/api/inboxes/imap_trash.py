from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TypedDict

from .imap_folder_inventory import (
    ImapListEntry,
    ImapListInventoryResult,
    is_runtime_compatible_mailbox_name,
    is_selectable_imap_list_entry,
    read_imap_list_inventory,
)
from .imap_snapshot import read_imap_message_identity
from .imap_uid_validity import (
    is_canonical_uid_validity,
    read_selected_mailbox_uid_validity,
)


_IMAP_UID_PATTERN = re.compile(r"[1-9][0-9]*", re.ASCII)
_TAGGED_COPYUID_PATTERN = re.compile(
    r"\A\[COPYUID ([^\] ]+) ([^\] ]+) ([^\] ]+)\](?= |\Z)",
    re.ASCII | re.IGNORECASE,
)
_MAX_IMAP_UID = 4_294_967_295
_MAX_CAPABILITY_BYTES = 16_384
_MAX_COPYUID_BYTES = 4_096
_MAX_COPYUID_SCALARS = 64
_MAX_COPYUID_DRAIN_RESPONSES = 32

_TRASH_ATTRIBUTE = r"\trash"
_CONFLICTING_SPECIAL_USE_ATTRIBUTES = frozenset(
    {
        r"\all",
        r"\archive",
        r"\drafts",
        r"\flagged",
        r"\important",
        r"\inbox",
        r"\junk",
        r"\sent",
    }
)

_ERROR_MESSAGES = {
    "invalid_source_folder": "The source mailbox folder is invalid.",
    "invalid_imap_uid": "The IMAP message UID is invalid.",
    "invalid_uid_validity": "The expected IMAP UIDVALIDITY is invalid.",
    "trash_folder_unavailable": "No selectable Trash mailbox is available.",
    "trash_folder_ambiguous": "More than one selectable Trash mailbox is available.",
    "trash_move_unsupported": "This IMAP server does not support verifiable Trash moves.",
    "trash_uidplus_unsupported": (
        "This IMAP server does not expose verifiable moved-message identifiers."
    ),
    "source_folder_unavailable": "The source mailbox folder could not be opened.",
    "uid_validity_unavailable": "The source mailbox UIDVALIDITY could not be verified.",
    "uid_validity_changed": "The source mailbox changed since the message was fetched.",
    "trash_message_not_found": "The source message no longer exists.",
    "source_identity_unconfirmed": "The source message identity could not be confirmed.",
    "trash_move_failed": "The IMAP server rejected the Trash move.",
    "trash_move_unconfirmed": "The Trash move could not be confirmed safely.",
    "target_folder_unavailable": "The Trash mailbox could not be opened for verification.",
    "target_uid_validity_unavailable": "The Trash mailbox UIDVALIDITY could not be verified.",
    "target_uid_validity_changed": "The Trash mailbox changed during verification.",
    "target_message_not_found": "The moved message was not found in Trash.",
    "target_identity_unconfirmed": "The moved message identity could not be confirmed in Trash.",
    "trash_target_mismatch": "The Trash target did not match the source message.",
    "imap_trash_failed": "The message could not be moved to Trash through IMAP.",
}


class ImapTrashError(TypedDict):
    code: str
    message: str
    stage: str


class ImapTrashResult(TypedDict):
    ok: bool
    status: Literal["ok", "error"]
    source_folder: str | None
    source_uid: str | None
    source_uid_validity: str | None
    trash_folder: str | None
    target_uid: str | None
    target_uid_validity: str | None
    confirmation: Literal["exact_target_verified"] | None
    error: ImapTrashError | None


@dataclass(frozen=True)
class TrashRoleAnalysis:
    category: Literal["A", "B", "C", "D", "E"]
    raw_marker_count: int | None
    special_use_folder: str | None


@dataclass(frozen=True)
class TrashFolderResolution:
    folder: str | None
    error: Literal[
        "trash_folder_unavailable",
        "trash_folder_ambiguous",
    ] | None
    analysis: TrashRoleAnalysis
    source: Literal["special_use", "configured"] | None


CopyUidMapping = tuple[str, str, str]


def _failure(code: str, stage: str) -> ImapTrashResult:
    return {
        "ok": False,
        "status": "error",
        "source_folder": None,
        "source_uid": None,
        "source_uid_validity": None,
        "trash_folder": None,
        "target_uid": None,
        "target_uid_validity": None,
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
    source_uid: str,
    source_uid_validity: str,
    trash_folder: str,
    target_uid: str,
    target_uid_validity: str,
) -> ImapTrashResult:
    return {
        "ok": True,
        "status": "ok",
        "source_folder": source_folder,
        "source_uid": source_uid,
        "source_uid_validity": source_uid_validity,
        "trash_folder": trash_folder,
        "target_uid": target_uid,
        "target_uid_validity": target_uid_validity,
        "confirmation": "exact_target_verified",
        "error": None,
    }


def _contains_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _valid_source_folder(value: object) -> bool:
    return is_runtime_compatible_mailbox_name(value)


def _valid_imap_uid(value: object) -> bool:
    if type(value) is not str or _IMAP_UID_PATTERN.fullmatch(value) is None:
        return False
    maximum = str(_MAX_IMAP_UID)
    return len(value) < len(maximum) or (
        len(value) == len(maximum) and value <= maximum
    )


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
        return value if len(encoded) <= maximum_bytes else None
    return None


def _is_ok_status(value: object) -> bool:
    text = _decode_bounded_ascii(value, 16)
    return text is not None and text.casefold() == "ok"


def _response_parts(response: object) -> tuple[object, object] | None:
    if type(response) not in (list, tuple) or len(response) != 2:
        return None
    return response[0], response[1]


def _is_safe_trash_target_entry(entry: ImapListEntry) -> bool:
    return (
        is_selectable_imap_list_entry(entry)
        and is_runtime_compatible_mailbox_name(entry.mailbox)
        and entry.mailbox.casefold() != "inbox"
        and not bool(entry.attributes & _CONFLICTING_SPECIAL_USE_ATTRIBUTES)
    )


def analyze_trash_role(
    inventory: ImapListInventoryResult,
) -> TrashRoleAnalysis:
    """Classify raw Trash-role evidence before applying any safety filter."""
    if inventory.error is not None or inventory.entries is None:
        return TrashRoleAnalysis(
            category="A",
            raw_marker_count=None,
            special_use_folder=None,
        )

    raw_markers = tuple(
        entry
        for entry in inventory.entries
        if _TRASH_ATTRIBUTE in entry.attributes
    )
    if not raw_markers:
        return TrashRoleAnalysis(
            category="B",
            raw_marker_count=0,
            special_use_folder=None,
        )
    if any(not _is_safe_trash_target_entry(entry) for entry in raw_markers):
        return TrashRoleAnalysis(
            category="E",
            raw_marker_count=len(raw_markers),
            special_use_folder=None,
        )
    if len(raw_markers) != 1:
        return TrashRoleAnalysis(
            category="D",
            raw_marker_count=len(raw_markers),
            special_use_folder=None,
        )

    candidate = raw_markers[0]
    return TrashRoleAnalysis(
        category="C",
        raw_marker_count=1,
        special_use_folder=candidate.mailbox,
    )


def configurable_trash_folder_entries(
    inventory: ImapListInventoryResult,
) -> tuple[ImapListEntry, ...]:
    """Return exact, unique mapping candidates only for valid category B."""
    analysis = analyze_trash_role(inventory)
    if analysis.category != "B" or inventory.entries is None:
        return ()

    mailbox_counts: dict[str, int] = {}
    for entry in inventory.entries:
        mailbox_counts[entry.mailbox] = mailbox_counts.get(entry.mailbox, 0) + 1
    return tuple(
        entry
        for entry in inventory.entries
        if mailbox_counts[entry.mailbox] == 1
        and _is_safe_trash_target_entry(entry)
    )


def resolve_trash_folder_from_inventory(
    inventory: ImapListInventoryResult,
    *,
    configured_trash_folder: str | None = None,
) -> TrashFolderResolution:
    """Resolve SPECIAL-USE first, with an exact configured fallback only in B."""
    analysis = analyze_trash_role(inventory)
    if analysis.category == "C":
        return TrashFolderResolution(
            folder=analysis.special_use_folder,
            error=None,
            analysis=analysis,
            source="special_use",
        )
    if analysis.category == "D":
        return TrashFolderResolution(
            folder=None,
            error="trash_folder_ambiguous",
            analysis=analysis,
            source=None,
        )
    if analysis.category != "B":
        return TrashFolderResolution(
            folder=None,
            error="trash_folder_unavailable",
            analysis=analysis,
            source=None,
        )

    if (
        inventory.entries is None
        or not is_runtime_compatible_mailbox_name(configured_trash_folder)
    ):
        return TrashFolderResolution(
            folder=None,
            error="trash_folder_unavailable",
            analysis=analysis,
            source=None,
        )
    exact_matches = tuple(
        entry
        for entry in configurable_trash_folder_entries(inventory)
        if entry.mailbox == configured_trash_folder
    )
    if (
        len(exact_matches) != 1
        or not _is_safe_trash_target_entry(exact_matches[0])
    ):
        return TrashFolderResolution(
            folder=None,
            error="trash_folder_unavailable",
            analysis=analysis,
            source=None,
        )
    return TrashFolderResolution(
        folder=exact_matches[0].mailbox,
        error=None,
        analysis=analysis,
        source="configured",
    )


def resolve_trash_folder(
    mailbox: object,
    *,
    configured_trash_folder: str | None = None,
) -> TrashFolderResolution:
    """Run exactly one LIST and resolve the authoritative Trash target."""
    return resolve_trash_folder_from_inventory(
        read_imap_list_inventory(mailbox),
        configured_trash_folder=configured_trash_folder,
    )


def discover_trash_folder(mailbox: object) -> tuple[str | None, str | None]:
    """Backward-compatible SPECIAL-USE-only Trash discovery wrapper."""
    resolution = resolve_trash_folder(mailbox)
    return resolution.folder, resolution.error


def _parse_capability_tokens(value: object) -> frozenset[str] | None:
    values = value if type(value) in (list, tuple, set, frozenset) else (value,)
    tokens: set[str] = set()
    total_bytes = 0
    for item in values:
        text = _decode_bounded_ascii(item, _MAX_CAPABILITY_BYTES)
        if text is None or _contains_control_characters(text):
            return None
        total_bytes += len(text.encode("ascii"))
        if total_bytes > _MAX_CAPABILITY_BYTES:
            return None
        for token in text.split():
            if not token or any(
                ord(character) < 33 or ord(character) > 126
                for character in token
            ):
                return None
            tokens.add(token.casefold())
    return frozenset(tokens)


def _mailbox_move_capability_error(mailbox: object) -> str | None:
    try:
        capability_method = getattr(mailbox, "capability", None)
    except Exception:
        return "trash_move_unsupported"

    if callable(capability_method):
        try:
            response = capability_method()
        except Exception:
            return "trash_move_unsupported"
        parts = _response_parts(response)
        if parts is None or not _is_ok_status(parts[0]):
            return "trash_move_unsupported"
        capabilities = _parse_capability_tokens(parts[1])
    else:
        try:
            capabilities = _parse_capability_tokens(
                getattr(mailbox, "capabilities", None)
            )
        except Exception:
            return "trash_move_unsupported"

    if capabilities is None or "move" not in capabilities:
        return "trash_move_unsupported"
    if "uidplus" not in capabilities:
        return "trash_uidplus_unsupported"
    return None


def _quote_mailbox_argument(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _identity_error_code(result: object) -> str | None:
    if not isinstance(result, dict):
        return None
    error = result.get("error")
    return error.get("code") if isinstance(error, dict) else None


def _read_source_identity(
    mailbox: object,
    *,
    source_folder: str,
    uid: str,
    expected_uid_validity: str,
) -> tuple[dict | None, ImapTrashResult | None]:
    try:
        result = read_imap_message_identity(
            mailbox,
            folder=source_folder,
            uid=uid,
            expected_uid_validity=expected_uid_validity,
        )
    except Exception:
        return None, _failure("source_identity_unconfirmed", "source_identity")

    if (
        not isinstance(result, dict)
        or result.get("ok") is not True
        or result.get("status") != "ok"
    ):
        code = _identity_error_code(result)
        mapped_code = {
            "folder_unavailable": "source_folder_unavailable",
            "uid_validity_unavailable": "uid_validity_unavailable",
            "uid_validity_changed": "uid_validity_changed",
            "message_not_found": "trash_message_not_found",
        }.get(code, "source_identity_unconfirmed")
        return None, _failure(mapped_code, "source_identity")

    identity = result.get("identity")
    if (
        not isinstance(identity, dict)
        or identity.get("providerFolder") != source_folder
        or identity.get("imapUid") != uid
        or identity.get("uidValidity") != expected_uid_validity
        or not isinstance(identity.get("fingerprint"), str)
        or not identity["fingerprint"]
        or (
            identity.get("rfcMessageId") is not None
            and not isinstance(identity.get("rfcMessageId"), str)
        )
    ):
        return None, _failure("source_identity_unconfirmed", "source_identity")
    return identity, None


def _copyuid_response_is_empty(tag: object, values: object) -> bool:
    empty_values = values in (None, [], (), [None], (None,))
    return empty_values and (tag is None or _is_copyuid_tag(tag))


def _is_copyuid_tag(value: object) -> bool:
    text = _decode_bounded_ascii(value, 32)
    return text is not None and text.casefold() == "copyuid"


def _drain_stale_copyuid(mailbox: object) -> bool:
    for _ in range(_MAX_COPYUID_DRAIN_RESPONSES):
        try:
            response = mailbox.response("COPYUID")
        except Exception:
            return False
        parts = _response_parts(response)
        if parts is None:
            return False
        tag, values = parts
        if _copyuid_response_is_empty(tag, values):
            return True
        if not _is_copyuid_tag(tag) or type(values) not in (list, tuple):
            return False
    return False


def _parse_copyuid_fields(
    uid_validity_text: str,
    source_uid_text: str,
    target_uid_text: str,
    *,
    expected_source_uid: str,
) -> CopyUidMapping | None:
    if (
        not is_canonical_uid_validity(uid_validity_text)
        or int(uid_validity_text) > _MAX_IMAP_UID
        or not _valid_imap_uid(source_uid_text)
        or not _valid_imap_uid(target_uid_text)
        or source_uid_text != expected_source_uid
    ):
        return None
    return uid_validity_text, source_uid_text, target_uid_text


def _flatten_response_scalars(value: object) -> list[object] | None:
    flattened: list[object] = []
    pending = [value]
    while pending:
        if len(pending) + len(flattened) > _MAX_COPYUID_SCALARS:
            return None
        item = pending.pop()
        if item is None:
            continue
        if type(item) in (bytes, str):
            flattened.append(item)
            continue
        if type(item) not in (list, tuple):
            return None
        pending.extend(reversed(item))
    return flattened


def _tagged_copyuid_evidence(
    values: object,
    *,
    expected_source_uid: str,
) -> tuple[CopyUidMapping | None, bool]:
    scalars = _flatten_response_scalars(values)
    if scalars is None:
        return None, False

    mappings: list[CopyUidMapping] = []
    total_bytes = 0
    for scalar in scalars:
        text = _decode_bounded_ascii(scalar, _MAX_COPYUID_BYTES)
        if text is None or _contains_control_characters(text):
            return None, False
        total_bytes += len(text.encode("ascii"))
        if total_bytes > _MAX_COPYUID_BYTES:
            return None, False
        matches = list(_TAGGED_COPYUID_PATTERN.finditer(text))
        if "copyuid" in text.casefold() and not matches:
            return None, False
        without_matches = _TAGGED_COPYUID_PATTERN.sub("", text)
        if "copyuid" in without_matches.casefold():
            return None, False
        for match in matches:
            mapping = _parse_copyuid_fields(
                match.group(1),
                match.group(2),
                match.group(3),
                expected_source_uid=expected_source_uid,
            )
            if mapping is None:
                return None, False
            mappings.append(mapping)

    if len(mappings) > 1:
        return None, False
    return (mappings[0] if mappings else None), True


def _untagged_copyuid_evidence(
    response: object,
    *,
    expected_source_uid: str,
) -> tuple[CopyUidMapping | None, bool]:
    parts = _response_parts(response)
    if parts is None:
        return None, False
    tag, values = parts
    if _copyuid_response_is_empty(tag, values):
        return None, True
    if (
        not _is_copyuid_tag(tag)
        or type(values) not in (list, tuple)
        or len(values) != 1
    ):
        return None, False
    text = _decode_bounded_ascii(values[0], _MAX_COPYUID_BYTES)
    if text is None or _contains_control_characters(text):
        return None, False
    fields = text.split(" ")
    if len(fields) != 3 or " ".join(fields) != text:
        return None, False
    mapping = _parse_copyuid_fields(
        fields[0],
        fields[1],
        fields[2],
        expected_source_uid=expected_source_uid,
    )
    return mapping, mapping is not None


def _current_move_copyuid(
    mailbox: object,
    *,
    move_values: object,
    expected_source_uid: str,
) -> CopyUidMapping | None:
    tagged, tagged_valid = _tagged_copyuid_evidence(
        move_values,
        expected_source_uid=expected_source_uid,
    )
    if not tagged_valid:
        return None
    try:
        untagged_response = mailbox.response("COPYUID")
    except Exception:
        return None
    untagged, untagged_valid = _untagged_copyuid_evidence(
        untagged_response,
        expected_source_uid=expected_source_uid,
    )
    if not untagged_valid:
        return None
    if tagged is None:
        return untagged
    if untagged is None:
        return tagged
    return tagged if tagged == untagged else None


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
    values = parts[1]
    if type(values) not in (list, tuple) or len(values) != 1:
        return "indeterminate"
    text = _decode_bounded_ascii(values[0], _MAX_COPYUID_BYTES)
    if text == "":
        return "absent"
    if text == uid:
        return "present"
    return "indeterminate"


def _verify_source_postcondition(
    mailbox: object,
    *,
    source_folder: str,
    source_uid: str,
    expected_uid_validity: str,
) -> ImapTrashResult | None:
    try:
        select_response = mailbox.select(_quote_mailbox_argument(source_folder))
    except Exception:
        return _failure("trash_move_unconfirmed", "source_postcondition")
    select_parts = _response_parts(select_response)
    if select_parts is None or not _is_ok_status(select_parts[0]):
        return _failure("trash_move_unconfirmed", "source_postcondition")

    current_uid_validity = read_selected_mailbox_uid_validity(mailbox)
    if current_uid_validity is None:
        return _failure("trash_move_unconfirmed", "source_postcondition")
    if current_uid_validity != expected_uid_validity:
        return _failure("trash_move_unconfirmed", "source_postcondition")
    if _source_uid_state(mailbox, source_uid) != "absent":
        return _failure("trash_move_unconfirmed", "source_postcondition")
    return None


def _verify_target_identity(
    mailbox: object,
    *,
    trash_folder: str,
    target_uid: str,
    target_uid_validity: str,
    source_identity: dict,
) -> ImapTrashResult | None:
    try:
        result = read_imap_message_identity(
            mailbox,
            folder=trash_folder,
            uid=target_uid,
            expected_uid_validity=target_uid_validity,
        )
    except Exception:
        return _failure("target_identity_unconfirmed", "target_identity")

    if (
        not isinstance(result, dict)
        or result.get("ok") is not True
        or result.get("status") != "ok"
    ):
        code = _identity_error_code(result)
        mapped_code = {
            "folder_unavailable": "target_folder_unavailable",
            "uid_validity_unavailable": "target_uid_validity_unavailable",
            "uid_validity_changed": "target_uid_validity_changed",
            "message_not_found": "target_message_not_found",
        }.get(code, "target_identity_unconfirmed")
        return _failure(mapped_code, "target_identity")

    identity = result.get("identity")
    if (
        not isinstance(identity, dict)
        or identity.get("providerFolder") != trash_folder
        or identity.get("imapUid") != target_uid
        or identity.get("uidValidity") != target_uid_validity
        or not isinstance(identity.get("fingerprint"), str)
        or not identity["fingerprint"]
    ):
        return _failure("target_identity_unconfirmed", "target_identity")
    if (
        identity["fingerprint"] != source_identity.get("fingerprint")
    ):
        return _failure("trash_target_mismatch", "target_identity")
    return None


def _trash_validated_imap_message(
    mailbox: object,
    *,
    source_folder: str,
    uid: str,
    expected_uid_validity: str,
    configured_trash_folder: str | None,
    mutation_state: dict[str, bool],
) -> ImapTrashResult:
    resolution = resolve_trash_folder(
        mailbox,
        configured_trash_folder=configured_trash_folder,
    )
    trash_folder = resolution.folder
    if resolution.error is not None or trash_folder is None:
        return _failure(
            resolution.error or "trash_folder_unavailable",
            "trash_discovery",
        )
    if source_folder.casefold() == trash_folder.casefold():
        return _failure("invalid_source_folder", "target_validation")
    capability_error = _mailbox_move_capability_error(mailbox)
    if capability_error is not None:
        return _failure(capability_error, "move_capability")

    source_identity, source_error = _read_source_identity(
        mailbox,
        source_folder=source_folder,
        uid=uid,
        expected_uid_validity=expected_uid_validity,
    )
    if source_error is not None or source_identity is None:
        return source_error or _failure(
            "source_identity_unconfirmed",
            "source_identity",
        )

    if not _drain_stale_copyuid(mailbox):
        return _failure("trash_move_unconfirmed", "copyuid_drain")

    mutation_state["attempted"] = True
    try:
        move_response = mailbox.uid(
            "MOVE",
            uid,
            _quote_mailbox_argument(trash_folder),
        )
    except Exception:
        return _failure("trash_move_unconfirmed", "move")
    move_parts = _response_parts(move_response)
    if move_parts is None:
        return _failure("trash_move_unconfirmed", "move")
    if not _is_ok_status(move_parts[0]):
        status_text = _decode_bounded_ascii(move_parts[0], 16)
        if status_text is not None and status_text.casefold() in {"no", "bad"}:
            return _failure("trash_move_failed", "move")
        return _failure("trash_move_unconfirmed", "move")

    copyuid = _current_move_copyuid(
        mailbox,
        move_values=move_parts[1],
        expected_source_uid=uid,
    )
    if copyuid is None:
        return _failure("trash_move_unconfirmed", "copyuid")
    target_uid_validity, _mapped_source_uid, target_uid = copyuid

    source_postcondition_error = _verify_source_postcondition(
        mailbox,
        source_folder=source_folder,
        source_uid=uid,
        expected_uid_validity=expected_uid_validity,
    )
    if source_postcondition_error is not None:
        return source_postcondition_error

    target_error = _verify_target_identity(
        mailbox,
        trash_folder=trash_folder,
        target_uid=target_uid,
        target_uid_validity=target_uid_validity,
        source_identity=source_identity,
    )
    if target_error is not None:
        return target_error

    return _success(
        source_folder=source_folder,
        source_uid=uid,
        source_uid_validity=expected_uid_validity,
        trash_folder=trash_folder,
        target_uid=target_uid,
        target_uid_validity=target_uid_validity,
    )


def trash_imap_message(
    mailbox: object,
    *,
    source_folder: str,
    uid: str,
    expected_uid_validity: str,
    configured_trash_folder: str | None = None,
) -> ImapTrashResult:
    """MOVE one exact UID to a freshly validated authoritative Trash target."""
    if not _valid_source_folder(source_folder):
        return _failure("invalid_source_folder", "input_validation")
    if source_folder != "INBOX":
        return _failure("invalid_source_folder", "input_validation")
    if not _valid_imap_uid(uid):
        return _failure("invalid_imap_uid", "input_validation")
    if not is_canonical_uid_validity(expected_uid_validity):
        return _failure("invalid_uid_validity", "input_validation")

    mutation_state = {"attempted": False}
    try:
        return _trash_validated_imap_message(
            mailbox,
            source_folder=source_folder,
            uid=uid,
            expected_uid_validity=expected_uid_validity,
            configured_trash_folder=configured_trash_folder,
            mutation_state=mutation_state,
        )
    except Exception:
        return _failure(
            "imap_trash_failed",
            "post_move" if mutation_state["attempted"] else "pre_move",
        )
