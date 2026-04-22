from __future__ import annotations

from typing import Any

COLLABORATION_THREAD_SCHEMA_VERSION = 1


def _normalize_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    return normalized or None


def _normalize_email(value: Any) -> str | None:
    normalized = _normalize_string(value)
    return normalized.lower() if normalized else None


def _normalize_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float) and value.is_integer():
        return int(value)

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None

    return None


def _normalize_bool(value: Any) -> bool:
    return value is True


def _normalize_string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None

    normalized_values: list[str] = []
    for entry in value:
        normalized_entry = _normalize_string(entry)
        if normalized_entry is None:
            return None
        normalized_values.append(normalized_entry)

    return normalized_values


def normalize_collaboration_mention_record(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None

    mention_id = _normalize_string(value.get("id"))
    name = _normalize_string(value.get("name"))
    email = _normalize_email(value.get("email"))
    handle = _normalize_string(value.get("handle"))

    if not mention_id or not name or not email or not handle:
        return None

    return {
        "id": mention_id,
        "name": name,
        "email": email,
        "handle": handle,
        "notify": _normalize_bool(value.get("notify")),
    }


def normalize_collaboration_message_record(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None

    message_id = _normalize_string(value.get("id"))
    author_id = _normalize_string(value.get("authorId"))
    author_name = _normalize_string(value.get("authorName"))
    text = _normalize_string(value.get("text"))
    timestamp = _normalize_int(value.get("timestamp"))

    if not message_id or not author_id or not author_name or text is None or timestamp is None:
        return None

    visibility = _normalize_string(value.get("visibility"))
    if visibility not in {"internal", "shared", None}:
        visibility = None

    mentions = value.get("mentions")
    normalized_mentions: list[dict] = []
    if isinstance(mentions, list):
        for mention in mentions:
            normalized_mention = normalize_collaboration_mention_record(mention)
            if normalized_mention:
                normalized_mentions.append(normalized_mention)

    normalized_message = {
        "id": message_id,
        "authorId": author_id,
        "authorName": author_name,
        "text": text,
        "timestamp": timestamp,
    }

    if visibility:
        normalized_message["visibility"] = visibility

    if normalized_mentions:
        normalized_message["mentions"] = normalized_mentions

    return normalized_message


def normalize_collaboration_participant_record(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None

    participant_id = _normalize_string(value.get("id"))
    name = _normalize_string(value.get("name"))
    email = _normalize_email(value.get("email"))
    kind = _normalize_string(value.get("kind"))
    status = _normalize_string(value.get("status"))

    if (
        not participant_id
        or not name
        or not email
        or kind not in {"internal", "external"}
        or status not in {"active", "invited", "declined"}
    ):
        return None

    normalized_participant = {
        "id": participant_id,
        "name": name,
        "email": email,
        "kind": kind,
        "status": status,
    }

    external_review_token = _normalize_string(value.get("externalReviewToken"))
    if external_review_token:
        normalized_participant["externalReviewToken"] = external_review_token

    return normalized_participant


def normalize_collaboration_record(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None

    state = _normalize_string(value.get("state"))
    requested_by = _normalize_string(value.get("requestedBy"))
    requested_user_id = _normalize_string(value.get("requestedUserId"))
    requested_user_name = _normalize_string(value.get("requestedUserName"))
    created_at = _normalize_int(value.get("createdAt"))
    updated_at = _normalize_int(value.get("updatedAt"))
    messages = value.get("messages")

    if (
        state not in {"needs_review", "needs_action", "note_only", "resolved"}
        or not requested_by
        or not requested_user_id
        or not requested_user_name
        or created_at is None
        or updated_at is None
        or not isinstance(messages, list)
    ):
        return None

    normalized_messages: list[dict] = []
    for message in messages:
        normalized_message = normalize_collaboration_message_record(message)
        if normalized_message:
            normalized_messages.append(normalized_message)

    if len(normalized_messages) != len(messages):
        return None

    participants = value.get("participants")
    normalized_participants: list[dict] = []
    if isinstance(participants, list):
        for participant in participants:
            normalized_participant = normalize_collaboration_participant_record(participant)
            if normalized_participant:
                normalized_participants.append(normalized_participant)

    normalized_collaboration = {
        "state": state,
        "requestedBy": requested_by,
        "requestedUserId": requested_user_id,
        "requestedUserName": requested_user_name,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "participants": normalized_participants,
        "messages": normalized_messages,
    }

    resolved_at = _normalize_int(value.get("resolvedAt"))
    if resolved_at is not None:
        normalized_collaboration["resolvedAt"] = resolved_at

    resolved_by_user_id = _normalize_string(value.get("resolvedByUserId"))
    if resolved_by_user_id:
        normalized_collaboration["resolvedByUserId"] = resolved_by_user_id

    resolved_by_user_name = _normalize_string(value.get("resolvedByUserName"))
    if resolved_by_user_name:
        normalized_collaboration["resolvedByUserName"] = resolved_by_user_name

    preview_text = _normalize_string(value.get("previewText"))
    if preview_text:
        normalized_collaboration["previewText"] = preview_text

    return normalized_collaboration


def normalize_source_message_snapshot(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None

    message_id = _normalize_string(value.get("id"))
    subject = _normalize_string(value.get("subject"))
    sender = _normalize_string(value.get("sender"))
    from_value = _normalize_string(value.get("from"))
    timestamp = _normalize_string(value.get("timestamp"))
    snippet = _normalize_string(value.get("snippet"))
    body = _normalize_string_list(value.get("body"))

    if (
        not message_id
        or subject is None
        or sender is None
        or from_value is None
        or timestamp is None
        or snippet is None
        or body is None
    ):
        return None

    normalized_source_message = {
        "id": message_id,
        "subject": subject,
        "sender": sender,
        "from": from_value,
        "timestamp": timestamp,
        "snippet": snippet,
        "body": body,
    }

    body_html = _normalize_string(value.get("bodyHtml"))
    if body_html:
        normalized_source_message["bodyHtml"] = body_html

    return normalized_source_message


def normalize_collaboration_thread_record(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None

    version = _normalize_int(value.get("v"))
    workspace_id = _normalize_string(value.get("workspaceId"))
    mailbox_id = _normalize_string(value.get("mailboxId"))
    message_id = _normalize_string(value.get("messageId"))
    source_message = normalize_source_message_snapshot(value.get("sourceMessage"))
    collaboration = normalize_collaboration_record(value.get("collaboration"))

    if (
        version != COLLABORATION_THREAD_SCHEMA_VERSION
        or not workspace_id
        or not mailbox_id
        or not message_id
        or source_message is None
        or collaboration is None
    ):
        return None

    return {
        "v": COLLABORATION_THREAD_SCHEMA_VERSION,
        "workspaceId": workspace_id,
        "mailboxId": mailbox_id,
        "messageId": message_id,
        "sourceMessage": source_message,
        "isShared": _normalize_bool(value.get("isShared")),
        "collaboration": collaboration,
    }


def normalize_collaboration_invite_record(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None

    version = _normalize_int(value.get("v"))
    token = _normalize_string(value.get("token"))
    workspace_id = _normalize_string(value.get("workspaceId"))
    mailbox_id = _normalize_string(value.get("mailboxId"))
    message_id = _normalize_string(value.get("messageId"))
    invitee_email = _normalize_email(value.get("inviteeEmail"))
    participant_id = _normalize_string(value.get("participantId"))
    status = _normalize_string(value.get("status"))
    created_at = _normalize_int(value.get("createdAt"))
    updated_at = _normalize_int(value.get("updatedAt"))
    created_by_user_id = _normalize_string(value.get("createdByUserId"))
    created_by_user_name = _normalize_string(value.get("createdByUserName"))

    if (
        version != COLLABORATION_THREAD_SCHEMA_VERSION
        or not token
        or not workspace_id
        or not mailbox_id
        or not message_id
        or not invitee_email
        or not participant_id
        or status not in {"active", "revoked", "expired"}
        or created_at is None
        or updated_at is None
        or not created_by_user_id
        or not created_by_user_name
    ):
        return None

    normalized_invite = {
        "v": COLLABORATION_THREAD_SCHEMA_VERSION,
        "token": token,
        "workspaceId": workspace_id,
        "mailboxId": mailbox_id,
        "messageId": message_id,
        "inviteeEmail": invitee_email,
        "participantId": participant_id,
        "status": status,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "createdByUserId": created_by_user_id,
        "createdByUserName": created_by_user_name,
    }

    expires_at = _normalize_int(value.get("expiresAt"))
    if expires_at is not None:
        normalized_invite["expiresAt"] = expires_at

    return normalized_invite


def is_active_collaboration_invite_record(value: Any) -> bool:
    normalized_invite = normalize_collaboration_invite_record(value)
    return bool(normalized_invite and normalized_invite["status"] == "active")


def build_external_collaboration_thread_view(value: Any) -> dict | None:
    normalized_thread = normalize_collaboration_thread_record(value)
    if not normalized_thread:
        return None

    external_participants: list[dict] = []
    for participant in normalized_thread["collaboration"]["participants"]:
        external_participants.append(
            {
                key: participant_value
                for key, participant_value in participant.items()
                if key != "externalReviewToken"
            }
        )

    external_messages = [
        message
        for message in normalized_thread["collaboration"]["messages"]
        if message.get("visibility") in {None, "shared"}
    ]

    return {
        **normalized_thread,
        "collaboration": {
            **normalized_thread["collaboration"],
            "participants": external_participants,
            "messages": external_messages,
        },
    }
