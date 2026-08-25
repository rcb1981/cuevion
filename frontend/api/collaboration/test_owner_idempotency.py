from __future__ import annotations

import base64
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from . import application, authorization, models, mutations, redis_store


MS = 1_800_000_000_000
COLLABORATION_ID = "A" * 22
WORKSPACE_ID = "wsp_" + ("w" * 22)
IDEMPOTENCY_KEY = base64.urlsafe_b64encode(b"i" * 32).decode("ascii").rstrip("=")


def _capability(action: str = "reply"):
    return authorization._InternalCollaborationCapability(
        authorization._INTERNAL_CAPABILITY_SENTINEL,
        "owner@example.com",
        WORKSPACE_ID,
        "mailbox-1",
        "google",
        COLLABORATION_ID,
        action,
        "owner",
        "Owner Person",
    )


def _thread() -> dict:
    return {
        "v": 2,
        "collaborationId": COLLABORATION_ID,
        "ownerEmail": "owner@example.com",
        "workspaceId": WORKSPACE_ID,
        "mailboxId": "mailbox-1",
        "sourceRef": {"provider": "google", "providerMessageId": "gmail-1"},
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
    }


class OwnerIdempotencyContractTests(unittest.TestCase):
    def test_key_contract_is_exact_canonical_256_bit_base64url(self):
        self.assertEqual(
            models.normalize_v2_owner_idempotency_key(IDEMPOTENCY_KEY),
            IDEMPOTENCY_KEY,
        )
        for invalid in (
            None,
            b"i" * 32,
            "short",
            "A" * 42,
            "A" * 44,
            ("A" * 42) + "!",
            ("A" * 42) + "B",
        ):
            with self.subTest(invalid=invalid):
                self.assertIsNone(models.normalize_v2_owner_idempotency_key(invalid))

    def test_fingerprint_changes_for_every_canonical_authority_or_payload_field(self):
        capability = _capability()
        baseline = mutations._owner_mutation_fingerprint(
            capability, "Canonical text", "shared"
        )
        self.assertRegex(baseline or "", r"^[0-9a-f]{64}$")
        variants = (
            (replace(capability, owner_email="other@example.com"), "Canonical text", "shared"),
            (replace(capability, workspace_id="wsp_" + ("x" * 22)), "Canonical text", "shared"),
            (replace(capability, mailbox_id="mailbox-2"), "Canonical text", "shared"),
            (replace(capability, mailbox_provider="custom_imap"), "Canonical text", "shared"),
            (replace(capability, collaboration_id="B" * 22), "Canonical text", "shared"),
            (replace(capability, actor_display_name="Other Owner"), "Canonical text", "shared"),
            (_capability("internal_note"), "Canonical text", "internal"),
            (capability, "Different text", "shared"),
        )
        for changed_capability, text, visibility in variants:
            with self.subTest(
                action=changed_capability.action,
                text=text,
                visibility=visibility,
            ):
                self.assertNotEqual(
                    mutations._owner_mutation_fingerprint(
                        changed_capability, text, visibility
                    ),
                    baseline,
                )

    def test_owner_mutation_passes_server_fingerprint_and_recovers_exact_outcome(self):
        capability = _capability()
        committed_message = {
            "id": "M" * 22,
            "authorKind": "owner",
            "authorDisplayName": "Owner Person",
            "text": "Canonical text",
            "visibility": "shared",
            "createdAt": MS + 1,
        }
        calls: list[tuple[dict, int, dict]] = []

        def saver(replacement, expected, **kwargs):
            calls.append((replacement, expected, kwargs))
            return redis_store._V2OwnerAppendResult(
                committed_message,
                MS + 1,
                recovered=bool(len(calls) > 1),
            )

        loader = Mock(return_value={"status": "ok", "record": _thread()})
        with patch.object(mutations.time, "time_ns", return_value=(MS + 5) * 1_000_000):
            first = mutations.append_owner_v2_message_idempotently(
                capability,
                "Canonical text",
                visibility="shared",
                idempotency_key=IDEMPOTENCY_KEY,
                thread_loader=loader,
                thread_saver=saver,
            )
        with patch.object(mutations.time, "time_ns", return_value=(MS + 50) * 1_000_000):
            retry = mutations.append_owner_v2_message_idempotently(
                capability,
                "Canonical text",
                visibility="shared",
                idempotency_key=IDEMPOTENCY_KEY,
                thread_loader=loader,
                thread_saver=saver,
            )
        self.assertEqual(first, retry)
        self.assertEqual(first["message"]["id"], "M" * 22)
        self.assertEqual(first["updatedAt"], MS + 1)
        self.assertEqual(calls[0][2]["idempotency_key"], IDEMPOTENCY_KEY)
        self.assertEqual(calls[0][2]["action"], "reply")
        self.assertRegex(calls[0][2]["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(calls[0][2]["fingerprint"], calls[1][2]["fingerprint"])

    def test_missing_key_and_conflicting_reuse_fail_without_retry_guessing(self):
        loader = Mock(return_value={"status": "ok", "record": _thread()})
        saver = Mock()
        missing = mutations.append_owner_v2_message_idempotently(
            _capability(),
            "text",
            visibility="shared",
            idempotency_key=None,
            thread_loader=loader,
            thread_saver=saver,
        )
        self.assertEqual(missing["error"], {"code": "invalid_request"})
        loader.assert_not_called()
        saver.assert_not_called()

        saver.return_value = {
            "status": "conflict",
            "error": {"code": "idempotency_conflict"},
        }
        with patch.object(mutations.time, "time_ns", return_value=(MS + 1) * 1_000_000):
            conflict = mutations.append_owner_v2_message_idempotently(
                _capability(),
                "text",
                visibility="shared",
                idempotency_key=IDEMPOTENCY_KEY,
                thread_loader=loader,
                thread_saver=saver,
            )
        self.assertEqual(conflict["error"], {"code": "idempotency_conflict"})
        saver.assert_called_once()

    def test_verified_owner_application_uses_only_idempotent_mutation_adapter(self):
        capability = _capability()
        authorized = {"status": "ok", "context": capability, "error": None}
        mutation_result = {
            "status": "ok",
            "message": {
                "id": "M" * 22,
                "authorDisplayName": "Owner Person",
                "authorRole": "Cuevion user",
                "text": "Canonical text",
                "timestamp": MS + 1,
                "visibility": "shared",
            },
            "updatedAt": MS + 1,
            "error": None,
        }
        owner_context = object()
        configuration = object()
        with patch.object(
            application,
            "resolve_verified_owner_collaboration_context",
            return_value=authorized,
        ), patch.object(
            application,
            "_append_idempotent_v2_owner_message",
            return_value=mutation_result,
        ) as mutate, patch.object(
            application,
            "_append_internal_v2_message",
            side_effect=AssertionError("ordinary CAS must not serve verified owner HTTP"),
        ):
            result = application.append_v2_shared_message_for_verified_owner(
                owner_context,
                [("Authorization", "private")],
                COLLABORATION_ID,
                {"text": "Canonical text"},
                idempotency_key=IDEMPOTENCY_KEY,
                owner_security_configuration=configuration,
            )
        self.assertEqual(set(result), {"message", "updatedAt"})
        mutate.assert_called_once_with(
            capability,
            "Canonical text",
            idempotency_key=IDEMPOTENCY_KEY,
        )


if __name__ == "__main__":
    unittest.main()
