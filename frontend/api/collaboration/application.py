from __future__ import annotations

if __name__ != "api.collaboration.application":
    raise ImportError(
        "api.collaboration.application must be imported by its canonical package path"
    )

import time
from typing import Any

from .authorization import (
    _resolve_active_team_member,
    _is_internal_capability,
    resolve_internal_collaboration_context,
    resolve_verified_owner_collaboration_context,
)
from .guest_session import (
    INVITE_LIFETIME_SECONDS,
    _is_guest_read_capability,
    _resolve_guest_read_access,
    normalize_v2_guest_session_record,
    read_guest_session_cookie,
)
from .models import (
    COLLABORATION_V2_THREAD_SCHEMA_VERSION,
    COLLABORATION_V2_SAFE_ERROR_CODES,
    MAX_V2_MESSAGE_TEXT,
    MAX_V2_EXPLICIT_PARTICIPANTS,
    MAX_V2_TIMESTAMP_MILLISECONDS,
    MAX_V2_TIMESTAMP_SECONDS,
    MIN_V2_TIMESTAMP_MILLISECONDS,
    MIN_V2_TIMESTAMP_SECONDS,
    _v2_free_text,
    build_v2_guest_thread_dto,
    generate_v2_bearer_secret,
    generate_v2_opaque_id,
    hash_v2_secret,
    is_v2_opaque_id,
    normalize_v2_email,
    normalize_v2_external_guest_projection,
    normalize_v2_invite_record,
    normalize_v2_source_ref,
    normalize_v2_participant_authority,
    normalize_v2_team_membership_ref,
    normalize_v2_thread_record,
    normalize_v2_user_id,
)
from .redis_store import (
    _V2RecordResult,
    _V2ThreadInviteCreateResult,
    _create_v2_thread,
    _create_v2_thread_with_guest,
    _load_v2_external_guest_records,
    _load_v2_thread,
    _load_v2_thread_by_source,
)
from .source_message import resolve_source_message


_SAFE_RESULT_STATUSES = frozenset(
    {
        "error",
        "expired",
        "forbidden",
        "malformed",
        "missing",
        "not_found",
        "revoked",
        "unauthorized",
        "unavailable",
    }
)

_SAFE_THREAD_LOAD_ERROR_CODES = frozenset(
    {
        "storage_protocol_error",
        "storage_unavailable",
    }
)

_ALLOWED_INITIAL_STATES = frozenset(
    {
        "needs_review",
        "needs_action",
        "note_only",
    }
)

_CANONICAL_OWNER_MUTATION_ERROR_CODES = {
    "collaboration_not_found": "collaboration_not_found",
    "forbidden": "forbidden",
    "invalid_request": "invalid_request",
    "idempotency_conflict": "idempotency_conflict",
    "stale_thread": "stale_thread",
    "storage_protocol_error": "storage_protocol_error",
    "storage_unavailable": "storage_unavailable",
}

_OWNER_MUTATION_MESSAGE_FIELDS = frozenset(
    {
        "id",
        "authorDisplayName",
        "authorRole",
        "text",
        "timestamp",
        "visibility",
    }
)

_AUTHOR_ROLE_BY_KIND = {
    "owner": "Cuevion user",
    "internal": "Cuevion user",
    "guest": "Guest reviewer",
    "system": "System",
}


def _failure(status: str, code: str) -> dict[str, Any]:
    safe_status = status if status in _SAFE_RESULT_STATUSES else "error"
    safe_code = (
        code
        if code in COLLABORATION_V2_SAFE_ERROR_CODES
        else "storage_protocol_error"
    )
    return {
        "status": safe_status,
        "collaboration": None,
        "error": {"code": safe_code},
    }


def _failure_from_result(
    value: object,
    *,
    default_status: str,
    default_code: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        return _failure(default_status, default_code)

    status = value.get("status")
    error = value.get("error")
    code = error.get("code") if type(error) is dict else None
    return _failure(
        status if type(status) is str else default_status,
        code if type(code) is str else default_code,
    )


def _success(dto: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "collaboration": dto, "error": None}


def _thread_load_failure(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        return _failure("malformed", "storage_protocol_error")

    if set(value) == {"status"} and value.get("status") == "missing":
        return _failure("not_found", "collaboration_not_found")

    if set(value) == {"status", "error"} and value.get("status") == "unavailable":
        error = value.get("error")
        if type(error) is dict and set(error) == {"code"}:
            code = error.get("code")
            if code in _SAFE_THREAD_LOAD_ERROR_CODES:
                return _failure("unavailable", code)
        return _failure("unavailable", "storage_protocol_error")

    return _failure("malformed", "storage_protocol_error")


def _load_exact_thread(
    collaboration_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    loaded = _load_v2_thread(collaboration_id)

    if type(loaded) is _V2RecordResult:
        if loaded.status != "ok":
            return None, _failure("malformed", "storage_protocol_error")
        record = loaded.record
    else:
        return None, _thread_load_failure(loaded)

    normalized = normalize_v2_thread_record(record)
    if normalized is None:
        return None, _failure("malformed", "storage_protocol_error")
    return normalized, None


def _thread_matches_owner_capability(thread: dict[str, Any], capability: object) -> bool:
    return (
        thread["collaborationId"] == capability.collaboration_id
        and thread["ownerEmail"] == capability.owner_email
        and thread["workspaceId"] == capability.workspace_id
        and thread["mailboxId"] == capability.mailbox_id
    )


def _thread_matches_guest_capability(thread: dict[str, Any], capability: object) -> bool:
    return (
        thread["collaborationId"] == capability.collaboration_id
        and thread["ownerEmail"] == capability.owner_email
        and thread["workspaceId"] == capability.workspace_id
        and thread["mailboxId"] == capability.mailbox_id
    )


def _thread_matches_create_binding(
    thread: dict[str, Any],
    capability: object,
    source_ref: dict[str, Any],
) -> bool:
    return (
        thread["ownerEmail"] == capability.owner_email
        and thread["workspaceId"] == capability.workspace_id
        and thread["mailboxId"] == capability.mailbox_id
        and thread["sourceRef"] == source_ref
    )


def _guest_session_matches_capability(
    session: object,
    capability: object,
) -> bool:
    return type(session) is dict and (
        session.get("sessionHash") == capability.session_hash
        and session.get("inviteId") == capability.invite_id
        and session.get("ownerEmail") == capability.owner_email
        and session.get("workspaceId") == capability.workspace_id
        and session.get("mailboxId") == capability.mailbox_id
        and session.get("collaborationId") == capability.collaboration_id
        and session.get("guestDisplayName") == capability.guest_display_name
        and session.get("expiresAt") == capability.expires_at
        and session.get("status") == "active"
        and session.get("allowedActions") == ["read", "reply"]
        and session.get("visibility") == "shared_only"
        and session.get("identityAssurance") == "link_possession"
    )


def _build_owner_thread_dto(thread: dict[str, Any]) -> dict[str, Any]:
    source_message = thread["sourceMessage"]
    return {
        "collaborationId": thread["collaborationId"],
        "mailboxId": thread["mailboxId"],
        "state": thread["state"],
        "createdAt": thread["createdAt"],
        "updatedAt": thread["updatedAt"],
        "source": {
            "subject": source_message["subject"],
            "senderDisplay": source_message["senderDisplay"],
            "fromDisplay": source_message["fromDisplay"],
            "timestamp": source_message["timestamp"],
            "bodyText": source_message["bodyText"],
        },
        "messages": [
            {
                "id": message["id"],
                "authorDisplayName": message["authorDisplayName"],
                "authorRole": _AUTHOR_ROLE_BY_KIND[message["authorKind"]],
                "text": message["text"],
                "visibility": message["visibility"],
                "timestamp": message["createdAt"],
            }
            for message in thread["messages"]
        ],
    }


def _build_verified_thread_dto(
    thread: dict[str, Any],
    capability: object,
    *,
    team_member_resolver=None,
    external_guests: object | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if team_member_resolver is None:
        team_member_resolver = _resolve_active_team_member
    if (
        not _is_internal_capability(capability, actions={"read", "create", "manage_participants"})
        or capability.viewer_access not in {"owner", "participant"}
    ):
        return None, _failure("forbidden", "forbidden")
    owner_user_id = (
        thread.get("ownerUserId")
        if "ownerUserId" in thread
        else capability.owner_user_id
    )
    owner_display_name = (
        thread.get("ownerDisplayName")
        if "ownerDisplayName" in thread
        else capability.owner_display_name
    )
    if (
        normalize_v2_user_id(owner_user_id) != owner_user_id
        or type(owner_display_name) is not str
        or not owner_display_name
    ):
        return None, _failure("malformed", "storage_protocol_error")
    visible_participants = [
        {
            "userId": owner_user_id,
            "displayName": owner_display_name,
            "access": "owner",
        }
    ]
    for participant in thread.get("participants", []):
        try:
            membership, team_error = team_member_resolver(
                thread["workspaceId"],
                participant["userId"],
            )
        except Exception:
            return None, _failure("unavailable", "storage_unavailable")
        if team_error == "unavailable":
            return None, _failure("unavailable", "storage_unavailable")
        if team_error is not None or type(membership) is not dict:
            continue
        if (
            membership.get("memberUserId") != participant["userId"]
            or membership.get("sourceInvitationId")
            != participant["membershipRef"]
        ):
            continue
        visible_participants.append(
            {
                "userId": participant["userId"],
                "displayName": participant["displayName"],
                "access": "participant",
            }
        )
    if not 1 <= len(visible_participants) <= MAX_V2_EXPLICIT_PARTICIPANTS + 1:
        return None, _failure("malformed", "storage_protocol_error")
    result = {
        **_build_owner_thread_dto(thread),
        "viewerAccess": capability.viewer_access,
        "participants": visible_participants,
    }
    if capability.viewer_access == "owner" and external_guests is None:
        return None, _failure("malformed", "storage_protocol_error")
    if capability.viewer_access == "participant" and external_guests is not None:
        return None, _failure("forbidden", "forbidden")
    if external_guests is not None:
        normalized_external_guests = normalize_v2_external_guest_projection(
            external_guests
        )
        if normalized_external_guests is None:
            return None, _failure("malformed", "storage_protocol_error")
        result["externalGuests"] = normalized_external_guests
    return result, None


def _project_v2_external_guests(records: object, *, now: int) -> list[dict] | None:
    if not isinstance(records, list) or type(now) is not int:
        return None
    projected: list[dict] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"invite", "session"}:
            return None
        invite = record.get("invite")
        session = record.get("session")
        if not isinstance(invite, dict) or (
            session is not None and normalize_v2_guest_session_record(session) != session
        ):
            return None
        if invite.get("status") == "revoked":
            status = "revoked"
        elif invite.get("status") == "expired" or invite.get("expiresAt", 0) <= now:
            status = "expired"
        elif invite.get("status") == "active":
            status = "pending"
        elif invite.get("status") == "exchanged" and session is None:
            status = "expired"
        elif invite.get("status") == "exchanged" and session.get("status") == "logged_out":
            status = "logged_out"
        elif invite.get("status") == "exchanged" and session.get("status") == "revoked":
            status = "revoked"
        elif invite.get("status") == "exchanged" and (
            session.get("status") == "expired" or session.get("expiresAt", 0) <= now
        ):
            status = "expired"
        elif invite.get("status") == "exchanged" and session.get("status") == "active":
            status = "active"
        else:
            return None
        item = {
            "inviteId": invite.get("inviteId"),
            "status": status,
            "expiresAt": invite.get("expiresAt"),
        }
        if invite.get("invitedEmail") is not None:
            item["invitedEmail"] = invite["invitedEmail"]
        if session is not None:
            item["displayName"] = session["guestDisplayName"]
        projected.append(item)
    return normalize_v2_external_guest_projection(projected)


def _build_verified_owner_thread_dto(
    thread: dict[str, Any],
    capability: object,
    *,
    team_member_resolver=None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if (
        not _is_internal_capability(
            capability,
            actions={"read", "create", "manage_participants"},
        )
        or capability.viewer_access != "owner"
        or thread.get("ownerEmail") != capability.owner_email
        or thread.get("workspaceId") != capability.workspace_id
        or thread.get("mailboxId") != capability.mailbox_id
        or (
            capability.collaboration_id is not None
            and thread.get("collaborationId") != capability.collaboration_id
        )
    ):
        return None, _failure("forbidden", "forbidden")
    current_time = int(time.time())
    if not MIN_V2_TIMESTAMP_SECONDS <= current_time <= MAX_V2_TIMESTAMP_SECONDS:
        return None, _failure("error", "invalid_request")
    loaded_guests = _load_v2_external_guest_records(
        thread["collaborationId"],
        owner_email=thread["ownerEmail"],
        workspace_id=thread["workspaceId"],
        mailbox_id=thread["mailboxId"],
        now=current_time,
        session_normalizer=normalize_v2_guest_session_record,
    )
    if type(loaded_guests) is not dict or loaded_guests.get("status") != "ok":
        return None, _create_storage_failure(loaded_guests)
    external_guests = _project_v2_external_guests(
        loaded_guests.get("records"), now=current_time
    )
    if external_guests is None:
        return None, _failure("malformed", "storage_protocol_error")
    return _build_verified_thread_dto(
        thread,
        capability,
        team_member_resolver=team_member_resolver,
        external_guests=external_guests,
    )


def _safe_v2_invitation_metadata(invite: dict[str, Any]) -> dict[str, Any]:
    result = {
        "inviteId": invite["inviteId"],
        "collaborationId": invite["collaborationId"],
        "allowedActions": ["read", "reply"],
        "identityAssurance": "link_possession",
        "expiresAt": invite["expiresAt"],
        "status": invite["status"],
    }
    if "invitedEmail" in invite:
        result["invitedEmail"] = invite["invitedEmail"]
    return result


def _resolve_participant_authority(
    workspace_id: str,
    participant_user_id: object,
    *,
    team_member_resolver=None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if team_member_resolver is None:
        team_member_resolver = _resolve_active_team_member
    canonical_user_id = normalize_v2_user_id(participant_user_id)
    if canonical_user_id is None or canonical_user_id != participant_user_id:
        return None, _failure("malformed", "invalid_request")
    try:
        membership, team_error = team_member_resolver(
            workspace_id,
            canonical_user_id,
        )
    except Exception:
        return None, _failure("unavailable", "storage_unavailable")
    if team_error == "unavailable":
        return None, _failure("unavailable", "storage_unavailable")
    if team_error is not None or type(membership) is not dict:
        return None, _failure("forbidden", "forbidden")
    membership_ref = normalize_v2_team_membership_ref(
        membership.get("sourceInvitationId")
    )
    participant = normalize_v2_participant_authority(
        {
            "userId": membership.get("memberUserId"),
            "membershipRef": membership_ref,
            "displayName": membership.get("displayName"),
        }
    )
    if participant is None or participant["userId"] != canonical_user_id:
        return None, _failure("unavailable", "storage_protocol_error")
    return participant, None


def _create_storage_failure(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"status", "error"}:
        return _failure("malformed", "storage_protocol_error")
    status = value.get("status")
    error = value.get("error")
    if type(status) is not str or type(error) is not dict or set(error) != {"code"}:
        return _failure("malformed", "storage_protocol_error")
    code = error.get("code")
    if type(code) is not str or code not in COLLABORATION_V2_SAFE_ERROR_CODES:
        return _failure("malformed", "storage_protocol_error")
    return _failure(status, code)


def _owner_mutation_failure(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"status", "error"}:
        return _failure("malformed", "storage_protocol_error")
    status = value.get("status")
    error = value.get("error")
    code = error.get("code") if type(error) is dict else None
    if (
        type(status) is not str
        or status != "error"
        or type(error) is not dict
        or set(error) != {"code"}
        or type(code) is not str
    ):
        return _failure("malformed", "storage_protocol_error")
    canonical_code = _CANONICAL_OWNER_MUTATION_ERROR_CODES.get(code)
    if canonical_code is None:
        return _failure("malformed", "storage_protocol_error")
    return _failure("error", canonical_code)


def _owner_mutation_dto(
    value: object,
    *,
    capability: object,
    text: str,
    visibility: str,
) -> dict[str, Any] | None:
    status = value.get("status") if type(value) is dict else None
    if (
        type(value) is not dict
        or set(value) != {"status", "message", "updatedAt", "error"}
        or type(status) is not str
        or status != "ok"
        or value.get("error") is not None
    ):
        return None

    message = value.get("message")
    updated_at = value.get("updatedAt")
    message_id = message.get("id") if type(message) is dict else None
    author_display_name = (
        message.get("authorDisplayName") if type(message) is dict else None
    )
    author_role = message.get("authorRole") if type(message) is dict else None
    message_text = message.get("text") if type(message) is dict else None
    message_visibility = (
        message.get("visibility") if type(message) is dict else None
    )
    if (
        type(message) is not dict
        or set(message) != _OWNER_MUTATION_MESSAGE_FIELDS
        or type(message_id) is not str
        or not is_v2_opaque_id(message_id)
        or type(author_display_name) is not str
        or author_display_name != capability.actor_display_name
        or type(author_role) is not str
        or author_role != "Cuevion user"
        or type(message_text) is not str
        or message_text != text
        or type(message_visibility) is not str
        or message_visibility != visibility
        or type(message.get("timestamp")) is not int
        or message.get("timestamp") < MIN_V2_TIMESTAMP_MILLISECONDS
        or message.get("timestamp") > MAX_V2_TIMESTAMP_MILLISECONDS
        or type(updated_at) is not int
        or updated_at != message.get("timestamp")
    ):
        return None

    return {
        "message": {
            "id": message["id"],
            "authorDisplayName": message["authorDisplayName"],
            "authorRole": message["authorRole"],
            "text": message["text"],
            "timestamp": message["timestamp"],
            "visibility": message["visibility"],
        },
        "updatedAt": updated_at,
    }


def _append_internal_v2_message(
    capability: object,
    text: str,
) -> dict:
    # Keep application.py's inactive import graph stable; the mutation foundation
    # is loaded only if one of the inactive mutation services is explicitly called.
    from .mutations import append_internal_v2_message

    if capability.action == "reply":
        visibility = "shared"
    elif capability.action == "internal_note":
        visibility = "internal"
    else:
        return {"status": "error", "error": {"code": "forbidden"}}
    return append_internal_v2_message(
        capability,
        text,
        visibility=visibility,
    )


def _append_idempotent_v2_owner_message(
    capability: object,
    text: str,
    *,
    idempotency_key: object,
) -> dict:
    # This adapter is intentionally used only by the verified owner HTTP path;
    # inactive internal/Team and guest helpers retain their existing behavior.
    from .mutations import append_owner_v2_message_idempotently

    if capability.action == "reply":
        visibility = "shared"
    elif capability.action == "internal_note":
        visibility = "internal"
    else:
        return {"status": "error", "error": {"code": "forbidden"}}
    return append_owner_v2_message_idempotently(
        capability,
        text,
        visibility=visibility,
        idempotency_key=idempotency_key,
    )


def _append_v2_owner_message(
    headers: object,
    collaboration_id: object,
    payload: object,
    *,
    required_action: str,
    owner_context: object | None = None,
    owner_security_configuration: object | None = None,
    idempotency_key: object | None = None,
) -> dict[str, Any]:
    if required_action == "reply":
        visibility = "shared"
    elif required_action == "internal_note":
        visibility = "internal"
    else:
        return _failure("malformed", "invalid_request")
    if type(payload) is not dict or set(payload) != {"text"}:
        return _failure("malformed", "invalid_request")
    text = payload.get("text")
    if (
        type(text) is not str
        or _v2_free_text(text, max_length=MAX_V2_MESSAGE_TEXT) != text
    ):
        return _failure("malformed", "invalid_request")

    if owner_context is None:
        authorized = resolve_internal_collaboration_context(
            headers,
            collaboration_id=collaboration_id,
            required_action=required_action,
        )
    else:
        authorized = resolve_verified_owner_collaboration_context(
            owner_context,
            headers,
            collaboration_id=collaboration_id,
            required_action=required_action,
            owner_security_configuration=owner_security_configuration,
        )
    if type(authorized) is not dict or authorized.get("status") != "ok":
        return _failure_from_result(
            authorized,
            default_status="error",
            default_code="storage_protocol_error",
        )
    if (
        set(authorized) != {"status", "context", "error"}
        or authorized.get("error") is not None
    ):
        return _failure("malformed", "storage_protocol_error")

    capability = authorized.get("context")
    if (
        not _is_internal_capability(
            capability,
            actions=frozenset({required_action}),
        )
        or capability.collaboration_id != collaboration_id
    ):
        return _failure("forbidden", "forbidden")

    mutated = (
        _append_internal_v2_message(
            capability,
            text,
        )
        if owner_context is None
        else _append_idempotent_v2_owner_message(
            capability,
            text,
            idempotency_key=idempotency_key,
        )
    )
    dto = _owner_mutation_dto(
        mutated,
        capability=capability,
        text=text,
        visibility=visibility,
    )
    if dto is not None:
        return dto
    return _owner_mutation_failure(mutated)


def append_v2_shared_message_for_owner(
    headers: object,
    collaboration_id: object,
    payload: object,
) -> dict[str, Any]:
    return _append_v2_owner_message(
        headers,
        collaboration_id,
        payload,
        required_action="reply",
    )


def append_v2_internal_note_for_owner(
    headers: object,
    collaboration_id: object,
    payload: object,
) -> dict[str, Any]:
    return _append_v2_owner_message(
        headers,
        collaboration_id,
        payload,
        required_action="internal_note",
    )


def append_v2_shared_message_for_verified_owner(
    owner_context: object,
    headers: object,
    collaboration_id: object,
    payload: object,
    *,
    idempotency_key: object,
    owner_security_configuration: object,
) -> dict[str, Any]:
    return _append_v2_owner_message(
        headers,
        collaboration_id,
        payload,
        required_action="reply",
        owner_context=owner_context,
        owner_security_configuration=owner_security_configuration,
        idempotency_key=idempotency_key,
    )


def append_v2_internal_note_for_verified_owner(
    owner_context: object,
    headers: object,
    collaboration_id: object,
    payload: object,
    *,
    idempotency_key: object,
    owner_security_configuration: object,
) -> dict[str, Any]:
    return _append_v2_owner_message(
        headers,
        collaboration_id,
        payload,
        required_action="internal_note",
        owner_context=owner_context,
        owner_security_configuration=owner_security_configuration,
        idempotency_key=idempotency_key,
    )


def _create_v2_collaboration_for_owner(
    headers: object,
    payload: object,
    *,
    owner_context: object | None = None,
    owner_security_configuration: object | None = None,
) -> dict[str, Any]:
    expected_fields = (
        {"mailboxId", "sourceRef", "state"}
        if owner_context is None
        else {"mailboxId", "sourceRef", "state", "participantUserId"}
    )
    if (
        type(payload) is not dict
        or set(payload) != expected_fields
        or type(payload.get("state")) is not str
        or payload.get("state") not in _ALLOWED_INITIAL_STATES
    ):
        return _failure("malformed", "invalid_request")

    mailbox_id = payload.get("mailboxId")
    if owner_context is None:
        authorized = resolve_internal_collaboration_context(
            headers,
            mailbox_id,
            required_action="create",
        )
    else:
        authorized = resolve_verified_owner_collaboration_context(
            owner_context,
            headers,
            mailbox_id,
            required_action="create",
            owner_security_configuration=owner_security_configuration,
        )
    if type(authorized) is not dict or authorized.get("status") != "ok":
        return _failure_from_result(
            authorized,
            default_status="error",
            default_code="storage_protocol_error",
        )
    if set(authorized) != {"status", "context", "error"} or authorized.get("error") is not None:
        return _failure("malformed", "storage_protocol_error")

    capability = authorized.get("context")
    if (
        not _is_internal_capability(capability, actions=frozenset({"create"}))
        or capability.collaboration_id is not None
        or capability.mailbox_id != mailbox_id
    ):
        return _failure("forbidden", "forbidden")

    participant_authority = None
    if owner_context is not None:
        if (
            normalize_v2_user_id(capability.actor_user_id)
            != capability.actor_user_id
            or payload.get("participantUserId") == capability.actor_user_id
        ):
            return _failure("malformed", "invalid_request")
        participant_authority, participant_error = _resolve_participant_authority(
            capability.workspace_id,
            payload.get("participantUserId"),
        )
        if participant_error is not None:
            return participant_error

    def reuse_authorized_context(
        received_headers: object,
        received_mailbox_id: object,
        *,
        required_action: str,
    ) -> dict[str, Any]:
        if (
            received_headers is not headers
            or received_mailbox_id != capability.mailbox_id
            or required_action != "create"
        ):
            return {
                "status": "forbidden",
                "context": None,
                "error": {"code": "forbidden"},
            }
        return authorized

    resolved = resolve_source_message(
        headers,
        {
            "mailboxId": capability.mailbox_id,
            "sourceRef": payload.get("sourceRef"),
        },
        authorization_resolver=reuse_authorized_context,
    )
    if type(resolved) is not dict or resolved.get("status") != "ok":
        return _failure_from_result(
            resolved,
            default_status="error",
            default_code="storage_protocol_error",
        )
    if set(resolved) != {"status", "source", "error"} or resolved.get("error") is not None:
        return _failure("malformed", "storage_protocol_error")

    source = resolved.get("source")
    locator = payload.get("sourceRef")
    if type(source) is not dict or set(source) != {"sourceRef", "sourceMessage"}:
        return _failure("malformed", "storage_protocol_error")
    if type(locator) is not dict:
        return _failure("malformed", "storage_protocol_error")
    expected_source_ref = normalize_v2_source_ref(
        {"provider": capability.mailbox_provider, **locator}
    )
    canonical_source_ref = normalize_v2_source_ref(source.get("sourceRef"))
    if (
        expected_source_ref is None
        or canonical_source_ref is None
        or source.get("sourceRef") != canonical_source_ref
        or canonical_source_ref != expected_source_ref
    ):
        return _failure("malformed", "storage_protocol_error")

    created_at = time.time_ns() // 1_000_000
    if (
        type(created_at) is not int
        or created_at < MIN_V2_TIMESTAMP_MILLISECONDS
        or created_at > MAX_V2_TIMESTAMP_MILLISECONDS
    ):
        return _failure("error", "invalid_request")

    collaboration_id = generate_v2_opaque_id()
    proposed_record = {
            "v": COLLABORATION_V2_THREAD_SCHEMA_VERSION,
            "collaborationId": collaboration_id,
            "ownerEmail": capability.owner_email,
            "workspaceId": capability.workspace_id,
            "mailboxId": capability.mailbox_id,
            "sourceRef": canonical_source_ref,
            "sourceMessage": source.get("sourceMessage"),
            "state": payload["state"],
            "messages": [],
            "createdAt": created_at,
            "updatedAt": created_at,
        }
    if participant_authority is not None:
        proposed_record.update(
            {
                "ownerUserId": capability.actor_user_id,
                "ownerDisplayName": capability.actor_display_name,
                "participants": [participant_authority],
            }
        )
    proposed = normalize_v2_thread_record(proposed_record)
    if proposed is None:
        return _failure("malformed", "storage_protocol_error")

    created = _create_v2_thread(proposed)
    if type(created) is not _V2RecordResult:
        return _create_storage_failure(created)
    if created.status != "ok" or type(created.created) is not bool:
        return _failure("malformed", "storage_protocol_error")

    returned = normalize_v2_thread_record(created.record)
    if returned is None:
        return _failure("malformed", "storage_protocol_error")
    if not _thread_matches_create_binding(
        returned,
        capability,
        canonical_source_ref,
    ):
        return _failure("forbidden", "forbidden")

    if created.created:
        if returned != proposed or returned["collaborationId"] != collaboration_id:
            return _failure("malformed", "storage_protocol_error")
        collaboration = returned
    else:
        collaboration, load_failure = _load_exact_thread(
            returned["collaborationId"]
        )
        if load_failure is not None:
            return load_failure
        if (
            collaboration is None
            or collaboration["collaborationId"] != returned["collaborationId"]
            or not _thread_matches_create_binding(
                collaboration,
                capability,
                canonical_source_ref,
            )
        ):
            return _failure("forbidden", "forbidden")

    if owner_context is not None:
        dto, dto_error = _build_verified_owner_thread_dto(
            collaboration, capability
        )
        if dto_error is not None:
            return dto_error
        if dto is None:
            return _failure("malformed", "storage_protocol_error")
        collaboration_dto = dto
    else:
        collaboration_dto = _build_owner_thread_dto(collaboration)

    return {
        "created": created.created,
        "collaboration": collaboration_dto,
    }


def create_v2_collaboration_for_owner(
    headers: object,
    payload: object,
) -> dict[str, Any]:
    return _create_v2_collaboration_for_owner(headers, payload)


def create_v2_collaboration_for_verified_owner(
    owner_context: object,
    headers: object,
    payload: object,
    *,
    owner_security_configuration: object,
) -> dict[str, Any]:
    return _create_v2_collaboration_for_owner(
        headers,
        payload,
        owner_context=owner_context,
        owner_security_configuration=owner_security_configuration,
    )


def create_v2_collaboration_with_guest_for_verified_owner(
    owner_context: object,
    headers: object,
    payload: object,
    *,
    owner_security_configuration: object,
) -> dict[str, Any]:
    base_fields = {"mailboxId", "sourceRef", "state"}
    if (
        type(payload) is not dict
        or frozenset(payload) not in {frozenset(base_fields), frozenset(base_fields | {"invitedEmail"})}
        or type(payload.get("state")) is not str
        or payload.get("state") not in _ALLOWED_INITIAL_STATES
    ):
        return _failure("malformed", "invalid_request")
    invited_email = (
        normalize_v2_email(payload.get("invitedEmail"))
        if "invitedEmail" in payload
        else None
    )
    if "invitedEmail" in payload and invited_email is None:
        return _failure("malformed", "invalid_request")

    mailbox_id = payload.get("mailboxId")
    authorized = resolve_verified_owner_collaboration_context(
        owner_context,
        headers,
        mailbox_id,
        required_action="create",
        owner_security_configuration=owner_security_configuration,
    )
    if type(authorized) is not dict or authorized.get("status") != "ok":
        return _failure_from_result(
            authorized,
            default_status="error",
            default_code="storage_protocol_error",
        )
    if set(authorized) != {"status", "context", "error"} or authorized.get("error") is not None:
        return _failure("malformed", "storage_protocol_error")
    capability = authorized.get("context")
    if (
        not _is_internal_capability(capability, actions={"create"})
        or capability.viewer_access != "owner"
        or capability.collaboration_id is not None
        or capability.mailbox_id != mailbox_id
        or normalize_v2_email(capability.owner_email) != capability.owner_email
    ):
        return _failure("forbidden", "forbidden")

    def reuse_authorized_context(
        received_headers: object,
        received_mailbox_id: object,
        *,
        required_action: str,
    ) -> dict[str, Any]:
        if (
            received_headers is not headers
            or received_mailbox_id != capability.mailbox_id
            or required_action != "create"
        ):
            return {
                "status": "forbidden",
                "context": None,
                "error": {"code": "forbidden"},
            }
        return authorized

    resolved = resolve_source_message(
        headers,
        {"mailboxId": capability.mailbox_id, "sourceRef": payload.get("sourceRef")},
        authorization_resolver=reuse_authorized_context,
    )
    if type(resolved) is not dict or resolved.get("status") != "ok":
        return _failure_from_result(
            resolved,
            default_status="error",
            default_code="storage_protocol_error",
        )
    if set(resolved) != {"status", "source", "error"} or resolved.get("error") is not None:
        return _failure("malformed", "storage_protocol_error")
    source = resolved.get("source")
    locator = payload.get("sourceRef")
    if type(source) is not dict or set(source) != {"sourceRef", "sourceMessage"} or type(locator) is not dict:
        return _failure("malformed", "storage_protocol_error")
    expected_source_ref = normalize_v2_source_ref(
        {"provider": capability.mailbox_provider, **locator}
    )
    canonical_source_ref = normalize_v2_source_ref(source.get("sourceRef"))
    if (
        expected_source_ref is None
        or canonical_source_ref is None
        or source.get("sourceRef") != canonical_source_ref
        or canonical_source_ref != expected_source_ref
    ):
        return _failure("malformed", "storage_protocol_error")

    created_at = time.time_ns() // 1_000_000
    invitation_created_at = created_at // 1_000
    if (
        not MIN_V2_TIMESTAMP_MILLISECONDS <= created_at <= MAX_V2_TIMESTAMP_MILLISECONDS
        or not MIN_V2_TIMESTAMP_SECONDS <= invitation_created_at <= MAX_V2_TIMESTAMP_SECONDS
        or invitation_created_at + INVITE_LIFETIME_SECONDS > MAX_V2_TIMESTAMP_SECONDS
    ):
        return _failure("error", "invalid_request")
    collaboration_id = generate_v2_opaque_id()
    proposed = normalize_v2_thread_record(
        {
            "v": COLLABORATION_V2_THREAD_SCHEMA_VERSION,
            "collaborationId": collaboration_id,
            "ownerEmail": capability.owner_email,
            "workspaceId": capability.workspace_id,
            "mailboxId": capability.mailbox_id,
            "sourceRef": canonical_source_ref,
            "sourceMessage": source.get("sourceMessage"),
            "state": payload["state"],
            "messages": [],
            "createdAt": created_at,
            "updatedAt": created_at,
        }
    )
    raw_token = generate_v2_bearer_secret()
    token_hash = hash_v2_secret(raw_token)
    proposed_invite_record = {
        "v": 2,
        "inviteId": generate_v2_opaque_id(),
        "tokenHash": token_hash,
        "ownerEmail": capability.owner_email,
        "workspaceId": capability.workspace_id,
        "mailboxId": capability.mailbox_id,
        "collaborationId": collaboration_id,
        "identityAssurance": "link_possession",
        "allowedActions": ["read", "reply"],
        "visibility": "shared_only",
        "createdBy": {
            "ownerEmail": capability.owner_email,
            "displayName": capability.actor_display_name,
        },
        "createdAt": invitation_created_at,
        "expiresAt": invitation_created_at + INVITE_LIFETIME_SECONDS,
        "status": "active",
        "exchangedAt": None,
        "exchangeCount": 0,
        "revokedAt": None,
        "revokedBy": None,
    }
    if invited_email is not None:
        proposed_invite_record["invitedEmail"] = invited_email
    proposed_invite = normalize_v2_invite_record(proposed_invite_record)
    if proposed is None or proposed_invite is None or token_hash is None:
        return _failure("malformed", "storage_protocol_error")

    stored = _create_v2_thread_with_guest(
        proposed,
        proposed_invite,
        now=invitation_created_at,
    )
    if type(stored) is not _V2ThreadInviteCreateResult:
        return _create_storage_failure(stored)
    if stored.status != "ok" or type(stored.thread_created) is not bool or type(stored.invite_created) is not bool:
        return _failure("malformed", "storage_protocol_error")
    thread = normalize_v2_thread_record(stored.thread)
    invitation = normalize_v2_invite_record(stored.invite)
    if (
        thread is None
        or invitation is None
        or not _thread_matches_create_binding(thread, capability, canonical_source_ref)
        or invitation["ownerEmail"] != thread["ownerEmail"]
        or invitation["workspaceId"] != thread["workspaceId"]
        or invitation["mailboxId"] != thread["mailboxId"]
        or invitation["collaborationId"] != thread["collaborationId"]
        or invitation.get("invitedEmail") != invited_email
    ):
        return _failure("malformed", "storage_protocol_error")
    if stored.thread_created and (thread != proposed or thread["collaborationId"] != collaboration_id):
        return _failure("malformed", "storage_protocol_error")
    if stored.invite_created and invitation["tokenHash"] != token_hash:
        return _failure("malformed", "storage_protocol_error")

    collaboration_dto, dto_error = _build_verified_owner_thread_dto(
        thread, capability
    )
    if dto_error is not None:
        return dto_error
    if collaboration_dto is None:
        return _failure("malformed", "storage_protocol_error")

    result = {
        "created": stored.thread_created,
        "invitationCreated": stored.invite_created,
        "collaboration": collaboration_dto,
        "invitation": _safe_v2_invitation_metadata(invitation),
    }
    if stored.invite_created:
        result["token"] = raw_token
    return result


def add_v2_participant_for_verified_owner(
    owner_context: object,
    headers: object,
    collaboration_id: object,
    payload: object,
    *,
    owner_security_configuration: object,
) -> dict[str, Any]:
    if type(payload) is not dict or set(payload) != {"participantUserId"}:
        return _failure("malformed", "invalid_request")
    authorized = resolve_verified_owner_collaboration_context(
        owner_context,
        headers,
        collaboration_id=collaboration_id,
        required_action="manage_participants",
        owner_security_configuration=owner_security_configuration,
    )
    if type(authorized) is not dict or authorized.get("status") != "ok":
        return _failure_from_result(
            authorized,
            default_status="error",
            default_code="storage_protocol_error",
        )
    capability = authorized.get("context")
    if (
        not _is_internal_capability(capability, actions={"manage_participants"})
        or capability.viewer_access != "owner"
        or capability.collaboration_id != collaboration_id
        or normalize_v2_user_id(capability.actor_user_id)
        != capability.actor_user_id
        or payload.get("participantUserId") == capability.actor_user_id
    ):
        return _failure("forbidden", "forbidden")
    participant, participant_error = _resolve_participant_authority(
        capability.workspace_id,
        payload.get("participantUserId"),
    )
    if participant_error is not None:
        return participant_error
    if participant is None:
        return _failure("malformed", "storage_protocol_error")
    from .mutations import add_v2_participant

    mutated = add_v2_participant(capability, participant)
    if type(mutated) is not dict or mutated.get("status") != "ok":
        return _failure_from_result(
            mutated,
            default_status="error",
            default_code="storage_protocol_error",
        )
    thread = normalize_v2_thread_record(mutated.get("record"))
    if thread is None:
        return _failure("malformed", "storage_protocol_error")
    dto, dto_error = _build_verified_owner_thread_dto(thread, capability)
    if dto_error is not None:
        return dto_error
    return _success(dto) if dto is not None else _failure("malformed", "storage_protocol_error")


def lookup_v2_collaboration_for_verified_owner(
    owner_context: object,
    headers: object,
    mailbox_id: object,
    source_locator: object,
    *,
    owner_security_configuration: object,
) -> dict[str, Any]:
    authorized = resolve_verified_owner_collaboration_context(
        owner_context,
        headers,
        mailbox_id,
        required_action="read",
        owner_security_configuration=owner_security_configuration,
    )
    if type(authorized) is not dict or authorized.get("status") != "ok":
        return _failure_from_result(
            authorized,
            default_status="error",
            default_code="storage_protocol_error",
        )
    if (
        set(authorized) != {"status", "context", "error"}
        or authorized.get("error") is not None
    ):
        return _failure("malformed", "storage_protocol_error")

    capability = authorized.get("context")
    if (
        not _is_internal_capability(capability, actions=frozenset({"read"}))
        or capability.collaboration_id is not None
        or capability.mailbox_id != mailbox_id
    ):
        return _failure("forbidden", "forbidden")

    locator_fields = {
        "google": {"providerMessageId"},
        "custom_imap": {"folder", "uidValidity", "imapUid"},
    }.get(capability.mailbox_provider)
    if (
        type(source_locator) is not dict
        or locator_fields is None
        or set(source_locator) != locator_fields
    ):
        return _failure("malformed", "invalid_request")

    source_ref = normalize_v2_source_ref(
        {"provider": capability.mailbox_provider, **source_locator}
    )
    if (
        source_ref is None
        or {
            field: source_ref[field]
            for field in locator_fields
        }
        != source_locator
    ):
        return _failure("malformed", "invalid_request")

    loaded = _load_v2_thread_by_source(
        capability.owner_email,
        capability.mailbox_id,
        source_ref,
        workspace_id=capability.workspace_id,
    )
    if type(loaded) is not _V2RecordResult:
        if type(loaded) is dict:
            status = loaded.get("status")
            error = loaded.get("error")
            code = error.get("code") if type(error) is dict else None
            if status == "missing" and code == "collaboration_not_found":
                return _failure("not_found", "collaboration_not_found")
            if status == "unavailable" and code in {
                "index_hmac_unavailable",
                "storage_protocol_error",
                "storage_unavailable",
            }:
                return _failure("unavailable", code)
        return _failure("unavailable", "storage_protocol_error")
    if loaded.status != "ok" or loaded.created is not None:
        return _failure("unavailable", "storage_protocol_error")

    thread = normalize_v2_thread_record(loaded.record)
    if thread is None:
        return _failure("unavailable", "storage_protocol_error")
    if not _thread_matches_create_binding(thread, capability, source_ref):
        return _failure("forbidden", "forbidden")
    collaboration_id = thread["collaborationId"]
    if not is_v2_opaque_id(collaboration_id):
        return _failure("unavailable", "storage_protocol_error")
    return {
        "status": "ok",
        "collaborationId": collaboration_id,
        "error": None,
    }


def _read_v2_collaboration_for_owner(
    headers: object,
    collaboration_id: object,
    *,
    owner_context: object | None = None,
    owner_security_configuration: object | None = None,
) -> dict[str, Any]:
    if owner_context is None:
        authorized = resolve_internal_collaboration_context(
            headers,
            collaboration_id=collaboration_id,
            required_action="read",
        )
    else:
        authorized = resolve_verified_owner_collaboration_context(
            owner_context,
            headers,
            collaboration_id=collaboration_id,
            required_action="read",
            owner_security_configuration=owner_security_configuration,
        )

    if type(authorized) is not dict or authorized.get("status") != "ok":
        return _failure_from_result(
            authorized,
            default_status="error",
            default_code="storage_protocol_error",
        )

    capability = authorized.get("context")
    if not _is_internal_capability(capability, actions=frozenset({"read"})):
        return _failure("forbidden", "forbidden")
    if capability.collaboration_id != collaboration_id:
        return _failure("forbidden", "forbidden")

    thread, load_failure = _load_exact_thread(capability.collaboration_id)
    if load_failure is not None:
        return load_failure
    if thread is None or not _thread_matches_owner_capability(thread, capability):
        return _failure("forbidden", "forbidden")

    if owner_context is None:
        return _success(_build_owner_thread_dto(thread))
    if capability.viewer_access == "owner":
        dto, dto_error = _build_verified_owner_thread_dto(thread, capability)
    else:
        dto, dto_error = _build_verified_thread_dto(thread, capability)
    if dto_error is not None:
        return dto_error
    return _success(dto) if dto is not None else _failure("malformed", "storage_protocol_error")


def read_v2_collaboration_for_owner(
    headers: object,
    collaboration_id: object,
) -> dict[str, Any]:
    return _read_v2_collaboration_for_owner(headers, collaboration_id)


def read_v2_collaboration_for_verified_owner(
    owner_context: object,
    headers: object,
    collaboration_id: object,
    *,
    owner_security_configuration: object,
) -> dict[str, Any]:
    return _read_v2_collaboration_for_owner(
        headers,
        collaboration_id,
        owner_context=owner_context,
        owner_security_configuration=owner_security_configuration,
    )


def read_v2_collaboration_for_guest(
    raw_headers: object,
) -> dict[str, Any]:
    current_time = int(time.time())
    if (
        type(current_time) is not int
        or current_time < MIN_V2_TIMESTAMP_SECONDS
        or current_time > MAX_V2_TIMESTAMP_SECONDS
    ):
        return _failure("error", "invalid_request")

    raw_session_id = read_guest_session_cookie(raw_headers)
    if raw_session_id is None:
        return _failure("missing", "session_not_found")

    resolved = _resolve_guest_read_access(
        raw_session_id,
        now=current_time,
    )

    if type(resolved) is not tuple or len(resolved) != 3:
        return _failure("malformed", "storage_protocol_error")
    capability, session, access_error = resolved
    if access_error is not None:
        return _failure_from_result(
            access_error,
            default_status="error",
            default_code="storage_protocol_error",
        )
    if not _is_guest_read_capability(capability):
        return _failure("revoked", "session_revoked")
    if not _guest_session_matches_capability(session, capability):
        return _failure("revoked", "session_revoked")

    thread, load_failure = _load_exact_thread(capability.collaboration_id)
    if load_failure is not None:
        return load_failure
    if thread is None or not _thread_matches_guest_capability(thread, capability):
        return _failure("forbidden", "forbidden")

    dto = build_v2_guest_thread_dto(thread)
    if type(dto) is not dict:
        return _failure("malformed", "storage_protocol_error")
    return _success(dto)


__all__ = [
    "append_v2_internal_note_for_owner",
    "append_v2_shared_message_for_owner",
    "create_v2_collaboration_for_owner",
    "create_v2_collaboration_with_guest_for_verified_owner",
    "read_v2_collaboration_for_guest",
    "read_v2_collaboration_for_owner",
]
