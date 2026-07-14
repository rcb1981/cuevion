from __future__ import annotations

import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from .authorization import resolve_internal_collaboration_context

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
