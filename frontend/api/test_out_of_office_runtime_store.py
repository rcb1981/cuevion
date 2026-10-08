from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from api.out_of_office_store import (
    OUT_OF_OFFICE_SUPPRESSION_TTL_SECONDS,
    OUT_OF_OFFICE_WORKER_LEASE_TTL_SECONDS,
    OutOfOfficeStore,
    OutOfOfficeStoreUnavailable,
    build_gmail_out_of_office_cursor,
    build_imap_out_of_office_cursor,
)


class RuntimeRedis:
    def __init__(self):
        self.values = {}
        self.expiries = {}

    def __call__(self, command):
        op = command[0]
        if op == "GET":
            return {"result": self.values.get(command[1])}
        if op == "SET":
            key = command[1]
            value = command[2]
            args = command[3:]
            if "NX" in args and key in self.values:
                return {"result": None}
            self.values[key] = value
            if "EX" in args:
                ex_index = args.index("EX")
                self.expiries[key] = args[ex_index + 1]
            return {"result": "OK"}
        if op == "EVAL":
            script = command[1]
            key_count = command[2]
            keys = command[3 : 3 + key_count]
            args = command[3 + key_count :]

            if "local current = redis.call('GET', KEYS[1])" in script:
                key = keys[0]
                token = args[0]
                if self.values.get(key) != token:
                    return {"result": 0}
                self.values.pop(key, None)
                self.expiries.pop(key, None)
                return {"result": 1}

            raise AssertionError(command)

        raise AssertionError(command)


class OutOfOfficeRuntimeStoreTests(unittest.TestCase):
    def setUp(self):
        self.redis = RuntimeRedis()
        self.store = OutOfOfficeStore(self.redis)
        self.now = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)

    def test_gmail_cursor_round_trip(self):
        cursor = build_gmail_out_of_office_cursor("123456789", now=self.now)
        self.store.put_cursor("owner@example.com", "mailbox-1", cursor)
        self.assertEqual(self.store.get_cursor("owner@example.com", "mailbox-1"), cursor)

    def test_imap_cursor_round_trip(self):
        cursor = build_imap_out_of_office_cursor("777", "42", now=self.now)
        self.store.put_cursor("owner@example.com", "mailbox-1", cursor)
        self.assertEqual(self.store.get_cursor("owner@example.com", "mailbox-1"), cursor)

    def test_malformed_cursor_fails_closed(self):
        key = next(iter(self.redis.values), None)
        self.assertIsNone(key)
        self.store.put_cursor(
            "owner@example.com",
            "mailbox-1",
            build_gmail_out_of_office_cursor("9", now=self.now),
        )
        key = next(iter(self.redis.values))
        self.redis.values[key] = json.dumps({"schemaVersion": 1, "provider": "google"})
        with self.assertRaises(OutOfOfficeStoreUnavailable):
            self.store.get_cursor("owner@example.com", "mailbox-1")

    def test_worker_lease_is_exclusive_and_token_guarded(self):
        first = self.store.acquire_worker_lease("owner@example.com", "mailbox-1")
        self.assertIsInstance(first, str)
        self.assertEqual(len(first), 43)
        self.assertIsNone(self.store.acquire_worker_lease("owner@example.com", "mailbox-1"))
        lease_key = next(key for key in self.redis.values if "worker-lease" in key)
        self.assertEqual(self.redis.expiries[lease_key], OUT_OF_OFFICE_WORKER_LEASE_TTL_SECONDS)
        self.assertFalse(self.store.release_worker_lease("owner@example.com", "mailbox-1", "wrong"))
        self.assertTrue(self.store.release_worker_lease("owner@example.com", "mailbox-1", first))
        self.assertIsNotNone(self.store.acquire_worker_lease("owner@example.com", "mailbox-1"))

    def test_sender_reply_reservation_claims_24_hour_suppression_before_send(self):
        token = self.store.reserve_sender_reply(
            "owner@example.com", "mailbox-1", "sender@example.com"
        )
        self.assertIsInstance(token, str)
        suppression_key = next(key for key in self.redis.values if ":suppression:" in key)
        self.assertEqual(
            self.redis.expiries[suppression_key], OUT_OF_OFFICE_SUPPRESSION_TTL_SECONDS
        )
        self.assertTrue(
            self.store.complete_sender_reply(
                "owner@example.com", "mailbox-1", "sender@example.com", token
            )
        )
        self.assertIsNone(
            self.store.reserve_sender_reply(
                "owner@example.com", "mailbox-1", "sender@example.com"
            )
        )

    def test_failed_send_can_release_reservation_for_retry(self):
        token = self.store.reserve_sender_reply(
            "owner@example.com", "mailbox-1", "sender@example.com"
        )
        self.assertTrue(
            self.store.release_sender_reply(
                "owner@example.com", "mailbox-1", "sender@example.com", token
            )
        )
        self.assertIsNotNone(
            self.store.reserve_sender_reply(
                "owner@example.com", "mailbox-1", "sender@example.com"
            )
        )


if __name__ == "__main__":
    unittest.main()
