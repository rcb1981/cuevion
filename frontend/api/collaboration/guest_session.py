from __future__ import annotations

if __name__ != "api.collaboration.guest_session":
    raise ImportError(
        "Collaboration helpers must be imported as "
        "api.collaboration.guest_session"
    )

import hmac
import os
import re
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from .authorization import _is_internal_capability
from .models import (
    generate_v2_bearer_secret,
    generate_v2_opaque_id,
    hash_v2_secret,
    normalize_v2_email,
    normalize_v2_thread_record,
    MAX_V2_GUEST_SESSION_LIFETIME_SECONDS,
    MIN_V2_TIMESTAMP_SECONDS,
    MAX_V2_TIMESTAMP_SECONDS,
)
from .redis_store import (
    _atomic_exchange_v2_invite,
    _create_v2_invite,
    _load_v2_guest_session_record,
    _load_v2_invite_by_id,
    _load_v2_invite_by_token,
    _load_v2_thread,
    _revoke_v2_guest_session,
    _revoke_v2_invite,
)

INVITE_LIFETIME_SECONDS = 24 * 60 * 60
GUEST_SESSION_LIFETIME_SECONDS = 8 * 60 * 60
GUEST_SESSION_COOKIE_NAME = "cuevion_collab_guest_session"
GUEST_SESSION_COOKIE_PATH = "/api/collaboration/guest"
CSRF_HEADER_NAME = "X-Cuevion-CSRF"
MAX_COOKIE_HEADER_BYTES = 8192
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_BEARER_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_MAILBOX_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,255}$")
_SAFE_GUEST_ERROR_CODES = {
    "invite_not_found", "invite_expired", "invite_revoked",
    "invite_already_exchanged", "session_not_found", "session_expired",
    "session_revoked", "csrf_failed", "origin_rejected", "invalid_request",
    "storage_unavailable", "storage_protocol_error", "atomic_exchange_unavailable",
    "already_logged_out", "already_revoked",
    "stale_invitation",
}

_GUEST_READ_SENTINEL = object()
_GUEST_MUTATION_SENTINEL = object()


@dataclass(frozen=True, slots=True)
class _GuestReadCapability:
    _sentinel: object
    session_hash: str
    invite_id: str
    owner_email: str
    workspace_id: str
    mailbox_id: str
    collaboration_id: str
    guest_display_name: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class _GuestMutationCapability:
    _sentinel: object
    session_hash: str
    invite_id: str
    owner_email: str
    workspace_id: str
    mailbox_id: str
    collaboration_id: str
    guest_display_name: str
    expires_at: int
    created_at: int
    last_used_at: int


def _is_guest_read_capability(value: object) -> bool:
    return type(value) is _GuestReadCapability and value._sentinel is _GUEST_READ_SENTINEL


def _is_guest_mutation_capability(value: object) -> bool:
    return (
        type(value) is _GuestMutationCapability
        and value._sentinel is _GUEST_MUTATION_SENTINEL
        and type(value.created_at) is int
        and type(value.last_used_at) is int
        and type(value.expires_at) is int
        and value.created_at <= value.last_used_at < value.expires_at
    )


def _failure(status: str, code: str) -> dict:
    return {"status": status, "error": {"code": code}}


def _guest_failure(value: object, *, default_code: str = "storage_protocol_error") -> dict:
    code = None
    if isinstance(value, dict) and isinstance(value.get("error"), dict):
        code = value["error"].get("code")
    safe_code = code if code in _SAFE_GUEST_ERROR_CODES else default_code
    return {"status": "error", "error": {"code": safe_code}}


def _invite_response(invite: dict) -> dict:
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


def _bounded_string(value, max_length: int, *, allow_empty: bool = False) -> str | None:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    ):
        return None
    if (not value and not allow_empty) or len(value.encode("utf-8")) > max_length:
        return None
    return value


def normalize_v2_guest_session_record(value: object) -> dict | None:
    required = {
        "v", "sessionHash", "inviteId", "ownerEmail", "workspaceId",
        "mailboxId", "collaborationId", "allowedActions", "visibility", "identityAssurance",
        "guestDisplayName", "createdAt", "lastUsedAt", "expiresAt", "status",
        "csrfTokenHash", "revokedAt", "loggedOutAt",
    }
    if not isinstance(value, dict) or set(value) != required:
        return None
    session_hash = value.get("sessionHash")
    csrf_hash = value.get("csrfTokenHash")
    owner_email = normalize_v2_email(value.get("ownerEmail"))
    workspace_id = normalize_v2_email(value.get("workspaceId"))
    guest_display_name = _bounded_string(value.get("guestDisplayName"), 256)
    timestamps: dict[str, int | None] = {}
    for key in ("createdAt", "lastUsedAt", "expiresAt"):
        entry = value.get(key)
        timestamps[key] = entry if type(entry) is int and MIN_V2_TIMESTAMP_SECONDS <= entry <= MAX_V2_TIMESTAMP_SECONDS else None
    revoked_at = value.get("revokedAt")
    if revoked_at is not None and (
        type(revoked_at) is not int or not MIN_V2_TIMESTAMP_SECONDS <= revoked_at <= MAX_V2_TIMESTAMP_SECONDS
    ):
        return None
    logged_out_at = value.get("loggedOutAt")
    if logged_out_at is not None and (
        type(logged_out_at) is not int or not MIN_V2_TIMESTAMP_SECONDS <= logged_out_at <= MAX_V2_TIMESTAMP_SECONDS
    ):
        return None
    status = _bounded_string(value.get("status"), 16)
    if (
        type(value.get("v")) is not int
        or value.get("v") != 2
        or not isinstance(session_hash, str)
        or not _HASH_RE.fullmatch(session_hash)
        or not isinstance(csrf_hash, str)
        or not _HASH_RE.fullmatch(csrf_hash)
        or not isinstance(value.get("inviteId"), str)
        or not _OPAQUE_ID_RE.fullmatch(value["inviteId"])
        or not owner_email
        or value.get("ownerEmail") != owner_email
        or value.get("workspaceId") != workspace_id
        or workspace_id != owner_email
        or not isinstance(value.get("collaborationId"), str)
        or not _OPAQUE_ID_RE.fullmatch(value["collaborationId"])
        or value.get("allowedActions") != ["read", "reply"]
        or value.get("visibility") != "shared_only"
        or value.get("identityAssurance") != "link_possession"
        or not _bounded_string(value.get("mailboxId"), 256)
        or not _MAILBOX_RE.fullmatch(value.get("mailboxId"))
        or not guest_display_name
        or any(timestamp is None for timestamp in timestamps.values())
        or timestamps["createdAt"] > timestamps["lastUsedAt"]
        or timestamps["lastUsedAt"] >= timestamps["expiresAt"]
        or timestamps["expiresAt"] - timestamps["createdAt"] > MAX_V2_GUEST_SESSION_LIFETIME_SECONDS
        or status not in {"active", "revoked", "expired", "logged_out"}
        or (status == "active" and (revoked_at is not None or logged_out_at is not None))
        or (status == "revoked" and (revoked_at is None or logged_out_at is not None))
        or (status == "logged_out" and (logged_out_at is None or revoked_at is not None))
        or (status == "expired" and (revoked_at is not None or logged_out_at is not None))
        or (revoked_at is not None and revoked_at <= timestamps["lastUsedAt"])
        or (revoked_at is not None and revoked_at >= timestamps["expiresAt"])
        or (logged_out_at is not None and logged_out_at <= timestamps["lastUsedAt"])
        or (logged_out_at is not None and logged_out_at >= timestamps["expiresAt"])
    ):
        return None
    return {
        "v": 2,
        "sessionHash": session_hash,
        "inviteId": value["inviteId"],
        "ownerEmail": owner_email,
        "workspaceId": workspace_id,
        "mailboxId": value["mailboxId"],
        "collaborationId": value["collaborationId"],
        "allowedActions": ["read", "reply"],
        "visibility": "shared_only",
        "identityAssurance": "link_possession",
        "guestDisplayName": guest_display_name,
        "createdAt": timestamps["createdAt"],
        "lastUsedAt": timestamps["lastUsedAt"],
        "expiresAt": timestamps["expiresAt"],
        "status": status,
        "csrfTokenHash": csrf_hash,
        "revokedAt": revoked_at,
        "loggedOutAt": logged_out_at,
    }


def _session_time_is_monotonic(session: object, now: int) -> bool:
    if not isinstance(session, dict) or type(now) is not int:
        return False
    audit_times = [session.get("createdAt"), session.get("lastUsedAt")]
    audit_times.extend(
        value
        for value in (session.get("revokedAt"), session.get("loggedOutAt"))
        if value is not None
    )
    return all(type(value) is int and now >= value for value in audit_times)


def issue_v2_invitation(
    context: object,
    collaboration_id: str,
    *,
    invited_email: str | None = None,
    now: int | None = None,
    lifetime_seconds: int = INVITE_LIFETIME_SECONDS,
    command_transport=None,
    thread_loader=_load_v2_thread,
) -> dict:
    current_time = int(time.time()) if now is None else now
    owner_email = context.owner_email if _is_internal_capability(context, actions={"issue_invite"}) else None
    workspace_id = context.workspace_id if owner_email is not None else None
    mailbox_id = context.mailbox_id if owner_email is not None else None
    capability_collaboration_id = context.collaboration_id if owner_email is not None else None
    creator_email = owner_email
    creator_name = context.actor_display_name if owner_email is not None else None
    normalized_invited_email = normalize_v2_email(invited_email) if invited_email is not None else None
    if (
        not isinstance(collaboration_id, str)
        or collaboration_id != capability_collaboration_id
        or owner_email is None
        or workspace_id != owner_email
        or not _bounded_string(mailbox_id, 256)
        or creator_email != owner_email
        or not creator_name
        or type(current_time) is not int
        or not MIN_V2_TIMESTAMP_SECONDS <= current_time <= MAX_V2_TIMESTAMP_SECONDS
        or type(lifetime_seconds) is not int
        or lifetime_seconds < 1
        or lifetime_seconds > INVITE_LIFETIME_SECONDS
        or current_time + lifetime_seconds > MAX_V2_TIMESTAMP_SECONDS
        or (invited_email is not None and normalized_invited_email is None)
    ):
        return _failure("malformed", "invalid_request")
    try:
        loaded = thread_loader(collaboration_id, command_transport=command_transport)
    except TypeError:
        loaded = thread_loader(collaboration_id)
    except Exception:
        return _failure("unavailable", "storage_unavailable")
    thread = normalize_v2_thread_record(loaded.get("record")) if hasattr(loaded, "get") and loaded.get("status") == "ok" else None
    if thread is None:
        return _failure("error", "collaboration_not_found" if isinstance(loaded, dict) and loaded.get("status") == "missing" else "storage_protocol_error")
    if (
        thread["ownerEmail"] != owner_email
        or thread["workspaceId"] != workspace_id
        or thread["mailboxId"] != mailbox_id
        or thread["collaborationId"] != collaboration_id
    ):
        return _failure("forbidden", "forbidden")
    raw_token = generate_v2_bearer_secret()
    token_hash = hash_v2_secret(raw_token)
    invite = {
        "v": 2,
        "inviteId": generate_v2_opaque_id(),
        "tokenHash": token_hash,
        "ownerEmail": thread["ownerEmail"],
        "workspaceId": workspace_id,
        "mailboxId": thread["mailboxId"],
        "collaborationId": thread["collaborationId"],
        "identityAssurance": "link_possession",
        "allowedActions": ["read", "reply"],
        "visibility": "shared_only",
        "createdBy": {"ownerEmail": creator_email, "displayName": creator_name},
        "createdAt": current_time,
        "expiresAt": current_time + lifetime_seconds,
        "status": "active",
        "exchangedAt": None,
        "exchangeCount": 0,
        "revokedAt": None,
        "revokedBy": None,
    }
    if normalized_invited_email is not None:
        invite["invitedEmail"] = normalized_invited_email
    stored = _create_v2_invite(invite, now=current_time, command_transport=command_transport)
    if stored.get("status") != "ok":
        return stored
    if stored.get("created") is not True:
        # A duplicate must not reveal a fresh, unpersisted bearer token.
        return {"status": "duplicate", "invite": _invite_response(stored["record"]), "error": None}
    return {"status": "ok", "invite": _invite_response(stored["record"]), "token": raw_token, "error": None}


def exchange_v2_invitation(
    raw_token: object,
    *,
    guest_display_name: object,
    now: int | None = None,
    session_lifetime_seconds: int = GUEST_SESSION_LIFETIME_SECONDS,
    command_transport=None,
) -> dict:
    current_time = int(time.time()) if now is None else now
    display_name = _bounded_string(guest_display_name, 256)
    if (
        not isinstance(raw_token, str)
        or not _BEARER_RE.fullmatch(raw_token)
        or not display_name
        or type(current_time) is not int
        or not MIN_V2_TIMESTAMP_SECONDS <= current_time <= MAX_V2_TIMESTAMP_SECONDS
        or type(session_lifetime_seconds) is not int
        or session_lifetime_seconds < 1
        or session_lifetime_seconds > GUEST_SESSION_LIFETIME_SECONDS
    ):
        return _failure("malformed", "invalid_request")
    loaded = _load_v2_invite_by_token(raw_token, now=current_time, command_transport=command_transport)
    if loaded.get("status") != "ok":
        return _guest_failure(loaded)
    invite = loaded["record"]
    if invite["status"] != "active" or invite["exchangeCount"] != 0:
        return _failure("exchanged", "invite_already_exchanged")
    raw_session_id = generate_v2_bearer_secret()
    raw_csrf_token = generate_v2_bearer_secret()
    session_hash = hash_v2_secret(raw_session_id)
    expires_at = min(
        current_time + session_lifetime_seconds,
        invite["expiresAt"],
    )
    session = {
        "v": 2,
        "sessionHash": session_hash,
        "inviteId": invite["inviteId"],
        "ownerEmail": invite["ownerEmail"],
        "workspaceId": invite["workspaceId"],
        "mailboxId": invite["mailboxId"],
        "collaborationId": invite["collaborationId"],
        "allowedActions": ["read", "reply"],
        "visibility": "shared_only",
        "identityAssurance": "link_possession",
        "guestDisplayName": display_name,
        "createdAt": current_time,
        "lastUsedAt": current_time,
        "expiresAt": expires_at,
        "status": "active",
        "csrfTokenHash": hash_v2_secret(raw_csrf_token),
        "revokedAt": None,
        "loggedOutAt": None,
    }
    if normalize_v2_guest_session_record(session) is None:
        return _failure("malformed", "invalid_request")
    exchanged = _atomic_exchange_v2_invite(
        raw_token=raw_token,
        invite_id=invite["inviteId"],
        session_record=session,
        now=current_time,
        session_ttl=expires_at - current_time,
        command_transport=command_transport,
    )
    if exchanged.get("status") != "ok":
        return _guest_failure(exchanged)
    return {
        "status": "ok",
        "sessionId": raw_session_id,
        "csrfToken": raw_csrf_token,
        "session": {
            "collaborationId": session["collaborationId"],
            "guestDisplayName": session["guestDisplayName"],
            "allowedActions": ["read", "reply"],
            "identityAssurance": "link_possession",
            "expiresAt": expires_at,
        },
        "error": None,
    }


def _validate_session_invite(session: dict, invite: dict) -> bool:
    return (
        invite.get("status") == "exchanged"
        and invite.get("inviteId") == session.get("inviteId")
        and invite.get("ownerEmail") == session.get("ownerEmail")
        and invite.get("workspaceId") == session.get("workspaceId")
        and invite.get("mailboxId") == session.get("mailboxId")
        and invite.get("collaborationId") == session.get("collaborationId")
        and invite.get("activeSessionHash") == session.get("sessionHash")
        and type(invite.get("exchangedAt")) is int
        and session.get("createdAt") == invite.get("exchangedAt")
        and invite.get("allowedActions") == ["read", "reply"]
        and invite.get("visibility") == "shared_only"
        and session.get("visibility") == "shared_only"
        and type(session.get("expiresAt")) is int
        and type(invite.get("expiresAt")) is int
        and session["expiresAt"] <= invite.get("expiresAt", -1)
    )


def _bootstrap_v2_guest_session_read_only(
    raw_session_id: object,
    *,
    now: int | None = None,
    command_transport=None,
) -> dict:
    """Load the bootstrap DTO without rotating CSRF or touching session state."""
    current_time = int(time.time()) if now is None else now
    if (
        type(current_time) is not int
        or not MIN_V2_TIMESTAMP_SECONDS <= current_time <= MAX_V2_TIMESTAMP_SECONDS
        or not isinstance(raw_session_id, str)
        or not _BEARER_RE.fullmatch(raw_session_id)
    ):
        return _failure("missing", "session_not_found")
    loaded = _load_v2_guest_session_record(
        raw_session_id,
        normalizer=normalize_v2_guest_session_record,
        now=current_time,
        command_transport=command_transport,
    )
    if loaded.get("status") != "ok":
        return _guest_failure(loaded)
    session = loaded["record"]
    if not _session_time_is_monotonic(session, current_time):
        return _failure("malformed", "invalid_request")
    if type(session.get("expiresAt")) is not int:
        return _failure("unavailable", "storage_unavailable")
    if current_time >= session["expiresAt"]:
        return _failure("expired", "session_expired")
    invite_loaded = _load_v2_invite_by_id(
        session["inviteId"], now=current_time, command_transport=command_transport
    )
    if invite_loaded.get("status") != "ok":
        code = (invite_loaded.get("error") or {}).get("code")
        if code == "invite_revoked":
            return _failure("revoked", "session_revoked")
        if code == "invite_expired":
            return _failure("expired", "session_expired")
        if code == "invite_not_found":
            return _failure("revoked", "session_revoked")
        return _guest_failure(invite_loaded)
    if not _validate_session_invite(session, invite_loaded["record"]):
        return _failure("revoked", "session_revoked")
    return {
        "status": "ok",
        "session": {
            "collaborationId": session["collaborationId"],
            "guestDisplayName": session["guestDisplayName"],
            "allowedActions": ["read", "reply"],
            "identityAssurance": "link_possession",
            "expiresAt": session["expiresAt"],
        },
        "error": None,
    }


def _resolve_guest_read_access(
    raw_session_id: object,
    *,
    now: int,
    command_transport=None,
) -> tuple[_GuestReadCapability | None, dict | None, dict | None]:
    if not isinstance(raw_session_id, str) or not _BEARER_RE.fullmatch(raw_session_id):
        return None, None, _failure("error", "session_not_found")
    loaded = _load_v2_guest_session_record(
        raw_session_id,
        normalizer=normalize_v2_guest_session_record,
        now=now,
        command_transport=command_transport,
    )
    if loaded.get("status") != "ok":
        return None, None, _guest_failure(loaded)
    session = loaded["record"]
    if not _session_time_is_monotonic(session, now):
        return None, None, _failure("malformed", "invalid_request")
    if type(session.get("expiresAt")) is not int:
        return None, None, _failure("unavailable", "storage_unavailable")
    if now >= session["expiresAt"]:
        return None, None, _failure("expired", "session_expired")
    invite_loaded = _load_v2_invite_by_id(
        session["inviteId"], now=now, command_transport=command_transport
    )
    if invite_loaded.get("status") != "ok":
        code = (invite_loaded.get("error") or {}).get("code")
        if code == "invite_expired":
            return None, None, _failure("error", "session_expired")
        if code in {"invite_revoked", "invite_not_found"}:
            return None, None, _failure("error", "session_revoked")
        return None, None, _guest_failure(invite_loaded)
    if not _validate_session_invite(session, invite_loaded.get("record", {})):
        return None, None, _failure("error", "session_revoked")
    capability = _GuestReadCapability(
        _GUEST_READ_SENTINEL,
        session["sessionHash"],
        session["inviteId"],
        session["ownerEmail"],
        session["workspaceId"],
        session["mailboxId"],
        session["collaborationId"],
        session["guestDisplayName"],
        session["expiresAt"],
    )
    return capability, session, None


def resolve_guest_v2_mutation_context(
    method: object,
    raw_headers: object,
    *,
    now: int | None = None,
    command_transport=None,
) -> dict:
    """Validate the complete raw request boundary and mint one mutation capability."""
    current_time = int(time.time()) if now is None else now
    if type(current_time) is not int or not MIN_V2_TIMESTAMP_SECONDS <= current_time <= MAX_V2_TIMESTAMP_SECONDS:
        return _failure("malformed", "invalid_request")
    if type(method) is not str or method != "POST":
        return _failure("malformed", "invalid_request")
    headers = _adapt_raw_security_headers(
        raw_headers,
        required={"origin", "content-type", CSRF_HEADER_NAME.lower(), "cookie"},
    )
    if headers is None:
        return _failure("malformed", "invalid_request")
    origin_result = _validate_adapted_origin(headers["origin"])
    if origin_result["status"] != "ok":
        return origin_result
    if headers["content-type"] not in {
        "application/json",
        "application/json; charset=utf-8",
    }:
        return _failure("malformed", "invalid_request")
    cookie_is_valid, raw_session_id = _parse_guest_cookie_value(headers["cookie"])
    if not cookie_is_valid:
        return _failure("malformed", "invalid_request")
    if raw_session_id is None:
        return _failure("error", "session_not_found")
    supplied_csrf = headers[CSRF_HEADER_NAME.lower()]
    if not _BEARER_RE.fullmatch(supplied_csrf):
        return _failure("malformed", "invalid_request")
    supplied_hash = hash_v2_secret(supplied_csrf)
    if supplied_hash is None:
        return _failure("malformed", "invalid_request")
    read_capability, session, error = _resolve_guest_read_access(
        raw_session_id,
        now=current_time,
        command_transport=command_transport,
    )
    if error is not None or not _is_guest_read_capability(read_capability) or session is None:
        return error or _failure("error", "session_not_found")
    if not hmac.compare_digest(session["csrfTokenHash"], supplied_hash):
        return _failure("forbidden", "csrf_failed")
    mutation_capability = _GuestMutationCapability(
        _GUEST_MUTATION_SENTINEL,
        read_capability.session_hash,
        read_capability.invite_id,
        read_capability.owner_email,
        read_capability.workspace_id,
        read_capability.mailbox_id,
        read_capability.collaboration_id,
        read_capability.guest_display_name,
        read_capability.expires_at,
        session["createdAt"],
        session["lastUsedAt"],
    )
    return {
        "status": "ok",
        "context": mutation_capability,
        "error": None,
    }


def revoke_invitation_for_owner(
    context: object,
    invite_id: str,
    *,
    now: int | None = None,
    command_transport=None,
    thread_loader=_load_v2_thread,
) -> dict:
    current_time = int(time.time()) if now is None else now
    valid_capability = _is_internal_capability(context, actions={"revoke_invite"})
    normalized_owner = context.owner_email if valid_capability else None
    normalized_revoker = normalized_owner
    if (
        not valid_capability
        or normalized_owner is None
        or context.workspace_id != normalized_owner
        or type(current_time) is not int
        or not MIN_V2_TIMESTAMP_SECONDS <= current_time <= MAX_V2_TIMESTAMP_SECONDS
    ):
        return _failure("malformed", "invalid_request")
    collaboration_id = context.collaboration_id
    try:
        loaded = thread_loader(collaboration_id, command_transport=command_transport)
    except TypeError:
        loaded = thread_loader(collaboration_id)
    except Exception:
        return _failure("unavailable", "storage_unavailable")
    canonical = normalize_v2_thread_record(loaded.get("record")) if hasattr(loaded, "get") and loaded.get("status") == "ok" else None
    if (
        canonical is None
        or canonical["ownerEmail"] != normalized_owner
        or canonical["workspaceId"] != normalized_owner
        or canonical["mailboxId"] != context.mailbox_id
        or canonical["collaborationId"] != collaboration_id
    ):
        return _failure("forbidden", "forbidden")
    return _revoke_v2_invite(
        invite_id,
        owner_email=normalized_owner,
        workspace_id=context.workspace_id,
        mailbox_id=context.mailbox_id,
        collaboration_id=collaboration_id,
        revoked_by=normalized_revoker,
        now=current_time,
        command_transport=command_transport,
    )


def logout_v2_guest_session(
    session_context: object,
    *,
    now: int | None = None,
    command_transport=None,
) -> dict:
    current_time = int(time.time()) if now is None else now
    if (
        type(current_time) is not int
        or not MIN_V2_TIMESTAMP_SECONDS <= current_time <= MAX_V2_TIMESTAMP_SECONDS
        or not _is_guest_mutation_capability(session_context)
    ):
        return _failure("malformed", "invalid_request")
    if current_time <= session_context.last_used_at:
        return _failure("malformed", "invalid_request")
    if current_time >= session_context.expires_at:
        return _failure("expired", "session_expired")
    result = _revoke_v2_guest_session(
        session_context.session_hash,
        invite_id=session_context.invite_id,
        owner_email=session_context.owner_email,
        workspace_id=session_context.workspace_id,
        mailbox_id=session_context.mailbox_id,
        collaboration_id=session_context.collaboration_id,
        now=current_time,
        command_transport=command_transport,
    )
    if result.get("status") == "ok":
        return {"status": "ok", "error": None}
    if result.get("status") == "already_logged_out":
        return {"status": "already_logged_out", "error": {"code": "already_logged_out"}}
    return _guest_failure(result)


def _adapt_raw_security_headers(
    raw_headers: object,
    *,
    required: set[str],
) -> dict[str, str] | None:
    """Adapt duplicate-preserving raw header pairs without normalizing values."""
    if (
        isinstance(raw_headers, (str, bytes, bytearray, Mapping))
        or not isinstance(raw_headers, Sequence)
    ):
        return None
    security_names = {"origin", "content-type", CSRF_HEADER_NAME.lower(), "cookie"}
    found: dict[str, str] = {}
    for pair in raw_headers:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            return None
        name, value = pair
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not name.isascii()
            or not _HEADER_NAME_RE.fullmatch(name)
            or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in name)
            or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
        ):
            return None
        lowered = name.lower()
        if lowered in security_names:
            if (
                lowered in found
                or value == ""
                or (lowered == "cookie" and value != value.strip())
                or "," in value
            ):
                return None
            found[lowered] = value
    return found if required <= set(found) else None


def _parse_guest_cookie_value(raw_cookie: str) -> tuple[bool, str | None]:
    if (
        not raw_cookie
        or raw_cookie != raw_cookie.strip()
        or "," in raw_cookie
        or len(raw_cookie.encode("utf-8")) > MAX_COOKIE_HEADER_BYTES
    ):
        return False, None
    target_values: list[str] = []
    for index, raw_part in enumerate(raw_cookie.split(";")):
        # RFC cookie-pairs permit separator spaces, but neither names nor values
        # are normalized. In particular, whitespace around '=' must not turn a
        # malformed or duplicate bearer into one valid cookie.
        if index > 0 and raw_part.startswith(" "):
            if raw_part.startswith("  "):
                return False, None
            part = raw_part[1:]
        else:
            part = raw_part
        if (
            not part
            or part != part.rstrip(" ")
            or any(not 0x21 <= ord(character) <= 0x7E for character in part)
        ):
            return False, None
        name, separator, value = part.partition("=")
        if not separator or not _HEADER_NAME_RE.fullmatch(name):
            return False, None
        # RFC 6265 cookie-octet: visible ASCII excluding DQUOTE, comma,
        # semicolon, and backslash. Validate every cookie-pair, not just the
        # bearer, so a malformed sibling cannot be ignored before storage.
        if any(
            ord(character) not in {0x21}
            and not 0x23 <= ord(character) <= 0x2B
            and not 0x2D <= ord(character) <= 0x3A
            and not 0x3C <= ord(character) <= 0x5B
            and not 0x5D <= ord(character) <= 0x7E
            for character in value
        ):
            return False, None
        if name == GUEST_SESSION_COOKIE_NAME:
            if not _BEARER_RE.fullmatch(value):
                return False, None
            target_values.append(value)
    if len(target_values) > 1:
        return False, None
    return True, target_values[0] if target_values else None


def _read_guest_cookie_value(raw_cookie: str) -> str | None:
    valid, value = _parse_guest_cookie_value(raw_cookie)
    return value if valid else None


def read_guest_session_cookie(raw_headers: object) -> str | None:
    headers = _adapt_raw_security_headers(raw_headers, required={"cookie"})
    return _read_guest_cookie_value(headers["cookie"]) if headers is not None else None


def build_guest_session_cookie(
    raw_session_id: str,
    *,
    expires_at: int,
    now: int,
) -> str | None:
    if not isinstance(raw_session_id, str) or not _BEARER_RE.fullmatch(raw_session_id):
        return None
    if isinstance(expires_at, bool) or not isinstance(expires_at, int) or isinstance(now, bool) or not isinstance(now, int):
        return None
    max_age = max(0, min(GUEST_SESSION_LIFETIME_SECONDS, expires_at - now))
    attributes = [
        f"{GUEST_SESSION_COOKIE_NAME}={raw_session_id}",
        f"Path={GUEST_SESSION_COOKIE_PATH}",
        f"Max-Age={max_age}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if _secure_cookie_required():
        attributes.append("Secure")
    return "; ".join(attributes)


def _secure_cookie_required() -> bool:
    environment = os.getenv("VERCEL_ENV", "production").strip().lower()
    return environment not in {"development", "test"}


def clear_guest_session_cookie() -> str:
    attributes = [
        f"{GUEST_SESSION_COOKIE_NAME}=",
        f"Path={GUEST_SESSION_COOKIE_PATH}",
        "Max-Age=0",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if _secure_cookie_required():
        attributes.append("Secure")
    return "; ".join(attributes)


def _canonical_origin(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 2048
        or not value.isascii()
        or "," in value
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    ):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ""
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    default_port = 443 if parsed.scheme == "https" else 80
    port_part = f":{port}" if port is not None and port != default_port else ""
    hostname = parsed.hostname.lower()
    host_part = f"[{hostname}]" if ":" in hostname else hostname
    canonical = f"{parsed.scheme}://{host_part}{port_part}"
    return canonical if value == canonical else None


def _validate_adapted_origin(supplied_value: str) -> dict:
    configured = os.getenv("CUEVION_APP_ORIGIN", "") or None
    expected = _canonical_origin(configured)
    supplied = _canonical_origin(supplied_value)
    if configured is not None and expected is None:
        return _failure("forbidden", "origin_rejected")
    if expected is None:
        if _secure_cookie_required():
            return _failure("forbidden", "origin_rejected")
        if supplied is None:
            return _failure("forbidden", "origin_rejected")
        parsed = urlsplit(supplied)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return _failure("forbidden", "origin_rejected")
        return {"status": "ok", "error": None}
    if supplied is None or supplied != expected:
        return _failure("forbidden", "origin_rejected")
    return {"status": "ok", "error": None}


def validate_guest_request_origin(raw_headers: object) -> dict:
    headers = _adapt_raw_security_headers(raw_headers, required={"origin"})
    if headers is None:
        return _failure("forbidden", "origin_rejected")
    return _validate_adapted_origin(headers["origin"])
