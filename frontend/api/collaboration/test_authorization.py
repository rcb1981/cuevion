from __future__ import annotations

import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from . import authorization
from .authorization import (
    resolve_internal_collaboration_context,
    resolve_verified_owner_collaboration_context,
)

MS = 1_800_000_000_000


def user_resolver(_headers):
    return {"email": "owner@example.com", "name": "Owner", "userType": "owner"}, None


def mailbox_resolver(_headers, mailbox_id):
    return {
        "status": "ok",
        "user": {"email": "owner@example.com"},
        "inbox": {"id": mailbox_id, "provider": "google"},
        "config": {},
        "error": None,
    }


def thread_loader(_collaboration_id):
    return {
        "status": "ok",
        "record": {
            "v": 2,
            "collaborationId": "A" * 22,
            "ownerEmail": "owner@example.com",
            "workspaceId": "owner@example.com",
            "mailboxId": "mailbox-1",
            "sourceRef": {"provider": "google", "providerMessageId": "gmail-1"},
            "sourceMessage": {
                "subject": "Review", "senderDisplay": "Sender",
                "fromDisplay": "sender@example.com", "timestamp": "today", "bodyText": "Body",
            },
            "state": "needs_review", "messages": [], "createdAt": MS + 100, "updatedAt": MS + 100,
        },
    }


class CollaborationV2AuthorizationTests(unittest.TestCase):
    def test_resolves_workspace_only_from_authenticated_owner(self):
        result = resolve_internal_collaboration_context(
            {"Cookie": "cuevion_collab_guest_session=ignored"},
            "mailbox-1",
            collaboration_id="A" * 22,
            required_action="issue_invite",
            user_resolver=user_resolver,
            mailbox_resolver=mailbox_resolver,
            thread_loader=thread_loader,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["context"].workspace_id, "owner@example.com")

    def test_thread_context_can_derive_owned_mailbox_without_browser_mailbox(self):
        result = resolve_internal_collaboration_context(
            {}, collaboration_id="A" * 22, required_action="read",
            user_resolver=user_resolver,
            mailbox_resolver=mailbox_resolver,
            thread_loader=thread_loader,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["context"].mailbox_id, "mailbox-1")

    def test_capability_is_opaque_and_mapping_shaped_forgeries_are_not_equivalent(self):
        result = resolve_internal_collaboration_context(
            {}, "mailbox-1", collaboration_id="A" * 22, required_action="reply",
            user_resolver=user_resolver, mailbox_resolver=mailbox_resolver,
            thread_loader=thread_loader,
        )
        capability = result["context"]
        copied = {
            "owner_email": capability.owner_email,
            "workspace_id": capability.workspace_id,
            "mailbox_id": capability.mailbox_id,
            "collaboration_id": capability.collaboration_id,
            "action": capability.action,
        }

        @dataclass
        class Lookalike:
            owner_email: str
            workspace_id: str
            mailbox_id: str
            collaboration_id: str
            action: str

        from .authorization import _is_internal_capability

        self.assertTrue(_is_internal_capability(capability, actions={"reply"}))
        for forged in (
            copied,
            SimpleNamespace(**copied),
            Lookalike(**copied),
        ):
            self.assertFalse(_is_internal_capability(forged, actions={"reply"}))

    def test_guest_cookie_alone_never_authenticates_internal_actions(self):
        result = resolve_internal_collaboration_context(
            {"Cookie": "cuevion_collab_guest_session=guest"},
            "mailbox-1",
            required_action="resolve",
            user_resolver=lambda _headers: (None, {"code": "missing_session"}),
            mailbox_resolver=mailbox_resolver,
        )
        self.assertEqual(result["error"]["code"], "auth_required")

    def test_cross_owner_thread_and_mailbox_are_forbidden(self):
        mismatched_thread = lambda _id: {
            "status": "ok",
            "record": {
                "ownerEmail": "other@example.com",
                "workspaceId": "other@example.com",
                "mailboxId": "mailbox-1",
            },
        }
        result = resolve_internal_collaboration_context(
            {}, "mailbox-1", collaboration_id="A" * 22,
            user_resolver=user_resolver,
            mailbox_resolver=mailbox_resolver,
            thread_loader=mismatched_thread,
        )
        self.assertEqual(result["error"]["code"], "forbidden")
        bad_mailbox = lambda _headers, _id: {
            "status": "ok", "user": {"email": "other@example.com"},
            "inbox": {"id": "mailbox-1"}, "config": {}, "error": None,
        }
        result = resolve_internal_collaboration_context(
            {}, "mailbox-1", user_resolver=user_resolver, mailbox_resolver=bad_mailbox
        )
        self.assertEqual(result["error"]["code"], "forbidden")

    def test_missing_mailbox_and_config_outage_use_safe_typed_errors(self):
        for store_status, expected in (("not_found", "mailbox_not_found"), ("unavailable", "storage_unavailable")):
            result = resolve_internal_collaboration_context(
                {}, "mailbox-1", user_resolver=user_resolver,
                mailbox_resolver=lambda _headers, _id, status=store_status: {
                    "status": status, "user": None, "inbox": None, "config": None,
                    "error": {"message": "raw provider detail must not escape"},
                },
            )
            self.assertEqual(result["error"], {"code": expected})

    def test_browser_mailbox_ownership_fails_before_thread_storage(self):
        calls = []
        result = resolve_internal_collaboration_context(
            {}, "mailbox-forged", collaboration_id="A" * 22,
            user_resolver=user_resolver,
            mailbox_resolver=lambda *_args: {"status": "not_found", "user": None, "inbox": None},
            thread_loader=lambda *_args: calls.append("thread"),
        )
        self.assertEqual(result["error"]["code"], "mailbox_not_found")
        self.assertEqual(calls, [])


class CollaborationV2ParticipantAuthorizationTests(unittest.TestCase):
    workspace_id = "wsp_" + "W" * 22
    owner_user_id = "usr_" + "A" * 22
    participant_user_id = "usr_" + "B" * 21 + "A"

    class Member:
        def __init__(self, *, user_id: str, workspace_id: str | None = None):
            self.user_id = user_id
            self.email = "participant@example.com"
            self.name = "Participant"
            self.workspace_id = workspace_id or CollaborationV2ParticipantAuthorizationTests.workspace_id
            self.auth_source = "auth0"
            self.user_type = "member"

    def setUp(self):
        self.context = SimpleNamespace(
            owner_email="participant@example.com",
            workspace_id=self.workspace_id,
            display_name="Participant",
        )
        self.thread = {
            "v": 2,
            "collaborationId": "P" * 22,
            "ownerEmail": "owner@example.com",
            "workspaceId": self.workspace_id,
            "mailboxId": "owner.mailbox",
            "sourceRef": {"provider": "google", "providerMessageId": "message-1"},
            "sourceMessage": {
                "subject": "Review",
                "senderDisplay": "Sender",
                "fromDisplay": "sender@example.com",
                "timestamp": "today",
                "bodyText": "Body",
            },
            "state": "needs_review",
            "messages": [],
            "createdAt": MS,
            "updatedAt": MS,
            "ownerUserId": self.owner_user_id,
            "ownerDisplayName": "Owner",
            "participants": [
                {
                    "userId": self.participant_user_id,
                    "membershipRef": "tinv_original",
                    "displayName": "Participant",
                }
            ],
        }
        self.member = self.Member(user_id=self.participant_user_id)
        self.security = SimpleNamespace(
            _is_owner_context=lambda value: value is self.context,
            owner_is_allowlisted=lambda _context, _configuration: True,
            mailbox_is_allowlisted=lambda *_args: False,
            OwnerSecurityError=RuntimeError,
        )
        self.runtime = SimpleNamespace(AuthenticatedMemberContext=self.Member)

    def resolve(self, *, action="read", member=None, team_result=None, context=None):
        selected_context = self.context if context is None else context
        selected_member = self.member if member is None else member
        selected_team_result = team_result or (
            {
                "memberUserId": self.participant_user_id,
                "displayName": "Participant",
                "accessLevel": "Shared",
                "sourceInvitationId": "tinv_original",
            },
            None,
        )
        with patch.object(
            authorization.importlib,
            "import_module",
            side_effect=lambda name: (
                self.security
                if name == "api.collaboration.owner_request_security"
                else self.runtime
            ),
        ):
            return resolve_verified_owner_collaboration_context(
                selected_context,
                (("cookie", "opaque"),),
                collaboration_id="P" * 22,
                required_action=action,
                owner_security_configuration=object(),
                mailbox_resolver=lambda *_args: (_ for _ in ()).throw(
                    AssertionError("participant must not resolve source mailbox")
                ),
                thread_loader=lambda _id: {"status": "ok", "record": self.thread},
                member_resolver=lambda _headers: (selected_member, None),
                team_member_resolver=lambda _workspace, _user: selected_team_result,
            )

    def test_explicit_current_participant_gets_direct_read_and_write_capabilities(self):
        for action in ("read", "reply", "internal_note"):
            result = self.resolve(action=action)
            self.assertEqual(result["status"], "ok")
            capability = result["context"]
            self.assertEqual(capability.viewer_access, "participant")
            self.assertEqual(capability.actor_kind, "internal")
            self.assertEqual(capability.actor_user_id, self.participant_user_id)
            self.assertEqual(capability.mailbox_id, "owner.mailbox")

    def test_nonparticipant_removed_reinvite_and_cross_workspace_fail_closed(self):
        nonparticipant = self.Member(user_id="usr_" + "C" * 21 + "A")
        self.assertEqual(self.resolve(member=nonparticipant)["error"], {"code": "forbidden"})
        self.assertEqual(
            self.resolve(team_result=(None, "not_active"))["error"],
            {"code": "forbidden"},
        )
        reinvited = (
            {
                "memberUserId": self.participant_user_id,
                "displayName": "Participant",
                "accessLevel": "Shared",
                "sourceInvitationId": "tinv_new",
            },
            None,
        )
        self.assertEqual(
            self.resolve(team_result=reinvited)["error"],
            {"code": "forbidden"},
        )
        other_context = SimpleNamespace(
            owner_email="participant@example.com",
            workspace_id="wsp_" + "X" * 22,
            display_name="Participant",
        )
        self.security._is_owner_context = lambda value: value is other_context
        self.assertEqual(
            self.resolve(context=other_context)["error"],
            {"code": "forbidden"},
        )

    def test_participant_cannot_manage_people(self):
        self.assertEqual(
            self.resolve(action="manage_participants")["error"],
            {"code": "forbidden"},
        )

    def test_unknown_action_is_rejected_before_resolvers_are_invoked(self):
        calls = []
        result = resolve_internal_collaboration_context(
            {}, "mailbox-1", required_action="delete_mailbox",
            user_resolver=lambda _headers: calls.append("auth"),
            mailbox_resolver=mailbox_resolver,
        )
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
