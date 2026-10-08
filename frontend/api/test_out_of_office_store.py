from __future__ import annotations

import unittest
from datetime import datetime, timezone

from api.out_of_office_store import (
    OUT_OF_OFFICE_ACTIVE_INDEX_KEY,
    OutOfOfficeStore,
    OutOfOfficeStoreUnavailable,
    OutOfOfficeValidationError,
    decode_active_target,
    default_out_of_office_settings,
    is_out_of_office_active,
    normalize_out_of_office_settings,
)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.sets = {}
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        operation = command[0]
        if operation == "GET":
            return {"result": self.values.get(command[1])}
        if operation == "SMEMBERS":
            return {"result": sorted(self.sets.get(command[1], set()))}
        if operation == "EVAL":
            key_count = command[2]
            if key_count == 2:
                (
                    _,
                    _script,
                    _,
                    config_key,
                    active_key,
                    encoded,
                    enabled,
                    target,
                ) = command
                self.values[config_key] = encoded
                members = self.sets.setdefault(active_key, set())
                if enabled == "1":
                    members.add(target)
                else:
                    members.discard(target)
                return {"result": 1}
            if key_count == 3:
                (
                    _,
                    _script,
                    _,
                    config_key,
                    cursor_key,
                    active_key,
                    encoded_settings,
                    encoded_cursor,
                    target,
                ) = command
                self.values[config_key] = encoded_settings
                self.values[cursor_key] = encoded_cursor
                self.sets.setdefault(active_key, set()).add(target)
                return {"result": 1}
            raise AssertionError(command)
        raise AssertionError(command)
        raise AssertionError(command)


class OutOfOfficeStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)

    def test_default_is_disabled(self):
        settings = default_out_of_office_settings(now=self.now)
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["subject"], "")
        self.assertEqual(settings["message"], "")

    def test_enabled_requires_subject_and_message(self):
        with self.assertRaises(OutOfOfficeValidationError):
            normalize_out_of_office_settings(
                {"enabled": True, "subject": "", "message": "Away"},
                now=self.now,
            )
        with self.assertRaises(OutOfOfficeValidationError):
            normalize_out_of_office_settings(
                {"enabled": True, "subject": "Away", "message": ""},
                now=self.now,
            )

    def test_time_window_must_be_timezone_aware_and_ordered(self):
        with self.assertRaises(OutOfOfficeValidationError):
            normalize_out_of_office_settings(
                {
                    "enabled": False,
                    "startsAt": "2026-10-08T10:00:00",
                    "endsAt": None,
                    "subject": "",
                    "message": "",
                },
                now=self.now,
            )
        with self.assertRaises(OutOfOfficeValidationError):
            normalize_out_of_office_settings(
                {
                    "enabled": False,
                    "startsAt": "2026-10-09T10:00:00Z",
                    "endsAt": "2026-10-08T10:00:00Z",
                    "subject": "",
                    "message": "",
                },
                now=self.now,
            )

    def test_active_window(self):
        settings = normalize_out_of_office_settings(
            {
                "enabled": True,
                "startsAt": "2026-10-08T09:00:00Z",
                "endsAt": "2026-10-08T12:00:00Z",
                "subject": "Out of office",
                "message": "I am away.",
            },
            now=self.now,
        )
        self.assertTrue(is_out_of_office_active(settings, now=self.now))
        self.assertFalse(
            is_out_of_office_active(
                settings,
                now=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
            )
        )

    def test_put_updates_record_and_active_index_atomically(self):
        redis = FakeRedis()
        store = OutOfOfficeStore(redis)
        settings = normalize_out_of_office_settings(
            {
                "enabled": True,
                "startsAt": None,
                "endsAt": None,
                "subject": "Away",
                "message": "Back soon.",
            },
            now=self.now,
        )
        store.put("Owner@Example.com", "mailbox-1", settings)
        self.assertEqual(redis.calls[-1][0], "EVAL")
        self.assertEqual(redis.calls[-1][4], OUT_OF_OFFICE_ACTIVE_INDEX_KEY)
        self.assertEqual(
            store.list_active_targets(),
            [{"ownerEmail": "owner@example.com", "mailboxId": "mailbox-1"}],
        )

        disabled = normalize_out_of_office_settings(
            {
                "enabled": False,
                "startsAt": None,
                "endsAt": None,
                "subject": "Away",
                "message": "Back soon.",
            },
            now=self.now,
        )
        store.put("owner@example.com", "mailbox-1", disabled)
        self.assertEqual(store.list_active_targets(), [])

    def test_get_round_trip_preserves_stored_updated_at(self):
        redis = FakeRedis()
        store = OutOfOfficeStore(redis)
        settings = normalize_out_of_office_settings(
            {
                "enabled": True,
                "startsAt": None,
                "endsAt": None,
                "subject": "Away",
                "message": "Back soon.",
            },
            now=self.now,
        )
        store.put("owner@example.com", "mailbox-1", settings)
        self.assertEqual(store.get("owner@example.com", "mailbox-1"), settings)

    def test_put_with_cursor_atomically_activates_target(self):
        redis = FakeRedis()
        store = OutOfOfficeStore(redis)
        settings = normalize_out_of_office_settings(
            {
                "enabled": True,
                "startsAt": None,
                "endsAt": None,
                "activatedAt": "2026-10-08T10:00:00Z",
                "subject": "Away",
                "message": "Back soon.",
            },
            now=self.now,
        )
        from api.out_of_office_store import build_gmail_out_of_office_cursor
        cursor = build_gmail_out_of_office_cursor("123", now=self.now)
        store.put_with_cursor("owner@example.com", "mailbox-1", settings, cursor)
        command = redis.calls[-1]
        self.assertEqual(command[0], "EVAL")
        self.assertEqual(command[2], 3)
        self.assertIn("active", command[5])

    def test_malformed_active_target_fails_closed(self):
        redis = FakeRedis()
        redis.sets[OUT_OF_OFFICE_ACTIVE_INDEX_KEY] = {"not-json"}
        with self.assertRaises(OutOfOfficeStoreUnavailable):
            OutOfOfficeStore(redis).list_active_targets()

    def test_decode_active_target_rejects_extra_fields(self):
        self.assertIsNone(
            decode_active_target(
                '{"ownerEmail":"owner@example.com","mailboxId":"mailbox-1","extra":true}'
            )
        )


if __name__ == "__main__":
    unittest.main()
