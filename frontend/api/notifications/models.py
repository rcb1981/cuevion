"""Strict, body-free v1 notification authority and its recipient-free DTO."""

from __future__ import annotations

import json
import re

from api.collaboration.models import (
    MIN_V2_TIMESTAMP_MILLISECONDS,
    MAX_V2_TIMESTAMP_MILLISECONDS,
    _v2_ascii_identifier,
    _v2_bounded_string,
    is_v2_opaque_id,
    normalize_v2_source_ref,
    normalize_v2_user_id,
    normalize_v2_workspace_id,
)

NOTIFICATION_KINDS = frozenset({
    "collaboration_started", "participant_added", "shared_message", "internal_note",
})
MAX_NOTIFICATION_PAGE_SIZE = 50
MAX_NOTIFICATIONS_PER_RECIPIENT = 1_000
NOTIFICATION_RETENTION_MS = 180 * 24 * 60 * 60 * 1_000
MAX_NOTIFICATION_RECORD_BYTES = 4_096
_FIELDS = frozenset({
    "v", "notificationId", "workspaceId", "recipientUserId", "kind",
    "collaborationId", "mailboxId", "sourceRef", "activityId", "actor",
    "createdAt", "expiresAt", "readAt",
})


def is_notification_id(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"ntf_[0-9a-f]{40}", value) is not None


def _timestamp(value: object) -> bool:
    return type(value) is int and MIN_V2_TIMESTAMP_MILLISECONDS <= value <= MAX_V2_TIMESTAMP_MILLISECONDS


def normalize_notification_actor(value: object) -> dict | None:
    if type(value) is not dict:
        return None
    actor_type = value.get("type")
    if type(actor_type) is not str:
        return None
    fields = {"type", "displayName", "userId"} if actor_type == "cuevion_user" else {"type", "displayName"}
    if set(value) != fields or actor_type not in {"cuevion_user", "external_guest"}:
        return None
    if _v2_bounded_string(value.get("displayName"), max_length=256) is None:
        return None
    if actor_type == "cuevion_user" and normalize_v2_user_id(value["userId"]) is None:
        return None
    return dict(value)


def normalize_notification_record(value: object) -> dict | None:
    if type(value) is not dict or set(value) != _FIELDS:
        return None
    actor = normalize_notification_actor(value.get("actor"))
    source = normalize_v2_source_ref(value.get("sourceRef"))
    mailbox = value.get("mailboxId")
    kind = value.get("kind")
    created, expires, read = value.get("createdAt"), value.get("expiresAt"), value.get("readAt")
    if (
        type(value.get("v")) is not int or value["v"] != 1
        or not is_notification_id(value.get("notificationId"))
        or normalize_v2_workspace_id(value.get("workspaceId")) is None
        or normalize_v2_user_id(value.get("recipientUserId")) is None
        or type(kind) is not str or kind not in NOTIFICATION_KINDS
        or not is_v2_opaque_id(value.get("collaborationId"))
        or _v2_ascii_identifier(mailbox, max_length=256) is None
        or re.fullmatch(r"[a-z0-9][a-z0-9._:-]*", mailbox) is None
        or source is None or actor is None
        or not _timestamp(created) or not _timestamp(expires)
        or expires <= created or expires > created + NOTIFICATION_RETENTION_MS
        or (read is not None and (not _timestamp(read) or read < created or read >= expires))
        or (value["activityId"] is not None and not is_v2_opaque_id(value["activityId"]))
        or (kind in {"shared_message", "internal_note"} and value["activityId"] is None)
        or (kind != "shared_message" and actor["type"] == "external_guest")
        or (actor["type"] == "cuevion_user" and actor["userId"] == value["recipientUserId"])
    ):
        return None
    normalized = {**value, "actor": actor, "sourceRef": source}
    try:
        if len(json.dumps(normalized, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_NOTIFICATION_RECORD_BYTES:
            return None
    except (ValueError, UnicodeError, TypeError):
        return None
    return normalized


def notification_dto(record: object) -> dict | None:
    normalized = normalize_notification_record(record)
    if normalized is None:
        return None
    return {key: value for key, value in normalized.items() if key != "recipientUserId"}


def normalize_notification_dto(value: object) -> dict | None:
    if type(value) is not dict or set(value) != _FIELDS - {"recipientUserId"}:
        return None
    # DTO carries no authority; supply a distinct valid sentinel only for schema validation.
    sentinel = "usr_" + "A" * 22
    if type(value.get("actor")) is dict and value["actor"].get("userId") == sentinel:
        sentinel = "usr_" + "B" * 21 + "A"
    return notification_dto({**value, "recipientUserId": sentinel})


def decode_notification_wire(raw: object) -> dict | None:
    def reject_number(_value):
        raise ValueError("noncanonical notification integer")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate notification field")
            result[key] = value
        return result

    if type(raw) is not str:
        return None
    try:
        if len(raw.encode("utf-8")) > MAX_NOTIFICATION_RECORD_BYTES:
            return None
        value = json.loads(raw, parse_int=reject_number, parse_float=reject_number,
                           parse_constant=reject_number, object_pairs_hook=unique_object)
        if type(value) is not dict:
            return None
        for field in ("v", "createdAt", "expiresAt", "readAt"):
            entry = value.get(field)
            if field == "readAt" and entry is None and field in value:
                continue
            if type(entry) is not str or re.fullmatch(r"(?:0|[1-9][0-9]{0,15})", entry) is None:
                return None
            value[field] = int(entry)
        return normalize_notification_record(value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return None
