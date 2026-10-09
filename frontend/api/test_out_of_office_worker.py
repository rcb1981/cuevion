from __future__ import annotations

import unittest
from datetime import datetime, timezone

from api.out_of_office_store import (
    build_gmail_out_of_office_cursor,
    normalize_out_of_office_settings,
)
from api.out_of_office_worker import (
    OutOfOfficeCursorReset,
    OutOfOfficeProviderError,
    process_out_of_office_target,
    should_auto_reply,
)


NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
TARGET = {"ownerEmail": "owner@example.com", "mailboxId": "mailbox-1"}


class FakeStore:
    def __init__(self, *, cursor=None, enabled=True):
        self.settings = normalize_out_of_office_settings(
            {
                "enabled": enabled,
                "startsAt": None,
                "endsAt": None,
                "activatedAt": "2026-10-08T09:59:00Z",
                "subject": "Out of office",
                "message": "Back soon.",
            },
            now=NOW,
        )
        self.cursor = cursor
        self.leased = False
        self.claims = {}
        self.put_cursor_calls = []

    def get(self, _owner, _mailbox):
        return self.settings

    def acquire_worker_lease(self, _owner, _mailbox):
        if self.leased:
            return None
        self.leased = True
        return "lease-token"

    def release_worker_lease(self, _owner, _mailbox, token):
        if token != "lease-token":
            return False
        self.leased = False
        return True

    def get_cursor(self, _owner, _mailbox):
        return self.cursor

    def put_cursor(self, _owner, _mailbox, cursor):
        self.cursor = cursor
        self.put_cursor_calls.append(cursor)

    def reserve_sender_reply(self, _owner, _mailbox, sender):
        if sender in self.claims:
            return None
        token = "claim-" + sender
        self.claims[sender] = token
        return token

    def complete_sender_reply(self, _owner, _mailbox, sender, token):
        return self.claims.get(sender) == token

    def release_sender_reply(self, _owner, _mailbox, sender, token):
        if self.claims.get(sender) != token:
            return False
        del self.claims[sender]
        return True


class FakeAdapter:
    provider = "google"

    def __init__(self):
        self.baseline_calls = 0
        self.fetch_calls = 0
        self.sent = []
        self.batch = {
            "cursor": build_gmail_out_of_office_cursor("11", now=NOW),
            "candidates": [],
        }
        self.reset_cursor = None
        self.send_error = None
        self.sent_copy_outcome = None

    def baseline(self, _mailbox, *, owner_email, now):
        self.baseline_calls += 1
        return build_gmail_out_of_office_cursor("10", now=now)

    def fetch_since(self, _mailbox, _cursor, *, owner_email, now):
        self.fetch_calls += 1
        if self.reset_cursor is not None:
            raise OutOfOfficeCursorReset(self.reset_cursor)
        return self.batch

    def send_reply(self, _mailbox, candidate, _settings, *, owner_email):
        if self.send_error:
            raise OutOfOfficeProviderError(self.send_error)
        self.sent.append(candidate["senderEmail"])
        return self.sent_copy_outcome


def mailbox():
    return {
        "id": "mailbox-1",
        "provider": "google",
        "email": "label@example.com",
    }


def candidate(sender="artist@example.com", **headers):
    return {
        "providerMessageId": "provider-1",
        "senderEmail": sender,
        "rfcMessageId": "<one@example.com>",
        "receivedAt": "2026-10-08T10:00:00Z",
        "headers": {key.lower(): value for key, value in headers.items()},
    }


class OutOfOfficeWorkerTests(unittest.TestCase):
    def test_first_run_only_baselines_and_sends_nothing(self):
        store = FakeStore(cursor=None)
        adapter = FakeAdapter()
        result = process_out_of_office_target(
            TARGET,
            store=store,
            load_mailbox=lambda _owner, _mailbox: mailbox(),
            resolve_adapter=lambda _mailbox: adapter,
            now=NOW,
        )
        self.assertEqual(result["status"], "baselined")
        self.assertEqual(adapter.baseline_calls, 1)
        self.assertEqual(adapter.fetch_calls, 0)
        self.assertEqual(adapter.sent, [])
        self.assertEqual(store.cursor["historyId"], "10")

    def test_provider_cursor_reset_rebaselines_without_sending(self):
        store = FakeStore(cursor=build_gmail_out_of_office_cursor("5", now=NOW))
        adapter = FakeAdapter()
        adapter.reset_cursor = build_gmail_out_of_office_cursor("20", now=NOW)
        result = process_out_of_office_target(
            TARGET,
            store=store,
            load_mailbox=lambda _owner, _mailbox: mailbox(),
            resolve_adapter=lambda _mailbox: adapter,
            now=NOW,
        )
        self.assertEqual(result["status"], "rebaselined")
        self.assertEqual(adapter.sent, [])
        self.assertEqual(store.cursor["historyId"], "20")

    def test_regular_inbound_sends_and_advances_cursor(self):
        store = FakeStore(cursor=build_gmail_out_of_office_cursor("10", now=NOW))
        adapter = FakeAdapter()
        adapter.batch["candidates"] = [candidate()]
        result = process_out_of_office_target(
            TARGET,
            store=store,
            load_mailbox=lambda _owner, _mailbox: mailbox(),
            resolve_adapter=lambda _mailbox: adapter,
            now=NOW,
        )
        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["sent"], 1)
        self.assertEqual(adapter.sent, ["artist@example.com"])
        self.assertEqual(store.cursor["historyId"], "11")

    def test_same_sender_is_suppressed_on_repeat_batch(self):
        store = FakeStore(cursor=build_gmail_out_of_office_cursor("10", now=NOW))
        adapter = FakeAdapter()
        adapter.batch["candidates"] = [candidate()]
        first = process_out_of_office_target(
            TARGET,
            store=store,
            load_mailbox=lambda _owner, _mailbox: mailbox(),
            resolve_adapter=lambda _mailbox: adapter,
            now=NOW,
        )
        self.assertEqual(first["sent"], 1)
        store.cursor = build_gmail_out_of_office_cursor("10", now=NOW)
        second = process_out_of_office_target(
            TARGET,
            store=store,
            load_mailbox=lambda _owner, _mailbox: mailbox(),
            resolve_adapter=lambda _mailbox: adapter,
            now=NOW,
        )
        self.assertEqual(second["sent"], 0)
        self.assertEqual(second["suppressed"], 1)

    def test_sent_append_failure_preserves_24h_suppression(self):
        store = FakeStore(cursor=build_gmail_out_of_office_cursor("10", now=NOW))
        adapter = FakeAdapter()
        adapter.batch["candidates"] = [candidate()]
        adapter.sent_copy_outcome = "sent_copy_failed"
        result = process_out_of_office_target(
            TARGET, store=store, load_mailbox=lambda *_args: mailbox(),
            resolve_adapter=lambda *_args: adapter, now=NOW,
        )
        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["sentCopyFailures"], 1)
        self.assertEqual(adapter.sent, ["artist@example.com"])
        self.assertIn("artist@example.com", store.claims)
        second = process_out_of_office_target(
            TARGET, store=store, load_mailbox=lambda *_args: mailbox(),
            resolve_adapter=lambda *_args: adapter, now=NOW,
        )
        self.assertEqual(second["sent"], 0)
        self.assertEqual(second["suppressed"], 1)
        self.assertEqual(adapter.sent, ["artist@example.com"])

    def test_send_failure_releases_sender_claim_and_keeps_old_cursor(self):
        old_cursor = build_gmail_out_of_office_cursor("10", now=NOW)
        store = FakeStore(cursor=old_cursor)
        adapter = FakeAdapter()
        adapter.batch["candidates"] = [candidate()]
        adapter.send_error = "provider_send_failed"
        result = process_out_of_office_target(
            TARGET,
            store=store,
            load_mailbox=lambda _owner, _mailbox: mailbox(),
            resolve_adapter=lambda _mailbox: adapter,
            now=NOW,
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "provider_send_failed")
        self.assertEqual(store.cursor, old_cursor)
        self.assertEqual(store.claims, {})

    def test_inactive_settings_do_not_acquire_provider(self):
        store = FakeStore(cursor=None, enabled=False)
        adapter = FakeAdapter()
        result = process_out_of_office_target(
            TARGET,
            store=store,
            load_mailbox=lambda _owner, _mailbox: self.fail("mailbox must not load"),
            resolve_adapter=lambda _mailbox: adapter,
            now=NOW,
        )
        self.assertEqual(result["status"], "inactive")

    def test_message_before_activation_is_skipped(self):
        store = FakeStore(cursor=build_gmail_out_of_office_cursor("10", now=NOW))
        adapter = FakeAdapter()
        item = candidate()
        item["receivedAt"] = "2026-10-08T09:58:59Z"
        adapter.batch["candidates"] = [item]
        result = process_out_of_office_target(
            TARGET,
            store=store,
            load_mailbox=lambda _owner, _mailbox: mailbox(),
            resolve_adapter=lambda _mailbox: adapter,
            now=NOW,
        )
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(adapter.sent, [])

    def test_automated_and_list_messages_are_skipped(self):
        blocked = [
            candidate(**{"Auto-Submitted": "auto-replied"}),
            candidate(**{"Precedence": "bulk"}),
            candidate(**{"List-Id": "<list.example.com>"}),
            candidate(**{"X-Auto-Response-Suppress": "All"}),
            candidate(sender="mailer-daemon@example.com"),
            candidate(sender="noreply@example.com"),
            candidate(**{"Return-Path": "<>"}),
        ]
        for item in blocked:
            with self.subTest(item=item):
                self.assertFalse(
                    should_auto_reply(item, mailbox_email="label@example.com")
                )

    def test_regular_human_sender_is_allowed(self):
        self.assertTrue(
            should_auto_reply(
                candidate(sender="artist@example.com", **{"Auto-Submitted": "no"}),
                mailbox_email="label@example.com",
            )
        )


if __name__ == "__main__":
    unittest.main()
