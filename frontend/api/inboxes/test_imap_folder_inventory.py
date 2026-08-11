from __future__ import annotations

import sys
import unittest
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from api.inboxes.imap_folder_inventory import (
    ImapListEntry,
    is_runtime_compatible_mailbox_name,
    is_selectable_imap_list_entry,
    read_imap_list_inventory,
)


class RecordingMailbox:
    def __init__(self, response):
        self.response = response
        self.list_calls = 0

    def list(self):
        self.list_calls += 1
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class ImapListInventoryTests(unittest.TestCase):
    def test_reads_one_complete_inventory_and_preserves_opaque_names(self):
        literal = b'Team "Deleted"\\2024'
        mailbox = RecordingMailbox(
            (
                "OK",
                [
                    r'(\HasNoChildren) "/" "INBOX"',
                    (
                        f'(\\HasNoChildren) "/" {{{len(literal)}}}'.encode(),
                        literal,
                    ),
                    b"",
                    r'(\Archive) NIL &AMk-l&AOk-ments',
                ],
            )
        )

        result = read_imap_list_inventory(mailbox)

        self.assertEqual(mailbox.list_calls, 1)
        self.assertIsNone(result.error)
        self.assertIsNotNone(result.entries)
        self.assertEqual(
            [entry.mailbox for entry in result.entries],
            ["INBOX", 'Team "Deleted"\\2024', "&AMk-l&AOk-ments"],
        )
        self.assertEqual(result.entries[1].delimiter, "/")
        self.assertEqual(result.entries[2].delimiter, None)
        self.assertEqual(result.entries[2].attributes, frozenset({r"\archive"}))

    def test_empty_ok_inventory_is_valid(self):
        mailbox = RecordingMailbox((b"oK", []))
        result = read_imap_list_inventory(mailbox)
        self.assertEqual(result.entries, ())
        self.assertIsNone(result.error)
        self.assertEqual(mailbox.list_calls, 1)

    def test_any_malformed_row_invalidates_the_entire_inventory(self):
        for rows in (
            [r'(\HasNoChildren) "/" "Valid"', None],
            [r'(\HasNoChildren) "/" "Valid"', b""],
            [r'(\HasNoChildren) "/" "unterminated'],
            [(br'(\HasNoChildren) "/" {3}', b"five")],
        ):
            with self.subTest(rows=rows):
                result = read_imap_list_inventory(
                    RecordingMailbox(("OK", rows))
                )
                self.assertIsNone(result.entries)
                self.assertEqual(result.error, "list_unavailable")

    def test_exception_non_ok_wrong_shape_and_overflow_are_unusable(self):
        valid_row = r'(\HasNoChildren) "/" "Folder"'
        responses = (
            RuntimeError("password=provider-secret"),
            ("NO", [b"private provider detail"]),
            ("OK", None),
            ("OK", [valid_row] * 4_097),
            ("OK", [], "extra"),
        )
        for response in responses:
            with self.subTest(response_type=type(response).__name__):
                result = read_imap_list_inventory(RecordingMailbox(response))
                self.assertIsNone(result.entries)
                self.assertEqual(result.error, "list_unavailable")

    def test_selectability_is_attribute_based(self):
        selectable = ImapListEntry(
            attributes=frozenset({r"\hasnochildren"}),
            delimiter="/",
            mailbox="Deleted",
        )
        self.assertTrue(is_selectable_imap_list_entry(selectable))
        for attribute in (r"\noselect", r"\nonexistent"):
            with self.subTest(attribute=attribute):
                self.assertFalse(
                    is_selectable_imap_list_entry(
                        ImapListEntry(
                            attributes=frozenset({attribute}),
                            delimiter="/",
                            mailbox="Deleted",
                        )
                    )
                )

    def test_runtime_mailbox_contract_is_exact_bounded_and_type_strict(self):
        self.assertTrue(is_runtime_compatible_mailbox_name("Deleted Items"))
        self.assertTrue(
            is_runtime_compatible_mailbox_name('Deleted "Items"\\2024')
        )
        for value in (
            None,
            b"Deleted",
            "",
            " Deleted",
            "Deleted ",
            "Deleted\nItems",
            "Deleted\u0085Items",
            "\ud800",
            "x" * 16_385,
        ):
            with self.subTest(value=repr(value)[:80]):
                self.assertFalse(is_runtime_compatible_mailbox_name(value))

    def test_c1_name_remains_opaque_inventory_data_but_is_not_runtime_safe(self):
        mailbox_name = "Deleted\u0085Items"
        result = read_imap_list_inventory(
            RecordingMailbox(
                ("OK", [f'(\\HasNoChildren) "/" "{mailbox_name}"'])
            )
        )
        self.assertIsNone(result.error)
        self.assertEqual(result.entries[0].mailbox, mailbox_name)
        self.assertFalse(
            is_runtime_compatible_mailbox_name(result.entries[0].mailbox)
        )


if __name__ == "__main__":
    unittest.main()
