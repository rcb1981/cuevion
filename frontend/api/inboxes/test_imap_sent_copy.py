from __future__ import annotations

import unittest
from email.message import EmailMessage

from api.inboxes.imap_sent_copy import (
    SentCopyStorageError,
    append_sent_copy,
    find_sent_folder,
    select_sent_folder,
)


class FakeSentImap:
    def __init__(self, rows=None, search_uids=b"", append_status="OK", select_status="OK"):
        self.rows = [b'(\\Sent \\HasNoChildren) "/" "Sent Messages"'] if rows is None else rows
        self.search_uids = search_uids
        self.append_status = append_status
        self.select_status = select_status
        self.calls = []
        self.appended = []

    def list(self):
        self.calls.append("LIST")
        return "OK", self.rows

    def select(self, folder, readonly=False):
        self.calls.append(("SELECT", folder, readonly))
        return self.select_status, [b"0"]

    def uid(self, command, *args):
        self.calls.append(("UID", command, *args))
        return "OK", [self.search_uids]

    def append(self, folder, flags, when, payload):
        self.appended.append((folder, flags, when, payload))
        return self.append_status, [b"done"]


def message():
    m = EmailMessage()
    m["From"] = "owner@example.com"
    m["To"] = "artist@example.com"
    m["Message-ID"] = "<outgoing-123@example.com>"
    m["Auto-Submitted"] = "auto-replied"
    m["Subject"] = "Away"
    m.set_content("Back soon")
    return m


class SentFolderTests(unittest.TestCase):
    def test_special_use_icloud_sent_messages(self):
        self.assertEqual(find_sent_folder(FakeSentImap()), "Sent Messages")

    def test_normal_fallback_when_no_sent_marker(self):
        f = FakeSentImap(rows=[b'(\\HasNoChildren) "/" "Sent"'])
        self.assertEqual(find_sent_folder(f), "Sent")

    def test_unrelated_folders_do_not_guess(self):
        f = FakeSentImap(rows=[b'(\\HasNoChildren) "/" "Archive"'])
        self.assertIsNone(find_sent_folder(f))

    def test_multiple_special_use_folders_fail_closed(self):
        f = FakeSentImap(rows=[
            b'(\\Sent) "/" "Sent"',
            b'(\\Sent) "/" "Sent Messages"',
        ])
        self.assertIsNone(find_sent_folder(f))

    def test_ambiguous_fallback_fails_closed(self):
        f = FakeSentImap(rows=[
            b'() "/" "Sent"',
            b'() "/" "Sent Messages"',
        ])
        self.assertIsNone(find_sent_folder(f))

    def test_noselect_folder_is_not_usable(self):
        f = FakeSentImap(rows=[b'(\\Sent \\Noselect) "/" "Sent Messages"'])
        self.assertIsNone(find_sent_folder(f))

    def test_malformed_list_fails_closed(self):
        f = FakeSentImap(rows=[b'not-a-valid-list-entry'])
        self.assertIsNone(find_sent_folder(f))

    def test_failed_select_fails(self):
        f = FakeSentImap(select_status="NO")
        with self.assertRaises(SentCopyStorageError):
            select_sent_folder(f, "Sent Messages")


class SentAppendTests(unittest.TestCase):
    def test_sends_exact_message_as_seen_to_sent(self):
        f = FakeSentImap()
        result = append_sent_copy(f, "Sent Messages", message())
        self.assertEqual(result, "stored")
        self.assertEqual(len(f.appended), 1)
        folder, flags, when, raw = f.appended[0]
        self.assertEqual(folder, "Sent Messages")
        self.assertEqual(flags, r"(\Seen)")
        self.assertIsNone(when)
        self.assertIn(b"Message-ID: <outgoing-123@example.com>", raw)
        self.assertIn(b"\r\n", raw)
        self.assertEqual(f.calls[0][0:2], ("UID", "SEARCH"))
        self.assertEqual(f.calls[0][-1], '"<outgoing-123@example.com>"')

    def test_provider_auto_saved_message_is_not_appended_twice(self):
        f = FakeSentImap(search_uids=b"99")
        self.assertEqual(append_sent_copy(f, "Sent Messages", message()), "already_stored")
        self.assertEqual(len(f.appended), 0)

    def test_search_failure_still_appends(self):
        f = FakeSentImap()
        def error(*_args, **_kwargs):
            raise OSError("not available")
        f.uid = error
        self.assertEqual(append_sent_copy(f, "Sent Messages", message()), "stored")
        self.assertEqual(len(f.appended), 1)

    def test_failed_append_is_not_silent(self):
        f = FakeSentImap(append_status="NO")
        with self.assertRaises(SentCopyStorageError):
            append_sent_copy(f, "Sent Messages", message())

    def test_missing_message_id_is_rejected(self):
        m = message()
        del m["Message-ID"]
        with self.assertRaises(SentCopyStorageError):
            append_sent_copy(FakeSentImap(), "Sent Messages", m)


if __name__ == "__main__":
    unittest.main()
