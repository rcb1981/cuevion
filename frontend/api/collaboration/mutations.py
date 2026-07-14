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
    _append_v2_guest_reply_if_expected,
    _load_v2_thread,
    _save_v2_thread_if_expected,
)


def _failure(code: str) -> dict:
    return {"status": "error", "error": {"code": code}}


def _load_scoped_thread(capability: object, *, thread_loader, command_transport=None) -> tuple[dict | None, dict | None]:
    if not (_is_internal_capability(capability) or _is_guest_mutation_capability(capability)):
        return None, _failure("invalid_request")
    collaboration_id = capability.collaboration_id
    if not isinstance(collaboration_id, str):
        return None, _failure("invalid_request")
    try:
        loaded = thread_loader(collaboration_id, command_transport=command_transport)
    except TypeError:
        loaded = thread_loader(collaboration_id)
    except Exception:
        return None, _failure("storage_unavailable")
    if not hasattr(loaded, "get") or loaded.get("status") != "ok":
        code = "collaboration_not_found" if hasattr(loaded, "get") and loaded.get("status") == "missing" else "storage_protocol_error"
        return None, _failure(code)
    thread = normalize_v2_thread_record(loaded.get("record"))
    if (
        thread is None
        or thread["collaborationId"] != collaboration_id
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
    try:
        saved = thread_saver(
            replacement,
            expected,
            command_transport=command_transport,
            **(saver_kwargs or {}),
        )
    except TypeError:
        if not allow_simple_saver:
            return _failure("storage_unavailable")
        saved = thread_saver(replacement, expected)
    except Exception:
        return _failure("storage_unavailable")
    if not hasattr(saved, "get") or saved.get("status") != "ok":
        code = (saved.get("error") or {}).get("code") if hasattr(saved, "get") else None
        return _failure(
            code
            if code in {
                "stale_thread", "session_revoked", "session_expired", "forbidden",
                "storage_unavailable", "storage_protocol_error",
            }
            else "storage_protocol_error"
        )
    saved_thread = normalize_v2_thread_record(saved.get("record"))
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
