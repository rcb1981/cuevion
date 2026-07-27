from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from api.inboxes.imap_archive import (
    archive_imap_message,
    parse_imap_list_entry,
)


_DEFAULT = object()


class RecordingMailbox:
    def __init__(
        self,
        *,
        list_response=_DEFAULT,
        capability_response=_DEFAULT,
        select_response=_DEFAULT,
        uid_validity_response=_DEFAULT,
        search_responses=_DEFAULT,
        move_response=_DEFAULT,
    ):
        self.list_response = (
            (
                "OK",
                [br'(\HasNoChildren \Archive) "/" "Archive"'],
            )
            if list_response is _DEFAULT
            else list_response
        )
        self.capability_response = (
            ("OK", [b"IMAP4rev1 MOVE"])
            if capability_response is _DEFAULT
            else capability_response
        )
        self.select_response = (
            ("OK", [b"1"])
            if select_response is _DEFAULT
            else select_response
        )
        self.uid_validity_response = (
            ("UIDVALIDITY", [b"456"])
            if uid_validity_response is _DEFAULT
            else uid_validity_response
        )
        self.search_responses = list(
            [
                ("OK", [b"123"]),
                ("OK", [b""]),
            ]
            if search_responses is _DEFAULT
            else search_responses
        )
        self.move_response = (
            ("OK", [None])
            if move_response is _DEFAULT
            else move_response
        )
        self.events: list[str] = []
        self.select_calls: list[str] = []
        self.response_calls: list[str] = []
        self.uid_calls: list[tuple] = []
        self.unsafe_calls: list[tuple] = []
        self._search_index = 0

    @staticmethod
    def _resolve(value):
        if isinstance(value, BaseException):
            raise value
        return value

    def list(self):
        self.events.append("discovery")
        return self._resolve(self.list_response)

    def capability(self):
        self.events.append("capability")
        return self._resolve(self.capability_response)

    def select(self, folder):
        self.events.append("select")
        self.select_calls.append(folder)
        return self._resolve(self.select_response)

    def response(self, name):
        self.events.append("uid_validity")
        self.response_calls.append(name)
        return self._resolve(self.uid_validity_response)

    def uid(self, command, *arguments):
        call = (command, *arguments)
        self.uid_calls.append(call)
        normalized_command = command.casefold()
        if normalized_command == "search":
            self.events.append(
                "existence"
                if self._search_index == 0
                else "postcondition"
            )
            if self._search_index >= len(self.search_responses):
                raise AssertionError("unexpected extra UID SEARCH")
            response = self.search_responses[self._search_index]
            self._search_index += 1
            return self._resolve(response)
        if normalized_command == "move":
            self.events.append("move")
            return self._resolve(self.move_response)
        self.unsafe_calls.append(call)
        raise AssertionError(f"unexpected UID command: {command}")

    def search(self, *arguments):
        self.unsafe_calls.append(("sequence_search", *arguments))
        raise AssertionError("sequence-number SEARCH must not be used")

    def copy(self, *arguments):
        self.unsafe_calls.append(("copy", *arguments))
        raise AssertionError("COPY fallback must not be used")

    def store(self, *arguments):
        self.unsafe_calls.append(("store", *arguments))
        raise AssertionError("STORE fallback must not be used")

    def expunge(self, *arguments):
        self.unsafe_calls.append(("expunge", *arguments))
        raise AssertionError("EXPUNGE must not be used")


class AttributeCapabilityMailbox(RecordingMailbox):
    capability = None

    def __init__(self, capabilities):
        super().__init__()
        self.capabilities = capabilities


def archive(mailbox: RecordingMailbox, **overrides):
    arguments = {
        "source_folder": "INBOX",
        "uid": "123",
        "expected_uid_validity": "456",
        **overrides,
    }
    return archive_imap_message(mailbox, **arguments)


def error_code(result):
    return result["error"]["code"]


class ImapArchiveInputValidationTests(unittest.TestCase):
    def test_invalid_source_folders_are_rejected_before_provider_calls(self):
        class StringSubclass(str):
            pass

        for source_folder in (
            "",
            " INBOX",
            "INBOX ",
            "IN\rBOX",
            "IN\nBOX",
            "IN\x00BOX",
            "IN\tBOX",
            "\ud800",
            None,
            StringSubclass("INBOX"),
        ):
            with self.subTest(source_folder=source_folder):
                mailbox = RecordingMailbox()
                result = archive(mailbox, source_folder=source_folder)
                self.assertFalse(result["ok"])
                self.assertEqual(error_code(result), "invalid_source_folder")
                self.assertEqual(mailbox.events, [])

    def test_invalid_uids_are_rejected_before_provider_calls(self):
        class StringSubclass(str):
            pass

        for uid in (
            None,
            "",
            "0",
            "-1",
            "1:2",
            "1,2",
            "abc",
            "１２",
            "01",
            "4294967296",
            "9" * 5_000,
            123,
            StringSubclass("123"),
        ):
            with self.subTest(uid=repr(uid)[:80]):
                mailbox = RecordingMailbox()
                result = archive(mailbox, uid=uid)
                self.assertFalse(result["ok"])
                self.assertEqual(error_code(result), "invalid_imap_uid")
                self.assertEqual(mailbox.events, [])

    def test_uidvalidity_is_required_and_must_be_canonical(self):
        for uid_validity in (
            None,
            "",
            "0",
            "01",
            "+1",
            " 1",
            "1 ",
            "１２",
            "100000000000000000000",
            456,
        ):
            with self.subTest(uid_validity=uid_validity):
                mailbox = RecordingMailbox()
                result = archive(
                    mailbox,
                    expected_uid_validity=uid_validity,
                )
                self.assertFalse(result["ok"])
                self.assertEqual(error_code(result), "invalid_uid_validity")
                self.assertEqual(mailbox.events, [])


class ImapListParserTests(unittest.TestCase):
    def test_parses_bytes_attributes_and_quoted_mailbox_with_spaces(self):
        entry = parse_imap_list_entry(
            br'(\HasNoChildren \Archive) "/" "Team Archive"'
        )
        self.assertIsNotNone(entry)
        self.assertEqual(
            entry.attributes,
            frozenset({r"\hasnochildren", r"\archive"}),
        )
        self.assertEqual(entry.delimiter, "/")
        self.assertEqual(entry.mailbox, "Team Archive")

    def test_parses_nil_delimiter_and_preserves_opaque_modified_utf7_atom(self):
        entry = parse_imap_list_entry(r"(\Archive) NIL &AMk-l&AOk-ments")
        self.assertIsNotNone(entry)
        self.assertIsNone(entry.delimiter)
        self.assertEqual(entry.mailbox, "&AMk-l&AOk-ments")

    def test_unescapes_only_quoted_quote_and_backslash(self):
        entry = parse_imap_list_entry(
            r'(\Archive) "/" "Old \"Archive\"\\2024"'
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.mailbox, 'Old "Archive"\\2024')

    def test_parses_exact_imaplib_literal_tuple_without_normalizing_name(self):
        literal = b'Team "Archive"\\2024'
        entry = parse_imap_list_entry(
            (
                f'(\\Archive) "/" {{{len(literal)}}}'.encode(),
                literal,
            )
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.mailbox, 'Team "Archive"\\2024')

    def test_malformed_list_entries_fail_closed(self):
        malformed_entries = (
            None,
            123,
            b"\xff",
            "",
            r"\Archive NIL Archive",
            r"(\Archive NIL Archive",
            r"( \Archive) NIL Archive",
            r"(\Archive ) NIL Archive",
            r"(\Archive  \HasNoChildren) NIL Archive",
            r"( ) NIL Archive",
            r"(garbage \Archive) NIL Archive",
            r"(\Archive bad*) NIL Archive",
            r"(\Archive \Bad]) NIL Archive",
            r'(\Archive)  "/" "Archive"',
            r'(\Archive) "/"  "Archive"',
            r"(\Archive) BAD Archive",
            r'(\Archive) "" Archive',
            r'(\Archive) "/"',
            r'(\Archive) "/" ""',
            r'(\Archive) "/" "Archive" trailing',
            r'(\Archive) "/" "Bad \q escape"',
            '(\\Archive) "/" "Bad trailing slash\\',
            r'(\Archive) "/" {7}',
            "(\\" + "Archive) \"/\" \"Bad\rName\"",
            (br'(\Archive) "/" {8}', b"Archive"),
            (br'(\Archive) "/" {2}', b"\xff\xff"),
            (br'(\Archive) "/" {7}',),
            '(\\Archive) "/" "\ud800"',
            (br'(\Archive) "/" {1}', "\ud800"),
        )
        for entry in malformed_entries:
            with self.subTest(entry=entry):
                self.assertIsNone(parse_imap_list_entry(entry))


class ImapArchiveDiscoveryTests(unittest.TestCase):
    def test_unique_archive_attribute_is_case_insensitive_and_allows_other_attributes(self):
        mailbox = RecordingMailbox(
            list_response=(
                "OK",
                [
                    r'(\Marked) "/" "Archive"',
                    r'(\HasNoChildren \aRcHiVe) "/" "Stored Mail"',
                ],
            )
        )
        result = archive(mailbox)
        self.assertTrue(result["ok"])
        self.assertEqual(result["archive_folder"], "Stored Mail")
        self.assertIn(("MOVE", "123", '"Stored Mail"'), mailbox.uid_calls)

    def test_noselect_archive_is_never_a_target(self):
        mailbox = RecordingMailbox(
            list_response=(
                "OK",
                [
                    r'(\Archive \Noselect) "/" "Container"',
                    r'(\Archive \HasNoChildren) "/" "Selectable"',
                ],
            )
        )
        result = archive(mailbox)
        self.assertTrue(result["ok"])
        self.assertEqual(result["archive_folder"], "Selectable")

    def test_no_archive_role_has_no_name_fallback(self):
        for mailbox_name in ("Archive", "Archives", "INBOX.Archive", "All Mail"):
            with self.subTest(mailbox_name=mailbox_name):
                mailbox = RecordingMailbox(
                    list_response=(
                        "OK",
                        [f'(\\HasNoChildren) "/" "{mailbox_name}"'],
                    )
                )
                result = archive(mailbox)
                self.assertEqual(
                    error_code(result),
                    "archive_folder_unavailable",
                )
                self.assertEqual(mailbox.events, ["discovery"])
                self.assertEqual(mailbox.uid_calls, [])

    def test_only_noselect_or_conflicting_archive_is_unavailable(self):
        for attributes in (
            r"\Archive \Noselect",
            r"\Archive \NonExistent",
            r"\Archive \Trash",
            r"\Archive \Sent",
        ):
            with self.subTest(attributes=attributes):
                mailbox = RecordingMailbox(
                    list_response=(
                        "OK",
                        [f'({attributes}) "/" "Unsafe"'],
                    )
                )
                result = archive(mailbox)
                self.assertEqual(
                    error_code(result),
                    "archive_folder_unavailable",
                )
                self.assertEqual(mailbox.uid_calls, [])

    def test_multiple_archive_roles_are_ambiguous_even_when_names_repeat(self):
        for entries in (
            [
                r'(\Archive) "/" "Archive A"',
                r'(\Archive) "/" "Archive B"',
            ],
            [
                r'(\Archive) "/" "Archive"',
                r'(\Archive) "/" "Archive"',
            ],
            [
                r'(\Archive \Trash) "/" "Conflicting"',
                r'(\Archive) "/" "Archive"',
            ],
        ):
            with self.subTest(entries=entries):
                mailbox = RecordingMailbox(
                    list_response=("OK", entries)
                )
                result = archive(mailbox)
                self.assertEqual(
                    error_code(result),
                    "archive_folder_ambiguous",
                )
                self.assertEqual(mailbox.events, ["discovery"])
                self.assertEqual(mailbox.uid_calls, [])

    def test_any_malformed_list_row_invalidates_discovery(self):
        mailbox = RecordingMailbox(
            list_response=(
                "OK",
                [
                    r'(\Archive) "/" "Archive"',
                    r'(\HasNoChildren) "/" "unterminated',
                ],
            )
        )
        result = archive(mailbox)
        self.assertEqual(error_code(result), "archive_folder_unavailable")
        self.assertEqual(mailbox.events, ["discovery"])

    def test_imaplib_literal_mailbox_response_is_discovered_safely(self):
        literal = b'Team "Archive"\\2024'
        mailbox = RecordingMailbox(
            list_response=(
                "OK",
                [
                    (
                        f'(\\Archive) "/" {{{len(literal)}}}'.encode(),
                        literal,
                    ),
                    b"",
                ],
            )
        )
        result = archive(mailbox)
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["archive_folder"],
            'Team "Archive"\\2024',
        )
        self.assertIn(
            (
                "MOVE",
                "123",
                r'"Team \"Archive\"\\2024"',
            ),
            mailbox.uid_calls,
        )

    def test_non_ok_or_malformed_list_response_is_unavailable(self):
        for list_response in (
            ("NO", [b"private provider detail"]),
            ("OK", None),
            ("OK", [None]),
            ("OK", [b"\xff"]),
            RuntimeError("password=provider-secret"),
        ):
            with self.subTest(list_response=list_response):
                mailbox = RecordingMailbox(list_response=list_response)
                result = archive(mailbox)
                self.assertEqual(
                    error_code(result),
                    "archive_folder_unavailable",
                )
                serialized = json.dumps(result)
                self.assertNotIn("provider-secret", serialized)
                self.assertNotIn("private provider detail", serialized)

    def test_source_may_not_equal_discovered_target(self):
        for source_folder, target in (
            ("Archive", "Archive"),
            ("archive", "Archive"),
            ("INBOX", "inbox"),
        ):
            with self.subTest(source_folder=source_folder, target=target):
                mailbox = RecordingMailbox(
                    list_response=(
                        "OK",
                        [f'(\\Archive) "/" "{target}"'],
                    )
                )
                result = archive(mailbox, source_folder=source_folder)
                self.assertEqual(error_code(result), "invalid_source_folder")
                self.assertEqual(mailbox.events, ["discovery"])


class ImapArchiveCapabilityTests(unittest.TestCase):
    def test_explicit_mixed_case_move_capability_is_accepted(self):
        mailbox = RecordingMailbox(
            capability_response=("OK", [b"imap4rev1 mOvE UIDPLUS"])
        )
        self.assertTrue(archive(mailbox)["ok"])

    def test_attribute_capabilities_are_supported_when_method_is_absent(self):
        mailbox = AttributeCapabilityMailbox(
            (b"imap4rev1", "mOvE", b"UIDPLUS")
        )
        result = archive(mailbox)
        self.assertTrue(result["ok"])
        self.assertNotIn("capability", mailbox.events)

    def test_missing_move_fails_without_any_mutation_fallback(self):
        mailbox = RecordingMailbox(
            capability_response=("OK", [b"IMAP4rev1 UIDPLUS"])
        )
        result = archive(mailbox)
        self.assertEqual(error_code(result), "archive_move_unsupported")
        self.assertEqual(mailbox.events, ["discovery", "capability"])
        self.assertEqual(mailbox.uid_calls, [])
        self.assertEqual(mailbox.unsafe_calls, [])

    def test_fresh_non_ok_or_malformed_capability_does_not_use_stale_attribute(self):
        for capability_response in (
            ("NO", [b"MOVE"]),
            ("OK", [b"\xffMOVE"]),
            ("OK", None),
            RuntimeError("capability failed"),
        ):
            with self.subTest(capability_response=capability_response):
                mailbox = RecordingMailbox(
                    capability_response=capability_response
                )
                mailbox.capabilities = ("IMAP4REV1", "MOVE")
                result = archive(mailbox)
                self.assertEqual(
                    error_code(result),
                    "archive_move_unsupported",
                )
                self.assertEqual(mailbox.uid_calls, [])


class ImapArchivePremutationTests(unittest.TestCase):
    def test_source_folder_is_safely_quoted_before_select(self):
        mailbox = RecordingMailbox()
        source_folder = 'Source "A"\\2024'
        result = archive(mailbox, source_folder=source_folder)
        self.assertTrue(result["ok"])
        self.assertEqual(
            mailbox.select_calls,
            [r'"Source \"A\"\\2024"'],
        )
        self.assertEqual(result["source_folder"], source_folder)

    def test_select_failure_is_source_folder_unavailable(self):
        for select_response in (
            ("NO", [b"not found"]),
            ("OK",),
            RuntimeError("select failed"),
        ):
            with self.subTest(select_response=select_response):
                mailbox = RecordingMailbox(
                    select_response=select_response
                )
                result = archive(mailbox)
                self.assertEqual(
                    error_code(result),
                    "source_folder_unavailable",
                )
                self.assertNotIn("existence", mailbox.events)
                self.assertNotIn("move", mailbox.events)

    def test_uidvalidity_is_read_from_selected_source_and_must_be_available(self):
        for response in (
            ("OK", [b"456"]),
            ("UIDVALIDITY", []),
            ("UIDVALIDITY", [b"0456"]),
            RuntimeError("provider failure"),
        ):
            with self.subTest(response=response):
                mailbox = RecordingMailbox(
                    uid_validity_response=response
                )
                result = archive(mailbox)
                self.assertEqual(
                    error_code(result),
                    "uid_validity_unavailable",
                )
                self.assertEqual(
                    mailbox.response_calls,
                    ["UIDVALIDITY"],
                )
                self.assertNotIn("move", mailbox.events)

    def test_changed_uidvalidity_prevents_move(self):
        mailbox = RecordingMailbox(
            uid_validity_response=("UIDVALIDITY", [b"457"])
        )
        result = archive(mailbox)
        self.assertEqual(error_code(result), "uid_validity_changed")
        self.assertEqual(mailbox.uid_calls, [])

    def test_missing_source_uid_prevents_move(self):
        mailbox = RecordingMailbox(
            search_responses=[("OK", [b""])]
        )
        result = archive(mailbox)
        self.assertEqual(error_code(result), "archive_message_not_found")
        self.assertEqual(
            mailbox.uid_calls,
            [("SEARCH", None, "UID", "123")],
        )
        self.assertNotIn("move", mailbox.events)

    def test_indeterminate_source_search_fails_closed(self):
        for search_response in (
            ("NO", [b"123"]),
            ("OK", [b"124"]),
            ("OK", [b"123 124"]),
            ("OK", [b"123 123"]),
            ("OK", [b" 123"]),
            ("OK", None),
            RuntimeError("search failed"),
        ):
            with self.subTest(search_response=search_response):
                mailbox = RecordingMailbox(
                    search_responses=[search_response]
                )
                result = archive(mailbox)
                self.assertEqual(error_code(result), "imap_archive_failed")
                self.assertNotIn("move", mailbox.events)

    def test_existence_check_uses_only_the_exact_requested_uid(self):
        mailbox = RecordingMailbox()
        result = archive(mailbox)
        self.assertTrue(result["ok"])
        search_calls = [
            call for call in mailbox.uid_calls
            if call[0].casefold() == "search"
        ]
        self.assertEqual(
            search_calls,
            [
                ("SEARCH", None, "UID", "123"),
                ("SEARCH", None, "UID", "123"),
            ],
        )
        self.assertEqual(mailbox.unsafe_calls, [])


class ImapArchiveMoveAndPostconditionTests(unittest.TestCase):
    def test_success_uses_exactly_one_uid_move_to_quoted_discovered_target(self):
        mailbox = RecordingMailbox(
            list_response=(
                "OK",
                [r'(\Archive) "/" "Team \"Archive\"\\2024"'],
            )
        )
        result = archive(mailbox)
        self.assertTrue(result["ok"])
        move_calls = [
            call for call in mailbox.uid_calls
            if call[0].casefold() == "move"
        ]
        self.assertEqual(
            move_calls,
            [
                (
                    "MOVE",
                    "123",
                    r'"Team \"Archive\"\\2024"',
                )
            ],
        )
        self.assertNotIn("Trash", json.dumps(move_calls))
        self.assertEqual(mailbox.unsafe_calls, [])

    def test_provider_move_failure_or_exception_is_safe_and_not_retried(self):
        secret = "password=do-not-leak"
        for move_response, expected_code in (
            (("NO", [secret.encode()]), "archive_move_failed"),
            (("BAD", []), "archive_move_failed"),
            (("OK",), "archive_move_failed"),
            (RuntimeError(secret), "archive_move_unconfirmed"),
        ):
            with self.subTest(move_response=move_response):
                mailbox = RecordingMailbox(
                    move_response=move_response
                )
                result = archive(mailbox)
                self.assertEqual(error_code(result), expected_code)
                self.assertEqual(mailbox.events.count("move"), 1)
                self.assertEqual(mailbox.events.count("postcondition"), 0)
                self.assertNotIn(secret, json.dumps(result))

    def test_uid_still_present_after_move_is_unconfirmed_without_retry(self):
        mailbox = RecordingMailbox(
            search_responses=[
                ("OK", [b"123"]),
                ("OK", [b"123"]),
            ]
        )
        result = archive(mailbox)
        self.assertEqual(error_code(result), "archive_move_unconfirmed")
        self.assertEqual(mailbox.events.count("move"), 1)
        self.assertEqual(mailbox.events.count("postcondition"), 1)

    def test_indeterminate_postcondition_is_unconfirmed_without_retry(self):
        for postcondition in (
            ("NO", [b""]),
            ("OK", [b"124"]),
            ("OK", [b"123 124"]),
            ("OK", None),
            RuntimeError("postcondition failed"),
        ):
            with self.subTest(postcondition=postcondition):
                mailbox = RecordingMailbox(
                    search_responses=[
                        ("OK", [b"123"]),
                        postcondition,
                    ]
                )
                result = archive(mailbox)
                self.assertEqual(
                    error_code(result),
                    "archive_move_unconfirmed",
                )
                self.assertEqual(mailbox.events.count("move"), 1)

    def test_confirmed_success_contains_only_safe_metadata(self):
        mailbox = RecordingMailbox()
        mailbox.host = "secret-imap.example.com"
        mailbox.username = "secret-user"
        mailbox.password = "secret-password"
        mailbox.access_token = "secret-token"
        result = archive(mailbox)
        self.assertEqual(
            result,
            {
                "ok": True,
                "status": "ok",
                "source_folder": "INBOX",
                "archive_folder": "Archive",
                "uid": "123",
                "uid_validity": "456",
                "confirmation": "source_removed",
                "error": None,
            },
        )
        serialized = json.dumps(result)
        for secret in (
            mailbox.host,
            mailbox.username,
            mailbox.password,
            mailbox.access_token,
        ):
            self.assertNotIn(secret, serialized)

    def test_operation_order_is_fixed(self):
        mailbox = RecordingMailbox()
        result = archive(mailbox)
        self.assertTrue(result["ok"])
        self.assertEqual(
            mailbox.events,
            [
                "discovery",
                "capability",
                "select",
                "uid_validity",
                "existence",
                "move",
                "postcondition",
            ],
        )
        self.assertEqual(mailbox.unsafe_calls, [])


if __name__ == "__main__":
    unittest.main()
