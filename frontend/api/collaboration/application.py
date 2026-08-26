from __future__ import annotations

if __name__ != "api.collaboration.application":
    raise ImportError(
        "api.collaboration.application must be imported by its canonical package path"
    )

import time
from typing import Any

from .authorization import (
    _is_internal_capability,
    resolve_internal_collaboration_context,
    resolve_verified_owner_collaboration_context,
)
from .guest_session import (
    _is_guest_read_capability,
    _resolve_guest_read_access,
    read_guest_session_cookie,
)
from .models import (
    COLLABORATION_V2_THREAD_SCHEMA_VERSION,
    COLLABORATION_V2_SAFE_ERROR_CODES,
    MAX_V2_MESSAGE_TEXT,
    MAX_V2_TIMESTAMP_MILLISECONDS,
    MAX_V2_TIMESTAMP_SECONDS,
    MIN_V2_TIMESTAMP_MILLISECONDS,
    MIN_V2_TIMESTAMP_SECONDS,
    _v2_free_text,
    build_v2_guest_thread_dto,
    generate_v2_opaque_id,
    is_v2_opaque_id,
    normalize_v2_source_ref,
    normalize_v2_thread_record,
)
from .redis_store import (
    _V2RecordResult,
    _create_v2_thread,
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
    if (
        type(payload) is not dict
        or set(payload) != {"mailboxId", "sourceRef", "state"}
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

    return {
        "created": created.created,
        "collaboration": _build_owner_thread_dto(collaboration),
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

    return _success(_build_owner_thread_dto(thread))


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
    "read_v2_collaboration_for_guest",
    "read_v2_collaboration_for_owner",
]
