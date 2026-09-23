"""Tests for account-level Gmail history cursor retrieval."""

from __future__ import annotations

import unittest

from cuevion_mailbox.gmail_history import read_gmail_account_history


class GmailAccountHistoryTests(unittest.TestCase):
    def _context(self):
        return {
            "mailbox_email": "Verified@Gmail.com",
            "mailbox_id": "gmail-1",
            "refresh_attempted": False,
        }

    def test_reads_exact_profile_and_binds_history_to_mailbox_identity(self):
        calls = []

        def request(context, path):
            calls.append((context, path))
            return (
                {
                    "emailAddress": "verified@gmail.com",
                    "historyId": "123456789",
                },
                None,
                {**context, "access_token": "refreshed"},
                None,
            )

        result = read_gmail_account_history(
            self._context(),
            request_with_one_refresh=request,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.history_id, "123456789")
        self.assertEqual(result.context["access_token"], "refreshed")
        self.assertEqual(calls[0][1], "/profile")

    def test_profile_identity_mismatch_or_invalid_history_fails_closed(self):
        payloads = (
            {
                "emailAddress": "other@gmail.com",
                "historyId": "123",
            },
            {
                "emailAddress": "verified@gmail.com",
                "historyId": "",
            },
            {
                "emailAddress": "verified@gmail.com",
                "historyId": "12x",
            },
            {
                "emailAddress": "verified@gmail.com",
                "historyId": "1" * 129,
            },
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                result = read_gmail_account_history(
                    self._context(),
                    request_with_one_refresh=lambda context, path, payload=payload: (
                        payload,
                        None,
                        context,
                        None,
                    ),
                )
                self.assertEqual(result.status, "invalid")
                self.assertIsNone(result.history_id)

    def test_provider_or_refresh_failure_is_unavailable_not_fabricated(self):
        for response in (
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
                {"status_code": 401},
            ),
        ):
            with self.subTest(response=response):
                result = read_gmail_account_history(
                    self._context(),
                    request_with_one_refresh=lambda _context, _path, response=response: response,
                )
                self.assertEqual(result.status, "unavailable")
                self.assertIsNone(result.history_id)


if __name__ == "__main__":
    unittest.main()
