from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest

from . import event_reference as event_reference_module
from .event_reference import (
    EVENT_REFERENCE_TTL_SECONDS,
    MAX_AUTHORED_TEXT_CHARACTERS,
    EventReferenceError,
    authored_text_matches,
    issue_outgoing_event_reference,
    verify_outgoing_event_reference,
)


def _account(prefix: str, byte: int) -> str:
    suffix = base64.urlsafe_b64encode(bytes([byte]) * 16).rstrip(b"=").decode("ascii")
    return prefix + suffix


SECRET = "priority-test-secret-with-more-than-thirty-two-bytes"
WORKSPACE_ID = _account("wsp_", 1)
USER_ID = _account("usr_", 2)


def _issue(*, now: int = 1_000, text: str = "  Sent\r\nthis.  ") -> str:
    return issue_outgoing_event_reference(
        secret=SECRET,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        mailbox_id="mailbox-1",
        provider="google",
        conversation_id="thread:mailbox-1|gmail:mailbox-1:thread-1",
        provider_conversation_id="thread-1",
        latest_turn_id="message-2",
        authored_text=text,
        occurred_at=now * 1_000,
        semantic_version="priority-semantic-state-v1",
        now=now,
    )


class EventReferenceTests(unittest.TestCase):
    def test_round_trip_binds_bounded_normalized_authored_text(self):
        reference = _issue()
        claims = verify_outgoing_event_reference(reference, secret=SECRET, now=1_001)

        self.assertEqual(claims.workspace_id, WORKSPACE_ID)
        self.assertEqual(claims.user_id, USER_ID)
        self.assertEqual(claims.provider_conversation_id, "thread-1")
        self.assertTrue(authored_text_matches(claims, "Sent\nthis."))
        self.assertFalse(authored_text_matches(claims, "Sent something else."))
        self.assertNotIn("Sent", reference)

    def test_tamper_and_wrong_secret_fail_without_exposing_values(self):
        reference = _issue()
        tampered = reference[:-1] + ("A" if reference[-1] != "A" else "B")
        with self.assertRaises(EventReferenceError):
            verify_outgoing_event_reference(tampered, secret=SECRET, now=1_001)
        with self.assertRaises(EventReferenceError):
            verify_outgoing_event_reference(
                reference,
                secret="another-priority-secret-that-is-long-enough",
                now=1_001,
            )

    def test_reference_is_valid_through_waiting_horizon_and_expires_at_boundary(self):
        reference = _issue()
        claims = verify_outgoing_event_reference(
            reference,
            secret=SECRET,
            now=1_000 + EVENT_REFERENCE_TTL_SECONDS - 1,
        )
        self.assertEqual(claims.expires_at, 1_000 + EVENT_REFERENCE_TTL_SECONDS)

        with self.assertRaises(EventReferenceError) as captured:
            verify_outgoing_event_reference(
                reference,
                secret=SECRET,
                now=1_000 + EVENT_REFERENCE_TTL_SECONDS,
            )
        self.assertEqual(captured.exception.code, "stale_event_ref")

    def test_oversize_authored_text_is_rejected_instead_of_hashing_a_prefix(self):
        with self.assertRaises(EventReferenceError):
            _issue(text="x" * (MAX_AUTHORED_TEXT_CHARACTERS + 1))

    def test_custom_imap_mint_and_precontainment_signed_reference_are_rejected(self):
        with self.assertRaises(EventReferenceError):
            issue_outgoing_event_reference(
                secret=SECRET,
                workspace_id=WORKSPACE_ID,
                user_id=USER_ID,
                mailbox_id="mailbox-1",
                provider="custom_imap",
                conversation_id="thread:mailbox-1|imap:rfc:mailbox-1:root%40example.net",
                provider_conversation_id="root@example.net",
                latest_turn_id="sent@example.net",
                authored_text="Done.",
                occurred_at=1_000_000,
                semantic_version="priority-semantic-state-v1",
                now=1_000,
            )

        legacy_payload = {
            "schemaVersion": 1,
            "workspaceId": WORKSPACE_ID,
            "userId": USER_ID,
            "mailboxId": "mailbox-1",
            "provider": "custom_imap",
            "conversationId": (
                "thread:mailbox-1|imap:rfc:mailbox-1:root%40example.net"
            ),
            "providerConversationId": "root@example.net",
            "latestTurnId": "sent@example.net",
            "anchorProviderFolder": "INBOX",
            "anchorUidValidity": "7",
            "anchorImapUid": "8",
            "authoredTextDigest": hashlib.sha256(b"Done.").hexdigest(),
            "occurredAt": 1_000_000,
            "issuedAt": 1_000,
            "expiresAt": 1_000 + EVENT_REFERENCE_TTL_SECONDS,
            "semanticVersion": "priority-semantic-state-v1",
        }
        encoded_payload = event_reference_module._base64url_encode(
            json.dumps(
                legacy_payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        signing_input = (
            f"{event_reference_module.EVENT_REFERENCE_PREFIX}.{encoded_payload}"
        ).encode("ascii")
        signature = event_reference_module._base64url_encode(
            hmac.new(
                event_reference_module.derive_priority_hmac_key(
                    SECRET,
                    event_reference_module._HMAC_INFO,
                ),
                signing_input,
                hashlib.sha256,
            ).digest()
        )
        legacy_reference = (
            f"{event_reference_module.EVENT_REFERENCE_PREFIX}."
            f"{encoded_payload}.{signature}"
        )
        with self.assertRaises(EventReferenceError):
            verify_outgoing_event_reference(
                legacy_reference,
                secret=SECRET,
                now=1_001,
            )


if __name__ == "__main__":
    unittest.main()
