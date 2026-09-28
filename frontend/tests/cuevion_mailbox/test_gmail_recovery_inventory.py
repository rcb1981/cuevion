"""Tests for bounded complete Gmail Inbox recovery inventory."""

from __future__ import annotations

import unittest

from cuevion_mailbox.gmail_recovery_inventory import (
    read_complete_gmail_inbox_recovery_inventory,
    read_gmail_inbox_bootstrap_page,
)


class GmailRecoveryInventoryTests(unittest.TestCase):
    def _context(self):
        return {
            "mailbox_email": "verified@gmail.com",
            "mailbox_id": "gmail-1",
            "refresh_attempted": False,
        }

    def test_bootstrap_page_is_small_and_resumable(self):
        calls = []
        result = read_gmail_inbox_bootstrap_page(
            self._context(), page_token="page-1",
            request_with_one_refresh=lambda context, path: (calls.append(path) or {"messages": [{"id": "message-1"}], "nextPageToken": "page-2"}, None, context, None),
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.provider_message_ids, ("message-1",))
        self.assertEqual(result.next_page_token, "page-2")
        self.assertIn("maxResults=10", calls[0])
        self.assertIn("pageToken=page-1", calls[0])

    def test_complete_single_page_inventory_is_usable(self):
        calls = []

        def request(context, path):
            calls.append((context, path))
            return (
                {
                    "messages": [
                        {"id": "message-1"},
                        {"id": "message-2"},
                    ]
                },
                None,
                {**context, "generation": 2},
                None,
            )

        result = read_complete_gmail_inbox_recovery_inventory(
            self._context(),
            request_with_one_refresh=request,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(
            result.provider_message_ids,
            ("message-1", "message-2"),
        )
        self.assertEqual(result.context["generation"], 2)
        self.assertEqual(
            calls[0][1],
            "/messages?labelIds=INBOX&maxResults=100",
        )

    def test_empty_complete_inbox_is_valid(self):
        result = read_complete_gmail_inbox_recovery_inventory(
            self._context(),
            request_with_one_refresh=lambda context, _path: (
                {},
                None,
                context,
                None,
            ),
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.provider_message_ids, ())

    def test_next_page_token_means_overflow_and_discards_partial_ids(self):
        result = read_complete_gmail_inbox_recovery_inventory(
            self._context(),
            request_with_one_refresh=lambda context, _path: (
                {
                    "messages": [{"id": "message-1"}],
                    "nextPageToken": "next-page",
                },
                None,
                context,
                None,
            ),
        )
        self.assertEqual(result.status, "overflow")
        self.assertEqual(result.provider_message_ids, ())

    def test_invalid_page_token_or_message_shape_fails_closed(self):
        payloads = (
            {"nextPageToken": ""},
            {"nextPageToken": 7},
            {"messages": "invalid"},
            {"messages": [None]},
            {"messages": [{"id": ""}]},
            {
                "messages": [
                    {"id": "message-1"},
                    {"id": "message-1"},
                ]
            },
            {"messages": [{"id": "bad id"}]},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                result = read_complete_gmail_inbox_recovery_inventory(
                    self._context(),
                    request_with_one_refresh=lambda context, _path, payload=payload: (
                        payload,
                        None,
                        context,
                        None,
                    ),
                )
                self.assertEqual(result.status, "invalid")
                self.assertEqual(result.provider_message_ids, ())

    def test_provider_or_refresh_failure_is_unavailable(self):
        cases = (
            (
                None,
                {"code": "gmail_unavailable"},
                self._context(),
                None,
            ),
            (
                None,
                {"code": "gmail_token_invalid"},
                self._context(),
                {"status_code": 503},
            ),
        )
        for response in cases:
            with self.subTest(response=response):
                result = read_complete_gmail_inbox_recovery_inventory(
                    self._context(),
                    request_with_one_refresh=lambda _context, _path, response=response: response,
                )
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.provider_message_ids, ())


if __name__ == "__main__":
    unittest.main()
