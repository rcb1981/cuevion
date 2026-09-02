from __future__ import annotations

if __name__ != "api.collaboration.authorization":
    raise ImportError(
        "Collaboration helpers must be imported as "
        "api.collaboration.authorization"
    )

import importlib
import re
import unicodedata
from dataclasses import dataclass

from .models import (
    normalize_v2_email,
    normalize_v2_thread_record,
    normalize_v2_user_id,
)
from .redis_store import _load_v2_thread


_INTERNAL_CAPABILITY_SENTINEL = object()


@dataclass(frozen=True, slots=True)
class _InternalCollaborationCapability:
    _sentinel: object
    owner_email: str
    workspace_id: str
    mailbox_id: str
    mailbox_provider: str
    collaboration_id: str | None
    action: str
    actor_kind: str
    actor_display_name: str
    actor_user_id: str | None = None
    viewer_access: str = "owner"
    owner_user_id: str | None = None
    owner_display_name: str = ""


def _is_internal_capability(value: object, *, actions: set[str] | None = None) -> bool:
    return (
        type(value) is _InternalCollaborationCapability
        and value._sentinel is _INTERNAL_CAPABILITY_SENTINEL
        and (actions is None or value.action in actions)
    )


def _security_string(value: object, maximum: int) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
        or len(value.encode("utf-8")) > maximum
    ):
        return None
    return value


def _shared_config_helper(name: str):
    # A lazy adapter keeps the inactive module import side-effect free and avoids
    # widening the existing route module's intentionally fixed import inventory.
    module = importlib.import_module("api.user_config_" + "store")
    return getattr(module, name)


def _resolve_authenticated_user(headers):
    return _shared_config_helper("resolve_authenticated_user")(headers)


def _resolve_owned_managed_inbox_record(headers, mailbox_id):
    return _shared_config_helper("resolve_owned_managed_inbox_record")(headers, mailbox_id)


def _resolve_verified_owned_managed_inbox_record(headers, mailbox_id):
    return _shared_config_helper("resolve_owned_managed_inbox_record")(
        headers,
        mailbox_id,
        include_member_authority=True,
    )


def _resolve_current_authenticated_member(headers):
    try:
        runtime = importlib.import_module("api.auth.runtime")
        resolution = runtime.resolve_authenticated_member(headers)
    except Exception:
        return None, "unavailable"
    if resolution.outcome is runtime.MemberResolutionOutcome.UNAUTHENTICATED:
        return None, "unauthorized"
    if (
        resolution.outcome is not runtime.MemberResolutionOutcome.AUTHENTICATED
        or type(resolution.member) is not runtime.AuthenticatedMemberContext
    ):
        return None, "unavailable"
    return resolution.member, None


def _resolve_active_team_member(workspace_id: str, member_user_id: str):
    try:
        authority = importlib.import_module("api.team.authority")
        membership, error = authority.build_runtime_team_authority().resolve_active_member_by_user_id(
            workspace_id=workspace_id,
            member_user_id=member_user_id,
        )
    except Exception:
        return None, "unavailable"
    if error is None and type(membership) is dict:
        return membership, None
    code = error.get("code") if type(error) is dict else None
    if code in {"invalid_request", "team_member_not_active"}:
        return None, "not_active"
    return None, "unavailable"

OWNER_ONLY_ACTIONS = {
    "create",
    "issue_invite",
    "revoke_invite",
    "manage_participants",
    "resolve",
    "reopen",
}
OWNER_ACTIONS = OWNER_ONLY_ACTIONS | {"read", "reply"}
OWNER_ACTIONS.add("internal_note")
PARTICIPANT_ACTIONS = frozenset({"read", "reply", "internal_note"})


def _failure(status: str, code: str) -> dict:
    return {"status": status, "context": None, "error": {"code": code}}


def resolve_internal_collaboration_context(
    headers,
    mailbox_id: object | None = None,
    *,
    collaboration_id: object | None = None,
    required_action: str = "read",
    user_resolver=_resolve_authenticated_user,
    mailbox_resolver=_resolve_owned_managed_inbox_record,
    thread_loader=_load_v2_thread,
) -> dict:
    """Resolve owner identity from the authenticated session and owned mailbox.

    Browser-supplied workspace or owner values are never accepted.  Guest cookies
    are deliberately ignored: possession sessions are authorized by the guest
    boundary, not by this internal-owner helper.
    """
    if (
        required_action not in OWNER_ACTIONS
        or (
            mailbox_id is not None
            and (
                not isinstance(mailbox_id, str)
                or not mailbox_id
                or mailbox_id != mailbox_id.strip()
                or not mailbox_id.isascii()
                or len(mailbox_id.encode("utf-8")) > 256
            )
        )
        or (
            collaboration_id is not None
            and (
                not isinstance(collaboration_id, str)
                or not collaboration_id
                or collaboration_id != collaboration_id.strip()
                or not collaboration_id.isascii()
                or not re.fullmatch(r"[A-Za-z0-9_-]{22,128}", collaboration_id)
            )
        )
    ):
        return _failure("malformed", "invalid_request")

    try:
        user, auth_error = user_resolver(headers)
    except Exception:
        return _failure("unavailable", "storage_unavailable")
    if not user:
        if isinstance(auth_error, dict) and auth_error.get("code") == "session_auth_unavailable":
            return _failure("unavailable", "storage_unavailable")
        return _failure("unauthorized", "auth_required")
    owner_email = normalize_v2_email(user.get("email")) if isinstance(user, dict) else None
    if owner_email is None:
        return _failure("unavailable", "storage_unavailable")

    def resolve_owned_mailbox(resolved_id: str) -> tuple[dict | None, dict | None]:
        try:
            owned_result = mailbox_resolver(headers, resolved_id)
        except Exception:
            return None, _failure("unavailable", "storage_unavailable")
        if not isinstance(owned_result, dict):
            return None, _failure("unavailable", "storage_unavailable")
        if owned_result.get("status") == "unauthorized":
            return None, _failure("unauthorized", "auth_required")
        if owned_result.get("status") == "not_found":
            return None, _failure("not_found", "mailbox_not_found")
        if owned_result.get("status") in {"unavailable", "malformed", "conflict"}:
            return None, _failure("unavailable", "storage_unavailable")
        owned_user = owned_result.get("user")
        inbox_record = owned_result.get("inbox")
        if (
            owned_result.get("status") != "ok"
            or not isinstance(owned_user, dict)
            or normalize_v2_email(owned_user.get("email")) != owner_email
            or not isinstance(inbox_record, dict)
            or inbox_record.get("id") != resolved_id
        ):
            return None, _failure("forbidden", "forbidden")
        return inbox_record, None

    thread = None
    resolved_mailbox_id = mailbox_id
    inbox = None
    if resolved_mailbox_id is not None:
        inbox, mailbox_error = resolve_owned_mailbox(resolved_mailbox_id)
        if mailbox_error:
            return mailbox_error

    if collaboration_id is not None:
        try:
            loaded = thread_loader(collaboration_id)
        except Exception:
            return _failure("unavailable", "storage_unavailable")
        if not hasattr(loaded, "get"):
            return _failure("unavailable", "storage_unavailable")
        if loaded.get("status") == "missing":
            return _failure("not_found", "collaboration_not_found")
        if loaded.get("status") in {"unavailable", "malformed"}:
            return _failure("unavailable", "storage_unavailable")
        thread = normalize_v2_thread_record(loaded.get("record"))
        if (
            loaded.get("status") != "ok"
            or not isinstance(thread, dict)
            or thread.get("ownerEmail") != owner_email
            or thread.get("workspaceId") != owner_email
            or not isinstance(thread.get("mailboxId"), str)
        ):
            return _failure("forbidden", "forbidden")
        if resolved_mailbox_id is not None and thread["mailboxId"] != resolved_mailbox_id:
            return _failure("forbidden", "forbidden")
        resolved_mailbox_id = thread["mailboxId"]
    if resolved_mailbox_id is None:
        return _failure("malformed", "invalid_request")
    if inbox is None:
        inbox, mailbox_error = resolve_owned_mailbox(resolved_mailbox_id)
        if mailbox_error:
            return mailbox_error

    display_name = _security_string(user.get("name"), 256)
    mailbox_value = _security_string(inbox.get("id") if isinstance(inbox, dict) else None, 256)
    mailbox_provider = inbox.get("provider") if isinstance(inbox, dict) else None
    if (
        display_name is None
        or mailbox_value != resolved_mailbox_id
        or not mailbox_value.isascii()
        or mailbox_value.lower() != mailbox_value
        or not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,255}", mailbox_value)
        or mailbox_provider not in {"google", "custom_imap"}
    ):
        return _failure("unavailable", "storage_unavailable")

    capability = _InternalCollaborationCapability(
        _INTERNAL_CAPABILITY_SENTINEL,
        owner_email,
        owner_email,
        mailbox_value,
        mailbox_provider,
        collaboration_id,
        required_action,
        "owner",
        display_name,
        None,
        "owner",
        None,
        display_name,
    )
    return {
        "status": "ok",
        "context": capability,
        "error": None,
    }


def resolve_verified_owner_collaboration_context(
    owner_context: object,
    headers: object,
    mailbox_id: object | None = None,
    *,
    collaboration_id: object | None = None,
    required_action: str = "read",
    owner_security_configuration: object,
    mailbox_resolver=_resolve_verified_owned_managed_inbox_record,
    thread_loader=_load_v2_thread,
    member_resolver=_resolve_current_authenticated_member,
    team_member_resolver=_resolve_active_team_member,
) -> dict:
    """Mint an internal capability from exact Auth0 owner and mailbox authority."""

    try:
        owner_security = importlib.import_module(
            "api.collaboration.owner_request_security"
        )
    except Exception:
        return _failure("unavailable", "storage_unavailable")
    if not owner_security._is_owner_context(owner_context):
        return _failure("unauthorized", "auth_required")
    if not owner_security.owner_is_allowlisted(
        owner_context,
        owner_security_configuration,
    ):
        raise owner_security.OwnerSecurityError("rollout_unavailable")
    if (
        required_action not in OWNER_ACTIONS
        or (
            mailbox_id is not None
            and (
                type(mailbox_id) is not str
                or not mailbox_id
                or mailbox_id != mailbox_id.strip()
                or not mailbox_id.isascii()
                or re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,255}", mailbox_id)
                is None
            )
        )
        or (
            collaboration_id is not None
            and (
                type(collaboration_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{22,128}", collaboration_id)
                is None
            )
        )
    ):
        return _failure("malformed", "invalid_request")

    thread = None
    viewer_is_owner = True
    resolved_mailbox_id = mailbox_id
    if collaboration_id is not None:
        try:
            loaded = thread_loader(collaboration_id)
        except Exception:
            return _failure("unavailable", "storage_unavailable")
        if not hasattr(loaded, "get"):
            return _failure("unavailable", "storage_unavailable")
        if loaded.get("status") == "missing":
            return _failure("not_found", "collaboration_not_found")
        if loaded.get("status") in {"unavailable", "malformed"}:
            return _failure("unavailable", "storage_unavailable")
        thread = normalize_v2_thread_record(loaded.get("record"))
        if (
            loaded.get("status") != "ok"
            or type(thread) is not dict
            or thread.get("workspaceId") != owner_context.workspace_id
            or type(thread.get("mailboxId")) is not str
        ):
            return _failure("forbidden", "forbidden")
        viewer_is_owner = thread.get("ownerEmail") == owner_context.owner_email
        if (
            resolved_mailbox_id is not None
            and thread["mailboxId"] != resolved_mailbox_id
        ):
            return _failure("forbidden", "forbidden")
        resolved_mailbox_id = thread["mailboxId"]

    if thread is not None and not viewer_is_owner:
        if (
            required_action not in PARTICIPANT_ACTIONS
            or mailbox_id is not None
            or normalize_v2_user_id(thread.get("ownerUserId")) is None
            or type(thread.get("ownerDisplayName")) is not str
            or not isinstance(thread.get("participants"), list)
        ):
            return _failure("forbidden", "forbidden")
        try:
            member, member_error = member_resolver(headers)
        except Exception:
            return _failure("unavailable", "storage_unavailable")
        if member_error == "unauthorized":
            return _failure("unauthorized", "auth_required")
        if member_error is not None:
            return _failure("unavailable", "storage_unavailable")
        try:
            auth_runtime = importlib.import_module("api.auth.runtime")
            member_matches = (
                type(member) is auth_runtime.AuthenticatedMemberContext
                and member.auth_source == "auth0"
                and member.user_type == "member"
                and normalize_v2_user_id(member.user_id) == member.user_id
                and member.email == owner_context.owner_email
                and member.workspace_id == owner_context.workspace_id
                and member.name == owner_context.display_name
            )
        except Exception:
            return _failure("unavailable", "storage_unavailable")
        if not member_matches:
            return _failure("forbidden", "forbidden")
        participant = next(
            (
                entry
                for entry in thread["participants"]
                if entry.get("userId") == member.user_id
            ),
            None,
        )
        if type(participant) is not dict:
            return _failure("forbidden", "forbidden")
        try:
            membership, team_error = team_member_resolver(
                owner_context.workspace_id,
                member.user_id,
            )
        except Exception:
            return _failure("unavailable", "storage_unavailable")
        if team_error == "unavailable":
            return _failure("unavailable", "storage_unavailable")
        if (
            team_error is not None
            or type(membership) is not dict
            or membership.get("memberUserId") != member.user_id
            or membership.get("sourceInvitationId")
            != participant.get("membershipRef")
        ):
            return _failure("forbidden", "forbidden")
        provider = thread.get("sourceRef", {}).get("provider")
        if provider not in {"google", "custom_imap"}:
            return _failure("unavailable", "storage_unavailable")
        capability = _InternalCollaborationCapability(
            _INTERNAL_CAPABILITY_SENTINEL,
            thread["ownerEmail"],
            thread["workspaceId"],
            thread["mailboxId"],
            provider,
            collaboration_id,
            required_action,
            "internal",
            member.name,
            member.user_id,
            "participant",
            thread["ownerUserId"],
            thread["ownerDisplayName"],
        )
        return {"status": "ok", "context": capability, "error": None}

    if type(resolved_mailbox_id) is not str:
        return _failure("malformed", "invalid_request")
    if not owner_security.mailbox_is_allowlisted(
        owner_context,
        resolved_mailbox_id,
        owner_security_configuration,
    ):
        raise owner_security.OwnerSecurityError("rollout_unavailable")

    try:
        owned_result = mailbox_resolver(headers, resolved_mailbox_id)
    except Exception:
        return _failure("unavailable", "storage_unavailable")
    if type(owned_result) is not dict:
        return _failure("unavailable", "storage_unavailable")
    if owned_result.get("status") == "unauthorized":
        return _failure("unauthorized", "auth_required")
    if owned_result.get("status") == "not_found":
        return _failure("not_found", "mailbox_not_found")
    if owned_result.get("status") in {"unavailable", "malformed", "conflict"}:
        return _failure("unavailable", "storage_unavailable")

    try:
        auth_runtime = importlib.import_module("api.auth.runtime")
        member = owned_result.get("memberAuthority")
        owned_user = owned_result.get("user")
        inbox = owned_result.get("inbox")
        member_matches = (
            type(member) is auth_runtime.AuthenticatedMemberContext
            and member.auth_source == "auth0"
            and member.user_type == "member"
            and normalize_v2_user_id(member.user_id) == member.user_id
            and member.email == owner_context.owner_email
            and member.workspace_id == owner_context.workspace_id
            and member.name == owner_context.display_name
        )
    except Exception:
        return _failure("unavailable", "storage_unavailable")
    if (
        owned_result.get("status") != "ok"
        or not member_matches
        or type(owned_user) is not dict
        or normalize_v2_email(owned_user.get("email"))
        != owner_context.owner_email
        or type(inbox) is not dict
        or inbox.get("id") != resolved_mailbox_id
        or (
            thread is not None
            and "ownerUserId" in thread
            and thread["ownerUserId"] != member.user_id
        )
    ):
        return _failure("forbidden", "forbidden")

    mailbox_value = _security_string(inbox.get("id"), 256)
    mailbox_provider = inbox.get("provider")
    if (
        mailbox_value != resolved_mailbox_id
        or not mailbox_value.isascii()
        or mailbox_value.lower() != mailbox_value
        or re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,255}", mailbox_value)
        is None
        or mailbox_provider not in {"google", "custom_imap"}
    ):
        return _failure("unavailable", "storage_unavailable")

    capability = _InternalCollaborationCapability(
        _INTERNAL_CAPABILITY_SENTINEL,
        owner_context.owner_email,
        owner_context.workspace_id,
        mailbox_value,
        mailbox_provider,
        collaboration_id,
        required_action,
        "owner",
        owner_context.display_name,
        member.user_id,
        "owner",
        (
            thread["ownerUserId"]
            if thread is not None and "ownerUserId" in thread
            else member.user_id
        ),
        (
            thread["ownerDisplayName"]
            if thread is not None and "ownerDisplayName" in thread
            else owner_context.display_name
        ),
    )
    return {"status": "ok", "context": capability, "error": None}
