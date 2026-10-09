"""Security boundary tests for authenticated owner Microsoft mailbox reads."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from api.auth.runtime import AuthenticatedMemberContext
from api.inboxes import microsoft_mailbox_access as access
from api.inboxes import microsoft_graph
from api.inboxes.microsoft_token_authority import (
    MicrosoftMailboxAuthority, MicrosoftTokenError,
)

ACCOUNT = "7bbd0415-c1ef-4182-9abf-adf94015acfd"
GEN = "v" * 43
MAILBOX_ID = "microsoft-artist-01"
OWNER = "owner@example.com"
HEADERS = {"Cookie": "fake-session"}


def valid_member(*, role="owner", email=OWNER, workspace="workspace-1"):
    return AuthenticatedMemberContext(
        user_id="user-1", email=email, name="Test Owner",
        workspace_id=workspace, membership_role=role,
    )


def mailbox(**overrides):
    return {
        "id": MAILBOX_ID,
        "email": "artist@example.com",
        "title": "Artist",
        "provider": "microsoft",
        "connected": True,
        "connectionStatus": "connected",
        "connectionMethod": "oauth",
        "oauthOwnerEmail": OWNER,
        "oauthOwnerUserId": "user-1",
        "oauthWorkspaceId": "workspace-1",
        "oauthAccountId": ACCOUNT,
        "oauthCredentialGeneration": GEN,
        "customImap": {"host": "", "port": "", "password": ""},
        **overrides,
    }


def resolved(*, member=None, box=None, status="ok", config=None, user=None):
    box = box if box is not None else mailbox()
    member = member or valid_member()
    user = user if user is not None else {"email": OWNER, "name": "Test Owner"}
    config = config if config is not None else {
        "email": OWNER,
        "managedInboxes": [copy.deepcopy(box)],
        "uiPreferences": {},
    }
    return {
        "status": status, "memberAuthority": member,
        "user": user, "inbox": box, "config": config,
        "error": None,
    }


class MailboxAuthorityFixture:
    def setUp(self):
        self.response = resolved()
        patcher = patch.object(
            access, "resolve_owned_managed_inbox_record",
            side_effect=self.resolve,
        )
        self.resolver = patcher.start()
        self.addCleanup(patcher.stop)

    def resolve(self, headers, mailbox_id, **options):
        self.assertIs(headers, HEADERS)
        self.assertEqual(mailbox_id, MAILBOX_ID)
        self.assertEqual(options, {"include_member_authority": True})
        return self.response

class OwnedMicrosoftMailboxTests(MailboxAuthorityFixture, unittest.TestCase):
    def test_exact_current_owner_and_mailbox_access(self):
        authority = access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)
        self.assertIsInstance(authority, MicrosoftMailboxAuthority)
        self.assertEqual(
            authority,
            MicrosoftMailboxAuthority(OWNER, MAILBOX_ID, "artist@example.com", ACCOUNT, GEN),
        )
        self.resolver.assert_called_once()

    def test_browser_id_selector_is_strictly_bounded(self):
        bad_ids = (None, 0, "", " ", "unexpected/path", "../secret", "bad?=x", "x"*161, True)
        for mailbox_id in bad_ids:
            with self.subTest(mailbox_id=mailbox_id):
                with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
                    access.resolve_owned_microsoft_mailbox(HEADERS, mailbox_id)
                self.assertEqual(caught.exception.code, "microsoft_mailbox_id_invalid")
        self.resolver.assert_not_called()

    def test_not_authenticated_and_not_found_are_distinct_safe_errors(self):
        for status, expected in (
            ("unauthorized", "microsoft_unauthorized"),
            ("not_found", "microsoft_mailbox_not_found"),
            ("malformed", "microsoft_mailbox_authority_unavailable"),
            ("unavailable", "microsoft_mailbox_authority_unavailable"),
        ):
            self.response = resolved(status=status)
            with self.subTest(status=status):
                with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
                    access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)
                self.assertEqual(caught.exception.code, expected)

    def test_callback_owner_does_not_authorize_member_role(self):
        for role in ("admin", "member"):
            self.response = resolved(member=valid_member(role=role))
            with self.subTest(role=role):
                with self.assertRaises(access.MicrosoftMailboxAccessError):
                    access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)

    def test_cross_owner_user_config_is_rejected(self):
        self.response = resolved(member=valid_member(email="other@example.com"))
        with self.assertRaises(access.MicrosoftMailboxAccessError):
            access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)

    def test_swapped_config_owner_is_rejected(self):
        data = resolved()
        data["config"]["email"] = "other@example.com"
        self.response = data
        with self.assertRaises(access.MicrosoftMailboxAccessError):
            access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)

    def test_owner_scoped_mailbox_owner_mismatch(self):
        self.response = resolved(box=mailbox(oauthOwnerEmail="someone@example.com"))
        with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
            access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")

    def test_microsoft_account_is_bound_to_exact_auth0_user_id(self):
        self.response = resolved(box=mailbox(oauthOwnerUserId="user-2"))
        with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
            access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")

    def test_microsoft_account_is_bound_to_exact_workspace_id(self):
        self.response = resolved(member=valid_member(workspace="workspace-2"))
        with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
            access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")

    def test_missing_workspace_or_auth0_account_binding_fails_closed(self):
        for changes in (
            {"oauthWorkspaceId": None},
            {"oauthOwnerUserId": None},
            {"oauthWorkspaceId": "workspace-2"},
            {"oauthOwnerUserId": "user-2"},
        ):
            self.response = resolved(box=mailbox(**changes))
            with self.subTest(changes=changes), self.assertRaises(access.MicrosoftMailboxAccessError):
                access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)

    def test_google_mailbox_with_spoofed_microsoft_fields_is_rejected(self):
        self.response = resolved(box=mailbox(provider="google"))
        with self.assertRaises(access.MicrosoftMailboxAccessError):
            access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)

    def test_pending_disabled_disconnected_reconnect_states_denied(self):
        for changes in (
            {"connected": False},
            {"connectionStatus": "authenticated_pending_activation"},
            {"connectionMethod": "imap"},
            {"oauthReconnectGeneration": "n" * 43},
        ):
            self.response = resolved(box=mailbox(**changes))
            with self.subTest(changes=changes), self.assertRaises(access.MicrosoftMailboxAccessError):
                access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)

    def test_absent_or_bogus_graph_identity_and_generation_denied(self):
        for changes in (
            {"oauthAccountId": None},
            {"oauthAccountId": "bad\nheader"},
            {"oauthAccountId": 4},
            {"oauthCredentialGeneration": "too-short"},
            {"oauthCredentialGeneration": None},
            {"oauthCredentialGeneration": True},
        ):
            self.response = resolved(box=mailbox(**changes))
            with self.subTest(changes=changes), self.assertRaises(access.MicrosoftMailboxAccessError):
                access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)

    def test_casefold_duplicate_mailbox_ids_fail_closed(self):
        box = mailbox()
        self.response = resolved(
            box=box, config={"email": OWNER, "managedInboxes": [box, mailbox(id=MAILBOX_ID.upper())]}
        )
        with self.assertRaises(access.MicrosoftMailboxAccessError):
            access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)

    def test_duplicate_mailbox_email_fails_closed(self):
        box = mailbox()
        self.response = resolved(
            box=box,
            config={"email": OWNER, "managedInboxes": [box, mailbox(id="other-mailbox")]},
        )
        with self.assertRaises(access.MicrosoftMailboxAccessError):
            access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)

    def test_stale_mailbox_snapshot_fails_closed(self):
        box = mailbox()
        self.response = resolved(
            box=box,
            config={"email": OWNER, "managedInboxes": [mailbox(title="Other title")]},
        )
        with self.assertRaises(access.MicrosoftMailboxAccessError):
            access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)

    def test_unexpected_resolver_exception_never_exposes_details(self):
        with patch.object(
            access, "resolve_owned_managed_inbox_record",
            side_effect=RuntimeError("password-and-private-data"),
        ):
            with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
                access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)
        self.assertEqual(caught.exception.code, "microsoft_mailbox_authority_unavailable")
        self.assertNotIn("password", str(caught.exception))

    def test_owner_server_authority_is_required(self):
        self.response = resolved()
        self.response.pop("memberAuthority")
        with self.assertRaises(access.MicrosoftMailboxAccessError):
            access.resolve_owned_microsoft_mailbox(HEADERS, MAILBOX_ID)


class OperationalMicrosoftGraphTests(MailboxAuthorityFixture, unittest.TestCase):
    # Downstream token and Graph operations cannot bypass mailbox ownership.
    def test_owned_list_uses_exact_authority_and_server_token(self):
        with patch.object(access, "ensure_owned_microsoft_access_token",
                          return_value={"provider": "microsoft", "access_token": "secure-token"}) as tokens, \
             patch.object(microsoft_graph, "list_microsoft_inbox", return_value={"messages": [], "hasMore": False}) as graph:
            result = access.list_owned_microsoft_inbox(HEADERS, MAILBOX_ID, page_size=4)
        self.assertEqual(result, {"messages": [], "hasMore": False})
        expected = MicrosoftMailboxAuthority(OWNER, MAILBOX_ID, "artist@example.com", ACCOUNT, GEN)
        tokens.assert_called_once_with(expected)
        graph.assert_called_once_with("secure-token", page_size=4)

    def test_owned_exact_read_uses_same_authority(self):
        with patch.object(access, "ensure_owned_microsoft_access_token",
                          return_value={"provider": "microsoft", "access_token": "secure-token"}), \
             patch.object(microsoft_graph, "get_microsoft_message", return_value={"id": "message-1"}) as graph:
            result = access.read_owned_microsoft_message(HEADERS, MAILBOX_ID, "message-1")
        self.assertEqual(result, {"id": "message-1"})
        graph.assert_called_once_with("secure-token", "message-1")

    def test_reconnect_during_graph_list_blocks_returning_old_messages(self):
        def graph_response(_token, *, page_size):
            self.response = resolved(box=mailbox(
                connected=False,
                connectionStatus="authenticated_pending_activation",
                oauthReconnectGeneration="r"*43,
            ))
            return {"messages": [{"id": "private-old-message"}], "hasMore": False}

        with patch.object(
            access, "ensure_owned_microsoft_access_token",
            return_value={"provider": "microsoft", "access_token": "secure-token"},
        ) as tokens, patch.object(
            microsoft_graph, "list_microsoft_inbox", side_effect=graph_response,
        ) as graph:
            with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
                access.list_owned_microsoft_inbox(HEADERS, MAILBOX_ID)
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")
        self.assertEqual(self.resolver.call_count, 2)
        tokens.assert_called_once()
        graph.assert_called_once()

    def test_account_switch_during_exact_graph_read_never_publishes_body(self):
        def graph_response(_token, _mid):
            self.response = resolved(box=mailbox(
                oauthCredentialGeneration="s"*43,
            ))
            return {"id": "old-message", "body": "private-old-data"}
        with patch.object(
            access, "ensure_owned_microsoft_access_token",
            return_value={"provider": "microsoft", "access_token": "secure-token"},
        ), patch.object(
            microsoft_graph, "get_microsoft_message", side_effect=graph_response,
        ):
            with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
                access.read_owned_microsoft_message(HEADERS, MAILBOX_ID, "old-message")
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")
        self.assertEqual(self.resolver.call_count, 2)

    def test_owner_removed_during_graph_fetch_cannot_publish_snapshot(self):
        def graph_response(_token, *, page_size):
            self.response = resolved(status="unauthorized")
            return {"messages": [{"id": "previously-owned"}], "hasMore": False}
        with patch.object(
            access, "ensure_owned_microsoft_access_token",
            return_value={"provider": "microsoft", "access_token": "secure-token"},
        ), patch.object(
            microsoft_graph, "list_microsoft_inbox", side_effect=graph_response,
        ):
            with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
                access.list_owned_microsoft_inbox(HEADERS, MAILBOX_ID)
        self.assertEqual(caught.exception.code, "microsoft_unauthorized")
        self.assertEqual(self.resolver.call_count, 2)

    def test_invalid_page_size_blocks_before_auth_or_network(self):
        for size in (0, 26, -1, "5", True):
            with self.subTest(size=size), self.assertRaises(access.MicrosoftMailboxAccessError):
                access.list_owned_microsoft_inbox(HEADERS, MAILBOX_ID, page_size=size)
        self.resolver.assert_not_called()

    def test_unowned_mailbox_never_requests_token_or_graph(self):
        self.response = resolved(member=valid_member(email="other@example.com"))
        with patch.object(access, "ensure_owned_microsoft_access_token") as token, \
             patch.object(microsoft_graph, "list_microsoft_inbox") as graph:
            with self.assertRaises(access.MicrosoftMailboxAccessError):
                access.list_owned_microsoft_inbox(HEADERS, MAILBOX_ID)
        token.assert_not_called()
        graph.assert_not_called()

    def test_expired_wrong_generation_token_never_calls_graph(self):
        with patch.object(access, "ensure_owned_microsoft_access_token",
                          side_effect=MicrosoftTokenError("microsoft_reconnect_required")), \
             patch.object(microsoft_graph, "get_microsoft_message") as graph:
            with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
                access.read_owned_microsoft_message(HEADERS, MAILBOX_ID, "message-1")
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")
        graph.assert_not_called()

    def test_invalid_returned_token_never_calls_graph(self):
        for record in (None, {"provider": "google", "access_token": "123"},
                       {"provider": "microsoft", "access_token": None}):
            with patch.object(access, "ensure_owned_microsoft_access_token",
                              return_value=record), \
                 patch.object(microsoft_graph, "list_microsoft_inbox") as graph:
                with self.subTest(record=record), self.assertRaises(access.MicrosoftMailboxAccessError):
                    access.list_owned_microsoft_inbox(HEADERS, MAILBOX_ID)
                graph.assert_not_called()


if __name__ == "__main__":
    unittest.main()
