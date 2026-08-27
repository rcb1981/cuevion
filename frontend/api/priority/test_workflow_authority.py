from __future__ import annotations

import unittest

from .authority import parse_priority_message_identity


class PriorityWorkflowIdentityTests(unittest.TestCase):
    def test_valid_gmail_identity_uses_exact_provider_message_id(self):
        identity = parse_priority_message_identity(
            {"provider": "google", "providerMessageId": "18f5abc123"},
            expected_provider="google",
        )
        self.assertEqual(
            identity.to_wire_dict(),
            {"provider": "google", "providerMessageId": "18f5abc123"},
        )
        self.assertEqual(identity.canonical_bytes(), b"google\x0018f5abc123")

    def test_malformed_gmail_identity_is_rejected(self):
        malformed = (
            {"provider": "google", "providerMessageId": ""},
            {"provider": "google", "providerMessageId": " message "},
            {"provider": "google", "providerMessageId": "bad\x00message"},
            {
                "provider": "google",
                "providerMessageId": "message",
                "subject": "unsafe identity",
            },
        )
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_priority_message_identity(value)

    def test_valid_custom_imap_identity_uses_exact_safe_locator_tuple(self):
        identity = parse_priority_message_identity(
            {
                "provider": "custom_imap",
                "providerFolder": "INBOX/Projects",
                "uidValidity": "4294967295",
                "imapUid": "17",
            },
            expected_provider="custom_imap",
        )
        self.assertEqual(
            identity.to_wire_dict(),
            {
                "provider": "custom_imap",
                "providerFolder": "INBOX/Projects",
                "uidValidity": "4294967295",
                "imapUid": "17",
            },
        )
        self.assertEqual(
            identity.canonical_bytes(),
            b"custom_imap\x00INBOX/Projects\x004294967295\x0017",
        )

    def test_malformed_or_unsafe_custom_imap_identity_is_rejected(self):
        malformed = (
            {
                "provider": "custom_imap",
                "providerFolder": " INBOX",
                "uidValidity": "7",
                "imapUid": "11",
            },
            {
                "provider": "custom_imap",
                "providerFolder": "INBOX\nOther",
                "uidValidity": "7",
                "imapUid": "11",
            },
            {
                "provider": "custom_imap",
                "providerFolder": "INBOX",
                "uidValidity": "0",
                "imapUid": "11",
            },
            {
                "provider": "custom_imap",
                "providerFolder": "INBOX",
                "uidValidity": "7",
                "imapUid": "0042",
            },
            {
                "provider": "custom_imap",
                "providerFolder": "INBOX",
                "uidValidity": "7",
                "imapUid": "4294967296",
            },
            {
                "provider": "custom_imap",
                "providerFolder": "INBOX",
                "uidValidity": "7",
                "imapUid": "11",
                "rfcMessageId": "subject-derived@example.test",
            },
        )
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_priority_message_identity(value)

    def test_provider_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_priority_message_identity(
                {"provider": "google", "providerMessageId": "message"},
                expected_provider="custom_imap",
            )


if __name__ == "__main__":
    unittest.main()
