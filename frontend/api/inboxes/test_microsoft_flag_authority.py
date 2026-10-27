"""Owner-bound lease, permission and post-Graph confirmation tests."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from api.inboxes import microsoft_mailbox_access as access
from api.inboxes import microsoft_message_flags as flags
from api.inboxes.microsoft_token_authority import MicrosoftMailboxAuthority
from api.inboxes.microsoft_graph import MicrosoftGraphError

BOX = "microsoft-0123456789abcdefabcd12345678"
ID = "AAMkAGVm.987/"
AUTHORITY = MicrosoftMailboxAuthority(
    owner_email="owner@example.com",
    mailbox_id=BOX,
    email="artist@example.com",
    account_id="account-123",
    credential_generation="r" * 43,
)
TOKEN = "valid-microsoft-access-token-12345"
HEADERS = (("Cookie", "test-session"),)


class FlagAuthorityTests(unittest.TestCase):
    def setUp(self):
        from api.inboxes import microsoft_draft_attachment_executor
        lease_check = patch.object(microsoft_draft_attachment_executor, '_assert_lease')
        lease_check.start(); self.addCleanup(lease_check.stop)
        # Lease-only unit fixtures; real Compose/Send CAS barriers are exercised
        # in test_microsoft_release_blockers and test_microsoft_security_fixes4.
        compose_barrier = patch('api.inboxes.microsoft_compose_draft_ledger.assert_unblocked')
        compose_barrier.start(); self.addCleanup(compose_barrier.stop)
        from api.inboxes import microsoft_draft_send_index
        p = patch.object(microsoft_draft_send_index, 'assert_mutable')
        p.start(); self.addCleanup(p.stop)
        self.authority = AUTHORITY
        self.resolved = []
        self.lease_calls = []
        self.release_calls = []
        self.graph_calls = []
        self.scopes = "User.Read Mail.Read Mail.ReadWrite offline_access"
        self.lease_result = {"status": "acquired", "token": "l" * 43}
        self.release_result = {"status": "released"}
        self.after_preflight = None
        self.post_write = None

        def resolve(headers, mailbox):
            self.resolved.append((headers, mailbox))
            return self.authority

        def lease(owner, mailbox):
            self.lease_calls.append((owner, mailbox))
            return self.lease_result

        def release(owner, mailbox, token):
            self.release_calls.append((owner, mailbox, token))
            return self.release_result

        def graph(token, message_id, *, unread, before_write):
            self.graph_calls.append((token, message_id, unread))
            if self.after_preflight:
                self.after_preflight()
            before_write()
            if self.post_write:
                self.post_write()
            return {"providerMessageId": message_id, "unread": unread, "changed": True}

        for name, value in (
            ("resolve_owned_microsoft_mailbox", resolve),
            ("acquire_mailbox_mutation_lease", lease),
            ("release_mailbox_mutation_lease", release),
            ("ensure_owned_microsoft_access_token",
             lambda auth: {"provider": "microsoft", "access_token": TOKEN, "scope": self.scopes}),
        ):
            obj = patch.object(access, name, side_effect=value)
            obj.start()
            self.addCleanup(obj.stop)
        obj = patch.object(flags, "set_exact_microsoft_inbox_read", side_effect=graph)
        obj.start()
        self.addCleanup(obj.stop)

    def action(self, *, unread=False):
        return access.mark_owned_microsoft_inbox_read(
            HEADERS, BOX, ID, unread=unread,
        )

    def test_exact_owner_and_lease_enclose_graph_write(self):
        result = self.action(unread=True)
        self.assertEqual(result, {"providerMessageId": ID, "unread": True, "changed": True})
        self.assertEqual(self.lease_calls, [(AUTHORITY.owner_email, BOX)])
        self.assertEqual(self.release_calls, [(AUTHORITY.owner_email, BOX, "l" * 43)])
        self.assertEqual(self.graph_calls, [(TOKEN, ID, True)])
        self.assertEqual(len(self.resolved), 9)  # initial/token; Send + Compose authority checks twice; postwrite

    def test_missing_write_scope_rejected_and_lease_released(self):
        for scope in (
            "Mail.Read User.Read",
            "https://hostile.example/Mail.ReadWrite User.Read Mail.Read",
            "",
        ):
            self.scopes = scope
            self.lease_calls.clear()
            self.release_calls.clear()
            self.graph_calls.clear()
            with self.subTest(scope=scope), self.assertRaises(
                access.MicrosoftMailboxAccessError
            ) as caught:
                self.action()
            self.assertEqual(caught.exception.code, "microsoft_write_permission_required")
            self.assertEqual(len(self.lease_calls), 1)
            self.assertEqual(len(self.release_calls), 1)
            self.assertFalse(self.graph_calls)

    def test_granted_full_graph_scope_is_accepted(self):
        self.scopes = "openid https://graph.microsoft.com/Mail.ReadWrite Mail.Read User.Read offline_access"
        self.assertTrue(self.action()["changed"])

    def test_held_lease_does_not_use_credentials_or_graph(self):
        self.lease_result = {"status": "held"}
        with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
            self.action()
        self.assertEqual(caught.exception.code, "microsoft_mutation_in_progress")
        self.assertFalse(self.release_calls)
        self.assertFalse(self.graph_calls)

    def test_lease_store_unavailable_denies_graph(self):
        self.lease_result = {"status": "unavailable"}
        with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
            self.action()
        self.assertEqual(caught.exception.code, "microsoft_mailbox_unavailable")
        self.assertFalse(self.graph_calls)

    def test_malformed_lease_ack_fails_closed(self):
        self.lease_result = {"status": "acquired"}
        with self.assertRaises(access.MicrosoftMailboxAccessError):
            self.action()
        self.assertFalse(self.graph_calls)

    def test_owner_generation_changes_during_preflight_blocks_patch(self):
        self.after_preflight = lambda: setattr(
            self, "authority", MicrosoftMailboxAuthority(
                AUTHORITY.owner_email, AUTHORITY.mailbox_id,
                AUTHORITY.email, AUTHORITY.account_id, "s"*43,
            ),
        )
        with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
            self.action()
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")
        self.assertEqual(len(self.release_calls), 1)
        self.assertEqual(len(self.graph_calls), 1)

    def test_owner_changes_after_graph_write_no_success(self):
        self.post_write = lambda: setattr(
            self, "authority", MicrosoftMailboxAuthority(
                "other-owner@example.com", BOX, AUTHORITY.email,
                AUTHORITY.account_id, AUTHORITY.credential_generation,
            ),
        )
        with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
            self.action()
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")
        self.assertEqual(len(self.release_calls), 1)

    def test_graph_unconfirmed_keeps_safe_error_and_releases_lease(self):
        with patch.object(
            flags, "set_exact_microsoft_inbox_read",
            side_effect=MicrosoftGraphError("microsoft_mutation_unconfirmed"),
        ):
            with self.assertRaises(MicrosoftGraphError) as caught:
                self.action()
        self.assertEqual(caught.exception.code, "microsoft_mutation_unconfirmed")
        self.assertEqual(len(self.release_calls), 1)

    def test_lost_lease_release_cannot_claim_confirmed(self):
        self.release_result = {"status": "not_owned"}
        with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
            self.action()
        self.assertEqual(caught.exception.code, "microsoft_mutation_unconfirmed")
        self.assertEqual(len(self.graph_calls), 1)

    def test_invalid_flag_no_lease_or_graph(self):
        for value in ("false", 0, 1, None):
            with self.subTest(value=value), self.assertRaises(access.MicrosoftMailboxAccessError):
                self.action(unread=value)
        self.assertFalse(self.lease_calls)

    def test_invalid_message_id_no_lease_or_graph(self):
        with self.assertRaises(MicrosoftGraphError):
            access.mark_owned_microsoft_inbox_read(HEADERS, BOX, "../bad", unread=False)
        self.assertFalse(self.lease_calls)


if __name__ == "__main__":
    unittest.main()
