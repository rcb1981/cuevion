"""Tests for bounded Gmail History API delta discovery."""

from __future__ import annotations

import unittest

from cuevion_mailbox.gmail_history_delta import read_gmail_history_delta


class GmailHistoryDeltaTests(unittest.TestCase):
    def _context(self):
        return {
            "mailbox_email": "verified@gmail.com",
            "mailbox_id": "gmail-1",
            "refresh_attempted": False,
        }

    def test_single_page_collects_all_change_shapes_and_deduplicates_ids(self):
        calls = []

        def request(context, path):
            calls.append((context, path))
            return (
                {
                    "historyId": "1050",
                    "history": [
                        {
                            "id": "1020",
                            "messages": [{"id": "message-1"}],
                            "messagesAdded": [
                                {"message": {"id": "message-1"}},
                                {"message": {"id": "message-2"}},
                            ],
                            "labelsAdded": [
                                {"message": {"id": "message-3"}}
                            ],
                            "labelsRemoved": [
                                {"message": {"id": "message-2"}}
                            ],
                            "messagesDeleted": [
                                {"message": {"id": "message-4"}}
                            ],
                        }
                    ],
                },
                None,
                {**context, "access_token": "refreshed"},
                None,
            )

        result = read_gmail_history_delta(
            self._context(),
            start_history_id="1000",
            request_with_one_refresh=request,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.next_history_id, "1050")
        self.assertEqual(
            result.affected_message_ids,
            ("message-1", "message-2", "message-3", "message-4"),
        )
        self.assertEqual(result.page_count, 1)
        self.assertEqual(result.history_record_count, 1)
        self.assertEqual(result.context["access_token"], "refreshed")
        self.assertEqual(
            calls[0][1],
            "/history?startHistoryId=1000&maxResults=100",
        )

    def test_paginates_with_latest_context_and_only_final_cursor(self):
        calls = []

        def request(context, path):
            calls.append((context, path))
            if len(calls) == 1:
                return (
                    {
                        "historyId": "1100",
                        "history": [
                            {
                                "id": "1050",
                                "messages": [{"id": "message-1"}],
                            }
                        ],
                        "nextPageToken": "page+2",
                    },
                    None,
                    {**context, "generation": 2},
                    None,
                )
            return (
                {
                    "historyId": "1125",
                    "history": [
                        {
                            "id": "1110",
                            "labelsRemoved": [
                                {"message": {"id": "message-2"}}
                            ],
                        }
                    ],
                },
                None,
                {**context, "generation": 3},
                None,
            )

        result = read_gmail_history_delta(
            self._context(),
            start_history_id="1000",
            request_with_one_refresh=request,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.next_history_id, "1125")
        self.assertEqual(
            result.affected_message_ids,
            ("message-1", "message-2"),
        )
        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.history_record_count, 2)
        self.assertEqual(result.context["generation"], 3)
        self.assertEqual(calls[1][0]["generation"], 2)
        self.assertEqual(
            calls[1][1],
            "/history?startHistoryId=1000&maxResults=100&pageToken=page%2B2",
        )

    def test_empty_history_is_valid_and_can_advance_cursor(self):
        result = read_gmail_history_delta(
            self._context(),
            start_history_id="1000",
            request_with_one_refresh=lambda context, _path: (
                {"historyId": "1005"},
                None,
                context,
                None,
            ),
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.next_history_id, "1005")
        self.assertEqual(result.affected_message_ids, ())
        self.assertEqual(result.page_count, 1)
        self.assertEqual(result.history_record_count, 0)

    def test_stale_history_404_requires_full_sync_without_cursor_advance(self):
        result = read_gmail_history_delta(
            self._context(),
            start_history_id="1000",
            request_with_one_refresh=lambda context, _path: (
                None,
                {"code": "gmail_message_not_found"},
                context,
                None,
            ),
        )

        self.assertEqual(result.status, "full_sync_required")
        self.assertIsNone(result.next_history_id)
        self.assertEqual(result.affected_message_ids, ())

    def test_provider_and_refresh_failures_are_unavailable(self):
        responses = (
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
        )

        for response in responses:
            with self.subTest(response=response):
                result = read_gmail_history_delta(
                    self._context(),
                    start_history_id="1000",
                    request_with_one_refresh=lambda _context, _path, response=response: response,
                )
                self.assertEqual(result.status, "unavailable")
                self.assertIsNone(result.next_history_id)

    def test_invalid_or_rewound_provider_payload_fails_closed(self):
        payloads = (
            {"historyId": "999", "history": []},
            {"historyId": "1001", "history": "invalid"},
            {
                "historyId": "1001",
                "history": [{"id": "1002", "messages": [{"id": "message-1"}]}],
            },
            {
                "historyId": "1005",
                "history": [{"id": "1001", "messages": [{"id": ""}]}],
            },
        )

        for payload in payloads:
            with self.subTest(payload=payload):
                result = read_gmail_history_delta(
                    self._context(),
                    start_history_id="1000",
                    request_with_one_refresh=lambda context, _path, payload=payload: (
                        payload,
                        None,
                        context,
                        None,
                    ),
                )
                self.assertEqual(result.status, "invalid")
                self.assertIsNone(result.next_history_id)

    def test_more_than_one_hundred_affected_messages_returns_overflow(self):
        messages = [{"id": f"message-{index}"} for index in range(101)]
        result = read_gmail_history_delta(
            self._context(),
            start_history_id="1000",
            request_with_one_refresh=lambda context, _path: (
                {
                    "historyId": "1100",
                    "history": [{"id": "1050", "messages": messages}],
                },
                None,
                context,
                None,
            ),
        )

        self.assertEqual(result.status, "overflow")
        self.assertIsNone(result.next_history_id)
        self.assertEqual(result.page_count, 1)

    def test_repeated_page_token_fails_closed(self):
        calls = 0

        def request(context, _path):
            nonlocal calls
            calls += 1
            return (
                {
                    "historyId": str(1000 + calls),
                    "history": [],
                    "nextPageToken": "same-token",
                },
                None,
                context,
                None,
            )

        result = read_gmail_history_delta(
            self._context(),
            start_history_id="1000",
            request_with_one_refresh=request,
        )

        self.assertEqual(result.status, "invalid")
        self.assertIsNone(result.next_history_id)
        self.assertEqual(result.page_count, 2)


if __name__ == "__main__":
    unittest.main()
