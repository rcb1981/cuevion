"""Current Team recipient proof before the bounded Collaboration Redis commit.

Team authority and Redis are separate stores. This preserves Collaboration's
existing check-before-commit race semantics; it does not claim a cross-store
transaction. Lua must still verify these IDs against its canonical thread.
"""

from __future__ import annotations

from api.collaboration.models import (
    MAX_V2_EXPLICIT_PARTICIPANTS,
    normalize_v2_participant_authority,
    normalize_v2_team_membership_ref,
    normalize_v2_thread_record,
    normalize_v2_user_id,
)


def _malformed():
    return {"status": "malformed", "error": {"code": "storage_protocol_error"}}


def _unavailable():
    return {"status": "unavailable", "error": {"code": "storage_unavailable"}}


def resolve_notification_recipients(
    thread: object, actor_user_id: object, *, team_member_resolver=None,
) -> dict:
    """Return only owner/current exact Team memberships, excluding the actor."""

    if type(thread) is not dict:
        return _malformed()
    if "ownerUserId" not in thread:
        # Frozen legacy records have no canonical app recipient authority.
        # Never infer an account from their owner email or guest session.
        return ({"status": "ok", "userIds": []}
                if normalize_v2_thread_record(thread) is not None else _malformed())
    owner = thread.get("ownerUserId")
    raw_participants = thread.get("participants")
    if (normalize_v2_user_id(owner) is None or type(raw_participants) is not list
            or len(raw_participants) > MAX_V2_EXPLICIT_PARTICIPANTS
            or (actor_user_id is not None and normalize_v2_user_id(actor_user_id) is None)):
        return _malformed()
    participants = {}
    for raw in raw_participants:
        participant = normalize_v2_participant_authority(raw)
        if participant is None:
            return _malformed()
        user_id = participant["userId"]
        if user_id == owner:
            continue
        if user_id in participants and participants[user_id] != participant:
            return _malformed()
        participants[user_id] = participant
    canonical = normalize_v2_thread_record({**thread, "participants": list(participants.values())})
    if canonical is None or (actor_user_id is not None and actor_user_id not in {owner, *participants}):
        return _malformed()
    if team_member_resolver is None:
        from api.collaboration.authorization import _resolve_active_team_member
        team_member_resolver = _resolve_active_team_member
    if not callable(team_member_resolver):
        return _unavailable()

    recipients = [] if owner == actor_user_id else [owner]
    for user_id, participant in participants.items():
        if user_id == actor_user_id:
            continue
        try:
            membership, error = team_member_resolver(canonical["workspaceId"], user_id)
        except Exception:
            return _unavailable()
        if error == "not_active":
            continue
        if error is not None or type(membership) is not dict:
            return _unavailable()
        if membership.get("memberUserId") != user_id:
            return _unavailable()
        current_ref = normalize_v2_team_membership_ref(membership.get("sourceInvitationId"))
        if current_ref is None:
            return _unavailable()
        # A removed and re-invited account has a new Team membershipRef and
        # cannot regain notification access to its old Collaboration grant.
        if current_ref != participant["membershipRef"]:
            continue
        recipients.append(user_id)
    return {"status": "ok", "userIds": recipients}
