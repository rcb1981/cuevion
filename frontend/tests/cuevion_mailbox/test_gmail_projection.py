"""Tests for pure Gmail snapshot to durable mailbox projection."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from cuevion_mailbox.gmail_projection import (
    derive_gmail_message_id,
    project_gmail_snapshot,
    project_gmail_snapshot_message,
)
from cuevion_mailbox.repository_contract import (
    BodyState,
    MailboxProvider,
    MailboxScope,
)


_FRONTEND = Path(__file__).resolve().parents[2]
_MODULE = _FRONTEND / "cuevion_mailbox" / "gmail_projection.py"


def _scope(*, generation: int = 1) -> MailboxScope:
    return MailboxScope(
        workspace_id="wsp_" + ("a" * 22),
        owner_user_id="usr_" + ("b" * 22),
        mailbox_id="gmail-1",
        source_generation=generation,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity="verified@gmail.com",
    )


def _preview(**overrides):
    return {
        "providerMessageId": "gmail-message-1",
        "providerThreadId": "gmail-thread-1",
        "providerFolder": "Inbox",
        "labelIds": ["UNREAD", "INBOX"],
        "rfcMessageId": "rfc-1@example.test",
        "to": "Owner <owner@example.test>",
        "cc": "",
        **overrides,
    }


def _source(**overrides):
    return {
        "provider": "google",
        "providerMessageId": "gmail-message-1",
        "providerThreadId": "gmail-thread-1",
        "providerFolder": "INBOX",
        "labels": ["INBOX", "UNREAD"],
        "providerTimestampMillis": "1790100000000",
        "senderDisplay": "Sender",
        "senderAddress": "sender@example.test",
        "subject": "Subject",
        "snippet": "Snippet",
        "unread": True,
        "flagged": False,
        **overrides,
    }


class GmailDurableProjectionTests(unittest.TestCase):
    def test_message_id_is_stable_bounded_and_generation_isolated(self):
        first = derive_gmail_message_id(_scope(), "gmail-message-1")
        again = derive_gmail_message_id(_scope(), "gmail-message-1")
        next_generation = derive_gmail_message_id(
            _scope(generation=2),
            "gmail-message-1",
        )
        self.assertEqual(first, again)
        self.assertNotEqual(first, next_generation)
        self.assertEqual(len(first), 26)
        self.assertTrue(first.startswith("mbm_"))

    def test_projection_is_canonical_and_body_remains_uncached(self):
        record = project_gmail_snapshot_message(
            _scope(),
            _preview(),
            _source(),
        )
        self.assertEqual(record.identity.provider_message_id, "gmail-message-1")
        self.assertEqual(record.identity.provider_folder, "INBOX")
        self.assertEqual(record.provider_labels, ("INBOX", "UNREAD"))
        self.assertEqual(record.provider_thread_id, "gmail-thread-1")
        self.assertEqual(record.sender_address, "sender@example.test")
        self.assertEqual(record.sender_display, "Sender")
        self.assertEqual(record.subject, "Subject")
        self.assertEqual(record.snippet, "Snippet")
        self.assertEqual(record.to_recipients, ("Owner <owner@example.test>",))
        self.assertEqual(record.cc_recipients, ())
        self.assertEqual(record.provider_timestamp_millis, 1790100000000)
        self.assertTrue(record.unread)
        self.assertFalse(record.starred)
        self.assertIs(record.body_state, BodyState.NOT_CACHED)
        self.assertRegex(record.metadata_hash, r"^[0-9a-f]{64}$")

    def test_empty_display_name_and_snippet_are_valid(self):
        record = project_gmail_snapshot_message(
            _scope(),
            _preview(),
            _source(senderDisplay="", snippet=""),
        )
        self.assertIsNone(record.sender_display)
        self.assertEqual(record.snippet, "")

    def test_label_order_does_not_change_metadata_hash(self):
        first = project_gmail_snapshot_message(
            _scope(),
            _preview(labelIds=["UNREAD", "INBOX"]),
            _source(labels=["INBOX", "UNREAD"]),
        )
        second = project_gmail_snapshot_message(
            _scope(),
            _preview(labelIds=["INBOX", "UNREAD"]),
            _source(labels=["UNREAD", "INBOX"]),
        )
        self.assertEqual(first.metadata_hash, second.metadata_hash)

    def test_batch_preserves_preview_order_and_ignores_unselected_source_rows(self):
        first_preview = _preview()
        second_preview = _preview(
            providerMessageId="gmail-message-2",
            providerThreadId="gmail-thread-2",
        )
        first_source = _source()
        second_source = _source(
            providerMessageId="gmail-message-2",
            providerThreadId="gmail-thread-2",
        )
        extra_source = _source(
            providerMessageId="gmail-message-filtered",
            providerThreadId="gmail-thread-filtered",
        )
        records = project_gmail_snapshot(
            _scope(),
            [second_preview, first_preview],
            [first_source, extra_source, second_source],
        )
        self.assertEqual(
            [record.identity.provider_message_id for record in records],
            ["gmail-message-2", "gmail-message-1"],
        )

    def test_mismatched_or_duplicate_provider_authority_fails_closed(self):
        cases = (
            (
                _preview(providerMessageId="other"),
                _source(),
            ),
            (
                _preview(providerThreadId="other-thread"),
                _source(),
            ),
            (
                _preview(providerFolder="Archive"),
                _source(),
            ),
            (
                _preview(labelIds=["INBOX"]),
                _source(),
            ),
        )
        for preview, source in cases:
            with self.subTest(preview=preview):
                with self.assertRaises(ValueError):
                    project_gmail_snapshot_message(_scope(), preview, source)

        with self.assertRaises(ValueError):
            project_gmail_snapshot(
                _scope(),
                [_preview()],
                [_source(), _source()],
            )

    def test_database_bounds_fail_before_any_writer_exists(self):
        with self.assertRaises(ValueError):
            project_gmail_snapshot_message(
                _scope(),
                _preview(),
                _source(senderAddress=("a" * 321)),
            )
        with self.assertRaises(ValueError):
            project_gmail_snapshot_message(
                _scope(),
                _preview(),
                _source(providerMessageId=("x" * 1025)),
            )
        with self.assertRaises(ValueError):
            project_gmail_snapshot_message(
                _scope(),
                _preview(),
                _source(providerTimestampMillis="-1"),
            )


class GmailDurableProjectionStaticTests(unittest.TestCase):
    def test_projection_module_has_no_runtime_or_io_boundary(self):
        source = _MODULE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        for forbidden in (
            "os",
            "socket",
            "urllib",
            "requests",
            "httpx",
            "psycopg",
        ):
            self.assertNotIn(forbidden, imported)
        for forbidden in (
            "commit_provider_delta",
            "build_shadow_mailbox_repositories",
            "build_active_read_mailbox_reader",
            "DATABASE_URL",
            "os.environ",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
