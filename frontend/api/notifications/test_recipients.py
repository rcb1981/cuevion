from __future__ import annotations

import copy
import unittest
from unittest.mock import Mock, patch

from .recipients import resolve_notification_recipients


OWNER = "usr_" + "A" * 22
PARTICIPANT = "usr_" + "B" * 21 + "A"
SECOND = "usr_" + "C" * 21 + "A"
WORKSPACE = "wsp_" + "W" * 22


def thread():
    return {
        "v": 2, "collaborationId": "A" * 22, "workspaceId": WORKSPACE,
        "mailboxId": "mailbox-1", "ownerEmail": "owner@example.com",
        "ownerUserId": OWNER, "ownerDisplayName": "Owner",
        "participants": [
            {"userId": PARTICIPANT, "displayName": "Participant", "membershipRef": "tinv_participant"},
            {"userId": SECOND, "displayName": "Second", "membershipRef": "tinv_second"},
        ],
        "sourceRef": {"provider": "google", "providerMessageId": "mail-1"},
        "sourceMessage": {"subject": "Review", "senderDisplay": "Sender", "fromDisplay": "Sender",
                          "timestamp": "today", "bodyText": "Private source"},
        "state": "needs_review", "createdAt": 1_800_000_000_000,
        "updatedAt": 1_800_000_000_000, "messages": [],
    }


def active(workspace, user_id):
    assert workspace == WORKSPACE
    return {"memberUserId": user_id, "sourceInvitationId": {
        PARTICIPANT: "tinv_participant", SECOND: "tinv_second",
    }[user_id]}, None


class NotificationRecipientAuthorityTests(unittest.TestCase):
    def test_owner_actor_is_self_suppressed_without_owner_team_lookup(self):
        resolver = Mock(side_effect=active)
        self.assertEqual(resolve_notification_recipients(thread(), OWNER, team_member_resolver=resolver),
                         {"status": "ok", "userIds": [PARTICIPANT, SECOND]})
        self.assertEqual([call.args for call in resolver.call_args_list],
                         [(WORKSPACE, PARTICIPANT), (WORKSPACE, SECOND)])

    def test_participant_actor_is_self_suppressed_and_owner_retained(self):
        resolver = Mock(side_effect=active)
        self.assertEqual(resolve_notification_recipients(thread(), PARTICIPANT, team_member_resolver=resolver),
                         {"status": "ok", "userIds": [OWNER, SECOND]})
        resolver.assert_called_once_with(WORKSPACE, SECOND)

    def test_external_actor_has_no_cuevion_id_and_all_entitled_users_remain(self):
        self.assertEqual(resolve_notification_recipients(thread(), None, team_member_resolver=active),
                         {"status": "ok", "userIds": [OWNER, PARTICIPANT, SECOND]})

    def test_inactive_and_reinvited_old_membership_are_excluded_for_every_actor(self):
        for actor, expected in ((OWNER, []), (PARTICIPANT, [OWNER]), (None, [OWNER])):
            def resolve(_workspace, user_id):
                if user_id == PARTICIPANT:
                    return None, "not_active"
                return {"memberUserId": SECOND, "sourceInvitationId": "tinv_new_invitation"}, None
            self.assertEqual(resolve_notification_recipients(thread(), actor, team_member_resolver=resolve),
                             {"status": "ok", "userIds": expected})

    def test_duplicate_user_ids_are_deduplicated_and_conflicting_grants_rejected(self):
        value = thread()
        value["participants"].append(copy.deepcopy(value["participants"][0]))
        resolver = Mock(side_effect=active)
        self.assertEqual(resolve_notification_recipients(value, None, team_member_resolver=resolver),
                         {"status": "ok", "userIds": [OWNER, PARTICIPANT, SECOND]})
        self.assertEqual(resolver.call_count, 2)
        value["participants"][-1]["membershipRef"] = "tinv_conflicting"
        self.assertEqual(resolve_notification_recipients(value, None, team_member_resolver=active)["status"], "malformed")

    def test_owner_duplicate_is_not_a_team_recipient_and_owner_only_create_emits_none(self):
        value = thread()
        value["participants"] = [{"userId": OWNER, "displayName": "Owner", "membershipRef": "tinv_owner"}]
        resolver = Mock(side_effect=AssertionError("owner has no Team enrollment"))
        self.assertEqual(resolve_notification_recipients(value, None, team_member_resolver=resolver),
                         {"status": "ok", "userIds": [OWNER]})
        self.assertEqual(resolve_notification_recipients(value, OWNER, team_member_resolver=resolver),
                         {"status": "ok", "userIds": []})
        resolver.assert_not_called()

    def test_guest_invite_records_cannot_become_participant_recipients(self):
        value = thread()
        value["participants"] = [{"inviteId": "I" * 22, "email": "guest@example.com"}]
        resolver = Mock()
        self.assertEqual(resolve_notification_recipients(value, None, team_member_resolver=resolver)["status"], "malformed")
        resolver.assert_not_called()

    def test_outage_exception_wrong_user_and_malformed_authority_fail_closed(self):
        for result in ((None, "unavailable"), (None, None), ({"memberUserId": OWNER}, None),
                       ({"memberUserId": PARTICIPANT}, None), (None, "unknown")):
            resolver = Mock(return_value=result)
            self.assertEqual(resolve_notification_recipients(thread(), OWNER, team_member_resolver=resolver),
                             {"status": "unavailable", "error": {"code": "storage_unavailable"}})
        resolver = Mock(side_effect=RuntimeError("private authority detail"))
        self.assertEqual(resolve_notification_recipients(thread(), OWNER, team_member_resolver=resolver),
                         {"status": "unavailable", "error": {"code": "storage_unavailable"}})

    def test_invalid_scope_and_actor_fail_before_external_authority(self):
        for value, actor in ((None, OWNER), ({**thread(), "ownerUserId": "guest"}, None),
                             ({**thread(), "workspaceId": "forged"}, OWNER), (thread(), "guest"),
                             (thread(), "usr_" + "D" * 21 + "A")):
            resolver = Mock()
            self.assertEqual(resolve_notification_recipients(value, actor, team_member_resolver=resolver)["status"], "malformed")
            resolver.assert_not_called()

    def test_default_reuses_existing_collaboration_team_authority(self):
        with patch("api.collaboration.authorization._resolve_active_team_member", side_effect=active) as resolver:
            self.assertEqual(resolve_notification_recipients(thread(), OWNER)["userIds"], [PARTICIPANT, SECOND])
            self.assertEqual(resolver.call_count, 2)

    def test_no_input_mutation_or_sensitive_return_fields(self):
        value = thread()
        original = copy.deepcopy(value)
        result = resolve_notification_recipients(value, None, team_member_resolver=active)
        self.assertEqual(value, original)
        self.assertEqual(set(result), {"status", "userIds"})

    def test_legacy_thread_has_no_inferred_account_recipients(self):
        value = thread()
        for field in ("ownerUserId", "ownerDisplayName", "participants"):
            value.pop(field)
        resolver = Mock()
        self.assertEqual(resolve_notification_recipients(value, None, team_member_resolver=resolver),
                         {"status": "ok", "userIds": []})
        resolver.assert_not_called()
