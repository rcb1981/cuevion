from __future__ import annotations

if __name__ != "api.collaboration.mutations":
    raise ImportError(
        "Collaboration helpers must be imported as "
        "api.collaboration.mutations"
    )

import time

from .models import (
    build_v2_guest_shared_reply,
    build_v2_internal_internal_message,
    build_v2_internal_shared_message,
    build_v2_owner_internal_message,
    build_v2_owner_shared_message,
    _build_v2_context_message,
    MAX_V2_SAFE_INTEGER,
    normalize_v2_thread_record,
)
from .authorization import _is_internal_capability
from .guest_session import _is_guest_mutation_capability
from .redis_store import (
    _V2RecordResult,
    _append_v2_guest_reply_if_expected,
    _load_v2_thread,
    _save_v2_thread_if_expected,
)


def _failure(code: str) -> dict:
    return {"status": "error", "error": {"code": code}}


_CANONICAL_MUTATION_STORAGE_ERRORS = {
    ("conflict", "stale_thread"): "stale_thread",
    ("expired", "session_expired"): "session_expired",
    ("forbidden", "forbidden"): "forbidden",
    ("malformed", "storage_protocol_error"): "storage_protocol_error",
    ("missing", "collaboration_not_found"): "collaboration_not_found",
    ("revoked", "session_expired"): "session_expired",
    ("revoked", "session_revoked"): "session_revoked",
    ("unavailable", "storage_protocol_error"): "storage_protocol_error",
    ("unavailable", "storage_unavailable"): "storage_unavailable",
}


def _canonical_storage_error(value: object) -> str:
    if type(value) is not dict or set(value) != {"status", "error"}:
        return "storage_protocol_error"
    status = value.get("status")
    error = value.get("error")
    code = error.get("code") if type(error) is dict else None
    if (
        type(status) is not str
        or type(error) is not dict
        or set(error) != {"code"}
        or type(code) is not str
        or (status, code) not in _CANONICAL_MUTATION_STORAGE_ERRORS
    ):
        return "storage_protocol_error"
    return _CANONICAL_MUTATION_STORAGE_ERRORS[(status, code)]


def _load_scoped_thread(capability: object, *, thread_loader, command_transport=None) -> tuple[dict | None, dict | None]:
    if not (_is_internal_capability(capability) or _is_guest_mutation_capability(capability)):
        return None, _failure("invalid_request")
    collaboration_id = capability.collaboration_id
    if not isinstance(collaboration_id, str):
        return None, _failure("invalid_request")
    loaded = thread_loader(
        collaboration_id,
        command_transport=command_transport,
    )
    if type(loaded) is _V2RecordResult:
        if type(loaded.status) is not str or loaded.status != "ok":
            return None, _failure("storage_protocol_error")
        record = loaded.record
    elif type(loaded) is dict:
        if set(loaded) == {"status"} and loaded.get("status") == "missing":
            return None, _failure("collaboration_not_found")
        if set(loaded) == {"status"} and loaded.get("status") == "malformed":
            return None, _failure("storage_protocol_error")
        if type(loaded.get("status")) is not str or loaded.get("status") != "ok":
            return None, _failure(_canonical_storage_error(loaded))
        if set(loaded) != {"status", "record"}:
            return None, _failure("storage_protocol_error")
        record = loaded.get("record")
    else:
        return None, _failure("storage_protocol_error")

    thread = normalize_v2_thread_record(record)
    if thread is None:
        return None, _failure("storage_protocol_error")
    if (
        thread["collaborationId"] != collaboration_id
        or thread["ownerEmail"] != capability.owner_email
        or thread["workspaceId"] != capability.workspace_id
        or thread["mailboxId"] != capability.mailbox_id
    ):
        return None, _failure("forbidden")
    return thread, None


def _append_message(
    capability: object,
    text: object,
    *,
    builder,
    thread_loader,
    thread_saver,
    command_transport=None,
    saver_kwargs: dict | None = None,
    allow_simple_saver: bool = True,
) -> dict:
    thread, error = _load_scoped_thread(
        capability, thread_loader=thread_loader, command_transport=command_transport
    )
    if error:
        return error
    expected = thread["updatedAt"]
    now = max(time.time_ns() // 1_000_000, expected + 1)
    if now > MAX_V2_SAFE_INTEGER:
        return _failure("invalid_request")
    message = builder(capability, text, now)
    if message is None:
        return _failure("invalid_request")
    replacement = normalize_v2_thread_record(
        {**thread, "messages": [*thread["messages"], message], "updatedAt": now}
    )
    if replacement is None:
        return _failure("invalid_request")
    if allow_simple_saver:
        saved = thread_saver(
            replacement,
            expected,
            command_transport=command_transport,
            **(saver_kwargs or {}),
        )
    else:
        try:
            saved = thread_saver(
                replacement,
                expected,
                command_transport=command_transport,
                **(saver_kwargs or {}),
            )
        except Exception:
            return _failure("storage_unavailable")

    if type(saved) is _V2RecordResult:
        if type(saved.status) is not str or saved.status != "ok":
            return _failure("storage_protocol_error")
        saved_record = saved.record
    elif type(saved) is dict:
        if type(saved.get("status")) is not str or saved.get("status") != "ok":
            return _failure(_canonical_storage_error(saved))
        if set(saved) != {"status", "record"}:
            return _failure("storage_protocol_error")
        saved_record = saved.get("record")
    else:
        return _failure("storage_protocol_error")

    saved_thread = normalize_v2_thread_record(saved_record)
    if saved_thread != replacement:
        return _failure("storage_protocol_error")
    return {
        "status": "ok",
        "message": {
            "id": message["id"],
            "authorDisplayName": message["authorDisplayName"],
            "authorRole": "Guest reviewer" if message["authorKind"] == "guest" else "Cuevion user",
            "text": message["text"],
            "timestamp": message["createdAt"],
            "visibility": message["visibility"],
        },
        "updatedAt": now,
        "error": None,
    }


def append_internal_v2_message(
    context: object,
    text: object,
    *,
    visibility: str,
    thread_loader=_load_v2_thread,
    thread_saver=_save_v2_thread_if_expected,
    command_transport=None,
) -> dict:
    if not _is_internal_capability(context, actions={"reply", "internal_note"}):
        return _failure("forbidden")
    if (context.actor_kind, visibility) not in {
        ("owner", "shared"), ("owner", "internal"),
        ("internal", "shared"), ("internal", "internal"),
    }:
        return _failure("forbidden")

    def builder(capability, raw_text, created_at):
        return _build_v2_context_message(
            capability,
            raw_text,
            author_kind=context.actor_kind,
            visibility=visibility,
            created_at=created_at,
        )

    return _append_message(
        context, text, builder=builder, thread_loader=thread_loader,
        thread_saver=thread_saver, command_transport=command_transport,
    )


def append_guest_v2_reply(
    session_context: object,
    text: object,
    *,
    thread_loader=_load_v2_thread,
    thread_saver=_append_v2_guest_reply_if_expected,
    command_transport=None,
) -> dict:
    if not _is_guest_mutation_capability(session_context):
        return _failure("session_revoked")

    commit_time = int(time.time())
    if commit_time < session_context.created_at or commit_time < session_context.last_used_at:
        return _failure("invalid_request")
    if commit_time >= session_context.expires_at:
        return _failure("session_expired")

    def builder(capability, raw_text, created_at):
        return build_v2_guest_shared_reply(
            capability, raw_text, _created_at=created_at
        )

    return _append_message(
        session_context, text, builder=builder, thread_loader=thread_loader,
        thread_saver=thread_saver, command_transport=command_transport,
        saver_kwargs={"session_context": session_context, "now": commit_time},
        allow_simple_saver=False,
    )
